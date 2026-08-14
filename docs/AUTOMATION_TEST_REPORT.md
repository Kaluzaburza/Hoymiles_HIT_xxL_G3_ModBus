# Automation simulation and safety report

Baseline date: 2026-08-13 (Europe/Warsaw)
Final RC6 live audit date: 2026-08-14 (Europe/Warsaw)
Last completed offline baseline in this report: **v1.5.3 full validation**
Accepted live runtime merge:
**`ce4afc614a691ce70c67da2439613de90e0c61c2`**
(implementation commit **`915337fa34529c5197ad581a41d351b78bfb1d33`**)
Current status: **v1.5.3 offline, exact-SHA CI and local Home Assistant live
acceptance PASS.**

This report covers the deterministic planning and control safeguards used by
the RCE market-price optimizer, tariff-aware grid charging and experimental
RCEm 253 V+ voltage management. The tests do not write to a real inverter.
They verify arithmetic, state transitions, interlocks and fail-safe behaviour
before field acceptance on each installation.

The v1.5.2 RC2 deterministic, packaging and firmware checks below were
completed on 2026-08-13. Its local Home Assistant 2026.8 deployment exposed a
P1 source-device split. RC3 added the fail-closed rebind, passed its dedicated
**6/6** contract and exact-SHA GitHub gates, and was deployed locally. The
rebind persisted and native physical EMS readbacks were live, but ordinary
stable sensor proxies remained unavailable because a parallel-topology
availability gate incorrectly covered every sensor. RC4 scoped that gate only
to the two derived parallel-power proxies, passed exact-SHA CI and restored the
proxies in the local installation. That live run then exposed an independent
PSE interval-end P1: `dtime_utc` marks the end of a quarter, not its start. RC5
fixed the UTC mapping, passed full offline validation and exact-SHA CI, and was
deployed to the local and parallel test installations. Its parser and backend
fail-closed state were correct live. The parallel dashboard then exposed a
separate frontend P1: a transient `404` from the integration's `static-r2` route
was retained by the reverse proxy/browser, so Lovelace timed out waiting for the
dashboard strategy after restart. RC6 is the startup hotfix target; its code,
independent review and full offline gate passed with P0=0/P1=0. Exact runtime
commit `7ce13155533bfa0bf9752a0fd201224dac1a7393` then passed push CI and
`workflow_dispatch`, including the exhaustive matrix, and passed the local and
parallel-installation live frontend audit. The later v1.5.0 section is retained
only as an explicitly archived live baseline.

## v1.5.3 corrective release — offline and live PASS

A later audit found that the published 2064-scenario matrix had inherited
fail-closed defaults for BMS availability/freshness and omitted maximum charge
current. All 696 RCE matrix cases therefore had zero controlled capability and
never reached the joint solver. This invalidates the old matrix as evidence of
nominal RCE execution, but not the independent 68-scenario RCE optimizer suite
or the runtime's fail-closed behaviour.

The corrected matrix now supplies complete nominal BMS contracts and retains
four explicit missing/stale/future/zero fail-closed cases outside the scenario
count. Fresh results on the current workspace are:

- **Quick:** 488/488; all four models have positive power, joint-solver calls
  and controlled export plans.
- **Exhaustive:** 2064/2064; 220 joint-solver calls and 160 controlled export
  plans in the 576 main RCE cases, plus 58/49 respectively in the 120 random
  boundary cases; BMS fail-closed contracts 4/4.
- **RCE optimizer:** 68/68; tariff, RCEm, history, shared energy/LOAD/power,
  startup and executor contracts passed.

The same candidate adds a monotonic input revision and a direct before/after
fingerprint around every executor-backed optimizer. A changed input withdraws
execution authority before recalculation, stale executor output is discarded,
and only a result matching the latest revision is published with
`result_current: true`. Cross-optimizer fingerprints include only attributes
actually consumed, preventing RCE/tariff diagnostic ping-pong.

A live browser check on the preceding candidate reproduced a five-second
Lovelace timeout waiting for
`ll-strategy-dashboard-hoymiles-hit-xxl-g3`. Frontend revision 17 publishes one
canonical full module, removes duplicate and legacy bootstrap resources via
the live Lovelace collection, and upgrades a bootstrap-first constructor in
place. Offline PL, EN and bootstrap-first execution each render exactly 42
Aurora frames. The exact v1.5.3 merge then passed push, pull-request and
workflow-dispatch CI, including the exhaustive matrix, and was installed on
the local Home Assistant test system. Two configuration-checked restarts and a
fresh browser session confirmed revision 17 without strategy/configuration
errors on any of the 17 dashboard paths.

