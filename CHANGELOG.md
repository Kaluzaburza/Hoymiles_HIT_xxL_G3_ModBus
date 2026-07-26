# Changelog

All notable changes to this project are documented in this file.

## [1.0.1] - 2026-07-26

### Fixed

- Entity IDs are now stable and independent of the Home Assistant language.
- Existing localized proxy entity IDs are migrated automatically during setup.
- EMS and RCE packages can resolve the EMS mode, discharge SOC and battery SOC
  entities after installation on Polish and English Home Assistant instances.
- The RCE card supports both the current Home Assistant states context and the
  legacy `hass` setter.
- The dashboard wraps the custom RCE chart in a standard stack to prevent an
  asynchronous custom-card loading race.
- The RCE resource cache key was increased to `v=1.0.1`.

### Validation

- Added a headless RCE card registration and rendering test to CI.
- Added release checks for the stable entity ID property and migration hook.

### Polski

- Identyfikatory encji są teraz stałe i niezależne od języka Home Assistanta.
- Istniejące, przetłumaczone identyfikatory encji są automatycznie migrowane.
- Naprawiono odwołania automatyki EMS/RCE do trybu EMS, minimalnego SOC
  rozładowania oraz SOC baterii.
- Karta RCE obsługuje aktualny mechanizm przekazywania stanów w Home Assistant.
- Dodano automatyczny test rejestracji i renderowania karty RCE.

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
