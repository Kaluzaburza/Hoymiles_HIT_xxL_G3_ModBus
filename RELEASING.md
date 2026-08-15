# Release procedure

This file is the persistent release checklist for maintainers. HACS presents
the GitHub Release body to users, so every release must explain the required
post-update actions in the order in which they must be performed.

## Required user-action section

Every `CHANGELOG.md` release entry and the matching GitHub Release body must
contain this exact heading:

```markdown
### User update steps / Kroki po aktualizacji
```

The section must contain a numbered, bilingual list covering:

1. **HACS** — whether the integration must be updated through HACS.
2. **Home Assistant** — whether a restart, reload or configuration check is
   required.
3. **ESP32 / ESPHome** — explicitly state whether firmware must be rebuilt and
   uploaded.
4. **Verification / Weryfikacja** — what the user should check after the
   update.

Do not write only “update normally”. If a step is unnecessary, state that
explicitly, for example:

```markdown
3. **ESP32 / ESPHome:** no firmware rebuild is required for this release.
   **PL:** ta wersja nie wymaga ponownej kompilacji ani wgrywania firmware ESP32.
```

## When ESP32 recompilation is mandatory

Tell users to rebuild and upload firmware whenever a release changes runtime
firmware behavior in any of:

- `packages/*.yaml`;
- runtime sections of `hoymiles-inverter.yaml` or `examples/esphome/*.yaml`;
- source entities expected by `entity_catalog.json`;
- UART, Modbus or ESPHome API configuration.

A documentation comment or `dashboard_import`-only change does not require an
existing user to rebuild firmware. State that explicitly and describe the
adoption metadata as optional. Do not claim that HACS or `dashboard_import`
flashes firmware automatically.

The instructions must say that HACS updates the Home Assistant integration but
does not flash the ESP32. Users must use the top-level ESPHome file and the
compatible remote-package tag named in that release's notes. When firmware is
unchanged, retain and document the last compatible ESPHome tag.

When a release changes both Home Assistant control logic and firmware source
freshness or actuator semantics, update Home Assistant first while every
automatic writer is disabled and observation-only modes remain enabled. Verify
that the physical EMS mode and controlled registers did not change across the
required Home Assistant restart(s). Only then rebuild and upload ESPHome from
the matching immutable tag, and repeat the no-write verification before
restoring the previous automation policy.

## Frontend asset startup contract

When a release changes managed dashboard or frontend assets:

- the release notes must require a Home Assistant restart and a hard refresh of
  any dashboard tab that remained open across that restart;
- copy every `/local` dependency before publishing its versioned URL, and load
  one canonical full module instead of competing bootstrap and bundle strategy
  implementations;
- update storage-mode resources through Lovelace's live resource collection;
  never mutate `.storage/lovelace.*` directly while Home Assistant is running;
- if `config/www` did not exist when frontend started, copy the assets but defer
  publication, raise a localized restart Repair and verify the next boot; and
- validate repeated module loading, fresh-no-`www`, storage and YAML modes
  offline. For a release candidate, also verify the exact versioned resource,
  dashboard render and absence of a strategy-registration timeout in a fresh
  browser session after restart; and
- record the exact commit SHA installed on every live test host before a
  documentation-only release finalization commit is created. If the eventual
  release/tag SHA differs, record it separately, verify that the delta contains
  no runtime changes, and never describe that later SHA as the deployed
  candidate.

## Repository rename cutover completed with v1.5.5

The v1.5.5 coordinated cutover establishes these exact public metadata values:

- **Repository:** `Kaluzaburza/hoymiles-hit-g3-ems`
- **Project:** `EMS for Hoymiles HIT-(5–20)L-G3`
- **GitHub description:** `Unofficial local EMS for Hoymiles HIT-G3 hybrid
  inverters — Home Assistant, ESPHome, Modbus, RCE, tariff optimization and
  RCEm.`

The description is also the English README tagline. Treat the repository
rename, metadata changes, release tag and HACS publication as one cutover: the
ESPHome remote package and `dashboard_import` URLs must remain fetchable at
every point.

The v1.5.5 cutover contract is:

1. Rename the GitHub repository and set its About/description field to the
   exact text above. Do not create a different repository under the old slug;
   GitHub's redirect must continue protecting installed configurations.
2. Update the local `origin` and every current repository URL, including HACS
   badges/instructions, manifest documentation and issue tracker, Repairs and
   dashboard links, issue templates, `NOTICE`, ESPHome `dashboard_import` and
   remote-package URLs, and their release-validator expectations.
3. Change the user-facing HACS/Home Assistant/dashboard project title to the
   exact project value above, update the canonical asset-generator sources,
   and regenerate all localized/bundled copies.
4. Keep all technical identities unchanged: the Home Assistant domain and
   component directory `hoymiles_hit_modbus`, entity/service/unique IDs,
   storage keys, dashboard strategy type `hoymiles-hit-xxl-g3`, ESPHome node
   names, and ESPHome `project.name: hoymiles.energy-storage-modbus`.
5. Run the complete release gate, then verify the new repository with HTTP,
   `git ls-remote`, HACS/hassfest, ESPHome `dashboard_import`, and a clean
   remote-package compile from the immutable v1.5.5 release tag.

Keep the old slug unclaimed after the cutover so GitHub's redirect continues
to protect existing HACS and ESPHome configurations. Never rename the stable
technical identities listed above as part of a later branding change.

## Release checklist

1. Move the completed `Unreleased` notes to the new version heading.
2. Review the user steps and remove instructions that are not required for
   that particular version.
3. Update the integration version numbers. Update remote ESPHome package tags
   only when runtime firmware changes; otherwise keep the last compatible tag
   and explain explicitly that no ESP32 rebuild is required.
4. Run the release validators and tests:

   ```text
   python tools/build_hacs_assets.py
   python tools/validate_release.py
   python tools/test_rce_optimizer.py
   python tools/test_rce_history.py
   python tools/test_tariff_profiles.py
   python tools/test_tariff_optimizer.py
   python tools/test_rcm_history.py
   python tools/test_rcm_optimizer.py
   python tools/test_energy_data.py
   python tools/test_load_model.py
   python tools/test_power_balance.py
   python tools/test_firmware_readback_contract.py
   python tools/test_optimizer_executor_contract.py
   python tools/test_optimizer_startup_contract.py
   python tools/test_source_device_rebind.py
   python tools/test_automation_matrix.py --exhaustive
   node tools/validate_rce_card.js
   ```

   The exhaustive matrix must report 2064 passed scenarios unless its reviewed
   scenario set was intentionally changed. Record the new count in
   `docs/AUTOMATION_TEST_REPORT.md`.
   It must also report positive RCE power for every representative model,
   non-zero joint-solver and planned-export coverage, and all four explicit BMS
   fail-closed contracts. A scenario count without these coverage counters is
   not release evidence.
5. Create the GitHub tag and release.
6. Copy the complete version notes, including the numbered bilingual user
   steps, into the GitHub Release body visible in HACS.
7. Confirm that HACS detects the new version and displays the instructions.
8. Confirm that `LICENSE`, `NOTICE`, `LICENSE_POLICY.md`, `CONTRIBUTING.md` and
   `.github/CODEOWNERS` are present and consistent with the MIT license.

## HACS and license compatibility

The project uses the OSI-approved MIT License. The official HACS Action,
Hassfest and all `project-checks` are mandatory and must pass without ignores
or `continue-on-error` before a release is published.
