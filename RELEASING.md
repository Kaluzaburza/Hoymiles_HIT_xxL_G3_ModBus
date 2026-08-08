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
does not flash the ESP32. Users must use the top-level ESPHome file and remote
package tag from the same release.

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
   python tools/test_automation_matrix.py --exhaustive
   node tools/validate_rce_card.js
   ```

   The exhaustive matrix must report 2064 passed scenarios unless its reviewed
   scenario set was intentionally changed. Record the new count in
   `docs/AUTOMATION_TEST_REPORT.md`.
5. Create the GitHub tag and release.
6. Copy the complete version notes, including the numbered bilingual user
   steps, into the GitHub Release body visible in HACS.
7. Confirm that HACS detects the new version and displays the instructions.
8. Confirm that `LICENSE`, `NOTICE`, `LICENSE_POLICY.md`, `CONTRIBUTING.md` and
   `.github/CODEOWNERS` are present. Do not describe the project as OSI open
   source; use **source-available for noncommercial use**.

## HACS license compatibility

The official HACS default index accepts only OSI-approved licenses. PolyForm
Noncommercial 1.0.0 is intentionally not OSI-approved because it restricts
commercial use, and GitHub Licensee may expose it as `NOASSERTION`. Therefore:

- keep the official HACS Action visible as an advisory check;
- keep Hassfest and all `project-checks` mandatory;
- publish a full GitHub Release so HACS custom-repository users receive the
  version and release notes;
- never replace PolyForm with MIT merely to make the official-index check
  green.
