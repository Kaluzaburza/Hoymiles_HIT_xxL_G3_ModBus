"""Recorder-backed voltage history for the RCEm overvoltage controller."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta


GRID_VOLTAGE_ENTITIES = (
    "sensor.hoymiles_hit_grid_voltage_l1",
    "sensor.hoymiles_hit_grid_voltage_l2",
    "sensor.hoymiles_hit_grid_voltage_l3",
)

SLOTS_PER_DAY = 96
SLOT_MINUTES = 15


@dataclass(frozen=True, slots=True)
class VoltageHistorySummary:
    """Compact four-day voltage model in 15-minute slots."""

    history_days: int
    sample_count: int
    profile_median_v: tuple[float, ...]
    profile_p90_v: tuple[float, ...]
    daily_peak_v: dict[str, float]
    risk_windows: tuple[tuple[int, int, float], ...]


def _quantile(values: Sequence[float], fraction: float) -> float:
    """Return a linearly interpolated quantile without third-party packages."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = min(max(fraction, 0.0), 1.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _merge_risk_windows(
    profile_p90_v: Sequence[float],
    *,
    risk_voltage_v: float,
) -> tuple[tuple[int, int, float], ...]:
    windows: list[tuple[int, int, float]] = []
    start: int | None = None
    peak = 0.0
    for slot, voltage in enumerate(profile_p90_v):
        risky = voltage >= risk_voltage_v
        if risky and start is None:
            start = slot
            peak = voltage
        elif risky:
            peak = max(peak, voltage)
        elif start is not None:
            windows.append(
                (start * SLOT_MINUTES, slot * SLOT_MINUTES, round(peak, 2))
            )
            start = None
            peak = 0.0
    if start is not None:
        windows.append(
            (start * SLOT_MINUTES, 24 * 60, round(peak, 2))
        )
    return tuple(windows)


def summarize_voltage_history(
    samples_by_entity: Mapping[str, Sequence[tuple[datetime, float]]],
    *,
    now: datetime,
    history_days: int = 4,
    risk_voltage_v: float = 248.5,
) -> VoltageHistorySummary:
    """Build a robust daily voltage profile from the previous complete days.

    Each day/slot is first reduced to the 95th percentile of every phase.  The
    four daily values are then reduced to a median and P90 profile.  This keeps
    one corrupt recorder sample from defining a complete risk window while
    retaining repeated short overvoltage events.
    """
    oldest = now.date() - timedelta(days=history_days)
    newest = now.date() - timedelta(days=1)
    raw: dict[tuple[object, int], list[float]] = defaultdict(list)
    sample_count = 0

    for entity_id in GRID_VOLTAGE_ENTITIES:
        for timestamp, voltage in samples_by_entity.get(entity_id, ()):
            local = timestamp.astimezone(now.tzinfo)
            if not (oldest <= local.date() <= newest):
                continue
            if not 180.0 <= voltage <= 280.0:
                continue
            slot = local.hour * 4 + local.minute // SLOT_MINUTES
            raw[(local.date(), slot)].append(voltage)
            sample_count += 1

    per_slot_days: list[list[float]] = [[] for _ in range(SLOTS_PER_DAY)]
    daily_peak: dict[str, float] = {}
    covered_days: set[object] = set()
    for (day, slot), values in raw.items():
        daily_slot_p95 = _quantile(values, 0.95)
        per_slot_days[slot].append(daily_slot_p95)
        key = day.isoformat()
        daily_peak[key] = max(daily_peak.get(key, 0.0), max(values))
        covered_days.add(day)

    # A predictive control window needs independent evidence from at least two
    # complete local days for that exact quarter-hour.  Overall history_days
    # alone is insufficient: a single sparse 254 V sample must not acquire the
    # authority of a repeated daily pattern.  P90 remains a diagnostic of all
    # available samples, but the median/control profile fails closed to 0 V.
    median_profile = tuple(
        round(_quantile(values, 0.50), 2) if len(values) >= 2 else 0.0
        for values in per_slot_days
    )
    p90_profile = tuple(
        round(_quantile(values, 0.90), 2) if values else 0.0
        for values in per_slot_days
    )
    return VoltageHistorySummary(
        history_days=len(covered_days),
        sample_count=sample_count,
        profile_median_v=median_profile,
        profile_p90_v=p90_profile,
        daily_peak_v={key: round(value, 2) for key, value in sorted(daily_peak.items())},
        # The median must confirm the window on more than one day.  P90 is
        # still exposed as an early-warning diagnostic, but a single corrupt
        # or exceptional day cannot schedule active control by itself.
        risk_windows=_merge_risk_windows(
            median_profile,
            risk_voltage_v=risk_voltage_v,
        ),
    )
