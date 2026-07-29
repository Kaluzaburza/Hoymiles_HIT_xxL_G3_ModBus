"""Build the HACS entity catalog and bundled Home Assistant assets.

The ESPHome YAML files remain the source of truth for entity names.  This script
extracts public entities, creates stable translation keys and writes the English
and Polish translation files used by the HACS integration.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"
TRANSLATIONS = COMPONENT / "translations"
RESOURCES = COMPONENT / "resources"

SUPPORTED_SOURCE_DOMAINS = {
    "button",
    "sensor",
    "text_sensor",
    "number",
    "select",
}
SKIPPED_FILES = {
    "optional_battery_legacy.yaml",
    "optional_battery_pack_diagnostics.yaml",
}

SPECIAL_NAMES = {
    "Tryb EMS": ("ems_mode", "EMS mode", "Tryb EMS"),
}

PHRASE_TRANSLATIONS = {
    "Overview Internal PV Total Power": "Podgląd łącznej mocy wewnętrznych wejść PV",
    "Overview External PV Total Power": "Podgląd łącznej mocy zewnętrznych źródeł PV",
    "Overview Grid Total Active Power": "Podgląd łącznej mocy czynnej sieci",
    "Overview Generator Active Power": "Podgląd mocy czynnej generatora",
    "Overview Load Active Power": "Podgląd mocy czynnej odbiorników",
    "Overview Smart Load Active Power": "Podgląd mocy czynnej Smart Load",
    "Overview Battery Power": "Podgląd mocy baterii",
    "Overview Battery SOC": "Podgląd stanu naładowania baterii",
    "Overview Inverter Active Power": "Podgląd mocy czynnej falownika",
    "Overview PV Total Power": "Podgląd łącznej mocy PV",
    "Grid Input Power Limitation (Valley)": "Ograniczenie mocy pobieranej z sieci (taryfa dolinowa)",
    "SOC Start Charge From Grid": "SOC rozpoczęcia ładowania z sieci",
    "Low SOC Grid Charge Power": "Moc ładowania z sieci przy niskim SOC",
    "Battery Max Charge Power": "Maksymalna moc ładowania baterii",
    "Battery Max Discharge Power": "Maksymalna moc rozładowania baterii",
    "Battery Current (BMS)": "Prąd baterii (BMS)",
    "Battery Power (BMS)": "Moc baterii (BMS)",
    "Battery Voltage (BMS)": "Napięcie baterii (BMS)",
    "Battery Current (Inverter)": "Prąd baterii (falownik)",
    "Battery 1 Voltage": "Napięcie baterii (falownik)",
    "Maximum Charge Power": "Maksymalna moc ładowania z sieci",
    "Maximum Discharge Power": "Maksymalna moc rozładowania do sieci",
    "Force Charge SOC": "Docelowy SOC ładowania z sieci",
    "Force Discharge SOC": "Minimalny SOC rozładowania do sieci",
    "Self-Use SOC": "Rezerwa SOC dla autokonsumpcji",
    "Three Phase Unbalance": "Asymetria trójfazowa",
    "Battery Type Setting": "Typ baterii",
    "BMS Type Setting": "Typ BMS",
    "Parallel Networking Command": "Polecenie sieci równoległej",
    "Parallel Topology": "Topologia sieci równoległej",
    "Parallel EMS Control Status": "Stan sterowania EMS sieci równoległej",
    "Machines Type": "Rola urządzenia w sieci równoległej",
    "Number of Machines (Master and Slave)": "Liczba falowników (Master i Slave)",
    "Communication Address 1 (Master Device)": "Adres komunikacyjny 1 (Master)",
    "Communication Address 2 (Slave Device 1)": "Adres komunikacyjny 2 (Slave 1)",
    "Communication Address 3 (Slave Device 2)": "Adres komunikacyjny 3 (Slave 2)",
    "Communication Address 4 (Slave Device 3)": "Adres komunikacyjny 4 (Slave 3)",
    "Communication Address 5 (Slave Device 4)": "Adres komunikacyjny 5 (Slave 4)",
    "Communication Address 6 (Slave Device 5)": "Adres komunikacyjny 6 (Slave 5)",
    "Communication Address 7 (Slave Device 6)": "Adres komunikacyjny 7 (Slave 6)",
    "Communication Address 8 (Slave Device 7)": "Adres komunikacyjny 8 (Slave 7)",
    "Communication Address 9 (Slave Device 8)": "Adres komunikacyjny 9 (Slave 8)",
    "Communication Address 10 (Slave Device 9)": "Adres komunikacyjny 10 (Slave 9)",
    "System Operation": "Praca systemu",
    "Inverter Work Status": "Stan pracy falownika",
    "Overview System Work Status": "Podgląd stanu pracy systemu",
    "Overview Battery Work Status": "Podgląd stanu pracy baterii",
    "Battery Work Status (BMS)": "Stan pracy baterii (BMS)",
    "Battery Link Status": "Stan łącza baterii",
    "PV Link Status": "Stan łącza PV",
    "Meter Link Status": "Stan łącza licznika",
    "Battery Type (BMS)": "Typ baterii (BMS)",
    "Battery Fault Code (BMS)": "Kod błędu baterii (BMS)",
    "Overview Battery Faults": "Podgląd błędów baterii",
    "Overview DSP Power Faults": "Podgląd błędów DSP mocy",
    "DSP Safety Faults": "Błędy DSP zabezpieczeń",
    "ARM Communication Faults": "Błędy komunikacji ARM",
    "ARM Peripheral Faults": "Błędy urządzeń peryferyjnych ARM",
    "ARM System Faults": "Błędy systemowe ARM",
    "SW Fault": "Błąd oprogramowania",
    "HW Fault": "Błąd sprzętowy",
    "Clear Fault": "Wyczyść alarmy falownika",
}

WORD_TRANSLATIONS = {
    "Active": "czynna",
    "Address": "adres",
    "Apparent": "pozorna",
    "Battery": "bateria",
    "BMS": "BMS",
    "Bus": "magistrala",
    "Buy": "pobór",
    "Capacity": "pojemność",
    "Charge": "ładowanie",
    "Charging": "ładowanie",
    "Communication": "komunikacja",
    "Current": "prąd",
    "Daily": "dzienna",
    "Day": "dzień",
    "Discharge": "rozładowanie",
    "Energy": "energia",
    "External": "zewnętrzne",
    "Fault": "błąd",
    "Faults": "błędy",
    "Frequency": "częstotliwość",
    "Generator": "generator",
    "Grid": "sieć",
    "Input": "wejście",
    "Internal": "wewnętrzne",
    "Inverter": "falownik",
    "Link": "łącze",
    "Load": "odbiorniki",
    "Maximum": "maksymalna",
    "Minimum": "minimalna",
    "Mode": "tryb",
    "Operation": "praca",
    "Output": "wyjście",
    "Overview": "podgląd",
    "Power": "moc",
    "Reactive": "bierna",
    "Sell": "oddawanie",
    "Setting": "ustawienie",
    "Smart": "Smart",
    "Status": "stan",
    "Temperature": "temperatura",
    "Today": "dzisiaj",
    "Total": "łącznie",
    "Type": "typ",
    "Voltage": "napięcie",
    "Work": "pracy",
}

OPTION_TRANSLATIONS = {
    "Autokonsumpcja (Self-Use)": ("self_use", "Self-Use", "Autokonsumpcja"),
    "Self-Use": ("self_use", "Self-Use", "Autokonsumpcja"),
    "Off-Grid": ("off_grid", "Off-Grid", "Praca wyspowa"),
    "Ładowanie z sieci": ("grid_charge", "Grid charge", "Ładowanie z sieci"),
    "Grid Charge": ("grid_charge", "Grid charge", "Ładowanie z sieci"),
    "Rozładowanie do sieci": (
        "grid_discharge",
        "Grid discharge",
        "Rozładowanie do sieci",
    ),
    "Grid Discharge": (
        "grid_discharge",
        "Grid discharge",
        "Rozładowanie do sieci",
    ),
    "Disabled": ("disabled", "Disabled", "Wyłączone"),
    "Enabled": ("enabled", "Enabled", "Włączone"),
    "Inactive": ("inactive", "Inactive", "Nieaktywne"),
    "Start": ("start", "Start", "Uruchom"),
    "Stop": ("stop", "Stop", "Zatrzymaj"),
    "Create Network": ("create_network", "Create network", "Utwórz sieć"),
    "Disassemble Network": (
        "disassemble_network",
        "Disassemble network",
        "Rozłącz sieć",
    ),
    "No Battery": ("no_battery", "No battery", "Brak baterii"),
    "Lithium": ("lithium", "Lithium-ion", "Litowo-jonowa"),
    "Lead-Acid": ("lead_acid", "Lead-acid", "Kwasowo-ołowiowa"),
    "Not Configured": ("not_configured", "Not configured", "Nieskonfigurowane"),
}

TEXT_STATE_TRANSLATIONS = {
    "unavailable": ("Unavailable", "Niedostępne"),
    "power_initialization": ("Power initialization", "Inicjalizacja zasilania"),
    "standby": ("Standby", "Czuwanie"),
    "grid_test": ("Grid test", "Test sieci"),
    "on_grid": ("On-grid operation", "Praca z siecią"),
    "fault": ("Fault", "Awaria"),
    "off_grid": ("Off-grid operation", "Praca wyspowa"),
    "bypass": ("Bypass", "Bypass"),
    "no_error": ("No error", "Brak błędu"),
    "no_errors": ("No faults", "Brak błędów"),
    "offline": ("Offline", "Offline"),
    "online": ("Online", "Online"),
    "hmid_connected": ("HMID: connected", "HMID: połączenie prawidłowe"),
    "hmid_connection_error": (
        "HMID: connection error",
        "HMID: błąd połączenia",
    ),
    "no_battery": ("No battery", "Brak baterii"),
    "lithium": ("Lithium-ion", "Litowa"),
    "lead_acid": ("Lead-acid", "Kwasowo-ołowiowa"),
    "simulated": ("Simulated", "Symulowana"),
    "charging": ("Charging", "Ładowanie"),
    "discharging": ("Discharging", "Rozładowanie"),
    "single_inverter": ("Single inverter", "Falownik pojedynczy"),
    "parallel_master": ("Master", "Master"),
    "parallel_slave": ("Slave", "Slave"),
    "unknown_topology": ("Unknown topology", "Nieznana topologia"),
    "invalid_topology": ("Invalid topology", "Nieprawidłowa topologia"),
    "parallel_ems_ready_direct": (
        "Ready - direct control",
        "Gotowe - sterowanie bezpośrednie",
    ),
    "parallel_ems_ready_master": (
        "Ready - Master controls the parallel network",
        "Gotowe - Master steruje siecią równoległą",
    ),
    "parallel_ems_blocked_slave": (
        "Blocked - ESP32 connected to Slave",
        "Zablokowane - ESP32 podłączone do Slave",
    ),
    "parallel_ems_waiting": (
        "Waiting for network detection",
        "Oczekiwanie na wykrycie sieci",
    ),
    "parallel_ems_waiting_master_mode": (
        "Waiting for Master EMS mode",
        "Oczekiwanie na tryb EMS Mastera",
    ),
    "parallel_ems_waiting_slave_confirmation": (
        "Waiting for Slave confirmation",
        "Oczekiwanie na potwierdzenie Slave",
    ),
    "parallel_address_error": (
        "Parallel-network address error",
        "Błąd adresów sieci równoległej",
    ),
    "parallel_slave_offline": (
        "Error - Slave is not responding",
        "Błąd - Slave nie odpowiada",
    ),
    "parallel_slave_ems_mismatch": (
        "Error - Slave EMS mode mismatch",
        "Błąd - tryb EMS Slave niezgodny",
    ),
    "parallel_invalid_count": (
        "Invalid inverter count",
        "Nieprawidłowa liczba falowników",
    ),
}

ENGLISH_REPLACEMENTS = {
    "Najważniejsze dane do codziennej obsługi falownika. Szczegółowe": (
        "The most important values for everyday inverter operation. Detailed"
    ),
    "rejestry są w osobnych zakładkach.": "registers are available on separate tabs.",
    "Automatyka wymaga zewnętrznej integracji": (
        "The automation requires the external integration"
    ),
    "Wymagane są prognozy **Forecast Today** i **Forecast Tomorrow**.": (
        "**Forecast Today** and **Forecast Tomorrow** are required."
    ),
    "Encje są wykrywane automatycznie w polskiej i angielskiej wersji HA;": (
        "The entities are detected automatically in Polish and English HA;"
    ),
    "w razie niestandardowych nazw można wpisać je u góry.": (
        "custom entity ids can be entered above."
    ),
    "Algorytm symuluje bilans co 30 minut od teraz przez dzisiejszą noc,": (
        "The optimizer simulates the balance every 30 minutes from now through tonight,"
    ),
    "cały kolejny dzień i następną noc do **90 minut po wschodzie**.": (
        "the whole next day and the following night until **90 minutes after sunrise**."
    ),
    "Najpierw zabezpiecza zasilanie domu, rezerwę awaryjną Self-Use oraz": (
        "It first protects the home supply, the Self-Use outage reserve, and"
    ),
    "korektę bezpieczeństwa. Dopiero pozostałą energię przydziela do": (
        "the safety correction. Only the remaining energy is assigned to"
    ),
    "najdroższych dostępnych bloków RCE dzisiaj lub jutro.": (
        "the most valuable available RCE slots today or tomorrow."
    ),
    "wykryta liczba falowników × ustawiony procent rozładowania**.": (
        "detected inverter count × configured discharge percentage**."
    ),
    "Przez pierwsze 24 godziny używane jest awaryjne zużycie dobowe z pola": (
        "For the first 24 hours, the fallback daily consumption entered"
    ),
    "u góry; później zastępuje je średnia z ostatnich czterech dni.": (
        "above is used; it is then replaced by the four-day average."
    ),
    "Pole rezerwy Self-Use nie jest zmieniane. Pozostaje energią dostępną": (
        "The Self-Use reserve setting is not changed. This energy remains available"
    ),
    "poniżej normalnego progu wyłącznie podczas zaniku sieci.": (
        "below the normal threshold only during a grid outage."
    ),
    "Automatyka wróci do **Autokonsumpcji (Self-Use)** po zakończeniu": (
        "The automation returns to **Self-Use** after the selected"
    ),
    "wybranego bloku, po osiągnięciu minimalnego SOC albo po wyłączeniu": (
        "slot ends, the minimum SOC is reached, or the"
    ),
    "przełącznika. Blokada sprzedaży wyklucza wskazane godziny z planu.": (
        "switch is disabled. The export lockout excludes the configured hours."
    ),
    "Automatyka RCE — ustawienia": "RCE automation — settings",
    "Automatyka RCE — konfiguracja wymagana": (
        "RCE automation — required configuration"
    ),
    "Włącz automatyczne rozładowanie według RCE": "Enable automatic discharge using RCE prices",
    "Minimalna cena sprzedaży": "Minimum export price",
    "Moc znamionowa jednego falownika": "Rated power of one inverter",
    "Moc rozładowania do sieci": "Grid discharge power",
    "Encja Solcast — prognoza na dzisiaj": "Solcast entity — today forecast",
    "Minimalny SOC akumulatora": "Minimum battery SOC",
    "Zużycie dobowe przed zebraniem historii": (
        "Daily consumption before history is available"
    ),
    "Dynamiczna rezerwa SOC": "Dynamic SOC reserve",
    "Automatycznie wyliczaj minimalny SOC": "Automatically calculate minimum SOC",
    "Korekta bezpieczeństwa SOC (+)": "SOC safety correction (+)",
    "Obowiązujący minimalny SOC": "Effective minimum SOC",
    "Próg zapisany do falownika": "Threshold written to the inverter",
    "Prognoza energii i rezerwa SOC": "Energy forecast and SOC reserve",
    "Encja Solcast — prognoza na jutro": "Solcast entity — tomorrow forecast",
    "RCE — stan, prognoza i wynik": "RCE — status, forecast and result",
    "Stan optymalizatora": "Optimizer status",
    "Prognozowana produkcja PV dzisiaj": "Forecast PV production today",
    "Pozostała prognozowana produkcja dzisiaj": (
        "Remaining forecast PV production today"
    ),
    "Prognozowana produkcja PV jutro": "Forecast PV production tomorrow",
    "Średnie dobowe zużycie LOAD — 4 dni": "Average daily LOAD — 4 days",
    "Pomiar zużycia okna nocnego aktywny": (
        "Night-window consumption sampling active"
    ),
    "Pełne okno nocne — zachód/wschód ± 1,5 h": (
        "Full night window — sunset/sunrise ± 1.5 h"
    ),
    "Chroniony czas pracy domu — najbliższa noc": (
        "Protected home runtime — nearest night"
    ),
    "Średnie zużycie w oknie nocnym — 4 dni": (
        "Average protected-window consumption — 4 days"
    ),
    "Energia potrzebna do rozpoczęcia produkcji PV": (
        "Energy required until PV production starts"
    ),
    "Przewidywany deficyt energii jutro": "Expected energy deficit tomorrow",
    "Deficyt dobowy według prognozy PV": "Daily deficit from PV forecast",
    "Energia domu chroniona przed sprzedażą": (
        "Home energy protected from export"
    ),
    "Energia wysłana do sieci dzisiaj": "Energy exported to grid today",
    "Szacunkowy przychód RCE dzisiaj": "Estimated RCE revenue today",
    "Pojemność magazynu z falownika": "Battery capacity from inverter",
    "Rezerwa awaryjna SOC — zanik sieci": "Outage SOC reserve",
    "Wyliczony minimalny SOC dla RCE": "Calculated minimum SOC for RCE",
    "Dane rezerwy kompletne": "Reserve data complete",
    "Stan wyliczania rezerwy": "Reserve calculation status",
    "Wymagane źródło prognozy": "Required forecast source",
    "Dynamiczna rezerwa wymaga zewnętrznej integracji": (
        "Dynamic reserve requires the external integration"
    ),
    "producenta **BJReplay**:": "by **BJReplay**:",
    "instalacja i konfiguracja": "installation and configuration",
    "Domyślna encja to": "The default entity is",
    "(HA po angielsku) albo": "(English HA) or",
    "(HA po polsku).": "(Polish HA).",
    "instalacji ma inną nazwę, wpisz ją w polu": (
        "installation uses a different name, enter it in the"
    ),
    "**Encja Solcast**.": "**Solcast entity** field.",
    "instalacji ma inną nazwę, wpisz ją w polu **Encja Solcast**.": (
        "installation uses a different name, enter it in the **Solcast entity** field."
    ),
    "Jeśli w Twojej": "If your",
    "Wyliczenie:": "Calculation:",
    "**rezerwa awaryjna Self-Use + deficyt LOAD względem prognozy PV +": (
        "**Self-Use outage reserve + LOAD deficit against the PV forecast +"
    ),
    "**rezerwa awaryjna Self-Use + pozostałe zużycie domu w najbliższym": (
        "**Self-Use outage reserve + remaining home demand in the nearest"
    ),
    "oknie nocnym + dodatkowy dobowy deficyt prognozy PV + korekta": (
        "night window + the additional daily PV forecast deficit +"
    ),
    "korekta bezpieczeństwa użytkownika**.": "user safety correction**.",
    "bezpieczeństwa użytkownika**.": "user safety correction**.",
    "Okno nocne rozpoczyna się **90 minut przed zachodem** i kończy": (
        "The night window starts **90 minutes before sunset** and ends"
    ),
    "**90 minut po wschodzie słońca**. W ciągu dnia automatyka chroni": (
        "**90 minutes after sunrise**. During the day, the automation protects"
    ),
    "energię dla całego nadchodzącego okna nocnego. Od 90 minut przed": (
        "energy for the entire upcoming night window. From 90 minutes before"
    ),
    "zachodem chroniona energia maleje wraz z pozostałym czasem, dzięki": (
        "sunset, protected energy decreases with the remaining time, so the"
    ),
    "czemu automat może sprzedać tylko tę część magazynu, która nie będzie": (
        "automation can export only the part of the battery that the home will not"
    ),
    "potrzebna domowi do rozpoczęcia produkcji PV.": (
        "need before PV production starts."
    ),
    "tylko tę część magazynu, która nie będzie potrzebna domowi do": (
        "only the part of the battery that the home will not need before"
    ),
    "rozpoczęcia produkcji PV.": "PV production starts.",
    "Przez pierwszą dobę zużycie nocne jest szacowane z czterodniowej": (
        "For the first day, night consumption is estimated from the four-day"
    ),
    "średniej dobowej. Później automat korzysta z rzeczywistego zużycia": (
        "daily average. Afterwards, the automation uses measured consumption"
    ),
    "z maksymalnie czterech ostatnich chronionych okien. Korekta jest": (
        "from up to four previous protected windows. The correction is"
    ),
    "dodawana w punktach procentowych. Przy brakujących danych Solcast,": (
        "added in percentage points. If Solcast, LOAD, sun, or battery"
    ),
    "LOAD, słońca lub pojemności magazynu sprzedaż RCE zostanie bezpiecznie": (
        "capacity data is missing, RCE export is safely"
    ),
    "zablokowana.": "blocked.",
    "Korekta jest dodawana w punktach procentowych. Przy brakujących": (
        "The correction is added in percentage points. If Solcast data,"
    ),
    "danych Solcast, historii LOAD lub pojemności magazynu automatyczna": (
        "LOAD history, or battery capacity is missing, automatic RCE"
    ),
    "sprzedaż RCE zostanie bezpiecznie zablokowana.": (
        "export is safely blocked."
    ),
    "Blokada sprzedaży": "Export lockout",
    "Włącz blokadę sprzedaży": "Enable export lockout",
    "Początek blokady": "Lockout start",
    "Koniec blokady": "Lockout end",
    "Blokada aktywna teraz": "Lockout active now",
    "Bieżąca cena RCE": "Current RCE price",
    "Stan automatyki": "Automation status",
    "RCE — ceny i plan rozładowania": "RCE — prices and discharge plan",
    "RCE — dzisiaj": "RCE — today",
    "RCE — jutro": "RCE — tomorrow",
    "Plan rozładowań RCE": "RCE discharge plan",
    "**Horyzont:**": "**Horizon:**",
    "**Najbardziej opłacalne okresy wybrane przez algorytm:**": (
        "**Most profitable periods selected by the optimizer:**"
    ),
    "Brak danych do przygotowania planu.": "No data available to build a plan.",
    "**Łączny plan:**": "**Total plan:**",
    "bloków po 30 min": "30-minute slots",
    "**Prognozowany eksport:**": "**Forecast export:**",
    "**Szacunkowy przychód:**": "**Estimated revenue:**",
    "Rozładowanie do sieci": "Grid discharge",
    "Ładowanie z sieci": "Grid charge",
    "Włącz harmonogram codzienny": "Enable daily schedule",
    "Godzina rozpoczęcia": "Start time",
    "Czas trwania": "Duration",
    "Pozostały czas bieżącego cyklu": "Current cycle remaining time",
    "Uruchom rozładowanie teraz": "Start grid discharge now",
    "Uruchom ładowanie teraz": "Start grid charge now",
    "Codzienne harmonogramy EMS": "Daily EMS schedules",
    "Sterowanie i zakończenie EMS": "EMS control and stop",
    "Tryb pracy": "Operating mode",
    "Zatrzymaj i wróć do Self-Use": "Stop and return to Self-Use",
    "Bieżący przepływ mocy": "Current power flow",
    "Bieżący przepływ energii": "Current energy flow",
    "name: Dom": "name: Load",
    "name: Sieć": "name: Grid",
    "name: Bateria (A)": "name: Battery (A)",
    "name: Bateria": "name: Battery",
    "Napięcie baterii (falownik)": "Battery voltage (inverter)",
    "Prąd baterii (falownik)": "Battery current (inverter)",
    "Podsumowanie dzienne": "Daily summary",
    "Produkcja dzisiaj": "Production today",
    "Do domu": "To home",
    "Do baterii": "To battery",
    "Do sieci": "To grid",
    "Stan systemu i łączność": "System and connectivity",
    "title: Sterowanie": "title: Control",
    "Alarmy — szybki podgląd": "Alarms — quick view",
    "Wyczyść alarmy falownika": "Clear inverter faults",
    "Stany komunikacji i pracy": "Communication and operating states",
    "Alarmy falownika i baterii": "Inverter and battery alarms",
    "Stan pracy falownika": "Inverter operating state",
    "Stan pracy systemu": "System operating state",
    "Łącze licznika": "Meter link",
    "Łącze PV": "PV link",
    "Łącze baterii": "Battery link",
    "Stan pracy baterii (BMS)": "Battery operating state (BMS)",
    "Stan pracy baterii": "Battery operating state",
    "Typ baterii (BMS)": "Battery type (BMS)",
    "Parametry ochrony i temperatury": "Protection parameters and temperatures",
    "Temperatura radiatora falownika": "Inverter heatsink temperature",
    "Temperatura radiatora toru baterii": "Battery-stage heatsink temperature",
    "Rezystancja izolacji": "Insulation resistance",
    "Wyprodukowano dzisiaj": "Generated today",
    "Moc — ostatnie 24 godziny [W]": "Power — last 24 hours [W]",
    "name: Moc": "name: Power",
    "Odbiory — moc": "Loads — power",
    "Odbiory — energia": "Loads — energy",
    "Maksymalna temperatura celi": "Maximum cell temperature",
    "Minimalna temperatura celi": "Minimum cell temperature",
    "Bateria — energia": "Battery — energy",
    "Licznik sieci": "Grid meter",
    "Ustawienia falownika i baterii": "Inverter and battery settings",
    "Błąd oprogramowania": "Software fault",
    "Błąd sprzętowy": "Hardware fault",
    "Błąd baterii (BMS)": "Battery fault (BMS)",
    "Błędy baterii": "Battery faults",
    "Błędy DSP mocy": "DSP power faults",
    "Błędy DSP zabezpieczeń": "DSP safety faults",
    "Błędy komunikacji ARM": "ARM communication faults",
    "Błędy urządzeń peryferyjnych ARM": "ARM peripheral faults",
    "Błędy systemowe ARM": "ARM system faults",
    "Praca z siecią": "On-grid operation",
    "HMID: połączenie prawidłowe": "HMID: connected",
    "Czuwanie": "Standby",
    "Ładowanie": "Charging",
    "Rozładowanie": "Discharging",
    "Parametry ochronne i temperatury": "Protection parameters and temperatures",
    "Napięcie L1": "L1 voltage",
    "Napięcie L2": "L2 voltage",
    "Napięcie L3": "L3 voltage",
    "Prąd L1": "L1 current",
    "Prąd L2": "L2 current",
    "Prąd L3": "L3 current",
    "Prąd łączny (suma faz)": "Total current (sum of phases)",
    "Moc L1": "L1 power",
    "Moc L2": "L2 power",
    "Moc L3": "L3 power",
    "Moc łączna": "Total power",
    "Częstotliwość": "Frequency",
    "Temperatura wnętrza falownika (CAV)": "Inverter internal temperature (CAV)",
    "Napięcie PP": "PP voltage",
    "Prąd różnicowy": "Residual current",
    "Moc stringów PV — ostatnie 24 godziny [W]": "PV string power — last 24 hours [W]",
    "Napięcie": "Voltage",
    "Prąd": "Current",
    "PV — moc z bloku energii i źródła zewnętrzne": "PV — energy-block and external-source power",
    "Sieć": "Grid",
    "Przepływy": "Flows",
    "Przepływy — moc": "Flows — power",
    "Przepływy — energia dzisiaj": "Flows — energy today",
    "Przepływy — energia całkowita": "Flows — total energy",
    "Licznik zewnętrznego PV": "External PV meter",
    "EMS — bezpieczny zapis całego bloku 4300–4306": "EMS — safe full-block write 4300–4306",
    "Overview — skrócone rejestry mocy": "Overview — essential power registers",
    "Sieć równoległa falowników": "Parallel inverter network",
    'name: "Topologia sieci"': 'name: "Network topology"',
    'name: "Gotowość sterowania EMS"': 'name: "EMS control readiness"',
    'name: "Typ urządzenia (kod)"': 'name: "Device type (code)"',
    'name: "Wykryta liczba falowników"': 'name: "Detected inverter count"',
    'name: "Adres 1 (Master)"': 'name: "Address 1 (Master)"',
    'name: "Adres 2 (Slave 1)"': 'name: "Address 2 (Slave 1)"',
    'name: "Adres 3 (Slave 2)"': 'name: "Address 3 (Slave 2)"',
    'name: "Adres 4 (Slave 3)"': 'name: "Address 4 (Slave 3)"',
    'name: "Adres 5 (Slave 4)"': 'name: "Address 5 (Slave 4)"',
    'name: "Adres 6 (Slave 5)"': 'name: "Address 6 (Slave 5)"',
    'name: "Adres 7 (Slave 6)"': 'name: "Address 7 (Slave 6)"',
    'name: "Adres 8 (Slave 7)"': 'name: "Address 8 (Slave 7)"',
    'name: "Adres 9 (Slave 8)"': 'name: "Address 9 (Slave 8)"',
    'name: "Adres 10 (Slave 9)"': 'name: "Address 10 (Slave 9)"',
    "Każdy harmonogram działa codziennie, dopóki jego przełącznik jest": (
        "Each schedule runs every day while its switch is"
    ),
    "włączony. Po upływie ustawionego czasu falownik wraca do": (
        "enabled. When the configured duration ends, the inverter returns to"
    ),
    "Nie ustawiaj ładowania i rozładowania na tę samą godzinę.": (
        "Do not schedule charging and discharging at the same time."
    ),
    "Wyłączenie przełącznika blokuje kolejne uruchomienia.": (
        "Turning a schedule off prevents subsequent starts."
    ),
    "Automatyka wróci do **Autokonsumpcji (Self-Use)** po spadku ceny do": (
        "The automation returns to **Self-Use** when the price falls to"
    ),
    "progu lub niżej, po osiągnięciu minimalnego SOC albo po wyłączeniu": (
        "the threshold or below, when minimum SOC is reached, or when the"
    ),
    "przełącznika. W czasie aktywnej blokady sprzedaży okresy są pomijane,": (
        "switch is disabled. Periods inside the active export lockout are skipped,"
    ),
    "a falownik pozostaje w trybie Self-Use.": (
        "and the inverter remains in Self-Use mode."
    ),
    "**Uwaga:** zmiana `System Operation` może zatrzymać falownik.": (
        "**Warning:** changing `System Operation` can stop the inverter."
    ),
    "Encja `select.hoymiles_hit_parallel_networking_command` jest celowo pominięta,": (
        "The `select.hoymiles_hit_parallel_networking_command` entity is intentionally omitted,"
    ),
    "ponieważ w ESPHome ma `disabled_by_default: true`. Włącz ją w Home": (
        "because ESPHome marks it `disabled_by_default: true`. Enable it in Home"
    ),
    "Assistant tylko wtedy, gdy konfigurujesz pracę równoległą.": (
        "Assistant only when configuring parallel operation."
    ),
    "PV — wartości bieżące": "PV — current values",
    "PV — energia dzisiaj": "PV — energy today",
    "PV — energia całkowita": "PV — total energy",
    "Bateria — stan, moc i limity BMS": "Battery — state, power and BMS limits",
    "Sieć — wartości bieżące": "Grid — current values",
    "Sieć — napięcia i częstotliwość": "Grid — voltages and frequency",
    "Sieć — prądy": "Grid — currents",
    "Sieć — moc": "Grid — power",
    "Sieć — energia dzisiaj": "Grid — energy today",
    "Sieć — energia całkowita": "Grid — total energy",
    "Generator — wartości bieżące": "Generator — current values",
    "Generator — energia dzisiaj": "Generator — energy today",
    "Generator — energia całkowita": "Generator — total energy",
    "Falownik — fazy, moc i magistrala DC": "Inverter — phases, power and DC bus",
    "Rezerwa SOC — Self-Use": "SOC reserve — Self-Use",
    "Docelowy SOC — ładowanie z sieci": "Target SOC — grid charge",
    "Minimalny SOC — rozładowanie do sieci": "Minimum SOC — grid discharge",
    "Maks. moc ładowania z sieci": "Max. grid charge power",
    "Maks. moc rozładowania do sieci": "Max. grid discharge power",
    "brak aktywności": "inactive",
    "Brak kompletnych danych PSE": "No complete PSE data",
    "Brak danych": "No data",
    "Brak błędu": "No error",
    "Litowa": "Lithium",
    "Kwasowo-fosforanowa": "Lead-acid/phosphate",
    "Symulowana": "Simulated",
    "Brak błędów": "No faults",
    "Niedostępne": "Unavailable",
    "Wyłączona": "Disabled",
    "Wyłączone": "Disabled",
    "wyłączona": "disabled",
    "wyłączono": "disabled",
    "Włączona": "Enabled",
    "Włączone": "Enabled",
    "Autokonsumpcja": "Self-Use",
    "Oczekiwanie": "Waiting",
    "Cena powyżej progu": "Price above threshold",
    "Oczekiwanie na cenę": "Waiting for price",
    "osiągnięto minimalny SOC": "minimum SOC reached",
    "aktywny harmonogram ręczny": "manual schedule active",
    "gotowa": "ready",
    "Dzień:": "Day:",
    "Okresy powyżej ustawionego progu:": "Periods above the configured threshold:",
    "Łączny plan:": "Total plan:",
    "godz.": "h",
    "min": "min",
    "Uruchom": "Run",
    "Zatrzymaj": "Stop",
    "codzienne": "daily",
    "rozładowanie": "discharge",
    "ładowanie": "charge",
    "godzina": "time",
    "początek": "start",
    "koniec": "end",
    "trwa cykl": "cycle active",
    "pozostały czas": "remaining time",
    "włączone": "enabled",
    "wybranych godzinach": "selected hours",
    "rozpocznij": "start",
    "zakończ": "finish",
    "wróć": "return",
    "wymuś": "enforce",
    "sterowanie": "control",
    "według ceny": "by price",
    "odzyskaj cykl po restarcie Home Assistant": "restore cycle after Home Assistant restart",
    "EMS — trwa cykl rozładowania": "EMS — discharge cycle active",
    "EMS — trwa cykl ładowania": "EMS — charge cycle active",
    "EMS — blokada sprzedaży w wybranych godzinach": "EMS — export lockout in selected hours",
    "EMS — godzina rozpoczęcia rozładowania": "EMS — discharge start time",
    "EMS — godzina rozpoczęcia ładowania": "EMS — charge start time",
    "EMS — początek blokady sprzedaży": "EMS — export lockout start",
    "EMS — koniec blokady sprzedaży": "EMS — export lockout end",
    "EMS — czas rozładowania": "EMS — discharge duration",
    "EMS — czas ładowania": "EMS — charge duration",
    "EMS RCE — minimalna cena sprzedaży": "EMS RCE — minimum export price",
    "EMS RCE — dynamiczna rezerwa SOC": "EMS RCE — dynamic SOC reserve",
    "EMS RCE — encja prognozy Solcast na dzisiaj": (
        "EMS RCE — Solcast today forecast entity"
    ),
    "EMS RCE — encja prognozy Solcast na jutro": (
        "EMS RCE — Solcast tomorrow forecast entity"
    ),
    "EMS RCE — moc znamionowa jednego falownika": (
        "EMS RCE — rated power of one inverter"
    ),
    "EMS RCE — dobowe zużycie awaryjne przed zebraniem historii": (
        "EMS RCE — fallback daily consumption before history is available"
    ),
    "EMS RCE — sprawność eksportu z baterii": (
        "EMS RCE — battery export efficiency"
    ),
    "EMS RCE — korekta bezpieczeństwa SOC": (
        "EMS RCE — SOC safety correction"
    ),
    "EMS RCE — ustaw domyślną encję Solcast": (
        "EMS RCE — set default Solcast entity"
    ),
    "EMS RCE — zapisz dynamiczną rezerwę SOC": (
        "EMS RCE — write dynamic SOC reserve"
    ),
    "EMS RCE — sterowanie rozładowaniem według ceny": (
        "EMS RCE — price-based discharge control"
    ),
    "Tryb ręczny — używany jest próg rozładowania falownika": (
        "Manual mode — using the inverter discharge threshold"
    ),
    "Gotowa — plan zoptymalizowany": "Ready — optimized plan",
    "Oczekiwanie — brak opłacalnego okna": (
        "Waiting — no profitable window"
    ),
    "Zasilanie domu zabezpieczone — brak energii na sprzedaż": (
        "Home supply protected — no energy available for export"
    ),
    "Za mało energii na potrzeby domu — sprzedaż zablokowana": (
        "Insufficient home energy — export blocked"
    ),
    "Brak wymaganych danych — sprzedaż zablokowana": (
        "Required data missing — export blocked"
    ),
    "Błąd obliczeń — sprzedaż zablokowana": (
        "Calculation error — export blocked"
    ),
    "Brak zaplanowanych okresów sprzedaży.": (
        "No export periods are currently planned."
    ),
    "Brak danych Solcast — sprzedaż zablokowana": (
        "No Solcast data — export blocked"
    ),
    "Za mało historii LOAD — wymagane minimum 24 godziny": (
        "Insufficient LOAD history — at least 24 hours required"
    ),
    "Brak danych wschodu lub zachodu słońca — sprzedaż zablokowana": (
        "Missing sunrise or sunset data — export blocked"
    ),
    "Brak pojemności baterii z falownika — sprzedaż zablokowana": (
        "No inverter battery capacity — export blocked"
    ),
    "Nie można obliczyć rezerwy — sprzedaż zablokowana": (
        "Cannot calculate reserve — export blocked"
    ),
    "Gotowa — dynamiczny próg": "Ready — dynamic threshold",
    "Brak danych rezerwy SOC — sprzedaż zablokowana": (
        "No SOC reserve data — export blocked"
    ),
    "Przy pierwszym uruchomieniu wpisuje standardową encję Forecast Tomorrow": (
        "On first start, sets the standard Forecast Tomorrow entity"
    ),
    "integracji Solcast PV Forecast producenta BJReplay. Późniejsze zmiany": (
        "from the BJReplay Solcast PV Forecast integration. Later changes"
    ),
    "użytkownika są zachowywane.": "made by the user are preserved.",
    "Wykrywa angielską albo polską encję Forecast Tomorrow integracji Solcast": (
        "Detects the English or Polish Forecast Tomorrow entity from the Solcast"
    ),
    "Wykrywa angielskie albo polskie encje Forecast Today i Forecast Tomorrow": (
        "Detects the English or Polish Forecast Today and Forecast Tomorrow entities"
    ),
    "integracji Solcast PV Forecast producenta BJReplay. Poprawne własne encje": (
        "from the BJReplay Solcast PV Forecast integration. Valid custom entities"
    ),
    "PV Forecast producenta BJReplay. Poprawna własna encja użytkownika jest": (
        "PV Forecast integration by BJReplay. A valid custom entity entered by the user is"
    ),
    "zachowywana.": "preserved.",
    "Zapisuje do falownika wyliczony minimalny SOC rozładowania. Próg obejmuje": (
        "Writes the calculated minimum discharge SOC to the inverter. The threshold includes"
    ),
    "Zapisuje do falownika wyliczony minimalny SOC rozładowania. Plan chroni": (
        "Writes the calculated minimum discharge SOC to the inverter. The plan protects"
    ),
    "zasilanie domu od teraz, przez dzisiejszą noc, kolejny dzień i następną": (
        "home supply from now through tonight, the next day, and the following"
    ),
    "noc do 90 minut po wschodzie, a następnie wybiera najdroższe okna RCE.": (
        "night until 90 minutes after sunrise, then selects the most valuable RCE slots."
    ),
    "rezerwę awaryjną Self-Use, energię domu chronioną w nocnym oknie,": (
        "the Self-Use outage reserve, home energy protected in the night window,"
    ),
    "dodatkowy prognozowany deficyt energii następnego dnia i korektę": (
        "the additional forecast energy deficit for the next day, and the"
    ),
    "prognozowany deficyt energii i korektę bezpieczeństwa użytkownika.": (
        "the forecast energy deficit, and the user's safety correction."
    ),
    "rezerwę awaryjną Self-Use, prognozowany deficyt energii i korektę": (
        "the Self-Use outage reserve, forecast energy deficit, and the user's"
    ),
    "bezpieczeństwa użytkownika.": "safety correction.",
    "Rozładowuje tylko powyżej progu i dynamicznej rezerwy SOC. Rezerwa": (
        "Discharges only above the price threshold and dynamic SOC reserve. The reserve"
    ),
    "obejmuje awaryjny SOC Self-Use, deficyt prognozy Solcast względem średniego": (
        "includes the Self-Use outage SOC, the Solcast forecast deficit against average"
    ),
    "obejmuje awaryjny SOC Self-Use, pozostałe zużycie domu w chronionym oknie": (
        "includes the Self-Use outage SOC, remaining home demand in the protected"
    ),
    "nocnym, dodatkowy deficyt prognozy Solcast na następny dzień oraz korektę": (
        "night window, the additional Solcast forecast deficit for the next day,"
        " and the safety"
    ),
    "bezpieczeństwa. Ręczne timery mają pierwszeństwo.": (
        "correction. Manual timers take priority."
    ),
    "nocnym, deficyt prognozy Solcast oraz korektę bezpieczeństwa. Ręczne": (
        "night window, the Solcast forecast deficit, and the safety correction. Manual"
    ),
    "timery mają pierwszeństwo.": "timers take priority.",
    "LOAD oraz korektę bezpieczeństwa. Ręczne timery mają pierwszeństwo.": (
        "LOAD, and the safety correction. Manual timers take priority."
    ),
    "Licznik dzienny LOAD zwiększa się w ciągu dnia i zeruje po północy.": (
        "The daily LOAD meter increases during the day and resets at midnight."
    ),
    "sum_differences_nonnegative sumuje wyłącznie dodatnie przyrosty z ostatnich": (
        "sum_differences_nonnegative sums only positive increments from the last"
    ),
    "96 godzin, uwzględniając reset licznika. Recorder odtwarza bufor po restarcie.": (
        "96 hours, accounting for the reset. Recorder restores the buffer after restart."
    ),
    "'brak'": "'none'",
    "EMS — pozostały czas rozładowania": "EMS — discharge time remaining",
    "EMS — pozostały czas ładowania": "EMS — charge time remaining",
    "Przełącza EMS na rozładowanie poza godzinami blokady sprzedaży i uruchamia timer.": (
        "Switches EMS to grid discharge outside the export lockout and starts the timer."
    ),
    "Przełącza EMS na ładowanie i uruchamia timer z czasu ustawionego na dashboardzie.": (
        "Switches EMS to grid charge and starts the timer using the dashboard duration."
    ),
    "Kończy oba timery i bezpiecznie przełącza falownik na autokonsumpcję.": (
        "Stops both timers and safely returns the inverter to Self-Use."
    ),
    "EMS — wymuś blokadę sprzedaży": "EMS — enforce export lockout",
    "W godzinach blokady zatrzymuje każde rozładowanie do sieci i przełącza": (
        "During the lockout, stops every grid-discharge cycle and switches"
    ),
    "falownik na Autokonsumpcję (Self-Use). Ładowanie z sieci pozostaje dozwolone.": (
        "the inverter to Self-Use. Grid charging remains allowed."
    ),
    "Co minutę porównuje bieżący półgodzinny blok RCE z progiem użytkownika.": (
        "Every minute, compares the current 30-minute RCE block with the user threshold."
    ),
    "Co minutę sprawdza dwudniowy plan optymalizatora. Rozładowuje tylko w": (
        "Every minute, checks the optimizer's two-day plan. It discharges only in"
    ),
    "wybranych najdroższych blokach, powyżej progu użytkownika i ponad SOC": (
        "selected highest-value slots, above the user threshold and above the SOC"
    ),
    "potrzebnym do zasilenia domu. Ręczne timery mają pierwszeństwo.": (
        "required to supply the home. Manual timers take priority."
    ),
    "Rozładowuje tylko powyżej progu i powyżej minimalnego SOC. Ręczne timery": (
        "Discharges only above the threshold and minimum SOC. Manual charge and"
    ),
    "ładowania i rozładowania mają pierwszeństwo.": (
        "discharge timers take priority."
    ),
    "Wznawia aktywny cykl albo wraca do Self-Use, jeśli timer upłynął podczas wyłączenia HA.": (
        "Restores an active cycle or returns to Self-Use if its timer expired while HA was offline."
    ),
    "Brak okresów powyżej progu poza blokadą sprzedaży.": (
        "No periods above the threshold outside the export lockout."
    ),
    "bloków po 30 min": "30-minute blocks",
}


@dataclass
class Entity:
    source_component: str
    source_domain: str
    source_name: str
    source_id: str
    entity_category: str | None
    options: list[str] = field(default_factory=list)


def slugify(value: str) -> str:
    """Return a stable Home Assistant translation/object-id key."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("%", " percent ")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value


