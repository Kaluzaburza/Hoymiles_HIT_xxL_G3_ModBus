# Repository operating guide

## Scope

- This root guide applies to the whole repository. Do not add nested `AGENTS.md` files unless a later task needs genuinely different local rules.
- This is an operating map, not a replacement for the README, safety documentation, release procedure, or tests.
- Inspect the relevant implementation, tests, and current working-tree state before editing. Preserve unrelated user changes in a dirty tree.
- Distinguish `implemented`, `validated offline`, `field/live accepted`, and `publicly released`. Code, a version number, or a changelog entry proves none of the later states by itself.
- Determine release status from current release notes, `RELEASING.md`, CI, and documented field evidence. If sources conflict, use the more conservative status, report the discrepancy, and do not silently rewrite documentation.

## Core operating rules

### Think before coding

- Do not assume. Establish the observed failure mode and the success criterion first.
- Separate confirmed facts from hypotheses; expose uncertainty, conflicting evidence, and material tradeoffs.
- Read the relevant implementation and regression tests before proposing a fix.
- For field observations, record versions, timestamps, source entities, freshness, and physical evidence before drawing conclusions.

### Simplicity first

- Implement the minimum change that solves the confirmed task. Prefer KISS and apply YAGNI aggressively.
- Do not add speculative abstractions, fallbacks, state machines, generalized frameworks, or future features.
- A theoretically cleaner architecture is not a reason to refactor working production code.
- Use SRP and existing authoritative sources. Apply DRY where the repository already defines generation or shared contracts; do not refactor merely to remove harmless duplication.

### Surgical, goal-driven changes

- Touch only files required by the task. Do not rename, reformat, clean up, or refactor unrelated code.
- Preserve public behavior unless the task explicitly requires changing it. Clean up only problems introduced by your change.
- Know the acceptance condition before implementation and run validation proportional to the changed subsystem and risk.
- Continue until the requested change and relevant tests are green. An unrelated failure is not permission to expand scope: determine causality and report it separately.

## Project purpose and safety boundary

This project is an independent local EMS for Hoymiles HIT-(5–20)L-G3 hybrid inverters, built with Home Assistant, ESPHome, and Modbus. It covers local monitoring/control, RCE energy-market planning, tariff-aware charging, experimental RCEm voltage-related management, PV/LOAD forecasting, battery/SOC planning, diagnostics, the Aurora dashboard, and ESPHome/Modbus transport.

> The EMS manages energy; it does not manage electrical or grid safety.

- Never silently expand EMS authority into certified grid protection, inverter protection thresholds, electrical-installation protection, or regulatory/certification claims.
- The project is not a certification body, protection relay, substitute for manufacturer declarations, grid approval, or electrical commissioning.
- Do not automate certified grid profiles, protection settings, Q(U), P(U), power factor, or three-phase imbalance unless the repository explicitly and intentionally changes this boundary.

## Where to look

| Area | Verified entry points |
| --- | --- |
| Integration lifecycle and HA runtime | `custom_components/hoymiles_hit_modbus/__init__.py`, `custom_components/hoymiles_hit_modbus/sensor.py`, `custom_components/hoymiles_hit_modbus/entity.py`, `custom_components/hoymiles_hit_modbus/source_device.py`, `custom_components/hoymiles_hit_modbus/models.py` |
| RCE planning and orchestration | `custom_components/hoymiles_hit_modbus/rce_optimizer.py`, `custom_components/hoymiles_hit_modbus/rce_sensor.py`, `custom_components/hoymiles_hit_modbus/rce_history.py` |
| Tariff planning and orchestration | `custom_components/hoymiles_hit_modbus/tariff_optimizer.py`, `custom_components/hoymiles_hit_modbus/tariff_sensor.py`, `custom_components/hoymiles_hit_modbus/tariff_profiles.py` |
| RCEm planning and orchestration | `custom_components/hoymiles_hit_modbus/rcm_optimizer.py`, `custom_components/hoymiles_hit_modbus/rcm_sensor.py`, `custom_components/hoymiles_hit_modbus/rcm_history.py` (historical `rcm` filename spelling) |
| Forecast, energy, LOAD, and balance models | `custom_components/hoymiles_hit_modbus/forecast_model.py`, `custom_components/hoymiles_hit_modbus/energy_data.py`, `custom_components/hoymiles_hit_modbus/load_model.py`, `custom_components/hoymiles_hit_modbus/power_balance.py` |
| Stale-result protection | `custom_components/hoymiles_hit_modbus/optimizer_revision.py` |
| Diagnostics and support ZIP | `custom_components/hoymiles_hit_modbus/diagnostics.py`, `custom_components/hoymiles_hit_modbus/diagnostic_bundle.py`, `custom_components/hoymiles_hit_modbus/diagnostic_redaction.py`, `custom_components/hoymiles_hit_modbus/support_http.py` |
| Persistent anonymous diagnostic identity | `custom_components/hoymiles_hit_modbus/installation_identity.py` |
| ESPHome/Modbus firmware | `hoymiles-inverter.yaml`, `examples/esphome/`, and `packages/` |
| EMS block `4300–4306` | `packages/settings.yaml` |
| Parallel topology and capabilities | `packages/parallel_network.yaml`; aggregate balance in `packages/backup_load.yaml` and `packages/overview.yaml` |
| Managed HA scheduler/package | `home_assistant/hoymiles_ems_scheduler.yaml` |
| Aurora dashboard and frontend sources | `dashboard_hoymiles.yaml`, `home_assistant/www/` |
| Asset generation | `tools/build_hacs_assets.py` |
| Tests and validation | `tools/`, especially `tools/validate_release.py` and `tools/validate_rce_card.js` |
| Offline diagnostic analyzer | `tools/analyze_diagnostic_bundles.py`, `tools/diagnostics_analysis/` |
| Release-specific documentation | `docs/releases/` |

