# Hoymiles HIT xxL G3 Modbus

Home Assistant and ESPHome integration for Hoymiles HIT xxL G3 hybrid
inverters using Modbus RTU over an ESP32 RS485 bridge.

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Kaluzaburza&repository=Hoymiles_HIT_xxL_G3_ModBus&category=integration)

**New to Home Assistant? / Pierwsza instalacja?** Use the concise bilingual
[five-step quick start / szybki start](docs/QUICK_START.md). Advanced wiring,
parallel systems and troubleshooting remain documented below.

> [!IMPORTANT]
> **License / Licencja:** this project is open-source software under the
> [MIT License](LICENSE). Private and commercial use, modification and
> distribution are permitted when the copyright and permission notices are
> retained. The software is provided without warranty.
>
> Projekt jest oprogramowaniem open source na [licencji MIT](LICENSE).
> Dozwolone jest użycie prywatne i komercyjne, modyfikowanie oraz
> rozpowszechnianie z zachowaniem informacji o prawach autorskich i treści
> zezwolenia. Oprogramowanie jest udostępniane bez gwarancji.

## Support the project / Wesprzyj projekt

This independent project was built from a real need for reliable, transparent
Hoymiles control in Home Assistant. Your support helps fund testing on real
installations, documentation, and safer EMS/RCE automation. If the integration
has saved you time or helps you use your energy more effectively, you can
support its continued development:

Ten niezależny projekt powstał z realnej potrzeby niezawodnego i przejrzystego
sterowania falownikiem Hoymiles w Home Assistant. Wsparcie pomaga rozwijać
automatykę EMS/RCE, prowadzić testy na rzeczywistych instalacjach i tworzyć
lepszą dokumentację. Jeśli integracja oszczędziła Ci czas lub pomaga lepiej
wykorzystać energię:

