"""Deterministic safety tests for the RCEm voltage controller."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from rcm_optimizer import (  # noqa: E402
    RCMOptimizerInput,
    RCMRiskWindowInput,
    optimize_rcm,
    select_rcm_load_profile,
    select_rcm_pv_profile,
)


WARSAW = ZoneInfo("Europe/Warsaw")


def settings(**overrides) -> RCMOptimizerInput:
    values = {
        "now": datetime(2026, 8, 8, 12, 0, tzinfo=WARSAW),
        "voltage_l1_v": 238.0,
        "voltage_l2_v": 239.0,
        "voltage_l3_v": 240.0,
        "filtered_voltage_v": 240.0,
        "rolling_10m_voltage_v": 240.0,
        "historical_p90_voltage_v": 250.0,
        "risk_windows": ((12 * 60 + 30, 14 * 60 + 15, 254.0),),
        "history_days": 4,
        "pv_power_kw": 8.0,
        "load_power_kw": 2.0,
        "grid_export_power_kw": 5.5,
        "battery_capacity_kwh": 21.0,
        "battery_soc_percent": 70.0,
        "reserve_soc_percent": 25.0,
        "safety_margin_soc_percent": 2.0,
        "protected_minimum_soc_percent": 35.0,
        "expected_risk_surplus_kwh": 8.0,
        "expected_natural_headroom_kwh": 0.4,
        "minutes_to_risk": 90,
        "risk_day_offset": 0,
        "system_power_kw": 10.0,
        "battery_voltage_v": 52.0,
        "bms_max_charge_current_a": 175.0,
        "bms_max_discharge_current_a": 175.0,
        "current_charge_limit_percent": 80.0,
        "saved_charge_limit_percent": 100.0,
        "export_control_enabled": True,
        "current_export_limit_percent": 50.0,
        "saved_export_limit_percent": 50.0,
        "user_export_cap_percent": 60.0,
        "charge_efficiency_percent": 95.0,
    }
    values.update(overrides)
    return RCMOptimizerInput(**values)


def main() -> None:
    weekday = tuple(0.1 + index / 1000.0 for index in range(48))
    weekend = tuple(0.2 + index / 1000.0 for index in range(48))
    selected_weekday = select_rcm_load_profile(
        weekend=False,
        average_profile=None,
        weekday_profile=weekday,
        weekend_profile=weekend,
        average_daily_kwh=10.0,
    )
    selected_weekend = select_rcm_load_profile(
        weekend=True,
        average_profile=None,
        weekday_profile=weekday,
        weekend_profile=weekend,
        average_daily_kwh=10.0,
    )
    assert selected_weekday.source == "weekday_48_slot"
    assert selected_weekday.slot_kwh == weekday
    assert selected_weekend.source == "weekend_48_slot"
    assert selected_weekend.slot_kwh == weekend
    flat_load = select_rcm_load_profile(
        weekend=False,
        average_profile=None,
        weekday_profile=None,
        weekend_profile=None,
        average_daily_kwh=9.6,
    )
    assert flat_load.source == "flat_daily_fallback"
    assert round(sum(flat_load.slot_kwh), 3) == 9.6
    malformed_load = select_rcm_load_profile(
        weekend=False,
        average_profile="not-a-profile",
        weekday_profile=None,
        weekend_profile=None,
        average_daily_kwh="not-a-number",
    )
    assert malformed_load.source == "flat_daily_fallback"
    assert malformed_load.selected_total_kwh == 0.0

    detailed_p90 = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=6.0,
        detailed_p50_by_slot={24: 0.8, 25: 2.2},
        detailed_p90_by_slot={24: 1.0, 25: 3.0},
        first_slot=24,
        current_slot_fraction=0.5,
        risk_slots=(24, 25),
    )
    assert detailed_p90.source == "solcast_30m_p90"
    assert round(sum(detailed_p90.slot_kwh), 3) == 6.0
    assert detailed_p90.slot_kwh[25] > detailed_p90.slot_kwh[24] * 5
    assert detailed_p90.confidence == 0.95
    p50_shape = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=5.0,
        detailed_p50_by_slot={24: 1.0, 25: 3.0},
        detailed_p90_by_slot={},
        first_slot=24,
        risk_slots=(24, 25),
    )
    assert p50_shape.source == "solcast_30m_p50_shape_p90_total"
    assert round(sum(p50_shape.slot_kwh), 3) == 5.0
    partial_p90 = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=5.0,
        detailed_p50_by_slot={24: 1.0, 25: 3.0},
        detailed_p90_by_slot={24: 1.0},
        first_slot=24,
        risk_slots=(24, 25),
    )
    assert partial_p90.source == "solcast_30m_p50_shape_p90_total"
    partial_all = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=5.0,
        detailed_p50_by_slot={24: 1.0},
        detailed_p90_by_slot={24: 1.0},
        first_slot=24,
        risk_slots=(24, 25),
    )
    assert partial_all.source == "solcast_p90_total_shaped_fallback"
    shaped_fallback = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=None,
        detailed_p50_by_slot=None,
        detailed_p90_by_slot=None,
        first_slot=10,
    )
    assert shaped_fallback.source == "solcast_total_shaped_fallback"
    assert shaped_fallback.confidence == 0.4
    assert round(sum(shaped_fallback.slot_kwh), 3) == 4.0
    malformed_pv = select_rcm_pv_profile(
        forecast_total_kwh="not-a-number",
        forecast_p90_total_kwh="not-a-number",
        detailed_p50_by_slot={"bad": "bad"},
        detailed_p90_by_slot=None,
        first_slot="bad",
        current_slot_fraction="bad",
        risk_slots=("bad", 99),
    )
    assert malformed_pv.selected_total_kwh == 0.0

    learning = optimize_rcm(settings(history_days=0))
    assert learning.status_code == "learning"
    assert learning.action == "restore"
    assert learning.recommended_charge_limit_percent == 90.0

    headroom = optimize_rcm(settings(now=settings().now.replace(hour=11, minute=0)))
    assert headroom.status_code == "preparing_discharge"
    assert headroom.action == "grid_discharge_preparation"
    assert headroom.recommended_charge_limit_percent == 70.0
    assert headroom.reserve_soc_percent == 35.0
    assert headroom.protected_minimum_soc_percent == 35.0
    assert headroom.headroom_shortfall_kwh > 1.0
    assert headroom.target_soc_before_risk_percent == 63.9
    assert headroom.expected_natural_headroom_kwh == 0.4
    assert headroom.planned_grid_discharge_kwh == 0.9
    assert headroom.pre_discharge_target_soc_percent == 65.8
    assert headroom.pre_discharge_power_percent == 29.9
    assert headroom.pre_discharge_ready

    natural_use_is_enough = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11, minute=0),
            expected_natural_headroom_kwh=2.0,
        )
    )
    assert natural_use_is_enough.status_code == "preparing_headroom"
    assert natural_use_is_enough.planned_grid_discharge_kwh == 0.0
    assert not natural_use_is_enough.pre_discharge_ready

    regulating = optimize_rcm(
        settings(
            now=settings().now.replace(hour=13),
            voltage_l1_v=250.0,
            voltage_l2_v=250.5,
            voltage_l3_v=251.1,
            filtered_voltage_v=250.8,
            rolling_10m_voltage_v=250.0,
            current_charge_limit_percent=50.0,
        )
    )
    assert regulating.status_code == "controlling"
    assert regulating.action == "absorb_pv"
    assert regulating.recommended_charge_limit_percent == 60.0

    # 52 V x 100 A is 5.2 kW, so a 10 kW inverter must never receive more
    # than a 52% global battery-charge recommendation.
    emergency = optimize_rcm(
        settings(
            now=settings().now.replace(hour=13),
            voltage_l1_v=253.2,
            voltage_l2_v=252.0,
            voltage_l3_v=251.0,
            filtered_voltage_v=252.8,
            rolling_10m_voltage_v=252.2,
            battery_voltage_v=52.0,
            bms_max_charge_current_a=100.0,
            current_charge_limit_percent=30.0,
        )
    )
    assert emergency.status_code == "emergency"
    assert emergency.action == "absorb_pv"
    assert emergency.bms_charge_power_limit_kw == 5.2
    assert emergency.recommended_charge_limit_percent == 52.0
    assert emergency.recommended_charge_power_kw == 5.2
    assert emergency.recommended_export_limit_percent == 0.0

    stable = optimize_rcm(
        settings(
            now=settings().now.replace(hour=17),
            historical_p90_voltage_v=240.0,
            battery_soc_percent=50.0,
            current_charge_limit_percent=40.0,
            saved_charge_limit_percent=80.0,
            risk_day_offset=1,
            minutes_to_risk=20 * 60,
        )
    )
    assert stable.status_code == "ready"
    assert stable.action == "restore"
    assert stable.recommended_charge_limit_percent == 50.0
    assert stable.recommended_export_limit_percent == 50.0

    contractual_cap = optimize_rcm(
        settings(
            current_export_limit_percent=80.0,
            saved_export_limit_percent=50.0,
            user_export_cap_percent=40.0,
        )
    )
    assert contractual_cap.effective_export_cap_percent == 40.0
    assert contractual_cap.recommended_export_limit_percent == 40.0

    full_battery = optimize_rcm(
        settings(
            now=settings().now.replace(hour=13),
            voltage_l1_v=251.2,
            voltage_l2_v=251.0,
            voltage_l3_v=250.5,
            filtered_voltage_v=251.1,
            rolling_10m_voltage_v=250.9,
            battery_soc_percent=100.0,
            current_export_limit_percent=50.0,
        )
    )
    assert full_battery.recommended_export_limit_percent == 45.0

    protected_floor = optimize_rcm(
        settings(
            battery_soc_percent=100.0,
            expected_risk_surplus_kwh=100.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=180,
        )
    )
    assert protected_floor.pre_discharge_target_soc_percent == 35.0
    assert protected_floor.planned_grid_discharge_kwh == 13.65

    zero_export = optimize_rcm(
        settings(
            current_export_limit_percent=0.0,
            saved_export_limit_percent=0.0,
        )
    )
    assert zero_export.pre_discharge_power_kw == 0.0
    assert not zero_export.pre_discharge_ready

    minimum_floor = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11, minute=0),
            battery_soc_percent=90.0,
            expected_risk_surplus_kwh=3.0,
            expected_natural_headroom_kwh=0.0,
            expected_pre_risk_surplus_kwh=2.0,
            minutes_to_risk=60,
            risk_window_forecasts=(
                RCMRiskWindowInput(
                    start_minute=12 * 60,
                    end_minute=13 * 60,
                    peak_voltage_v=253.4,
                    day_offset=0,
                    expected_pv_kwh=4.0,
                    expected_load_kwh=1.0,
                    expected_surplus_kwh=3.0,
                    natural_headroom_before_kwh=0.0,
                ),
            ),
        )
    )
    assert minimum_floor.unavoidable_minimum_charge_kwh == 0.95
    assert minimum_floor.required_headroom_kwh == 3.8
    assert minimum_floor.risk_window_plans[0].required_headroom_kwh == 3.8
    assert minimum_floor.planned_grid_discharge_kwh == 1.7

    exact_minimum_floor = optimize_rcm(
        settings(
            now=settings().now.replace(hour=8, minute=0),
            battery_soc_percent=90.0,
            expected_risk_surplus_kwh=3.0,
            expected_pre_risk_surplus_kwh=20.0,
            expected_unavoidable_charge_input_kwh=0.25,
            minutes_to_risk=240,
            risk_window_forecasts=(
                RCMRiskWindowInput(720, 780, 253.0, 0, 4.0, 1.0, 3.0, 0.0),
            ),
        )
    )
    assert exact_minimum_floor.unavoidable_minimum_charge_kwh == 0.237
    assert exact_minimum_floor.required_headroom_kwh == 3.087

    two_windows = optimize_rcm(
        settings(
            now=settings().now.replace(hour=9, minute=0),
            battery_soc_percent=90.0,
            expected_risk_surplus_kwh=4.0,
            expected_natural_headroom_kwh=0.0,
            expected_pre_risk_surplus_kwh=0.0,
            minutes_to_risk=120,
            risk_window_forecasts=(
                RCMRiskWindowInput(660, 720, 252.0, 0, 3.0, 1.0, 2.0, 0.0),
                RCMRiskWindowInput(780, 840, 253.0, 0, 3.0, 1.0, 2.0, 0.5),
            ),
        )
    )
    assert len(two_windows.risk_window_plans) == 2
    assert two_windows.risk_window_plans[0].cumulative_headroom_shortfall_kwh == 0.0
    assert two_windows.risk_window_plans[1].projected_headroom_before_kwh == 0.7
    assert two_windows.risk_window_plans[1].cumulative_headroom_shortfall_kwh == 1.2
    assert two_windows.required_headroom_kwh == 3.3
    assert two_windows.planned_grid_discharge_kwh == 1.2

    replenished_between_windows = optimize_rcm(
        settings(
            battery_soc_percent=95.0,
            expected_risk_surplus_kwh=4.0,
            expected_natural_headroom_kwh=0.0,
            risk_window_forecasts=(
                RCMRiskWindowInput(660, 720, 252.0, 0, 3.0, 1.0, 2.0, 0.0),
                RCMRiskWindowInput(780, 840, 253.0, 0, 3.0, 1.0, 2.0, 2.5),
            ),
        )
    )
    assert replenished_between_windows.required_headroom_kwh == 1.9
    assert replenished_between_windows.headroom_shortfall_kwh == 0.85
    assert replenished_between_windows.planned_grid_discharge_kwh == 0.85

    night = optimize_rcm(
        settings(
            now=settings().now.replace(hour=2),
            pv_power_kw=0.0,
            load_power_kw=0.8,
            grid_export_power_kw=0.0,
        )
    )
    assert night.estimated_safe_export_power_kw is None

    for result in (
        learning,
        headroom,
        regulating,
        emergency,
        stable,
        contractual_cap,
        full_battery,
        protected_floor,
        zero_export,
        minimum_floor,
        exact_minimum_floor,
        two_windows,
        replenished_between_windows,
        night,
    ):
        assert 10.0 <= result.recommended_charge_limit_percent <= 100.0
        assert result.recommended_charge_power_kw <= result.bms_charge_power_limit_kw + 1e-6
        assert 0.0 <= result.recommended_export_limit_percent <= 100.0

    sensor_source = (
        ROOT / "custom_components" / "hoymiles_hit_modbus" / "rcm_sensor.py"
    ).read_text(encoding="utf-8")
    for attribute in (
        "pv_profile_source",
        "pv_profile_confidence_percent",
        "load_profile_mode",
        "risk_window_details",
        "unavoidable_charge_before_risk_kwh",
        "estimated_safe_export_power_kw",
    ):
        assert f'"{attribute}"' in sensor_source

    print("RCEm optimizer: safety, headroom and BMS-limit scenarios passed")


if __name__ == "__main__":
    main()
