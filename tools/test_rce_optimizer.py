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

FORECAST_PATH = (
    ROOT
    / "custom_components"
    / "hoymiles_hit_modbus"
    / "forecast_model.py"
)
FORECAST_SPEC = importlib.util.spec_from_file_location(
    "hoymiles_forecast_model",
    FORECAST_PATH,
)
if FORECAST_SPEC is None or FORECAST_SPEC.loader is None:
    raise RuntimeError("Cannot load the shared forecast model")
FORECAST = importlib.util.module_from_spec(FORECAST_SPEC)
sys.modules[FORECAST_SPEC.name] = FORECAST
FORECAST_SPEC.loader.exec_module(FORECAST)

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
        charge_efficiency_percent=100.0,
        house_discharge_efficiency_percent=100.0,
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


def test_recorder_profile_is_used_instead_of_flat_load() -> None:
    """A valid 48-slot weekday profile must drive the physical simulation."""
    profile = [0.0] * 48
    profile[12] = 2.0
    profile[36] = 6.0
    profile[44] = 2.0
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=6),
            price_slots=slots(0, 18, 4, 1.0),
            average_daily_load_kwh=10.0,
            average_night_load_kwh=2.0,
            weekday_load_profile_30m_kwh=tuple(profile),
            weekend_load_profile_30m_kwh=tuple(profile),
        )
    )
    assert result.ready
    assert result.load_profile_mode == "weekday_48_slot"


def test_conservative_pv_band_blocks_unsafe_export() -> None:
    """P50 optimism must not make a plan feasible when P10 cannot feed home."""
    noon = NOW.replace(hour=12)
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=8),
            price_slots=slots(0, 18, 4, 1.0),
            battery_soc_percent=20.0,
            average_daily_load_kwh=8.0,
            average_night_load_kwh=4.0,
            pv_by_slot_kwh={noon: 20.0},
            conservative_pv_by_slot_kwh={noon: 0.0},
        )
    )
    assert not result.ready
    assert result.status_code == "home_energy_shortage"


def test_gcf_zero_export_is_a_hard_cap_with_clear_status() -> None:
    """An enabled 0% GCF installation must never receive an export plan."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 4, 2.0),
            export_power_cap_kw=0.0,
            pv_by_slot_kwh={NOW.replace(hour=12): 30.0},
        )
    )
    assert result.ready
    assert result.status_code == "zero_export"
    assert result.maximum_export_power_kw == 0.0
    assert not result.planned_exports
    assert result.natural_export_kwh == 0.0
    assert result.uncontrolled_export_kwh == 0.0
    assert result.physical_limit_source == "gcf_export_cap"


def test_effective_power_input_caps_slot_energy() -> None:
    """A trusted learned limit must cap the catalogue inverter power."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 1, 1.0),
            battery_capacity_kwh=100.0,
            outage_reserve_soc_percent=0.0,
            effective_export_power_kw=3.2,
        )
    )
    assert abs(result.maximum_export_power_kw - 3.2) < 1e-6
    assert abs(result.planned_export_kwh - 1.6) < 0.02
    assert result.physical_limit_source == "effective_export_power"


def test_gcf_caps_natural_pv_export_per_slot() -> None:
    """PV overflow must respect the same enabled GCF AC export ceiling."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=[],
            pv_by_slot_kwh={NOW.replace(hour=12): 10.0},
            export_power_cap_kw=2.0,
        )
    )
    assert result.ready
    assert result.natural_export_kwh <= 1.0 + 1e-6


def test_battery_wear_rejects_gross_but_unprofitable_sale() -> None:
    """Gross positive RCE price below throughput wear must retain energy."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 2, 0.05),
            battery_wear_cost_pln_kwh=0.08,
        )
    )
    assert not result.planned_exports
    assert result.net_optimization_gain_pln >= -1e-6


def test_day3_terminal_value_retains_energy_for_future_import() -> None:
    """Weak Day-3 PV gives stored energy an avoided-import value."""
    pv = {
        NOW.replace(hour=12): 5.0,
        NOW.replace(hour=12) + timedelta(days=1): 5.0,
    }
    price = slots(1, 18, 4, 0.50)
    without_day3 = RCE.optimize_rce(
        base_input(
            price_slots=price,
            pv_by_slot_kwh=pv,
            average_daily_load_kwh=5.0,
            average_night_load_kwh=0.0,
            battery_wear_cost_pln_kwh=0.0,
        )
    )
    with_day3 = RCE.optimize_rce(
        base_input(
            price_slots=price,
            pv_by_slot_kwh=pv,
            average_daily_load_kwh=5.0,
            average_night_load_kwh=0.0,
            battery_wear_cost_pln_kwh=0.0,
            avoided_import_price_pln_kwh=1.0,
            day3_pv_forecast_kwh=0.0,
        )
    )
    assert with_day3.terminal_energy_target_kwh == 5.0
    assert with_day3.planned_export_kwh < without_day3.planned_export_kwh
    assert with_day3.ending_battery_kwh >= 9.0 - 0.02