[☕ Support development / Postaw kawę autorowi](https://buycoffee.to/kaluzaaa)

Version **1.4.5** is the current release. It contains:

- 276 localized read-only and writable Modbus entities;
- four physical PV inputs (PV1–PV4);
- grid, load/EPS, battery/BMS, generator and inverter registers;
- safe atomic EMS writes for registers 4300–4306;
- daily grid charge/discharge schedules and optional mobile push notifications;
- a 48-hour PSE RCE profit optimizer that selects the most valuable permitted
  half-hour blocks, protects home demand and outage reserve, observes BMS and
  parallel-system power limits, and separates controlled export from natural
  PV surplus in the revenue statistics;
- automatic G11/G12/G12w/G13 grid charging with 2026 presets for PGE, TAURON,
  ENEA, ENERGA and STOEN, a manual profile, physical charge lead time,
  forecast-error correction, estimated savings and a latched charging target
  that prevents rapid Grid Charge/Self-Use oscillation;
- experimental **RCEm 253 V+** voltage management, starting in observation
  mode, with four-day voltage history, battery-headroom planning, optional
  morning pre-discharge and a user-capped export controller;
- scheduled LiFePO4 storage balancing: use PV first, finish from the grid,
  slow the final 99â€“100% stage and hold full SOC for a configured period;
- Solcast-, Recorder- and true LOAD-based planning for today and tomorrow,
  including a dynamic SOC reserve that protects the home through the night;
- automatic single/Master/Slave topology detection and system-wide live power
  values for parallel installations;
- 5-second grid voltage, phase-power and live energy-flow polling;
- a bilingual dynamic Home Assistant dashboard with RCE, tariff charging,
  RCEm, revenue, PV production and installation-diagnostic views;
- native, high-contrast history and statistics charts for power flow, LOAD,
  PV strings, grid, battery, revenue and production, plus responsive cards for
  desktop and mobile layouts;
- explicit EMS control ownership and conflict diagnostics so automatic modes
  cannot silently compete for inverter control;
- automatic in-place migration of existing storage-mode dashboards to the
  alternating-row cards, with a rollback backup and frontend cache busting;
- English and Polish entity names, select options, config flow and services.

> [!WARNING]
> This project writes operating parameters to a high-power inverter. Verify the
> register map, wiring, battery limits and grid-code requirements for your exact
> model before enabling writable entities. You use it at your own risk.

## Architecture

```text
Hoymiles inverter ── RS485/Modbus RTU ── ESP32/ESPHome
                                              │
                                      native ESPHome API
                                              │
                                    Home Assistant + HACS
```

The HACS integration creates localized, stable proxy entities from the native
ESPHome device. It listens to Home Assistant state events, so it does **not**
add another Modbus polling cycle.

## Dashboard overview / Podgląd dashboardu

The single overview below shows the live start page, RCE optimizer, tariff
charging and RCEm 253 V+ voltage protection from the current release.

Poniższy podgląd przedstawia bieżący panel główny, optymalizator RCE, tanie
ładowanie oraz ochronę napięciową RCEm 253 V+ z aktualnego wydania.

![Hoymiles dashboard overview: start, RCE, tariff charging and RCEm](docs/images/dashboard-overview.png)

## Requirements

- a compatible Hoymiles HIT xxL G3 inverter (tested primarily with HIT-10L-G3);
- ESP32;
- a UART/TTL ↔ RS485 converter compatible with **3.3 V logic**:
  an automatic-direction module is recommended, while MAX3485/MAX485-style
  modules require one additional GPIO for `DE` and `/RE`;
- ESPHome 2026.7 or newer;
- Home Assistant 2026.7 or newer;
- HACS 2.x.

Forecast-based RCE, tariff and RCEm planning additionally requires a configured
[BJReplay Solcast PV Forecast](https://github.com/BJReplay/ha-solcast-solar)
integration. RCE prices require internet access to the public PSE reports API.
Home Assistant Recorder must retain the relevant power and energy entities;
Recorder is enabled by default in a standard Home Assistant installation. An
external household meter is not required.

## ESP32 and RS485 wiring

### First identify the converter

There are two common converter types and they must not be wired or configured
the same way:

| Converter type | Typical TTL-side pins | Extra ESPHome configuration |
|---|---|---|
| **Automatic direction — recommended** | `VCC`, `GND`, `TXD`, `RXD` (sometimes `DI` and `RO`) | None |
| **Manual direction, e.g. bare MAX3485/MAX485 module** | `VCC`, `GND`, `DI`, `RO`, `DE`, `/RE` | Join `DE` with `/RE`, connect them to an ESP32 GPIO and configure `flow_control_pin` |

Do not decide only from the product name. Confirm in the module datasheet
whether direction switching is automatic and whether its supply and UART logic
are compatible with 3.3 V.

### Option A — automatic-direction converter (recommended)

```text
ESP32                        Converter                         Inverter
GPIO17 (TX)  ------------->  RXD / DI  (input from ESP32)
GPIO16 (RX)  <-------------  TXD / RO  (output to ESP32)
3.3 V        ------------->  VCC (only on a 3.3 V-rated module)
GND          --------------  GND  ---------------------------- GND / reference
                              A / D+ -------------------------- A+ / D+
                              B / D- -------------------------- B- / D-
```

| ESP32 | Converter — TTL/UART side | Converter — RS485 side | Inverter Modbus terminal |
|---|---|---|---|
| GPIO17 (`TX`) | converter receive/input: `RXD` or `DI` | — | — |
| GPIO16 (`RX`) | converter transmit/output: `TXD` or `RO` | — | — |
| 3.3 V | `VCC`, **only if the module is rated for 3.3 V** | — | — |
| `GND` | `GND` | `GND`/reference | `GND`/reference |
| — | — | `A`, `A+` or `D+` | `A+` or `D+` |
| — | — | `B`, `B−` or `D−` | `B−` or `D−` |

No `flow_control_pin` entry is required for this option.

### Option B — MAX3485/MAX485-style module with `DE` and `/RE`

Use the same power, `DI`, `RO`, `A`, `B` and `GND` connections as above, plus:

```text
MAX3485 DE ----+
               +------------ GPIO4 (example)
MAX3485 /RE ---+
```

After the existing `packages:` section in `hoymiles-inverter.yaml`, extend the
UART created by the package:

```yaml
uart:
  - id: !extend modbus_uart
    flow_control_pin:
      number: GPIO4
      inverted: false
```

Use a different safe GPIO if GPIO4 is not available on the selected ESP32
board. Do not create a second `modbus_1` block.

Technical reference: ESPHome documents RS485 direction control under
[`uart.flow_control_pin`](https://esphome.io/components/uart/#configuration-variables)
and changes to included package components with
[`!extend`](https://esphome.io/components/packages/#extend).

### Before switching the power on

1. Switch off the inverter and ESP32 while changing wires.
2. Make sure the converter uses 3.3 V UART logic. Never feed a 5 V signal into
   an ESP32 GPIO.
3. Connect `TX` to the converter **input** and `RX` to its **output**. The
   labels `TXD`/`RXD` are written from the converter's point of view on some
   modules and from the host's point of view on others, so verify the arrows or
   datasheet.
4. Connect `A/D+` to `A+/D+`, `B/D−` to `B−/D−`, and connect the Modbus
   reference/GND when the inverter manual provides it.
5. Never connect ESP32 `3.3 V` or converter `VCC` to an inverter communication
   terminal.
6. Use the inverter port explicitly described as external RS485/Modbus for the
   exact model. Do not choose a `Parallel` socket only because it has the same
   connector.

> [!CAUTION]
> RS485 terminal labels are not fully consistent between manufacturers. If
> communication is missing after every other check passes, switch all power
> off and verify whether the converter documents `A/B` with the opposite
> polarity. Never swap wires while the inverter is powered.

## 1. Install through HACS

HACS installs the Home Assistant integration. After the integration is added,
it installs and registers the managed dashboard and EMS assets. ESPHome
firmware packages are downloaded directly from this GitHub repository, so the
HACS and ESPHome parts do not depend on files copied into one another.

1. Use the **Open this repository in HACS** button at the top of this README;
   or open **HACS → three-dot menu → Custom repositories**.
2. For manual setup, add
   `https://github.com/Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus` and select the
   **Integration** category.
3. Download **Hoymiles HIT xxL G3 Modbus** and restart Home Assistant.

The integration automatically uses the Home Assistant language. English and
Polish translations are bundled in `translations/en.json` and
`translations/pl.json`.

## 2. Flash the ESP32

1. In ESPHome, create a new device or upload the self-contained
   [`hoymiles-inverter.yaml`](hoymiles-inverter.yaml) to the ESPHome
   configuration directory.
2. Copy the required keys from
   [`secrets.yaml.example`](secrets.yaml.example) to the local `secrets.yaml`
   and replace every example value.
3. Confirm the board, `uart_tx_pin` and `uart_rx_pin` substitutions.
4. Choose wiring option A or B above. Add the `!extend modbus_uart` block only
   for a converter that requires manual `DE`/`/RE` direction control.
5. Validate, compile and flash the configuration.

Do **not** copy the repository's `packages` directory. The public ESPHome entry
file downloads all versioned register packages directly from GitHub. It does
not depend on files installed by HACS.

Default serial settings are Modbus RTU `115200 8N1`, slave address `1`.

If compilation succeeds but every Modbus entity is unavailable, first verify
the converter type and `DE`/`/RE`, then common GND/reference, `A/B` polarity,
the selected inverter port and unit address.

### ESPHome log window shows `SocketClosedAPIError`

If the dashboard and ESPHome entities continue to update but the Device
Builder log window repeatedly reconnects with `EOF received
(SocketClosedAPIError)`, the ESP32 and Modbus link are usually still healthy.
The ESPHome Device Builder can leave several stale native-API log sessions
open until all eight ESP32 API client slots are occupied.

1. Close every ESPHome log dialog and duplicate browser tab for this device.
2. Wait about 15 seconds.
3. Restart **only** the ESPHome Device Builder add-on; do not restart the
   inverter or reflash the ESP32.
4. Open one log stream and confirm
   `Successful handshake with hoymiles-inverter`.

Do not increase `api.max_connections` above 8 as a workaround. It only delays
the same saturation and consumes more ESP32 memory. The current firmware uses
staggered polling intervals and limits verbose Modbus logging so normal
operation leaves more time for the native API.

## 3. Add the integrations

1. Add the discovered device through the standard **ESPHome** integration.
2. Open **Settings → Devices & services → Add integration**.
3. Search for **Hoymiles HIT xxL G3 Modbus** and select that ESPHome device.

## 4. Dashboard and EMS automation

During setup, the integration automatically copies:

- `/config/dashboard_hoymiles.yaml`;
- `/config/packages/hoymiles_ems_scheduler.yaml`;
- `/config/www/hoymiles-rce-chart-card.js`.

There is no asset-copy checkbox and no manual Lovelace resource step. If Home
Assistant packages are not enabled yet, **Settings → System → Repairs** shows
the exact next action. Add this under the existing `homeassistant:` section in
`configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Do not create a second `homeassistant:` key. Check the configuration and restart
Home Assistant.

The integration registers its versioned frontend module automatically. Do not
add a manual `/local/hoymiles-rce-chart-card.js` resource. Starting with
version 1.3.4, an old resource left by an earlier installation is migrated to
the integration-served URL automatically.

On Home Assistant 2026.5 or newer, open **Settings → Dashboards → Add
dashboard**, select **Hoymiles HIT xxL G3** under Community dashboards and
finish the dialog. The stored dashboard contains only this strategy:

```yaml
strategy:
  type: custom:hoymiles-hit-xxl-g3
```

The strategy loads the current Polish or English dashboard bundled with the
installed integration. After a HACS update and Home Assistant restart, the
dashboard therefore uses the new version automatically instead of retaining a
stale storage-mode copy.

Existing storage-mode Hoymiles dashboards are upgraded in place when an
installed release contains a reviewed migration. Only the required managed
card types, entity rows or asset paths are changed; view order, layout, custom
entities and other user cards are preserved. Before writing, the integration
creates an exact `.pre-<release>.bak` copy in `/config/.storage` (for example
`.pre-1.4.0.bak` in this release).

The copied `/config/dashboard_hoymiles.yaml` remains available for legacy and
manual installations. The managed-asset installer updates an unchanged
dashboard, EMS package, chart card and inverter image automatically. If a user
has modified one of these files, it is preserved; an explicit overwrite is
still available through the service below. Legacy device-name-dependent IDs
are migrated with a `.pre-stable-entity-ids.bak` backup.

To reinstall the bundled assets later, call:

```yaml
action: hoymiles_hit_modbus.install_assets
data:
  overwrite: true
```

## Automation modes and EMS safety

Changing the EMS mode writes the complete register block `4300–4306` with one
FC10 command. Writing register `4300` alone may leave the inverter with an
inconsistent EMS configuration.

The provided EMS automation supports:

- Self-Use;
- Off-Grid;
- grid charge;
- grid discharge;
- daily start time and duration;
- charge/discharge power and SOC limits;
- PSE RCE prices in 30-minute control blocks;
- an optional dynamic RCE minimum SOC calculated from the BJReplay Solcast
  `Forecast Tomorrow` sensor, the trailing four-day LOAD average, inverter
  battery capacity, the Self-Use outage reserve, the remaining home demand
  between 90 minutes before sunset and 90 minutes after sunrise, and a user
  safety correction;
- a configurable export lockout window, including ranges across midnight.

Manual schedules take priority over RCE automation. The export lockout always
returns the inverter to Self-Use and blocks manual, scheduled and RCE export.
Dynamic SOC control fails safe: missing Solcast, LOAD history, sun data or
battery capacity data blocks automatic RCE export. Install and configure
[BJReplay Solcast PV Forecast](https://github.com/BJReplay/ha-solcast-solar)
before enabling this option. The package automatically detects both the
English `sensor.solcast_pv_forecast_forecast_tomorrow` and Polish
`sensor.solcast_pv_forecast_prognoza_na_jutro` entity IDs.

During the protected night window, the reserve decreases with the remaining
time until 90 minutes after sunrise. After the first complete day, the estimate
uses the measured average consumption from up to four previous protected
windows; before that, it falls back to a proportional share of the four-day
daily LOAD average. Household demand is reconstructed from the dedicated LOAD
reading, not by adding PV-to-LOAD and battery-to-LOAD flows, which would count
the same consumer energy more than once.

### RCE market-price optimization

The RCE planner uses all available price blocks for today and tomorrow. When
PSE has not published tomorrow yet, it can operate on today's complete data and
automatically rebuilds the plan when the second day appears. It does not use a
fixed minimum selling price. Instead, it calculates exportable battery energy,
natural PV surplus, inverter/BMS power, conversion losses, the protected home
reserve and the export lockout, then assigns energy to the most valuable
permitted blocks. Missing or stale safety-critical inputs prevent controlled
battery export.

The dashboard distinguishes forecast revenue from realized revenue and
separates deliberate battery export, natural PV surplus and unclassified
historical export. These values are operational estimates based on the PSE RCE
price and measured inverter energy; they are not a settlement invoice.

### Tariff-aware grid charging

The tariff planner simulates the household and battery balance in 30-minute
steps through the end of tomorrow. It considers Solcast, four-day day/night
LOAD history, the outage reserve, BMS limits, charge/discharge efficiencies,
the configured Grid Charge power and the fact that this shared AC limit first
supplies the house. It therefore starts early enough to physically store the
required energy before an expensive period and can preserve battery SOC while
cheap grid energy directly supplies current LOAD.

Bundled profiles cover G11, G12, G12w and G13 where offered by PGE, TAURON,
ENEA, ENERGA and STOEN. They include seasonal windows, weekends and Polish
public holidays. The included 2026 gross marginal-price presets omit fixed and
capacity fees because those do not change when charging moves between zones.
They are a convenience snapshot, not a substitute for the user's contract or
bill. Choose **Manual** when the supplier, product or price differs.

### RCEm 253 V+ voltage management

RCEm learns recurring high-voltage windows from the previous four days and
combines them with live L1/L2/L3 voltage, a rolling ten-minute value, PV/load
power, Solcast and storage headroom. It can reserve battery space before the
risk window and regulate global battery charge power so more PV is absorbed as
voltage rises. Optional morning discharge can create missing headroom without
crossing the protected home SOC. Optional export regulation never exceeds the
smaller of the existing inverter value and the user cap.

RCEm starts in **observation-only** mode and performs no writes until the user
explicitly disables that mode. It never disables certified grid protection,
never changes three-phase unbalance, never enables GCF and never changes a
protection threshold. It is simulation-tested but still requires field
observation on a real high-voltage export site; do not describe it as a method
of bypassing the statutory 253 V limit.

### LiFePO4 storage balancing

The optional service cycle runs at a user-selected interval. It starts after
sunrise and leaves normal Self-Use operation active so PV fills the battery
first. After sunset it completes the charge from the grid if needed. From 99%
to 100% it targets approximately 2 kW of battery charging, corrected for the
house load that shares the Grid Charge limit, and only starts the hold timer at
confirmed full SOC. Original charge settings and EMS mode are restored when
the cycle finishes or is cancelled.

### Interlocks and limitations

Only one automatic owner may control EMS at a time: RCE, tariff charging or
RCEm. Starting one disables the other automatic planners. A balancing cycle
has higher priority and blocks automatic and manual schedules until the battery
is released. Manual schedules retain their documented priority, and disabling
an automation returns settings it owns to their saved values. The integration
does not change the three-phase-unbalance setting and never automates the
maximum export limit outside the explicitly enabled RCEm export controller.

## Firmware compatibility

The integration creates stable proxy entities for the complete catalog even
when the installed ESPHome firmware predates a newly added register. Such an
entity is shown as unavailable and includes
`firmware_update_required: true` instead of appearing as a missing dashboard
entity. Updating ESPHome makes the same entity available without changing its
entity ID or unique ID.

## Parallel inverter systems

The standard ESPHome configuration reads the manufacturer topology registers
`6048-6095` and automatically distinguishes a single inverter, a Master and a
Slave. No manual inverter-count selector is required. Connect the ESP32 Modbus
converter to the **Master**. For a detected Master, the integration writes the
complete EMS block `4300-4306` as one Modbus FC16 broadcast to address `0`.
The same command is therefore received by the Master and every Slave on the
shared `RS485_2` bus. Broadcast writes are sent directly through the Modbus
hub because address `0` never returns a response; this prevents the normal
controller response queue from stalling. A write is blocked if the connected
inverter explicitly reports the Slave role or if a Master reports an invalid
device count.

When a Master is detected, the overview entities used by the Home Assistant
dashboard and its animated energy-flow card automatically switch to the
manufacturer's system-wide registers for PV power, battery power, LOAD power
and grid power. The physical battery voltage still comes from the Master DC
port, while the displayed battery current is calculated from the total
parallel-system battery power. In single-inverter mode the same public entity
IDs continue to expose the direct inverter readings.

The communication addresses listed in registers `6050-6095` belong to the
inverter's internal parallel network. They are topology diagnostics, not
additional Modbus devices routed through the Master's external RS485 port, so
the ESP32 does not poll them as separate slave IDs.

The `Parallel Networking Command` entity for register `3016` remains disabled
by default. It is a commissioning command that creates or disassembles the
parallel network, not a normal EMS control. The integration never invokes it
automatically.

According to the Hoymiles HIT-(5-20)L-G3 user manual, an on-grid parallel
system supports up to 10 inverters. Off-grid operation supports up to three
units without AC contactors or up to 10 with the required contactors. The
meter and DTS must be connected to the Master, and the first and last devices
on the dedicated parallel communication bus require termination.

## Repository structure

```text
custom_components/hoymiles_hit_modbus/  HACS integration
packages/                               ESPHome Modbus register packages
examples/esphome/                       public ESPHome entry configuration
home_assistant/                         dashboard card and source EMS package
docs/images/                            README screenshots
tools/                                  catalog and validation scripts
```

## Development

Regenerate the localized entity catalog and bundled assets:

```bash
python tools/build_hacs_assets.py
```

GitHub Actions run mandatory HACS validation, Hassfest and project
structural/logic tests. The reviewed multi-system automation matrix and its
field-test limits are recorded in the
[automation simulation report](docs/AUTOMATION_TEST_REPORT.md).

## Support

Please open an issue and include:

- exact inverter model and firmware version;
- ESPHome and Home Assistant versions;
- relevant logs with secrets removed;
- the affected Modbus register and the expected value.

For a complete, privacy-filtered report use the one-click ZIP card in the last
**Diagnostics** dashboard view, Home Assistant's native **Download diagnostics**
action or the optional one-command terminal collector. See
[Diagnostics / Diagnostyka](docs/DIAGNOSTICS.md) for exact steps and the data
included in each report.

---

## Polski

Integracja łączy falowniki hybrydowe Hoymiles HIT xxL G3 z Home Assistantem
przez ESP32 i magistralę RS485/Modbus RTU. Wersja **1.4.5** udostępnia
276 encji, cztery wejścia PV, ustawienia baterii i EMS, harmonogramy dobowe,
optymalizator sprzedaży RCE, automatyczne ładowanie w taryfach
G11/G12/G12w/G13, eksperymentalną ochronę eksportu RCEm 253 V+, serwisowe
wyrównywanie LiFePO4, statystyki przychodu i produkcji oraz dynamiczny dashboard
PL/EN. Planowanie korzysta z Solcast, historii Recorder, rzeczywistego LOAD,
pojemności magazynu, limitów BMS i topologii pojedynczej lub równoległej.
Automatyczne ładowanie taryfowe zapamiętuje cel oraz czas rozpoczętego okna,
dzięki czemu bieżące przeliczenia nie zapętlają trybów Grid Charge/Self-Use.

### Podłączenie ESP32 i konwertera RS485

#### Najpierw rozpoznaj rodzaj konwertera

Najczęstszy błąd polega na potraktowaniu modułu MAX3485 jak konwertera
automatycznego. Są to dwa różne warianty:

| Rodzaj konwertera | Typowe piny od strony ESP32 | Zmiana w ESPHome |
|---|---|---|
| **Automatyczna zmiana kierunku — zalecany** | `VCC`, `GND`, `TXD`, `RXD` (czasem `DI` i `RO`) | Brak |
| **Ręczna zmiana kierunku, np. moduł MAX3485/MAX485** | `VCC`, `GND`, `DI`, `RO`, `DE`, `/RE` | Połącz `DE` z `/RE`, podłącz do GPIO ESP32 i ustaw `flow_control_pin` |

Nie kieruj się wyłącznie nazwą aukcji. W dokumentacji konkretnego modułu
sprawdź automatyczne sterowanie kierunkiem oraz zgodność z logiką 3,3 V.

#### Wariant A — konwerter automatyczny (zalecany)

```text
ESP32                        Konwerter                         Falownik
GPIO17 (TX)  ------------->  RXD / DI  (wejście z ESP32)
GPIO16 (RX)  <-------------  TXD / RO  (wyjście do ESP32)
3,3 V        ------------->  VCC (tylko moduł zasilany 3,3 V)
GND          --------------  GND  ---------------------------- GND / referencja
                              A / D+ -------------------------- A+ / D+
                              B / D- -------------------------- B- / D-
```

| ESP32 | Konwerter — strona TTL/UART | Konwerter — strona RS485 | Zaciski Modbus falownika |
|---|---|---|---|
| GPIO17 (`TX`) | wejście konwertera: `RXD` albo `DI` | — | — |
| GPIO16 (`RX`) | wyjście konwertera: `TXD` albo `RO` | — | — |
| 3,3 V | `VCC`, **tylko jeśli moduł jest zasilany napięciem 3,3 V** | — | — |
| `GND` | `GND` | `GND`/referencja | `GND`/referencja |
| — | — | `A`, `A+` albo `D+` | `A+` albo `D+` |
| — | — | `B`, `B−` albo `D−` | `B−` albo `D−` |

Dla tego wariantu nie dodawaj `flow_control_pin`.

#### Wariant B — moduł MAX3485/MAX485 z pinami `DE` i `/RE`

Podłącz zasilanie, `DI`, `RO`, `A`, `B` i `GND` jak w tabeli wyżej, a dodatkowo:

```text
MAX3485 DE ----+
               +------------ GPIO4 (przykład)
MAX3485 /RE ---+
```

Za istniejącą sekcją `packages:` w pliku `hoymiles-inverter.yaml` rozszerz
UART utworzony przez pakiet:

```yaml
uart:
  - id: !extend modbus_uart
    flow_control_pin:
      number: GPIO4
      inverted: false
```

Jeżeli GPIO4 nie jest dostępny na wybranej płytce, użyj innego bezpiecznego
GPIO. Nie twórz drugiej sekcji `modbus_1`.

Podstawa konfiguracji:
[`uart.flow_control_pin` w ESPHome](https://esphome.io/components/uart/#configuration-variables)
oraz rozszerzanie elementów pakietu przez
[`!extend`](https://esphome.io/components/packages/#extend).

#### Kontrola przed włączeniem zasilania

1. Podczas zmiany przewodów wyłącz falownik i ESP32.
2. Sprawdź, czy strona UART konwertera pracuje z logiką 3,3 V. Nie podawaj
   sygnału 5 V na GPIO ESP32.
3. `TX` ESP32 połącz z **wejściem** konwertera, a `RX` z jego **wyjściem**.
   Niektóre moduły opisują `TXD`/`RXD` z perspektywy konwertera, inne z
   perspektywy urządzenia nadrzędnego — sprawdź strzałki albo dokumentację.
4. Połącz `A/D+` z `A+/D+`, `B/D−` z `B−/D−` oraz przewód
   odniesienia/GND, jeżeli przewiduje go instrukcja falownika.
5. Nie podłączaj `3,3 V` ESP32 ani `VCC` konwertera do żadnego zacisku
   komunikacyjnego falownika.
6. Użyj portu opisanego dla konkretnego modelu jako zewnętrzny RS485/Modbus.
   Nie wybieraj gniazda `Parallel` tylko dlatego, że ma taki sam wtyk.

> [!CAUTION]
> Oznaczenia pary RS485 nie są całkowicie jednolite. Jeżeli po wykonaniu
> wszystkich pozostałych kontroli nadal nie ma komunikacji, wyłącz zasilanie i
> sprawdź w dokumentacji, czy producent konwertera nie stosuje odwrotnej
> polaryzacji `A/B`. Nie zamieniaj przewodów przy włączonym falowniku.

### Instalacja

HACS instaluje integrację Home Assistanta. Po dodaniu integracji zostają
automatycznie zainstalowane i zarejestrowane zarządzany dashboard oraz zasoby
EMS. ESPHome pobiera pakiety firmware bezpośrednio z GitHuba. Te dwa etapy nie
wymagają kopiowania plików między katalogami HACS i ESPHome.

1. Kliknij przycisk **Open this repository in HACS** na początku README albo w
   **HACS → menu z trzema kropkami → Niestandardowe repozytoria** dodaj
   `https://github.com/Kaluzaburza/Hoymiles_HIT_xxL_G3_ModBus` jako
   **Integration**. Zainstaluj integrację i uruchom Home Assistant ponownie.
2. W ESPHome utwórz urządzenie lub wgraj samowystarczalny
   [`hoymiles-inverter.yaml`](hoymiles-inverter.yaml).
3. Przepisz klucze z
   [`secrets.yaml.example`](secrets.yaml.example) do lokalnego
   `secrets.yaml`, uzupełnij wszystkie wartości i sprawdź model płytki oraz
   piny `uart_tx_pin` i `uart_rx_pin`.
4. Wybierz wariant konwertera A albo B. Blok `!extend modbus_uart` dodawaj
   wyłącznie dla modułu wymagającego sterowania `DE`/`/RE`.
5. W ESPHome wybierz **Validate**, następnie skompiluj i wgraj firmware.
   Plik pobiera wersjonowane pakiety rejestrów z GitHuba — nie kopiuj katalogu
   `packages`.
6. Dodaj wykryte urządzenie przez standardową integrację ESPHome.
7. Dodaj integrację **Hoymiles HIT xxL G3 Modbus**. Jedyny zgodny ESP32 zostanie
   podpowiedziany, a dashboard i automatyka EMS skopiują się automatycznie.
8. Sprawdź encję **Stan instalacji**. Jeżeli Home Assistant pokaże Naprawę
   dotyczącą pakietów, wykonaj podaną instrukcję i uruchom HA ponownie. Zasób
   karty Lovelace jest rejestrowany automatycznie — nie dodawaj adresu `/local`
   ani `/api` ręcznie.
9. W **Ustawienia → Panele → Dodaj panel** wybierz społecznościowy dashboard
   **Hoymiles HIT xxL G3**. Będzie on automatycznie korzystał z wersji
   dostarczonej przez aktualnie zainstalowaną integrację.

Jeżeli kompilacja przebiega poprawnie, ale wszystkie encje Modbus są
niedostępne, sprawdź kolejno: rodzaj konwertera i linie `DE`/`/RE`, wspólną
referencję/GND, polaryzację `A/B`, wybrany port falownika oraz adres urządzenia.

#### Okno logów ESPHome pokazuje `SocketClosedAPIError`

Jeżeli dashboard i encje nadal się odświeżają, lecz okno logów Device Buildera
zapętla ponowne połączenia z komunikatem `EOF received
(SocketClosedAPIError)`, ESP32 i Modbus najczęściej działają prawidłowo. Dodatek
ESPHome Device Builder może pozostawić stare sesje logów i zająć wszystkie
osiem miejsc klientów API w ESP32.

1. Zamknij wszystkie okna logów ESPHome i powielone karty przeglądarki dla tego
   urządzenia.
2. Odczekaj około 15 sekund.
3. Uruchom ponownie **wyłącznie** dodatek ESPHome Device Builder. Nie restartuj
   falownika i nie wgrywaj ponownie firmware.
4. Otwórz jeden strumień logów i sprawdź, czy pojawia się
   `Successful handshake with hoymiles-inverter`.

Nie zwiększaj `api.max_connections` powyżej 8. Taki zabieg jedynie odsuwa
problem i zużywa dodatkową pamięć ESP32. Aktualny firmware rozkłada odczyty w
czasie i ogranicza szczegółowe logi Modbus, aby pozostawić więcej czasu dla API.

Nowy dashboard strategiczny nie wymaga ponownego wklejania konfiguracji po
aktualizacji HACS. Po restarcie Home Assistanta pobiera bieżącą wersję PL albo
EN bezpośrednio z integracji. Stary sposób z plikiem
`/config/dashboard_hoymiles.yaml` pozostaje obsługiwany. Instalator aktualizuje
niezmodyfikowane pliki automatycznie, a pliki zmienione przez użytkownika
pozostawia bez nadpisania. Migracja starych identyfikatorów nadal tworzy kopię
`.pre-stable-entity-ids.bak`.

Od wersji 1.3.4 integracja potrafi bezpiecznie migrować również aktywny
dashboard zapisany przez Home Assistanta w trybie `storage`. Zmienia wyłącznie
zarządzane typy kart, wiersze encji lub ścieżki zasobów, zachowując układ i
własne encje użytkownika. Przed każdą zmianą tworzy kopię
`.pre-<wersja>.bak` w `/config/.storage` — dla tego wydania jest to na przykład
`.pre-1.4.0.bak` — i automatycznie aktualizuje stary zasób `/local`.

Jeżeli firmware ESP32 jest starszy od integracji HACS, nowe encje pojawią się
jako **niedostępne**, a nie jako brakujące. Atrybut
`firmware_update_required: true` wskazuje konieczność aktualizacji ESPHome.

Nazwy encji i opcje wyboru są tłumaczone automatycznie na polski lub angielski
zależnie od języka Home Assistanta. Pierwsze tłumaczenia są celowo opisowe i
mogą być dalej poprawiane w plikach `translations/pl.json` oraz
`translations/en.json`.

### Automatyki EMS

#### Optymalizacja RCE

Automat analizuje dostępne ceny PSE dla dzisiaj i jutra. Jeżeli dane jutrzejsze
nie zostały jeszcze opublikowane, może działać na kompletnym dniu bieżącym i
przelicza plan ponownie po pojawieniu się drugiego dnia. Nie korzysta ze stałej
minimalnej ceny sprzedaży. Wylicza energię możliwą do oddania, naturalną
nadwyżkę PV, rezerwę domu i zaniku sieci, limity mocy falownika i BMS, sprawność
oraz blokadę godzinową, a następnie wybiera najdroższe dozwolone bloki po 30
minut. Brak aktualnych danych krytycznych blokuje sterowane rozładowanie.

Zapotrzebowanie domu jest odtwarzane z dedykowanego odczytu LOAD, a nie z sumy
PV→LOAD i bateria→LOAD, która mogłaby podwójnie zliczyć tę samą energię.
Dashboard rozdziela prognozowany i rzeczywisty eksport sterowany, naturalną
nadwyżkę oraz eksport nierozpoznany. Statystyka przychodu jest estymacją według
RCE, a nie dokumentem rozliczeniowym sprzedawcy.

#### Automatyczne ładowanie taryfowe

Planer symuluje bilans domu i magazynu w krokach 30-minutowych do końca jutra.
Uwzględnia Solcast, czterodniową historię dnia i nocy, rezerwę awaryjną, limity
BMS, sprawności oraz fakt, że limit Grid Charge najpierw zasila odbiorniki, a
dopiero pozostałą mocą ładuje magazyn. Dzięki temu rozpoczyna ładowanie na tyle
wcześnie, aby fizycznie zgromadzić wymaganą energię przed drogą strefą.

Gotowe profile obejmują dostępne warianty G11, G12, G12w i G13 dla PGE, TAURON,
ENEA, ENERGA oraz STOEN, wraz z sezonami, weekendami i polskimi świętami.
Wbudowane ceny zmienne brutto na 2026 rok nie obejmują opłat stałych i
mocowych. Są wygodnym punktem startowym, ale użytkownik powinien porównać je z
własną umową i fakturą. Dla innego produktu lub sprzedawcy służy profil
**Manual**.

#### RCEm 253 V+

RCEm uczy się powtarzalnych okien wysokiego napięcia z czterech poprzednich dni
i łączy je z bieżącym napięciem L1/L2/L3, średnią 10-minutową, mocą PV i LOAD,
Solcast oraz wolnym miejscem w magazynie. Może wcześniej przygotować miejsce w
baterii, regulować globalną moc ładowania oraz — po osobnym włączeniu — płynnie
ograniczać eksport do wartości nie większej od zastanej nastawy falownika i
limitu użytkownika.

Tryb startuje jako **tylko obserwacja** i wtedy nie zapisuje rejestrów. Nie
wyłącza zabezpieczeń sieciowych, nie zmienia asymetrii trójfazowej, nie włącza
GCF i nie modyfikuje progów ochronnych. Funkcja przeszła symulacje, ale wymaga
dalszych prób na rzeczywistej instalacji z problemem napięciowym. Nie służy do
obchodzenia prawnego limitu 253 V.

#### Wyrównywanie magazynu LiFePO4

Opcjonalny cykl serwisowy uruchamia się w odstępie ustawionym przez użytkownika.
Po wschodzie pozostawia Self-Use, aby możliwie dużo energii dostarczyło PV. Po
zachodzie uzupełnia brak z sieci. Od 99% do 100% utrzymuje około 2 kW mocy
trafiającej do baterii, z korektą o bieżący pobór domu, a czas wyrównywania
zaczyna liczyć dopiero po potwierdzeniu pełnego SOC. Po zakończeniu lub anulowaniu
odtwarza poprzednie ustawienia ładowania i tryb EMS.

#### Priorytety i blokady

W jednej chwili tylko jeden automat może być właścicielem EMS: RCE, ładowanie
taryfowe albo RCEm. Włączenie jednego wyłącza pozostałe. Wyrównywanie magazynu
ma wyższy priorytet i na czas cyklu blokuje automatyczne oraz ręczne plany.
Automatyka nie steruje asymetrią trójfazową. Maksymalny limit eksportu może być
zmieniany wyłącznie przez jawnie włączony regulator eksportu RCEm.

### Zgłaszanie problemów

Do zgłoszenia dołącz dokładną godzinę błędu, model i firmware falownika oraz
ZIP pobrany jednym kliknięciem z ostatniej zakładki **Diagnostyka** albo raport
utworzony przez natywną akcję Home Assistant **Pobierz diagnostykę**. Przy
błędach ESPHome, Modbus, uruchamiania albo pętli automatyzacji możesz użyć także
jednej komendy tworzącej rozszerzoną paczkę. Instrukcja i opis maskowanych
danych znajdują się w dokumencie [Diagnostyka](docs/DIAGNOSTICS.md).

### Bezpieczeństwo

Integracja zapisuje ustawienia falownika. Przed użyciem sprawdź model, mapę
rejestrów, limity BMS oraz wymagania operatora sieci. Tryb EMS jest zapisywany
atomowo jako cały blok `4300–4306`, tak jak po użyciu przycisku **Save** w
aplikacji producenta.
