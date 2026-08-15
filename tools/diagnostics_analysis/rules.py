"""Versioned diagnostic rules for RCE, RCEm and tariff observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import math
from typing import Any, Iterable, Mapping, Sequence

from .extractors import (
    context_attributes,
    context_state,
    finite_number,
    is_unknown,
    strict_bool,
)
from .models import (
    Confidence,
    Controller,
    ControllerObservation,
    Finding,
    LoadedDiagnosticArchive,
    Severity,
)


POWER_TOLERANCE_KW = 0.05
READBACK_TOLERANCE_PERCENT = 1.0


def _value(observation: ControllerObservation, key: str) -> Any:
    if key in observation.flags:
        return observation.flags[key]
    if key in observation.freshness:
        return observation.freshness[key]
    if key in observation.metrics:
        return observation.metrics[key]
    if key in observation.details:
        return observation.details[key]
    return None


def _finding(
    code: str,
    severity: Severity,
    message: str,
    observation: ControllerObservation | None = None,
    *,
    archive: LoadedDiagnosticArchive | None = None,
    controller: Controller | None = None,
    confidence: Confidence = Confidence.HIGH,
    evidence: Mapping[str, Any] | None = None,
    recommendation: str | None = None,
) -> Finding:
    return Finding(
        code=code,
        severity=severity,
        message=message,
        confidence=confidence,
        installation_key=(
            observation.installation_key
            if observation is not None
            else archive.metadata.installation_key
            if archive is not None
            else None
        ),
        archive_key=(
            observation.archive_key
            if observation is not None
            else archive.metadata.archive_key
            if archive is not None
            else None
        ),
        controller=(
            observation.controller if observation is not None else controller
        ),
        observed_at=(
            observation.observed_at
            if observation is not None
            else archive.metadata.generated_at
            if archive is not None
            else None
        ),
        evidence=dict(evidence or {}),
        recommendation=recommendation,
    )


def _context_numeric_state(
    context: Mapping[str, Mapping[str, Any]],
    suffix: str,
) -> float | None:
    value = context_state(context, suffix)
    if value is None:
        return None
    try:
        numeric = float(value)
    except ValueError:
        return None
    return numeric if math.isfinite(numeric) else None


def _context_active_age_seconds(
    context: Mapping[str, Mapping[str, Any]],
    suffix: str,
    observed_at: datetime | None,
) -> float | None:
    if observed_at is None:
        return None
    for entity_id, snapshot in context.items():
        if not entity_id.endswith(suffix) or snapshot.get("state") != "on":
            continue
        raw = snapshot.get("last_changed")
        if not isinstance(raw, str):
            return None
        try:
            changed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if changed.tzinfo is None or observed_at.tzinfo is None:
            return None
        return max((observed_at - changed).total_seconds(), 0.0)
    return None


def _controller_map(
    observations: Sequence[ControllerObservation],
) -> dict[Controller, ControllerObservation]:
    return {observation.controller: observation for observation in observations}


def evaluate_archive(
    archive: LoadedDiagnosticArchive,
    observations: Sequence[ControllerObservation],
    context: Mapping[str, Mapping[str, Any]],
    extraction_issues: Sequence[Mapping[str, Any]],
    log_counts: Mapping[str, int] | None = None,
) -> tuple[Finding, ...]:
    """Evaluate one package without inferring evidence it does not contain."""
    findings: list[Finding] = []
    by_controller = _controller_map(observations)

    for issue in extraction_issues:
        findings.append(
            _finding(
                str(issue.get("code", "ARCHIVE_EXTRACTION_WARNING")),
                Severity.WARNING,
                "Raporty config entry zawierają rozbieżne kopie globalnego snapshotu.",
                archive=archive,
                controller=_controller_from_value(issue.get("controller")),
                confidence=Confidence.HIGH,
                evidence={**issue, "assessment": "confirmed"},
                recommendation=(
                    "Sprawdź kolejność generowania raportów; analizator wybrał "
                    "deterministycznie najświeższy snapshot."
                ),
            )
        )

    for controller in (Controller.RCE, Controller.RCEM, Controller.TARIFF):
        observation = by_controller.get(controller)
        if observation is None:
            declared = _catalog_declares_controller(archive, controller)
            findings.append(
                _finding(
                    (
                        "PLANNER_SNAPSHOT_MISSING"
                        if declared
                        else "PLANNER_SNAPSHOT_NOT_EVALUABLE"
                    ),
                    Severity.ERROR if declared else Severity.INFO,
                    (
                        f"Brak zadeklarowanego snapshotu planera {controller.value}."
                        if declared
                        else (
                            f"Paczka nie udostępnia planera {controller.value}; "
                            "ten obszar nie może zostać oceniony."
                        )
                    ),
                    archive=archive,
                    controller=controller,
                    confidence=Confidence.HIGH,
                    evidence={
                        "assessment": (
                            "confirmed" if declared else "not_evaluable"
                        ),
                        "catalog_declared": declared,
                    },
                    recommendation=(
                        "Sprawdź kompletność encji integracji i wygeneruj nową paczkę."
                        if declared
                        else "Porównuj tylko kontrolery obecne w tej wersji paczki."
                    ),
                )
            )

    _evaluate_archive_health(archive, findings)
    _evaluate_common_control(archive, observations, context, findings)
    if Controller.RCE in by_controller:
        _evaluate_rce(by_controller[Controller.RCE], context, findings)
    if Controller.RCEM in by_controller:
        _evaluate_rcem(by_controller[Controller.RCEM], context, findings)
    if Controller.TARIFF in by_controller:
        _evaluate_tariff(by_controller[Controller.TARIFF], context, findings)
    _evaluate_logs(archive, log_counts or {}, findings)
    return tuple(findings)


def _evaluate_archive_health(
    archive: LoadedDiagnosticArchive,
    findings: list[Finding],
) -> None:
    for report in archive.reports:
        coverage = report.catalog_coverage
        missing_count = finite_number(coverage.get("missing_count"))
        runtime_loaded = strict_bool(coverage.get("runtime_loaded"))
        if runtime_loaded is False or (
            missing_count is not None and missing_count > 0
        ):
            findings.append(
                _finding(
                    "DIAG_CATALOG_INCOMPLETE",
                    Severity.ERROR,
                    "Katalog encji integracji jest niekompletny.",
                    archive=archive,
                    confidence=Confidence.HIGH,
                    evidence={
                        "assessment": "confirmed",
                        "missing_count": (
                            int(missing_count)
                            if missing_count is not None
                            else None
                        ),
                        "runtime_loaded": runtime_loaded,
                        "report_index": report.report_index,
                    },
                    recommendation=(
                        "Sprawdź source device, migrację rejestru encji i zgodność firmware."
                    ),
                )
            )
    # The current snapshot/history are global and repeated for every config
    # entry; catalog coverage above is per entry and must not be short-circuited.
    if archive.reports:
        history = archive.reports[0].control_history
        available = strict_bool(history.get("available"))
        if available is False:
            findings.append(
                _finding(
                    "DIAG_HISTORY_UNAVAILABLE",
                    Severity.WARNING,
                    "Historia Recorder nie była dostępna podczas eksportu.",
                    archive=archive,
                    confidence=Confidence.HIGH,
                    evidence={
                        "assessment": "confirmed",
                        "error_type": history.get("error_type"),
                    },
                    recommendation=(
                        "Sprawdź Recorder, wolne miejsce i ponów paczkę po ustabilizowaniu HA."
                    ),
                )
            )
        truncated = history.get("truncated_entities")
        if isinstance(truncated, list) and truncated:
            findings.append(
                _finding(
                    "DIAG_HISTORY_TRUNCATED",
                    Severity.WARNING,
                    "Historia części encji osiągnęła limit 500 zdarzeń.",
                    archive=archive,
                    confidence=Confidence.HIGH,
                    evidence={
                        "assessment": "confirmed",
                        "truncated_entity_count": len(truncated),
                    },
                    recommendation=(
                        "Sprawdź flapping encji i zbierz paczkę w krótszym oknie problemu."
                    ),
                )
            )


def _catalog_declares_controller(
    archive: LoadedDiagnosticArchive,
    controller: Controller,
) -> bool:
    expected = {
        Controller.RCE: "rce_optimized_plan",
        Controller.RCEM: "rcm_voltage_plan",
        Controller.TARIFF: "tariff_charge_plan",
    }[controller]
    return any(
        row.get("translation_key") == expected
        for report in archive.reports
        for row in report.catalog_entities
        if isinstance(row, Mapping)
    )


def _active_families(
    observations: Sequence[ControllerObservation],
    context: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    families: list[str] = []
    for observation in observations:
        if observation.active is True:
            families.append(observation.controller.value)
    manual_charge = context_state(context, "hoymiles_charge_cycle_active") == "on"
    manual_discharge = (
        context_state(context, "hoymiles_discharge_cycle_active") == "on"
    )
    balancing = context_state(context, "hoymiles_battery_balancing_active") == "on"
    if manual_charge or manual_discharge:
        families.append("manual")
    if balancing:
        families.append("balancing")
    return sorted(set(families))


def _evaluate_common_control(
    archive: LoadedDiagnosticArchive,
    observations: Sequence[ControllerObservation],
    context: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    families = _active_families(observations, context)
    manual_charge = context_state(context, "hoymiles_charge_cycle_active") == "on"
    manual_discharge = (
        context_state(context, "hoymiles_discharge_cycle_active") == "on"
    )
    rcm_charge = context_state(context, "hoymiles_rcm_active") == "on"
    rcm_export = (
        context_state(context, "hoymiles_rcm_export_control_active") == "on"
    )
    rcm_pre = (
        context_state(context, "hoymiles_rcm_pre_discharge_active") == "on"
    )
    conflict = context_state(context, "hoymiles_ems_control_conflict") == "on"
    if len(families) > 1:
        findings.append(
            _finding(
                "SYS_ACTIVE_MODE_OVERLAP",
                Severity.CRITICAL,
                "Jednocześnie aktywna jest więcej niż jedna rodzina sterowania EMS.",
                archive=archive,
                evidence={"assessment": "confirmed", "active_families": families},
                recommendation="Zatrzymaj właścicieli i sprawdź interlock/rollback.",
            )
        )
    if manual_charge and manual_discharge:
        findings.append(
            _finding(
                "SYS_MANUAL_DIRECTION_CONFLICT",
                Severity.CRITICAL,
                "Ręczne ładowanie i rozładowanie są aktywne jednocześnie.",
                archive=archive,
                evidence={
                    "assessment": "confirmed",
                    "manual_charge_active": True,
                    "manual_discharge_active": True,
                },
                recommendation="Zatrzymaj oba cykle i zweryfikuj flagi ownership.",
            )
        )
    if rcm_pre and (rcm_charge or rcm_export):
        findings.append(
            _finding(
                "RCEM_SUBPATH_CONFLICT",
                Severity.CRITICAL,
                "RCEm pre-discharge nakłada się na inny tor wykonawczy RCEm.",
                archive=archive,
                controller=Controller.RCEM,
                confidence=Confidence.MEDIUM,
                evidence={
                    "assessment": "suspected",
                    "charge_active": rcm_charge,
                    "export_active": rcm_export,
                    "pre_discharge_active": rcm_pre,
                },
                recommendation="Sprawdź handover/rollback RCEm i ponów paczkę po chwili.",
            )
        )
    if conflict:
        findings.append(
            _finding(
                "SYS_CONTROL_CONFLICT",
                Severity.CRITICAL if len(families) > 1 else Severity.ERROR,
                "System zgłasza konflikt polityk sterowania EMS.",
                archive=archive,
                evidence={
                    "assessment": "confirmed",
                    "active_families": families,
                },
                recommendation=(
                    "Wyłącz konkurujące polityki; przy overlapie potwierdź neutralny rollback."
                ),
            )
        )

    owner_attributes = context_attributes(context, "hoymiles_ems_control_owner")
    owner_code = owner_attributes.get("owner_code")
    if len(families) == 1 and isinstance(owner_code, str):
        expected = families[0]
        aliases = {
            "rcem": {"rcm", "rcem"},
            "rce": {"rce"},
            "tariff": {"tariff"},
            "manual": {"manual"},
            "balancing": {"balancing"},
        }
        if owner_code not in aliases.get(expected, {expected}):
            findings.append(
                _finding(
                    "SYS_OWNER_MISMATCH",
                    Severity.ERROR,
                    "Deklarowany właściciel EMS nie odpowiada aktywnej rodzinie.",
                    archive=archive,
                    evidence={
                        "assessment": "confirmed",
                        "active_family": expected,
                        "owner_code": owner_code,
                    },
                    recommendation="Sprawdź flagi ownership i ścieżkę stop/restore.",
                )
            )

    execution_ready = context_state(context, "hoymiles_ems_execution_ready")
    for observation in observations:
        if (
            observation.active is True
            and observation.controller in {Controller.RCE, Controller.TARIFF}
            and execution_ready == "off"
        ):
            findings.append(
                _finding(
                    "SYS_ACTIVE_WITHOUT_EXECUTION_READY",
                    Severity.CRITICAL,
                    "Sterowanie jest aktywne mimo wyłączonej gotowości EMS.",
                    observation,
                    evidence={"assessment": "confirmed"},
                    recommendation="Natychmiast zatrzymaj cykl i sprawdź gate/readback.",
                )
            )


def _optimizer_common(
    observation: ControllerObservation,
    findings: list[Finding],
) -> None:
    if observation.enabled is True or observation.active is True:
        required_coverage = ["active_helper", "owner", "result_current"]
        if observation.controller is not Controller.RCEM:
            required_coverage.append("ems_execution_ready")
        missing = [
            key
            for key in required_coverage
            if observation.coverage.get(key) is not None
            and observation.coverage[key].value != "present"
        ]
        # Older analyzer payloads may not have populated coverage at all.  In
        # that case the absence is itself explicitly non-evaluable.
        missing.extend(
            key for key in required_coverage if key not in observation.coverage
        )
        if missing:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    f"Brakuje dowodów wykonawczych dla {observation.controller.value}.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": sorted(set(missing)),
                    },
                    recommendation=(
                        "Sprawdź kompletność managed snapshotu i ponów paczkę; "
                        "brak pola nie oznacza stanu OFF."
                    ),
                )
            )
    if observation.status_code == "optimizer_error":
        findings.append(
            _finding(
                f"{observation.controller.value.upper()}_OPTIMIZER_ERROR",
                Severity.CRITICAL if observation.active is True else Severity.ERROR,
                f"Planer {observation.controller.value} zakończył się wyjątkiem.",
                observation,
                evidence={"assessment": "confirmed"},
                recommendation="Sprawdź log cluster i komplet wejść z tej samej paczki.",
            )
        )
    elif observation.status_code == "missing_data":
        findings.append(
            _finding(
                f"{observation.controller.value.upper()}_MISSING_DATA",
                Severity.CRITICAL if observation.active is True else Severity.ERROR,
                f"Planer {observation.controller.value} nie ma wymaganych danych.",
                observation,
                evidence={"assessment": "confirmed"},
                recommendation="Sprawdź missing_entities i jawne freshness/reasons.",
            )
        )
    if observation.result_current is False:
        findings.append(
            _finding(
                f"{observation.controller.value.upper()}_RESULT_STALE",
                Severity.CRITICAL if observation.active is True else Severity.WARNING,
                f"Opublikowany wynik {observation.controller.value} nie odpowiada bieżącym wejściom.",
                observation,
                confidence=Confidence.MEDIUM,
                evidence={
                    "assessment": "suspected",
                    "recalculation_pending": _value(
                        observation, "recalculation_pending"
                    ),
                },
                recommendation="Pobierz kolejną paczkę po 5–30 s i sprawdź recovery.",
            )
        )


def _evaluate_rce(
    observation: ControllerObservation,
    context: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    _optimizer_common(observation, findings)
    metrics = observation.metrics
    flags = observation.flags
    sale_block = context_state(context, "hoymiles_sale_block_active") == "on"
    if observation.active is True and sale_block:
        findings.append(
            _finding(
                "RCE_ACTIVE_DURING_SALE_BLOCK",
                Severity.CRITICAL,
                "RCE pozostaje aktywne podczas blokady sprzedaży.",
                observation,
                evidence={"assessment": "confirmed"},
                recommendation="Zatrzymaj eksport i sprawdź automatykę sale-block.",
            )
        )
    if observation.active is True and observation.planned is not True:
        findings.append(
            _finding(
                "RCE_ACTIVE_WITHOUT_PLAN",
                Severity.CRITICAL,
                "Rozładowanie RCE jest aktywne bez bieżącego wybranego slotu.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "suppression_reason": observation.suppression_reason,
                    "continue_reason": observation.continue_reason,
                },
                recommendation="Wykonaj neutralny rollback i sprawdź continuation gate.",
            )
        )
    if (
        observation.active is True
        and observation.flags.get("current_slot_continue_eligible") is False
    ):
        findings.append(
            _finding(
                "RCE_CONTINUATION_BLOCKED_WHILE_ACTIVE",
                Severity.CRITICAL,
                "RCE pozostaje aktywne mimo utraty prawa kontynuacji slotu.",
                observation,
                confidence=Confidence.MEDIUM,
                evidence={
                    "assessment": "suspected",
                    "continue_eligible": False,
                    "continue_reason": observation.continue_reason,
                },
                recommendation=(
                    "Sprawdź natychmiastowy rollback; ponów paczkę po kilku "
                    "sekundach, aby wykluczyć chwilę przejścia."
                ),
            )
        )
    elif (
        observation.active is True
        and "current_slot_continue_eligible" not in observation.flags
    ):
        findings.append(
            _finding(
                "CONTROLLER_EVIDENCE_INCOMPLETE",
                Severity.WARNING,
                "Brak prawa kontynuacji dla aktywnego slotu RCE.",
                observation,
                evidence={
                    "assessment": "not_evaluable",
                    "missing_evidence": ["current_slot_continue_eligible"],
                },
            )
        )
    if observation.active is True:
        required_freshness = (
            "bms_discharge_data_fresh",
            "gcf_execution_data_fresh",
            "soc_data_fresh",
        )
        stale = [key for key in required_freshness if flags.get(key) is False]
        missing_freshness = [
            key for key in required_freshness if key not in flags
        ]
        if stale or observation.result_current is False:
            findings.append(
                _finding(
                    "RCE_INPUTS_STALE_WHILE_ACTIVE",
                    Severity.CRITICAL,
                    "RCE jest aktywne przy nieaktualnych wejściach sterujących.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "stale_inputs": stale,
                    },
                    recommendation="Zatrzymaj cykl i sprawdź źródła freshness/readback.",
                )
            )
        if missing_freshness:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak safety-freshness dla aktywnego RCE.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": missing_freshness,
                    },
                    recommendation="Ponów pełną paczkę przed oceną aktywnego eksportu.",
                )
            )

    requested = finite_number(metrics.get("requested_export_power_kw"))
    bms = finite_number(metrics.get("bms_discharge_power_limit_kw"))
    gcf = finite_number(metrics.get("export_power_cap_kw"))
    effective = finite_number(metrics.get("effective_export_power_kw"))
    actual = finite_number(metrics.get("maximum_export_power_kw"))
    source = metrics.get("physical_limit_source")
    limits: list[tuple[str, float]] = []
    if requested is not None:
        limits.append(("requested_power", max(requested, 0.0)))
    # A numeric 0 kW BMS limit is the deliberate fail-closed minimum when the
    # live BMS input is stale or unavailable.  Dropping it from this set would
    # falsely accuse a safe suppressed plan of violating the limit contract.
    if bms is not None and (
        flags.get("bms_discharge_data_available") is not False
        or bms <= POWER_TOLERANCE_KW
    ):
        limits.append(("bms", max(bms, 0.0)))
    if gcf is not None:
        limits.append(("gcf_export_cap", max(gcf, 0.0)))
    if effective is not None:
        limits.append(("effective_export_power", max(effective, 0.0)))
    if actual is not None and limits:
        expected_source, expected = min(limits, key=lambda item: item[1])
        tied_sources = {
            name for name, value in limits if abs(value - expected) <= POWER_TOLERANCE_KW
        }
        mismatch = abs(actual - expected) > POWER_TOLERANCE_KW
        source_mismatch = isinstance(source, str) and source not in tied_sources
        if mismatch or source_mismatch:
            severity = Severity.CRITICAL if actual > expected else Severity.ERROR
            findings.append(
                _finding(
                    "RCE_PHYSICAL_LIMIT_INCONSISTENT",
                    severity,
                    "Limit mocy RCE jest niespójny z minimum ograniczeń fizycznych.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "limits_kw": dict(limits),
                        "expected_maximum_kw": expected,
                        "expected_source": expected_source,
                        "actual_maximum_kw": actual,
                        "actual_source": source,
                    },
                    recommendation="Sprawdź adapter BMS/GCF i mapowanie physical_limit_source.",
                )
            )
    if (
        flags.get("bms_discharge_data_fresh") is True
        and flags.get("bms_discharge_data_available") is True
        and bms is not None
        and bms > POWER_TOLERANCE_KW
        and actual is not None
        and actual <= POWER_TOLERANCE_KW
        and source == "bms"
    ):
        findings.append(
            _finding(
                "RCE_BMS_POSITIVE_BUT_EXPORT_ZERO",
                Severity.ERROR,
                "Świeży dodatni limit BMS został opisany jako zerowy limit eksportu.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "bms_limit_kw": bms,
                    "maximum_export_power_kw": actual,
                    "physical_limit_source": source,
                },
                recommendation="Prześledź źródło BMS i obliczenie minimum przed solverem.",
            )
        )

    ending = finite_number(metrics.get("ending_battery_kwh"))
    floor = finite_number(metrics.get("base_reserve_energy_kwh"))
    planned_export = finite_number(metrics.get("planned_export_kwh"))
    executable_plan = bool(
        observation.status_code == "ready"
        and observation.result_current is True
        and planned_export is not None
        and planned_export > 0.02
    )
    if (
        executable_plan
        and ending is not None
        and floor is not None
        and ending + 0.05 < floor
    ):
        findings.append(
            _finding(
                "RCE_BASE_RESERVE_VIOLATION",
                Severity.CRITICAL,
                "Plan RCE kończy horyzont poniżej twardej rezerwy energii.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "ending_battery_kwh": ending,
                    "base_reserve_energy_kwh": floor,
                },
                recommendation="Zablokuj wykonanie i odtwórz przypadek w optimizer test.",
            )
        )
    elif observation.status_code == "home_energy_shortage":
        findings.append(
            _finding(
                "RCE_HOME_ENERGY_SHORTAGE",
                Severity.WARNING,
                "Domowe zapotrzebowanie przekracza energię dostępną ponad rezerwę.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "planned_export_kwh": planned_export,
                    "ending_battery_kwh": ending,
                    "base_reserve_energy_kwh": floor,
                },
                recommendation=(
                    "To bezpieczna blokada eksportu; sprawdź prognozę LOAD, PV i rezerwę."
                ),
            )
        )
    quality_score = finite_number(metrics.get("data_quality_score"))
    quality_issues = observation.details.get("data_quality_issues")
    if (quality_score is not None and quality_score < 75.0) or quality_issues:
        findings.append(
            _finding(
                "RCE_LOW_DATA_QUALITY",
                Severity.WARNING,
                "RCE pracuje na danych o obniżonej jakości.",
                observation,
                confidence=Confidence.HIGH,
                evidence={
                    "assessment": "confirmed",
                    "score": quality_score,
                    "issues": quality_issues,
                },
                recommendation="Porównaj freshness, historię LOAD i prognozy Solcast.",
            )
        )


def _evaluate_rcem(
    observation: ControllerObservation,
    context: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    _optimizer_common(observation, findings)
    live_emergency = strict_bool(_value(observation, "live_emergency"))
    emergency_ready = strict_bool(_value(observation, "emergency_action_ready"))
    emergency_fresh = strict_bool(
        _value(observation, "emergency_voltage_data_fresh")
    )
    maximum_voltage = finite_number(_value(observation, "maximum_voltage_v"))
    shadow = strict_bool(_value(observation, "shadow_mode"))
    if (
        emergency_fresh is True
        and maximum_voltage is not None
        and maximum_voltage >= 253.0
        and live_emergency is False
    ):
        findings.append(
            _finding(
                "RCEM_EMERGENCY_STATE_INCONSISTENT",
                Severity.CRITICAL,
                "Świeże napięcie emergency nie zgadza się z flagą live_emergency.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "maximum_voltage_v": maximum_voltage,
                    "emergency_voltage_data_fresh": True,
                    "live_emergency": False,
                },
                recommendation="Sprawdź próg 253 V i publikację stanu RCEm.",
            )
        )
    if live_emergency is True and emergency_ready is False:
        findings.append(
            _finding(
                "RCEM_EMERGENCY_UNHANDLED",
                Severity.CRITICAL,
                "RCEm wykryło emergency napięciowe bez gotowej reakcji wykonawczej.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "maximum_voltage_v": _value(observation, "maximum_voltage_v"),
                    "emergency_action_ready": emergency_ready,
                },
                recommendation="Sprawdź direct-register gate, freshness 306/258/259 i owner.",
            )
        )
    elif live_emergency is True and emergency_ready is None:
        findings.append(
            _finding(
                "CONTROLLER_EVIDENCE_INCOMPLETE",
                Severity.WARNING,
                "Brak pola gotowości aktuatora dla aktywnego emergency RCEm.",
                observation,
                evidence={
                    "assessment": "not_evaluable",
                    "missing_evidence": ["emergency_action_ready"],
                    "maximum_voltage_v": maximum_voltage,
                },
                recommendation="Wygeneruj paczkę z pełnymi atrybutami RCEm.",
            )
        )
    if shadow is True and observation.active is True:
        findings.append(
            _finding(
                "RCEM_SHADOW_WRITE_CONFLICT",
                Severity.CRITICAL,
                "RCEm ma aktywnego właściciela wykonawczego w trybie shadow.",
                observation,
                evidence={"assessment": "confirmed"},
                recommendation="Zatrzymaj transakcję i sprawdź shadow-mode interlock.",
            )
        )
    charge_active = context_state(context, "hoymiles_rcm_active") == "on"
    export_active = (
        context_state(context, "hoymiles_rcm_export_control_active") == "on"
    )
    pre_discharge_active = (
        context_state(context, "hoymiles_rcm_pre_discharge_active") == "on"
    )
    direct_ready = context_state(
        context, "hoymiles_direct_register_execution_ready"
    )
    if (charge_active or export_active) and direct_ready == "off":
        findings.append(
            _finding(
                "RCEM_DIRECT_REGISTER_GATE_BLOCKED",
                Severity.CRITICAL,
                "RCEm ma aktywnego właściciela przy wyłączonym gate rejestrów 258/259/306.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "direct_register_execution_ready": "off",
                    "charge_active": charge_active,
                    "export_active": export_active,
                    "pre_discharge_active": pre_discharge_active,
                },
                recommendation="Zatrzymaj właściciela RCEm i sprawdź readback/topologię.",
            )
        )
    elif charge_active or export_active:
        if direct_ready is None:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak stanu direct-register gate dla aktywnego toru RCEm.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": [
                            "direct_register_execution_ready"
                        ],
                    },
                )
            )
    if pre_discharge_active:
        ems_ready = context_state(context, "hoymiles_ems_execution_ready")
        if ems_ready == "off":
            findings.append(
                _finding(
                    "RCEM_PRE_DISCHARGE_EMS_GATE_BLOCKED",
                    Severity.CRITICAL,
                    "RCEm pre-discharge jest aktywne przy wyłączonym gate EMS.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "ems_execution_ready": "off",
                    },
                    recommendation="Wykonaj rollback pre-discharge i sprawdź FC03/topologię.",
                )
            )
        elif ems_ready is None:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak stanu EMS gate dla aktywnego pre-discharge RCEm.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": ["ems_execution_ready"],
                    },
                )
            )
    if observation.active is True:
        required_by_path: dict[str, tuple[str, ...]] = {}
        if charge_active:
            required_by_path["charge"] = (
                "charge_actuator_data_fresh",
                "bms_charge_data_fresh",
            )
        if export_active:
            required_by_path["export"] = (
                "export_actuator_data_fresh",
                "export_register_data_fresh",
                "gcf_data_fresh",
            )
        if pre_discharge_active:
            required_by_path["pre_discharge"] = (
                "pre_discharge_actuator_data_fresh",
                "discharge_registers_data_fresh",
                "ems_mode_data_fresh",
                "bms_discharge_data_fresh",
                "voltage_data_fresh",
                "gcf_data_fresh",
            )
        stale_by_path = {
            path: [
                key
                for key in required
                if observation.flags.get(key) is False
            ]
            for path, required in required_by_path.items()
        }
        stale_by_path = {
            path: keys for path, keys in stale_by_path.items() if keys
        }
        stale = sorted(
            {key for keys in stale_by_path.values() for key in keys}
        )
        missing_by_path = {
            path: [key for key in required if key not in observation.flags]
            for path, required in required_by_path.items()
        }
        missing_by_path = {
            path: keys for path, keys in missing_by_path.items() if keys
        }
        if stale:
            findings.append(
                _finding(
                    "RCEM_ACTIVE_WITH_STALE_ACTUATOR",
                    Severity.CRITICAL,
                    "RCEm jest aktywne przy nieświeżych danych wykonawczych.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "stale_inputs": stale,
                        "stale_by_path": stale_by_path,
                    },
                    recommendation="Przerwij zapis i sprawdź readback/freshness.",
                )
            )
        if missing_by_path:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak branch-specific freshness dla aktywnego RCEm.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_by_path": missing_by_path,
                    },
                    recommendation="Ponów paczkę z pełnymi atrybutami aktuatorów.",
                )
            )
        if (
            pre_discharge_active
            and observation.flags.get("pre_discharge_continue_eligible") is False
        ):
            findings.append(
                _finding(
                    "RCEM_PRE_DISCHARGE_CONTINUATION_BLOCKED",
                    Severity.CRITICAL,
                    "RCEm pre-discharge pozostaje aktywne po utracie prawa kontynuacji.",
                    observation,
                    confidence=Confidence.MEDIUM,
                    evidence={
                        "assessment": "suspected",
                        "pre_discharge_continue_eligible": False,
                    },
                    recommendation=(
                        "Sprawdź rollback pre-discharge i ponów paczkę po kilku sekundach."
                    ),
                )
            )
        elif (
            pre_discharge_active
            and "pre_discharge_continue_eligible" not in observation.flags
        ):
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak prawa kontynuacji aktywnego RCEm pre-discharge.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": [
                            "pre_discharge_continue_eligible"
                        ],
                    },
                )
            )
    prediction_ready = strict_bool(_value(observation, "prediction_ready"))
    enabled = observation.enabled
    if enabled is True and prediction_ready is False:
        findings.append(
            _finding(
                "RCEM_PREDICTION_DEGRADED",
                Severity.WARNING,
                "Predykcja RCEm jest wstrzymana lub zdegradowana.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "block_reason": observation.block_reason,
                    "history_days": _value(observation, "history_days"),
                },
                recommendation="Sprawdź Recorder, prognozę, capacity i live freshness.",
            )
        )
    shortfall = finite_number(_value(observation, "headroom_shortfall_kwh"))
    if shortfall is not None and shortfall > 0.05:
        findings.append(
            _finding(
                "RCEM_HEADROOM_SHORTFALL",
                Severity.WARNING,
                "RCEm nie może utworzyć całego wymaganego headroomu.",
                observation,
                evidence={"assessment": "confirmed", "shortfall_kwh": shortfall},
                recommendation="Sprawdź rezerwę domu, BMS i czas do okna ryzyka.",
            )
        )
    if (
        strict_bool(_value(observation, "pre_discharge_ready")) is True
        and strict_bool(_value(observation, "pre_discharge_transaction_ready"))
        is False
    ):
        findings.append(
            _finding(
                "RCEM_PRE_DISCHARGE_NOT_EXECUTABLE",
                Severity.ERROR if observation.active is True else Severity.WARNING,
                "RCEm planuje pre-discharge, ale transakcja nie jest wykonawczo gotowa.",
                observation,
                evidence={"assessment": "confirmed"},
                recommendation="Sprawdź EMS/direct-register gates oraz BMS discharge.",
            )
        )

    if observation.active is True and shadow is not True:
        charge_target = finite_number(
            _value(observation, "recommended_charge_limit_percent")
        )
        charge_actual = _context_numeric_state(
            context, "hoymiles_hit_battery_max_charge_power_readback"
        )
        charge_age = _context_active_age_seconds(
            context,
            "hoymiles_rcm_active",
            observation.observed_at,
        )
        if (
            charge_active
            and charge_age is not None
            and charge_age >= 120.0
            and charge_target is not None
            and charge_actual is not None
            and abs(charge_target - charge_actual) >= READBACK_TOLERANCE_PERCENT
        ):
            findings.append(
                _finding(
                    "RCEM_RECOMMENDATION_READBACK_MISMATCH",
                    Severity.WARNING,
                    "Readback limitu ładowania nie odpowiada rekomendacji RCEm.",
                    observation,
                    confidence=Confidence.MEDIUM,
                    evidence={
                        "assessment": "suspected",
                        "active_age_seconds": charge_age,
                        "target_percent": charge_target,
                        "readback_percent": charge_actual,
                    },
                    recommendation="Ponów paczkę po 2–3 min i sprawdź write generation.",
                )
            )
        export_target = finite_number(
            _value(observation, "recommended_export_limit_percent")
        )
        export_actual = _context_numeric_state(
            context, "hoymiles_hit_gcf_maximum_export_power_readback"
        )
        export_age = _context_active_age_seconds(
            context,
            "hoymiles_rcm_export_control_active",
            observation.observed_at,
        )
        if (
            export_active
            and export_age is not None
            and export_age >= 120.0
            and export_target is not None
            and export_actual is not None
            and abs(export_target - export_actual) >= READBACK_TOLERANCE_PERCENT
        ):
            findings.append(
                _finding(
                    "RCEM_EXPORT_RECOMMENDATION_READBACK_MISMATCH",
                    Severity.WARNING,
                    "Readback limitu eksportu nie odpowiada rekomendacji RCEm.",
                    observation,
                    confidence=Confidence.MEDIUM,
                    evidence={
                        "assessment": "suspected",
                        "actuator": "gcf_export_limit",
                        "active_age_seconds": export_age,
                        "target_percent": export_target,
                        "readback_percent": export_actual,
                    },
                    recommendation="Ponów paczkę po 2–3 min i sprawdź write generation.",
                )
            )


def _evaluate_tariff(
    observation: ControllerObservation,
    context: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    _optimizer_common(observation, findings)
    status = observation.status_code
    if status in {
        "soc_limits_conflict",
        "hard_reserve_unavailable",
        "unsupported_profile",
        "expired_profile",
    }:
        findings.append(
            _finding(
                "TARIFF_RESERVE_UNAVAILABLE"
                if status == "hard_reserve_unavailable"
                else "TARIFF_CONFIGURATION_BLOCKED",
                Severity.ERROR,
                "Tani plan ładowania jest zablokowany przez konfigurację lub rezerwę.",
                observation,
                evidence={"assessment": "confirmed", "status_code": status},
                recommendation="Sprawdź profil taryfy, limity SOC i hard reserve.",
            )
        )
    elif status in {"insufficient_cheap_window", "no_cheap_window"}:
        findings.append(
            _finding(
                "TARIFF_INSUFFICIENT_WINDOW",
                Severity.WARNING,
                "Taryfa nie znalazła wystarczającego taniego okna.",
                observation,
                evidence={"assessment": "confirmed", "status_code": status},
                recommendation="Sprawdź harmonogram, moc BMS i wymagany target SOC.",
            )
        )
    if observation.active is True:
        if observation.planned is not True or observation.action in {None, "none"}:
            findings.append(
                _finding(
                    "TARIFF_ACTIVE_WITHOUT_PLAN",
                    Severity.CRITICAL,
                    "Ładowanie taryfowe jest aktywne bez bieżącego planu/akcji.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "action": observation.action,
                        "planned": observation.planned,
                    },
                    recommendation="Wykonaj rollback do Self-Use i sprawdź slot continuation.",
                )
            )
        if observation.flags.get("current_run_continue_eligible") is False:
            findings.append(
                _finding(
                    "TARIFF_CONTINUATION_BLOCKED_WHILE_ACTIVE",
                    Severity.CRITICAL,
                    "Ładowanie taryfowe pozostaje aktywne po utracie prawa kontynuacji.",
                    observation,
                    confidence=Confidence.MEDIUM,
                    evidence={
                        "assessment": "suspected",
                        "continue_eligible": False,
                        "continue_reason": observation.continue_reason,
                    },
                    recommendation=(
                        "Sprawdź rollback cyklu i ponów paczkę po kilku sekundach."
                    ),
                )
            )
        elif "current_run_continue_eligible" not in observation.flags:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak prawa kontynuacji dla aktywnego cyklu taryfowego.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": ["current_run_continue_eligible"],
                    },
                )
            )
        if strict_bool(_value(observation, "control_inputs_fresh")) is False:
            findings.append(
                _finding(
                    "TARIFF_ACTIVE_WITH_STALE_INPUTS",
                    Severity.CRITICAL,
                    "Ładowanie taryfowe jest aktywne przy nieświeżych wejściach.",
                    observation,
                    evidence={
                        "assessment": "confirmed",
                        "block_reason": observation.block_reason,
                    },
                    recommendation="Zatrzymaj cykl i sprawdź SOC/BMS/LOAD/forecast freshness.",
                )
            )
        elif "control_inputs_fresh" not in observation.flags:
            findings.append(
                _finding(
                    "CONTROLLER_EVIDENCE_INCOMPLETE",
                    Severity.WARNING,
                    "Brak zbiorczej świeżości wejść aktywnego cyklu taryfowego.",
                    observation,
                    evidence={
                        "assessment": "not_evaluable",
                        "missing_evidence": ["control_inputs_fresh"],
                    },
                    recommendation="Ponów paczkę z pełnymi atrybutami taryfy.",
                )
            )
        zone = _value(observation, "current_zone")
        if (
            observation.action
            in {"battery_charge", "grid_support", "grid_support_and_charge"}
            and isinstance(zone, str)
            and zone not in {"low", "cheap"}
        ):
            findings.append(
                _finding(
                    "TARIFF_ACTIVE_OUTSIDE_LOW_ZONE",
                    Severity.ERROR,
                    "Ładowanie baterii trwa poza tanią strefą taryfową.",
                    observation,
                    evidence={"assessment": "confirmed", "current_zone": zone},
                    recommendation="Sprawdź profil stref i granice slotu/DST.",
                )
            )

    negative_fields = {
        key: finite_number(_value(observation, key))
        for key in (
            "planned_grid_import_kwh",
            "planned_stored_energy_kwh",
            "planned_direct_load_kwh",
        )
    }
    invalid_negative = {
        key: value
        for key, value in negative_fields.items()
        if value is not None and value < -0.001
    }
    target = finite_number(_value(observation, "target_soc_percent"))
    maximum = finite_number(
        _value(observation, "model_input_maximum_soc_percent")
    )
    target_invalid = (
        target is not None and maximum is not None and target > maximum + 0.2
    )
    if invalid_negative or target_invalid:
        findings.append(
            _finding(
                "TARIFF_PLAN_INCONSISTENT",
                Severity.CRITICAL if target_invalid else Severity.ERROR,
                "Plan taryfowy narusza podstawowe inwarianty energii/SOC.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "negative_energy_fields": invalid_negative,
                    "target_soc_percent": target,
                    "maximum_soc_percent": maximum,
                },
                recommendation="Zablokuj wykonanie i odtwórz przypadek w testach optimizer.",
            )
        )
    factor = finite_number(
        _value(observation, "charge_power_feedback_applied_factor")
    )
    samples = finite_number(
        _value(observation, "effective_charge_power_feedback_samples")
    )
    feedback_ready = strict_bool(
        _value(observation, "charge_power_feedback_ready")
    )
    enough_evidence = bool(
        feedback_ready is True
        or (samples is not None and samples >= 5)
    )
    if (
        factor is not None
        and samples is not None
        and enough_evidence
        and factor < 0.75
    ):
        findings.append(
            _finding(
                "TARIFF_DELIVERY_UNDERPERFORMING",
                Severity.ERROR if factor < 0.60 else Severity.WARNING,
                "Rzeczywista moc taniego ładowania jest trwale niższa od modelu.",
                observation,
                evidence={
                    "assessment": "confirmed",
                    "effective_power_factor": factor,
                    "sample_count": int(samples),
                },
                recommendation="Skalibruj efektywną moc i sprawdź BMS/inverter/readback.",
            )
        )


def _evaluate_logs(
    archive: LoadedDiagnosticArchive,
    log_counts: Mapping[str, int],
    findings: list[Finding],
) -> None:
    mapping = {
        "optimizer_exception": (
            "LOG_OPTIMIZER_EXCEPTION",
            Severity.ERROR,
            "Logi zawierają wyjątki optimizerów.",
        ),
        "modbus_communication": (
            "LOG_MODBUS_COMMUNICATION",
            Severity.ERROR,
            "Logi zawierają błędy komunikacji Modbus/ESPHome.",
        ),
        "readback_failure": (
            "LOG_READBACK_FAILURE",
            Severity.ERROR,
            "Logi zawierają nieudane potwierdzenia readback.",
        ),
        "rollback_failure": (
            "LOG_ROLLBACK_FAILURE",
            Severity.CRITICAL,
            "Logi zawierają nieudany rollback sterowania.",
        ),
    }
    for category, (code, severity, message) in mapping.items():
        count = int(log_counts.get(category, 0))
        if count <= 0:
            continue
        findings.append(
            _finding(
                code,
                severity,
                message,
                archive=archive,
                confidence=Confidence.HIGH,
                evidence={"assessment": "confirmed", "occurrences": count},
                recommendation="Skoreluj kategorię z timeline i bieżącymi gates.",
            )
        )


def evaluate_longitudinal(
    observations: Iterable[ControllerObservation],
) -> tuple[Finding, ...]:
    """Escalate defects repeated across independently captured packages."""
    grouped: dict[tuple[str, Controller], list[ControllerObservation]] = defaultdict(list)
    for observation in observations:
        grouped[(observation.installation_key, observation.controller)].append(
            observation
        )
    findings: list[Finding] = []
    for (_installation, controller), items in grouped.items():
        ordered = sorted(
            items,
            key=lambda item: (
                item.observed_at.isoformat() if item.observed_at else "",
                item.archive_key,
            ),
        )
        stale = [item for item in ordered if item.result_current is False]
        if len(stale) >= 2:
            last = stale[-1]
            findings.append(
                _finding(
                    f"{controller.value.upper()}_RESULT_STALE_PERSISTENT",
                    Severity.ERROR,
                    f"Nieaktualny wynik {controller.value} powtórzył się w wielu paczkach.",
                    last,
                    confidence=Confidence.HIGH,
                    evidence={
                        "assessment": "confirmed",
                        "capture_count": len(stale),
                        "first_seen": _iso(stale[0].observed_at),
                        "last_seen": _iso(last.observed_at),
                    },
                    recommendation="Sprawdź starvation/ping-pong i źródła input revision.",
                )
            )
        errors = [item for item in ordered if item.status_code == "optimizer_error"]
        if len(errors) >= 2:
            last = errors[-1]
            findings.append(
                _finding(
                    f"{controller.value.upper()}_OPTIMIZER_ERROR_PERSISTENT",
                    Severity.ERROR,
                    f"Wyjątek {controller.value} powtórzył się w wielu paczkach.",
                    last,
                    evidence={
                        "assessment": "confirmed",
                        "capture_count": len(errors),
                        "first_seen": _iso(errors[0].observed_at),
                        "last_seen": _iso(last.observed_at),
                    },
                    recommendation="Porównaj wspólne wejścia i cluster logów między paczkami.",
                )
            )
    return tuple(findings)


def _controller_from_value(value: Any) -> Controller | None:
    try:
        return Controller(str(value))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
