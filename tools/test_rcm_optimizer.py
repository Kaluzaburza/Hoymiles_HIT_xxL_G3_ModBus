"""Deterministic safety tests for the RCEm voltage controller."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from rcm_optimizer import RCMOptimizerInput, optimize_rcm  # noqa: E402


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
    ):
        assert 10.0 <= result.recommended_charge_limit_percent <= 100.0
        assert result.recommended_charge_power_kw <= result.bms_charge_power_limit_kw + 1e-6
        assert 0.0 <= result.recommended_export_limit_percent <= 100.0

    print("RCEm optimizer: safety, headroom and BMS-limit scenarios passed")


if __name__ == "__main__":
    main()