## Sources of truth

- `README.md` and `README.pl.md`: public purpose, architecture, installation, operation, and user-facing boundaries.
- `docs/SAFETY_AND_COMPLIANCE.md`: safety, certification, commissioning, and evidence boundaries.
- `RELEASING.md`: maintainer procedure, user update steps, release gates, and field-acceptance requirements.
- `docs/AUTOMATION_TEST_REPORT.md`: recorded deterministic and field evidence, exact version/SHA scope, and limitations; it is not a certificate.
- `docs/DIAGNOSTICS.md`: support-bundle content, privacy, and interpretation.
- `docs/DIAGNOSTICS_ANALYZER.md`: offline batch-analyzer inputs, outputs, limits, evidence scope, and exit codes.
- The newest applicable file in `docs/releases/`: version-specific scope and status. Cross-check it with `RELEASING.md`, `.github/workflows/validate.yml`, and field evidence.
- `.github/workflows/validate.yml`: the executable CI baseline. For a release gate, use the union of CI and `RELEASING.md` when their command lists differ.

## Critical engineering invariants

### Fail closed, with provenance

- Missing, unavailable, non-numeric, non-finite, future-dated, stale, incoherent, or unverifiable control-critical inputs do not grant execution authority. A reported zero capability is a real zero, never “unlimited.”
- Preserve value, availability, freshness, and source/provenance separately. A plausible number without the required age and physical source is insufficient (`custom_components/hoymiles_hit_modbus/energy_data.py`, `custom_components/hoymiles_hit_modbus/entity.py`, scheduler readiness gates).

### Physical readback and EMS writes

- Requested state and HA/ESPHome command echo are not physical acknowledgement. Success requires a newer physical FC03 generation and matching readback.
- Every EMS mode change writes the complete `4300–4306` block from a complete, range-valid, fresh FC03 snapshot; never write only `4300`.
- A single inverter uses addressed FC16. A parallel Master with a validated count uses one address-0 FC16 broadcast; a Slave or invalid topology is blocked.
- The later Master FC03 confirms only Master configuration. Aggregate system response is not individual Slave protocol acknowledgement.
- ESP32, Master, and every Slave must share the external RS485 multidrop for broadcast delivery. Registers `258`, `259`, and `306` are outside the EMS block and retain separate fail-closed restrictions on parallel systems.
- Preserve these contracts in `packages/settings.yaml`, `packages/parallel_network.yaml`, `home_assistant/hoymiles_ems_scheduler.yaml`, `tools/test_firmware_readback_contract.py`, and `tools/test_automation_matrix.py`.

### Optimizers and ownership

- Optimizer input revision and consumed-field fingerprints protect executor-backed calculations. If either changes during calculation, discard the stale result, set `result_current=false`/recalculation pending, and withhold execution authority until a matching result is committed.
- Distinguish policy `enabled`, a plan existing, waiting/planned execution, an active writer, and the current execution owner. Enabled alone never means “currently controlling.”
- Human-readable “Brak aktywnej automatyki” means no active automatic writer; compatibility attributes such as `owner_code=manual` alone are not proof that a person is actively commanding the inverter.
- Only one writer family may control execution at a time. Claim ownership before writes and release it only after the repository's verified neutral/readback contract.
- Physical Off-Grid has priority. Automatic cleanup must not silently force Self-Use over user/inverter-owned Off-Grid.

### Forecast learning

- Use only the implemented deterministic model; do not invent curtailed-PV, headroom, or new predictive models as incidental work.
- With fresh, coherent physical GCF readback showing GCF enabled and effective export limit exactly `0.0`, RCE and tariff learning use `fixed_zero_export`, exclude learning samples/history, disable live adaptive correction, and use factor `0.80`.
- A positive limit or disabled GCF retains adaptive behavior. Missing/stale/incoherent GCF is unverified and conservative, not confirmed zero-export; its fallback may be lower than `0.80`.
- Historical learning accepts only fully evidenced days under the current version/readback contract. Recorder retention or missing evidence may cause a conservative cold start; do not infer eligibility.

### Diagnostics and claims

