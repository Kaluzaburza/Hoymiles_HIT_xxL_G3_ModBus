"""Pure optimizer for time-of-use grid charging.

The optimizer is deliberately independent from Home Assistant.  It simulates
the battery in 30-minute steps and schedules grid charging only in a cheaper
tariff slot that occurs before the energy is needed by the home.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil

try:  # Package import in Home Assistant; direct import in deterministic tests.
    from .tariff_profiles import MANUAL_OPERATOR, get_tariff_profile, profile_rate
except ImportError:  # pragma: no cover - exercised by tools/test_tariff_optimizer.py
    from tariff_profiles import MANUAL_OPERATOR, get_tariff_profile, profile_rate


SLOT = timedelta(minutes=30)
_EPSILON = 1e-6
LIVE_FORECAST_MIN_EXPECTED_KWH = 2.0
LIVE_FORECAST_FULL_CONFIDENCE_KWH = 6.0
LIVE_FORECAST_MIN_FACTOR = 0.15


def adaptive_forecast_factor(
    historical_factor: float,
    actual_energy_kwh: float,
    expected_elapsed_kwh: float | None,
    *,
    eligible: bool,
) -> tuple[float, float | None, float]:
    """Blend complete-day history with live cumulative PV underproduction.

    Small morning samples are deliberately ignored.  Once Solcast expected at
    least 2 kWh, cumulative production gradually gains authority and reaches
    full confidence at 6 kWh.  The live signal can only lower today's
    forecast; tomorrow remains based on complete-day history.
    """
    historical = min(max(historical_factor, LIVE_FORECAST_MIN_FACTOR), 1.0)
    if (
        not eligible
        or expected_elapsed_kwh is None
        or expected_elapsed_kwh < LIVE_FORECAST_MIN_EXPECTED_KWH
    ):
        return historical, None, 0.0

    live_ratio = min(
        max(actual_energy_kwh / max(expected_elapsed_kwh, _EPSILON), 0.0),
        1.10,
    )
    confidence = min(
        max(
            expected_elapsed_kwh / LIVE_FORECAST_FULL_CONFIDENCE_KWH,
            0.0,
        ),
        1.0,
    )
    conservative_live = min(max(live_ratio, LIVE_FORECAST_MIN_FACTOR), historical)
    effective = historical + (conservative_live - historical) * confidence
    return min(max(effective, LIVE_FORECAST_MIN_FACTOR), historical), live_ratio, confidence


@dataclass(frozen=True, slots=True)
class TariffSchedule:
    """User-configurable time-of-use schedule."""

    tariff_type: str
    g11_price_pln_kwh: float
    low_price_pln_kwh: float
    medium_price_pln_kwh: float
    peak_price_pln_kwh: float
    cheap_windows: tuple[tuple[int, int], ...]
    medium_windows: tuple[tuple[int, int], ...] = ()
    weekend_low_price: bool = False
    polish_holidays_low_price: bool = False
    operator: str = MANUAL_OPERATOR


@dataclass(frozen=True, slots=True)
class TariffOptimizerInput:
    """All deterministic inputs used by the charging optimizer."""

    now: datetime
    pv_by_slot_kwh: dict[datetime, float]
    battery_capacity_kwh: float
    battery_soc_percent: float
    reserve_soc_percent: float
    maximum_soc_percent: float
    average_daily_load_kwh: float
    average_night_load_kwh: float | None
    night_start_minute: int
    night_end_minute: int
    # Total AC power that Grid Charge may draw for the home and battery
    # together.  The inverter subtracts the live home load from this budget.
    charge_power_kw: float
    charge_efficiency_percent: float
    discharge_efficiency_percent: float
    minimum_saving_pln_kwh: float
    schedule: TariffSchedule
    load_by_slot_kwh: dict[datetime, float] | None = None
    # Battery-side DC charging limit reported by the BMS.
    battery_charge_power_kw: float | None = None
    # Battery-side DC discharge limit reported by the BMS.
    battery_discharge_power_kw: float | None = None
    pv_charge_power_kw: float | None = None


@dataclass(frozen=True, slots=True)
class PlannedCharge:
    """One planned half-hour grid-support or battery-charge block."""

    start: datetime
    price_pln_kwh: float
    zone: str
    grid_import_kwh: float
    stored_energy_kwh: float
    direct_load_kwh: float
    action: str
    target_soc_percent: float


@dataclass(frozen=True, slots=True)
class TariffOptimizerResult:
    """Optimized charging plan and its diagnostics."""

    status_code: str
    planned_charges: tuple[PlannedCharge, ...]
    baseline_shortage_kwh: float
    remaining_shortage_kwh: float
    planned_grid_import_kwh: float
    planned_stored_energy_kwh: float
    planned_direct_load_kwh: float
    planned_cost_pln: float
    baseline_grid_cost_pln: float
    optimized_grid_cost_pln: float
    automation_savings_pln: float
    baseline_grid_import_kwh: float
    optimized_grid_import_kwh: float
    g11_reference_cost_pln: float
    estimated_savings_pln: float
    ending_battery_kwh: float
    ending_battery_soc_percent: float
    target_soc_percent: float
    current_slot_planned: bool
    current_action: str
    current_slot_end: datetime | None
    current_price_pln_kwh: float
    current_zone: str
    next_charge_start: datetime | None
    charge_power_kw: float


@dataclass(slots=True)
class _Simulation:
    shortage_kwh: float
    first_shortage_index: int | None
    ending_battery_kwh: float
    accepted_import_kwh: dict[int, float]
    accepted_support_kwh: dict[int, float]
    stored_import_kwh: dict[int, float]
    battery_after_kwh: dict[int, float]
    uncovered_import_kwh: dict[int, float]
    total_grid_import_kwh: float
    total_grid_cost_pln: float


def floor_half_hour(value: datetime) -> datetime:
    """Return the start of the half-hour containing ``value``."""
    return value.replace(
        minute=0 if value.minute < 30 else 30,
        second=0,
        microsecond=0,
    )


def _in_window(minute: int, start: int, end: int) -> bool:
    """Return whether a minute belongs to a possibly overnight window."""
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher method."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = (h + length - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def is_polish_public_holiday(value: date) -> bool:
    """Return whether ``value`` is a statutory Polish public holiday."""
    if (value.month, value.day) in {
        (1, 1),
        (1, 6),
        (5, 1),
        (5, 3),
        (8, 15),
        (11, 1),
        (11, 11),
        (12, 24),
        (12, 25),
        (12, 26),
    }:
        return True
    easter = _easter_sunday(value.year)
    return value in {
        easter,
        easter + timedelta(days=1),
        easter + timedelta(days=49),
        easter + timedelta(days=60),
    }


def tariff_rate(
    start: datetime,
    schedule: TariffSchedule,
) -> tuple[float, str]:
    """Return the marginal price and zone for one half-hour slot."""
    if schedule.operator != MANUAL_OPERATOR:
        profile = get_tariff_profile(schedule.operator, schedule.tariff_type)
        if profile is not None:
            return profile_rate(
                start,
                profile,
                is_public_holiday=is_polish_public_holiday(start.date()),
            )
    tariff_type = schedule.tariff_type.casefold().replace(" ", "")
    if tariff_type == "g11":
        return schedule.g11_price_pln_kwh, "g11"

    low_day = (
        schedule.weekend_low_price and start.weekday() >= 5
    ) or (
        schedule.polish_holidays_low_price
        and is_polish_public_holiday(start.date())
    )
    minute = start.hour * 60 + start.minute
    if low_day or any(
        _in_window(minute, window_start, window_end)
        for window_start, window_end in schedule.cheap_windows
    ):
        return schedule.low_price_pln_kwh, "low"

    if tariff_type == "g13" and any(
        _in_window(minute, window_start, window_end)
        for window_start, window_end in schedule.medium_windows
    ):
        return schedule.medium_price_pln_kwh, "medium"

    return schedule.peak_price_pln_kwh, "peak"


def _night_slot(minute: int, start: int, end: int) -> bool:
    return _in_window(minute, start, end)


def _slot_loads(settings: TariffOptimizerInput, starts: list[datetime]) -> list[float]:
    """Distribute daily and protected-night demand across future slots."""
    if settings.load_by_slot_kwh:
        return [
            max(settings.load_by_slot_kwh.get(start, 0.0), 0.0)
            for start in starts
        ]

    daily = max(settings.average_daily_load_kwh, 0.0)
    night = settings.average_night_load_kwh
    if night is None:
        night = daily * 0.45
    night = min(max(night, 0.0), daily)

    night_slots_per_day = sum(
        _night_slot(
            hour * 60 + minute,
            settings.night_start_minute,
            settings.night_end_minute,
        )
        for hour in range(24)
        for minute in (0, 30)
    )
    day_slots_per_day = max(48 - night_slots_per_day, 1)
    night_per_slot = night / max(night_slots_per_day, 1)
    day_per_slot = (daily - night) / day_slots_per_day
    return [
        night_per_slot
        if _night_slot(
            start.hour * 60 + start.minute,
            settings.night_start_minute,
            settings.night_end_minute,
        )
        else day_per_slot
        for start in starts
    ]


def _simulate(
    settings: TariffOptimizerInput,
    starts: list[datetime],
    loads: list[float],
    imports: dict[int, float],
    supports: dict[int, float],
    rates: list[tuple[float, str]],
    slot_fractions: list[float],
) -> _Simulation:
    capacity = max(settings.battery_capacity_kwh, 0.001)
    reserve = capacity * min(max(settings.reserve_soc_percent, 0.0), 100.0) / 100.0
    maximum = capacity * min(max(settings.maximum_soc_percent, 0.0), 100.0) / 100.0
    maximum = max(maximum, reserve)
    # The reserve and configured maximum are control thresholds, not physical
    # clamps on the energy already present.  Starting below reserve must not
    # create energy, while starting above the configured charge target must not
    # silently discard it.
    battery = min(
        max(capacity * settings.battery_soc_percent / 100.0, 0.0),
        capacity,
    )
    charge_efficiency = min(max(settings.charge_efficiency_percent / 100.0, 0.01), 1.0)
    discharge_efficiency = min(
        max(settings.discharge_efficiency_percent / 100.0, 0.01),
        1.0,
    )
    shortage = 0.0
    first_shortage: int | None = None
    accepted: dict[int, float] = {}
    accepted_support: dict[int, float] = {}
    stored: dict[int, float] = {}
    battery_after: dict[int, float] = {}
    uncovered_import: dict[int, float] = {}

    for index, start in enumerate(starts):
        fraction = slot_fractions[index]
        slot_hours = 0.5 * fraction
        pv = max(settings.pv_by_slot_kwh.get(start, 0.0), 0.0) * fraction
        net = pv - loads[index]
        requested_support = max(supports.get(index, 0.0), 0.0)
        requested_import = max(imports.get(index, 0.0), 0.0)
        grid_charge_active = (
            requested_support > _EPSILON or requested_import > _EPSILON
        )
        # Grid Charge has one shared AC input budget.  Once enabled, the
        # inverter supplies the complete remaining home load first and only
        # the unused part of the configured power can charge the battery.
        grid_budget = max(settings.charge_power_kw, 0.0) * slot_hours
        battery_charge_budget = (
            max(settings.battery_charge_power_kw, 0.0) * slot_hours
            if settings.battery_charge_power_kw is not None
            else float("inf")
        )
        direct_grid = 0.0
        if grid_charge_active and net < 0 and grid_budget > _EPSILON:
            direct_grid = min(-net, grid_budget)
            net += direct_grid
            accepted_support[index] = direct_grid
        remaining_grid_budget = max(grid_budget - direct_grid, 0.0)
        if net >= 0:
            # PV surplus is subject to the same physical BMS/inverter charging
            # limit and conversion loss as energy imported from the grid.
            pv_battery_limit = (
                settings.pv_charge_power_kw
                if settings.pv_charge_power_kw is not None
                else settings.charge_power_kw * charge_efficiency
            )
            stored_from_pv = min(
                net * charge_efficiency,
                max(pv_battery_limit, 0.0) * slot_hours,
                battery_charge_budget,
                max(maximum - battery, 0.0),
            )
            battery += max(stored_from_pv, 0.0)
            battery_charge_budget = max(
                battery_charge_budget - max(stored_from_pv, 0.0),
                0.0,
            )
        else:
            required_from_battery = -net / discharge_efficiency
            available = max(battery - reserve, 0.0)
            discharge_budget = (
                max(settings.battery_discharge_power_kw, 0.0) * slot_hours
                if settings.battery_discharge_power_kw is not None
                else float("inf")
            )
            discharged = min(required_from_battery, available, discharge_budget)
            battery -= discharged
            uncovered = max(-net - discharged * discharge_efficiency, 0.0)
            if uncovered > _EPSILON:
                shortage += uncovered
                uncovered_import[index] = uncovered
                if first_shortage is None:
                    first_shortage = index

        if requested_import > 0:
            battery_ac_limit = battery_charge_budget / charge_efficiency
            accepted_import = min(
                requested_import,
                remaining_grid_budget,
                battery_ac_limit,
                max(maximum - battery, 0.0) / charge_efficiency,
            )
            stored_energy = accepted_import * charge_efficiency
            battery += stored_energy
            accepted[index] = accepted_import
            stored[index] = stored_energy
        battery_after[index] = battery

    explicit_grid_import = sum(accepted.values()) + sum(accepted_support.values())
    total_grid_import = explicit_grid_import + sum(uncovered_import.values())
    total_grid_cost = sum(
        (
            accepted.get(index, 0.0)
            + accepted_support.get(index, 0.0)
            + uncovered_import.get(index, 0.0)
        )
        * rates[index][0]
        for index in range(len(starts))
    )
    return _Simulation(
        shortage_kwh=shortage,
        first_shortage_index=first_shortage,
        ending_battery_kwh=battery,
        accepted_import_kwh=accepted,
        accepted_support_kwh=accepted_support,
        stored_import_kwh=stored,
        battery_after_kwh=battery_after,
        uncovered_import_kwh=uncovered_import,
        total_grid_import_kwh=total_grid_import,
        total_grid_cost_pln=total_grid_cost,
    )


def optimize_tariff_charging(
    settings: TariffOptimizerInput,
) -> TariffOptimizerResult:
    """Build the least-cost feasible charging plan for today and tomorrow."""
    now_slot = floor_half_hour(settings.now)
    horizon_end = datetime.combine(
        settings.now.date() + timedelta(days=2),
        datetime.min.time(),
        tzinfo=settings.now.tzinfo,
    )
    starts: list[datetime] = []
    cursor = now_slot
    while cursor < horizon_end:
        starts.append(cursor)
        cursor += SLOT

    first_fraction = max(
        min((30 - settings.now.minute % 30) / 30.0, 1.0),
        1 / 30,
    )
    slot_fractions = [1.0 for _ in starts]
    if slot_fractions:
        slot_fractions[0] = first_fraction
    loads = _slot_loads(settings, starts)
    if loads:
        loads[0] *= first_fraction
    rates = [tariff_rate(start, settings.schedule) for start in starts]
    baseline = _simulate(
        settings,
        starts,
        loads,
        {},
        {},
        rates,
        slot_fractions,
    )
    planned: dict[int, float] = {}
    planned_support: dict[int, float] = {}
    simulation = baseline
    charge_power = max(settings.charge_power_kw, 0.0)
    charge_efficiency = min(
        max(settings.charge_efficiency_percent / 100.0, 0.01), 1.0
    )
    block_limits = [charge_power * 0.5 for _ in starts]
    if block_limits:
        block_limits[0] *= first_fraction
    battery_slot_limits = [
        min(limit, max(settings.battery_charge_power_kw, 0.0) * 0.5 * fraction)
        if settings.battery_charge_power_kw is not None
        else limit
        for limit, fraction in zip(block_limits, slot_fractions)
    ]
    support_limits = [
        max(
            loads[index]
            - max(settings.pv_by_slot_kwh.get(start, 0.0), 0.0)
            * slot_fractions[index],
            0.0,
        )
        for index, start in enumerate(starts)
    ]

    # The configured Self-Use reserve plus the user's safety correction is a
    # real planning floor.  If the battery is still below that dynamic floor
    # when a low-price slot arrives, restore only the missing energy.  PV
    # forecast before the cheap slot is honoured, so this does not force an
    # unnecessary early grid charge on a normal sunny day.
    reserve_energy = (
        max(settings.battery_capacity_kwh, 0.001)
        * min(max(settings.reserve_soc_percent, 0.0), 100.0)
        / 100.0
    )
    if (
        settings.schedule.tariff_type.casefold().replace(" ", "") != "g11"
        and settings.battery_capacity_kwh
        * min(max(settings.battery_soc_percent, 0.0), 100.0)
        / 100.0
        < reserve_energy - _EPSILON
    ):
        charge_efficiency = min(
            max(settings.charge_efficiency_percent / 100.0, 0.01),
            1.0,
        )
        for index in range(len(starts)):
            if rates[index][1] != "low":
                continue
            projected = simulation.battery_after_kwh.get(index, 0.0)
            if projected >= reserve_energy - _EPSILON:
                break
            if support_limits[index] > _EPSILON:
                planned_support[index] = support_limits[index]
            requested_ac = (reserve_energy - projected) / charge_efficiency
            available = block_limits[index] - planned.get(index, 0.0)
            if requested_ac > _EPSILON and available > _EPSILON:
                planned[index] = planned.get(index, 0.0) + min(
                    requested_ac,
                    available,
                )
            simulation = _simulate(
                settings,
                starts,
                loads,
                planned,
                planned_support,
                rates,
                slot_fractions,
            )
            if simulation.battery_after_kwh.get(index, 0.0) >= (
                reserve_energy - _EPSILON
            ):
                break

    # Half a kilowatt-hour provides sufficient precision for the dashboard and
    # keeps the repeated storage simulation inexpensive even for 230 kWh banks.
    quantum = max(min(charge_power * 0.5, 0.5), 0.05)
    max_iterations = max(ceil(baseline.shortage_kwh / 0.05) + len(starts) * 4, 100)
    for _ in range(max_iterations):
        if simulation.shortage_kwh <= 0.01:
            break
        first_shortage = simulation.first_shortage_index
        if first_shortage is None:
            break
        best: tuple[
            tuple[float, float, int, int],
            str,
            int,
            dict[int, float],
            dict[int, float],
            _Simulation,
        ] | None = None
        # An unavoidable shortage before the next low-price window must not
        # cancel the rest of the plan.  A later action cannot repair energy
        # already bought from the grid, but it can still reduce subsequent
        # expensive imports; the complete simulation accepts it only when the
        # total future shortage is genuinely reduced.
        for index in range(len(starts)):
            rate = rates[index][0]

            # Grid Charge is a mode for the complete slot: enabling it makes
            # the inverter supply the whole remaining home load from the grid.
            # A fractional support action would not match the hardware.
            support_remaining = support_limits[index]
            if (
                support_remaining > _EPSILON
                and index not in planned_support
            ):
                trial_support = dict(planned_support)
                trial_support[index] = support_remaining
                trial_simulation = _simulate(
                    settings,
                    starts,
                    loads,
                    planned,
                    trial_support,
                    rates,
                    slot_fractions,
                )
                reduction = simulation.shortage_kwh - trial_simulation.shortage_kwh
                accepted_delta = (
                    trial_simulation.accepted_support_kwh.get(index, 0.0)
                    - simulation.accepted_support_kwh.get(index, 0.0)
                    + trial_simulation.accepted_import_kwh.get(index, 0.0)
                    - simulation.accepted_import_kwh.get(index, 0.0)
                )
                cost_reduction = (
                    simulation.total_grid_cost_pln
                    - trial_simulation.total_grid_cost_pln
                )
                if (
                    reduction > 1e-5
                    and accepted_delta > _EPSILON
                    and cost_reduction
                    > settings.minimum_saving_pln_kwh * accepted_delta
                ):
                    score = (
                        -cost_reduction / accepted_delta,
                        rate,
                        index,
                        0,
                    )
                    candidate = (
                        score,
                        "grid_support",
                        index,
                        planned,
                        trial_support,
                        trial_simulation,
                    )
                    if best is None or candidate[0] < best[0]:
                        best = candidate

            charge_remaining = block_limits[index] - planned.get(index, 0.0)
            if charge_remaining > _EPSILON:
                increment = min(quantum, charge_remaining)
                trial_charge = dict(planned)
                trial_charge[index] = trial_charge.get(index, 0.0) + increment
                trial_simulation = _simulate(
                    settings,
                    starts,
                    loads,
                    trial_charge,
                    planned_support,
                    rates,
                    slot_fractions,
                )
                reduction = simulation.shortage_kwh - trial_simulation.shortage_kwh
                accepted_delta = (
                    trial_simulation.accepted_import_kwh.get(index, 0.0)
                    - simulation.accepted_import_kwh.get(index, 0.0)
                    + trial_simulation.accepted_support_kwh.get(index, 0.0)
                    - simulation.accepted_support_kwh.get(index, 0.0)
                )
                cost_reduction = (
                    simulation.total_grid_cost_pln
                    - trial_simulation.total_grid_cost_pln
                )
                if (
                    reduction > 1e-5
                    and accepted_delta > _EPSILON
                    and cost_reduction
                    > settings.minimum_saving_pln_kwh * accepted_delta
                ):
                    score = (
                        -cost_reduction / accepted_delta,
                        rate,
                        -index,
                        1,
                    )
                    candidate = (
                        score,
                        "battery_charge",
                        index,
                        trial_charge,
                        planned_support,
                        trial_simulation,
                    )
                    if best is None or candidate[0] < best[0]:
                        best = candidate

        if best is None:
            break
        _, _, _, planned, planned_support, simulation = best

    charges: list[PlannedCharge] = []
    capacity = max(settings.battery_capacity_kwh, 0.001)
    action_indices = sorted(
        set(simulation.accepted_import_kwh)
        | set(simulation.accepted_support_kwh)
    )
    for index in action_indices:
        battery_import = simulation.accepted_import_kwh.get(index, 0.0)
        direct_load = simulation.accepted_support_kwh.get(index, 0.0)
        grid_import = battery_import + direct_load
        if grid_import <= 0.001:
            continue
        price, zone = rates[index]
        if battery_import > 0.001 and direct_load > 0.001:
            action = "grid_support_and_charge"
        elif battery_import > 0.001:
            action = "battery_charge"
        else:
            action = "grid_support"
        charges.append(
            PlannedCharge(
                start=starts[index],
                price_pln_kwh=price,
                zone=zone,
                grid_import_kwh=grid_import,
                stored_energy_kwh=simulation.stored_import_kwh.get(index, 0.0),
                direct_load_kwh=direct_load,
                action=action,
                target_soc_percent=min(
                    max(
                        simulation.battery_after_kwh.get(index, 0.0)
                        / capacity
                        * 100.0,
                        0.0,
                    ),
                    100.0,
                ),
            )
        )

    planned_grid = sum(item.grid_import_kwh for item in charges)
    planned_stored = sum(item.stored_energy_kwh for item in charges)
    planned_direct = sum(item.direct_load_kwh for item in charges)
    cost = sum(item.grid_import_kwh * item.price_pln_kwh for item in charges)
    baseline_grid_cost = baseline.total_grid_cost_pln
    optimized_grid_cost = simulation.total_grid_cost_pln
    automation_savings = max(baseline_grid_cost - optimized_grid_cost, 0.0)
    reference_cost = baseline.total_grid_import_kwh * settings.schedule.g11_price_pln_kwh
    savings = max(reference_cost - optimized_grid_cost, 0.0)
    current_planned = bool(charges and charges[0].start == now_slot)
    current_action = charges[0].action if current_planned else "none"
    current_slot_end: datetime | None = None
    if current_planned:
        current_slot_end = charges[0].start + timedelta(minutes=30)
        for item in charges[1:]:
            if item.start != current_slot_end or item.action != current_action:
                break
            current_slot_end += timedelta(minutes=30)
    next_start = charges[0].start if charges else None

    current_index = 0
    if current_planned:
        if current_action == "grid_support":
            target_energy = simulation.battery_after_kwh.get(
                0,
                settings.battery_capacity_kwh
                * settings.battery_soc_percent
                / 100.0,
            )
        else:
            contiguous = [0]
            for index in range(1, len(starts)):
                if index not in simulation.accepted_import_kwh:
                    break
                contiguous.append(index)
            target_energy = max(
                simulation.battery_after_kwh.get(index, 0.0)
                for index in contiguous
            )
            # When the remaining part of the current low-price window is too
            # short, the feasible imports above describe only what can still
            # be stored.  Using that small value as the live Force Charge SOC
            # target made HA reach it within seconds, switch back to Self-Use,
            # recalculate another tiny target and loop until the tariff window
            # closed.  Keep the target at the energy actually required for the
            # future non-low slots.  It remains almost constant while SOC rises
            # and is capped by the user's configured maximum SOC.
            future_expensive_shortage = sum(
                energy
                for index, energy in simulation.uncovered_import_kwh.items()
                if index >= current_index and rates[index][1] not in {"low", "g11"}
            )
            discharge_efficiency = min(
                max(settings.discharge_efficiency_percent / 100.0, 0.01),
                1.0,
            )
            maximum_energy = max(
                capacity
                * min(max(settings.maximum_soc_percent, 0.0), 100.0)
                / 100.0,
                capacity
                * min(max(settings.reserve_soc_percent, 0.0), 100.0)
                / 100.0,
            )
            target_energy = min(
                max(
                    target_energy,
                    simulation.battery_after_kwh.get(0, target_energy)
                    + future_expensive_shortage / discharge_efficiency,
                ),
                maximum_energy,
            )
    elif charges:
        target_energy = charges[0].target_soc_percent / 100.0 * capacity
    else:
        target_energy = settings.battery_capacity_kwh * settings.battery_soc_percent / 100.0

    if charges:
        # A partial plan is still worth executing, but it must not be presented
        # as fully feasible.  This happens, for example, when only the last few
        # minutes of a low-price window remain and the configured Grid Charge
        # power cannot store all energy needed for the following peak period.
        status = (
            "insufficient_cheap_window"
            if simulation.shortage_kwh > 0.01
            else "ready"
        )
    elif baseline.shortage_kwh <= 0.01:
        status = "no_charge_needed"
    elif settings.schedule.tariff_type.casefold().replace(" ", "") == "g11":
        status = "no_discount_window"
    elif not charges:
        uncovered_slots = tuple(baseline.uncovered_import_kwh)
        if uncovered_slots and all(
            rates[index][1] == "low" for index in uncovered_slots
        ):
            # No battery round trip is useful here: Self-Use will import the
            # unavoidable deficit directly while the low tariff is active.
            status = "shortage_in_low_period"
        else:
            status = "no_cheap_window"
    else:
        status = "ready"

    current_price, current_zone = rates[current_index]
    return TariffOptimizerResult(
        status_code=status,
        planned_charges=tuple(charges),
        baseline_shortage_kwh=baseline.shortage_kwh,
        remaining_shortage_kwh=simulation.shortage_kwh,
        planned_grid_import_kwh=planned_grid,
        planned_stored_energy_kwh=planned_stored,
        planned_direct_load_kwh=planned_direct,
        planned_cost_pln=cost,
        baseline_grid_cost_pln=baseline_grid_cost,
        optimized_grid_cost_pln=optimized_grid_cost,
        automation_savings_pln=automation_savings,
        baseline_grid_import_kwh=baseline.total_grid_import_kwh,
        optimized_grid_import_kwh=simulation.total_grid_import_kwh,
        g11_reference_cost_pln=reference_cost,
        estimated_savings_pln=savings,
        ending_battery_kwh=simulation.ending_battery_kwh,
        ending_battery_soc_percent=simulation.ending_battery_kwh / capacity * 100.0,
        target_soc_percent=min(max(target_energy / capacity * 100.0, 0.0), 100.0),
        current_slot_planned=current_planned,
        current_action=current_action,
        current_slot_end=current_slot_end,
        current_price_pln_kwh=current_price,
        current_zone=current_zone,
        next_charge_start=next_start,
        charge_power_kw=charge_power,
    )
