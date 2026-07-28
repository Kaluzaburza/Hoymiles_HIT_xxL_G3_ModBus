"""State localization helpers for Polish text returned by the firmware."""

from __future__ import annotations


SIMPLE_STATE_TRANSLATIONS: dict[str, tuple[str, str]] = {
    "Niedostępne": ("Unavailable", "Niedostępne"),
    "Inicjalizacja zasilania": (
        "Power initialization",
        "Inicjalizacja zasilania",
    ),
    "Czuwanie": ("Standby", "Czuwanie"),
    "Test sieci": ("Grid test", "Test sieci"),
    "Praca z siecią": ("On-grid operation", "Praca z siecią"),
    "Awaria": ("Fault", "Awaria"),
    "Praca wyspowa": ("Off-grid operation", "Praca wyspowa"),
    "Bypass": ("Bypass", "Bypass"),
    "Brak błędu": ("No error", "Brak błędu"),
    "Brak błędów": ("No faults", "Brak błędów"),
    "Offline": ("Offline", "Offline"),
    "Online": ("Online", "Online"),
    "HMID: połączenie prawidłowe": (
        "HMID: connected",
        "HMID: połączenie prawidłowe",
    ),
    "HMID: błąd połączenia": (
        "HMID: connection error",
        "HMID: błąd połączenia",
    ),
    "Brak baterii": ("No battery", "Brak baterii"),
    "Litowa": ("Lithium-ion", "Litowa"),
    "Kwasowo-fosforanowa": ("Lead-acid", "Kwasowo-fosforanowa"),
    "Symulowana": ("Simulated", "Symulowana"),
    "Ładowanie": ("Charging", "Ładowanie"),
    "Rozładowanie": ("Discharging", "Rozładowanie"),
    "Falownik pojedynczy": ("Single inverter", "Falownik pojedynczy"),
    "Master": ("Master", "Master"),
    "Slave": ("Slave", "Slave"),
    "Nieznana topologia": ("Unknown topology", "Nieznana topologia"),
    "Nieprawidłowa topologia": ("Invalid topology", "Nieprawidłowa topologia"),
    "Gotowe - sterowanie bezpośrednie": (
        "Ready - direct control",
        "Gotowe - sterowanie bezpośrednie",
    ),
    "Gotowe - wszystkie falowniki zsynchronizowane": (
        "Ready - all inverters synchronized",
        "Gotowe - wszystkie falowniki zsynchronizowane",
    ),
    "Gotowe - Master steruje siecią równoległą": (
        "Ready - Master controls the parallel network",
        "Gotowe - Master steruje siecią równoległą",
    ),
    "Zablokowane - ESP32 podłączone do Slave": (
        "Blocked - ESP32 connected to Slave",
        "Zablokowane - ESP32 podłączone do Slave",
    ),
    "Oczekiwanie na wykrycie sieci": (
        "Waiting for network detection",
        "Oczekiwanie na wykrycie sieci",
    ),
    "Oczekiwanie na tryb EMS Mastera": (
        "Waiting for Master EMS mode",
        "Oczekiwanie na tryb EMS Mastera",
    ),
    "Oczekiwanie na potwierdzenie Slave": (
        "Waiting for Slave confirmation",
        "Oczekiwanie na potwierdzenie Slave",
    ),
    "Błąd adresów sieci równoległej": (
        "Parallel-network address error",
        "Błąd adresów sieci równoległej",
    ),
    "Błąd - Slave nie odpowiada": (
        "Error - Slave is not responding",
        "Błąd - Slave nie odpowiada",
    ),
    "Błąd - tryb EMS Slave niezgodny": (
        "Error - Slave EMS mode mismatch",
        "Błąd - tryb EMS Slave niezgodny",
    ),
    "Nieprawidłowa liczba falowników": (
        "Invalid inverter count",
        "Nieprawidłowa liczba falowników",
    ),
}