def test_partial_day3_deficit_keeps_energy_below_avoided_import_value() -> None:
    """Every retained kWh for a real Day-3 deficit has full import value."""
    pv = {
        NOW.replace(hour=12): 10.0,
        NOW.replace(hour=12) + timedelta(days=1): 10.0,
    }
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 8, 0.55),
            pv_by_slot_kwh=pv,
            average_daily_load_kwh=10.0,
            day3_pv_forecast_kwh=5.0,
            battery_wear_cost_pln_kwh=0.0,
            avoided_import_price_pln_kwh=1.0,
            dynamic_reserve_enabled=False,
        )
    )

    assert result.terminal_energy_target_kwh == 5.0
    assert result.terminal_energy_value_pln_kwh == 1.0
    assert result.ending_battery_kwh >= 9.0 - 0.02
    assert result.terminal_energy_value_pln >= 5.0 - 0.02


def test_terminal_value_boundary_uses_house_discharge_efficiency() -> None:
    """The sale boundary follows avoided-import value after DC-to-house loss."""
    pv = {
        NOW.replace(hour=12): 10.0,
        NOW.replace(hour=12) + timedelta(days=1): 10.0,
    }
    common = {
        "pv_by_slot_kwh": pv,
        "average_daily_load_kwh": 10.0,
        "day3_pv_forecast_kwh": 5.0,
        "battery_wear_cost_pln_kwh": 0.0,
        "avoided_import_price_pln_kwh": 1.0,
        "dynamic_reserve_enabled": False,
        "export_efficiency_percent": 95.0,
        "house_discharge_efficiency_percent": 80.0,
    }
    below = RCE.optimize_rce(
        base_input(price_slots=slots(0, 18, 8, 0.84), **common)
    )
    above = RCE.optimize_rce(
        base_input(price_slots=slots(0, 18, 8, 0.85), **common)
    )

    assert below.terminal_energy_value_pln_kwh == 0.8
    assert below.terminal_energy_target_kwh == 6.25
    assert below.ending_battery_kwh >= 10.25 - 0.02
    assert below.terminal_energy_value_pln >= 5.0 - 0.02
    assert above.ending_battery_kwh < below.ending_battery_kwh - 6.1


def test_terminal_reserve_reports_day3_availability_and_reason() -> None:
    """A zero target must distinguish missing Day-3 data from sufficient PV."""
    common = {
        "price_slots": slots(1, 18, 4, 0.50),
        "average_daily_load_kwh": 5.0,
        "average_night_load_kwh": 0.0,
        "battery_wear_cost_pln_kwh": 0.0,
    }
    missing = RCE.optimize_rce(base_input(**common))
    covered = RCE.optimize_rce(
        base_input(**common, day3_pv_forecast_kwh=6.0)
    )
    deficit = RCE.optimize_rce(
        base_input(**common, day3_pv_forecast_kwh=1.0)
    )

    assert not missing.day3_forecast_available
    assert missing.day3_forecast_kwh is None
    assert missing.day3_energy_shortfall_kwh == 0.0
    assert missing.terminal_energy_target_kwh == 0.0
    assert missing.terminal_reserve_reason == "day3_forecast_missing"

    assert covered.day3_forecast_available
    assert covered.day3_forecast_kwh == 6.0
    assert covered.day3_load_requirement_kwh == 5.0
    assert covered.day3_energy_shortfall_kwh == 0.0
    assert covered.terminal_energy_target_kwh == 0.0
    assert covered.terminal_reserve_reason == "day3_pv_covers_load"

    assert deficit.day3_forecast_available
    assert deficit.day3_energy_shortfall_kwh == 4.0
    assert deficit.terminal_energy_target_kwh == 4.0
    assert deficit.terminal_reserve_reason == "day3_pv_deficit"


def test_whole_soc_control_reserve_does_not_overstate_export() -> None:
    """A fractional protected reserve must be rounded up before planning."""
    result = RCE.optimize_rce(
        base_input(
            now=NOW.replace(hour=12),
            price_slots=slots(0, 18, 5, 2.0),
            battery_capacity_kwh=230.0,
            battery_soc_percent=55.0,
            outage_reserve_soc_percent=20.0,
            safety_margin_soc_percent=2.0,
            average_daily_load_kwh=7.79,
            average_night_load_kwh=7.79,
            inverter_power_kw=30.0,
            export_efficiency_percent=95.0,
            battery_wear_cost_pln_kwh=0.0,
        )
    )

    assert result.ready
    assert result.minimum_soc_percent == 26
    assert abs(result.base_reserve_energy_kwh - 50.6) < 1e-6
    assert abs(result.protected_home_energy_kwh - 58.39) < 1e-6
    assert abs(result.control_reserve_energy_kwh - 59.8) < 1e-6
    assert abs(result.soc_quantization_reserve_kwh - 1.41) < 1e-6
    assert abs(result.available_energy_now_kwh - 66.7) < 1e-6
    assert (
        result.planned_export_kwh
        <= result.available_energy_now_kwh * 0.95 + 1e-6
    )
    # The control threshold protects the upcoming night at export time.  LOAD
    # then legitimately consumes that protected energy, so horizon-end SOC may
    # be below 26% but must remain above the 22% outage reserve.
    assert result.ending_battery_kwh < result.control_reserve_energy_kwh
    assert result.ending_battery_kwh >= result.base_reserve_energy_kwh - 1e-6


