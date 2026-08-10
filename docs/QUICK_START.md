# Quick start / Szybki start

This is the shortest safe installation path. The main README contains wiring
variants and advanced troubleshooting.

## English — five steps

1. **Wire the powered-off system.** Connect ESP32 GPIO17 (TX) to the RS485
   converter input, GPIO16 (RX) to its output and GND to GND. Connect converter
   A/B/GND to the inverter's supported Modbus port. Do not connect ESP32 3.3 V
   to the inverter.
2. **Install from HACS.** Add this repository as an Integration, install
   **Hoymiles HIT xxL G3 Modbus** and restart Home Assistant.
3. **Flash ESP32.** Copy `hoymiles-inverter.yaml` to ESPHome, add the five keys
   from `secrets.yaml.example`, select **Validate**, then **Install**. ESPHome's
   dashboard-import metadata lets the dashboard recognize and adopt the
   maintained public configuration. Firmware remains under the user's control
   and is never updated without an explicit build and upload.
4. **Add the device.** Add the discovered ESPHome device, then add
   **Hoymiles HIT xxL G3 Modbus**. If only one compatible ESP32 exists, it is
   preselected. Dashboard files and EMS automation are installed automatically.
5. **Complete the displayed next step.** Open the integration device and check
   **Installation status**. If Home Assistant shows a Repair about packages,
   add the following under the existing `homeassistant:` section, validate the
   configuration and restart once:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   Never create a second `homeassistant:` key. Finally add the community
   dashboard **Hoymiles HIT xxL G3** in **Settings → Dashboards**.

### Before enabling automatic EMS

Keep RCE, tariff charging, RCEm and battery balancing disabled until this short
check is complete:

1. **Installation status** is `Ready` and the ESPHome device remains online.
2. PV, true LOAD, grid, battery voltage/current and SOC are plausible when
   compared once with the inverter display or manufacturer application.
3. Battery capacity, inverter power, BMS charge/discharge limits and the
   Self-Use outage reserve match the physical installation.
4. Solcast today/tomorrow sensors are available before forecast-based planning
   is enabled. RCE additionally needs current PSE price data.
5. Leave RCEm in **observation-only** mode until its voltage history and proposed
   actions have been reviewed on the target site.

The integration interlocks automatic owners, but correct physical limits and
credible source data must be confirmed by the installer or user.

## Updating

1. Read the numbered **User update steps** in HACS.
2. Update the integration in HACS and restart Home Assistant if requested.
3. Rebuild ESP32 only when the release notes explicitly require it. Open the
   device in ESPHome and use **Install**; do not copy the repository's
   `packages` directory.
4. Check **Installation status**. `Ready` means ESP32, entity coverage and the
   EMS package are available.

## Polski — pięć kroków

1. **Podłącz wyłączony układ.** GPIO17 (TX) ESP32 połącz z wejściem konwertera
   RS485, GPIO16 (RX) z jego wyjściem, a GND z GND. A/B/GND konwertera podłącz do
   obsługiwanego portu Modbus falownika. Nie podłączaj 3,3 V ESP32 do falownika.
2. **Zainstaluj przez HACS.** Dodaj repozytorium jako **Integration**, zainstaluj
   **Hoymiles HIT xxL G3 Modbus** i uruchom Home Assistant ponownie.
3. **Wgraj ESP32.** Skopiuj `hoymiles-inverter.yaml` do ESPHome, dodaj pięć
   wartości z `secrets.yaml.example`, wybierz **Validate**, a następnie
   **Install**. Metadane dashboard-import pozwalają ESPHome rozpoznać i
   zaadoptować utrzymywaną konfigurację publiczną. Firmware pozostaje pod
   kontrolą użytkownika i nigdy nie aktualizuje się bez jawnej kompilacji i
   wgrania.
4. **Dodaj urządzenie.** Dodaj wykryte ESPHome, a potem integrację
   **Hoymiles HIT xxL G3 Modbus**. Jeżeli jest tylko jeden zgodny ESP32, zostanie
   od razu wybrany. Dashboard i automatyka EMS instalują się automatycznie.
5. **Wykonaj wyświetlony następny krok.** Na urządzeniu integracji sprawdź
   **Stan instalacji**. Jeżeli Home Assistant pokaże Naprawę dotyczącą pakietów,
   dodaj poniższy wpis pod istniejącą sekcją `homeassistant:`, sprawdź
   konfigurację i wykonaj jeden restart:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   Nigdy nie twórz drugiego klucza `homeassistant:`. Na koniec w
   **Ustawienia → Panele** dodaj społecznościowy dashboard
   **Hoymiles HIT xxL G3**.

### Przed włączeniem automatycznego EMS

Pozostaw RCE, ładowanie taryfowe, RCEm i wyrównywanie magazynu wyłączone do
zakończenia krótkiej kontroli:

1. **Stan instalacji** ma wartość `Gotowe`, a urządzenie ESPHome pozostaje
   dostępne.
2. Moc PV, rzeczywisty LOAD, przepływ sieci, napięcie/prąd baterii i SOC mają
   wiarygodne wartości po jednorazowym porównaniu z ekranem falownika albo
   aplikacją producenta.
3. Pojemność magazynu, moc falownika, limity ładowania/rozładowania BMS i rezerwa
   awaryjna Self-Use odpowiadają fizycznej instalacji.
4. Encje Solcast dla dzisiaj i jutra są dostępne przed uruchomieniem planowania
   zależnego od prognozy. RCE wymaga również aktualnych cen PSE.
5. Pozostaw RCEm w trybie **tylko obserwacja**, dopóki nie sprawdzisz historii
   napięć i proponowanych działań na docelowej instalacji.

Integracja blokuje konflikt właścicieli EMS, ale poprawne limity fizyczne i
wiarygodność danych wejściowych musi potwierdzić instalator albo użytkownik.

## Aktualizacja

1. Przeczytaj numerowane **Kroki po aktualizacji** wyświetlone w HACS.
2. Zaktualizuj integrację w HACS i wykonaj restart, jeżeli opis tego wymaga.
3. ESP32 przebuduj tylko wtedy, gdy changelog mówi o tym wprost. Otwórz
   urządzenie w ESPHome i wybierz **Install** — nie kopiuj katalogu `packages`.
4. Sprawdź **Stan instalacji**. `Gotowe` oznacza dostępny ESP32, komplet encji i
   aktywny pakiet EMS.
