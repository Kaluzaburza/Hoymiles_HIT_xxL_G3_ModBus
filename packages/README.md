# Pakiety integracji

- `core.yaml` — ESPHome, API, OTA i uptime.
- `network.yaml` — Wi-Fi oraz informacje sieciowe.
- `modbus_connection.yaml` — UART, RS485 i harmonogram odpytywania Modbus.
- `settings.yaml` — zapisywalne ustawienia falownika, baterii i EMS (`number`/`select`).
- `system.yaml` — sterowanie pracą systemu i bezpieczny przycisk kasowania alarmów.
- `states_alarms.yaml` — opisowe stany, łącza i zdekodowane maski alarmów.
- `inverter.yaml` — wyjście falownika.
- `grid.yaml` — sieć AC.
- `backup_load.yaml` — złącze EPS/LOAD.
- `generator.yaml` — złącze GEN.
- `pv.yaml` — fotowoltaika, wyłącznie cztery fizyczne wejścia PV1–PV4.
- `battery.yaml` — bateria i BMS.
- `energy_flow.yaml` — przepływy energii.
- `meters.yaml` — zewnętrzne liczniki.
- `parallel_network.yaml` — praca równoległa.
- `overview.yaml` — najważniejsze wartości w jednym bloku.
- `diagnostics.yaml` — temperatury i diagnostyka.

Pliki zaczynające się od `optional_` są domyślnie wyłączone. Włączaj je pojedynczo
w pliku głównym dopiero po potwierdzeniu, że dany model falownika obsługuje te rejestry.

Nazwy encji używają oznaczeń `L1`, `L2`, `L3` dla faz oraz `L1n`, `L2n`, `L3n`
dla pomiarów faza–neutralny. Nazwy przepływów są rozdzielone, np. `PV to Grid`,
`PV to Load` i `PV to Battery`.

## Bezpieczna zmiana trybu EMS

Encja `Tryb EMS` udostępnia wyłącznie tryby używane w tej instalacji:
autokonsumpcję, Off-Grid, ładowanie z sieci oraz rozładowanie do sieci. Zmiana trybu
wysyła jednym poleceniem FC10 cały blok `4300-4306` (tryb, SOC i limity mocy),
odpowiadając działaniu przycisku **Save** w aplikacji S-Miles. Nie zapisuj samego
rejestru `4300`, ponieważ może to pozostawić falownik z niespójnym zestawem nastaw.
Rejestr `4302` (Backup SOC) jest odczytywany wyłącznie wewnętrznie do zachowania
pełnego zapisu FC10; nie tworzy encji Home Assistant, ponieważ tryb Pure Off-Grid
nie korzysta z tej nastawy.