### v1.5.3 exact-SHA live evidence — 2026-08-14

| Check | Result |
|---|---|
| Runtime traceability | Implementation `915337fa34529c5197ad581a41d351b78bfb1d33`; tested `main` merge `ce4afc614a691ce70c67da2439613de90e0c61c2` |
| GitHub CI | Push run `31771450198`, PR run `31771472055`, exhaustive dispatch run `31771487285`, and post-merge `main` run `31771971876`: PASS |
| Deployment archive | SHA-256 `85881F64BBD6AFD66114C8944BADAABBF0F83BD70380D0B661F376560D0D7C22` |
| Home Assistant | Two `ha core check` runs and two required restarts succeeded; package **1.5.3**, setup **Ready** |
| Optimizer authority | RCE, tariff and RCEm `result_current: true` after restart |
| Control safety | Self-Use; every execution owner/cycle off; RCE and balancing off; tariff inactive; RCEm enabled in shadow mode |
| Frontend assets | Revision-17 card, PL/EN JSON and image returned HTTP 200; exactly one managed Hoymiles module resource; no relevant Repair issue |
| Aurora browser audit | All 17 authoritative paths rendered in a fresh session without strategy/configuration errors or relevant console warnings |
| Aurora structure | PL, EN and bootstrap-first deterministic configurations each contain exactly 42 non-nested Aurora frames; live DOM count is conditional by design |
| Firmware action | None; this release does not require another ESPHome flash on a system already running the v1.5.2 FC03-readback firmware |

The final release/tag commit is expected to differ only by this live-evidence
documentation. Its delta from the tested runtime merge must remain
documentation-only.

## Representative systems

| System | Inverters | Battery | Daily PV | Home/day | Home/night | BMS current |
|---|---:|---:|---:|---:|---:|---:|
| HIT-10 | 1 × 10 kW | 10.2 kWh | 12 kWh | 8 kWh | 3.2 kWh | 100 A |
| HIT-15 | 1 × 15 kW | 21 kWh | 28 kWh | 16 kWh | 6.5 kWh | 175 A |
| HIT-20 | 1 × 20 kW | 40 kWh | 55 kWh | 28 kWh | 11 kWh | 250 A |
| Parallel HIT-20 | 2 × 20 kW | 230 kWh | 120 kWh | 48 kWh | 19 kWh | 700 A |

The values are representative test fixtures, not manufacturer performance
claims. They intentionally combine undersized and oversized storage, weak and
strong PV, ordinary and winter-like load, parallel operation and BMS limits
below inverter power.

## Scenario-matrix result — v1.5.2 offline candidate

- **Quick matrix:** 488/488 scenarios passed.
- **Exhaustive matrix:** 2064/2064 scenarios passed.

The complete sweep covers:

- inverter power of 10, 15, 20 and 2 × 20 kW;
- SOC below reserve, mid-range and nearly full;
- no PV, a severe forecast miss, ordinary production and overflow;
- today's and tomorrow's price peaks, negative prices and blocked export
  periods;
- G11, G12, G12w and G13, weekday/weekend/holiday and DST boundaries;
- 240.0–253.2 V, zero-export and user export caps, full batteries,
  insufficient headroom and BMS-limited power;
- power/energy invariants for every half-hour slot, conversion losses,
  minimum-SOC floors and end-of-horizon battery bounds.

## RCE v1.5.2 acceptance checks — passed offline

- The protected energy reserve is rounded **up** to a full 1% SOC step and is
  enforced for every planned export slot, not only at the end of the plan.
- The model distinguishes energy available now from energy expected later from
  PV, so a 48-hour export plan cannot silently treat forecast energy as current
  battery energy.
- RCE keeps a revenue-first objective across a bounded joint horizon. Day-three
  availability, forecast, expected LOAD, and shortfall remain explicit
  diagnostics; they do not add a terminal objective to the sale planner.
- The active-set implementation is explicitly heuristic. An independent oracle
  compares small constructed horizons as regression evidence; it does not prove
  exact or global optimality for the full mixed-constraint problem.
- Gross sale revenue and estimated net benefit are separate. Net benefit can
  subtract modeled battery wear, but no terminal household-energy value is
  reported as realized sale revenue.