def polish_name(english_name: str) -> str:
    """Create a useful first-pass Polish entity name."""
    if english_name in PHRASE_TRANSLATIONS:
        return PHRASE_TRANSLATIONS[english_name]

    tokens = re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]+", english_name)
    translated = "".join(WORD_TRANSLATIONS.get(token, token) for token in tokens)
    if translated:
        translated = translated[0].upper() + translated[1:]
    return translated


def parse_entities(path: Path) -> list[Entity]:
    """Extract top-level ESPHome entities from a package YAML file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    entities: list[Entity] = []
    current_domain = ""
    start_indexes: list[tuple[int, str]] = []

    for index, line in enumerate(lines):
        top_level = re.fullmatch(r"([a-z_]+):\s*", line)
        if top_level:
            current_domain = top_level.group(1)
            continue
        platform = re.match(r"^  - platform:\s*(.+?)\s*$", line)
        if platform and current_domain in SUPPORTED_SOURCE_DOMAINS:
            start_indexes.append((index, current_domain))

    for position, (start, source_domain) in enumerate(start_indexes):
        end = start_indexes[position + 1][0] if position + 1 < len(start_indexes) else len(lines)
        block = lines[start:end]
        name = ""
        source_id = ""
        category: str | None = None
        options: list[str] = []
        option_mode: str | None = None
        nested_entities: list[Entity] = []
        nested_name = ""
        nested_id = ""
        nested_category: str | None = None

        def flush_nested() -> None:
            nonlocal nested_name, nested_id, nested_category
            if nested_name:
                nested_entities.append(
                    Entity(
                        source_component=source_domain,
                        source_domain=(
                            "sensor" if source_domain == "text_sensor" else source_domain
                        ),
                        source_name=nested_name,
                        source_id=nested_id or slugify(nested_name),
                        entity_category=nested_category,
                    )
                )
            nested_name = ""
            nested_id = ""
            nested_category = None

        for line in block:
            name_match = re.match(r'^    name:\s*["\']?(.*?)["\']?\s*$', line)
            if name_match:
                name = name_match.group(1)
                continue
            id_match = re.match(r"^    id:\s*(.+?)\s*$", line)
            if id_match:
                source_id = id_match.group(1).strip("\"'")
                continue
            category_match = re.match(r"^    entity_category:\s*(.+?)\s*$", line)
            if category_match:
                category = category_match.group(1).strip("\"'")
                continue
            if re.match(r"^    options:\s*$", line):
                option_mode = "list"
                continue
            if re.match(r"^    optionsmap:\s*$", line):
                option_mode = "map"
                continue
            if option_mode == "list":
                item = re.match(r'^      -\s*["\'](.*?)["\']\s*$', line)
                if item:
                    options.append(item.group(1))
                elif line.strip() and not line.startswith("      "):
                    option_mode = None
            elif option_mode == "map":
                item = re.match(r'^      ["\'](.*?)["\']:\s*.+$', line)
                if item:
                    options.append(item.group(1))
                elif line.strip() and not line.startswith("      "):
                    option_mode = None
            nested_section = re.match(r"^    [a-z_]+:\s*$", line)
            if nested_section:
                flush_nested()
                continue
            nested_name_match = re.match(
                r'^      name:\s*["\']?(.*?)["\']?\s*$',
                line,
            )
            if nested_name_match:
                nested_name = nested_name_match.group(1)
                continue
            nested_id_match = re.match(r"^      id:\s*(.+?)\s*$", line)
            if nested_id_match:
                nested_id = nested_id_match.group(1).strip("\"'")
                continue
            nested_category_match = re.match(
                r"^      entity_category:\s*(.+?)\s*$",
                line,
            )
            if nested_category_match:
                nested_category = nested_category_match.group(1).strip("\"'")

        flush_nested()

        if not name and nested_entities:
            entities.extend(nested_entities)
            continue

        if name:
            entities.append(
                Entity(
                    source_component=source_domain,
                    source_domain="sensor" if source_domain == "text_sensor" else source_domain,
                    source_name=name,
                    source_id=source_id or slugify(name),
                    entity_category=category,
                    options=options,
                )
            )

    return entities


def option_definition(raw: str) -> dict[str, str]:
    """Return a canonical select option and both UI translations."""
    if raw in OPTION_TRANSLATIONS:
        key, english, polish = OPTION_TRANSLATIONS[raw]
    else:
        key, english, polish = slugify(raw), raw, raw
    return {"key": key, "raw": raw, "en": english, "pl": polish}


def static_translations(language: str) -> dict:
    """Return config-flow and service translations."""
    if language == "pl":
        return {
            "title": "Hoymiles HIT xxL G3 Modbus",
            "config": {
                "step": {
                    "user": {
                        "title": "Połącz z urządzeniem ESPHome",
                        "description": (
                            "Wybierz urządzenie ESPHome z falownikiem Hoymiles. "
                            "Integracja utworzy lokalizowane encje bez dodatkowego "
                            "odpytywania magistrali Modbus."
                        ),
                        "data": {
                            "source_device_id": "Urządzenie źródłowe ESPHome",
                            "copy_assets": "Skopiuj dashboard i automatykę EMS",
                        },
                        "data_description": {
                            "source_device_id": (
                                "Urządzenie musi używać firmware z tego projektu."
                            ),
                            "copy_assets": (
                                "Kopiuje dashboard, pakiet EMS i kartę RCE do "
                                "odpowiednich katalogów /config."
                            ),
                        },
                    }
                },
                "error": {
                    "device_not_found": "Nie znaleziono wybranego urządzenia.",
                    "no_entities": "Urządzenie nie udostępnia obsługiwanych encji.",
                },
                "abort": {
                    "already_configured": "To urządzenie jest już skonfigurowane."
                },
            },
            "services": {
                "install_assets": {
                    "name": "Zainstaluj lub zaktualizuj zasoby",
                    "description": (
                        "Kopiuje dashboard, kartę RCE i pakiet automatyki EMS "
                        "do katalogu konfiguracyjnego Home Assistanta."
                    ),
                    "fields": {
                        "overwrite": {
                            "name": "Nadpisz istniejące pliki",
                            "description": (
                                "Zastępuje wcześniej skopiowane zasoby wersją "
                                "dołączoną do integracji."
                            ),
                        }
                    },
                }
            },
        }

    return {
        "title": "Hoymiles HIT xxL G3 Modbus",
        "config": {
            "step": {
                "user": {
                    "title": "Connect an ESPHome device",
                    "description": (
                        "Select the ESPHome device connected to the Hoymiles "
                        "inverter. The integration creates localized entities "
                        "without additional Modbus polling."
                    ),
                    "data": {
                        "source_device_id": "Source ESPHome device",
                        "copy_assets": "Copy the dashboard and EMS automation",
                    },
                    "data_description": {
                        "source_device_id": (
                            "The device must run firmware provided by this project."
                        ),
                        "copy_assets": (
                            "Copies the dashboard, EMS package and RCE card to "
                            "their corresponding /config directories."
                        ),
                    },
                }
            },
            "error": {
                "device_not_found": "The selected device was not found.",
                "no_entities": "The device exposes no supported entities.",
            },
            "abort": {
                "already_configured": "This device is already configured."
            },
        },
        "services": {
            "install_assets": {
                "name": "Install or update assets",
                "description": (
                    "Copies the dashboard, RCE card and EMS automation package "
                    "to the Home Assistant configuration directory."
                ),
                "fields": {
                    "overwrite": {
                        "name": "Overwrite existing files",
                        "description": (
                            "Replace previously copied assets with the version "
                            "bundled with this integration."
                        ),
                    }
                },
            }
        },
    }


def transform_entity_ids(text: str, catalog: list[dict]) -> str:
    """Replace installation-specific ESPHome ids with stable proxy ids."""
    candidates: dict[str, list[dict]] = {
        "button": [],
        "sensor": [],
        "number": [],
        "select": [],
    }
    for record in catalog:
        candidates[record["domain"]].append(record)
    for records in candidates.values():
        records.sort(key=lambda record: len(record["source_object_id"]), reverse=True)

    pattern = re.compile(r"\b(button|sensor|number|select)\.([a-z0-9_]+)\b")

    def replace(match: re.Match[str]) -> str:
        domain, object_id = match.groups()
        if "hoymiles_inverter" not in object_id:
            return match.group(0)
        for record in candidates[domain]:
            source_object_id = record["source_object_id"]
            if object_id == source_object_id or object_id.endswith(
                f"_{source_object_id}"
            ):
                return f"{domain}.hoymiles_hit_{record['translation_key']}"
        return match.group(0)

    return pattern.sub(replace, text)


def add_dashboard_entity_names(
    text: str, catalog: list[dict], language: str
) -> str:
    """Expand entity-card shorthand with short localized dashboard-only names."""
    names: dict[str, str] = {}
    localized_names: dict[str, dict[str, str]] = {}
    for record in catalog:
        domain = record["domain"]
        source_object_id = record["source_object_id"]
        name = record["name"][language]
        aliases = (
            f"{domain}.hoymiles_hit_{record['translation_key']}",
            f"{domain}.hoymiles_inverter_{source_object_id}",
            f"{domain}.pv_hoymiles_inverter_{source_object_id}",
        )
        for alias in aliases:
            names[alias] = name
            localized_names[alias] = record["name"]

    shorthand = re.compile(
        r"^(?P<indent>\s*)-\s+"
        r"(?P<entity>(?:button|sensor|number|select)\.[a-z0-9_]+)"
        r"\s*$"
    )
    expanded: list[str] = []
    entities_indent: int | None = None
    for line in text.splitlines():
        leading = len(line) - len(line.lstrip())
        if line.strip() and entities_indent is not None and leading <= entities_indent:
            entities_indent = None
        if re.match(r"^\s*entities:\s*$", line):
            entities_indent = leading
            expanded.append(line)
            continue

        match = shorthand.match(line)
        if (
            not match
            or entities_indent is None
            or len(match.group("indent")) != entities_indent + 2
        ):
            expanded.append(line)
            continue
        entity_id = match.group("entity")
        name = names.get(entity_id)
        if not name:
            expanded.append(line)
            continue
        indent = match.group("indent")
        expanded.append(f"{indent}- entity: {entity_id}")
        expanded.append(
            f"{indent}  name: {json.dumps(name, ensure_ascii=False)}"
        )
    relocalized: list[str] = []
    entity_row = re.compile(
        r"^(?P<indent>\s*)-\s+entity:\s+"
        r"(?P<entity>(?:button|sensor|number|select)\.[a-z0-9_]+)\s*$"
    )
    name_row = re.compile(r"^(?P<indent>\s*)name:\s+(?P<name>.+?)\s*$")
    index = 0
    while index < len(expanded):
        line = expanded[index]
        match = entity_row.match(line)
        if (
            match
            and index + 1 < len(expanded)
            and match.group("entity") in localized_names
        ):
            following = name_row.match(expanded[index + 1])
            if following:
                raw_name = following.group("name")
                try:
                    current_name = json.loads(raw_name)
                except json.JSONDecodeError:
                    current_name = raw_name.strip("'\"")
                known_names = set(
                    localized_names[match.group("entity")].values()
                )
                if current_name in known_names:
                    relocalized.append(line)
                    relocalized.append(
                        f"{following.group('indent')}name: "
                        f"{json.dumps(names[match.group('entity')], ensure_ascii=False)}"
                    )
                    index += 2
                    continue
        relocalized.append(line)
        index += 1

    return "\n".join(relocalized) + ("\n" if text.endswith("\n") else "")


def translate_asset_to_english(text: str) -> str:
    """Create a first-pass English dashboard/package for the current release."""
    text = text.replace('"Autokonsumpcja (Self-Use)"', '"self_use"')
    text = text.replace('"Ładowanie z sieci"', '"grid_charge"')
    text = text.replace('"Rozładowanie do sieci"', '"grid_discharge"')
    for polish, english in sorted(
        ENGLISH_REPLACEMENTS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        text = text.replace(polish, english)
    return text


def canonicalize_proxy_select_options(text: str) -> str:
    """Use canonical proxy options in the localized Polish package as well."""
    return (
        text.replace('"Autokonsumpcja (Self-Use)"', '"self_use"')
        .replace('"Ładowanie z sieci"', '"grid_charge"')
        .replace('"Rozładowanie do sieci"', '"grid_discharge"')
    )


def build() -> None:
    """Generate catalog/translations and copy public assets."""
    entities: list[Entity] = []
    for path in sorted(PACKAGES.glob("*.yaml")):
        if path.name not in SKIPPED_FILES:
            entities.extend(parse_entities(path))

    seen: set[tuple[str, str]] = set()
    catalog: list[dict] = []
    en = static_translations("en")
    pl = static_translations("pl")
    en["entity"] = {"button": {}, "sensor": {}, "number": {}, "select": {}}
    pl["entity"] = {"button": {}, "sensor": {}, "number": {}, "select": {}}

    for entity in entities:
        if entity.source_name in SPECIAL_NAMES:
            key, english, polish = SPECIAL_NAMES[entity.source_name]
        else:
            key = slugify(entity.source_name)
            english = entity.source_name
            polish = polish_name(entity.source_name)

        identity = (entity.source_domain, key)
        if identity in seen:
            key = f"{key}_{slugify(entity.source_id)}"
            identity = (entity.source_domain, key)
        seen.add(identity)

        options = [option_definition(raw) for raw in entity.options]
        record = {
            "domain": entity.source_domain,
            "source_component": entity.source_component,
            "translation_key": key,
            "source_name": entity.source_name,
            "source_id": entity.source_id,
            "source_object_id": slugify(entity.source_name),
            "entity_category": entity.entity_category,
            "name": {"en": english, "pl": polish},
            "description": {
                "en": (
                    f"Hoymiles HIT xxL G3 Modbus value: {english}."
                    if entity.source_domain == "sensor"
                    else (
                        f"One-shot Hoymiles HIT xxL G3 command: {english}."
                        if entity.source_domain == "button"
                        else f"Writable Hoymiles HIT xxL G3 setting: {english}."
                    )
                ),
                "pl": (
                    f"Wartość Modbus falownika Hoymiles HIT xxL G3: {polish}."
                    if entity.source_domain == "sensor"
                    else (
                        f"Jednorazowe polecenie falownika Hoymiles HIT xxL G3: {polish}."
                        if entity.source_domain == "button"
                        else f"Zapisywalne ustawienie falownika Hoymiles HIT xxL G3: {polish}."
                    )
                ),
            },
            "options": options,
        }
        catalog.append(record)

        en_entity = {"name": english}
        pl_entity = {"name": polish}
        if options:
            en_entity["state"] = {option["key"]: option["en"] for option in options}
            pl_entity["state"] = {option["key"]: option["pl"] for option in options}
        elif entity.source_component == "text_sensor":
            en_entity["state"] = {
                key: value[0] for key, value in TEXT_STATE_TRANSLATIONS.items()
            }
            pl_entity["state"] = {
                key: value[1] for key, value in TEXT_STATE_TRANSLATIONS.items()
            }
        en["entity"][entity.source_domain][key] = en_entity
        pl["entity"][entity.source_domain][key] = pl_entity

    COMPONENT.mkdir(parents=True, exist_ok=True)
    TRANSLATIONS.mkdir(parents=True, exist_ok=True)
    (COMPONENT / "entity_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TRANSLATIONS / "en.json").write_text(
        json.dumps(en, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (TRANSLATIONS / "pl.json").write_text(
        json.dumps(pl, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    dashboard = transform_entity_ids(
        (ROOT / "dashboard_hoymiles.yaml").read_text(encoding="utf-8"),
        catalog,
    )
    package = transform_entity_ids(
        (ROOT / "home_assistant" / "hoymiles_ems_scheduler.yaml").read_text(
            encoding="utf-8"
        ),
        catalog,
    )

    localized_assets = {
        RESOURCES / "dashboard_hoymiles_pl.yaml": add_dashboard_entity_names(
            dashboard, catalog, "pl"
        ),
        RESOURCES / "dashboard_hoymiles_en.yaml": translate_asset_to_english(
            add_dashboard_entity_names(dashboard, catalog, "en")
        ),
        RESOURCES
        / "home_assistant"
        / "pl"
        / "hoymiles_ems_scheduler.yaml": canonicalize_proxy_select_options(package),
        RESOURCES
        / "home_assistant"
        / "en"
        / "hoymiles_ems_scheduler.yaml": translate_asset_to_english(package),
    }
    for destination, content in localized_assets.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    for language in ("pl", "en"):
        dashboard_yaml = (
            RESOURCES / f"dashboard_hoymiles_{language}.yaml"
        ).read_text(encoding="utf-8")
        dashboard_data = yaml.safe_load(dashboard_yaml)
        if (
            not isinstance(dashboard_data, dict)
            or not isinstance(dashboard_data.get("views"), list)
        ):
            raise ValueError(
                f"Generated {language} dashboard has no top-level views list"
            )
        dashboard_json = (
            RESOURCES / "www" / f"dashboard_hoymiles_{language}.json"
        )
        dashboard_json.parent.mkdir(parents=True, exist_ok=True)
        dashboard_json.write_text(
            json.dumps(dashboard_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    for bundled_www_asset in (
        "hoymiles-rce-chart-card.js",
        "hoymiles-inverter.png",
    ):
        card_source = ROOT / "home_assistant" / "www" / bundled_www_asset
        card_destination = RESOURCES / "www" / bundled_www_asset
        card_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(card_source, card_destination)

    print(f"Generated {len(catalog)} localized entities.")


if __name__ == "__main__":
    build()
