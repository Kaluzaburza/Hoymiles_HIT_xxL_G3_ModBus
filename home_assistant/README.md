# Codzienny harmonogram EMS w Home Assistant

Plik `hoymiles_ems_scheduler.yaml` jest pakietem Home Assistant, a nie pakietem
ESPHome. Zapewnia dwa niezależne harmonogramy wykonywane codziennie:

- rozładowanie do sieci,
- ładowanie z sieci.

Pakiet zawiera również automatykę rozładowania według oficjalnej ceny RCE PSE.
Pobiera bieżący dzień z `https://api.raporty.pse.pl/api/rce-pln`, łączy 96
okresów 15-minutowych w 48 bloków po 30 minut i przelicza PLN/MWh na PLN/kWh.
Użytkownik ustawia próg ceny, maksymalną moc rozładowania oraz minimalny SOC.
Powyżej progu falownik przechodzi na `Rozładowanie do sieci`, a po spadku ceny
lub osiągnięciu minimalnego SOC wraca do `Autokonsumpcja (Self-Use)`.

Każdy harmonogram ma przełącznik włączenia, godzinę startu i czas trwania
w minutach. Po zakończeniu timer przełącza EMS na `Autokonsumpcja (Self-Use)`.
Uruchomienie jednego cyklu automatycznie anuluje drugi, więc ładowanie i
rozładowanie nie będą działały jednocześnie.

## Instalacja

1. Skopiuj `hoymiles_ems_scheduler.yaml` do katalogu
   `/config/packages/hoymiles_ems_scheduler.yaml` w Home Assistant.
2. Jeżeli pakiety nie są jeszcze włączone, dodaj w `/config/configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   Jeżeli sekcja `homeassistant:` już istnieje, dopisz do niej tylko wiersz
   `packages:` — nie twórz drugiej sekcji o tej samej nazwie.
3. W Home Assistant wybierz **Narzędzia deweloperskie → YAML → Sprawdź
   konfigurację**, a następnie uruchom Home Assistant ponownie.
4. Wklej zaktualizowany `dashboard_hoymiles.yaml` do surowego edytora
   dashboardu.
5. Przy pierwszym uruchomieniu ustaw na dashboardzie godzinę i czas trwania,
   np. `20:00` oraz `90 min`, a następnie włącz harmonogram. Home Assistant
   będzie później odtwarzał ustawione wartości po restarcie.
6. Dla automatyki RCE ustaw najpierw cenę w PLN/kWh, moc i minimalny SOC.
   Dopiero potem włącz przełącznik `EMS RCE — automatyczne rozładowanie
   włączone`. Ręczne timery mają pierwszeństwo przed automatyką RCE.

Wyłączenie przełącznika harmonogramu zatrzymuje jego kolejne codzienne
uruchomienia. Do natychmiastowego zakończenia aktualnego cyklu służy encja
`script.hoymiles_stop_scheduled_cycle` — kończy timery i wraca do Self-Use.

Pakiet oczekuje istniejącej encji
`select.hoymiles_inverter_tryb_ems` z opcjami dokładnie:

- `Autokonsumpcja (Self-Use)`,
- `Ładowanie z sieci`,
- `Rozładowanie do sieci`.