- Charge and discharge plans share physical inverter/BMS/AC/export budgets,
  respect energy-arrival time, and do not count natural PV export twice.
- Export lockout, GCF/zero-export, physical system/BMS limits, stale inputs and
  Master/Slave readiness are fail-closed.
- Official PSE `dtime_utc` values are treated as 15-minute interval ends. The
  parser validates `period_utc`/`business_date`, supports the live `24:00`
  label, and reconstructs exactly 48 normal-day, 46 spring-DST and 50
  autumn-DST half-hours without shifting the current price.

## Tariff-charging v1.5.2 acceptance checks — passed offline

- Planning uses a real rolling horizon of at least 48 hours when fresh day-three
  Solcast data is available, including 47/48/49-slot DST days.
- If the third-day tail is missing or stale, the unknown period is modelled as
  zero PV plus average household load and a conservative reserve, capped by the
  user's maximum SOC.
- The plan exposes the exact PV and LOAD values used by the optimizer instead
  of displaying only unrelated live sensors.
- Effective Grid Charge power can be learned from confirmed charging sessions.
  The state is explicit: not observed, collecting, learned live or restored
  from previous evidence. Learning never occurs from Self-Use or a zero-power
  sample.
- Charging lead time includes household load and conversion losses. A required
  10 kWh cannot be postponed to the last minutes of a cheap window when the
  available battery-charging power needs a full hour or longer.
- G11 does not cycle the battery merely because all hours have the same price.

## RCEm 253 V+ v1.5.2 acceptance checks — passed offline

- RCEm starts in **observation-only (shadow)** mode, performs no inverter
  writes, and does not change certified grid-protection settings.
- Voltage risk uses per-phase history and robust recurring-window detection;
  current voltage can override historical expectations when the grid is calmer
  or worse than usual.
- The planner combines interval PV, weekday/weekend household LOAD, battery
  headroom, BMS limits and the user's legal/contractual export cap.
- Pre-discharge is permitted only when it creates useful headroom before a
  later high-voltage/PV-risk window and still protects household energy.
- Missing/stale profile data degrades to a conservative plan. At night, a
  supposedly safe live export power is deliberately reported as unavailable
  rather than invented from non-representative conditions.
- Multiple risk windows retain operational headroom independently; energy
  prepared for an early window is not double-counted for a later one.

## Static control-contract checks for v1.5.2 — passed offline

These repository tests verify scheduler markers, interlocks and planner
contracts statically. They do not execute a real Home Assistant automation or
a Modbus write/read-back cycle; live acceptance evidence is documented
separately below.

- Exactly one owner may control EMS: RCE, tariff charging, RCEm, battery
  balancing or a manual schedule.
- Mode, SOC and power writes are idempotent and require read-back confirmation.
- Freshness gates cover SOC, price, forecast, plan, inverter availability and
  parallel topology. Persistent loss of required control data returns the
  inverter to Self-Use.
- RCE decisions are latched to the selected 30-minute block; tariff targets are
  latched to the required charging window. This prevents rapid mode chatter.
- Notification debounce and fingerprints suppress repeated phone alerts while
  preserving a genuinely different stable state.
- The Home Assistant 2026.8 source-device contract preserves the configured
  composite anchor, accepts only one ESPHome-owned successor with matching
  native entity evidence, rejects ambiguous or contradictory candidates, and
  prevents a second integration entry for the resolved child.

## v1.5.2 RC5 frontend finding and RC6 final audit — 2026-08-14

- RCE optimizer: **68/68** deterministic scenarios passed, including the
  independent small-horizon oracle, padded 48-hour regressions, the exact live
  PSE interval-end shape, payload reordering, `24:00`, and both DST day lengths.
- Automation scheduler: **488/488** quick and **2064/2064** exhaustive
  cross-system scenarios passed.
- Tariff, RCEm, history reconstruction, policy-neutral energy/LOAD/power
  helpers, diagnostics, executor offload and the frontend card all passed.
- The heavy 96/110-slot solver wall-clock regressions use a **1.0 s**
  shared-runner ceiling, while the small horizon-clamp fixture retains its
  tighter **0.5 s** guard and event-loop executor offload remains an independent
  contract. This RC3 adjustment changes only test thresholds, not the
  production optimizer algorithm.
- The physical FC03 actuator-readback and ownership contract passed; generated
  Polish and English assets were deterministic and `validate_release.py`
  reported **291** localized entities with no structural or localization error.