- Missing diagnostic evidence is unknown, not false, zero, or healthy. Respect privacy/redaction and the persistent random installation identity contract.
- One support ZIP cannot prove fast physical ramp, every historical attribute, or per-Slave acknowledgement. Do not strengthen compatibility, safety, performance, manufacturer, or parallel claims beyond tests and documented field acceptance.
- Offline tests prove offline behavior only. State explicitly when exact-version real-inverter validation is still required.

## Generated files and one source of truth

- Edit canonical `home_assistant/hoymiles_ems_scheduler.yaml`, `dashboard_hoymiles.yaml`, and files under `home_assistant/www/`; do not hand-edit their copies under `custom_components/hoymiles_hit_modbus/resources/`.
- ESPHome entity definitions in `packages/` and generator-owned mappings in `tools/build_hacs_assets.py` feed `custom_components/hoymiles_hit_modbus/entity_catalog.json`, `custom_components/hoymiles_hit_modbus/translations/en.json`, and `custom_components/hoymiles_hit_modbus/translations/pl.json`; treat those outputs as generated.
- Regenerate all managed PL/EN scheduler, dashboard YAML/JSON, translations, catalog, and copied frontend assets with `python tools/build_hacs_assets.py`.
- After generation, inspect the diff. In a clean release-preparation tree, `git diff --exit-code` after a second generation run proves determinism; do not use it blindly in a dirty user worktree.

## Validation guide

Match cost to risk; do not run every test for every documentation edit.

| Change area | Minimum relevant commands |
| --- | --- |
| Docs only | `git diff --check`; verify changed links, paths, and claims |
| Fast structural check | `python tools/validate_release.py` |
| RCE | `python tools/test_rce_optimizer.py`; `python tools/test_rce_history.py` |
| Tariff | `python tools/test_tariff_profiles.py`; `python tools/test_tariff_optimizer.py` |
| RCEm | `python tools/test_rcm_history.py`; `python tools/test_rcm_optimizer.py` |
| Shared inputs/balance | `python tools/test_energy_data.py`; `python tools/test_load_model.py`; `python tools/test_power_balance.py` as applicable |
| Scheduler, ownership, cross-policy | `python tools/test_automation_matrix.py`; add optimizer executor/startup contracts when relevant |
| Async optimizer lifecycle | `python tools/test_optimizer_executor_contract.py`; `python tools/test_optimizer_startup_contract.py` |
| Source-device lifecycle | `python tools/test_source_device_rebind.py` |
| Readback/parallel/firmware contract | `python tools/test_firmware_readback_contract.py`; relevant matrix and balance tests |
| Dashboard/frontend/generated assets | `python tools/build_hacs_assets.py`; `python tools/validate_release.py`; `node tools/validate_rce_card.js` |
| Diagnostics/privacy/analyzer | `python tools/test_diagnostics.py`; `python tools/test_diagnostic_analyzer.py` |

- Any change to execution authority, safety gates, Modbus writes, readback, optimizer constraints, or parallel behavior requires the stronger relevant path, not only a source-level smoke test.
- For ESPHome/package semantics, use the CI-pinned environment and run `esphome config tools/esphome_verify_ci.yaml` and `esphome compile tools/esphome_verify_ci.yaml` when the release process requires it.
- Before release, follow the union of `RELEASING.md` and `.github/workflows/validate.yml`: regenerate deterministically, run the full applicable test set, `python tools/test_automation_matrix.py --exhaustive`, frontend validation, HACS/hassfest CI, and the conditional firmware compile.

## Change discipline

- **Confirmed bug:** reproduce or establish evidence, identify root cause, apply the smallest fix, add/adjust a regression, and validate the affected subsystem.
- **Observation without proof:** collect states, timestamps, logs, and diagnostics first. Do not edit code because a hypothesis sounds plausible.
- **Feature request:** implement only the requested capability; do not attach unrelated “nice to have” work.
- **Field discovery:** separate site-specific behavior, inverter/platform behavior, and a confirmed project bug before generalizing.
- **Claims:** never extend compatibility, safety, performance, manufacturer, or parallel-system claims beyond available evidence.
- Preserve the dirty worktree, avoid destructive Git commands, stage only explicit release paths, and never commit, push, tag, publish, or open a PR without authorization.

## Stabilization philosophy

- Default to confirmed bug fixes, evidence-driven diagnostics, and field data over speculative features.
- Keep RCE, tariff, and RCEm changes scoped unless a verified shared contract requires coordinated work.
- A confirmed safety defect still requires prompt correction; “stabilization” does not mean refusing necessary change.
- Record transition artifacts and limitations rather than hiding them. Treat exact-version field acceptance as separate from simulation and CI.

## Before finishing any task

Provide a concise completion summary containing:

1. Root cause or requested goal.
2. What changed.
3. Why it is the minimal appropriate change.
4. What was intentionally not changed.
5. Tests or validation executed.
6. Results.
7. Remaining limitations, evidence gaps, or tradeoffs.

If a safety/control contract changed, state the resulting contract explicitly. If field validation remains necessary, say so; never present offline tests as real-inverter acceptance.
