"""Behavioral tests for the offline batch diagnostics analyzer."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
from time import perf_counter
from typing import Callable
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_diagnostic_bundles import main as cli_main  # noqa: E402
import diagnostics_analysis.archive as archive_module  # noqa: E402
from diagnostics_analysis.analyzer import analyze_inputs  # noqa: E402
from diagnostics_analysis.archive import (  # noqa: E402
    DEFAULT_LIMITS,
    ArchiveReadError,
    discover_archives,
    load_diagnostic_archive,
)
from diagnostics_analysis.history import analyze_control_history  # noqa: E402
from diagnostics_analysis.outputs import (  # noqa: E402
    build_output_payloads,
    write_analysis_outputs,
)


FIXED_ZIP_TIME = (2026, 8, 15, 12, 0, 0)
INSTALLATION_A = "3f6f8b4e-7793-4f4b-9f45-486ddf65f78a"
INSTALLATION_B = "8c87c546-1c27-4c09-9627-d1666ab00c96"
BASE_TIME = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _state(
    value,
    attributes: dict | None,
    timestamp: datetime,
) -> dict:
    return {
        "state": value,
        "attributes": attributes or {},
        "last_changed": timestamp.isoformat(),
        "last_updated": timestamp.isoformat(),
    }


def _healthy_snapshot(timestamp: datetime) -> dict[str, dict]:
    return {
        "sensor.hoymiles_hit_rce_optimized_plan": _state(
            "ready",
            {
                "status_code": "ready",
                "result_current": True,
                "recalculation_pending": False,
                "automatic_discharge_enabled": False,
                "current_slot_planned": False,
                "planned_export_kwh": 20.0,
                "planned_revenue_pln": 12.5,
                "requested_export_power_kw": 40.0,
                "bms_discharge_power_limit_kw": 40.96,
                "bms_discharge_data_fresh": True,
                "bms_discharge_data_available": True,
                "maximum_export_power_kw": 40.0,
                "physical_limit_source": "requested_power",
                "base_reserve_energy_kwh": 50.0,
                "ending_battery_kwh": 61.0,
                "ending_battery_soc": 26.5,
                "data_quality_score": 98.0,
                "data_quality_issues": [],
                "forecast_day3_data_fresh": True,
            },
            timestamp,
        ),
        "sensor.hoymiles_hit_rcm_voltage_plan": _state(
            "ready",
            {
                "status_code": "ready",
                "result_current": True,
                "recalculation_pending": False,
                "enabled": True,
                "shadow_mode": True,
                "action": "none",
                "maximum_voltage_v": 250.1,
                "history_days": 4,
                "history_samples": 384,
                "history_data_fresh": True,
                "headroom_shortfall_kwh": 0.0,
                "live_emergency": False,
                "emergency_action_ready": True,
                "prediction_ready": True,
                "prediction_block_reason": "ready",
                "battery_capacity_data_available": True,
                "actuator_data_fresh": True,
                "voltage_data_fresh": True,
                "bms_charge_data_fresh": True,
                "pre_discharge_ready": False,
            },
            timestamp,
        ),
        "sensor.hoymiles_hit_tariff_charge_plan": _state(
            "no_charge_needed",
            {
                "status_code": "no_charge_needed",
                "result_current": True,
                "recalculation_pending": False,
                "automatic_charge_enabled": False,
                "current_slot_planned": False,
                "current_action": "none",
                "control_inputs_fresh": True,
                "control_input_block_reason": "none",
                "planned_grid_import_kwh": 0.0,
                "planned_stored_energy_kwh": 0.0,
                "planned_direct_load_kwh": 0.0,
                "planned_cost_pln": 0.0,
                "target_soc_percent": 70.0,
                "model_input_maximum_soc_percent": 90.0,
                "planning_horizon_hours": 72.0,
                "forecast_day_3_data_fresh": True,
            },
            timestamp,
        ),
        "sensor.hoymiles_ems_control_owner": _state(
            "Sterowanie ręczne",
            {"owner_code": "manual"},
            timestamp,
        ),
        "binary_sensor.hoymiles_ems_control_conflict": _state(
            "off", {}, timestamp
        ),
        "binary_sensor.hoymiles_ems_execution_ready": _state(
            "on", {"reason": "ready"}, timestamp
        ),
        "binary_sensor.hoymiles_sale_block_active": _state("off", {}, timestamp),
        "input_boolean.hoymiles_rce_discharge_active": _state(
            "off", {}, timestamp
        ),
        "input_boolean.hoymiles_tariff_charge_active": _state(
            "off", {}, timestamp
        ),
        "input_boolean.hoymiles_rcm_active": _state("off", {}, timestamp),
        "input_boolean.hoymiles_rcm_export_control_active": _state(
            "off", {}, timestamp
        ),
        "input_boolean.hoymiles_rcm_pre_discharge_active": _state(
            "off", {}, timestamp
        ),
        "sensor.hoymiles_hit_battery_max_charge_power_readback": _state(
            "100", {}, timestamp
        ),
    }


def _history(timestamp: datetime) -> dict:
    event_time = (timestamp - timedelta(minutes=5)).isoformat()
    return {
        "available": True,
        "hours": 24,
        "start": (timestamp - timedelta(hours=24)).isoformat(),
        "end": timestamp.isoformat(),
        "entities": {
            "input_boolean.hoymiles_rce_discharge_active": [
                {
                    "state": "off",
                    "last_changed": event_time,
                    "last_updated": event_time,
                }
            ],
            "input_boolean.hoymiles_tariff_charge_active": [
                {
                    "state": "off",
                    "last_changed": event_time,
                    "last_updated": event_time,
                }
            ],
        },
        "truncated_entities": [],
    }


def _catalog() -> list[dict]:
    return [
        {
            "translation_key": "rce_optimized_plan",
            "proxy_entity_id": "sensor.hoymiles_hit_rce_optimized_plan",
        },
        {
            "translation_key": "rcm_voltage_plan",
            "proxy_entity_id": "sensor.hoymiles_hit_rcm_voltage_plan",
        },
        {
            "translation_key": "tariff_charge_plan",
            "proxy_entity_id": "sensor.hoymiles_hit_tariff_charge_plan",
        },
    ]


def _reports(
    installation_id: str,
    timestamp: datetime,
    *,
    mutation: str | None = None,
    divergent_second_entry: bool = False,
) -> list[dict]:
    snapshot = _healthy_snapshot(timestamp)
    if mutation == "rce_bms_zero":
        attributes = snapshot[
            "sensor.hoymiles_hit_rce_optimized_plan"
        ]["attributes"]
        attributes["maximum_export_power_kw"] = 0.0
        attributes["physical_limit_source"] = "bms"
    elif mutation == "rcem_emergency":
        attributes = snapshot[
            "sensor.hoymiles_hit_rcm_voltage_plan"
        ]["attributes"]
        attributes["maximum_voltage_v"] = 253.8
        attributes["live_emergency"] = True
        attributes["emergency_action_ready"] = False
    elif mutation == "tariff_active_stale":
        tariff = snapshot[
            "sensor.hoymiles_hit_tariff_charge_plan"
        ]["attributes"]
        tariff.update(
            {
                "status_code": "ready",
                "automatic_charge_enabled": True,
                "current_slot_planned": True,
                "current_action": "battery_charge",
                "current_zone": "low",
                "control_inputs_fresh": False,
                "control_input_block_reason": "bms_charge_data_stale",
            }
        )
        snapshot["input_boolean.hoymiles_tariff_charge_active"]["state"] = "on"
        snapshot["sensor.hoymiles_ems_control_owner"]["attributes"][
            "owner_code"
        ] = "tariff"

    def report(index: int, report_snapshot: dict) -> dict:
        return {
            "report_schema": 1,
            "anonymous_installation_id": installation_id,
            "installation_id_schema_version": 1,
            "generated_at": timestamp.isoformat(),
            "integration_version": "1.5.4",
            "config_entry": {"title": f"entry-{index}"},
            "catalog_coverage": {
                "runtime_loaded": True,
                "missing_count": 0,
                "missing_translation_keys": [],
            },
            "catalog_entities": _catalog(),
            "managed_state_snapshot": report_snapshot,
            "control_history": _history(timestamp),
        }

    second_snapshot = json.loads(json.dumps(snapshot))
    if divergent_second_entry:
        second_snapshot["sensor.hoymiles_hit_rce_optimized_plan"]["attributes"][
            "planned_export_kwh"
        ] = 99.0
        newer = timestamp + timedelta(seconds=2)
        second_snapshot["sensor.hoymiles_hit_rce_optimized_plan"][
            "last_updated"
        ] = newer.isoformat()
    return [report(1, snapshot), report(2, second_snapshot)]


def _write_member(archive: ZipFile, name: str, payload: str) -> None:
    info = ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, payload.encode("utf-8"))


def build_bundle(
    path: Path,
    installation_id: str,
    timestamp: datetime,
    *,
    mutation: str | None = None,
    divergent_second_entry: bool = False,
    identity_mismatch: bool = False,
    suspicious_member: bool = False,
    snapshot_mutator: Callable[[dict[str, dict]], None] | None = None,
    report_mutator: Callable[[list[dict]], None] | None = None,
    relevant_logs: str | None = None,
) -> None:
    reports = _reports(
        installation_id,
        timestamp,
        mutation=mutation,
        divergent_second_entry=divergent_second_entry,
    )
    if snapshot_mutator is not None:
        for report in reports:
            snapshot_mutator(report["managed_state_snapshot"])
    if report_mutator is not None:
        report_mutator(reports)
    environment_id = INSTALLATION_B if identity_mismatch else installation_id
    environment = {
        "generated_at": timestamp.isoformat(),
        "home_assistant_version": "2026.8.0",
        "report_count": len(reports),
        "anonymous_installation_id": environment_id,
        "installation_id_schema_version": 1,
    }
    with ZipFile(path, "w") as archive:
        _write_member(
            archive,
            "environment.json",
            json.dumps(environment, sort_keys=True),
        )
        _write_member(
            archive,
            "hoymiles_diagnostics.json",
            json.dumps(reports, sort_keys=True),
        )
        _write_member(
            archive,
            "home_assistant_relevant_logs.txt",
            relevant_logs
            if relevant_logs is not None
            else "No relevant Hoymiles-related Core log lines were found.\n",
        )
        if suspicious_member:
            _write_member(archive, "../escape.txt", "must never be extracted")


def _rule_ids(summary: dict) -> set[str]:
    return {str(item["rule_id"]) for item in summary["findings"]}


def _analyze_adversarial_case(
    root: Path,
    name: str,
    generated_at: datetime,
    *,
    snapshot_mutator: Callable[[dict[str, dict]], None] | None = None,
    report_mutator: Callable[[list[dict]], None] | None = None,
    relevant_logs: str | None = None,
) -> dict:
    path = root / f"adversarial-{name}.zip"
    build_bundle(
        path,
        INSTALLATION_A,
        BASE_TIME,
        snapshot_mutator=snapshot_mutator,
        report_mutator=report_mutator,
        relevant_logs=relevant_logs,
    )
    return analyze_inputs([path], generated_at=generated_at)


def _planner_attributes(snapshot: dict[str, dict], entity_id: str) -> dict:
    return snapshot[entity_id]["attributes"]


def _finding(summary: dict, rule_id: str) -> dict | None:
    return next(
        (
            item
            for item in summary["findings"]
            if str(item.get("rule_id")) == rule_id
        ),
        None,
    )


def _run_adversarial_regressions(
    root: Path,
    generated_at: datetime,
) -> None:
    """Lock safety semantics and evidence honesty against nearby snapshots."""

    # 1. An unavailable/stale BMS input is intentionally reduced to a 0 kW
    # fail-closed physical limit by the live RCE contract.
    def stale_bms_fail_closed(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
        )
        attributes.update(
            {
                "bms_discharge_power_limit_kw": 0.0,
                "bms_discharge_data_fresh": False,
                "bms_discharge_data_available": False,
                "maximum_export_power_kw": 0.0,
                "physical_limit_source": "bms",
            }
        )

    stale_bms = _analyze_adversarial_case(
        root,
        "stale-bms-fail-closed",
        generated_at,
        snapshot_mutator=stale_bms_fail_closed,
    )
    require(
        "RCE_PHYSICAL_LIMIT_INCONSISTENT" not in _rule_ids(stale_bms),
        "A valid stale/unavailable-BMS 0 kW fail-closed limit was rejected",
    )

    # 2. home_energy_shortage means the hard floor was already infeasible
    # without export.  A zero-export fallback point is not a reserve violation.
    def home_energy_shortage(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
        )
        attributes.update(
            {
                "status_code": "home_energy_shortage",
                "current_slot_planned": False,
                "planned_export_kwh": 0.0,
                "ending_battery_kwh": 10.0,
                "base_reserve_energy_kwh": 50.0,
            }
        )

    shortage = _analyze_adversarial_case(
        root,
        "home-energy-shortage",
        generated_at,
        snapshot_mutator=home_energy_shortage,
    )
    require(
        "RCE_BASE_RESERVE_VIOLATION" not in _rule_ids(shortage),
        "A safe zero-export home_energy_shortage fallback was called a violation",
    )

    # 3a. RCEm writes are unsafe when its dedicated direct-register gate is off.
    def rcem_direct_gate_off(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "shadow_mode": False,
                "action": "absorb_pv",
                "recommended_charge_limit_percent": 50.0,
            }
        )
        snapshot["input_boolean.hoymiles_rcm_active"] = _state(
            "on", {}, BASE_TIME - timedelta(minutes=5)
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCEm", {"owner_code": "rcm"}, BASE_TIME
        )
        snapshot["binary_sensor.hoymiles_direct_register_execution_ready"] = (
            _state("off", {"reason": "register_readback_stale"}, BASE_TIME)
        )

    direct_gate = _analyze_adversarial_case(
        root,
        "rcem-direct-gate-off",
        generated_at,
        snapshot_mutator=rcem_direct_gate_off,
    )
    require(
        "RCEM_DIRECT_REGISTER_GATE_BLOCKED" in _rule_ids(direct_gate),
        "Active non-shadow RCEm ignored the dedicated direct-register gate",
    )

    # 3b. The readback entity must be retained in context, but only compared
    # after an explicit five-minute grace period and for the absorb_pv branch.
    def rcem_charge_readback_mismatch(snapshot: dict[str, dict]) -> None:
        rcem_direct_gate_off(snapshot)
        snapshot["binary_sensor.hoymiles_direct_register_execution_ready"] = (
            _state("on", {"reason": "ready"}, BASE_TIME)
        )
        snapshot[
            "sensor.hoymiles_hit_battery_max_charge_power_readback"
        ] = _state("100", {}, BASE_TIME)

    readback_mismatch = _analyze_adversarial_case(
        root,
        "rcem-charge-readback-mismatch",
        generated_at,
        snapshot_mutator=rcem_charge_readback_mismatch,
    )
    require(
        "RCEM_RECOMMENDATION_READBACK_MISMATCH"
        in _rule_ids(readback_mismatch),
        "Mature absorb_pv target/readback mismatch was not evaluated",
    )

    # 4. Pre-discharge has a different continuation/freshness contract from
    # charge limiting and must not reuse only the generic BMS-charge checks.
    def rcem_pre_discharge_blocked(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "shadow_mode": False,
                "action": "grid_discharge_preparation",
                "pre_discharge_ready": True,
                "pre_discharge_transaction_ready": True,
                "pre_discharge_continue_eligible": False,
                "pre_discharge_actuator_data_fresh": False,
                "discharge_registers_data_fresh": False,
                "ems_mode_data_fresh": False,
                "bms_discharge_data_fresh": False,
            }
        )
        snapshot["input_boolean.hoymiles_rcm_pre_discharge_active"] = _state(
            "on", {}, BASE_TIME - timedelta(minutes=5)
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCEm", {"owner_code": "rcm"}, BASE_TIME
        )

    pre_discharge = _analyze_adversarial_case(
        root,
        "rcem-pre-discharge-continuation",
        generated_at,
        snapshot_mutator=rcem_pre_discharge_blocked,
    )
    require(
        "RCEM_PRE_DISCHARGE_CONTINUATION_BLOCKED"
        in _rule_ids(pre_discharge),
        "Active RCEm pre-discharge ignored its continuation/freshness contract",
    )

    # 5a. A selected RCE slot may remain planned after the live continuation
    # gate has closed; active execution must still be diagnosed.
    def rce_continuation_blocked(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
        )
        attributes.update(
            {
                "automatic_discharge_enabled": True,
                "current_slot_planned": True,
                "current_slot_continue_eligible": False,
                "current_slot_continue_reason": "execution_power_unavailable",
            }
        )
        snapshot["input_boolean.hoymiles_rce_discharge_active"] = _state(
            "on", {}, BASE_TIME - timedelta(minutes=5)
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCE", {"owner_code": "rce"}, BASE_TIME
        )

    rce_blocked = _analyze_adversarial_case(
        root,
        "rce-continuation-blocked",
        generated_at,
        snapshot_mutator=rce_continuation_blocked,
    )
    require(
        "RCE_CONTINUATION_BLOCKED_WHILE_ACTIVE" in _rule_ids(rce_blocked),
        "Active RCE ignored current_slot_continue_eligible=false",
    )

    # 5b. Tariff execution uses the analogous current-run continuation gate.
    def tariff_continuation_blocked(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_tariff_charge_plan"
        )
        attributes.update(
            {
                "status_code": "ready",
                "automatic_charge_enabled": True,
                "current_slot_planned": True,
                "current_action": "battery_charge",
                "current_zone": "low",
                "control_inputs_fresh": True,
                "current_run_continue_eligible": False,
                "current_run_continue_reason": "pv_covers_load",
            }
        )
        snapshot["input_boolean.hoymiles_tariff_charge_active"] = _state(
            "on", {}, BASE_TIME - timedelta(minutes=5)
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "Tariff", {"owner_code": "tariff"}, BASE_TIME
        )

    tariff_blocked = _analyze_adversarial_case(
        root,
        "tariff-continuation-blocked",
        generated_at,
        snapshot_mutator=tariff_continuation_blocked,
    )
    require(
        "TARIFF_CONTINUATION_BLOCKED_WHILE_ACTIVE"
        in _rule_ids(tariff_blocked),
        "Active tariff run ignored current_run_continue_eligible=false",
    )

    # 6. These are the production attribute names emitted by tariff_sensor.py.
    def tariff_feedback(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_tariff_charge_plan"
        )
        attributes.update(
            {
                "charge_power_feedback_applied_factor": 0.5,
                "effective_charge_power_feedback_samples": 8,
                "charge_power_feedback_ready": True,
            }
        )

    feedback = _analyze_adversarial_case(
        root,
        "tariff-production-feedback-fields",
        generated_at,
        snapshot_mutator=tariff_feedback,
    )
    require(
        "TARIFF_DELIVERY_UNDERPERFORMING" in _rule_ids(feedback),
        "Production tariff feedback field names were not analyzed",
    )

    # 7. INFO statements that explicitly negate a defect are not evidence of
    # an exception, communication error or rollback failure.
    negated_logs = _analyze_adversarial_case(
        root,
        "negated-info-logs",
        generated_at,
        relevant_logs=(
            "INFO Rollback completed without error\n"
            "INFO Modbus error count is 0\n"
            "INFO optimizer_error is not present\n"
        ),
    )
    require(
        not any(rule_id.startswith("LOG_") for rule_id in _rule_ids(negated_logs)),
        "Negated INFO log statements were classified as confirmed failures",
    )
    positive_log = _analyze_adversarial_case(
        root,
        "positive-error-log",
        generated_at,
        relevant_logs="ERROR Modbus communication timeout while reading FC03\n",
    )
    require(
        "LOG_MODBUS_COMMUNICATION" in _rule_ids(positive_log),
        "Log negation guard also suppressed an explicit ERROR",
    )

    # 8. Schema 1 never advertised planner capabilities.  A legacy package
    # without RCEm/tariff snapshots is not evidence of a current defect.
    def legacy_reports(reports: list[dict]) -> None:
        for report in reports:
            report["integration_version"] = "0.8.0"
            snapshot = report["managed_state_snapshot"]
            snapshot.pop("sensor.hoymiles_hit_rcm_voltage_plan", None)
            snapshot.pop("sensor.hoymiles_hit_tariff_charge_plan", None)
            report["catalog_entities"] = [
                row
                for row in report["catalog_entities"]
                if row.get("translation_key") == "rce_optimized_plan"
            ]

    legacy = _analyze_adversarial_case(
        root,
        "legacy-missing-planners",
        generated_at,
        report_mutator=legacy_reports,
    )
    require(
        "PLANNER_SNAPSHOT_MISSING" not in _rule_ids(legacy),
        "A legacy package was treated as proof of a missing current planner",
    )
    legacy_not_evaluable = _finding(legacy, "PLANNER_SNAPSHOT_NOT_EVALUABLE")
    require(
        legacy_not_evaluable is not None
        and legacy_not_evaluable.get("severity") == "info"
        and legacy_not_evaluable.get("sample_evidence", {}).get("assessment")
        == "not_evaluable",
        "Legacy planner absence was not reported honestly as not_evaluable",
    )

    # 9a. Fresh voltage at/above the live 253 V emergency boundary cannot be
    # reconciled with live_emergency=false.
    def emergency_state_mismatch(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "maximum_voltage_v": 253.8,
                "emergency_voltage_data_fresh": True,
                "live_emergency": False,
                "emergency_action_ready": True,
            }
        )

    emergency_mismatch = _analyze_adversarial_case(
        root,
        "rcem-emergency-state-mismatch",
        generated_at,
        snapshot_mutator=emergency_state_mismatch,
    )
    require(
        "RCEM_EMERGENCY_STATE_INCONSISTENT" in _rule_ids(emergency_mismatch),
        "Fresh >=253 V evidence was inconsistent with live_emergency=false",
    )

    # 9b. Missing emergency_action_ready is unknown evidence, never a confirmed
    # false value.
    def emergency_action_missing(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "maximum_voltage_v": 253.8,
                "emergency_voltage_data_fresh": True,
                "live_emergency": True,
            }
        )
        attributes.pop("emergency_action_ready", None)

    emergency_unknown = _analyze_adversarial_case(
        root,
        "rcem-emergency-action-missing",
        generated_at,
        snapshot_mutator=emergency_action_missing,
    )
    require(
        "RCEM_EMERGENCY_UNHANDLED" not in _rule_ids(emergency_unknown),
        "Missing emergency_action_ready was coerced into confirmed false evidence",
    )
    require(
        "CONTROLLER_EVIDENCE_INCOMPLETE" in _rule_ids(emergency_unknown),
        "Missing emergency action evidence was silently presented as evaluable",
    )

    # 10. A package without active/owner/gate context must not look healthy.
    def missing_control_context(snapshot: dict[str, dict]) -> None:
        for entity_id in (
            "sensor.hoymiles_ems_control_owner",
            "binary_sensor.hoymiles_ems_execution_ready",
            "input_boolean.hoymiles_rce_discharge_active",
            "input_boolean.hoymiles_tariff_charge_active",
            "input_boolean.hoymiles_rcm_active",
            "input_boolean.hoymiles_rcm_export_control_active",
            "input_boolean.hoymiles_rcm_pre_discharge_active",
        ):
            snapshot.pop(entity_id, None)

    incomplete = _analyze_adversarial_case(
        root,
        "missing-control-context",
        generated_at,
        snapshot_mutator=missing_control_context,
    )
    require(
        "CONTROLLER_EVIDENCE_INCOMPLETE" in _rule_ids(incomplete),
        "Missing active/owner/gate context produced a clean verdict",
    )


    # 11. RCEm pre-discharge owns the EMS 4300-4306 path.  It deliberately
    # does not own direct registers 258/259/306, charge limit or GCF limit.
    def isolated_pre_discharge(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "shadow_mode": False,
                "action": "grid_discharge_preparation",
                "pre_discharge_ready": True,
                "pre_discharge_transaction_ready": True,
                "pre_discharge_continue_eligible": True,
                "pre_discharge_actuator_data_fresh": True,
                "discharge_registers_data_fresh": True,
                "ems_mode_data_fresh": True,
                "bms_discharge_data_fresh": True,
                "voltage_data_fresh": True,
                "gcf_data_fresh": True,
                "recommended_charge_limit_percent": 50.0,
                "recommended_export_limit_percent": 40.0,
            }
        )
        snapshot["input_boolean.hoymiles_rcm_pre_discharge_active"] = _state(
            "on", {}, BASE_TIME - timedelta(minutes=5)
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCEm", {"owner_code": "rcm"}, BASE_TIME
        )
        snapshot["binary_sensor.hoymiles_direct_register_execution_ready"] = (
            _state("off", {"reason": "direct_registers_unsupported"}, BASE_TIME)
        )
        snapshot[
            "sensor.hoymiles_hit_battery_max_charge_power_readback"
        ] = _state("100", {}, BASE_TIME)
        snapshot[
            "sensor.hoymiles_hit_gcf_maximum_export_power_readback"
        ] = _state("100", {}, BASE_TIME)

    pre_discharge_scope = _analyze_adversarial_case(
        root,
        "isolated-pre-discharge-scope",
        generated_at,
        snapshot_mutator=isolated_pre_discharge,
    )
    pre_discharge_scope_rules = _rule_ids(pre_discharge_scope)
    for forbidden in (
        "RCEM_DIRECT_REGISTER_GATE_BLOCKED",
        "RCEM_RECOMMENDATION_READBACK_MISMATCH",
        "RCEM_EXPORT_RECOMMENDATION_READBACK_MISMATCH",
    ):
        require(
            forbidden not in pre_discharge_scope_rules,
            f"Isolated pre-discharge was incorrectly assigned {forbidden}",
        )

    # 12. Charge/export readback checks use the same >=1.0 percentage-point
    # contract as the verified scripts and ignore the first 120 seconds.
    def rcem_charge_readback(
        age_seconds: float,
        readback_percent: float,
    ) -> Callable[[dict[str, dict]], None]:
        def mutate(snapshot: dict[str, dict]) -> None:
            attributes = _planner_attributes(
                snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
            )
            attributes.update(
                {
                    "shadow_mode": False,
                    "action": "absorb_pv",
                    "recommended_charge_limit_percent": 50.0,
                    "charge_actuator_data_fresh": True,
                    "bms_charge_data_fresh": True,
                }
            )
            snapshot["input_boolean.hoymiles_rcm_active"] = _state(
                "on", {}, BASE_TIME - timedelta(seconds=age_seconds)
            )
            snapshot["sensor.hoymiles_ems_control_owner"] = _state(
                "RCEm", {"owner_code": "rcm"}, BASE_TIME
            )
            snapshot[
                "binary_sensor.hoymiles_direct_register_execution_ready"
            ] = _state("on", {"reason": "ready"}, BASE_TIME)
            snapshot[
                "sensor.hoymiles_hit_battery_max_charge_power_readback"
            ] = _state(str(readback_percent), {}, BASE_TIME)

        return mutate

    grace = _analyze_adversarial_case(
        root,
        "rcem-readback-grace",
        generated_at,
        snapshot_mutator=rcem_charge_readback(119.0, 100.0),
    )
    require(
        "RCEM_RECOMMENDATION_READBACK_MISMATCH" not in _rule_ids(grace),
        "RCEm readback was judged before its two-minute grace elapsed",
    )
    within_tolerance = _analyze_adversarial_case(
        root,
        "rcem-readback-within-tolerance",
        generated_at,
        snapshot_mutator=rcem_charge_readback(120.0, 50.9),
    )
    require(
        "RCEM_RECOMMENDATION_READBACK_MISMATCH"
        not in _rule_ids(within_tolerance),
        "A verified <1.0 percentage-point RCEm readback was rejected",
    )
    outside_tolerance = _analyze_adversarial_case(
        root,
        "rcem-readback-outside-tolerance",
        generated_at,
        snapshot_mutator=rcem_charge_readback(120.0, 51.0),
    )
    require(
        "RCEM_RECOMMENDATION_READBACK_MISMATCH"
        in _rule_ids(outside_tolerance),
        "A mature >=1.0 percentage-point RCEm readback mismatch was missed",
    )

    # 13. Owner evidence is the owner_code attribute, not the translated state.
    def missing_owner_code(snapshot: dict[str, dict]) -> None:
        snapshot["sensor.hoymiles_ems_control_owner"]["attributes"].pop(
            "owner_code", None
        )

    owner_unknown = _analyze_adversarial_case(
        root,
        "owner-code-missing",
        generated_at,
        snapshot_mutator=missing_owner_code,
    )
    owner_finding = _finding(owner_unknown, "CONTROLLER_EVIDENCE_INCOMPLETE")
    require(
        owner_finding is not None
        and "owner"
        in owner_finding.get("sample_evidence", {}).get("missing_evidence", []),
        "A translated owner state hid a missing owner_code attribute",
    )

    # 14. Missing safety freshness is unknown evidence for every active path.
    def missing_rce_freshness(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
        )
        attributes.update(
            {
                "automatic_discharge_enabled": True,
                "current_slot_planned": True,
                "current_slot_continue_eligible": True,
            }
        )
        for key in (
            "bms_discharge_data_fresh",
            "gcf_execution_data_fresh",
            "soc_data_fresh",
        ):
            attributes.pop(key, None)
        snapshot["input_boolean.hoymiles_rce_discharge_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCE", {"owner_code": "rce"}, BASE_TIME
        )

    missing_rce = _analyze_adversarial_case(
        root,
        "rce-missing-safety-freshness",
        generated_at,
        snapshot_mutator=missing_rce_freshness,
    )
    require(
        "CONTROLLER_EVIDENCE_INCOMPLETE" in _rule_ids(missing_rce),
        "Missing active RCE safety freshness produced a clean verdict",
    )

    def missing_tariff_freshness(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_tariff_charge_plan"
        )
        attributes.update(
            {
                "automatic_charge_enabled": True,
                "current_slot_planned": True,
                "current_action": "battery_charge",
                "current_zone": "low",
                "current_run_continue_eligible": True,
            }
        )
        attributes.pop("control_inputs_fresh", None)
        snapshot["input_boolean.hoymiles_tariff_charge_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "Tariff", {"owner_code": "tariff"}, BASE_TIME
        )

    missing_tariff = _analyze_adversarial_case(
        root,
        "tariff-missing-safety-freshness",
        generated_at,
        snapshot_mutator=missing_tariff_freshness,
    )
    require(
        "CONTROLLER_EVIDENCE_INCOMPLETE" in _rule_ids(missing_tariff),
        "Missing active tariff safety freshness produced a clean verdict",
    )

    # 15. Directional and branch-level overlaps cannot be hidden by family
    # deduplication.
    def manual_direction_overlap(snapshot: dict[str, dict]) -> None:
        snapshot["input_boolean.hoymiles_charge_cycle_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["input_boolean.hoymiles_discharge_cycle_active"] = _state(
            "on", {}, BASE_TIME
        )

    manual_overlap = _analyze_adversarial_case(
        root,
        "manual-direction-overlap",
        generated_at,
        snapshot_mutator=manual_direction_overlap,
    )
    require(
        "SYS_MANUAL_DIRECTION_CONFLICT" in _rule_ids(manual_overlap),
        "Simultaneous manual charge/discharge was hidden as one family",
    )

    def rcem_subpath_overlap(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rcm_voltage_plan"
        )
        attributes.update(
            {
                "shadow_mode": False,
                "action": "grid_discharge_preparation",
                "pre_discharge_continue_eligible": True,
            }
        )
        snapshot["input_boolean.hoymiles_rcm_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["input_boolean.hoymiles_rcm_pre_discharge_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "RCEm", {"owner_code": "rcm"}, BASE_TIME
        )
        snapshot["binary_sensor.hoymiles_direct_register_execution_ready"] = (
            _state("on", {"reason": "ready"}, BASE_TIME)
        )

    rcem_overlap = _analyze_adversarial_case(
        root,
        "rcem-subpath-overlap",
        generated_at,
        snapshot_mutator=rcem_subpath_overlap,
    )
    require(
        "RCEM_SUBPATH_CONFLICT" in _rule_ids(rcem_overlap),
        "RCEm pre-discharge overlap was hidden inside one controller family",
    )

    # 16. Grid support is also a low-zone-only actuator.
    def grid_support_outside_low(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_tariff_charge_plan"
        )
        attributes.update(
            {
                "status_code": "ready",
                "automatic_charge_enabled": True,
                "current_slot_planned": True,
                "current_action": "grid_support",
                "current_zone": "high",
                "current_run_continue_eligible": True,
                "control_inputs_fresh": True,
            }
        )
        snapshot["input_boolean.hoymiles_tariff_charge_active"] = _state(
            "on", {}, BASE_TIME
        )
        snapshot["sensor.hoymiles_ems_control_owner"] = _state(
            "Tariff", {"owner_code": "tariff"}, BASE_TIME
        )

    support_outside_low = _analyze_adversarial_case(
        root,
        "grid-support-outside-low-zone",
        generated_at,
        snapshot_mutator=grid_support_outside_low,
    )
    require(
        "TARIFF_ACTIVE_OUTSIDE_LOW_ZONE" in _rule_ids(support_outside_low),
        "Active grid_support outside the low zone was not diagnosed",
    )

    # 17. Privacy-default output redacts UUID text even when it appears in a
    # permitted diagnostic attribute instead of the identity metadata field.
    def uuid_in_metric(snapshot: dict[str, dict]) -> None:
        attributes = _planner_attributes(
            snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
        )
        attributes["rce_debug_marker"] = INSTALLATION_B

    uuid_summary = _analyze_adversarial_case(
        root,
        "uuid-in-metric",
        generated_at,
        snapshot_mutator=uuid_in_metric,
    )
    require(
        INSTALLATION_A not in json.dumps(uuid_summary)
        and INSTALLATION_B not in json.dumps(uuid_summary),
        "A full UUID leaked through normalized controller evidence",
    )
    uuid_opt_in = analyze_inputs(
        [root / "adversarial-uuid-in-metric.zip"],
        generated_at=generated_at,
        include_anonymous_id=True,
    )
    require(
        INSTALLATION_A in json.dumps(uuid_opt_in)
        and INSTALLATION_B not in json.dumps(uuid_opt_in),
        "Anonymous-ID opt-in exposed unrelated UUID evidence",
    )

    # 18. A newer suffixed planner from another config entry must not replace
    # the canonical entity consumed by the packaged scheduler.
    def suffixed_planner(snapshot: dict[str, dict]) -> None:
        canonical = snapshot["sensor.hoymiles_hit_rce_optimized_plan"]
        suffixed = json.loads(json.dumps(canonical))
        suffixed["attributes"]["planned_export_kwh"] = 999.0
        suffixed["last_updated"] = (BASE_TIME + timedelta(minutes=1)).isoformat()
        snapshot["sensor.hoymiles_hit_rce_optimized_plan_2"] = suffixed

    suffix_summary = _analyze_adversarial_case(
        root,
        "canonical-planner-before-suffix",
        generated_at,
        snapshot_mutator=suffixed_planner,
    )
    rce_observation = next(
        item
        for item in suffix_summary["observations"]
        if item.get("controller") == "rce"
    )
    require(
        rce_observation.get("entity_id")
        == "sensor.hoymiles_hit_rce_optimized_plan"
        and rce_observation.get("metrics", {}).get("planned_export_kwh") == 20.0,
        "A non-controlling suffixed planner replaced the canonical planner",
    )

    # 19. Catalog coverage belongs to each config entry even though the state
    # snapshot is global and repeated.
    def second_entry_incomplete(reports: list[dict]) -> None:
        reports[1]["catalog_coverage"].update(
            {
                "missing_count": 1,
                "missing_translation_keys": ["ems_mode_readback_code"],
            }
        )

    coverage_summary = _analyze_adversarial_case(
        root,
        "second-entry-catalog-incomplete",
        generated_at,
        report_mutator=second_entry_incomplete,
    )
    coverage_finding = _finding(
        coverage_summary, "DIAG_CATALOG_INCOMPLETE"
    )
    require(
        coverage_finding is not None
        and coverage_finding.get("sample_evidence", {}).get("report_index")
        == 1,
        "Incomplete catalog coverage of the second config entry was ignored",
    )


def _run_history_regressions() -> None:
    installation = "inst-history-test"

    def event(entity_id: str, state: str, seconds: int) -> dict:
        timestamp = (BASE_TIME + timedelta(seconds=seconds)).isoformat()
        return {
            "installation_key": installation,
            "archive_key": "archive-history",
            "entity_id": entity_id,
            "state": state,
            "last_changed": timestamp,
            "last_updated": timestamp,
        }

    events = [
        event("input_boolean.hoymiles_rce_discharge_active", "off", -10),
        event("input_boolean.hoymiles_rce_discharge_active", "on", 0),
        event("input_boolean.hoymiles_rce_discharge_active", "off", 60),
        event("binary_sensor.hoymiles_ems_execution_ready", "on", -10),
        event("binary_sensor.hoymiles_ems_execution_ready", "off", 10),
        event("binary_sensor.hoymiles_ems_execution_ready", "on", 40),
        # Pre-discharge does not own direct registers 258/259/306.  Its
        # execution gate is EMS, so this direct-gate overlap must stay silent.
        event("input_boolean.hoymiles_rcm_pre_discharge_active", "off", -10),
        event("input_boolean.hoymiles_rcm_pre_discharge_active", "on", 70),
        event("input_boolean.hoymiles_rcm_pre_discharge_active", "off", 130),
        event("binary_sensor.hoymiles_direct_register_execution_ready", "off", 60),
        event("binary_sensor.hoymiles_direct_register_execution_ready", "on", 140),
    ]
    capture_end = {installation: BASE_TIME + timedelta(seconds=150)}
    metrics, findings = analyze_control_history(events, capture_end)
    codes = {finding.code for finding in findings}
    require(
        "HISTORY_RCE_ACTIVE_WITH_EMS_GATE_OFF" in codes,
        "A confirmed 30-second historical gate overlap was missed",
    )
    require(
        "HISTORY_RCEM_ACTIVE_WITH_DIRECT_GATE_OFF" not in codes,
        "Pre-discharge was falsely treated as a direct-register owner",
    )
    rce_metric = next(
        item
        for item in metrics
        if item.get("entity_id")
        == "input_boolean.hoymiles_rce_discharge_active"
    )
    require(
        rce_metric.get("starts") == 1
        and rce_metric.get("stops") == 1
        and rce_metric.get("active_minutes") == 1.0,
        "Control-run start/stop/duration metrics are incorrect",
    )
    reversed_result = analyze_control_history(
        list(reversed(events)), capture_end
    )
    require(
        (metrics, findings) == reversed_result,
        "History analysis depends on event ordering",
    )

    # Separate Recorder queries must not bridge an unobserved multi-day gap.
    # Otherwise two unrelated owners appear to overlap and an open state is
    # counted for thousands of invented minutes.
    day_one = BASE_TIME
    day_ten = BASE_TIME + timedelta(days=9)
    gap_events = [
        {
            **event("input_boolean.hoymiles_rce_discharge_active", "on", 0),
            "last_changed": day_one.isoformat(),
            "last_updated": day_one.isoformat(),
        },
        {
            **event("input_boolean.hoymiles_tariff_charge_active", "on", 0),
            "last_changed": day_ten.isoformat(),
            "last_updated": day_ten.isoformat(),
        },
    ]
    gap_windows = {
        installation: (
            (day_one, day_one + timedelta(hours=1)),
            (day_ten, day_ten + timedelta(hours=1)),
        )
    }
    gap_metrics, gap_findings = analyze_control_history(
        gap_events, gap_windows
    )
    require(
        "HISTORY_ACTIVE_FAMILY_OVERLAP"
        not in {finding.code for finding in gap_findings},
        "Disjoint Recorder windows were bridged into a false owner overlap",
    )
    require(
        all(item.get("active_minutes") == 60.0 for item in gap_metrics),
        "An open state was extended through an unobserved history gap",
    )


def _run_history_archive_regressions(
    root: Path,
    generated_at: datetime,
) -> None:
    """Verify extraction of boundary seeds and non-EMS RCEm gate history."""

    def direct_gate_history(reports: list[dict]) -> None:
        start = BASE_TIME - timedelta(minutes=5)
        seed_time = start - timedelta(hours=2)
        for report in reports:
            report["control_history"] = {
                "available": True,
                "hours": 24,
                "start": start.isoformat(),
                "end": BASE_TIME.isoformat(),
                "entities": {
                    "input_boolean.hoymiles_rcm_active": [
                        {
                            "state": "on",
                            "last_changed": seed_time.isoformat(),
                            "last_updated": seed_time.isoformat(),
                        }
                    ],
                    "binary_sensor.hoymiles_direct_register_execution_ready": [
                        {
                            "state": "off",
                            "last_changed": seed_time.isoformat(),
                            "last_updated": seed_time.isoformat(),
                        }
                    ],
                },
                "truncated_entities": [],
            }

    summary = _analyze_adversarial_case(
        root,
        "history-direct-register-gate",
        generated_at,
        report_mutator=direct_gate_history,
    )
    require(
        "HISTORY_RCEM_ACTIVE_WITH_DIRECT_GATE_OFF" in _rule_ids(summary),
        "Direct-register gate history was filtered out before RCEm analysis",
    )
    boundary_events = [
        event
        for event in summary["control_events"]
        if event.get("history_boundary_seed") is True
    ]
    require(
        len(boundary_events) == 2,
        "Recorder include-start states were not clamped to the query boundary",
    )


def main() -> None:
    require(UUID(INSTALLATION_A).version == 4, "Fixture ID A is not UUID v4")
    fixed_analysis_time = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="hoymiles_batch_analyzer_") as raw_tmp:
        root = Path(raw_tmp)
        _run_adversarial_regressions(root, fixed_analysis_time)
        _run_history_regressions()
        _run_history_archive_regressions(root, fixed_analysis_time)
        bundles = root / "bundles"
        bundles.mkdir()
        start = perf_counter()
        for index in range(100):
            installation = INSTALLATION_A if index < 60 else INSTALLATION_B
            mutation = (
                "rce_bms_zero"
                if index == 10
                else "rcem_emergency"
                if index == 20
                else "tariff_active_stale"
                if index == 30
                else None
            )
            build_bundle(
                bundles / f"bundle-{99-index:03d}.zip",
                installation,
                BASE_TIME + timedelta(minutes=index),
                mutation=mutation,
                divergent_second_entry=index == 40,
            )

        summary = analyze_inputs(
            [bundles],
            generated_at=fixed_analysis_time,
        )
        elapsed = perf_counter() - start
        totals = summary["totals"]
        require(totals["discovered_archives"] == 100, "Did not discover 100 ZIPs")
        require(
            totals["accepted_or_partial_archives"] == 100,
            "Did not analyze all 100 valid ZIPs",
        )
        require(totals["installations"] == 2, "Installations were merged/split")
        require(
            totals["controller_observations"] == 300,
            "Multi-entry snapshots were counted more than once",
        )
        divergent_observation = next(
            item
            for item in summary["observations"]
            if item.get("controller") == "rce"
            and item.get("observed_at")
            == (BASE_TIME + timedelta(minutes=40)).isoformat()
        )
        require(
            divergent_observation.get("report_index") == 1
            and divergent_observation.get("metrics", {}).get(
                "planned_export_kwh"
            )
            == 99.0,
            "Newest multi-entry planner snapshot was not selected by timestamp",
        )
        require(
            totals["deduplicated_control_events"] == 200,
            "Control history was not deduplicated per archive/install/time",
        )
        require(elapsed < 30.0, f"100-package analysis too slow: {elapsed:.2f}s")
        rules = _rule_ids(summary)
        for expected in (
            "RCE_PHYSICAL_LIMIT_INCONSISTENT",
            "RCE_BMS_POSITIVE_BUT_EXPORT_ZERO",
            "RCEM_EMERGENCY_UNHANDLED",
            "TARIFF_ACTIVE_WITH_STALE_INPUTS",
            "ARCHIVE_MULTI_ENTRY_SNAPSHOT_DIVERGENCE",
        ):
            require(expected in rules, f"Missing expected finding {expected}")
        require(
            "RCE_PHYSICAL_RESPONSE_MISMATCH" not in rules,
            "Analyzer invented physical ramp evidence from one snapshot",
        )
        require(
            INSTALLATION_A not in json.dumps(summary),
            "Full anonymous UUID leaked into default analysis",
        )
        require(
            "bundle-099.zip" not in json.dumps(summary),
            "Source filename leaked into default analysis",
        )

        reversed_summary = analyze_inputs(
            list(reversed(discover_archives([bundles]))),
            generated_at=fixed_analysis_time,
        )
        require(summary == reversed_summary, "Analysis depends on input ordering")

        output = root / "output"
        manifest = write_analysis_outputs(summary, output)
        require(len(manifest) == 12, "Output manifest is incomplete")
        for name in (
            "summary.json",
            "report.md",
            "report.html",
            "findings.csv",
            "rce_observations.csv",
            "rcem_observations.csv",
            "tariff_observations.csv",
            "control_runs.csv",
        ):
            require((output / name).is_file(), f"Missing output {name}")
        try:
            write_analysis_outputs(summary, output)
        except FileExistsError:
            pass
        else:
            raise RuntimeError("Output overwrite was allowed without --force")
        source_summary = dict(summary)
        source_summary["source_map"] = [
            {"input_key": "x", "source_path": "=2+5"}
        ]
        source_csv = build_output_payloads(source_summary)["source_map.csv"].decode(
            "utf-8-sig"
        )
        require("'=2+5" in source_csv, "CSV formula injection was not neutralized")
        toggle_output = root / "toggle-source-map"
        write_analysis_outputs(source_summary, toggle_output)
        require(
            (toggle_output / "source_map.csv").is_file(),
            "Opt-in source map was not published",
        )
        write_analysis_outputs(summary, toggle_output, force=True)
        require(
            not (toggle_output / "source_map.csv").exists(),
            "A privacy-sensitive source map survived a default --force run",
        )

        # A corrupt package is isolated while the other package remains useful.
        partial = root / "partial"
        partial.mkdir()
        shutil.copy2(bundles / "bundle-099.zip", partial / "good.zip")
        (partial / "corrupt.zip").write_bytes(b"not a ZIP")
        partial_summary = analyze_inputs(
            [partial], generated_at=fixed_analysis_time
        )
        require(
            partial_summary["totals"]["accepted_or_partial_archives"] == 1
            and partial_summary["totals"]["rejected_archives"] == 1,
            "One corrupt ZIP stopped or contaminated the batch",
        )
        require(
            "ARCHIVE_CORRUPT" in _rule_ids(partial_summary),
            "Rejected archive did not produce a stable finding",
        )

        mismatch = root / "identity-mismatch.zip"
        build_bundle(
            mismatch,
            INSTALLATION_A,
            BASE_TIME,
            identity_mismatch=True,
        )
        mismatch_summary = analyze_inputs(
            [mismatch], generated_at=fixed_analysis_time
        )
        require(
            "ARCHIVE_IDENTITY_MISMATCH" in _rule_ids(mismatch_summary),
            "Identity mismatch was silently linked",
        )
        require(
            mismatch_summary["packages"][0]["installation_key"].startswith(
                "unlinked-"
            ),
            "Mismatched identity was used for longitudinal grouping",
        )

        # Same timestamp may contain several genuinely different diagnostic
        # variants.  A later archive equal to any known variant is a semantic
        # duplicate, not another conflict or accepted sample.
        semantic = root / "semantic-variants"
        semantic.mkdir()

        def changed_rce(snapshot: dict[str, dict]) -> None:
            _planner_attributes(
                snapshot, "sensor.hoymiles_hit_rce_optimized_plan"
            )["planned_export_kwh"] = 21.0

        build_bundle(semantic / "a.zip", INSTALLATION_A, BASE_TIME)
        build_bundle(
            semantic / "b.zip",
            INSTALLATION_A,
            BASE_TIME,
            snapshot_mutator=changed_rce,
            relevant_logs="INFO semantic variant B\n",
        )
        build_bundle(
            semantic / "c.zip",
            INSTALLATION_A,
            BASE_TIME,
            snapshot_mutator=changed_rce,
            relevant_logs="INFO semantic variant C\n",
        )
        semantic_summary = analyze_inputs(
            [semantic], generated_at=fixed_analysis_time
        )
        require(
            semantic_summary["totals"]["accepted_or_partial_archives"] == 2
            and semantic_summary["totals"]["duplicate_archives"] == 1,
            "A duplicate of the second same-timestamp variant was reaccepted",
        )
        require(
            sum(
                item.get("code") == "ARCHIVE_DUPLICATE_TIMESTAMP_CONFLICT"
                for item in semantic_summary["finding_occurrences"]
            )
            == 1,
            "Same-timestamp semantic variants produced the wrong conflicts",
        )

        # An unused multi-entry candidate still carries a divergence warning
        # and must participate in semantic deduplication.
        unused_candidates = root / "semantic-unused-candidates"
        unused_candidates.mkdir()
        build_bundle(
            unused_candidates / "equal.zip",
            INSTALLATION_A,
            BASE_TIME,
        )
        build_bundle(
            unused_candidates / "divergent.zip",
            INSTALLATION_A,
            BASE_TIME,
            divergent_second_entry=True,
        )
        unused_summary = analyze_inputs(
            [unused_candidates], generated_at=fixed_analysis_time
        )
        require(
            unused_summary["totals"]["accepted_or_partial_archives"] == 2
            and "ARCHIVE_MULTI_ENTRY_SNAPSHOT_DIVERGENCE"
            in _rule_ids(unused_summary),
            "A divergent non-selected planner candidate was deduplicated away",
        )

        unsafe = root / "unsafe.zip"
        build_bundle(
            unsafe,
            INSTALLATION_A,
            BASE_TIME,
            suspicious_member=True,
        )
        try:
            load_diagnostic_archive(unsafe)
        except ArchiveReadError as err:
            require(
                err.code == "ZIP_SUSPICIOUS_MEMBER_PATH",
                f"Unexpected unsafe-ZIP code: {err.code}",
            )
        else:
            raise RuntimeError("Path traversal member was accepted")
        require(not (root / "escape.txt").exists(), "ZIP member was extracted")

        # Member count is rejected from the bounded EOCD preflight before
        # Python materializes an attacker-controlled central directory.
        many_members = root / "many-members.zip"
        with ZipFile(many_members, "w") as archive:
            for index in range(DEFAULT_LIMITS.max_members + 1):
                _write_member(archive, f"extra-{index:03d}.txt", "")
        try:
            load_diagnostic_archive(many_members)
        except ArchiveReadError as err:
            require(
                err.code == "ZIP_TOO_MANY_MEMBERS",
                f"Unexpected central-directory preflight code: {err.code}",
            )
        else:
            raise RuntimeError("Oversized central directory reached ZipFile")

        # A damaged DEFLATE stream/CRC is converted to an isolated archive
        # rejection instead of escaping as a raw zlib exception.
        damaged = root / "damaged-deflate.zip"
        shutil.copy2(bundles / "bundle-099.zip", damaged)
        with ZipFile(damaged, "r") as archive:
            member = archive.getinfo("hoymiles_diagnostics.json")
            local_header = member.header_offset
        damaged_bytes = bytearray(damaged.read_bytes())
        name_length = int.from_bytes(
            damaged_bytes[local_header + 26 : local_header + 28], "little"
        )
        extra_length = int.from_bytes(
            damaged_bytes[local_header + 28 : local_header + 30], "little"
        )
        data_offset = local_header + 30 + name_length + extra_length
        damaged_bytes[data_offset + max(member.compress_size // 2, 1)] ^= 0x01
        damaged.write_bytes(damaged_bytes)
        try:
            load_diagnostic_archive(damaged)
        except ArchiveReadError as err:
            require(
                err.code == "ZIP_MEMBER_READ_FAILED",
                f"Damaged DEFLATE leaked the wrong error: {err.code}",
            )
        else:
            raise RuntimeError("Damaged DEFLATE member was accepted")

        too_many_reports = root / "too-many-reports.zip"

        def expand_reports(reports: list[dict]) -> None:
            template = json.loads(json.dumps(reports[0]))
            reports[:] = [
                json.loads(json.dumps(template))
                for _index in range(DEFAULT_LIMITS.max_reports + 1)
            ]

        build_bundle(
            too_many_reports,
            INSTALLATION_A,
            BASE_TIME,
            report_mutator=expand_reports,
        )
        try:
            load_diagnostic_archive(too_many_reports)
        except ArchiveReadError as err:
            require(
                err.code == "DIAGNOSTIC_REPORT_COUNT_LIMIT",
                f"Unexpected report-count limit code: {err.code}",
            )
        else:
            raise RuntimeError("Excessive config-entry reports were accepted")

        invalid_unicode = root / "invalid-unicode.zip"

        def lone_surrogate(reports: list[dict]) -> None:
            reports[0]["managed_state_snapshot"][
                "sensor.hoymiles_hit_rce_optimized_plan"
            ]["attributes"]["rce_debug_marker"] = "\ud800"

        build_bundle(
            invalid_unicode,
            INSTALLATION_A,
            BASE_TIME,
            report_mutator=lone_surrogate,
        )
        invalid_unicode_summary = analyze_inputs(
            [invalid_unicode, bundles / "bundle-099.zip"],
            generated_at=fixed_analysis_time,
        )
        require(
            invalid_unicode_summary["totals"]["rejected_archives"] == 1
            and invalid_unicode_summary["totals"][
                "accepted_or_partial_archives"
            ]
            == 1,
            "Invalid JSON Unicode was not isolated from the valid archive",
        )

        # The hard archive limit is checked before any partial result is built.
        too_many = root / "too-many"
        too_many.mkdir()
        for index in range(150):
            shutil.copy2(
                bundles / "bundle-099.zip",
                too_many / f"copy-{index:03d}.zip",
            )
        original_path_key = archive_module._path_key
        path_key_calls = 0

        def counted_path_key(path: Path) -> str:
            nonlocal path_key_calls
            path_key_calls += 1
            return original_path_key(path)

        archive_module._path_key = counted_path_key
        try:
            try:
                discover_archives(
                    [too_many],
                    limits=replace(DEFAULT_LIMITS, max_archives=100),
                )
            except ArchiveReadError as err:
                require(
                    err.code == "TOO_MANY_ARCHIVES",
                    "Wrong archive-limit error",
                )
            else:
                raise RuntimeError("150 archives passed a 100-archive hard limit")
        finally:
            archive_module._path_key = original_path_key
        require(
            path_key_calls <= 101,
            "Archive discovery scanned the whole oversized directory",
        )

        unsupported = root / "unsupported-schema.zip"

        def schema_two(reports: list[dict]) -> None:
            for report in reports:
                report["report_schema"] = 2

        build_bundle(
            unsupported,
            INSTALLATION_A,
            BASE_TIME,
            report_mutator=schema_two,
        )
        unsupported_output = root / "unsupported-output"
        require(
            cli_main(
                [
                    str(unsupported),
                    "--output",
                    str(unsupported_output),
                ]
            )
            == 3,
            "Unsupported-only input did not return the dedicated CLI code",
        )

        cli_output = root / "cli-output"
        cli_code = cli_main(
            [
                str(bundles / "bundle-099.zip"),
                "--output",
                str(cli_output),
            ]
        )
        require(cli_code == 0, f"CLI failed with exit code {cli_code}")
        require((cli_output / "report.html").is_file(), "CLI omitted HTML report")

    print(
        "Diagnostic analyzer tests passed: 100 archives, security, "
        "longitudinal grouping and deterministic outputs"
    )


if __name__ == "__main__":
    main()
