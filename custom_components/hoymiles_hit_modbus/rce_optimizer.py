"""Pure RCE energy-planning helpers.

The optimizer is intentionally independent from Home Assistant so the energy
model can be tested without importing Home Assistant.  It maximizes expected
RCE revenue while preserving enough battery energy for the house, including
the protected night after the second market day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


SLOT = timedelta(minutes=30)
_PERIOD_START = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})")


@dataclass(frozen=True, slots=True)
class PriceSlot:
    """One half-hour market slot."""

    start: datetime
    price_pln_kwh: float
    blocked: bool = False


@dataclass(frozen=True, slots=True)
class PlannedExport:
    """Scheduled AC export for one market slot."""

    start: datetime
    price_pln_kwh: float
    energy_kwh: float

    @property
    def revenue_pln(self) -> float:
        """Return expected revenue for the slot."""
        return self.price_pln_kwh * self.energy_kwh


@dataclass(slots=True)
class OptimizerInput:
    """Inputs required by the RCE optimizer."""

    now: datetime
    price_slots: list[PriceSlot]
    pv_by_slot_kwh: Mapping[datetime, float]
    battery_capacity_kwh: float
    battery_soc_percent: float
    outage_reserve_soc_percent: float
    safety_margin_soc_percent: float
    manual_minimum_soc_percent: float
    dynamic_reserve_enabled: bool
    average_daily_load_kwh: float
    average_night_load_kwh: float | None
    night_start_minute: int
    night_end_minute: int
    inverter_power_kw: float
    inverter_count: int
    discharge_power_percent: float
    export_efficiency_percent: float
    bms_max_discharge_current_a: float | None = None
    battery_voltage_v: float | None = None
    bms_power_safety_percent: float = 95.0
    actual_day_load_today_kwh: float | None = None
    pv_to_load_power_kw: float = 0.0
    # Optional recorder profiles contain one kWh value for every half-hour.
    # They keep household peaks instead of spreading day/night energy flat.
    load_profile_30m_kwh: tuple[float, ...] = ()
    weekday_load_profile_30m_kwh: tuple[float, ...] = ()
    weekend_load_profile_30m_kwh: tuple[float, ...] = ()
    # P50 drives expected revenue; this risk-adjusted P10/P50 blend is used
    # for every reserve and physical-feasibility check.
    conservative_pv_by_slot_kwh: Mapping[datetime, float] | None = None
    forecast_confidence_percent: float = 0.0
    # Additional physical caps.  GCF is an installation/DSO limit, while the
    # effective value may be learned from delivered power or another sensor.
    export_power_cap_kw: float | None = None
    effective_export_power_kw: float | None = None
    # Economic defaults require no new helper.  Stored energy is credited only
    # when a Day-3 PV deficit exists; battery wear applies to DC throughput.
    avoided_import_price_pln_kwh: float = 1.0
    battery_wear_cost_pln_kwh: float = 0.08
    day3_pv_forecast_kwh: float | None = None
    charge_efficiency_percent: float = 95.0
    house_discharge_efficiency_percent: float = 95.0


@dataclass(slots=True)
class OptimizerResult:
    """Calculated RCE plan and diagnostics."""

    ready: bool
    status_code: str
    minimum_soc_percent: int
    base_reserve_energy_kwh: float
    protected_night_energy_kwh: float
    additional_forecast_reserve_kwh: float
    protected_home_energy_kwh: float
    available_energy_now_kwh: float
    planned_exports: list[PlannedExport] = field(default_factory=list)
    natural_export_kwh: float = 0.0
    natural_revenue_pln: float = 0.0
    uncontrolled_export_kwh: float = 0.0
    uncontrolled_revenue_pln: float = 0.0
    ending_battery_kwh: float = 0.0
    system_power_kw: float = 0.0
    requested_export_power_kw: float = 0.0
    bms_discharge_power_limit_kw: float | None = None
    bms_discharge_limit_percent: float | None = None
    bms_limit_active: bool = False
    maximum_export_power_kw: float = 0.0
    historical_day_load_kwh: float = 0.0
    live_projected_day_load_kwh: float = 0.0
    modeled_day_load_kwh: float = 0.0
    daylight_progress_percent: float = 0.0
    load_profile_mode: str = "flat_day_night_fallback"
    forecast_confidence_percent: float = 0.0
    export_power_cap_kw: float | None = None
    effective_export_power_kw: float | None = None
    physical_limit_source: str = "requested_power"
    battery_wear_cost_pln: float = 0.0
    control_reserve_energy_kwh: float = 0.0
    soc_quantization_reserve_kwh: float = 0.0
    day3_forecast_available: bool = False
    day3_forecast_kwh: float | None = None
    day3_load_requirement_kwh: float = 0.0
    day3_energy_shortfall_kwh: float = 0.0
    terminal_reserve_reason: str = "day3_forecast_missing"
    terminal_energy_target_kwh: float = 0.0
    terminal_energy_value_pln_kwh: float = 0.0
    terminal_energy_value_pln: float = 0.0
    baseline_terminal_energy_value_pln: float = 0.0
    net_objective_pln: float = 0.0
    baseline_net_objective_pln: float = 0.0

    @property
    def planned_export_kwh(self) -> float:
        """Return scheduled export energy."""
        return sum(item.energy_kwh for item in self.planned_exports)

    @property
    def planned_revenue_pln(self) -> float:
        """Return scheduled export revenue."""
        return sum(item.revenue_pln for item in self.planned_exports)

    @property
    def total_export_kwh(self) -> float:
        """Return scheduled plus unavoidable PV export."""
        return self.planned_export_kwh + self.natural_export_kwh

    @property
    def total_revenue_pln(self) -> float:
        """Return scheduled plus unavoidable PV-export revenue."""
        return self.planned_revenue_pln + self.natural_revenue_pln

    @property
    def automatic_price_floor_pln_kwh(self) -> float | None:
        """Return the lowest price selected by the optimized plan."""
        if not self.planned_exports:
            return None
        return min(item.price_pln_kwh for item in self.planned_exports)

    @property
    def optimization_gain_pln(self) -> float:
        """Return the legacy gross-revenue gain.

        Keep this property for dashboard and package compatibility.  New
        consumers should use :attr:`gross_optimization_gain_pln` or
        :attr:`net_optimization_gain_pln`, whose names state whether battery
        wear and terminal stored-energy value are included.
        """
        return self.gross_optimization_gain_pln

    @property
    def gross_optimization_gain_pln(self) -> float:
        """Return gross revenue gained over uncontrolled PV export."""
        return self.total_revenue_pln - self.uncontrolled_revenue_pln

    @property
    def net_optimization_gain_pln(self) -> float:
        """Return economic gain including wear and terminal battery value."""
        return self.net_objective_pln - self.baseline_net_objective_pln

    @property
    def terminal_energy_value_delta_pln(self) -> float:
        """Return retained-energy value added relative to no RCE control."""
        return (
            self.terminal_energy_value_pln
            - self.baseline_terminal_energy_value_pln
        )


def floor_half_hour(value: datetime) -> datetime:
    """Floor an aware datetime to a half-hour boundary."""
    return value.replace(
        minute=0 if value.minute < 30 else 30,
        second=0,
        microsecond=0,
    )


def blocked_minute(
    minute: int,
    start_minute: int,
    end_minute: int,
    enabled: bool,
) -> bool:
    """Return whether a minute of day is in a configured lockout."""
    if not enabled or start_minute == end_minute:
        return False
    if start_minute < end_minute:
        return start_minute <= minute < end_minute
    return minute >= start_minute or minute < end_minute


def parse_rce_rows(
    rows: Iterable[Mapping[str, Any]],
    timezone: ZoneInfo,
    *,
    block_enabled: bool,
    block_start_minute: int,
    block_end_minute: int,
) -> list[PriceSlot]:
    """Convert PSE 15-minute rows to half-hour market slots."""
    parsed: list[tuple[datetime, float]] = []
    for row in rows:
        business_date = str(row.get("business_date", ""))
        period = str(row.get("period", ""))
        match = _PERIOD_START.search(period)
        if not business_date or match is None:
            continue
        try:
            day = date.fromisoformat(business_date)
            start = datetime.combine(
                day,
                time(
                    hour=int(match.group("hour")) % 24,
                    minute=int(match.group("minute")),
                ),
                tzinfo=timezone,
            )
            price = float(row["rce_pln"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        parsed.append((start, price))

    parsed.sort(key=lambda item: item[0])
    result: list[PriceSlot] = []
    for index in range(0, len(parsed) - 1, 2):
        first, second = parsed[index], parsed[index + 1]
        if second[0] - first[0] > timedelta(minutes=20):
            continue
        minute = first[0].hour * 60 + first[0].minute
        result.append(
            PriceSlot(
                start=first[0],
                price_pln_kwh=(first[1] + second[1]) / 2.0,
                blocked=blocked_minute(
                    minute,
                    block_start_minute,
                    block_end_minute,
                    block_enabled,
                ),
            )
        )
    return result


def _is_night(
    moment: datetime,
    start_minute: int,
    end_minute: int,
) -> bool:
    minute = moment.hour * 60 + moment.minute
    if start_minute == end_minute:
        return False
    if start_minute < end_minute:
        return start_minute <= minute < end_minute
    return minute >= start_minute or minute < end_minute


def _horizon_end(settings: OptimizerInput) -> datetime:
    """Extend the two-day market horizon through the following protected night."""
    if settings.price_slots:
        last_market_day = max(slot.start.date() for slot in settings.price_slots)
    else:
        last_market_day = settings.now.date() + timedelta(days=1)
    end_day = last_market_day + timedelta(days=1)
    hour, minute = divmod(settings.night_end_minute, 60)
    return datetime.combine(
        end_day,
        time(hour=hour % 24, minute=minute),
        tzinfo=settings.now.tzinfo,
    )


def _day_load_projection(
    settings: OptimizerInput,
) -> tuple[float, float, float, float]:
    """Return historical, live and selected daytime-load estimates.

    ``PV to Load Energy Today`` is a direct inverter counter.  It is used only
    for the elapsed part of the current daylight window, so energy already
    consumed by the house is never subtracted from the future PV forecast a
    second time.  The live projection may increase the historical estimate but
    never reduce it; a cloudy morning must not weaken the home reserve.
    """

    night_minutes = (
        settings.night_end_minute - settings.night_start_minute
    ) % (24 * 60)
    night_hours = max(night_minutes / 60.0, 0.5)
    daily = max(settings.average_daily_load_kwh, 0.0)
    if settings.average_night_load_kwh is None:
        night_energy = daily * night_hours / 24.0
    else:
        night_energy = min(max(settings.average_night_load_kwh, 0.0), daily)
    historical_day_energy = max(daily - night_energy, 0.0)

    day_minutes = max((24 * 60) - night_minutes, 30)
    current_minute = settings.now.hour * 60 + settings.now.minute
    day_start = settings.night_end_minute % (24 * 60)
    day_end = settings.night_start_minute % (24 * 60)
    elapsed = (current_minute - day_start) % (24 * 60)
    in_daylight_window = elapsed < day_minutes
    if in_daylight_window:
        progress = min(max(elapsed / day_minutes, 0.0), 1.0)
    elif day_start <= day_end:
        # With the normal European solar window, the hours before day_start
        # belong to the new day and those after day_end to the completed day.
        progress = 1.0 if current_minute >= day_end else 0.0
    else:
        progress = 0.0

    actual_day_load = max(settings.actual_day_load_today_kwh or 0.0, 0.0)
    live_projection = 0.0
    if progress >= 0.05 and actual_day_load > 0:
        # Cap a transiently high early projection.  The cap is deliberately
        # generous: it protects the house while preventing one 0.1 kWh counter
        # step just after sunrise from reserving the entire battery.
        projection_cap = max(
            daily * 2.0,
            historical_day_energy,
            actual_day_load,
        )
        live_projection = min(
            actual_day_load / progress,
            projection_cap,
        )
    modeled_day_energy = max(historical_day_energy, live_projection)
    return (
        historical_day_energy,
        live_projection,
        modeled_day_energy,
        progress,
    )


def _load_by_slot(
    settings: OptimizerInput,
    starts: list[datetime],
    *,
    historical_day_energy: float,
    live_projected_day_energy: float,
    modeled_day_energy: float,
    daylight_progress: float,
) -> tuple[dict[datetime, float], str]:
    night_minutes = (
        settings.night_end_minute - settings.night_start_minute
    ) % (24 * 60)
    night_hours = max(night_minutes / 60.0, 0.5)
    daily = max(settings.average_daily_load_kwh, 0.0)
    if settings.average_night_load_kwh is None:
        night_energy = daily * night_hours / 24.0
    else:
        night_energy = min(max(settings.average_night_load_kwh, 0.0), daily)
    night_slot = night_energy / night_hours * 0.5

    def valid_profile(values: tuple[float, ...]) -> tuple[float, ...]:
        if len(values) != 48:
            return ()
        normalized = tuple(max(float(value), 0.0) for value in values)
        return normalized if sum(normalized) > 0 else ()

    average_profile = valid_profile(settings.load_profile_30m_kwh)
    weekday_profile = valid_profile(settings.weekday_load_profile_30m_kwh)
    weekend_profile = valid_profile(settings.weekend_load_profile_30m_kwh)

    if average_profile or weekday_profile or weekend_profile:
        loads: dict[datetime, float] = {}
        modes: set[str] = set()
        dates = sorted({start.date() for start in starts})
        for slot_date in dates:
            weekend = slot_date.weekday() >= 5
            profile = weekend_profile if weekend else weekday_profile
            if profile:
                modes.add("weekend_48_slot" if weekend else "weekday_48_slot")
            else:
                profile = average_profile
                if profile:
                    modes.add("average_48_slot")
            if not profile:
                # One day type can be absent after a fresh installation.  Use
                # whichever learned profile exists instead of dropping back to
                # a flat curve for that day only.
                profile = weekday_profile or weekend_profile
                modes.add("cross_day_48_slot_fallback")

            night_indexes = [
                index
                for index in range(48)
                if _is_night(
                    datetime.combine(
                        slot_date,
                        time(hour=index // 2, minute=(index % 2) * 30),
                        tzinfo=settings.now.tzinfo,
                    ),
                    settings.night_start_minute,
                    settings.night_end_minute,
                )
            ]
            night_weight = sum(profile[index] for index in night_indexes)
            day_weight = max(sum(profile) - night_weight, 0.0)
            day_indexes = [
                index for index in range(48) if index not in night_indexes
            ]
            night_scale = (
                night_energy / night_weight if night_weight > 0 else 0.0
            )
            day_scale = (
                modeled_day_energy / day_weight if day_weight > 0 else 0.0
            )
            for start in (item for item in starts if item.date() == slot_date):
                index = start.hour * 2 + start.minute // 30
                is_night = _is_night(
                    start,
                    settings.night_start_minute,
                    settings.night_end_minute,
                )
                if is_night and night_weight <= 0:
                    loads[start] = night_energy / max(len(night_indexes), 1)
                elif not is_night and day_weight <= 0:
                    loads[start] = modeled_day_energy / max(len(day_indexes), 1)
                else:
                    loads[start] = profile[index] * (
                        night_scale if is_night else day_scale
                    )

        # For the unfinished daylight window, actual measured consumption may
        # imply more remaining energy than the historical curve.  Scale only
        # future daytime slots upward; never weaken the historical reserve.
        today_day_starts = [
            start
            for start in starts
            if start.date() == settings.now.date()
            and not _is_night(
                start,
                settings.night_start_minute,
                settings.night_end_minute,
            )
        ]
        if today_day_starts:
            historical_remaining = historical_day_energy * max(
                1.0 - daylight_progress,
                0.0,
            )
            live_remaining = max(
                live_projected_day_energy
                - max(settings.actual_day_load_today_kwh or 0.0, 0.0),
                0.0,
            )
            current_profile_remaining = sum(
                loads[start] for start in today_day_starts
            )
            target_remaining = max(
                current_profile_remaining,
                historical_remaining,
                live_remaining,
            )
            if current_profile_remaining > 0:
                scale = target_remaining / current_profile_remaining
                for start in today_day_starts:
                    loads[start] *= scale
            elif target_remaining > 0:
                per_slot = target_remaining / len(today_day_starts)
                for start in today_day_starts:
                    loads[start] = per_slot

        if {"weekday_48_slot", "weekend_48_slot"} <= modes:
            mode = "weekday_weekend_48_slot"
        elif "weekday_48_slot" in modes:
            mode = "weekday_48_slot"
        elif "weekend_48_slot" in modes:
            mode = "weekend_48_slot"
        elif "average_48_slot" in modes:
            mode = "average_48_slot"
        else:
            mode = "cross_day_48_slot_fallback"
        return loads, mode

    loads: dict[datetime, float] = {}
    day_slots_by_date: dict[date, list[datetime]] = {}
    for start in starts:
        if _is_night(
            start,
            settings.night_start_minute,
            settings.night_end_minute,
        ):
            loads[start] = night_slot
        else:
            day_slots_by_date.setdefault(start.date(), []).append(start)

    for slot_date, day_starts in day_slots_by_date.items():
        day_energy = modeled_day_energy
        if slot_date == settings.now.date():
            historical_remaining = historical_day_energy * max(
                1.0 - daylight_progress,
                0.0,
            )
            live_remaining = max(
                live_projected_day_energy
                - max(settings.actual_day_load_today_kwh or 0.0, 0.0),
                0.0,
            )
            day_energy = max(historical_remaining, live_remaining)
        per_slot = day_energy / max(len(day_starts), 1)
        for start in day_starts:
            loads[start] = per_slot
    return loads, "flat_day_night_fallback"


def _quantize_reserve_to_soc_percent(
    energy_kwh: float,
    capacity_kwh: float,
) -> float:
    """Round a protected DC-energy reserve up to a whole inverter SOC step.

    Hoymiles Force Discharge SOC accepts complete percentage points.  A
    continuous optimizer that retained 25.39% while commanding 26% would
    overstate exportable energy by 0.61% of the battery.  Quantizing every
    per-slot control reserve upward models the register that will actually be
    written and can only make the plan more conservative.
    """
    if capacity_kwh <= 0:
        return max(energy_kwh, 0.0)
    percent = math.ceil(
        min(max(energy_kwh / capacity_kwh * 100.0, 0.0), 100.0) - 1e-9
    )
    return capacity_kwh * percent / 100.0


def _simulate(
    starts: list[datetime],
    settings: OptimizerInput,
    load_by_slot: Mapping[datetime, float],
    exports: Mapping[datetime, float],
    floor_kwh: float,
    export_reserve_by_slot: Mapping[datetime, float] | None = None,
    pv_by_slot_kwh: Mapping[datetime, float] | None = None,
) -> tuple[bool, float, dict[datetime, float]]:
    capacity = settings.battery_capacity_kwh
    battery = capacity * settings.battery_soc_percent / 100.0
    efficiency = max(
        min(settings.export_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    charge_efficiency = max(
        min(settings.charge_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    house_discharge_efficiency = max(
        min(settings.house_discharge_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    natural_exports: dict[datetime, float] = {}
    pv_map = pv_by_slot_kwh or settings.pv_by_slot_kwh
    for start in starts:
        pv = max(float(pv_map.get(start, 0.0)), 0.0)
        load = max(float(load_by_slot.get(start, 0.0)), 0.0)
        if pv >= load:
            battery += (pv - load) * charge_efficiency
        else:
            battery -= (load - pv) / house_discharge_efficiency
        battery -= max(float(exports.get(start, 0.0)), 0.0) / efficiency
        if battery < floor_kwh - 1e-6:
            return False, battery, {}
        if (
            exports.get(start, 0.0) > 0
            and export_reserve_by_slot is not None
            and battery
            < max(float(export_reserve_by_slot.get(start, floor_kwh)), floor_kwh)
            - 1e-6
        ):
            return False, battery, {}
        if battery > capacity:
            overflow_ac = (
                battery - capacity
            ) / charge_efficiency
            if settings.export_power_cap_kw is not None:
                remaining_export_headroom = max(
                    settings.export_power_cap_kw * 0.5
                    - max(float(exports.get(start, 0.0)), 0.0),
                    0.0,
                )
                overflow_ac = min(overflow_ac, remaining_export_headroom)
            if overflow_ac > 0:
                natural_exports[start] = overflow_ac
            battery = capacity
    return True, battery, natural_exports


def _market_revenue(
    exports: Mapping[datetime, float],
    natural_exports: Mapping[datetime, float],
    price_by_start: Mapping[datetime, float],
) -> float:
    """Return revenue from scheduled and natural exports."""
    return sum(
        energy * price_by_start.get(start, 0.0)
        for start, energy in exports.items()
    ) + sum(
        energy * price_by_start.get(start, 0.0)
        for start, energy in natural_exports.items()
    )


def _required_energy_now(
    starts: list[datetime],
    settings: OptimizerInput,
    load_by_slot: Mapping[datetime, float],
    floor_kwh: float,
    pv_by_slot_kwh: Mapping[datetime, float] | None = None,
) -> float:
    required = floor_kwh
    capacity = settings.battery_capacity_kwh
    pv_map = pv_by_slot_kwh or settings.pv_by_slot_kwh
    charge_efficiency = max(
        min(settings.charge_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    discharge_efficiency = max(
        min(settings.house_discharge_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    for start in reversed(starts):
        load = max(float(load_by_slot.get(start, 0.0)), 0.0)
        pv = max(float(pv_map.get(start, 0.0)), 0.0)
        if pv >= load:
            required -= (pv - load) * charge_efficiency
        else:
            required += (load - pv) / discharge_efficiency
        required = min(max(required, floor_kwh), capacity)
    return required


def _economic_objective(
    *,
    exports: Mapping[datetime, float],
    natural_exports: Mapping[datetime, float],
    price_by_start: Mapping[datetime, float],
    ending_battery_kwh: float,
    floor_kwh: float,
    export_efficiency: float,
    battery_wear_cost_pln_kwh: float,
    terminal_energy_target_kwh: float,
    terminal_energy_value_pln_kwh: float,
) -> tuple[float, float, float]:
    """Return net objective, wear cost and credited terminal value."""

    revenue = _market_revenue(exports, natural_exports, price_by_start)
    dc_throughput = sum(max(value, 0.0) for value in exports.values()) / max(
        export_efficiency,
        0.01,
    )
    wear = dc_throughput * max(battery_wear_cost_pln_kwh, 0.0)
    retained = min(
        max(ending_battery_kwh - floor_kwh, 0.0),
        max(terminal_energy_target_kwh, 0.0),
    )
    terminal_value = retained * max(terminal_energy_value_pln_kwh, 0.0)
    return revenue - wear + terminal_value, wear, terminal_value


def _protected_night_reserve_by_slot(
    starts: list[datetime],
    settings: OptimizerInput,
    load_by_slot: Mapping[datetime, float],
    floor_kwh: float,
) -> tuple[float, dict[datetime, float]]:
    """Return current and per-export reserves for the next protected night.

    PV expected before sunset must not erase the explicit night reserve.  For
    every possible export slot we therefore retain the base outage reserve plus
    all forecast house load still remaining in the current or next protected
    night window.  The reserve decreases only while that night is actually
    consumed and is rebuilt for the following night after sunrise.
    """

    capacity = settings.battery_capacity_kwh
    discharge_efficiency = max(
        min(settings.house_discharge_efficiency_percent / 100.0, 1.0),
        0.01,
    )

    def remaining_night_energy(after_index: int) -> float:
        entered_night = False
        energy = 0.0
        for later in starts[after_index:]:
            is_night = _is_night(
                later,
                settings.night_start_minute,
                settings.night_end_minute,
            )
            if not entered_night:
                if not is_night:
                    continue
                entered_night = True
            elif not is_night:
                break
            energy += (
                max(float(load_by_slot.get(later, 0.0)), 0.0)
                / discharge_efficiency
            )
        return energy

    current_night_energy = remaining_night_energy(0)
    reserve_by_slot = {
        start: _quantize_reserve_to_soc_percent(
            min(
                floor_kwh + remaining_night_energy(index + 1),
                capacity,
            ),
            capacity,
        )
        for index, start in enumerate(starts)
    }
    return current_night_energy, reserve_by_slot


def optimize_rce(settings: OptimizerInput) -> OptimizerResult:
    """Return the most valuable feasible RCE export plan."""
    capacity = settings.battery_capacity_kwh
    if (
        capacity <= 0
        or settings.average_daily_load_kwh < 0
        or settings.inverter_power_kw <= 0
        or settings.inverter_count <= 0
    ):
        return OptimizerResult(
            ready=False,
            status_code="missing_data",
            minimum_soc_percent=100,
            base_reserve_energy_kwh=0.0,
            protected_night_energy_kwh=0.0,
            additional_forecast_reserve_kwh=0.0,
            protected_home_energy_kwh=0.0,
            available_energy_now_kwh=0.0,
        )

    now_slot = floor_half_hour(settings.now)
    horizon_end = _horizon_end(settings)
    starts: list[datetime] = []
    cursor = now_slot
    while cursor < horizon_end:
        starts.append(cursor)
        cursor += SLOT

    (
        historical_day_load,
        live_projected_day_load,
        modeled_day_load,
        daylight_progress,
    ) = _day_load_projection(settings)
    load_by_slot, load_profile_mode = _load_by_slot(
        settings,
        starts,
        historical_day_energy=historical_day_load,
        live_projected_day_energy=live_projected_day_load,
        modeled_day_energy=modeled_day_load,
        daylight_progress=daylight_progress,
    )
    conservative_pv = (
        settings.conservative_pv_by_slot_kwh
        if settings.conservative_pv_by_slot_kwh is not None
        else settings.pv_by_slot_kwh
    )
    if settings.dynamic_reserve_enabled:
        base_soc = min(
            max(
                settings.outage_reserve_soc_percent
                + settings.safety_margin_soc_percent,
                0.0,
            ),
            100.0,
        )
    else:
        base_soc = min(max(settings.manual_minimum_soc_percent, 0.0), 100.0)
    floor_kwh = capacity * base_soc / 100.0
    protected_night_energy, export_reserve_by_slot = (
        _protected_night_reserve_by_slot(
            starts,
            settings,
            load_by_slot,
            floor_kwh,
        )
        if settings.dynamic_reserve_enabled
        else (0.0, {})
    )
    required_now = (
        _required_energy_now(
            starts,
            settings,
            load_by_slot,
            floor_kwh,
            conservative_pv,
        )
        if settings.dynamic_reserve_enabled
        else floor_kwh
    )
    if settings.dynamic_reserve_enabled:
        # Keep the upcoming night explicit even when a sunny forecast before
        # sunset would otherwise reduce the backward energy requirement.
        required_now = max(
            required_now,
            min(floor_kwh + protected_night_energy, capacity),
        )
    minimum_soc = math.ceil(required_now / capacity * 100.0 - 1e-9)
    minimum_soc = min(max(minimum_soc, math.ceil(base_soc)), 100)
    control_reserve = capacity * minimum_soc / 100.0
    quantization_reserve = max(control_reserve - required_now, 0.0)
    if export_reserve_by_slot:
        # The scheduler writes one whole-percent Force Discharge SOC for the
        # current plan, not a fractional/per-slot value.  Every planned export
        # must therefore respect at least that exact register-level reserve.
        export_reserve_by_slot = {
            start: max(reserve, control_reserve)
            for start, reserve in export_reserve_by_slot.items()
        }
    current_energy = capacity * settings.battery_soc_percent / 100.0
    available_now = max(current_energy - control_reserve, 0.0)

    baseline_ok, baseline_end, _ = _simulate(
        starts,
        settings,
        load_by_slot,
        {},
        floor_kwh,
        pv_by_slot_kwh=conservative_pv,
    )
    _, baseline_expected_end, baseline_natural = _simulate(
        starts,
        settings,
        load_by_slot,
        {},
        floor_kwh,
        pv_by_slot_kwh=settings.pv_by_slot_kwh,
    )
    system_power = settings.inverter_power_kw * settings.inverter_count
    requested_power = system_power * min(
        max(settings.discharge_power_percent, 0.0),
        100.0,
    ) / 100.0
    bms_power_limit: float | None = None
    if (
        settings.bms_max_discharge_current_a is not None
        and settings.bms_max_discharge_current_a > 0
        and settings.battery_voltage_v is not None
        and settings.battery_voltage_v > 0
    ):
        # Register 1917 is the dynamic DC-current limit reported by the BMS.
        # Convert it to safe AC export power and keep a separate guard below
        # that limit so voltage/temperature changes do not trip the battery.
        bms_power_limit = (
            settings.bms_max_discharge_current_a
            * settings.battery_voltage_v
            / 1000.0
            * min(max(settings.export_efficiency_percent, 0.0), 100.0)
            / 100.0
            * min(max(settings.bms_power_safety_percent, 0.0), 100.0)
            / 100.0
        )
    power_limits: list[tuple[str, float]] = [
        ("requested_power", requested_power)
    ]
    if bms_power_limit is not None:
        power_limits.append(("bms", bms_power_limit))
    if settings.export_power_cap_kw is not None:
        power_limits.append(
            ("gcf_export_cap", max(settings.export_power_cap_kw, 0.0))
        )
    if settings.effective_export_power_kw is not None:
        power_limits.append(
            (
                "effective_export_power",
                max(settings.effective_export_power_kw, 0.0),
            )
        )
    physical_limit_source, maximum_power = min(
        power_limits,
        key=lambda item: item[1],
    )
    bms_limit_percent = (
        min(max(bms_power_limit / system_power * 100.0, 0.0), 100.0)
        if bms_power_limit is not None and system_power > 0
        else None
    )
    export_efficiency = max(
        min(settings.export_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    day3_available = settings.day3_pv_forecast_kwh is not None
    day3_forecast = (
        max(settings.day3_pv_forecast_kwh, 0.0)
        if settings.day3_pv_forecast_kwh is not None
        else None
    )
    day3_load_requirement = max(settings.average_daily_load_kwh, 0.0)
    day3_shortfall = (
        max(
            day3_load_requirement - (day3_forecast or 0.0),
            0.0,
        )
        if day3_available
        else 0.0
    )
    if not day3_available:
        terminal_reserve_reason = "day3_forecast_missing"
    elif day3_load_requirement <= 0.0:
        terminal_reserve_reason = "day3_load_not_required"
    elif day3_shortfall <= 1e-9:
        terminal_reserve_reason = "day3_pv_covers_load"
    else:
        terminal_reserve_reason = "day3_pv_deficit"
    house_discharge_efficiency = max(
        min(settings.house_discharge_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    # Day-3 shortfall is AC energy consumed by the home, while battery state
    # and the terminal target are DC energy.  Reserve enough DC energy to
    # deliver the complete forecast shortfall after inverter losses.
    terminal_energy_target = min(
        day3_shortfall / house_discharge_efficiency,
        max(capacity - floor_kwh, 0.0),
    )
    terminal_unit_value = (
        max(settings.avoided_import_price_pln_kwh, 0.0)
        * house_discharge_efficiency
    )
    price_by_start = {
        slot.start: slot.price_pln_kwh for slot in settings.price_slots
    }
    (
        baseline_objective,
        _,
        baseline_terminal_value,
    ) = _economic_objective(
        exports={},
        natural_exports=baseline_natural,
        price_by_start=price_by_start,
        ending_battery_kwh=baseline_expected_end,
        floor_kwh=floor_kwh,
        export_efficiency=export_efficiency,
        battery_wear_cost_pln_kwh=settings.battery_wear_cost_pln_kwh,
        terminal_energy_target_kwh=terminal_energy_target,
        terminal_energy_value_pln_kwh=terminal_unit_value,
    )
    result = OptimizerResult(
        ready=baseline_ok,
        status_code="ready",
        minimum_soc_percent=minimum_soc,
        base_reserve_energy_kwh=floor_kwh,
        protected_night_energy_kwh=protected_night_energy,
        additional_forecast_reserve_kwh=max(required_now - floor_kwh, 0.0),
        protected_home_energy_kwh=required_now,
        available_energy_now_kwh=available_now,
        ending_battery_kwh=max(baseline_end, 0.0),
        system_power_kw=system_power,
        requested_export_power_kw=requested_power,
        bms_discharge_power_limit_kw=bms_power_limit,
        bms_discharge_limit_percent=bms_limit_percent,
        bms_limit_active=(
            bms_power_limit is not None
            and bms_power_limit < requested_power - 0.001
        ),
        maximum_export_power_kw=maximum_power,
        historical_day_load_kwh=historical_day_load,
        live_projected_day_load_kwh=live_projected_day_load,
        modeled_day_load_kwh=modeled_day_load,
        daylight_progress_percent=daylight_progress * 100.0,
        load_profile_mode=load_profile_mode,
        forecast_confidence_percent=min(
            max(settings.forecast_confidence_percent, 0.0),
            100.0,
        ),
        export_power_cap_kw=settings.export_power_cap_kw,
        effective_export_power_kw=settings.effective_export_power_kw,
        physical_limit_source=physical_limit_source,
        control_reserve_energy_kwh=control_reserve,
        soc_quantization_reserve_kwh=quantization_reserve,
        day3_forecast_available=day3_available,
        day3_forecast_kwh=day3_forecast,
        day3_load_requirement_kwh=day3_load_requirement,
        day3_energy_shortfall_kwh=day3_shortfall,
        terminal_reserve_reason=terminal_reserve_reason,
        terminal_energy_target_kwh=terminal_energy_target,
        terminal_energy_value_pln_kwh=terminal_unit_value,
        terminal_energy_value_pln=baseline_terminal_value,
        baseline_terminal_energy_value_pln=baseline_terminal_value,
        net_objective_pln=baseline_objective,
        baseline_net_objective_pln=baseline_objective,
        uncontrolled_export_kwh=sum(baseline_natural.values()),
        uncontrolled_revenue_pln=_market_revenue(
            {},
            baseline_natural,
            {
                slot.start: slot.price_pln_kwh
                for slot in settings.price_slots
            },
        ),
    )
    if not baseline_ok:
        result.status_code = "home_energy_shortage"
        return result

    candidates = [
        slot
        for slot in settings.price_slots
        if slot.start >= now_slot
        and slot.start < horizon_end
        and not slot.blocked
    ]
    candidates.sort(key=lambda slot: (-slot.price_pln_kwh, slot.start))
    if not candidates or maximum_power <= 0:
        result.status_code = (
            "zero_export"
            if settings.export_power_cap_kw is not None
            and settings.export_power_cap_kw <= 0
            else "waiting_for_market"
        )
        result.natural_export_kwh = sum(baseline_natural.values())
        result.natural_revenue_pln = result.uncontrolled_revenue_pln
        return result

    exports: dict[datetime, float] = {}
    current_objective = baseline_objective
    maximum_slot_energy = maximum_power * 0.5
    for candidate in candidates:
        low = 0.0
        high = maximum_slot_energy
        for _ in range(12):
            middle = (low + high) / 2.0
            trial = dict(exports)
            trial[candidate.start] = middle
            feasible, _, _ = _simulate(
                starts,
                settings,
                load_by_slot,
                trial,
                floor_kwh,
                export_reserve_by_slot,
                conservative_pv,
            )
            if feasible:
                low = middle
            else:
                high = middle
        if low < 0.01:
            continue

        def evaluate(energy: float) -> tuple[bool, float, dict[datetime, float]]:
            trial = dict(exports)
            if energy >= 0.001:
                trial[candidate.start] = energy
            feasible, _, _ = _simulate(
                starts,
                settings,
                load_by_slot,
                trial,
                floor_kwh,
                export_reserve_by_slot,
                conservative_pv,
            )
            if not feasible:
                return False, -math.inf, trial
            _, expected_end, natural = _simulate(
                starts,
                settings,
                load_by_slot,
                trial,
                floor_kwh,
                export_reserve_by_slot,
                settings.pv_by_slot_kwh,
            )
            objective, _, _ = _economic_objective(
                exports=trial,
                natural_exports=natural,
                price_by_start=price_by_start,
                ending_battery_kwh=expected_end,
                floor_kwh=floor_kwh,
                export_efficiency=export_efficiency,
                battery_wear_cost_pln_kwh=settings.battery_wear_cost_pln_kwh,
                terminal_energy_target_kwh=terminal_energy_target,
                terminal_energy_value_pln_kwh=terminal_unit_value,
            )
            return True, objective, trial

        # Feasible maximum is not always the economic maximum: after retained
        # Day-3 energy drops below its target, another exported kWh may cost
        # more in future imports than it earns.  The objective is piecewise
        # linear/concave for one slot, so a bounded ternary search finds that
        # kink without introducing a heavyweight solver dependency.
        search_low = 0.0
        search_high = low
        for _ in range(18):
            first = search_low + (search_high - search_low) / 3.0
            second = search_high - (search_high - search_low) / 3.0
            _, first_objective, _ = evaluate(first)
            _, second_objective, _ = evaluate(second)
            if first_objective < second_objective:
                search_low = first
            else:
                search_high = second

        best_energy = 0.0
        best_objective = current_objective
        best_trial = dict(exports)
        for energy in (search_low, (search_low + search_high) / 2.0, search_high, low):
            feasible, objective, trial = evaluate(energy)
            if feasible and objective > best_objective + 0.0001:
                best_energy = energy
                best_objective = objective
                best_trial = trial
        if best_energy >= 0.01:
            exports = best_trial
            current_objective = best_objective

    feasible, ending_battery, _ = _simulate(
        starts,
        settings,
        load_by_slot,
        exports,
        floor_kwh,
        export_reserve_by_slot,
        conservative_pv,
    )
    _, expected_ending_battery, natural_exports = _simulate(
        starts,
        settings,
        load_by_slot,
        exports,
        floor_kwh,
        export_reserve_by_slot,
        settings.pv_by_slot_kwh,
    )
    if not feasible:
        result.ready = False
        result.status_code = "optimizer_error"
        return result

    result.planned_exports = [
        PlannedExport(
            start=start,
            price_pln_kwh=price_by_start[start],
            energy_kwh=energy,
        )
        for start, energy in sorted(exports.items())
    ]
    result.natural_export_kwh = sum(natural_exports.values())
    result.natural_revenue_pln = sum(
        energy * price_by_start.get(start, 0.0)
        for start, energy in natural_exports.items()
    )
    result.ending_battery_kwh = ending_battery
    (
        result.net_objective_pln,
        result.battery_wear_cost_pln,
        result.terminal_energy_value_pln,
    ) = _economic_objective(
        exports=exports,
        natural_exports=natural_exports,
        price_by_start=price_by_start,
        ending_battery_kwh=expected_ending_battery,
        floor_kwh=floor_kwh,
        export_efficiency=export_efficiency,
        battery_wear_cost_pln_kwh=settings.battery_wear_cost_pln_kwh,
        terminal_energy_target_kwh=terminal_energy_target,
        terminal_energy_value_pln_kwh=terminal_unit_value,
    )
    if not result.planned_exports:
        result.status_code = "home_protected"
    return result
