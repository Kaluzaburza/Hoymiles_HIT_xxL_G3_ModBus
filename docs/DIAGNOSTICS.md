# Diagnostics / Diagnostyka

The integration provides two support reports. Start with the native Home
Assistant report. Use the terminal archive when the fault concerns disconnects,
ESPHome, Modbus communication, an automation loop or a failed startup.

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

Integracja udostępnia dwa raporty. Zacznij od raportu natywnego. Paczki
terminalowej użyj, gdy problem dotyczy rozłączeń, ESPHome, komunikacji Modbus,
pętli automatyzacji albo nieudanego uruchomienia.

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