def test_gross_and_net_optimization_gain_are_unambiguous() -> None:
    """Legacy gain stays gross while net gain explicitly subtracts wear."""
    result = RCE.optimize_rce(
        base_input(
            price_slots=slots(0, 18, 4, 1.0),
            battery_wear_cost_pln_kwh=0.08,
        )
    )
    assert result.planned_export_kwh > 0.0
    assert abs(
        result.optimization_gain_pln - result.gross_optimization_gain_pln
    ) < 1e-9
    assert result.net_optimization_gain_pln < result.gross_optimization_gain_pln
    assert abs(
        result.net_optimization_gain_pln
        - (
            result.gross_optimization_gain_pln
            - result.battery_wear_cost_pln
            + result.terminal_energy_value_delta_pln
        )
    ) < 1e-6


def test_sensor_exposes_terminal_and_gain_contract() -> None:
    """The HA sensor must publish the explicit optimizer diagnostics."""
    sensor_source = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "rce_sensor.py"
    ).read_text(encoding="utf-8")
    for attribute in (
        '"day3_forecast_available"',
        '"day3_energy_shortfall_kwh"',
        '"terminal_reserve_reason"',
        '"terminal_energy_target_reason"',
        '"gross_optimization_gain_pln"',
        '"net_optimization_gain_pln"',
        '"terminal_energy_value_delta_pln"',
    ):
        assert attribute in sensor_source


def test_shared_forecast_model_is_conservative_and_robust() -> None:
    """Live underproduction and an outlier cannot make PV optimistic."""
    factor, ratio, confidence = FORECAST.adaptive_forecast_factor(
        0.9,
        2.0,
        5.0,
        eligible=True,
    )
    assert ratio == 0.4
    assert 0.4 < factor < 0.9
    assert confidence > 0.8
    learned, uncertainty, count = FORECAST.robust_weighted_factor(
        [(1.0, 0.82), (2.0, 0.80), (3.0, 1.10), (8.0, 0.78)]
    )
    assert count == 4
    assert 0.78 <= learned <= 0.83
    assert uncertainty >= 0.0


def test_house_energy_model_applies_charge_and_discharge_losses() -> None:
    """PV surplus and battery-fed LOAD must use their own efficiencies."""
    start = NOW.replace(hour=12)
    settings = base_input(
        battery_soc_percent=50.0,
        charge_efficiency_percent=80.0,
        house_discharge_efficiency_percent=80.0,
        pv_by_slot_kwh={start: 4.0},
    )
    feasible, ending, _ = RCE._simulate(
        [start],
        settings,
        {start: 2.0},
        {},
        0.0,
    )
    assert feasible
    assert abs(ending - 11.6) < 1e-6
    settings = replace(settings, pv_by_slot_kwh={})
    feasible, ending, _ = RCE._simulate(
        [start],
        settings,
        {start: 2.0},
        {},
        0.0,
    )
    assert feasible
    assert abs(ending - 7.5) < 1e-6


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
        test_recorder_profile_is_used_instead_of_flat_load,
        test_conservative_pv_band_blocks_unsafe_export,
        test_gcf_zero_export_is_a_hard_cap_with_clear_status,
        test_effective_power_input_caps_slot_energy,
        test_gcf_caps_natural_pv_export_per_slot,
        test_battery_wear_rejects_gross_but_unprofitable_sale,
        test_day3_terminal_value_retains_energy_for_future_import,
        test_partial_day3_deficit_keeps_energy_below_avoided_import_value,
        test_terminal_value_boundary_uses_house_discharge_efficiency,
        test_terminal_reserve_reports_day3_availability_and_reason,
        test_whole_soc_control_reserve_does_not_overstate_export,
        test_gross_and_net_optimization_gain_are_unambiguous,
        test_sensor_exposes_terminal_and_gain_contract,
        test_shared_forecast_model_is_conservative_and_robust,
        test_house_energy_model_applies_charge_and_discharge_losses,
    ]
    for test in tests:
        test()
    print(f"RCE optimizer: {len(tests)} deterministic scenarios passed")


if __name__ == "__main__":
    main()
