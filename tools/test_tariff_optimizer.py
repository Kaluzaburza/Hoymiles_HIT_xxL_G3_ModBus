"""Deterministic tests for the tariff grid-charging optimizer."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from random import Random
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from tariff_optimizer import (  # noqa: E402
    TariffOptimizerInput,
    TariffSchedule,
    _simulate,
    adaptive_forecast_factor,
    is_polish_public_holiday,
    optimize_tariff_charging,
    tariff_rate,
)


ZONE = ZoneInfo("Europe/Warsaw")


def schedule(kind: str = "G12") -> TariffSchedule:
    return TariffSchedule(
        tariff_type=kind,
        g11_price_pln_kwh=0.85,
        low_price_pln_kwh=0.62,
        medium_price_pln_kwh=0.82,
        peak_price_pln_kwh=1.03,
        cheap_windows=((22 * 60, 6 * 60), (13 * 60, 15 * 60)),
        medium_windows=((7 * 60, 13 * 60),),
        weekend_low_price=kind in {"G12w", "G13"},
        polish_holidays_low_price=True,
    )


def settings(now: datetime, **overrides) -> TariffOptimizerInput:
    values = {
        "now": now,
        "pv_by_slot_kwh": {},
        "battery_capacity_kwh": 20.0,
        "battery_soc_percent": 30.0,
        "reserve_soc_percent": 20.0,
        "maximum_soc_percent": 100.0,
        "average_daily_load_kwh": 18.0,
        "average_night_load_kwh": 8.0,
        "night_start_minute": 20 * 60,
        "night_end_minute": 7 * 60,
        "charge_power_kw": 5.0,
        "charge_efficiency_percent": 95.0,
        "discharge_efficiency_percent": 95.0,
        "minimum_saving_pln_kwh": 0.05,
        "schedule": schedule(),
    }
    values.update(overrides)
    return TariffOptimizerInput(**values)


def main() -> None:
    monday = datetime(2026, 8, 3, 21, 10, tzinfo=ZONE)

    # The inverter has one shared Grid Charge budget.  With a 10 kW command in
    # a 30-minute slot, 4 kWh of LOAD leaves only 1 kWh AC for the battery.
    # Merely enabling the mode also moves the complete remaining LOAD to grid;
    # the requested support value is a mode flag, not a fractional flow.
    physical = settings(
        monday.replace(hour=22, minute=0),
        battery_soc_percent=50.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
        charge_power_kw=10.0,
        battery_charge_power_kw=20.0,
    )
    physical_simulation = _simulate(
        physical,
        [physical.now],
        [4.0],
        {0: 5.0},
        {0: 0.1},
        [(0.62, "low")],
        [1.0],
    )
    assert abs(physical_simulation.accepted_support_kwh[0] - 4.0) < 1e-6
    assert abs(physical_simulation.accepted_import_kwh[0] - 1.0) < 1e-6
    assert abs(
        physical_simulation.accepted_support_kwh[0]
        + physical_simulation.accepted_import_kwh[0]
        - 5.0
    ) < 1e-6

    # The BMS limit applies only to battery-side DC power.  It must not reduce
    # the part of the common AC budget that supplies the home.
    bms_limited = settings(
        physical.now,
        battery_soc_percent=50.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
        charge_power_kw=10.0,
        battery_charge_power_kw=2.0,
    )
    bms_simulation = _simulate(
        bms_limited,
        [physical.now],
        [2.0],
        {0: 5.0},
        {},
        [(0.62, "low")],
        [1.0],
    )
    assert abs(bms_simulation.accepted_support_kwh[0] - 2.0) < 1e-6
    assert abs(bms_simulation.stored_import_kwh[0] - 1.0) < 1e-6
    assert bms_simulation.total_grid_import_kwh < 5.0

    # PV and grid charging share the same BMS current limit.  One source must
    # not receive the full limit after the other has already consumed it.
    mixed_source = settings(
        physical.now,
        pv_by_slot_kwh={physical.now: 1.0},
        battery_soc_percent=50.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
        charge_power_kw=10.0,
        battery_charge_power_kw=2.0,
        pv_charge_power_kw=2.0,
    )
    mixed_simulation = _simulate(
        mixed_source,
        [physical.now],
        [0.0],
        {0: 5.0},
        {},
        [(0.62, "low")],
        [1.0],
    )
    assert abs(
        mixed_simulation.stored_import_kwh[0] + 0.95 - 1.0
    ) < 1e-6

    # A BMS discharge-current limit can force grid import even when sufficient
    # energy remains above reserve.
    discharge_limited = settings(
        physical.now,
        battery_soc_percent=100.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
        battery_discharge_power_kw=2.0,
    )
    discharge_simulation = _simulate(
        discharge_limited,
        [physical.now],
        [3.0],
        {},
        {},
        [(1.03, "peak")],
        [1.0],
    )
    assert abs(discharge_simulation.uncovered_import_kwh[0] - 2.05) < 1e-6

    # Control thresholds cannot manufacture or discard the initial energy.
    below_reserve = settings(
        physical.now,
        battery_soc_percent=10.0,
        reserve_soc_percent=20.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
    )
    below_simulation = _simulate(
        below_reserve, [physical.now], [0.0], {}, {}, [(0.62, "low")], [1.0]
    )
    assert abs(below_simulation.ending_battery_kwh - 2.0) < 1e-6
    above_target = settings(
        physical.now,
        battery_soc_percent=90.0,
        maximum_soc_percent=80.0,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
    )
    above_simulation = _simulate(
        above_target, [physical.now], [0.0], {}, {}, [(0.62, "low")], [1.0]
    )
    assert abs(above_simulation.ending_battery_kwh - 18.0) < 1e-6

    result = optimize_tariff_charging(settings(monday))
    assert result.planned_grid_import_kwh > 0
    assert result.estimated_savings_pln > 0
    assert all(item.price_pln_kwh == 0.62 for item in result.planned_charges)
    assert all(
        item.grid_import_kwh <= result.charge_power_kw * 0.5 + 1e-6
        for item in result.planned_charges
    )

    # Charging lead time is derived from energy, effective AC power and the
    # 30-minute slot duration.  A 10 kW Grid Charge budget needs two slots to
    # move 10 kWh, so the latest feasible start before the 15:00 peak is 14:00.
    pre_peak = datetime(2026, 8, 6, 12, 30, tzinfo=ZONE)
    peak_load = {
        pre_peak.replace(hour=hour, minute=minute): 10.0 / 14.0
        for hour in range(15, 22)
        for minute in (0, 30)
    }
    ten_kw_lead = optimize_tariff_charging(
        settings(
            pre_peak,
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=peak_load,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert ten_kw_lead.status_code == "ready"
    assert [item.start.strftime("%H:%M") for item in ten_kw_lead.planned_charges] == [
        "14:00",
        "14:30",
    ]
    assert abs(ten_kw_lead.planned_stored_energy_kwh - 10.0) < 1e-6
    assert ten_kw_lead.remaining_shortage_kwh < 0.01

    # The percentage sent to the inverter is a shared Grid Charge budget, not
    # pure battery power.  With 2 kWh of LOAD in every cheap half-hour, only
    # 3 kWh from each 10 kW block remain for storage, so charging must begin at
    # 13:00 instead of incorrectly assuming that 14:00 is still sufficient.
    loaded_cheap_period = dict(peak_load)
    loaded_cheap_period.update(
        {
            pre_peak.replace(hour=hour, minute=minute): 2.0
            for hour in (13, 14)
            for minute in (0, 30)
        }
    )
    net_power_lead = optimize_tariff_charging(
        settings(
            pre_peak,
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=loaded_cheap_period,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert net_power_lead.status_code == "ready"
    assert net_power_lead.planned_charges[0].start.strftime("%H:%M") == "13:00"
    assert abs(net_power_lead.planned_stored_energy_kwh - 10.0) < 1e-6
    assert abs(net_power_lead.planned_direct_load_kwh - 8.0) < 1e-6

    combined_active = optimize_tariff_charging(
        settings(
            pre_peak.replace(hour=13, minute=0),
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=loaded_cheap_period,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert combined_active.current_slot_planned
    assert combined_active.current_action == "grid_support_and_charge"
    assert combined_active.current_slot_end is not None
    assert combined_active.current_slot_end > combined_active.planned_charges[0].start

    # On a single 10 kW inverter, a 50% command is only 5 kW.  The same 10 kWh
    # therefore needs all four half-hour slots from 13:00 until 15:00.
    five_kw_lead = optimize_tariff_charging(
        settings(
            pre_peak,
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=peak_load,
            charge_power_kw=5.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert five_kw_lead.status_code == "ready"
    assert [item.start.strftime("%H:%M") for item in five_kw_lead.planned_charges] == [
        "13:00",
        "13:30",
        "14:00",
        "14:30",
    ]
    assert abs(five_kw_lead.planned_stored_energy_kwh - 10.0) < 1e-6

    # If only ten minutes remain, the optimizer may safely use them, but it
    # must report that the cheap window cannot cover the complete deficit.
    too_late = optimize_tariff_charging(
        settings(
            pre_peak.replace(hour=14, minute=50),
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=peak_load,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert too_late.status_code == "insufficient_cheap_window"
    assert too_late.remaining_shortage_kwh > 8.0
    # The live SOC target must remain the complete required target, not the
    # tiny amount that fits into the last ten minutes.  Otherwise HA reaches
    # the moving target, stops and restarts Grid Charge every minute.
    assert too_late.target_soc_percent >= 69.9
    minute_later = optimize_tariff_charging(
        settings(
            pre_peak.replace(hour=14, minute=55),
            battery_soc_percent=21.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=peak_load,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert minute_later.current_slot_planned
    assert abs(minute_later.target_soc_percent - too_late.target_soc_percent) < 0.1
    assert minute_later.current_slot_end == pre_peak.replace(hour=15, minute=0)

    # The controller latches the end of the complete contiguous run selected
    # at cycle start.  A live replan may remove the current slot after SOC moves,
    # but it must not shorten this already accepted 14:00-15:00 charging window.
    active_contiguous_run = optimize_tariff_charging(
        settings(
            pre_peak.replace(hour=14, minute=0),
            battery_soc_percent=20.0,
            reserve_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh=peak_load,
            charge_power_kw=10.0,
            battery_charge_power_kw=20.0,
            charge_efficiency_percent=100.0,
            discharge_efficiency_percent=100.0,
            minimum_saving_pln_kwh=0.0,
        )
    )
    assert active_contiguous_run.current_slot_planned
    assert active_contiguous_run.current_action == "battery_charge"
    assert active_contiguous_run.current_slot_end == pre_peak.replace(
        hour=15,
        minute=0,
    )

    # Live correction ignores tiny dawn samples, then progressively lowers
    # only today's forecast when a string fault causes sustained shortfall.
    dawn_factor = adaptive_forecast_factor(
        0.90,
        actual_energy_kwh=0.4,
        expected_elapsed_kwh=0.7,
        eligible=True,
    )
    assert dawn_factor == (0.90, None, 0.0)
    failed_string_factor, live_ratio, confidence = adaptive_forecast_factor(
        0.90,
        actual_energy_kwh=2.5,
        expected_elapsed_kwh=5.0,
        eligible=True,
    )
    assert live_ratio == 0.5
    assert 0.8 < confidence < 0.9
    assert 0.50 < failed_string_factor < 0.60

    # The reserve is dynamic.  With Self-Use 25% and a 2-point correction the
    # optimizer restores 27%; changing the user threshold changes the target.
    cheap_now = monday.replace(hour=22, minute=0)
    dynamic_reserve = optimize_tariff_charging(
        settings(
            cheap_now,
            battery_soc_percent=25.0,
            reserve_soc_percent=27.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
        )
    )
    assert dynamic_reserve.status_code == "ready"
    assert dynamic_reserve.planned_stored_energy_kwh >= 0.39
    assert dynamic_reserve.target_soc_percent >= 27.0 - 0.1
    user_changed_reserve = optimize_tariff_charging(
        settings(
            cheap_now,
            battery_soc_percent=30.0,
            reserve_soc_percent=37.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
        )
    )
    assert user_changed_reserve.planned_stored_energy_kwh >= 1.39
    assert user_changed_reserve.target_soc_percent >= 37.0 - 0.1

    # Do not buy energy only because SOC is initially below the dynamic floor
    # when forecast PV will rebuild the reserve before the next cheap window.
    pv_restores_reserve = optimize_tariff_charging(
        settings(
            datetime(2026, 8, 4, 6, 0, tzinfo=ZONE),
            battery_soc_percent=25.0,
            reserve_soc_percent=27.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            pv_by_slot_kwh={
                datetime(2026, 8, 4, 8, 0, tzinfo=ZONE): 1.0,
            },
        )
    )
    assert pv_restores_reserve.status_code == "no_charge_needed"
    assert pv_restores_reserve.planned_grid_import_kwh == 0.0

    # Abundant PV removes the need to charge from the grid.
    sunny = {
        monday.replace(day=4, hour=hour, minute=0): 5.0
        for hour in range(8, 17)
    }
    sunny_result = optimize_tariff_charging(
        settings(
            monday,
            battery_soc_percent=70.0,
            pv_by_slot_kwh=sunny,
            average_daily_load_kwh=8.0,
            average_night_load_kwh=3.0,
        )
    )
    assert sunny_result.status_code == "no_charge_needed"
    assert sunny_result.planned_grid_import_kwh == 0

    # If the battery lasts through most of the cheap night, buy only the small
    # amount that genuinely lowers the full future tariff bill.
    morning_pv = {
        monday.replace(day=4, hour=hour, minute=minute): 5.0
        for hour in range(7, 24)
        for minute in (0, 30)
    }
    late_bridge = optimize_tariff_charging(
        settings(
            monday.replace(minute=0),
            battery_soc_percent=50.0,
            pv_by_slot_kwh=morning_pv,
            average_daily_load_kwh=20.0,
            average_night_load_kwh=8.0,
        )
    )
    assert late_bridge.planned_grid_import_kwh > 0
    assert late_bridge.automation_savings_pln > 0
    assert not any(
        item.start.hour == 22 and item.start.date() == monday.date()
        for item in late_bridge.planned_charges
    )

    # Winter: very low PV and high demand require both direct low-tariff home
    # supply and battery charging for the following expensive periods.
    winter = optimize_tariff_charging(
        settings(
            monday,
            battery_soc_percent=20.0,
            pv_by_slot_kwh={},
            average_daily_load_kwh=30.0,
            average_night_load_kwh=12.0,
            charge_power_kw=8.0,
        )
    )
    assert winter.planned_stored_energy_kwh > 0
    assert any(
        item.action in {"battery_charge", "grid_support_and_charge"}
        for item in winter.planned_charges
    )
    assert all(item.zone == "low" for item in winter.planned_charges)
    assert winter.ending_battery_soc_percent >= 20.0 - 0.1
    assert winter.optimized_grid_cost_pln < winter.baseline_grid_cost_pln

    # A cheap period after the first energy deficit cannot repair the past, but
    # it must still protect later expensive hours instead of cancelling the
    # remainder of the plan.
    before_peak = datetime(2026, 8, 3, 6, 10, tzinfo=ZONE)
    constrained = optimize_tariff_charging(
        settings(
            before_peak,
            battery_soc_percent=20.0,
            average_daily_load_kwh=30.0,
            average_night_load_kwh=10.0,
        )
    )
    assert constrained.remaining_shortage_kwh > 0
    assert any(
        13 <= item.start.hour < 15 for item in constrained.planned_charges
        if item.start > before_peak.replace(hour=7, minute=0)
    )

    # A deficit that exists only during the currently cheap period should be
    # bought directly for the home. Charging the battery first would add
    # conversion losses without avoiding any higher-priced import.
    cheap_direct = optimize_tariff_charging(
        settings(
            monday.replace(hour=22, minute=0),
            battery_soc_percent=20.0,
            average_daily_load_kwh=0.0,
            average_night_load_kwh=0.0,
            load_by_slot_kwh={
                monday.replace(hour=22, minute=0): 2.0,
            },
        )
    )
    assert cheap_direct.baseline_shortage_kwh > 1.9
    assert cheap_direct.planned_grid_import_kwh == 0
    assert cheap_direct.status_code == "shortage_in_low_period"

    # G11 is a reference tariff; it must never create a fake saving.
    g11 = optimize_tariff_charging(
        settings(monday, schedule=schedule("G11"))
    )
    assert g11.status_code == "no_discount_window"
    assert g11.planned_grid_import_kwh == 0

    # A nominally lower zone is rejected if conversion losses and the user's
    # minimum margin make shifting energy more expensive than buying it later.
    narrow_spread = TariffSchedule(
        tariff_type="G12",
        g11_price_pln_kwh=0.85,
        low_price_pln_kwh=0.80,
        medium_price_pln_kwh=0.82,
        peak_price_pln_kwh=0.84,
        cheap_windows=((22 * 60, 6 * 60),),
    )
    narrow = optimize_tariff_charging(
        settings(monday, schedule=narrow_spread, minimum_saving_pln_kwh=0.05)
    )
    assert narrow.planned_grid_import_kwh == 0
    assert narrow.automation_savings_pln == 0

    # A real spread must be exploited even when both prices are below the G11
    # reference; decisions use the future avoided cost, not a fixed G11 gate.
    real_spread = TariffSchedule(
        tariff_type="G12",
        g11_price_pln_kwh=1.30,
        low_price_pln_kwh=0.45,
        medium_price_pln_kwh=0.85,
        peak_price_pln_kwh=1.05,
        cheap_windows=((22 * 60, 6 * 60),),
    )
    shifted = optimize_tariff_charging(
        settings(monday, schedule=real_spread)
    )
    assert shifted.planned_grid_import_kwh > 0
    assert shifted.automation_savings_pln > 0
    assert shifted.optimized_grid_cost_pln < shifted.baseline_grid_cost_pln

    # A learned 30-minute profile is accepted directly, without adding any
    # user-facing configuration fields.
    profiled_load = {
        monday.replace(hour=22, minute=0): 1.5,
        monday.replace(hour=22, minute=30): 1.5,
    }
    profiled = optimize_tariff_charging(
        settings(
            monday,
            schedule=real_spread,
            load_by_slot_kwh=profiled_load,
            battery_soc_percent=20.0,
        )
    )
    assert profiled.baseline_shortage_kwh > 2.9

    saturday = datetime(2026, 8, 8, 10, 0, tzinfo=ZONE)
    assert tariff_rate(saturday, schedule("G12w"))[1] == "low"
    assert tariff_rate(saturday, schedule("G12"))[1] == "peak"
    assert is_polish_public_holiday(datetime(2026, 12, 24).date())
    assert is_polish_public_holiday(datetime(2026, 4, 6).date())
    holiday_only = TariffSchedule(
        tariff_type="G12w",
        g11_price_pln_kwh=0.85,
        low_price_pln_kwh=0.62,
        medium_price_pln_kwh=0.82,
        peak_price_pln_kwh=1.03,
        cheap_windows=((22 * 60, 6 * 60), (13 * 60, 15 * 60)),
        weekend_low_price=False,
        polish_holidays_low_price=True,
    )
    holiday = datetime(2026, 12, 25, 10, 0, tzinfo=ZONE)
    assert tariff_rate(holiday, holiday_only) == (0.62, "low")

    # Deterministic property sweep: varied batteries, loads, power limits and
    # efficiencies must never violate energy, shared-power or cost invariants.
    random = Random(20260804)
    for _ in range(40):
        capacity = random.uniform(5.0, 230.0)
        charge_efficiency = random.uniform(82.0, 98.0)
        case = settings(
            monday,
            battery_capacity_kwh=capacity,
            battery_soc_percent=random.uniform(5.0, 100.0),
            reserve_soc_percent=random.uniform(5.0, 40.0),
            maximum_soc_percent=random.uniform(70.0, 100.0),
            average_daily_load_kwh=random.uniform(4.0, 90.0),
            average_night_load_kwh=random.uniform(1.0, 25.0),
            charge_power_kw=random.uniform(2.0, 40.0),
            battery_charge_power_kw=random.uniform(1.0, 30.0),
            battery_discharge_power_kw=random.uniform(1.0, 35.0),
            charge_efficiency_percent=charge_efficiency,
            discharge_efficiency_percent=random.uniform(82.0, 98.0),
        )
        swept = optimize_tariff_charging(case)
        assert -1e-6 <= swept.ending_battery_kwh <= capacity + 1e-6
        assert swept.remaining_shortage_kwh <= swept.baseline_shortage_kwh + 1e-6
        reserve_gap = max(
            case.reserve_soc_percent - case.battery_soc_percent,
            0.0,
        ) / 100.0 * capacity
        reserve_restoration_cost = (
            reserve_gap
            / max(case.charge_efficiency_percent / 100.0, 0.01)
            * case.schedule.low_price_pln_kwh
        )
        assert swept.optimized_grid_cost_pln <= (
            swept.baseline_grid_cost_pln + reserve_restoration_cost + 1e-6
        )
        assert 0.0 <= swept.target_soc_percent <= 100.0
        for item in swept.planned_charges:
            assert item.grid_import_kwh <= swept.charge_power_kw * 0.5 + 1e-6
            assert item.stored_energy_kwh <= (
                (item.grid_import_kwh - item.direct_load_kwh)
                * charge_efficiency
                / 100.0
                + 1e-6
            )

    print("Tariff optimizer: deterministic scenarios passed")


if __name__ == "__main__":
    main()
