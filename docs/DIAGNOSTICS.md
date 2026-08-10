# Diagnostics / Diagnostyka

The integration provides three access methods for support data. Start with the
one-click dashboard ZIP or native Home Assistant report. Use the terminal
archive when the fault concerns disconnects, ESPHome, Modbus communication, an
automation loop or a failed startup.

## Download one ZIP from the dashboard

Open the last **Diagnostics** view and press **Collect data and download ZIP**.
An administrator's browser receives a fresh ZIP containing the native report,
24 hours of significant control history and filtered Home Assistant Core logs.
The archive is built in memory and is not left in `/config`.

Email the ZIP to [info@kaluzaaa.com](mailto:info@kaluzaaa.com) together with a
description of the problem, what you expected and the exact local date and time
when it occurred.

Home Assistant Core cannot open a live ESPHome device-log session. For a
low-level UART/Modbus fault, also save the relevant excerpt from ESPHome Device
Builder. The current ESPHome entity states are already present in the ZIP.

## One-click Home Assistant report

1. Open **Settings → Devices & services → Hoymiles HIT xxL G3 Modbus**.
2. Open the integration entry menu and choose **Download diagnostics**.
3. Attach the downloaded JSON file to the issue together with the exact local
   time of the fault and a short description of the expected behaviour.

The report contains integration/firmware versions, entity coverage, current
Hoymiles states, calculation attributes and 24 hours of significant control
changes. Fast telemetry history is deliberately omitted so the report remains
small and does not overload Recorder.

## Extended terminal archive

In the **Terminal & SSH** add-on run:

```sh
sh /config/custom_components/hoymiles_hit_modbus/collect_diagnostics.sh
```

The command prints the path of one `.tar.gz` file in
`/config/hoymiles_diagnostics/`. Download that file with File editor, Studio
Code Server, Samba or SSH and attach it to the issue.

The archive adds Home Assistant/Supervisor/host versions, storage and memory
information, relevant redacted Core and ESPHome logs, a redacted ESPHome entry
configuration and — when the local API permits it — the native diagnostic JSON.

Passwords, API keys, tokens, Wi-Fi identifiers, URLs, IP/MAC addresses, serial
numbers and the source device ID are masked. The command never copies
`secrets.yaml` or the Home Assistant `.storage` database. Automatic masking is
not a substitute for review: inspect the archive before publishing it in a
public issue.

---

Integracja udostępnia trzy sposoby zebrania danych. Zacznij od ZIP-u z
dashboardu albo raportu natywnego. Paczki terminalowej użyj, gdy problem
dotyczy rozłączeń, ESPHome, komunikacji Modbus, pętli automatyzacji albo
nieudanego uruchomienia.

## Pobranie jednego ZIP-u z dashboardu

Otwórz ostatnią zakładkę **Diagnostyka** i naciśnij **Zbierz dane i pobierz
ZIP**. Przeglądarka administratora otrzyma świeżą paczkę zawierającą natywny
raport, 24 godziny istotnych zmian sterowania i odfiltrowane logi HA Core.
Paczka powstaje w pamięci i nie pozostaje w katalogu `/config`.

Wyślij ZIP na [info@kaluzaaa.com](mailto:info@kaluzaaa.com) razem z opisem
problemu, oczekiwanym zachowaniem oraz dokładną lokalną datą i godziną jego
wystąpienia.

Proces Home Assistant Core nie może sam otworzyć sesji logów urządzenia
ESPHome. Przy niskopoziomowym błędzie UART/Modbus zapisz dodatkowo odpowiedni
fragment z ESPHome Device Builder. Bieżące stany encji ESPHome są już w ZIP-ie.

## Raport Home Assistant jednym kliknięciem

1. Otwórz **Ustawienia → Urządzenia oraz usługi → Hoymiles HIT xxL G3 Modbus**.
2. Otwórz menu wpisu integracji i wybierz **Pobierz diagnostykę**.
3. Dołącz pobrany plik JSON do zgłoszenia razem z dokładną lokalną godziną
   wystąpienia błędu i krótkim opisem oczekiwanego działania.

Raport zawiera wersje integracji i firmware, kompletność encji, bieżące stany
Hoymiles, parametry obliczeń oraz 24 godziny istotnych zmian sterowania. Historia
szybkiej telemetrii jest celowo pomijana, aby raport pozostał mały i nie
obciążał bazy Recorder.

## Rozszerzona paczka z terminala

W dodatku **Terminal & SSH** wykonaj:

```sh
sh /config/custom_components/hoymiles_hit_modbus/collect_diagnostics.sh
```

Komenda wyświetli ścieżkę jednego pliku `.tar.gz` w katalogu
`/config/hoymiles_diagnostics/`. Pobierz go przez File editor, Studio Code
Server, Sambę albo SSH i dołącz do zgłoszenia.

Paczka dodaje wersje Home Assistant/Supervisor/hosta, stan pamięci i dysku,
odfiltrowane logi Core i ESPHome, oczyszczoną konfigurację wejściową ESPHome
oraz — jeśli lokalne API na to pozwoli — natywny raport diagnostyczny.

Hasła, klucze API, tokeny, dane Wi-Fi, adresy URL, IP/MAC, numery seryjne i ID
urządzenia źródłowego są maskowane. Komenda nigdy nie kopiuje `secrets.yaml`
ani bazy `.storage` Home Assistanta. Automatyczne maskowanie nie zastępuje
kontroli — przejrzyj paczkę przed dodaniem jej do publicznego zgłoszenia.
