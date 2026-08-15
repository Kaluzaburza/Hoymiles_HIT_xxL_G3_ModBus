# EMS safety, audit readiness and functional programme mapping

## What this document means

This project is open-source control software for Home Assistant. It is not a
certification body, an inverter protection relay or a substitute for the
manufacturer's declarations, grid-connection approval and electrical
commissioning. The statements below describe implemented, testable functions
and evidence that can support a technical acceptance report.

## Mapping to selected functional requirements of the 2026 Polish home-storage programme

The official **Przydomowe Magazyny Energii** programme defines EMS as a system
that makes decisions, analyses weather or economic data and manages energy
between current demand, storage and the grid. It lists charging control,
discharging control and time-profile operation as minimum functions.

| Programme expectation | Implemented evidence in this project |
|---|---|
| Intelligent energy-flow decisions | RCE, tariff and RCEm optimizers publish the selected action, inputs, protected reserve and reason for acting or waiting. |
| Weather-data analysis | Solcast today/tomorrow/optional day-three forecasts, P10/P50/P90 uncertainty, live forecast-error correction and stale-data guards. |
| Economic-data analysis | PSE RCE 30-minute prices, regional G11/G12/G12w/G13 profiles, conversion losses, battery-wear cost and net-benefit calculations. |
| Charging control | Grid Charge target SOC and power, physical charging lead time, learned effective charge power, BMS limits and tariff-slot latch. |
| Discharging control | Highest-value RCE slot selection, protected home/outage reserve, BMS/system/GCF power caps and minimum-SOC enforcement. |
| Time-profile operation | 30-minute market and tariff model, manual schedules, weekday/weekend/holiday profiles and DST-aware 47/48/49-slot days. |
| Priority for current demand and storage | True LOAD model, dynamic home/night reserve, Self-Use fallback and optional battery-headroom preparation before PV surplus. |
| Monitoring and evidence | Bilingual dashboard, current controller owner, conflict indication, decision attributes, statistics and downloadable diagnostic ZIP. |

The programme also requires a compliant storage system, documented island
operation and certified equipment. Those are properties of the installed
battery, inverter and complete installation. They must be demonstrated with
manufacturer documents and the commissioning/acceptance protocol.
This feature mapping does **not** confirm eligibility for a subsidy or replace
the assessment required by the programme operator.

Official references:

- [Przydomowe Magazyny Energii — programme requirements](https://przydomowemagazyny.gov.pl/o-programie/)
- [NFOŚiGW consultation and programme information](https://www.gov.pl/web/funduszmodernizacyjny/przydomowe-magazyny-energii)
- [EU 2016/631 network code for generator connection](https://eur-lex.europa.eu/eli/reg/2016/631/oj/)

## Implemented control-safety mechanisms

- **One control owner:** RCE, tariff charging, RCEm, LiFePO4 balancing and
  manual schedules cannot silently write competing EMS commands.
- **Fail closed:** missing, stale or inconsistent control data blocks a new
  automatic cycle; sustained data loss returns the inverter to Self-Use.
- **Freshness gates:** SOC, price, plan, Solcast and parallel-system state have
  explicit maximum ages before automatic execution is allowed.
- **Physical limits first:** BMS current, battery voltage, inverter count,
  system rating, GCF/export cap and user/site limits reduce the requested
  charge or discharge power.
- **No protection override:** the project does not write grid-code profiles,
  protection thresholds, Q(U), P(U), power factor or three-phase unbalance.
- **Idempotent commands and acknowledgement:** a command is written only when
  the requested value differs; the controller verifies the state read back
  from the inverter before treating a mode as active. In a parallel system the
  current FC03 acknowledgement proves the Master block only; each Slave still
  requires a shared physical external RS485 connection and commissioning
  evidence.
- **Stable decisions:** 30-minute RCE slot latching, tariff target/window
  latching, minimum dwell times and notification fingerprinting prevent rapid
  mode oscillation and notification storms.
- **Operator-safe RCEm rollout:** voltage control starts in observation mode,
  respects the existing export cap and rate-limits normal corrections.
- **Manual recovery:** every automatic module can be disabled independently;
  the clear-fault function and direct Self-Use control remain visible.
- **Off-Grid precedence:** mode code `3` is a user/inverter-owned island state.
  Automatic engines yield and restore only their owned limits without forcing
  Self-Use.
- **Parallel evidence boundary:** a newer matching Master FC03 acknowledges
  the configuration, while commissioning still requires separate evidence for
  every inverter. In v1.5.6 a separate post-command diagnostic applies 20
  seconds of transition grace, evaluates five newer complete aggregate-power
  generations and requires three consecutive stable generations. It confirms
  a system-level physical response only; aggregate response cannot prove
  execution by a named Slave and is never described as a per-Slave protocol
  acknowledgement.
- **Transition evidence:** an isolated mode-change sample is annotated but is
  neither accepted as stable response nor treated by itself as an error. On
  this installation, HA history for 8–14 August, 19:00–22:00 local, showed an
  approximately 60 kW stop transient on 8 August and an approximately 60 kW
  start transient aligned with the stored mode-code change on 14 August. The
  9–13 August windows contain repeated discharge plateaus and switching
  impulses, though sampling may miss the full peak. Separately, the 15 August
  live trace at 18:20 local captured 63.069 kW battery / 65.910 kW inverter
  during a switch. This is bounded evidence, not an inverter specification.
- **Version-scoped field evidence:** the 2026-08-15 shared-bus observation used
  managed Home Assistant package 1.5.4 and ESP32 firmware/project 1.5.3. Its
  two stable 33.653/33.863 kW battery-discharge samples and the operator's
  report that both nodes showed Grid Discharge support the physical bus and
  protocol path. They do not accept v1.5.5 or v1.5.6 software, provide
  per-node power, or prove an automatic stop. An exact-version v1.5.6 retest is
  required.
- **Traceability:** the optimizer state includes data age, model source,
  reserve, power constraints, selected windows and the calculated financial
  result; the support ZIP provides a redacted diagnostic snapshot.

## Cybersecurity boundary

The programme page references NIS2, the Cyber Resilience Act, RED delegated
cybersecurity requirements and ETSI EN 303 645 for products within their legal
scope and applicable dates. This repository supports a local-first deployment,
does not require a vendor cloud for inverter control, uses Home Assistant user
permissions and can use ESPHome API encryption. These design choices reduce
exposure but are **not a formal conformity assessment**. The system owner must
secure Home Assistant, ESPHome, Wi-Fi/VLAN access, remote tunnels, backups and
updates as part of the complete installation.

## Evidence package for an installer or audit

1. Record the inverter, battery/BMS, protection and OSD documentation.
2. Save the exact integration, managed Home Assistant package, ESPHome
   firmware/project and candidate commit versions. Evidence from an older
   deployed stack cannot accept a later software release.
3. Export the Hoymiles diagnostic ZIP after commissioning.
4. Capture Self-Use, Grid Charge and Grid Discharge acceptance tests at a safe
   power limit, including the return to Self-Use.
5. Verify the configured battery capacity, SOC reserve, BMS current limits,
   system power, GCF/export cap and parallel topology.
6. For a parallel plant, record the external multidrop RS485 wiring and its
   termination, then retain timestamped mode **and power** evidence separately
   for the Master and every Slave during Grid Discharge and return to Self-Use.
   An operator statement without per-node power or a retained screenshot must
   be labelled as limited evidence.
7. Record the exact start and stop service-event timestamps, both Master FC03
   generations, at least two stable aggregate power samples, timer/ownership
   release and the final safe-power time. State explicitly whether the stop was
   automatic, timer-driven or manual; an idle timer is not a substitute for the
   missing stop-command timestamp.
8. Snapshot `4300–4306`, GCF `258/259`, register `306`, SOC and faults before,
   during and after the test. Do not derive per-Slave acknowledgement from the
   Master FC03 or aggregate power.
9. Keep the automated test report and the site-specific acceptance protocol.

## Polish summary / Podsumowanie po polsku

Projekt realizuje i mapuje **wybrane funkcjonalne założenia inteligentnego EMS**
opisane w programie: podejmuje decyzje, analizuje pogodę i ekonomię, steruje
ładowaniem i rozładowaniem oraz pracuje według profili czasowych. Ma też blokady
wzajemne, kontrolę świeżości danych, limity BMS/falownika, bezpieczny powrót do
Self-Use, potwierdzanie zapisów i rozbudowany ślad diagnostyczny.

Nie oznacza to automatycznej certyfikacji całej instalacji. Zgodność magazynu,
falownika, pracy wyspowej, przyłączenia do sieci i cyberbezpieczeństwa trzeba
potwierdzić dokumentacją producentów oraz protokołem odbioru kompletnego
systemu. Integracja nie zmienia certyfikowanych nastaw ochrony sieci.
Ta matryca nie potwierdza kwalifikowalności instalacji do dotacji i nie
zastępuje oceny operatora programu.

W instalacji równoległej broadcast EMS działa tylko na wspólnej fizycznej
magistrali zewnętrznego Modbus/RS485 obejmującej Mastera i każdego Slave'a.
FC03 Mastera nie jest potwierdzeniem Slave'ów, dlatego protokół odbioru musi
zawierać osobny dowód trybu i mocy każdej jednostki.

Obserwacja z 2026-08-15 na pakiecie Home Assistant 1.5.4 i firmware/projekcie
ESP32 1.5.3 jest dowodem sprzętowym i protokołowym wspólnej magistrali, a nie
odbiorem oprogramowania v1.5.5 lub v1.5.6. Operator potwierdził Grid Discharge
na obu węzłach, lecz nie zachowano osobnych mocy, zrzutów ani dokładnych czasów
aplikacji. Nie zapisano też dokładnego czasu ręcznego polecenia stop, więc nie
wolno przypisywać temu testowi zatrzymania automatycznego ani dokładnej latencji.
Kandydat v1.5.6 wymaga pełnego ponownego testu.

Kod trybu `3` ma pierwszeństwo jako stan Off-Grid należący do
użytkownika/falownika. Automatyczne silniki ustępują i przywracają wyłącznie
własne limity, bez wymuszania Self-Use. W v1.5.6 osobna diagnostyka po
potwierdzeniu konfiguracji przez FC03 Mastera stosuje 20 sekund czasu
przejściowego, ocenia pięć nowszych kompletnych generacji mocy sumarycznej i
wymaga trzech kolejnych stabilnych generacji.
Potwierdza tylko odpowiedź całego systemu; sama moc sumaryczna nadal nie dowodzi
wykonania przez nazwanego Slave'a i nie jest jego protokołowym ACK. Pojedynczy
pik zmiany trybu jest adnotowany i pomijany w stabilnym oknie, a nie traktowany
samodzielnie jako błąd.
