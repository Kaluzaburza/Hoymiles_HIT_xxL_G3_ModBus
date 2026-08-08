"""Deterministic safety and profitability tests for the RCE optimizer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "custom_components"
    / "hoymiles_hit_modbus"
    / "rce_optimizer.py"
)
SPEC = importlib.util.spec_from_file_location("hoymiles_rce_optimizer", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the RCE optimizer")
RCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RCE
SPEC.loader.exec_module(RCE)

WARSAW = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 7, 28, 0, 0, tzinfo=WARSAW)


def slots(day_offset: int, hour: int, count: int, price: float):
    """Create consecutive half-hour price slots."""
    start = NOW.replace(hour=hour) + timedelta(days=day_offset)
    return [
        RCE.PriceSlot(start=start + timedelta(minutes=30 * index), price_pln_kwh=price)
        for index in range(count)
    ]


def base_input(**changes):
    """Return a complete deterministic optimizer input."""
    settings = RCE.OptimizerInput(
        now=NOW,
        price_slots=[],
        pv_by_slot_kwh={},
        battery_capacity_kwh=20.0,
        battery_soc_percent=100.0,
        outage_reserve_soc_percent=20.0,
        safety_margin_soc_percent=0.0,
        manual_minimum_soc_percent=20.0,
        dynamic_reserve_enabled=True,
        average_daily_load_kwh=0.0,
        average_night_load_kwh=0.0,
        night_start_minute=20 * 60,
        night_end_minute=8 * 60,
        inverter_power_kw=10.0,
        inverter_count=1,
        discharge_power_percent=100.0,
        export_efficiency_percent=100.0,
    )
    return replace(settings, **changes)


def test_higher_tomorrow_price_wins() -> None:
    """Energy must be held for tomorrow when its price is higher."""
    today = slots(0, 18, 4, 0.70)
    tomorrow = slots(1, 6, 4, 0.90)
    result = RCE.optimize_rce(
        base_input(price_slots=[*today, *tomorrow])
    )
    assert result.ready
    assert result.planned_export_kwh > 15.9
    assert {
        item.start.date() for item in result.planned_exports
    } == {tomorrow[0].start.date()}
    assert result.ending_battery_kwh >= 4.0 - 1e-6
    assert abs(result.automatic_price_floor_pln_kwh - 0.9) < 1e-6


def test_home_energy_is_never_sold() -> None:
    """A weak PV forecast must keep enough energy for both protected nights."""
    price_slots = [
        *slots(0, 18, 8, 1.20),
        *slots(1, 6, 8, 1.50),
    ]
    tomorrow_noon = NOW.replace(hour=12) + timedelta(days=1)
    result = RCE.optimize_rce(
        base_input(
            price_slots=price_slots,
            pv_by_slot_kwh={tomorrow_noon: 2.0},
            average_daily_load_kwh=7.0,
            average_night_load_kwh=4.0,
        )
    )
    assert result.ready
    assert result.minimum_soc_percent > 20
    assert abs(result.base_reserve_energy_kwh - 4.0) < 1e-6
    assert result.additional_forecast_reserve_kwh > 0
    assert abs(
        result.protected_home_energy_kwh
        - (
            result.base_reserve_energy_kwh
            + result.additional_forecast_reserve_kwh
        )
    ) < 1e-6
    assert result.ending_battery_kwh >= 4.0 - 1e-6
    assert result.planned_export_kwh < 4.0


def test_upcoming_night_is_reserved_even_before_sunset() -> None:
    """A sunny daytime forecast must not hide the next night's house load."""
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=12),
            price_slots=slots(0, 18, 4, 1.0),
            battery_capacity_kwh=100.0,
            outage_reserve_soc_percent=30.0,
            safety_margin_soc_percent=2.0,
            average_daily_load_kwh=30.0,
            average_night_load_kwh=20.0,
            pv_by_slot_kwh={
                NOW.replace(hour=16): 60.0,
            },
        )
    )
    assert result.ready
    assert abs(result.base_reserve_energy_kwh - 32.0) < 1e-6
    assert abs(result.protected_night_energy_kwh - 20.0) < 1e-6
    assert abs(result.protected_home_energy_kwh - 52.0) < 1e-6
    assert result.minimum_soc_percent == 52
    assert result.ending_battery_kwh >= 32.0 - 1e-6