- ESPHome **2026.7.1** accepted the complete local fixture and compiled it with
  ESP-IDF **5.5.5**. The resulting application used 43.3% RAM and 52.9% flash.
- A read-only audit found no open P0/P1 before RC2 deployment, but the local
  Home Assistant 2026.8 run then exposed the composite-device P1 described
  above. The RC3 rebind contract passes **6/6** offline: unique successor,
  revalidated previous successor, ambiguous-candidate rejection, unchanged
  live source, duplicate-entry rejection and invalid linkage rejection.
- RC3 subsequently passed its exact-SHA GitHub project checks, HACS Action,
  Hassfest and the exhaustive matrix. On local deployment the verified
  successor was persisted and native ESPHome physical EMS readbacks reported
  live values, confirming that the rebind itself worked.
- The same local deployment exposed a new P1: `HoymilesSensor.available`
  applied the parallel-topology readiness gate to every ordinary sensor proxy.
  Stable physical EMS readback proxies therefore remained unavailable despite
  their live native sources. RC4 limits this gate to `PARALLEL_POWER_TARGETS`;
  its dynamic ordinary-proxy regression passes.
- RC4 then passed exact-SHA push and workflow-dispatch CI, including the
  exhaustive matrix, and was deployed locally. The rebind, native readbacks,
  ordinary proxies, setup readiness and fail-closed safety state were all live.
- The live 96-row PSE payload exposed another independent P1. Its first
  `dtime_utc` was `22:15` for local `00:00 - 00:15`, proving that the field is
  the interval end. Treating it as a start produced 47/48 half-hours and safely
  held RCE in `missing_data`, but made the engine unusable. RC5 subtracts 15
  minutes on the UTC timeline and validates the accompanying metadata.
- Full RC5 offline validation passes: `validate_release.py`, the physical
  firmware/readback contract, executor/startup offload, source-device rebind
  **6/6**, quick **488/488**, exhaustive **2064/2064**, RCE **68/68**, RCE
  history **3/3**, tariff, RCEm, frontend and dynamic power-balance suites.
- RC5 passed exact-SHA push and workflow-dispatch CI and was deployed to both
  test installations. Its PSE interval-end mapping and backend fail-closed
  safety state were correct live.
- The parallel dashboard exposed an independent frontend P1. Lovelace requested
  `/api/hoymiles_hit_modbus/static-r2/hoymiles-rce-chart-card.js?v=1.5.2.15`
  before the custom route was ready, received a transient `404`, and continued
  receiving that cached response after restart. It then hit the five-second
  timeout while waiting for
  `ll-strategy-dashboard-hoymiles-hit-xxl-g3`.
- The RC6 target uses one canonical full module at
  `/local/hoymiles-rce-chart-card.js?v=1.5.2.16`. Before publishing the URL, the
  installer atomically materializes the card, bootstrap, PL/EN dashboard JSON
  and image. Storage mode is reconciled through Lovelace's live resource
  collection, while runtime installation does not directly mutate
  `.storage/lovelace.*`. A fresh installation without `config/www` copies the
  files, defers publication and raises a restart Repair. An already-open browser
  session requires a hard refresh after restart.
- Exact runtime commit `7ce13155533bfa0bf9752a0fd201224dac1a7393`
  passed push CI and `workflow_dispatch`; the exhaustive workflow completed
  successfully.

The earlier deterministic, packaging, firmware and RC5 backend evidence remains
valid. Together with the RC6 exact-SHA CI and live results below, it supports GO
for the tested Home Assistant runtime/frontend. Meter ESP32 firmware acceptance
remains separate and pending; no firmware was flashed during this audit and no
protected EMS writes were enabled.

## RC6 frontend startup contract — offline and live PASS

The exact runtime candidate demonstrated the following:

- one canonical full, versioned `/local` module registers the dashboard strategy
  before Lovelace's five-second deadline, and loading it again is idempotent;
- the card, bootstrap, both localized dashboard JSON files and image exist under
  `config/www` before the resource URL is published;
- storage-mode migration uses Home Assistant's live Lovelace resource collection
  and preserves user dashboards and `.storage/lovelace.*` files from direct
  runtime mutation;
- a fresh installation with no pre-existing `config/www` remains restart-gated,
  creates the localized Repair and succeeds after the required restart;
- optional asset `OSError` and live-resource failure are fail-soft: integration
  setup completes, no incomplete module is published, and Repair is created;
