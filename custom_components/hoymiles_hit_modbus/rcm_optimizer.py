"""Pure planning and feedback logic for RCEm 253 V+ automation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import ceil


MINIMUM_CHARGE_LIMIT_PERCENT = 10.0
P_U_START_V = 248.4
CONTROL_TARGET_V = 249.2
WARNING_V = 251.0
EMERGENCY_V = 252.2


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def _within_window(minute: int, start: int, end: int) -> bool:
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


@dataclass(frozen=True, slots=True)
class RCMProfileSelection:
    """One normalized 48-slot forecast/profile selection."""

    slot_kwh: tuple[float, ...]
    source: str
    confidence: float
    selected_total_kwh: float


@dataclass(frozen=True, slots=True)
class RCMRiskWindowInput:
    """Forecast energy balance for one recurring voltage-risk window."""

    start_minute: int
    end_minute: int
    peak_voltage_v: float
    day_offset: int
    expected_pv_kwh: float
    expected_load_kwh: float
    expected_surplus_kwh: float
    natural_headroom_before_kwh: float


@dataclass(frozen=True, slots=True)
class RCMRiskWindowPlan:
    """Battery-space requirement calculated for one risk window."""

    start_minute: int
    end_minute: int
    peak_voltage_v: float
    day_offset: int
    expected_pv_kwh: float
    expected_load_kwh: float
    expected_surplus_kwh: float
    required_headroom_kwh: float
    projected_headroom_before_kwh: float
    cumulative_headroom_shortfall_kwh: float


def _valid_48_slot_profile(values: Sequence[float] | None) -> tuple[float, ...]:
    """Return a sanitized profile or an empty tuple when it is unusable."""
    if values is None or isinstance(values, (str, bytes)):
        return ()
    try:
        if len(values) != 48:
            return ()
        profile = tuple(max(float(value), 0.0) for value in values)
    except (TypeError, ValueError):
        return ()
    return profile if sum(profile) > 0 else ()


def select_rcm_load_profile(
    *,
    weekend: bool,
    average_profile: Sequence[float] | None,
    weekday_profile: Sequence[float] | None,
    weekend_profile: Sequence[float] | None,
    average_daily_kwh: float,
) -> RCMProfileSelection:
    """Select weekday/weekend LOAD history with conservative fallbacks."""
    preferred = _valid_48_slot_profile(
        weekend_profile if weekend else weekday_profile
    )
    if preferred:
        return RCMProfileSelection(
            preferred,
            "weekend_48_slot" if weekend else "weekday_48_slot",
            0.95,
            sum(preferred),
        )
    average = _valid_48_slot_profile(average_profile)
    if average:
        return RCMProfileSelection(
            average,
            "average_48_slot_fallback",
            0.75,
            sum(average),
        )
    cross_day = _valid_48_slot_profile(
        weekday_profile if weekend else weekend_profile
    )
    if cross_day:
        return RCMProfileSelection(
            cross_day,
            "cross_day_48_slot_fallback",
            0.55,
            sum(cross_day),
        )
    try:
        daily = max(float(average_daily_kwh), 0.0)
    except (TypeError, ValueError):
        daily = 0.0
    flat = (daily / 48.0,) * 48
    return RCMProfileSelection(
        flat,
        "flat_daily_fallback",
        0.30 if daily > 0 else 0.0,
        daily,
    )


def select_rcm_pv_profile(
    *,
    forecast_total_kwh: float,
    forecast_p90_total_kwh: float | None,
    detailed_p50_by_slot: Mapping[int, float] | None,
    detailed_p90_by_slot: Mapping[int, float] | None,
    first_slot: int,
    current_slot_fraction: float = 1.0,
    risk_slots: Sequence[int] = (),
) -> RCMProfileSelection:
    """Prefer Solcast 30-minute intervals and retain a safe shaped fallback.

    P90 is used as the headroom energy envelope.  If Solcast only exposes
    detailed P50 intervals, their shape is scaled to the P90 total.  This is
    safer than discarding the detailed timing or pretending that the P50
    total describes a high-production day.
    """
    try:
        first = int(_clamp(float(first_slot), 0.0, 47.0))
    except (TypeError, ValueError):
        first = 0
    try:
        fraction = _clamp(float(current_slot_fraction), 0.0, 1.0)
    except (TypeError, ValueError):
        fraction = 1.0
    try:
        expected_total = max(float(forecast_total_kwh), 0.0)
    except (TypeError, ValueError):
        expected_total = 0.0
    try:
        p90_total = (
            max(float(forecast_p90_total_kwh), 0.0)
            if forecast_p90_total_kwh is not None
            else None
        )
    except (TypeError, ValueError):
        p90_total = None
    target_total = p90_total if p90_total is not None else expected_total

    def sanitize(
        values: Mapping[int, float] | None,
    ) -> tuple[list[float], set[int]]:
        profile = [0.0] * 48
        present: set[int] = set()
        if not isinstance(values, Mapping):
            return profile, present
        for raw_slot, raw_value in values.items():
            try:
                slot = int(raw_slot)
                value = max(float(raw_value), 0.0)
            except (TypeError, ValueError):
                continue
            if slot < first or slot >= 48:
                continue
            profile[slot] += value
            present.add(slot)
        if first in present:
            profile[first] *= fraction
        return profile, present

    p90_profile, p90_present = sanitize(detailed_p90_by_slot)
    p50_profile, p50_present = sanitize(detailed_p50_by_slot)
    relevant_slots: set[int] = set()
    for raw_slot in risk_slots:
        try:
            slot = int(raw_slot)
        except (TypeError, ValueError):
            continue
        if first <= slot < 48:
            relevant_slots.add(slot)

    def covers_risk(present: set[int]) -> bool:
        # A partial detailed forecast must not silently turn missing risk slots
        # into 0 kWh.  Prefer another complete detailed percentile, otherwise
        # use the conservative shaped-total fallback.
        return not relevant_slots or relevant_slots.issubset(present)

    if p90_present and sum(p90_profile) > 0 and covers_risk(p90_present):
        selected = p90_profile
        present = p90_present
        source = "solcast_30m_p90"
        base_confidence = 0.95
    elif p50_present and sum(p50_profile) > 0 and covers_risk(p50_present):
        selected = p50_profile
        present = p50_present
        source = (
            "solcast_30m_p50_shape_p90_total"
            if p90_total is not None
            else "solcast_30m_p50"
        )
        base_confidence = 0.82 if p90_total is not None else 0.78
    else:
        selected = [0.0] * 48
        present = set()
        for slot in range(max(12, first), 42):
            distance = abs(slot - 27) / 15.0
            selected[slot] = max(1.0 - distance * distance, 0.0)
        source = (
            "solcast_p90_total_shaped_fallback"
            if p90_total is not None
            else "solcast_total_shaped_fallback"
        )
        base_confidence = 0.50 if p90_total is not None else 0.40

    total = sum(selected)
    if total > 0 and target_total > 0:
        selected = [value * target_total / total for value in selected]
    elif target_total <= 0:
        selected = [0.0] * 48

    if present and relevant_slots:
        coverage = len(present & relevant_slots) / len(relevant_slots)
        confidence = base_confidence * (0.65 + 0.35 * coverage)
    else:
        confidence = base_confidence
    return RCMProfileSelection(
        tuple(selected),
        source,
        round(_clamp(confidence, 0.0, 1.0), 3),
        round(sum(selected), 6),
    )


@dataclass(frozen=True, slots=True)
class RCMOptimizerInput:
    """Current system state and the compact four-day risk model."""

    now: datetime
    voltage_l1_v: float
    voltage_l2_v: float
    voltage_l3_v: float
    filtered_voltage_v: float
    rolling_10m_voltage_v: float
    historical_p90_voltage_v: float
    risk_windows: tuple[tuple[int, int, float], ...]
    history_days: int
    pv_power_kw: float
    load_power_kw: float
    grid_export_power_kw: float
    battery_capacity_kwh: float
    battery_soc_percent: float
    reserve_soc_percent: float
    safety_margin_soc_percent: float
    protected_minimum_soc_percent: float
    expected_risk_surplus_kwh: float
    expected_natural_headroom_kwh: float
    minutes_to_risk: int | None
    risk_day_offset: int
    system_power_kw: float
    battery_voltage_v: float | None
    bms_max_charge_current_a: float | None
    bms_max_discharge_current_a: float | None
    current_charge_limit_percent: float
    saved_charge_limit_percent: float
    export_control_enabled: bool
    current_export_limit_percent: float
    saved_export_limit_percent: float
    user_export_cap_percent: float
    charge_efficiency_percent: float = 95.0
    expected_pre_risk_surplus_kwh: float = 0.0
    risk_window_forecasts: tuple[RCMRiskWindowInput, ...] = ()
    expected_unavoidable_charge_input_kwh: float | None = None


@dataclass(frozen=True, slots=True)
class RCMOptimizerResult:
    """One deterministic recommendation for the RCEm controller."""

    status_code: str
    action: str
    maximum_voltage_v: float
    control_voltage_v: float
    voltage_risk_score: float
    risk_window_active: bool
    next_risk_start_minute: int | None
    reserve_soc_percent: float
    protected_minimum_soc_percent: float
    required_headroom_kwh: float
    available_headroom_kwh: float
    headroom_shortfall_kwh: float
    expected_natural_headroom_kwh: float
    planned_grid_discharge_kwh: float
    pre_discharge_target_soc_percent: float
    pre_discharge_power_kw: float
    pre_discharge_power_percent: float
    pre_discharge_ready: bool
    target_soc_before_risk_percent: float
    pv_surplus_power_kw: float
    bms_charge_power_limit_kw: float
    recommended_charge_limit_percent: float
    recommended_charge_power_kw: float
    effective_export_cap_percent: float
    recommended_export_limit_percent: float
    estimated_safe_export_power_kw: float | None
    saturated: bool
    unavoidable_minimum_charge_kwh: float
    risk_window_plans: tuple[RCMRiskWindowPlan, ...]


def optimize_rcm(settings: RCMOptimizerInput) -> RCMOptimizerResult:
    """Return a bounded, ramp-limited PV charge recommendation.

    The controller uses the global battery charge limit and, when explicitly
    enabled, a bounded maximum-export limit.  It never requests Grid Charge,
    GCF, phase-unbalance or protection-setting changes.
    """
    if settings.battery_capacity_kwh <= 0 or settings.system_power_kw <= 0:
        raise ValueError("battery capacity and system power must be positive")
    if settings.history_days < 0:
        raise ValueError("history_days cannot be negative")

    maximum_voltage = max(
        settings.voltage_l1_v,
        settings.voltage_l2_v,
        settings.voltage_l3_v,
    )
    control_voltage = max(
        maximum_voltage,
        settings.filtered_voltage_v,
        settings.rolling_10m_voltage_v + 0.25,
    )
    fast_score = _clamp(
        (control_voltage - P_U_START_V) / (EMERGENCY_V - P_U_START_V),
        0.0,
        1.0,
    )
    history_score = _clamp(
        (settings.historical_p90_voltage_v - P_U_START_V)
        / (EMERGENCY_V - P_U_START_V),
        0.0,
        1.0,
    )
    risk_score = max(fast_score, history_score * 0.35)

    minute = settings.now.hour * 60 + settings.now.minute
    active_window = any(
        _within_window(minute, start, end)
        for start, end, _peak in settings.risk_windows
    )
    future_starts = sorted(
        start if start > minute else start + 24 * 60
        for start, _end, _peak in settings.risk_windows
    )
    next_risk_start = future_starts[0] if future_starts else None

    base_reserve_soc = _clamp(
        settings.reserve_soc_percent + settings.safety_margin_soc_percent,
        0.0,
        100.0,
    )
    protected_minimum_soc = _clamp(
        max(base_reserve_soc, settings.protected_minimum_soc_percent),
        0.0,
        100.0,
    )
    reserve_soc = protected_minimum_soc
    usable_capacity = settings.battery_capacity_kwh * (100.0 - reserve_soc) / 100.0
    efficiency = _clamp(settings.charge_efficiency_percent, 1.0, 100.0) / 100.0
    pre_risk_hours = max(
        min(float(settings.minutes_to_risk or 0), 24 * 60) / 60.0,
        0.0,
    )
    minimum_charge_floor_kw = (
        settings.system_power_kw * MINIMUM_CHARGE_LIMIT_PERCENT / 100.0
    )
    if settings.expected_unavoidable_charge_input_kwh is not None:
        unavoidable_charge_input = max(
            settings.expected_unavoidable_charge_input_kwh,
            0.0,
        )
    else:
        # Backward-compatible fallback for pure callers without a slot model.
        unavoidable_charge_input = min(
            max(settings.expected_pre_risk_surplus_kwh, 0.0),
            minimum_charge_floor_kw * pre_risk_hours,
        )
    unavoidable_minimum_charge = unavoidable_charge_input * efficiency
    aggregate_required_headroom = min(
        # Headroom is battery-side stored energy.  Charging losses reduce the
        # amount that reaches the cells; dividing here would reserve too much
        # space and needlessly sacrifice earlier export.
        max(settings.expected_risk_surplus_kwh, 0.0) * efficiency
        + unavoidable_minimum_charge,
        usable_capacity,
    )
    available_headroom = settings.battery_capacity_kwh * (
        100.0 - _clamp(settings.battery_soc_percent, 0.0, 100.0)
    ) / 100.0
    natural_headroom = min(
        max(settings.expected_natural_headroom_kwh, 0.0),
        max(
            settings.battery_capacity_kwh
            * (
                _clamp(settings.battery_soc_percent, 0.0, 100.0)
                - protected_minimum_soc
            )
            / 100.0,
            0.0,
        ),
    )
    available_above_protected = max(
        settings.battery_capacity_kwh
        * (
            _clamp(settings.battery_soc_percent, 0.0, 100.0)
            - protected_minimum_soc
        )
        / 100.0,
        0.0,
    )
    window_plans: list[RCMRiskWindowPlan] = []
    cumulative_window_requirement = 0.0
    operational_required_headroom = 0.0
    window_discharge_requirement = 0.0
    for index, window in enumerate(
        sorted(
            settings.risk_window_forecasts,
            key=lambda item: (item.day_offset, item.start_minute),
        )
    ):
        incremental_requirement = (
            max(window.expected_surplus_kwh, 0.0) * efficiency
            + (unavoidable_minimum_charge if index == 0 else 0.0)
        )
        natural_before = min(
            max(window.natural_headroom_before_kwh, 0.0),
            available_above_protected,
        )
        projected_before = max(
            available_headroom
            + natural_before
            - cumulative_window_requirement,
            0.0,
        )
        cumulative_window_requirement += incremental_requirement
        required_from_initial_headroom = max(
            cumulative_window_requirement - natural_before,
            0.0,
        )
        operational_required_headroom = max(
            operational_required_headroom,
            required_from_initial_headroom,
        )
        cumulative_shortfall = max(
            required_from_initial_headroom - available_headroom,
            0.0,
        )
        window_discharge_requirement = max(
            window_discharge_requirement,
            cumulative_shortfall,
        )
        window_plans.append(
            RCMRiskWindowPlan(
                start_minute=window.start_minute,
                end_minute=window.end_minute,
                peak_voltage_v=round(window.peak_voltage_v, 3),
                day_offset=window.day_offset,
                expected_pv_kwh=round(max(window.expected_pv_kwh, 0.0), 3),
                expected_load_kwh=round(max(window.expected_load_kwh, 0.0), 3),
                expected_surplus_kwh=round(
                    max(window.expected_surplus_kwh, 0.0),
                    3,
                ),
                required_headroom_kwh=round(incremental_requirement, 3),
                projected_headroom_before_kwh=round(projected_before, 3),
                cumulative_headroom_shortfall_kwh=round(
                    cumulative_shortfall,
                    3,
                ),
            )
        )
    required_headroom = (
        min(operational_required_headroom, usable_capacity)
        if window_plans
        else aggregate_required_headroom
    )
    headroom_shortfall = max(required_headroom - available_headroom, 0.0)
    planned_grid_discharge = (
        min(window_discharge_requirement, usable_capacity)
        if window_plans
        else max(
            required_headroom - available_headroom - natural_headroom,
            0.0,
        )
    )
    planned_grid_discharge = min(
        planned_grid_discharge,
        available_above_protected,
    )
    pre_discharge_target_soc = _clamp(
        settings.battery_soc_percent
        - planned_grid_discharge / settings.battery_capacity_kwh * 100.0,
        protected_minimum_soc,
        100.0,
    )
    target_soc = _clamp(
        100.0 - required_headroom / settings.battery_capacity_kwh * 100.0,
        reserve_soc,
        100.0,
    )

    bms_limit_kw = settings.system_power_kw
    if (
        settings.battery_voltage_v is not None
        and settings.bms_max_charge_current_a is not None
        and settings.battery_voltage_v > 0
        and settings.bms_max_charge_current_a > 0
    ):
        bms_limit_kw = min(
            bms_limit_kw,
            settings.battery_voltage_v
            * settings.bms_max_charge_current_a
            / 1000.0,
        )
    maximum_limit_percent = _clamp(
        bms_limit_kw / settings.system_power_kw * 100.0,
        MINIMUM_CHARGE_LIMIT_PERCENT,
        100.0,
    )
    pv_surplus = max(settings.pv_power_kw - settings.load_power_kw, 0.0)

    current_limit = _clamp(
        settings.current_charge_limit_percent,
        MINIMUM_CHARGE_LIMIT_PERCENT,
        100.0,
    )
    saved_limit = _clamp(
        settings.saved_charge_limit_percent,
        MINIMUM_CHARGE_LIMIT_PERCENT,
        100.0,
    )
    risk_ahead = next_risk_start is not None and next_risk_start > minute

    if settings.history_days == 0:
        status = "learning"
        action = "restore"
        unconstrained_target = saved_limit
    elif maximum_voltage >= 253.0:
        status = "emergency"
        action = "absorb_pv"
        unconstrained_target = maximum_limit_percent
    elif active_window or control_voltage >= P_U_START_V:
        status = "controlling"
        if pv_surplus <= 0.05:
            action = "monitor"
            unconstrained_target = saved_limit
        else:
            # The predicted profile starts the battery gently.  Live voltage
            # then takes over and can use the complete BMS-safe power.
            desired_absorption_kw = min(
                pv_surplus * max(risk_score, 0.20 if active_window else 0.0),
                bms_limit_kw,
            )
            unconstrained_target = _clamp(
                desired_absorption_kw / settings.system_power_kw * 100.0,
                MINIMUM_CHARGE_LIMIT_PERCENT,
                maximum_limit_percent,
            )
            if control_voltage >= WARNING_V:
                unconstrained_target = max(
                    unconstrained_target,
                    min(75.0, maximum_limit_percent),
                )
            if control_voltage >= EMERGENCY_V:
                unconstrained_target = maximum_limit_percent
            action = (
                "absorb_pv"
                if unconstrained_target >= current_limit
                else "release_export"
            )
    elif (
        settings.risk_day_offset == 0
        and planned_grid_discharge > 0.1
        and control_voltage < P_U_START_V
    ):
        status = "preparing_discharge"
        action = "grid_discharge_preparation"
        unconstrained_target = MINIMUM_CHARGE_LIMIT_PERCENT
    elif risk_ahead and settings.battery_soc_percent >= target_soc - 0.5:
        status = "preparing_headroom"
        action = "preserve_headroom"
        unconstrained_target = MINIMUM_CHARGE_LIMIT_PERCENT
    else:
        status = "ready"
        action = "restore"
        unconstrained_target = saved_limit

    # Normal regulation changes the global battery limit by at most ten
    # percentage points per run.  The emergency path may move by 25 points.
    if maximum_voltage >= 253.0:
        # At the legal disconnection boundary there is no time for the normal
        # minute-by-minute ramp.  Use all BMS-safe absorption immediately.
        recommended = maximum_limit_percent
    else:
        recommended = _clamp(
            unconstrained_target,
            current_limit - 10.0,
            current_limit + 10.0,
        )
    recommended = _clamp(
        recommended,
        MINIMUM_CHARGE_LIMIT_PERCENT,
        maximum_limit_percent,
    )
    recommended_power = settings.system_power_kw * recommended / 100.0
    saturated = (
        recommended >= maximum_limit_percent - 0.05
        and control_voltage >= WARNING_V
    )

    # The contractual/user cap is a hard ceiling.  The controller may reduce
    # export below it but can never raise the inverter above either the value
    # found at activation or the explicit limit selected by the user.
    effective_export_cap = _clamp(
        min(
            settings.saved_export_limit_percent,
            settings.user_export_cap_percent,
        ),
        0.0,
        100.0,
    )
    current_export_limit = _clamp(
        settings.current_export_limit_percent,
        0.0,
        100.0,
    )
    if not settings.export_control_enabled:
        recommended_export_limit = current_export_limit
    elif maximum_voltage >= 253.0:
        recommended_export_limit = 0.0
    elif current_export_limit > effective_export_cap:
        recommended_export_limit = effective_export_cap
    elif control_voltage >= EMERGENCY_V:
        recommended_export_limit = current_export_limit - 15.0
    elif control_voltage >= WARNING_V and (
        saturated or available_headroom <= 0.25
    ):
        recommended_export_limit = current_export_limit - 5.0
    elif control_voltage <= P_U_START_V:
        recommended_export_limit = current_export_limit + 5.0
    else:
        recommended_export_limit = current_export_limit
    recommended_export_limit = _clamp(
        recommended_export_limit,
        0.0,
        effective_export_cap,
    )

    bms_discharge_limit_kw = settings.system_power_kw
    if (
        settings.battery_voltage_v is not None
        and settings.bms_max_discharge_current_a is not None
        and settings.battery_voltage_v > 0
        and settings.bms_max_discharge_current_a > 0
    ):
        bms_discharge_limit_kw = min(
            bms_discharge_limit_kw,
            settings.battery_voltage_v
            * settings.bms_max_discharge_current_a
            / 1000.0,
        )
    export_capacity_kw = (
        settings.system_power_kw * effective_export_cap / 100.0
    )
    discharge_window_hours = max(
        ((settings.minutes_to_risk or 0) - 30) / 60.0,
        0.0,
    )
    if (
        planned_grid_discharge > 0.1
        and discharge_window_hours > 0
        and export_capacity_kw > 0.1
    ):
        desired_grid_export_kw = max(
            planned_grid_discharge / discharge_window_hours * 1.10,
            settings.system_power_kw * 0.05,
        )
        pre_discharge_power = min(
            desired_grid_export_kw + max(settings.load_power_kw, 0.0),
            bms_discharge_limit_kw,
            export_capacity_kw + max(settings.load_power_kw, 0.0),
        )
    else:
        pre_discharge_power = 0.0
    pre_discharge_power_percent = _clamp(
        pre_discharge_power / settings.system_power_kw * 100.0,
        0.0,
        100.0,
    )
    pre_discharge_ready = (
        settings.risk_day_offset == 0
        and planned_grid_discharge > 0.1
        and pre_discharge_power > 0.1
        and export_capacity_kw > 0.1
        and maximum_voltage < P_U_START_V
        and settings.rolling_10m_voltage_v < CONTROL_TARGET_V
        and (settings.minutes_to_risk or 0) > 30
        and settings.battery_soc_percent > pre_discharge_target_soc + 0.5
    )

    # This estimate is intentionally conservative until local voltage/export
    # sensitivity has been learned from a site with actual export.
    safe_export_factor = _clamp(
        (EMERGENCY_V - control_voltage) / (EMERGENCY_V - CONTROL_TARGET_V),
        0.0,
        1.0,
    )
    export_limit_kw = (
        settings.system_power_kw * recommended_export_limit / 100.0
    )
    safe_export = (
        max(
            min(
                pv_surplus,
                settings.grid_export_power_kw
                if settings.grid_export_power_kw > 0.05
                else pv_surplus,
                export_limit_kw,
            )
            * safe_export_factor,
            0.0,
        )
        if pv_surplus > 0.05
        else None
    )
    if saturated and status == "controlling":
        status = "battery_limited"

    return RCMOptimizerResult(
        status_code=status,
        action=action,
        maximum_voltage_v=round(maximum_voltage, 2),
        control_voltage_v=round(control_voltage, 2),
        voltage_risk_score=round(risk_score * 100.0, 1),
        risk_window_active=active_window,
        next_risk_start_minute=next_risk_start,
        reserve_soc_percent=round(reserve_soc, 1),
        protected_minimum_soc_percent=round(protected_minimum_soc, 1),
        required_headroom_kwh=round(required_headroom, 3),
        available_headroom_kwh=round(available_headroom, 3),
        headroom_shortfall_kwh=round(headroom_shortfall, 3),
        expected_natural_headroom_kwh=round(natural_headroom, 3),
        planned_grid_discharge_kwh=round(planned_grid_discharge, 3),
        pre_discharge_target_soc_percent=round(
            ceil(pre_discharge_target_soc * 10.0) / 10.0,
            1,
        ),
        pre_discharge_power_kw=round(pre_discharge_power, 3),
        pre_discharge_power_percent=round(pre_discharge_power_percent, 1),
        pre_discharge_ready=pre_discharge_ready,
        target_soc_before_risk_percent=round(ceil(target_soc * 10.0) / 10.0, 1),
        pv_surplus_power_kw=round(pv_surplus, 3),
        bms_charge_power_limit_kw=round(bms_limit_kw, 3),
        recommended_charge_limit_percent=round(recommended, 1),
        recommended_charge_power_kw=round(recommended_power, 3),
        effective_export_cap_percent=round(effective_export_cap, 1),
        recommended_export_limit_percent=round(
            recommended_export_limit,
            1,
        ),
        estimated_safe_export_power_kw=(
            round(safe_export, 3) if safe_export is not None else None
        ),
        saturated=saturated,
        unavoidable_minimum_charge_kwh=round(
            unavoidable_minimum_charge,
            3,
        ),
        risk_window_plans=tuple(window_plans),
    )
