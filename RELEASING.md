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

Tell users to rebuild and upload firmware whenever a release changes any of:

- `packages/*.yaml`;
- `hoymiles-inverter.yaml`;
- `examples/esphome/*.yaml`;
- source entities expected by `entity_catalog.json`;
- UART, Modbus or ESPHome API configuration.

The instructions must say that HACS updates the Home Assistant integration but
does not flash the ESP32. Users must use the top-level ESPHome file and remote
package tag from the same release.

## Release checklist

1. Move the completed `Unreleased` notes to the new version heading.
2. Review the user steps and remove instructions that are not required for
   that particular version.
3. Update all version numbers and remote ESPHome package tags.
4. Run the release validators and tests.
5. Create the GitHub tag and release.
6. Copy the complete version notes, including the numbered bilingual user
   steps, into the GitHub Release body visible in HACS.
7. Confirm that HACS detects the new version and displays the instructions.
