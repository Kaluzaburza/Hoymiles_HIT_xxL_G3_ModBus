"""Pure planning and feedback logic for RCEm 253 V+ automation."""

from __future__ import annotations

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
    estimated_safe_export_power_kw: float
    saturated: bool


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
    required_headroom = min(
        # Headroom is battery-side stored energy.  Charging losses reduce the
        # amount that reaches the cells; dividing here would reserve too much
        # space and needlessly sacrifice earlier export.
        max(settings.expected_risk_surplus_kwh, 0.0) * efficiency,
        usable_capacity,
    )
    available_headroom = settings.battery_capacity_kwh * (
        100.0 - _clamp(settings.battery_soc_percent, 0.0, 100.0)
    ) / 100.0
    headroom_shortfall = max(required_headroom - available_headroom, 0.0)
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
    planned_grid_discharge = max(
        required_headroom - available_headroom - natural_headroom,
        0.0,
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
    safe_export = max(
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
        estimated_safe_export_power_kw=round(safe_export, 3),
        saturated=saturated,
    )