FAULT_TRANSLATIONS: dict[str, tuple[str, str]] = {
    "Niskie napięcie baterii": ("Low battery voltage", "Niskie napięcie baterii"),
    "Wysokie napięcie baterii": ("High battery voltage", "Wysokie napięcie baterii"),
    "Niskie napięcie pakietu przy rozładowaniu": (
        "Low pack voltage while discharging",
        "Niskie napięcie pakietu przy rozładowaniu",
    ),
    "Wysokie napięcie pakietu przy ładowaniu": (
        "High pack voltage while charging",
        "Wysokie napięcie pakietu przy ładowaniu",
    ),
    "Niska temperatura ładowania": (
        "Low charging temperature",
        "Niska temperatura ładowania",
    ),
    "Wysoka temperatura ładowania": (
        "High charging temperature",
        "Wysoka temperatura ładowania",
    ),
    "Niska temperatura rozładowania": (
        "Low discharging temperature",
        "Niska temperatura rozładowania",
    ),
    "Wysoka temperatura rozładowania": (
        "High discharging temperature",
        "Wysoka temperatura rozładowania",
    ),
    "Za wysokie napięcie wejściowe (HV)": (
        "High-voltage input overvoltage",
        "Za wysokie napięcie wejściowe (HV)",
    ),
    "Wyłączenie": ("Shutdown", "Wyłączenie"),
    "Zabezpieczenie sprzętowe": ("Hardware protection", "Zabezpieczenie sprzętowe"),
    "Błąd komparatora": ("Comparator fault", "Błąd komparatora"),
    "Błąd komunikacji SPI": ("SPI communication fault", "Błąd komunikacji SPI"),
    "Błąd komunikacji ARM": ("ARM communication fault", "Błąd komunikacji ARM"),
    "Nieprawidłowe przesunięcie pomiaru": (
        "Invalid measurement offset",
        "Nieprawidłowe przesunięcie pomiaru",
    ),
    "Błąd obliczenia przesunięcia CMPSS": (
        "CMPSS offset calculation fault",
        "Błąd obliczenia przesunięcia CMPSS",
    ),
    "Błąd EEPROM": ("EEPROM fault", "Błąd EEPROM"),
    "Błąd PV": ("PV fault", "Błąd PV"),
    "Błąd baterii": ("Battery fault", "Błąd baterii"),
    "Nieprawidłowe zasilanie pomocnicze": (
        "Invalid auxiliary supply",
        "Nieprawidłowe zasilanie pomocnicze",
    ),
    "Zabezpieczenie przekaźnika sieci": (
        "Grid relay protection",
        "Zabezpieczenie przekaźnika sieci",
    ),
    "Nieprawidłowa szyna DC": ("Invalid DC bus", "Nieprawidłowa szyna DC"),
    "Błąd wentylatora": ("Fan fault", "Błąd wentylatora"),
    "Niezgodny typ urządzenia": (
        "Device type mismatch",
        "Niezgodny typ urządzenia",
    ),
    "Niezgodna wersja": ("Version mismatch", "Niezgodna wersja"),
    "Nieprawidłowe okablowanie": ("Invalid wiring", "Nieprawidłowe okablowanie"),
    "Napięcie PV wyższe od napięcia szyny": (
        "PV voltage above bus voltage",
        "Napięcie PV wyższe od napięcia szyny",
    ),
    "Błąd sterownika mocy": ("Power driver fault", "Błąd sterownika mocy"),
    "Nieprawidłowa temperatura": (
        "Invalid temperature",
        "Nieprawidłowa temperatura",
    ),
    "Zabezpieczenie przekaźnika EPS": (
        "EPS relay protection",
        "Zabezpieczenie przekaźnika EPS",
    ),
    "Niski poziom energii baterii": (
        "Low battery energy",
        "Niski poziom energii baterii",
    ),
    "Nieprawidłowe napięcie EPS": (
        "Invalid EPS voltage",
        "Nieprawidłowe napięcie EPS",
    ),
    "Przeciążenie EPS": ("EPS overload", "Przeciążenie EPS"),
    "Błąd generatora": ("Generator fault", "Błąd generatora"),
    "Przeciążenie generatora": (
        "Generator overload",
        "Przeciążenie generatora",
    ),
    "Nieprawidłowe okablowanie licznika": (
        "Invalid meter wiring",
        "Nieprawidłowe okablowanie licznika",
    ),
    "Przekroczenie czasu komunikacji CAN": (
        "CAN communication timeout",
        "Przekroczenie czasu komunikacji CAN",
    ),
    "Nieudana synchronizacja wstępna": (
        "Pre-synchronization failed",
        "Nieudana synchronizacja wstępna",
    ),
    "Brak synchronizacji PLL": ("PLL synchronization lost", "Brak synchronizacji PLL"),
    "Nieprawidłowe napięcie sieci": (
        "Invalid grid voltage",
        "Nieprawidłowe napięcie sieci",
    ),
    "Za wysokie napięcie sieci": (
        "Grid overvoltage",
        "Za wysokie napięcie sieci",
    ),
    "Za niskie napięcie sieci": ("Grid undervoltage", "Za niskie napięcie sieci"),
    "Przekroczenie napięcia 10-minutowego": (
        "10-minute voltage limit exceeded",
        "Przekroczenie napięcia 10-minutowego",
    ),
    "Za wysoka częstotliwość sieci": (
        "Grid frequency too high",
        "Za wysoka częstotliwość sieci",
    ),
    "Za niska częstotliwość sieci": (
        "Grid frequency too low",
        "Za niska częstotliwość sieci",
    ),
    "Zbyt szybka zmiana częstotliwości": (
        "Frequency changes too quickly",
        "Zbyt szybka zmiana częstotliwości",
    ),
    "Nieudane ponowne połączenie": (
        "Reconnection failed",
        "Nieudane ponowne połączenie",
    ),
    "Zanik sieci": ("Grid lost", "Zanik sieci"),
    "Niepodłączony przewód neutralny N": (
        "Neutral conductor N disconnected",
        "Niepodłączony przewód neutralny N",
    ),
    "Zwarcie faza–PE": ("Phase-to-PE short circuit", "Zwarcie faza–PE"),
    "Zwarcie N–PE": ("N-to-PE short circuit", "Zwarcie N–PE"),
    "Błąd autotestu AFCI": ("AFCI self-test fault", "Błąd autotestu AFCI"),
    "Błąd łuku AFCI": ("AFCI arc fault", "Błąd łuku AFCI"),
    "Błąd izolacji": ("Insulation fault", "Błąd izolacji"),
    "Błąd autotestu RCD": ("RCD self-test fault", "Błąd autotestu RCD"),
    "Błąd RCD": ("RCD fault", "Błąd RCD"),
    "Błąd komunikacji Flash": (
        "Flash communication fault",
        "Błąd komunikacji Flash",
    ),
    "Składowa DC napięcia off-grid poza zakresem": (
        "Off-grid voltage DC component out of range",
        "Składowa DC napięcia off-grid poza zakresem",
    ),
    "Składowa DC prądu sieci poza zakresem": (
        "Grid current DC component out of range",
        "Składowa DC prądu sieci poza zakresem",
    ),
    "Przepełnienie przerwania": ("Interrupt overflow", "Przepełnienie przerwania"),
    "Błąd mikrokontrolera": ("Microcontroller fault", "Błąd mikrokontrolera"),
    "Za niska temperatura otoczenia": (
        "Ambient temperature too low",
        "Za niska temperatura otoczenia",
    ),
    "Przegrzanie": ("Overtemperature", "Przegrzanie"),
    "Brak komunikacji DSP_PWR": (
        "No DSP_PWR communication",
        "Brak komunikacji DSP_PWR",
    ),
    "Brak komunikacji DSP_SAF": (
        "No DSP_SAF communication",
        "Brak komunikacji DSP_SAF",
    ),
    "Brak komunikacji z licznikiem PV": (
        "No PV meter communication",
        "Brak komunikacji z licznikiem PV",
    ),
    "Brak komunikacji z licznikiem sieci": (
        "No grid meter communication",
        "Brak komunikacji z licznikiem sieci",
    ),
    "Brak komunikacji BMS CAN": (
        "No BMS CAN communication",
        "Brak komunikacji BMS CAN",
    ),
    "Za niska wersja DTU": ("DTU version too old", "Za niska wersja DTU"),
    "Brak komunikacji DTS": ("No DTS communication", "Brak komunikacji DTS"),
    "Nieprawidłowy typ licznika sieci": (
        "Invalid grid meter type",
        "Nieprawidłowy typ licznika sieci",
    ),
    "Nieprawidłowy typ licznika PV": (
        "Invalid PV meter type",
        "Nieprawidłowy typ licznika PV",
    ),
    "Błędne podłączenie baterii urządzenia podrzędnego": (
        "Slave-device battery wiring fault",
        "Błędne podłączenie baterii urządzenia podrzędnego",
    ),
    "Brak komunikacji DTU1 RS485": (
        "No DTU1 RS485 communication",
        "Brak komunikacji DTU1 RS485",
    ),
    "Brak komunikacji DTU2 RS485": (
        "No DTU2 RS485 communication",
        "Brak komunikacji DTU2 RS485",
    ),
    "Brak komunikacji DTU3 RS485": (
        "No DTU3 RS485 communication",
        "Brak komunikacji DTU3 RS485",
    ),
    "Brak komunikacji DTU4 RS485": (
        "No DTU4 RS485 communication",
        "Brak komunikacji DTU4 RS485",
    ),
    "Brak komunikacji DTU5 RS485": (
        "No DTU5 RS485 communication",
        "Brak komunikacji DTU5 RS485",
    ),
    "Brak komunikacji z ładowarką EV1": (
        "No EV1 charger communication",
        "Brak komunikacji z ładowarką EV1",
    ),
    "Brak komunikacji z ładowarką EV2": (
        "No EV2 charger communication",
        "Brak komunikacji z ładowarką EV2",
    ),
    "Błąd ładowarki EV1": ("EV1 charger fault", "Błąd ładowarki EV1"),
    "Błąd ładowarki EV2": ("EV2 charger fault", "Błąd ładowarki EV2"),
    "Trwale ujemna moc obciążenia": (
        "Persistently negative load power",
        "Trwale ujemna moc obciążenia",
    ),
    "Nieprawidłowa pojemność baterii": (
        "Invalid battery capacity",
        "Nieprawidłowa pojemność baterii",
    ),
}


def localized_text_state(raw_state: str, language: str) -> str:
    """Return a stable translated state or a localized dynamic fault string."""
    use_polish = language.casefold().startswith("pl")
    if translated := SIMPLE_STATE_TRANSLATIONS.get(raw_state):
        return translated[1 if use_polish else 0]

    if "; " in raw_state:
        parts = raw_state.split("; ")
        return "; ".join(
            FAULT_TRANSLATIONS.get(part, (part, part))[1 if use_polish else 0]
            for part in parts
        )

    dynamic_prefixes = {
        "Kod błędu programu:": "Software fault code:",
        "Kod błędu sprzętu:": "Hardware fault code:",
        "Kod błędu BMS:": "BMS fault code:",
        "Błąd w zarezerwowanym bicie:": "Fault in a reserved bit:",
        "Nieznany stan": "Unknown state",
        "Nieznany typ": "Unknown type",
    }
    if not use_polish:
        for polish, english in dynamic_prefixes.items():
            if raw_state.startswith(polish):
                return raw_state.replace(polish, english, 1)

    translated = FAULT_TRANSLATIONS.get(raw_state)
    if translated:
        return translated[1 if use_polish else 0]
    return raw_state
