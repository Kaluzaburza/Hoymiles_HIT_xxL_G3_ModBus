# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-07-24

First public release.

### Added

- HACS-compatible Home Assistant custom integration.
- 271 localized sensor, number and select entities.
- English and Polish entity names, select options, config flow and services.
- ESPHome remote package for Hoymiles HIT xxL G3 Modbus RTU.
- Four PV inputs, grid, EPS/load, battery/BMS, generator and diagnostic data.
- Atomic EMS writes for registers 4300–4306.
- Daily charge and discharge schedules.
- PSE RCE price automation with a configurable export lockout.
- Ready-to-use Home Assistant dashboard and localized RCE chart.
- HACS, Hassfest and project-specific validation workflows.

### Safety

- Writable inverter settings remain the responsibility of the installer.
- Battery, grid-code and Modbus register limits must be verified for the exact
  inverter and firmware before use.

---

## Polski

Pierwsze publiczne wydanie integracji:

- instalacja integracji Home Assistant przez HACS;
- 271 encji z nazwami i opcjami po polsku oraz angielsku;
- firmware ESPHome jako zdalny pakiet GitHub;
- cztery wejścia PV, sieć, EPS/odbiorniki, bateria/BMS, generator i diagnostyka;
- bezpieczny zapis całego bloku EMS 4300–4306;
- harmonogramy ładowania i rozładowania;
- automatyka cenowa RCE PSE z blokadą sprzedaży;
- gotowy dashboard i wykres RCE;
- automatyczne testy HACS, Hassfest i testy struktury projektu.
