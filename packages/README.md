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
- `parallel_network.yaml` — automatyczne wykrywanie pracy pojedynczej lub
  równoległej Master/Slave (do 10 falowników) i bezpieczna kontrola toru EMS.
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

`settings.yaml` korzysta z roli i liczby urządzeń wykrytych w
`parallel_network.yaml`. W standardowej konfiguracji oba pakiety są zawsze
włączone. Zapis EMS jest wykonywany bezpośrednio dla pojedynczego falownika albo
przez Mastera dla całej sieci. Gdy ESP32 zostanie podłączone do urządzenia
zgłaszającego rolę Slave, zapis jest blokowany.

Po wykryciu Mastera encje podglądu mocy używane przez dashboard przełączają się
automatycznie na rejestry sumaryczne całego systemu: PV, bateria, LOAD i sieć.
Animacja przepływu energii pokazuje dzięki temu bilans wszystkich wykrytych
falowników. Napięcie baterii pozostaje fizycznym pomiarem na złączu DC Mastera,
a prąd baterii jest wyliczany z sumarycznej mocy baterii.

Adresy komunikacyjne z rejestrów `6050-6095` opisują wewnętrzną sieć równoległą
falownika. Port RS485 Mastera nie przekazuje do nich zewnętrznych zapytań
Modbus, dlatego są używane tylko do diagnostyki topologii, a nie jako osobne
adresy odpytywane przez ESP32.