- YAML-mode loading remains supported; and
- after restart and hard refresh, the version-16 resource returns successfully,
  the dashboard renders, and the browser console contains no strategy timeout.

## RC6 live installation audit — PASS with one degraded-history observation

| Check | Local installation | Parallel meter installation |
|---|---|---|
| Home Assistant | 2026.8.1 | 2026.7.4 |
| Exact candidate | Integration and managed-asset hashes matched runtime commit `7ce13155533bfa0bf9752a0fd201224dac1a7393` | Runtime and asset hashes matched the same commit |
| Installation state | Integration/package 1.5.2; **Ready** | Integration runtime 1.5.2; ESP32 firmware update still pending |
| Frontend | Version-16 `/local` assets returned HTTP 200; fresh-tab and hard-refresh dashboard checks passed | All required version-16 `/local` assets returned HTTP 200; hard refresh rendered the Aurora dashboard |
| Control safety | Self-Use; every controller owner off | RCE and tariff off; RCEm enabled in shadow only; every owner and execution off |
| Firmware action | No firmware action was part of this frontend audit | No firmware was flashed |

One bounded background RCEm Recorder-history query timed out once during the
final observation. The optimizer stayed fail-closed, execution and all owners
remained off, no inverter write was attempted, and the timeout did not repeat in
the observation window. This is recorded as observed degraded history
availability. The bounded timeout and safe fallback behaved as designed, so it
is not an RC6 Home Assistant runtime/frontend release blocker.

## Exact-SHA traceability after documentation finalization

The deployed and live-tested runtime SHA is
`7ce13155533bfa0bf9752a0fd201224dac1a7393`. This final report changes
documentation only, so its eventual commit and release/tag SHA will necessarily
be later and different. Record that future SHA separately and verify that the
delta from the runtime candidate contains documentation/release metadata only.
Do not describe the future documentation/tag SHA as the candidate installed on
either Home Assistant host.

## Archived v1.5.0 live candidate acceptance — 2026-08-12

The same candidate archive was installed on a single-inverter Home Assistant
test system and on the two-inverter field system at `miernik.com.pl` during the
2026-08-12 01:06–02:00 CEST deployment and capture window. The
archive SHA-256 was verified before installation on both hosts. Home Assistant
configuration validation passed, the managed EMS package and Aurora dashboard
were synchronized, and no dashboard configuration errors remained after the
required restart cycle. Candidate archive SHA-256:
`260EC0A1B6374003298CAD60F76A4AD43FEC74C687FD7C1B6110508F489016CC`.

Observed field checks on the parallel installation:

- **RCE:** the protected reserve was 57.50 kWh (25% of a 230 kWh battery),
  current energy above reserve was 66.70 kWh and the projected end-of-horizon
  SOC was 28.3%. The plan stayed above the control reserve in every selected
  block and selected the highest-value allowed half-hour periods.
- **Tariff charging:** TAURON G12w produced `no_charge_needed`; 298.88 kWh of
  conservative modelled PV covered 83.41 kWh of modelled household demand.
  The missing day-three tail was visible and conservatively protected instead
  of being treated as free PV. No unnecessary Grid Charge cycle was created.
- **RCEm 253 V+:** four days and 70,915 phase-voltage samples identified three
  recurring risk windows. Daily maxima included 263.3, 253.6, 254.9 and
  257.1 V. The model found about 11.88 kWh of expected surplus in the risk
  windows, calculated independent headroom for each one and remained in
  observation mode. This is useful site evidence, not permission to change
  certified inverter protection thresholds.
- **Parallel readiness:** both inverters reported ready and the controller
  remained in Self-Use while the automatic modules were disabled for review.

The local zero-export installation also behaved correctly: RCE was blocked by
GCF at 0%, tariff charging reported no required charge, and RCEm remained an
observation-only diagnostic. These observations confirm the expected safe
behaviour on these two materially different configurations; they do not prove
behaviour on every possible installation.

## Remaining field-test limits

The simulations validate planning arithmetic and software safety invariants,
not inverter firmware, Modbus transport, wiring, an electricity meter or the
distribution grid. RCEm still requires observation on a real high-voltage
export site before it can be described as field-proven. Parallel command
propagation remains dependent on the physical Hoymiles topology and firmware.

This evidence can support a documented commissioning or acceptance process,
but it is **not a formal certificate** for the inverter, battery or complete
installation. See [Safety, compliance and commissioning evidence](SAFETY_AND_COMPLIANCE.md).