def test_low_market_prices_still_use_the_best_48h_slots() -> None:
    """No manual threshold may prevent controlled export at the best price."""
    today = slots(0, 18, 4, 0.05)
    tomorrow = slots(1, 6, 4, 0.12)
    result = RCE.optimize_rce(
        base_input(price_slots=[*today, *tomorrow])
    )
    assert result.ready
    assert result.planned_exports
    assert {
        item.start.date() for item in result.planned_exports
    } == {tomorrow[0].start.date()}
    assert abs(result.automatic_price_floor_pln_kwh - 0.12) < 1e-6


def test_negative_prices_do_not_dump_stored_energy() -> None:
    """Stored surplus must be retained when every available sale loses money."""
    result = RCE.optimize_rce(
        base_input(price_slots=slots(0, 18, 4, -0.2))
    )
    assert result.ready
    assert not result.planned_exports
    assert result.status_code == "home_protected"
    assert result.automatic_price_floor_pln_kwh is None
    assert abs(result.optimization_gain_pln) < 1e-6


def test_discharge_creates_headroom_before_worse_pv_overflow() -> None:
    """An earlier sale is valid when it avoids a lower-priced PV spill."""
    earlier = RCE.PriceSlot(
        start=NOW.replace(hour=10),
        price_pln_kwh=0.30,
    )
    overflow = RCE.PriceSlot(
        start=NOW.replace(hour=12),
        price_pln_kwh=-0.20,
    )
    result = RCE.optimize_rce(
        base_input(
            price_slots=[earlier, overflow],
            pv_by_slot_kwh={overflow.start: 10.0},
        )
    )
    assert result.ready
    assert [item.start for item in result.planned_exports] == [earlier.start]
    assert result.total_revenue_pln > result.uncontrolled_revenue_pln
    assert result.optimization_gain_pln > 2.4


def test_shortage_blocks_export() -> None:
    """Export must fail closed when the home cannot reach the safety floor."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 4, 2.0),
            battery_soc_percent=35.0,
            average_daily_load_kwh=12.0,
            average_night_load_kwh=7.0,
        )
    )
    assert not result.ready
    assert result.status_code == "home_energy_shortage"
    assert not result.planned_exports


def test_parallel_power_scales_slot_energy() -> None:
    """Detected parallel inverters must multiply the physical export limit."""
    candidate = slots(0, 12, 1, 1.0)
    single = RCE.optimize_rce(
        base_input(
            price_slots=candidate,
            battery_capacity_kwh=100.0,
            outage_reserve_soc_percent=0.0,
            inverter_count=1,
            discharge_power_percent=50.0,
        )
    )
    parallel = RCE.optimize_rce(
        base_input(
            price_slots=candidate,
            battery_capacity_kwh=100.0,
            outage_reserve_soc_percent=0.0,
            inverter_count=2,
            discharge_power_percent=50.0,
        )
    )
    assert abs(single.maximum_export_power_kw - 5.0) < 1e-6
    assert abs(parallel.maximum_export_power_kw - 10.0) < 1e-6
    assert abs(single.planned_export_kwh - 2.5) < 0.02
    assert abs(parallel.planned_export_kwh - 5.0) < 0.02


def test_export_lockout_excludes_slots() -> None:
    """A blocked high price must not override the configured lockout."""
    blocked = RCE.PriceSlot(
        start=NOW.replace(hour=22),
        price_pln_kwh=2.0,
        blocked=True,
    )
    allowed = RCE.PriceSlot(
        start=NOW.replace(hour=20),
        price_pln_kwh=0.8,
    )
    result = RCE.optimize_rce(base_input(price_slots=[blocked, allowed]))
    assert [item.start for item in result.planned_exports] == [allowed.start]


def test_feasible_plan_is_not_broken_by_display_rounding() -> None:
    """A fractional final slot must not invalidate an otherwise safe plan."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 6, 32, 0.8),
            battery_capacity_kwh=5.1,
            battery_soc_percent=53.0,
            export_efficiency_percent=93.0,
            discharge_power_percent=30.0,
        )
    )
    assert result.ready
    assert result.status_code != "optimizer_error"
    assert result.ending_battery_kwh >= 1.02 - 1e-6


