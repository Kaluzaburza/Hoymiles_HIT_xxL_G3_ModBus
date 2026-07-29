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
    minimum_price_pln_kwh: float
    inverter_power_kw: float
    inverter_count: int
    discharge_power_percent: float
    export_efficiency_percent: float


@dataclass(slots=True)
class OptimizerResult:
    """Calculated RCE plan and diagnostics."""

    ready: bool
    status_code: str
    minimum_soc_percent: int
    protected_home_energy_kwh: float
    available_energy_now_kwh: float
    planned_exports: list[PlannedExport] = field(default_factory=list)
    natural_export_kwh: float = 0.0
    natural_revenue_pln: float = 0.0
    ending_battery_kwh: float = 0.0
    system_power_kw: float = 0.0
    maximum_export_power_kw: float = 0.0

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


def _load_by_slot(
    settings: OptimizerInput,
    starts: list[datetime],
) -> dict[datetime, float]:
    night_minutes = (
        settings.night_end_minute - settings.night_start_minute
    ) % (24 * 60)
    night_hours = max(night_minutes / 60.0, 0.5)
    day_hours = max(24.0 - night_hours, 0.5)
    daily = max(settings.average_daily_load_kwh, 0.0)
    if settings.average_night_load_kwh is None:
        night_energy = daily * night_hours / 24.0
    else:
        night_energy = min(max(settings.average_night_load_kwh, 0.0), daily)
    day_energy = max(daily - night_energy, 0.0)
    night_slot = night_energy / night_hours * 0.5
    day_slot = day_energy / day_hours * 0.5
    return {
        start: (
            night_slot
            if _is_night(
                start,
                settings.night_start_minute,
                settings.night_end_minute,
            )
            else day_slot
        )
        for start in starts
    }


def _simulate(
    starts: list[datetime],
    settings: OptimizerInput,
    load_by_slot: Mapping[datetime, float],
    exports: Mapping[datetime, float],
    floor_kwh: float,
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
        if battery > capacity:
            natural_exports[start] = battery - capacity
            battery = capacity
    return True, battery, natural_exports


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

    load_by_slot = _load_by_slot(settings, starts)
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
    required_now = (
        _required_energy_now(starts, settings, load_by_slot, floor_kwh)
        if settings.dynamic_reserve_enabled
        else floor_kwh
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
    maximum_power = system_power * min(
        max(settings.discharge_power_percent, 0.0),
        100.0,
    ) / 100.0
    result = OptimizerResult(
        ready=baseline_ok,
        status_code="ready",
        minimum_soc_percent=minimum_soc,
        protected_home_energy_kwh=max(required_now - floor_kwh, 0.0),
        available_energy_now_kwh=available_now,
        ending_battery_kwh=max(baseline_end, 0.0),
        system_power_kw=system_power,
        maximum_export_power_kw=maximum_power,
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
        and slot.price_pln_kwh > settings.minimum_price_pln_kwh
    ]
    candidates.sort(key=lambda slot: (-slot.price_pln_kwh, slot.start))
    if not candidates or maximum_power <= 0:
        result.status_code = "waiting_for_price"
        result.natural_export_kwh = sum(baseline_natural.values())
        result.natural_revenue_pln = sum(
            energy * price_by_start.get(start, 0.0)
            for start, energy in baseline_natural.items()
        )
        return result

    exports: dict[datetime, float] = {}
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
            )
            if feasible:
                low = middle
            else:
                high = middle
        if low >= 0.01:
            # Keep the feasible value found by the binary search. Rounding it
            # to two decimals here can round upward and make the sum of many
            # slots exceed the protected battery reserve. Values are rounded
            # only when exposed as Home Assistant attributes.
            exports[candidate.start] = low

    feasible, ending_battery, natural_exports = _simulate(
        starts,
        settings,
        load_by_slot,
        exports,
        floor_kwh,
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
