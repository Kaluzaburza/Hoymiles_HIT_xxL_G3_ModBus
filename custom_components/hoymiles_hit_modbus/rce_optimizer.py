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
    pv_to_load_today_kwh: float = 0.0
    pv_to_load_power_kw: float = 0.0


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
        """Return revenue gained over leaving all export uncontrolled."""
        return self.total_revenue_pln - self.uncontrolled_revenue_pln


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

    actual_self_consumption = max(settings.pv_to_load_today_kwh, 0.0)
    live_projection = 0.0
    if progress >= 0.05 and actual_self_consumption > 0:
        # Cap a transiently high early projection.  The cap is deliberately
        # generous: it protects the house while preventing one 0.1 kWh counter
        # step just after sunrise from reserving the entire battery.
        projection_cap = max(
            daily * 2.0,
            historical_day_energy,
            actual_self_consumption,
        )
        live_projection = min(
            actual_self_consumption / progress,
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
) -> dict[datetime, float]:
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
                - max(settings.pv_to_load_today_kwh, 0.0),
                0.0,
            )
            day_energy = max(historical_remaining, live_remaining)
        per_slot = day_energy / max(len(day_starts), 1)
        for start in day_starts:
            loads[start] = per_slot
    return loads


def _simulate(
    starts: list[datetime],
    settings: OptimizerInput,
    load_by_slot: Mapping[datetime, float],
    exports: Mapping[datetime, float],
    floor_kwh: float,
    export_reserve_by_slot: Mapping[datetime, float] | None = None,
) -> tuple[bool, float, dict[datetime, float]]:
    capacity = settings.battery_capacity_kwh
    battery = capacity * settings.battery_soc_percent / 100.0
    efficiency = max(
        min(settings.export_efficiency_percent / 100.0, 1.0),
        0.01,
    )
    natural_exports: dict[datetime, float] = {}
    for start in starts:
        battery += max(float(settings.pv_by_slot_kwh.get(start, 0.0)), 0.0)
        battery -= max(float(load_by_slot.get(start, 0.0)), 0.0)
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
            natural_exports[start] = battery - capacity
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
) -> float:
    required = floor_kwh
    capacity = settings.battery_capacity_kwh
    for start in reversed(starts):
        required += max(float(load_by_slot.get(start, 0.0)), 0.0)
        required -= max(float(settings.pv_by_slot_kwh.get(start, 0.0)), 0.0)
        required = min(max(required, floor_kwh), capacity)
    return required


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
            energy += max(float(load_by_slot.get(later, 0.0)), 0.0)
        return energy

    current_night_energy = remaining_night_energy(0)
    reserve_by_slot = {
        start: min(
            floor_kwh + remaining_night_energy(index + 1),
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
    load_by_slot = _load_by_slot(
        settings,
        starts,
        historical_day_energy=historical_day_load,
        live_projected_day_energy=live_projected_day_load,
        modeled_day_energy=modeled_day_load,
        daylight_progress=daylight_progress,
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
        _required_energy_now(starts, settings, load_by_slot, floor_kwh)
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
    current_energy = capacity * settings.battery_soc_percent / 100.0
    available_now = max(current_energy - required_now, 0.0)

    baseline_ok, baseline_end, baseline_natural = _simulate(
        starts,
        settings,
        load_by_slot,
        {},
        floor_kwh,
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
    maximum_power = (
        min(requested_power, bms_power_limit)
        if bms_power_limit is not None
        else requested_power
    )
    bms_limit_percent = (
        min(max(bms_power_limit / system_power * 100.0, 0.0), 100.0)
        if bms_power_limit is not None and system_power > 0
        else None
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

    price_by_start = {slot.start: slot.price_pln_kwh for slot in settings.price_slots}
    candidates = [
        slot
        for slot in settings.price_slots
        if slot.start >= now_slot
        and slot.start < horizon_end
        and not slot.blocked
    ]
    candidates.sort(key=lambda slot: (-slot.price_pln_kwh, slot.start))
    if not candidates or maximum_power <= 0:
        result.status_code = "waiting_for_market"
        result.natural_export_kwh = sum(baseline_natural.values())
        result.natural_revenue_pln = result.uncontrolled_revenue_pln
        return result

    exports: dict[datetime, float] = {}
    current_revenue = result.uncontrolled_revenue_pln
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
            )
            if feasible:
                low = middle
            else:
                high = middle
        if low < 0.01:
            continue

        # Compare the complete market result, including PV that would otherwise
        # overflow naturally.  This prevents a low-price forced export from
        # displacing a more valuable natural export, while still allowing an
        # earlier discharge when it creates battery headroom before an even
        # cheaper (or negative-price) PV surplus.
        trial = dict(exports)
        trial[candidate.start] = low
        feasible, _, trial_natural = _simulate(
            starts,
            settings,
            load_by_slot,
            trial,
            floor_kwh,
            export_reserve_by_slot,
        )
        trial_revenue = _market_revenue(
            trial,
            trial_natural,
            price_by_start,
        )
        if feasible and trial_revenue > current_revenue + 0.0001:
            # Keep the feasible value found by the binary search. Rounding it
            # here can round upward and violate the protected reserve.
            exports = trial
            current_revenue = trial_revenue

    feasible, ending_battery, natural_exports = _simulate(
        starts,
        settings,
        load_by_slot,
        exports,
        floor_kwh,
        export_reserve_by_slot,
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
    if not result.planned_exports:
        result.status_code = "home_protected"
    return result