def test_today_only_prices_produce_a_safe_plan() -> None:
    """Missing tomorrow prices must not block profitable slots today."""
    today = slots(0, 18, 6, 0.9)
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=6),
            price_slots=today,
            average_daily_load_kwh=4.0,
            average_night_load_kwh=2.0,
        )
    )
    assert result.ready
    assert result.planned_exports
    assert {item.start.date() for item in result.planned_exports} == {
        NOW.date()
    }
    assert result.ending_battery_kwh >= 4.0 - 1e-6


def test_export_and_revenue_totals_expose_their_sources() -> None:
    """The UI totals must equal battery export plus natural PV surplus."""
    planned = RCE.PriceSlot(
        start=NOW.replace(hour=10),
        price_pln_kwh=1.0,
    )
    overflow = RCE.PriceSlot(
        start=NOW.replace(hour=12),
        price_pln_kwh=0.5,
    )
    result = RCE.optimize_rce(
        base_input(
            price_slots=[planned, overflow],
            pv_by_slot_kwh={overflow.start: 10.0},
        )
    )
    assert result.ready
    assert result.planned_export_kwh > 4.9
    assert result.natural_export_kwh > 4.9
    assert abs(
        result.total_export_kwh
        - (result.planned_export_kwh + result.natural_export_kwh)
    ) < 1e-6
    assert abs(
        result.total_revenue_pln
        - (result.planned_revenue_pln + result.natural_revenue_pln)
    ) < 1e-6


def test_bms_current_limit_caps_export_power() -> None:
    """A small battery must cap a larger inverter before planning export."""
    candidate = slots(0, 18, 1, 1.0)
    result = RCE.optimize_rce(
        base_input(
            price_slots=candidate,
            battery_capacity_kwh=21.0,
            outage_reserve_soc_percent=0.0,
            inverter_power_kw=20.0,
            discharge_power_percent=100.0,
            export_efficiency_percent=95.0,
            bms_max_discharge_current_a=170.0,
            battery_voltage_v=53.0,
            bms_power_safety_percent=95.0,
        )
    )
    expected_limit = 170.0 * 53.0 / 1000.0 * 0.95 * 0.95
    assert result.ready
    assert result.bms_limit_active
    assert abs(result.requested_export_power_kw - 20.0) < 1e-6
    assert abs(result.bms_discharge_power_limit_kw - expected_limit) < 1e-6
    assert abs(result.maximum_export_power_kw - expected_limit) < 1e-6
    assert abs(
        result.bms_discharge_limit_percent
        - expected_limit / 20.0 * 100.0
    ) < 1e-6
    assert abs(result.planned_export_kwh - expected_limit * 0.5) < 0.02


def test_actual_day_load_corrects_day_load_projection() -> None:
    """Actual phase LOAD energy must correct an understated daytime profile."""
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=14),
            price_slots=slots(0, 14, 12, 1.0),
            battery_capacity_kwh=100.0,
            average_daily_load_kwh=10.0,
            average_night_load_kwh=8.0,
            actual_day_load_today_kwh=4.0,
            pv_to_load_power_kw=2.0,
        )
    )
    assert result.ready
    assert abs(result.daylight_progress_percent - 50.0) < 1e-6
    assert abs(result.historical_day_load_kwh - 2.0) < 1e-6
    assert abs(result.live_projected_day_load_kwh - 8.0) < 1e-6
    assert abs(result.modeled_day_load_kwh - 8.0) < 1e-6


def main() -> None:
    """Run without pytest so the release validator has no extra dependency."""
    tests = [
        test_higher_tomorrow_price_wins,
        test_low_market_prices_still_use_the_best_48h_slots,
        test_negative_prices_do_not_dump_stored_energy,
        test_discharge_creates_headroom_before_worse_pv_overflow,
        test_home_energy_is_never_sold,
        test_upcoming_night_is_reserved_even_before_sunset,
        test_shortage_blocks_export,
        test_parallel_power_scales_slot_energy,
        test_export_lockout_excludes_slots,
        test_feasible_plan_is_not_broken_by_display_rounding,
        test_today_only_prices_produce_a_safe_plan,
        test_export_and_revenue_totals_expose_their_sources,
        test_bms_current_limit_caps_export_power,
        test_actual_day_load_corrects_day_load_projection,
    ]
    for test in tests:
        test()
    print(f"RCE optimizer: {len(tests)} deterministic scenarios passed")


if __name__ == "__main__":
    main()
