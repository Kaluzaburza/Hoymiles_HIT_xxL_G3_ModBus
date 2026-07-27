# Changelog

All notable changes to this project are documented in this file.

## [1.0.2] - 2026-07-27

### Added

- Added a localized Clear Fault button that writes value `1` to holding
  register `3004`.
- Added inverter-side battery current and voltage entities for installations
  where BMS communication is absent or reports different values.
- Added explicit dashboard names for every visible entity in both Polish and
  English.

### Changed

- Dashboard labels no longer repeat the `Hoymiles Inverter` device prefix.
- Battery current and voltage on the overview now use physical inverter
  measurements instead of BMS telemetry.
- Alarm and connectivity sections use native Home Assistant entity rows again,
  including tap-to-open state history.
- The Polish and English dashboards support both stable HACS entity IDs and
  migrated IDs from existing installations.
- Improved dashboard layout, live power presentation, grid phase tables,
  energy-flow precision and inverter control grouping.

### Fixed

- Restored native alarm display formatting after the temporary custom status
  table caused poor readability.
- Corrected missing or English-only Polish labels for writable battery and EMS
  controls.
- Fixed dashboard references that could show missing entities on a fresh HACS
  installation or after entity-ID migration.
- Removed stale PV5/PV6 and redundant dashboard references from the generated
  assets.

### Validation

- Release validation now checks the Clear Fault register command, physical
  battery entities, localized short dashboard names, legacy/stable entity-ID
  compatibility and fresh Polish/English asset installation.
- Rebuilt and verified all generated catalogs, translations and bundled
  dashboards.

### Polski

- Dodano przycisk „Wyczyść alarm”, zapisujący wartość `1` do rejestru `3004`.
- Dodano fizyczny prąd i napięcie baterii mierzone przez falownik, niezależne od
  telemetrii BMS.
- Usunięto przedrostek „Hoymiles Inverter” z nazw widocznych na dashboardzie.
- Przywrócono standardowy wygląd alarmów i statusów wraz z otwieraniem historii
  po kliknięciu.
- Poprawiono polskie tłumaczenia ustawień baterii i EMS.
- Dashboardy PL/EN obsługują zarówno stałe identyfikatory encji HACS, jak i
  identyfikatory zmigrowane z wcześniejszych instalacji.
- Rozszerzono testy nowych instalacji, tłumaczeń, rejestrów i zgodności
  wygenerowanych zasobów.

## [1.0.1] - 2026-07-26

### Added

- Documented the required HACS-first installation order for the integration and
  ESPHome package.
- Added the ESP32-to-RS485-to-inverter wiring table for GPIO17/GPIO16, A+/B-,
  power and Modbus ground.
- Added dashboard screenshots for the live energy flow and RCE automation.
- Expanded the README with installation, update and troubleshooting guidance.

### Fixed

- Corrected standalone ESPHome installation so public remote packages resolve
  from the tagged GitHub release.
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

- Opisano wymaganą kolejność instalacji: najpierw repozytorium przez HACS, a
  następnie konfiguracja urządzenia ESPHome.
- Dodano tabelę podłączenia ESP32, konwertera RS485 i falownika dla GPIO17/GPIO16,
  A+/B-, zasilania oraz masy Modbus.
- Dodano zrzuty dashboardu przepływu energii i automatyki RCE.
- Rozszerzono instrukcję instalacji, aktualizacji i diagnostyki.
- Naprawiono samodzielną instalację ESPHome oraz publiczne odwołania do pakietów
  z oznaczonego wydania GitHub.
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
