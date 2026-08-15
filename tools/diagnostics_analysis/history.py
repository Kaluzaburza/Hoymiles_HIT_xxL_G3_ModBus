"""Bounded longitudinal analysis of deduplicated EMS control history.

The diagnostic bundle exposes state changes, not historical planner
attributes or fast power telemetry.  This module therefore limits itself to
facts that can be proved from explicit ``on``/``off`` intervals.  In
particular, cross-helper findings require at least 30 seconds of measured
overlap; a missing gate or helper timeline never becomes an implicit
``off``/``on`` value.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from typing import Any

from .models import Confidence, Controller, Finding, Severity


MAX_INPUT_EVENTS = 100_000
MAX_EVENTS_PER_HELPER = 20_000
SHORT_RUN_SECONDS = 120.0
FLAPPING_TOGGLES_PER_HOUR = 6
SEVERE_FLAPPING_TOGGLES_PER_HOUR = 10
MIN_VIOLATION_OVERLAP_SECONDS = 30.0
PARALLEL_RESPONSE_ENTITY_ID = (
    "sensor.hoymiles_parallel_aggregate_physical_response"
)
PARALLEL_RESPONSE_MINIMUM_VERSION = (1, 5, 6)
# Covers 20 s grace, five 20 s waits and final event/state propagation when a
# legacy event omits the authoritative bounded runtime attribute.
PARALLEL_RESPONSE_PENDING_HORIZON_SECONDS = 135.0
BOUNDED_SEMANTIC_VERSION_RE = re.compile(
    r"\s*(\d{1,6})\.(\d{1,6})\.(\d{1,6})"
    r"(?:[-+][0-9A-Za-z.-]{1,64})?\s*"
)

RCE_ACTIVE = "input_boolean.hoymiles_rce_discharge_active"
TARIFF_ACTIVE = "input_boolean.hoymiles_tariff_charge_active"
RCEM_ACTIVE = "input_boolean.hoymiles_rcm_active"
RCEM_EXPORT_ACTIVE = "input_boolean.hoymiles_rcm_export_control_active"
RCEM_PRE_DISCHARGE_ACTIVE = (
    "input_boolean.hoymiles_rcm_pre_discharge_active"
)
MANUAL_CHARGE_ACTIVE = "input_boolean.hoymiles_charge_cycle_active"
MANUAL_DISCHARGE_ACTIVE = "input_boolean.hoymiles_discharge_cycle_active"
BALANCING_ACTIVE = "input_boolean.hoymiles_battery_balancing_active"
EMS_EXECUTION_READY = "binary_sensor.hoymiles_ems_execution_ready"
DIRECT_REGISTER_EXECUTION_READY = (
    "binary_sensor.hoymiles_direct_register_execution_ready"
)
SALE_BLOCK_ACTIVE = "binary_sensor.hoymiles_sale_block_active"

HELPER_FAMILY: Mapping[str, str] = {
    RCE_ACTIVE: "rce",
    TARIFF_ACTIVE: "tariff",
    RCEM_ACTIVE: "rcem",
    RCEM_EXPORT_ACTIVE: "rcem",
    RCEM_PRE_DISCHARGE_ACTIVE: "rcem",
    MANUAL_CHARGE_ACTIVE: "manual",
    MANUAL_DISCHARGE_ACTIVE: "manual",
    BALANCING_ACTIVE: "balancing",
    EMS_EXECUTION_READY: "gate",
    DIRECT_REGISTER_EXECUTION_READY: "gate",
    SALE_BLOCK_ACTIVE: "gate",
}

HELPER_CONTROLLER: Mapping[str, Controller] = {
    RCE_ACTIVE: Controller.RCE,
    TARIFF_ACTIVE: Controller.TARIFF,
    RCEM_ACTIVE: Controller.RCEM,
    RCEM_EXPORT_ACTIVE: Controller.RCEM,
    RCEM_PRE_DISCHARGE_ACTIVE: Controller.RCEM,
}

FAMILY_HELPERS: Mapping[str, tuple[str, ...]] = {
    "rce": (RCE_ACTIVE,),
    "tariff": (TARIFF_ACTIVE,),
    "rcem": (
        RCEM_ACTIVE,
        RCEM_EXPORT_ACTIVE,
        RCEM_PRE_DISCHARGE_ACTIVE,
    ),
    "manual": (MANUAL_CHARGE_ACTIVE, MANUAL_DISCHARGE_ACTIVE),
    "balancing": (BALANCING_ACTIVE,),
}


@dataclass(frozen=True, slots=True)
class _Point:
    at: datetime
    state: str | None


@dataclass(frozen=True, slots=True)
class _Interval:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)


@dataclass(frozen=True, slots=True)
class _Timeline:
    installation_key: str
    entity_id: str
    points: tuple[_Point, ...]
    capture_end: datetime | None
    starts: int
    stops: int
    transitions: int
    transition_times: tuple[datetime, ...]
    active_intervals: tuple[_Interval, ...]
    inactive_intervals: tuple[_Interval, ...]
    short_runs: int
    open_run: bool
    ambiguous_points: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class _Overlap:
    count: int = 0
    total_seconds: float = 0.0
    longest_seconds: float = 0.0
    first_start: datetime | None = None
    last_end: datetime | None = None


def _parse_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _event_datetime(event: Mapping[str, Any]) -> datetime | None:
    for key in ("last_updated", "last_changed"):
        parsed = _parse_aware_datetime(event.get(key))
        if parsed is not None:
            return parsed
    return None


def _response_version_supported(event: Mapping[str, Any]) -> bool:
    raw = event.get("integration_version")
    if not isinstance(raw, str):
        return False
    match = BOUNDED_SEMANTIC_VERSION_RE.fullmatch(raw)
    return bool(
        match is not None
        and tuple(int(part) for part in match.groups())
        >= PARALLEL_RESPONSE_MINIMUM_VERSION
    )


def _event_attributes(event: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = event.get("attributes")
    return raw if isinstance(raw, Mapping) else {}


def _finite_attribute(attributes: Mapping[str, Any], key: str) -> float | None:
    value = attributes.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _parallel_proof_required(attributes: Mapping[str, Any]) -> bool:
    machine_type = _finite_attribute(attributes, "latched_machine_type")
    machine_count = _finite_attribute(attributes, "detected_inverters")
    return (
        attributes.get("requires_parallel_proof") is True
        and machine_type == 1.0
        and machine_count is not None
        and machine_count >= 2.0
    )


def _response_horizon(attributes: Mapping[str, Any]) -> float:
    configured = _finite_attribute(attributes, "verification_horizon_seconds")
    if configured is not None and 1.0 <= configured <= 600.0:
        return configured
    return PARALLEL_RESPONSE_PENDING_HORIZON_SECONDS


def _response_controller(attributes: Mapping[str, Any]) -> Controller:
    owner = attributes.get("owner")
    if owner == "rce":
        return Controller.RCE
    if owner == "rcm_pre_discharge":
        return Controller.RCEM
    return Controller.SYSTEM


def _response_capture_end(
    installation_key: str,
    at: datetime,
    capture_windows_by_installation: Mapping[str, Any],
) -> datetime | None:
    windows = _capture_windows(
        installation_key,
        capture_windows_by_installation,
        {at: {"pending"}},
    )
    containing = [window.end for window in windows if window.start <= at <= window.end]
    return min(containing) if containing else None


def _pending_started_at(
    at: datetime,
    event: Mapping[str, Any],
) -> datetime:
    declared = _parse_aware_datetime(_event_attributes(event).get("pending_at"))
    return declared if declared is not None and declared <= at else at


def _analyze_parallel_response_history(
    events: Sequence[Mapping[str, Any]],
    capture_windows_by_installation: Mapping[str, Any],
    *,
    input_truncated: bool,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Finding, ...]]:
    """Correlate bounded pending/terminal aggregate-response transactions."""
    grouped: dict[tuple[str, str], list[tuple[datetime, Mapping[str, Any]]]] = (
        defaultdict(list)
    )
    for event in events:
        if (
            event.get("entity_id") != PARALLEL_RESPONSE_ENTITY_ID
            or not _response_version_supported(event)
        ):
            continue
        installation_key = event.get("installation_key")
        state = event.get("state")
        at = _event_datetime(event)
        attributes = _event_attributes(event)
        transaction_id = attributes.get("transaction_id")
        if (
            not isinstance(installation_key, str)
            or not installation_key
            or len(installation_key) > 256
            or state not in {
                "pending",
                "confirmed",
                "not_confirmed",
                "not_evaluable",
            }
            or at is None
            or not isinstance(transaction_id, str)
            or not transaction_id
            or len(transaction_id) > 256
        ):
            continue
        grouped[(installation_key, transaction_id)].append((at, event))

    metrics: list[Mapping[str, Any]] = []
    findings: list[Finding] = []
    for (installation_key, transaction_id), raw_items in sorted(grouped.items()):
        items = sorted(raw_items, key=lambda item: (item[0], str(item[1].get("state"))))
        pending_items = [item for item in items if item[1].get("state") == "pending"]
        terminal_items = [
            item
            for item in items
            if item[1].get("state") != "pending"
            and item[1].get("history_boundary_seed") is not True
        ]
        actionable_items = sorted(
            (*pending_items, *terminal_items),
            key=lambda item: (item[0], str(item[1].get("state"))),
        )
        # Recorder's include-start terminal state only initializes the query
        # window.  It proves nothing about a transition completed inside the
        # observed window and therefore cannot emit a terminal verdict.
        if not actionable_items:
            continue
        qualifying_attributes = next(
            (
                _event_attributes(event)
                for _at, event in actionable_items
                if _parallel_proof_required(_event_attributes(event))
            ),
            None,
        )
        if qualifying_attributes is None:
            continue
        terminal_at, terminal_event = (
            terminal_items[-1] if terminal_items else (None, None)
        )
        terminal_state = (
            terminal_event.get("state") if terminal_event is not None else None
        )
        pending_event_at = pending_items[-1][0] if pending_items else None
        pending_at = (
            _pending_started_at(*pending_items[-1]) if pending_items else None
        )
        latency = (
            (terminal_at - pending_at).total_seconds()
            if pending_at is not None
            and terminal_at is not None
            and terminal_at >= pending_at
            else None
        )
        transaction_capture_end = _response_capture_end(
            installation_key,
            actionable_items[-1][0],
            capture_windows_by_installation,
        )
        metrics.append(
            {
                "installation_key": installation_key,
                "entity_id": PARALLEL_RESPONSE_ENTITY_ID,
                "family": "parallel_aggregate_response",
                "controller": _response_controller(qualifying_attributes).value,
                "event_count": len(actionable_items),
                "starts": len(pending_items),
                "stops": len(terminal_items),
                "active_minutes": (
                    round(latency / 60.0, 3) if latency is not None else 0.0
                ),
                "longest_active_minutes": (
                    round(latency / 60.0, 3) if latency is not None else 0.0
                ),
                "short_runs": 0,
                "short_run_threshold_seconds": None,
                "transitions": max(len(actionable_items) - 1, 0),
                "max_toggles_per_hour": 0,
                "open": terminal_event is None,
                "first_observed_at": actionable_items[0][0].isoformat(),
                "last_observed_at": actionable_items[-1][0].isoformat(),
                "capture_end": (
                    transaction_capture_end.isoformat()
                    if transaction_capture_end is not None
                    else None
                ),
                "ambiguous_event_count": 0,
                "evidence_truncated": input_truncated,
            }
        )

        if terminal_event is not None and terminal_at is not None:
            terminal_attributes = _event_attributes(terminal_event)
            controller = _response_controller(terminal_attributes)
            evidence = {
                "assessment": (
                    "confirmed"
                    if terminal_state in {"confirmed", "not_confirmed"}
                    else "not_evaluable"
                ),
                "transaction_id": transaction_id,
                "terminal_state": terminal_state,
                "pending_to_terminal_seconds": (
                    round(latency, 3) if latency is not None else None
                ),
                "reason": terminal_attributes.get("reason"),
                "owner": terminal_attributes.get("owner"),
                "evidence_scope": terminal_attributes.get("evidence_scope"),
                "configuration_acknowledgement_scope": (
                    terminal_attributes.get(
                        "configuration_acknowledgement_scope"
                    )
                ),
                "authoritative_expected_power": (
                    terminal_attributes.get("authoritative_expected_power")
                    if isinstance(
                        terminal_attributes.get("authoritative_expected_power"),
                        bool,
                    )
                    else None
                ),
                "expected_power_kw": _finite_attribute(
                    terminal_attributes, "expected_power_kw"
                ),
                "observed_median_power_kw": _finite_attribute(
                    terminal_attributes, "observed_median_power_kw"
                ),
                "observed_spread_kw": _finite_attribute(
                    terminal_attributes, "observed_spread_kw"
                ),
                "sample_count": _finite_attribute(
                    terminal_attributes, "sample_count"
                ),
                "baseline_generation": _finite_attribute(
                    terminal_attributes, "baseline_generation"
                ),
                "final_generation": _finite_attribute(
                    terminal_attributes, "final_generation"
                ),
            }
            if terminal_state == "confirmed":
                findings.append(
                    Finding(
                        code="HISTORY_PARALLEL_AGGREGATE_RESPONSE_CONFIRMED",
                        severity=Severity.INFO,
                        message=(
                            "Historia potwierdza zakończoną zbiorczą odpowiedź "
                            "mocy układu równoległego."
                        ),
                        confidence=Confidence.HIGH,
                        installation_key=installation_key,
                        controller=controller,
                        observed_at=terminal_at,
                        evidence=evidence,
                    )
                )
            elif terminal_state == "not_confirmed":
                findings.append(
                    Finding(
                        code="HISTORY_PARALLEL_AGGREGATE_RESPONSE_NOT_CONFIRMED",
                        severity=Severity.ERROR,
                        message=(
                            "Historia zawiera zakończoną, niepotwierdzoną "
                            "odpowiedź mocy układu równoległego."
                        ),
                        confidence=Confidence.HIGH,
                        installation_key=installation_key,
                        controller=controller,
                        observed_at=terminal_at,
                        evidence=evidence,
                        recommendation=(
                            "Sprawdź reason oraz świeże kompletne generacje "
                            "GRID/PV/LOAD dla tej transakcji."
                        ),
                    )
                )
            else:
                findings.append(
                    Finding(
                        code="HISTORY_PARALLEL_AGGREGATE_RESPONSE_NOT_EVALUABLE",
                        severity=Severity.WARNING,
                        message=(
                            "Historia zawiera nieocenialną odpowiedź mocy "
                            "aktywnego układu równoległego."
                        ),
                        confidence=Confidence.HIGH,
                        installation_key=installation_key,
                        controller=controller,
                        observed_at=terminal_at,
                        evidence=evidence,
                        recommendation=(
                            "Sprawdź reason, topologię i generację pełnego bilansu."
                        ),
                    )
                )

            sampled_peak = _finite_attribute(
                terminal_attributes, "sampled_transition_peak_kw"
            )
            if (
                terminal_attributes.get("sampled_transition_observed") is True
                and sampled_peak is not None
            ):
                findings.append(
                    Finding(
                        code="HISTORY_PARALLEL_TRANSITION_SAMPLE_OBSERVED",
                        severity=Severity.INFO,
                        message=(
                            "Historia zawiera informacyjny pik próbki podczas "
                            "zmiany trybu."
                        ),
                        confidence=Confidence.HIGH,
                        installation_key=installation_key,
                        controller=controller,
                        observed_at=terminal_at,
                        evidence={
                            "assessment": "observed",
                            "transaction_id": transaction_id,
                            "sampled_transition_peak_kw": sampled_peak,
                            "sampled_transition_scope": (
                                terminal_attributes.get(
                                    "sampled_transition_scope"
                                )
                            ),
                        },
                    )
                )
            continue

        if pending_at is None:
            continue
        assert pending_event_at is not None
        capture_end = _response_capture_end(
            installation_key,
            pending_event_at,
            capture_windows_by_installation,
        )
        if capture_end is None or capture_end < pending_at:
            continue
        horizon = _response_horizon(qualifying_attributes)
        pending_age = (capture_end - pending_at).total_seconds()
        timed_out = pending_age > horizon
        findings.append(
            Finding(
                code=(
                    "HISTORY_PARALLEL_AGGREGATE_RESPONSE_PENDING_TIMEOUT"
                    if timed_out
                    else "HISTORY_PARALLEL_AGGREGATE_RESPONSE_PENDING"
                ),
                severity=Severity.ERROR if timed_out else Severity.INFO,
                message=(
                    "Otwarte oczekiwanie na odpowiedź agregatową przekroczyło "
                    "ograniczony horyzont historii."
                    if timed_out
                    else (
                        "Historia kończy się w trakcie zbierania odpowiedzi "
                        "agregatowej."
                    )
                ),
                confidence=Confidence.HIGH,
                installation_key=installation_key,
                controller=_response_controller(qualifying_attributes),
                observed_at=capture_end,
                evidence={
                    "assessment": "confirmed" if timed_out else "pending",
                    "transaction_id": transaction_id,
                    "pending_age_seconds": round(pending_age, 3),
                    "pending_horizon_seconds": horizon,
                },
                recommendation=(
                    "Sprawdź zakończenie transakcji i świeże kompletne generacje."
                    if timed_out
                    else None
                ),
            )
        )
    return tuple(metrics), tuple(findings)


def _capture_windows(
    installation_key: str,
    capture_windows_by_installation: Mapping[str, Any],
    raw_points: Mapping[datetime, set[str]],
) -> tuple[_Interval, ...]:
    """Return merged, explicitly observed history windows.

    Multiple diagnostic bundles can be separated by days.  A state at the
    end of one 24-hour Recorder query must never be extended across the
    unobserved gap to the next query.  The legacy scalar form is retained for
    direct callers and represents one window beginning with the first event.
    """

    raw_windows = capture_windows_by_installation.get(installation_key)
    intervals: list[_Interval] = []
    scalar_end = _parse_aware_datetime(raw_windows)
    if scalar_end is not None:
        if raw_points:
            start = min(raw_points)
            if scalar_end > start:
                intervals.append(_Interval(start, scalar_end))
        return tuple(intervals)

    if isinstance(raw_windows, Mapping):
        candidates: Sequence[Any] = (raw_windows,)
    elif isinstance(raw_windows, Sequence) and not isinstance(
        raw_windows, (str, bytes, bytearray)
    ):
        candidates = raw_windows
    else:
        candidates = ()

    for candidate in candidates:
        if isinstance(candidate, Mapping):
            start_raw = candidate.get("start")
            end_raw = candidate.get("end")
        elif (
            isinstance(candidate, Sequence)
            and not isinstance(candidate, (str, bytes, bytearray))
            and len(candidate) == 2
        ):
            start_raw, end_raw = candidate
        else:
            continue
        start = _parse_aware_datetime(start_raw)
        end = _parse_aware_datetime(end_raw)
        if start is not None and end is not None and end > start:
            intervals.append(_Interval(start, end))
    return _merge_intervals(intervals)


def _merge_intervals(
    intervals: Iterable[_Interval],
) -> tuple[_Interval, ...]:
    ordered = sorted(
        (item for item in intervals if item.end > item.start),
        key=lambda item: (item.start, item.end),
    )
    if not ordered:
        return ()
    merged: list[_Interval] = [ordered[0]]
    for item in ordered[1:]:
        current = merged[-1]
        if item.start <= current.end:
            merged[-1] = _Interval(current.start, max(current.end, item.end))
        else:
            merged.append(item)
    return tuple(merged)


def _state_intervals(
    points: Sequence[_Point],
    state: str,
    capture_end: datetime | None,
) -> tuple[_Interval, ...]:
    intervals: list[_Interval] = []
    for index, point in enumerate(points):
        if point.state != state:
            continue
        end = (
            points[index + 1].at
            if index + 1 < len(points)
            else capture_end
        )
        if end is not None and end > point.at:
            intervals.append(_Interval(point.at, end))
    return _merge_intervals(intervals)


def _max_toggles_per_hour(times: Sequence[datetime]) -> int:
    if not times:
        return 0
    left = 0
    maximum = 0
    for right, current in enumerate(times):
        while (
            left <= right
            and (current - times[left]).total_seconds() > 3600.0
        ):
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def _build_timeline(
    installation_key: str,
    entity_id: str,
    raw_points: Mapping[datetime, set[str]],
    *,
    capture_windows: Sequence[_Interval],
    truncated: bool,
) -> _Timeline:
    all_points = tuple(
        _Point(at, next(iter(states)) if len(states) == 1 else None)
        for at, states in sorted(raw_points.items())
    )
    windows = tuple(capture_windows)
    if not windows and all_points:
        # Without an explicit Recorder window, transitions are still usable,
        # but no state is extended beyond the final observed event.
        windows = (_Interval(all_points[0].at, all_points[-1].at),)

    segments = tuple(
        (
            window,
            tuple(
                point
                for point in all_points
                if window.start <= point.at <= window.end
            ),
        )
        for window in windows
    )
    points = tuple(
        point
        for _window, segment_points in segments
        for point in segment_points
    )
    starts = 0
    stops = 0
    transitions = 0
    transition_times: list[datetime] = []
    completed_short_runs = 0
    active_intervals: list[_Interval] = []
    inactive_intervals: list[_Interval] = []
    open_run = False

    for segment_index, (window, segment_points) in enumerate(segments):
        if not segment_points:
            continue
        previous: str | None = None
        run_start: datetime | None = None
        run_start_observed = False
        for point in segment_points:
            if point.state is None:
                previous = None
                run_start = None
                run_start_observed = False
                continue
            if previous is None:
                previous = point.state
                if point.state == "on":
                    run_start = point.at
                    run_start_observed = False
                continue
            if point.state == previous:
                continue
            transitions += 1
            transition_times.append(point.at)
            if previous == "off" and point.state == "on":
                starts += 1
                run_start = point.at
                run_start_observed = True
            elif previous == "on" and point.state == "off":
                stops += 1
                if (
                    run_start is not None
                    and run_start_observed
                    and 0.0
                    < (point.at - run_start).total_seconds()
                    < SHORT_RUN_SECONDS
                ):
                    completed_short_runs += 1
                run_start = None
                run_start_observed = False
            previous = point.state

        active_intervals.extend(
            _state_intervals(segment_points, "on", window.end)
        )
        inactive_intervals.extend(
            _state_intervals(segment_points, "off", window.end)
        )
        if segment_index == len(segments) - 1:
            open_run = segment_points[-1].state == "on"

    return _Timeline(
        installation_key=installation_key,
        entity_id=entity_id,
        points=points,
        capture_end=(windows[-1].end if windows else None),
        starts=starts,
        stops=stops,
        transitions=transitions,
        transition_times=tuple(transition_times),
        active_intervals=_merge_intervals(active_intervals),
        inactive_intervals=_merge_intervals(inactive_intervals),
        short_runs=completed_short_runs,
        open_run=open_run,
        ambiguous_points=sum(point.state is None for point in points),
        truncated=truncated,
    )


def _overlap(
    first: Sequence[_Interval],
    second: Sequence[_Interval],
) -> _Overlap:
    left = 0
    right = 0
    qualifying: list[_Interval] = []
    while left < len(first) and right < len(second):
        start = max(first[left].start, second[right].start)
        end = min(first[left].end, second[right].end)
        if (
            end > start
            and (end - start).total_seconds()
            >= MIN_VIOLATION_OVERLAP_SECONDS
        ):
            qualifying.append(_Interval(start, end))
        if first[left].end <= second[right].end:
            left += 1
        else:
            right += 1
    if not qualifying:
        return _Overlap()
    durations = [item.seconds for item in qualifying]
    return _Overlap(
        count=len(qualifying),
        total_seconds=sum(durations),
        longest_seconds=max(durations),
        first_start=qualifying[0].start,
        last_end=qualifying[-1].end,
    )


def _metric(timeline: _Timeline, *, input_truncated: bool) -> dict[str, Any]:
    durations = [item.seconds for item in timeline.active_intervals]
    return {
        "installation_key": timeline.installation_key,
        "entity_id": timeline.entity_id,
        "family": HELPER_FAMILY[timeline.entity_id],
        "controller": (
            HELPER_CONTROLLER[timeline.entity_id].value
            if timeline.entity_id in HELPER_CONTROLLER
            else Controller.SYSTEM.value
        ),
        "event_count": len(timeline.points),
        "starts": timeline.starts,
        "stops": timeline.stops,
        "active_minutes": round(sum(durations) / 60.0, 3),
        "longest_active_minutes": round(
            (max(durations) if durations else 0.0) / 60.0,
            3,
        ),
        "short_runs": timeline.short_runs,
        "short_run_threshold_seconds": SHORT_RUN_SECONDS,
        "transitions": timeline.transitions,
        "max_toggles_per_hour": _max_toggles_per_hour(
            timeline.transition_times
        ),
        "open": timeline.open_run,
        "first_observed_at": (
            timeline.points[0].at.isoformat() if timeline.points else None
        ),
        "last_observed_at": (
            timeline.points[-1].at.isoformat() if timeline.points else None
        ),
        "capture_end": (
            timeline.capture_end.isoformat()
            if timeline.capture_end is not None
            else None
        ),
        "ambiguous_event_count": timeline.ambiguous_points,
        "evidence_truncated": timeline.truncated or input_truncated,
    }


def _finding_sort_key(finding: Finding) -> tuple[str, str, str, str]:
    return (
        finding.installation_key or "",
        finding.observed_at.astimezone(timezone.utc).isoformat()
        if finding.observed_at is not None
        else "",
        finding.controller.value if finding.controller is not None else "",
        finding.code,
    )


def _flapping_findings(
    timelines: Mapping[tuple[str, str], _Timeline],
) -> list[Finding]:
    findings: list[Finding] = []
    for key in sorted(timelines):
        timeline = timelines[key]
        toggles = _max_toggles_per_hour(timeline.transition_times)
        if toggles < FLAPPING_TOGGLES_PER_HOUR:
            continue
        findings.append(
            Finding(
                code="HISTORY_HELPER_FLAPPING",
                severity=(
                    Severity.ERROR
                    if toggles >= SEVERE_FLAPPING_TOGGLES_PER_HOUR
                    else Severity.WARNING
                ),
                message="Historia pokazuje częste przełączanie helpera EMS.",
                confidence=Confidence.HIGH,
                installation_key=timeline.installation_key,
                controller=HELPER_CONTROLLER.get(
                    timeline.entity_id, Controller.SYSTEM
                ),
                observed_at=(
                    timeline.transition_times[-1]
                    if timeline.transition_times
                    else None
                ),
                evidence={
                    "assessment": "confirmed",
                    "entity_id": timeline.entity_id,
                    "max_toggles_per_hour": toggles,
                    "transitions": timeline.transitions,
                    "short_runs": timeline.short_runs,
                    "threshold": FLAPPING_TOGGLES_PER_HOUR,
                },
                recommendation=(
                    "Sprawdź continuation gate, stabilizację intencji, interlock "
                    "oraz przyczynę restartowania cyklu."
                ),
            )
        )
    return findings


def _condition_finding(
    *,
    installation_key: str,
    active: Sequence[_Interval],
    blocked: Sequence[_Interval],
    code: str,
    controller: Controller,
    message: str,
    active_helper: str,
    blocking_helper: str,
    recommendation: str,
) -> Finding | None:
    overlap = _overlap(active, blocked)
    if overlap.count == 0:
        return None
    return Finding(
        code=code,
        severity=Severity.CRITICAL,
        message=message,
        confidence=Confidence.HIGH,
        installation_key=installation_key,
        controller=controller,
        observed_at=overlap.last_end,
        evidence={
            "assessment": "confirmed",
            "active_helper": active_helper,
            "blocking_helper": blocking_helper,
            "minimum_overlap_seconds": MIN_VIOLATION_OVERLAP_SECONDS,
            "overlap_count": overlap.count,
            "overlap_seconds": round(overlap.total_seconds, 3),
            "longest_overlap_seconds": round(overlap.longest_seconds, 3),
            "first_overlap_start": (
                overlap.first_start.isoformat()
                if overlap.first_start is not None
                else None
            ),
        },
        recommendation=recommendation,
        occurrences=overlap.count,
    )


def _family_intervals(
    installation_key: str,
    family: str,
    timelines: Mapping[tuple[str, str], _Timeline],
) -> tuple[_Interval, ...]:
    return _merge_intervals(
        interval
        for entity_id in FAMILY_HELPERS[family]
        for interval in timelines.get(
            (installation_key, entity_id),
            _EMPTY_TIMELINE,
        ).active_intervals
    )


_EMPTY_TIMELINE = _Timeline(
    installation_key="",
    entity_id="",
    points=(),
    capture_end=None,
    starts=0,
    stops=0,
    transitions=0,
    transition_times=(),
    active_intervals=(),
    inactive_intervals=(),
    short_runs=0,
    open_run=False,
    ambiguous_points=0,
    truncated=False,
)


def _safety_findings(
    timelines: Mapping[tuple[str, str], _Timeline],
) -> list[Finding]:
    findings: list[Finding] = []
    installations = sorted({key[0] for key in timelines})
    for installation_key in installations:
        rce = timelines.get((installation_key, RCE_ACTIVE))
        tariff = timelines.get((installation_key, TARIFF_ACTIVE))
        ems_gate = timelines.get((installation_key, EMS_EXECUTION_READY))
        sale_block = timelines.get((installation_key, SALE_BLOCK_ACTIVE))
        direct_gate = timelines.get(
            (installation_key, DIRECT_REGISTER_EXECUTION_READY)
        )
        rcem_direct_intervals = _merge_intervals(
            interval
            for helper in (RCEM_ACTIVE, RCEM_EXPORT_ACTIVE)
            for interval in timelines.get(
                (installation_key, helper), _EMPTY_TIMELINE
            ).active_intervals
        )
        rcem_pre = timelines.get(
            (installation_key, RCEM_PRE_DISCHARGE_ACTIVE)
        )

        # Each check requires both explicit timelines.  An absent gate is
        # unknown evidence and intentionally produces no verdict.
        candidates: tuple[Finding | None, ...] = (
            _condition_finding(
                installation_key=installation_key,
                active=rce.active_intervals if rce is not None else (),
                blocked=(
                    ems_gate.inactive_intervals
                    if rce is not None and ems_gate is not None
                    else ()
                ),
                code="HISTORY_RCE_ACTIVE_WITH_EMS_GATE_OFF",
                controller=Controller.RCE,
                message="RCE było aktywne przy wyłączonej gotowości EMS.",
                active_helper=RCE_ACTIVE,
                blocking_helper=EMS_EXECUTION_READY,
                recommendation="Sprawdź gate wykonania i neutralny rollback RCE.",
            ),
            _condition_finding(
                installation_key=installation_key,
                active=tariff.active_intervals if tariff is not None else (),
                blocked=(
                    ems_gate.inactive_intervals
                    if tariff is not None and ems_gate is not None
                    else ()
                ),
                code="HISTORY_TARIFF_ACTIVE_WITH_EMS_GATE_OFF",
                controller=Controller.TARIFF,
                message=(
                    "Ładowanie taryfowe było aktywne przy wyłączonej "
                    "gotowości EMS."
                ),
                active_helper=TARIFF_ACTIVE,
                blocking_helper=EMS_EXECUTION_READY,
                recommendation=(
                    "Sprawdź control-data gate i rollback ładowania taryfowego."
                ),
            ),
            _condition_finding(
                installation_key=installation_key,
                active=rce.active_intervals if rce is not None else (),
                blocked=(
                    sale_block.active_intervals
                    if rce is not None and sale_block is not None
                    else ()
                ),
                code="HISTORY_RCE_ACTIVE_DURING_SALE_BLOCK",
                controller=Controller.RCE,
                message="RCE było aktywne podczas blokady sprzedaży.",
                active_helper=RCE_ACTIVE,
                blocking_helper=SALE_BLOCK_ACTIVE,
                recommendation="Sprawdź automat sale-block i ścieżkę stop/restore.",
            ),
            _condition_finding(
                installation_key=installation_key,
                active=rcem_direct_intervals,
                blocked=(
                    direct_gate.inactive_intervals
                    if rcem_direct_intervals and direct_gate is not None
                    else ()
                ),
                code="HISTORY_RCEM_ACTIVE_WITH_DIRECT_GATE_OFF",
                controller=Controller.RCEM,
                message=(
                    "RCEm było aktywne przy wyłączonej gotowości bezpośrednich "
                    "rejestrów."
                ),
                active_helper="rcem_charge_or_export_family",
                blocking_helper=DIRECT_REGISTER_EXECUTION_READY,
                recommendation=(
                    "Sprawdź FC03/readback i zatrzymaj zapis RCEm do czasu "
                    "odzyskania gate."
                ),
            ),
            _condition_finding(
                installation_key=installation_key,
                active=(
                    rcem_pre.active_intervals if rcem_pre is not None else ()
                ),
                blocked=(
                    ems_gate.inactive_intervals
                    if rcem_pre is not None and ems_gate is not None
                    else ()
                ),
                code="HISTORY_RCEM_PRE_DISCHARGE_WITH_EMS_GATE_OFF",
                controller=Controller.RCEM,
                message=(
                    "RCEm pre-discharge było aktywne przy wyłączonej "
                    "gotowości EMS."
                ),
                active_helper=RCEM_PRE_DISCHARGE_ACTIVE,
                blocking_helper=EMS_EXECUTION_READY,
                recommendation=(
                    "Sprawdź FC03 EMS i rollback toru pre-discharge."
                ),
            ),
        )
        findings.extend(item for item in candidates if item is not None)

        manual_charge = timelines.get(
            (installation_key, MANUAL_CHARGE_ACTIVE), _EMPTY_TIMELINE
        )
        manual_discharge = timelines.get(
            (installation_key, MANUAL_DISCHARGE_ACTIVE), _EMPTY_TIMELINE
        )
        manual_overlap = _overlap(
            manual_charge.active_intervals,
            manual_discharge.active_intervals,
        )
        if manual_overlap.count:
            findings.append(
                Finding(
                    code="HISTORY_MANUAL_DIRECTION_CONFLICT",
                    severity=Severity.CRITICAL,
                    message=(
                        "Historia potwierdza jednoczesne ręczne ładowanie i "
                        "rozładowanie."
                    ),
                    confidence=Confidence.HIGH,
                    installation_key=installation_key,
                    controller=Controller.SYSTEM,
                    observed_at=manual_overlap.last_end,
                    evidence={
                        "assessment": "confirmed",
                        "overlap_count": manual_overlap.count,
                        "overlap_seconds": round(
                            manual_overlap.total_seconds, 3
                        ),
                    },
                    recommendation="Sprawdź flagi cykli ręcznych i interlock.",
                    occurrences=manual_overlap.count,
                )
            )

        pre_direct_overlap = _overlap(
            rcem_pre.active_intervals if rcem_pre is not None else (),
            rcem_direct_intervals,
        )
        if pre_direct_overlap.count:
            findings.append(
                Finding(
                    code="HISTORY_RCEM_SUBPATH_CONFLICT",
                    severity=Severity.CRITICAL,
                    message=(
                        "Historia potwierdza overlap RCEm pre-discharge z "
                        "torem charge/export."
                    ),
                    confidence=Confidence.HIGH,
                    installation_key=installation_key,
                    controller=Controller.RCEM,
                    observed_at=pre_direct_overlap.last_end,
                    evidence={
                        "assessment": "confirmed",
                        "overlap_count": pre_direct_overlap.count,
                        "overlap_seconds": round(
                            pre_direct_overlap.total_seconds, 3
                        ),
                    },
                    recommendation="Sprawdź handover i rollback torów RCEm.",
                    occurrences=pre_direct_overlap.count,
                )
            )

        family_intervals = {
            family: _family_intervals(installation_key, family, timelines)
            for family in FAMILY_HELPERS
        }
        families = sorted(family_intervals)
        for index, first_family in enumerate(families):
            for second_family in families[index + 1 :]:
                overlap = _overlap(
                    family_intervals[first_family],
                    family_intervals[second_family],
                )
                if overlap.count == 0:
                    continue
                findings.append(
                    Finding(
                        code="HISTORY_ACTIVE_FAMILY_OVERLAP",
                        severity=Severity.CRITICAL,
                        message=(
                            "Historia potwierdza jednoczesną aktywność różnych "
                            "rodzin sterowania EMS."
                        ),
                        confidence=Confidence.HIGH,
                        installation_key=installation_key,
                        controller=Controller.SYSTEM,
                        observed_at=overlap.last_end,
                        evidence={
                            "assessment": "confirmed",
                            "first_family": first_family,
                            "second_family": second_family,
                            "minimum_overlap_seconds": (
                                MIN_VIOLATION_OVERLAP_SECONDS
                            ),
                            "overlap_count": overlap.count,
                            "overlap_seconds": round(
                                overlap.total_seconds, 3
                            ),
                            "longest_overlap_seconds": round(
                                overlap.longest_seconds, 3
                            ),
                        },
                        recommendation=(
                            "Sprawdź ownership/interlock i potwierdź powrót do "
                            "neutralnego Self-Use."
                        ),
                        occurrences=overlap.count,
                    )
                )
    return findings


def analyze_control_history(
    events: Iterable[Mapping[str, Any]],
    capture_windows_by_installation: Mapping[str, Any],
    *,
    input_truncated: bool = False,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Finding, ...]]:
    """Return deterministic helper metrics and evidence-backed findings.

    ``events`` is expected to be the deduplicated, bounded output of
    :func:`diagnostics_analysis.extractors.merge_events`.  This function still
    applies an independent hard cap and exact deduplication so it remains safe
    when called directly.
    """

    points_by_helper: dict[
        tuple[str, str], dict[datetime, set[str]]
    ] = defaultdict(lambda: defaultdict(set))
    response_events: list[Mapping[str, Any]] = []
    exact_seen: set[tuple[str, str, datetime, str]] = set()
    locally_truncated = False

    for index, event in enumerate(events):
        if index >= MAX_INPUT_EVENTS:
            locally_truncated = True
            break
        if not isinstance(event, Mapping):
            continue
        installation_key = event.get("installation_key")
        entity_id = event.get("entity_id")
        state = event.get("state")
        if entity_id == PARALLEL_RESPONSE_ENTITY_ID:
            response_events.append(event)
        if (
            not isinstance(installation_key, str)
            or not installation_key
            or len(installation_key) > 256
            or not isinstance(entity_id, str)
            or entity_id not in HELPER_FAMILY
            or not isinstance(state, str)
        ):
            continue
        normalized_state = state.strip().casefold()
        if normalized_state not in {"on", "off"}:
            continue
        at = _event_datetime(event)
        if at is None:
            continue
        identity = (installation_key, entity_id, at, normalized_state)
        if identity in exact_seen:
            continue
        exact_seen.add(identity)
        points_by_helper[(installation_key, entity_id)][at].add(
            normalized_state
        )

    timelines: dict[tuple[str, str], _Timeline] = {}
    for key in sorted(points_by_helper):
        installation_key, entity_id = key
        raw_points = points_by_helper[key]
        truncated = len(raw_points) > MAX_EVENTS_PER_HELPER
        if truncated:
            # Retain the newest bounded window.  The first retained state is
            # left-censored, so it cannot manufacture a short-run finding.
            raw_points = dict(
                sorted(raw_points.items())[-MAX_EVENTS_PER_HELPER:]
            )
        timelines[key] = _build_timeline(
            installation_key,
            entity_id,
            raw_points,
            capture_windows=_capture_windows(
                installation_key,
                capture_windows_by_installation,
                raw_points,
            ),
            truncated=truncated,
        )

    helper_metrics = tuple(
        _metric(
            timelines[key],
            input_truncated=input_truncated or locally_truncated,
        )
        for key in sorted(timelines)
    )
    response_metrics, response_findings = _analyze_parallel_response_history(
        response_events,
        capture_windows_by_installation,
        input_truncated=input_truncated or locally_truncated,
    )
    metrics = (*helper_metrics, *response_metrics)
    findings = [
        *_flapping_findings(timelines),
        *_safety_findings(timelines),
        *response_findings,
    ]
    return metrics, tuple(sorted(findings, key=_finding_sort_key))


__all__ = [
    "FLAPPING_TOGGLES_PER_HOUR",
    "MAX_EVENTS_PER_HELPER",
    "MAX_INPUT_EVENTS",
    "MIN_VIOLATION_OVERLAP_SECONDS",
    "SHORT_RUN_SECONDS",
    "analyze_control_history",
]
