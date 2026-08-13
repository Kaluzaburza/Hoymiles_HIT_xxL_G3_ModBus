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
    select_rcm_load_envelopes,
    select_rcm_load_profile,
    select_rcm_pv_profile,
    stateful_natural_headroom_kwh,
    stateful_pre_risk_home_buffer_kwh,
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

    load_envelopes = select_rcm_load_envelopes(
        weekend=False,
        average_profile=tuple(1.0 for _ in range(48)),
        weekday_profile=None,
        weekend_profile=None,
        average_daily_kwh=48.0,
        daily_totals_kwh=(31.0, 35.0, 45.0, 55.0),
    )
    assert load_envelopes.low.selected_total_kwh < 48.0
    assert load_envelopes.high.selected_total_kwh > 48.0
    assert "p10" in load_envelopes.low.source
    assert "p90" in load_envelopes.high.source

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
    detailed_p10 = select_rcm_pv_profile(
        forecast_total_kwh=4.0,
        forecast_p90_total_kwh=6.0,
        forecast_p10_total_kwh=2.0,
        detailed_p50_by_slot={24: 0.8, 25: 2.2},
        detailed_p90_by_slot={24: 1.0, 25: 3.0},
        detailed_p10_by_slot={24: 0.2, 25: 0.8},
        first_slot=24,
        risk_slots=(24, 25),
        scenario="low",
    )
    assert detailed_p10.source == "solcast_30m_p10"
    assert round(sum(detailed_p10.slot_kwh), 3) == 2.0
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

    # The legal-boundary feedback path must outrank missing history and stale
    # prediction. It only relies on fresh voltages and writable actuators.
    emergency_while_learning = optimize_rcm(
        settings(
            history_days=0,
            forecast_data_fresh=False,
            history_data_fresh=False,
            voltage_l1_v=253.4,
            voltage_l2_v=249.0,
            voltage_l3_v=248.0,
        )
    )
    assert emergency_while_learning.status_code == "emergency"
    assert emergency_while_learning.live_emergency
    assert emergency_while_learning.emergency_action_ready
    assert emergency_while_learning.recommended_export_limit_percent == 0.0

    # A missing inverter count/rated-power contract cannot safely convert the
    # shared BMS kW allowance into a percentage. It blocks charge and all
    # predictive discharge, while a fresh export actuator may still perform
    # the independent emergency clamp.
    topology_missing_emergency = optimize_rcm(
        settings(
            voltage_l1_v=253.4,
            voltage_l2_v=249.0,
            voltage_l3_v=248.0,
            system_power_data_valid=False,
            export_control_enabled=True,
        )
    )
    assert topology_missing_emergency.live_emergency
    assert topology_missing_emergency.emergency_action_ready
    assert not topology_missing_emergency.system_power_data_valid
    assert not topology_missing_emergency.bms_charge_available
    assert not topology_missing_emergency.prediction_ready
    assert topology_missing_emergency.prediction_block_reason == "system_power_unavailable"
    assert topology_missing_emergency.recommended_export_limit_percent == 0.0
    topology_missing_no_export_path = optimize_rcm(
        settings(
            voltage_l1_v=253.4,
            voltage_l2_v=249.0,
            voltage_l3_v=248.0,
            system_power_data_valid=False,
            export_control_enabled=False,
        )
    )
    assert not topology_missing_no_export_path.emergency_action_ready

    stale_high_voltage = optimize_rcm(
        settings(
            voltage_data_fresh=False,
            voltage_l1_v=254.0,
            voltage_l2_v=254.0,
            voltage_l3_v=254.0,
        )
    )
    assert not stale_high_voltage.live_emergency
    assert stale_high_voltage.status_code == "stale_voltage"
    assert stale_high_voltage.recommended_charge_limit_percent == 80.0

    # A positively fresh 253 V phase remains an emergency even when another
    # phase is unavailable.  Prediction still requires the coherent all-phase
    # ``voltage_data_fresh`` contract.
    partial_phase_emergency = optimize_rcm(
        settings(
            voltage_data_fresh=False,
            emergency_voltage_data_fresh=True,
            voltage_l1_v=253.4,
            voltage_l2_v=249.0,
            voltage_l3_v=0.0,
        )
    )
    assert partial_phase_emergency.live_emergency
    assert partial_phase_emergency.emergency_action_ready
    assert partial_phase_emergency.emergency_voltage_data_fresh
    assert not partial_phase_emergency.voltage_data_fresh
    partial_phase_not_high = optimize_rcm(
        settings(
            voltage_data_fresh=False,
            emergency_voltage_data_fresh=True,
            voltage_l1_v=252.9,
            voltage_l2_v=249.0,
            voltage_l3_v=0.0,
        )
    )
    assert not partial_phase_not_high.live_emergency

    unavailable_actuator_emergency = optimize_rcm(
        settings(
            actuator_data_fresh=False,
            voltage_l1_v=253.4,
            voltage_l2_v=249.0,
            voltage_l3_v=248.0,
        )
    )
    assert unavailable_actuator_emergency.status_code == "emergency_actuator_unavailable"
    assert not unavailable_actuator_emergency.emergency_action_ready
    assert unavailable_actuator_emergency.recommended_charge_limit_percent == 80.0

    # Isolate the planned battery-discharge contract from coincident PV.  The
    # common export-budget regression below covers PV + BATTERY - LOAD.
    headroom = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11, minute=0),
            pv_power_kw=0.0,
        )
    )
    assert headroom.status_code == "preparing_discharge"
    assert headroom.action == "grid_discharge_preparation"
    assert headroom.recommended_charge_limit_percent == 70.0
    # RCEm owns only Self-Use + its safety margin.  A stale/independent RCE
    # target supplied through the compatibility field must not lift the floor.
    assert headroom.reserve_soc_percent == 27.0
    assert headroom.protected_minimum_soc_percent == 27.0
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

    # 0 A or stale BMS telemetry is an unavailable power path, never an
    # implicit full-system limit. Export limiting remains available at 253 V.
    zero_charge_bms = optimize_rcm(
        settings(
            voltage_l1_v=253.2,
            bms_max_charge_current_a=0.0,
            current_charge_limit_percent=30.0,
        )
    )
    assert not zero_charge_bms.bms_charge_available
    assert zero_charge_bms.recommended_charge_power_kw == 0.0
    assert zero_charge_bms.recommended_charge_limit_percent == 30.0
    assert zero_charge_bms.recommended_export_limit_percent == 0.0
    assert not zero_charge_bms.bms_charge_quantization_limited

    # Register 306 has a 10% minimum.  A positive BMS allowance below that
    # value must disable the charge path rather than round the command up past
    # the physical limit.  Exactly 10% remains representable and usable.
    sub_minimum_charge_bms = optimize_rcm(
        settings(
            system_power_kw=20.0,
            voltage_l1_v=253.2,
            battery_voltage_v=52.0,
            bms_max_charge_current_a=10.0,
            current_charge_limit_percent=30.0,
        )
    )
    assert sub_minimum_charge_bms.bms_charge_power_limit_kw == 0.52
    assert not sub_minimum_charge_bms.bms_charge_available
    assert sub_minimum_charge_bms.bms_charge_quantization_limited
    assert sub_minimum_charge_bms.recommended_charge_power_kw == 0.0
    assert sub_minimum_charge_bms.action == "limit_export"
    assert sub_minimum_charge_bms.emergency_action_ready

    exact_minimum_charge_bms = optimize_rcm(
        settings(
            system_power_kw=20.0,
            voltage_l1_v=253.2,
            battery_voltage_v=52.0,
            bms_max_charge_current_a=2000.0 / 52.0,
            current_charge_limit_percent=30.0,
        )
    )
    assert exact_minimum_charge_bms.bms_charge_power_limit_kw == 2.0
    assert exact_minimum_charge_bms.bms_charge_available
    assert not exact_minimum_charge_bms.bms_charge_quantization_limited
    assert exact_minimum_charge_bms.recommended_charge_limit_percent == 10.0
    assert exact_minimum_charge_bms.recommended_charge_power_kw == 2.0
    no_emergency_path = optimize_rcm(
        settings(
            voltage_l1_v=253.2,
            bms_max_charge_current_a=0.0,
            export_control_enabled=False,
            current_charge_limit_percent=30.0,
        )
    )
    assert not no_emergency_path.emergency_action_ready
    assert no_emergency_path.status_code == "emergency_actuator_unavailable"

    stale_discharge_bms = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11),
            bms_discharge_data_fresh=False,
        )
    )
    assert not stale_discharge_bms.bms_discharge_available
    assert stale_discharge_bms.pre_discharge_power_kw == 0.0
    assert not stale_discharge_bms.pre_discharge_start_eligible

    power_limited_headroom = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11),
            battery_soc_percent=90.0,
            protected_minimum_soc_percent=25.0,
            risk_window_forecasts=(
                RCMRiskWindowInput(
                    12 * 60,
                    14 * 60,
                    253.5,
                    0,
                    10.0,
                    2.0,
                    8.0,
                    0.0,
                    absorbable_surplus_kwh=2.0,
                    protected_home_energy_kwh=1.0,
                    absorption_power_limited=True,
                ),
            ),
            expected_absorbable_risk_surplus_kwh=2.0,
            expected_protected_home_energy_kwh=1.0,
        )
    )
    assert power_limited_headroom.headroom_power_limited
    assert power_limited_headroom.absorbable_risk_surplus_kwh == 2.0
    assert power_limited_headroom.required_headroom_kwh < 3.0
    assert power_limited_headroom.protected_minimum_soc_percent == 27.0
    assert power_limited_headroom.nominal_pre_risk_home_buffer_kwh == 1.0

    stale_prediction_continuation = optimize_rcm(
        settings(
            now=settings().now.replace(hour=11),
            forecast_data_fresh=False,
            pre_discharge_active=True,
        )
    )
    assert not stale_prediction_continuation.prediction_ready
    assert not stale_prediction_continuation.pre_discharge_start_eligible
    assert stale_prediction_continuation.pre_discharge_continue_eligible
    assert stale_prediction_continuation.pre_discharge_deadline is not None

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

    explicit_zero_cap = optimize_rcm(
        settings(
            current_export_limit_percent=80.0,
            saved_export_limit_percent=100.0,
            user_export_cap_percent=0.0,
        )
    )
    assert explicit_zero_cap.effective_export_cap_percent == 0.0
    assert explicit_zero_cap.recommended_export_limit_percent == 0.0

    # With GCF/export regulation disabled, dormant 0% registers must not
    # suppress predictive Grid Discharge.  The physical system power and the
    # explicit user cap remain the only export envelope; no export-limit write
    # itself is requested.
    gcf_disabled_dormant_zero = optimize_rcm(
        settings(
            export_control_enabled=False,
            gcf_active=False,
            current_export_limit_percent=0.0,
            saved_export_limit_percent=0.0,
            user_export_cap_percent=60.0,
            pv_power_kw=2.0,
            load_power_kw=1.0,
            battery_soc_percent=100.0,
            expected_risk_surplus_kwh=8.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=180,
        )
    )
    assert gcf_disabled_dormant_zero.effective_export_cap_percent == 60.0
    assert gcf_disabled_dormant_zero.recommended_export_limit_percent == 0.0
    assert gcf_disabled_dormant_zero.pre_discharge_power_kw > 0.1
    assert gcf_disabled_dormant_zero.pre_discharge_start_eligible
    assert gcf_disabled_dormant_zero.pre_discharge_transaction_ready

    # A real user/DSO cap still constrains predictive export without GCF
    # regulation; 20% of a 10 kW system is a 2 kW grid-export envelope.
    gcf_disabled_user_cap = optimize_rcm(
        settings(
            export_control_enabled=False,
            gcf_active=False,
            current_export_limit_percent=0.0,
            saved_export_limit_percent=0.0,
            user_export_cap_percent=20.0,
            battery_soc_percent=100.0,
            expected_risk_surplus_kwh=8.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=180,
        )
    )
    assert gcf_disabled_user_cap.effective_export_cap_percent == 20.0
    assert gcf_disabled_user_cap.pre_discharge_power_kw <= 4.0 + 1e-6

    # Regression: a dormant GCF register cannot hide the common AC budget.
    # On a 10 kW system with a 50% user cap, PV=5 kW and LOAD=1 kW already
    # export 4 kW.  That is above this plan's desired GRID export, so no
    # battery discharge is authorized; the old LOAD-only formula requested
    # about 1.99 kW and would have produced about 5.99 kW at GRID.
    gcf_disabled_common_export_budget = optimize_rcm(
        settings(
            export_control_enabled=False,
            gcf_active=False,
            current_export_limit_percent=0.0,
            saved_export_limit_percent=0.0,
            user_export_cap_percent=50.0,
            pv_power_kw=5.0,
            load_power_kw=1.0,
            expected_risk_surplus_kwh=8.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=117,
        )
    )
    assert gcf_disabled_common_export_budget.effective_export_cap_percent == 50.0
    old_load_only_power_kw = max(
        gcf_disabled_common_export_budget.planned_grid_discharge_kwh
        / ((117 - 30) / 60.0)
        * 1.10,
        0.5,
    ) + 1.0
    assert round(old_load_only_power_kw, 2) == 1.99
    assert 5.0 + old_load_only_power_kw - 1.0 > 5.0
    assert gcf_disabled_common_export_budget.pre_discharge_power_kw == 0.0
    projected_common_export_kw = (
        5.0
        + gcf_disabled_common_export_budget.pre_discharge_power_kw
        - 1.0
    )
    # Physical GCF provenance is independent of permission to change it. With
    # GCF enabled the live register is a hard cap even when RCEm export writes
    # are disabled; when RCEm owns export control, current/saved/user are all
    # respected. The helper must not resurrect a dormant 0% register.
    gcf_enabled_helper_off = optimize_rcm(
        settings(
            export_control_enabled=False,
            gcf_active=True,
            current_export_limit_percent=20.0,
            saved_export_limit_percent=80.0,
            user_export_cap_percent=100.0,
        )
    )
    assert gcf_enabled_helper_off.effective_export_cap_percent == 20.0
    gcf_enabled_owned = optimize_rcm(
        settings(
            export_control_enabled=True,
            gcf_active=True,
            current_export_limit_percent=20.0,
            saved_export_limit_percent=80.0,
            user_export_cap_percent=100.0,
        )
    )
    assert gcf_enabled_owned.effective_export_cap_percent == 20.0
    gcf_disabled_helper_on = optimize_rcm(
        settings(
            export_control_enabled=True,
            gcf_active=False,
            current_export_limit_percent=0.0,
            saved_export_limit_percent=0.0,
            user_export_cap_percent=100.0,
        )
    )
    assert gcf_disabled_helper_on.effective_export_cap_percent == 100.0

    sensor_source = (
        ROOT
        / "custom_components"
        / "hoymiles_hit_modbus"
        / "rcm_sensor.py"
    ).read_text(encoding="utf-8")
    for marker in (
        '"sensor.hoymiles_hit_gcf_enable_readback_code"',
        '"sensor.hoymiles_hit_gcf_maximum_export_power_readback"',
        '"sensor.hoymiles_hit_ems_mode_readback_code"',
        "gcf_state_fresh",
        "gcf_active=gcf_active",
        "gcf_data_fresh=gcf_data_fresh",
        '"export_control_path_enabled"',
    ):
        assert marker in sensor_source, f"RCEm GCF provenance lacks {marker}"
    assert projected_common_export_kw <= 5.0 + 1e-6
    assert not gcf_disabled_common_export_budget.pre_discharge_start_eligible
    assert not gcf_disabled_common_export_budget.pre_discharge_transaction_ready

    # Capacity 0 is authoritative telemetry.  It must not be promoted to a
    # synthetic battery, divide by zero, or authorize a new/continued
    # predictive transaction.
    zero_capacity = optimize_rcm(
        settings(
            battery_capacity_kwh=0.0,
            battery_soc_percent=100.0,
            expected_risk_surplus_kwh=8.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=180,
            pre_discharge_active=True,
        )
    )
    assert not zero_capacity.prediction_ready
    assert zero_capacity.prediction_block_reason == "battery_capacity_unavailable"
    assert zero_capacity.planned_grid_discharge_kwh == 0.0
    assert zero_capacity.pre_discharge_power_kw == 0.0
    assert not zero_capacity.pre_discharge_start_eligible
    assert not zero_capacity.pre_discharge_continue_eligible
    assert not zero_capacity.pre_discharge_transaction_ready

    stale_gcf_active_cycle = optimize_rcm(
        settings(
            pre_discharge_active=True,
            gcf_active=True,
            gcf_data_fresh=False,
            battery_soc_percent=100.0,
            expected_risk_surplus_kwh=8.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=180,
        )
    )
    assert not stale_gcf_active_cycle.prediction_ready
    assert stale_gcf_active_cycle.prediction_block_reason == "gcf_state_stale"
    assert not stale_gcf_active_cycle.pre_discharge_continue_eligible

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
    assert protected_floor.pre_discharge_target_soc_percent == 27.0
    assert protected_floor.planned_grid_discharge_kwh == 15.33
    assert protected_floor.unabsorbed_surplus_due_floor_kwh > 0.0

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

    # Chronology matters for the transient home buffer: earlier PV can fund a
    # later load, but later PV cannot retroactively fund an earlier load.
    assert stateful_pre_risk_home_buffer_kwh(
        (5.0, 0.0),
        (0.0, 5.0),
        charge_efficiency=1.0,
    ) == 0.0
    assert stateful_pre_risk_home_buffer_kwh(
        (0.0, 5.0),
        (5.0, 0.0),
        charge_efficiency=1.0,
    ) == 5.0
    assert stateful_pre_risk_home_buffer_kwh(
        (0.0,),
        (5.0,),
        charge_efficiency=1.0,
        house_discharge_efficiency=0.80,
    ) == 6.25

    # Natural headroom has the inverse chronology at a full battery: a later
    # PV surplus can refill room created by earlier LOAD, while PV that arrived
    # before the LOAD was spilled and cannot erase the later headroom.
    assert stateful_natural_headroom_kwh(
        (0.0, 5.0),
        (5.0, 0.0),
        initial_headroom_kwh=0.0,
        maximum_headroom_kwh=100.0,
    ) == 0.0
    assert stateful_natural_headroom_kwh(
        (5.0, 0.0),
        (0.0, 5.0),
        initial_headroom_kwh=0.0,
        maximum_headroom_kwh=100.0,
    ) == 5.0
    assert stateful_natural_headroom_kwh(
        (0.0, 5.0),
        (5.0, 0.0),
        initial_headroom_kwh=0.0,
        maximum_headroom_kwh=100.0,
        charge_input_limits_kwh=(0.0, 2.0),
    ) == 3.0

    # Meter-scale regression: the former tariff-like P10/P90 branch raised a
    # 31% hard floor to 43%.  RCEm now retains 31%; the stress energy remains
    # visible and can only cancel a discharge in an energy-critical state.
    meter_floor = optimize_rcm(
        settings(
            now=settings().now.replace(hour=8),
            battery_capacity_kwh=230.0,
            battery_soc_percent=58.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            protected_minimum_soc_percent=43.0,
            expected_protected_home_energy_kwh=12.0,
            expected_stress_home_energy_kwh=27.6,
            expected_risk_surplus_kwh=80.0,
            expected_absorbable_risk_surplus_kwh=80.0,
            expected_natural_headroom_kwh=0.0,
            minutes_to_risk=240,
        )
    )
    assert meter_floor.protected_minimum_soc_percent == 31.0
    assert meter_floor.reserve_soc_percent == 31.0
    assert meter_floor.nominal_pre_risk_home_buffer_kwh == 12.0
    assert meter_floor.stress_protected_home_energy_kwh == 27.6
    assert not meter_floor.stress_reserve_energy_critical

    post_plan_stress_guard = optimize_rcm(
        settings(
            now=settings().now.replace(hour=8),
            battery_capacity_kwh=100.0,
            battery_soc_percent=60.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            expected_risk_surplus_kwh=80.0,
            expected_absorbable_risk_surplus_kwh=80.0,
            expected_natural_headroom_kwh=0.0,
            expected_stress_home_energy_kwh=27.6,
            pv_power_kw=0.0,
            minutes_to_risk=240,
        )
    )
    assert not post_plan_stress_guard.stress_reserve_energy_critical
    assert post_plan_stress_guard.stress_discharge_limited
    assert post_plan_stress_guard.planned_grid_discharge_kwh == 1.4
    assert post_plan_stress_guard.pre_discharge_target_soc_percent == 58.6
    assert post_plan_stress_guard.pre_discharge_start_eligible

    low_pv_branch = optimize_rcm(
        settings(
            battery_capacity_kwh=100.0,
            battery_soc_percent=80.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            protected_minimum_soc_percent=43.0,
            expected_risk_surplus_kwh=10.0,
            expected_absorbable_risk_surplus_kwh=10.0,
            expected_natural_headroom_kwh=0.0,
        )
    )
    high_pv_branch = optimize_rcm(
        settings(
            battery_capacity_kwh=100.0,
            battery_soc_percent=80.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            protected_minimum_soc_percent=43.0,
            expected_risk_surplus_kwh=80.0,
            expected_absorbable_risk_surplus_kwh=80.0,
            expected_natural_headroom_kwh=0.0,
        )
    )
    assert high_pv_branch.required_headroom_kwh > low_pv_branch.required_headroom_kwh
    assert high_pv_branch.protected_minimum_soc_percent == 31.0
    assert low_pv_branch.protected_minimum_soc_percent == 31.0

    critical_low_soc = optimize_rcm(
        settings(
            now=settings().now.replace(hour=8),
            battery_capacity_kwh=100.0,
            battery_soc_percent=35.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            expected_risk_surplus_kwh=80.0,
            expected_absorbable_risk_surplus_kwh=80.0,
            expected_natural_headroom_kwh=0.0,
            expected_stress_home_energy_kwh=10.0,
            minutes_to_risk=240,
        )
    )
    assert critical_low_soc.stress_reserve_energy_critical
    assert critical_low_soc.planned_grid_discharge_kwh == 0.0
    assert not critical_low_soc.pre_discharge_start_eligible
    assert critical_low_soc.action == "restore"

    stale_brokered_load = optimize_rcm(
        settings(load_profile_data_fresh=False)
    )
    assert not stale_brokered_load.prediction_ready
    assert stale_brokered_load.prediction_block_reason == "load_profile_stale"

    capacity_limited = optimize_rcm(
        settings(
            battery_capacity_kwh=100.0,
            battery_soc_percent=80.0,
            reserve_soc_percent=29.0,
            safety_margin_soc_percent=2.0,
            expected_risk_surplus_kwh=80.0,
            expected_absorbable_risk_surplus_kwh=80.0,
            expected_natural_headroom_kwh=0.0,
        )
    )
    assert capacity_limited.required_headroom_kwh == (
        capacity_limited.unconstrained_required_headroom_kwh
    )
    assert capacity_limited.headroom_capacity_limited
    assert capacity_limited.unabsorbed_surplus_due_floor_kwh > 0.0
    assert capacity_limited.creatable_headroom_kwh < (
        capacity_limited.unconstrained_required_headroom_kwh
    )

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
        explicit_zero_cap,
        gcf_disabled_dormant_zero,
        gcf_disabled_user_cap,
        gcf_disabled_common_export_budget,
        zero_capacity,
        night,
    ):
        assert 10.0 <= result.recommended_charge_limit_percent <= 100.0
        assert result.recommended_charge_power_kw <= result.bms_charge_power_limit_kw + 1e-6
        assert 0.0 <= result.recommended_export_limit_percent <= 100.0

    sensor_source = (
        ROOT / "custom_components" / "hoymiles_hit_modbus" / "rcm_sensor.py"
    ).read_text(encoding="utf-8")
    assert 'rce_plan.attributes.get("minimum_soc")' not in sensor_source
    assert 'rce.attributes.get("forecast_remaining_today_kwh")' not in sensor_source
    assert 'rce.attributes.get("forecast_tomorrow_kwh")' not in sensor_source
    assert '_forecast_total(forecast_state, "p50")' in sensor_source
    assert '_forecast_total(forecast_state, "p10")' in sensor_source
    assert '_forecast_total(forecast_state, "p90")' in sensor_source
    assert "if user_export_cap is None" in sensor_source
    assert "user_export_cap or 100.0" not in sensor_source
    assert "battery_capacity or 1.0" not in sensor_source
    assert '"sensor.hoymiles_hit_ems_self_use_soc_readback"' in sensor_source
    assert '"number.hoymiles_hit_self_use_soc"' not in sensor_source
    assert (
        '"sensor.hoymiles_hit_ems_self_use_soc_readback",\n'
        "                now=now,\n"
        "                max_age_seconds=ACTUATOR_MAX_AGE_SECONDS,\n"
        "                minimum=10.0,\n"
        "                maximum=100.0,"
        in sensor_source
    )
    assert (
        '"sensor.hoymiles_hit_overview_battery_soc",\n'
        "                now=now,\n"
        "                max_age_seconds=SLOW_TELEMETRY_MAX_AGE_SECONDS,\n"
        "                minimum=0.0,\n"
        "                maximum=100.0,"
        in sensor_source
    )
    assert (
        'required["sensor.hoymiles_hit_overview_battery_soc"] = (\n'
        "                battery_soc_value if battery_soc_fresh else None"
        in sensor_source
    )
    assert (
        'freshness["sensor.hoymiles_hit_overview_battery_soc"]'
        in sensor_source
    )
    assert (
        '"sensor.hoymiles_hit_battery_max_charge_power_readback",\n'
        "                ACTUATOR_MAX_AGE_SECONDS,\n"
        "                minimum=10.0,\n"
        "                maximum=100.0,"
        in sensor_source
    )
    assert "current_charge_limit or 10.0" not in sensor_source
    assert (
        'freshness[\n                "sensor.hoymiles_hit_ems_self_use_soc_readback"'
        in sensor_source
    )
    register_freshness_block = sensor_source.split(
        "discharge_registers_data_fresh = bool(", 1
    )[1].split(")", 1)[0]
    assert "max_discharge_fresh" in register_freshness_block
    assert "force_discharge_fresh" in register_freshness_block
    assert "ems_mode_fresh" in register_freshness_block
    assert "battery_soc_fresh" not in register_freshness_block
    assert "battery_capacity_fresh" not in register_freshness_block
    pre_discharge_freshness_block = sensor_source.split(
        "pre_discharge_actuator_fresh = bool(", 1
    )[1].split(")", 1)[0]
    assert "discharge_registers_data_fresh" in pre_discharge_freshness_block
    assert "battery_soc_fresh" in pre_discharge_freshness_block
    assert "battery_capacity_fresh" in pre_discharge_freshness_block
    forecast_fresh_block = sensor_source.split(
        "forecast_data_fresh = bool(", 1
    )[1].split(")", 1)[0]
    assert "source_forecast_fresh" in forecast_fresh_block
    assert "rce_plan_fresh" not in forecast_fresh_block
    for attribute in (
        "pv_profile_source",
        "pv_profile_confidence_percent",
        "load_profile_mode",
        "risk_window_details",
        "unavoidable_charge_before_risk_kwh",
        "estimated_safe_export_power_kw",
        "live_emergency",
        "emergency_action_ready",
        "prediction_ready",
        "prediction_block_reason",
        "system_power_data_valid",
        "voltage_data_fresh",
        "emergency_voltage_data_fresh",
        "actuator_data_fresh",
        "forecast_data_fresh",
        "load_profile_data_fresh",
        "history_data_fresh",
        "live_power_data_fresh",
        "bms_charge_available",
        "bms_charge_quantization_limited",
        "bms_discharge_available",
        "headroom_power_limited",
        "headroom_capacity_limited",
        "unconstrained_required_headroom_kwh",
        "creatable_headroom_kwh",
        "unabsorbed_surplus_due_floor_kwh",
        "nominal_pre_risk_home_buffer_kwh",
        "stress_protected_home_energy_kwh",
        "stress_reserve_energy_critical",
        "stress_discharge_limited",
        "load_profile_broker_entity_id",
        "load_profile_broker_fresh",
        "load_profile_broker_age_seconds",
        "pre_discharge_start_eligible",
        "pre_discharge_continue_eligible",
        "pre_discharge_transaction_ready",
        "pre_discharge_deadline",
        "discharge_registers_data_fresh",
        "data_freshness",
        "data_age_seconds",
    ):
        assert f'"{attribute}"' in sensor_source

    print("RCEm optimizer: safety, headroom and BMS-limit scenarios passed")


if __name__ == "__main__":
    main()
