# Codzienny harmonogram EMS w Home Assistant

## Wesprzyj rozwój projektu

Projekt jest rozwijany niezależnie i testowany na rzeczywistych instalacjach.
Każde wsparcie pomaga rozwijać bezpieczną automatykę EMS/RCE, sprawdzać kolejne
konfiguracje falowników oraz utrzymywać aktualną dokumentację. Jeśli rozwiązanie
oszczędziło Ci czas lub pomaga lepiej wykorzystać energię:

[☕ Postaw kawę autorowi i wesprzyj dalszy rozwój](https://buycoffee.to/kaluzaaa)

Plik `hoymiles_ems_scheduler.yaml` jest pakietem Home Assistant, a nie pakietem
ESPHome. Zapewnia dwa niezależne harmonogramy wykonywane codziennie:

- rozładowanie do sieci,
- ładowanie z sieci.

Pakiet zawiera również automatykę rozładowania według oficjalnej ceny RCE PSE.
Pobiera bieżący dzień z `https://api.raporty.pse.pl/api/rce-pln`, łączy 96
okresów 15-minutowych w 48 bloków po 30 minut i przelicza PLN/MWh na PLN/kWh.
Użytkownik ustawia próg ceny i maksymalną moc rozładowania. Minimalny SOC może
być ustawiany ręcznie albo obliczany automatycznie z prognozy Solcast na jutro,
średniego zużycia LOAD z ostatnich 96 godzin, pojemności magazynu odczytanej
z falownika, awaryjnej rezerwy Self-Use oraz korekty bezpieczeństwa.
Powyżej progu falownik przechodzi na `Rozładowanie do sieci`, a po spadku ceny
lub osiągnięciu obowiązującego minimalnego SOC wraca do
`Autokonsumpcja (Self-Use)`.

## Wymagana integracja Solcast

Dynamiczna rezerwa SOC wymaga zewnętrznej integracji
[Solcast PV Forecast producenta BJReplay](https://github.com/BJReplay/ha-solcast-solar).
Po jej zainstalowaniu i skonfigurowaniu pakiet automatycznie szuka encji
`sensor.solcast_pv_forecast_forecast_tomorrow` (Home Assistant po angielsku)
albo `sensor.solcast_pv_forecast_prognoza_na_jutro` (Home Assistant po polsku).
Jeśli encja została zmieniona lub ma inną nazwę, należy wpisać jej identyfikator
na dashboardzie.

Obliczenie ma postać:

```text
dynamiczny SOC =
  rezerwa awaryjna Self-Use
  + max(
      pozostałe zużycie domu w chronionym oknie nocnym,
      max(średni LOAD z 4 dni - prognoza PV jutro, 0)
    ) / pojemność baterii
  + korekta bezpieczeństwa użytkownika
```

Chronione okno nocne trwa od 90 minut przed zachodem do 90 minut po wschodzie
słońca. Pozostałe zużycie jest zmniejszane wraz z upływem tego okna, dlatego
wieczorna sprzedaż może trwać tylko do poziomu zapewniającego zasilanie domu
do rozpoczęcia produkcji PV. Przez pierwszą dobę po instalacji zużycie w oknie
jest szacowane proporcjonalnie z czterodniowej średniej dobowej. Następnie
pakiet korzysta z rzeczywistego średniego zużycia z maksymalnie czterech
ostatnich chronionych okien.

Korekta jest dodawana w punktach procentowych. Przykład dla magazynu 20 kWh,
rezerwy awaryjnej 20%, pozostałego zużycia nocnego 6 kWh, deficytu dobowego
2 kWh i korekty 5%: `20% + 30% + 5% = 55%`.

Brak prognozy Solcast, minimum 24 godzin historii LOAD, danych `sun.sun` albo
poprawnej pojemności magazynu blokuje automatyczną sprzedaż RCE. Po wyłączeniu
dynamicznej rezerwy automatyka wraca do ręcznego progu rozładowania.

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
4. Dodaj zasób JavaScript:
   `/api/hoymiles_hit_modbus/static/hoymiles-rce-chart-card.js` jako moduł,
   a następnie utwórz panel społecznościowy **Hoymiles HIT xxL G3**.
   Panel zapisuje tylko strategię
   `custom:hoymiles-hit-xxl-g3`, dlatego po aktualizacji HACS automatycznie
   ładuje najnowszy dashboard PL albo EN z integracji.
5. Przy pierwszym uruchomieniu ustaw na dashboardzie godzinę i czas trwania,
   np. `20:00` oraz `90 min`, a następnie włącz harmonogram. Home Assistant
   będzie później odtwarzał ustawione wartości po restarcie.
6. Dla automatyki RCE ustaw najpierw cenę w PLN/kWh i moc. Jeżeli korzystasz
   z dynamicznej rezerwy, zainstaluj Solcast BJReplay, sprawdź encję prognozy
   na jutro, ustaw awaryjną rezerwę Self-Use i opcjonalną korektę SOC.
7. Włącz `EMS RCE — dynamiczna rezerwa SOC`, sprawdź stan wyliczenia, a dopiero
   potem włącz `EMS RCE — automatyczne rozładowanie włączone`. Ręczne timery
   mają pierwszeństwo przed automatyką RCE.

Wyłączenie przełącznika harmonogramu zatrzymuje jego kolejne codzienne
uruchomienia. Do natychmiastowego zakończenia aktualnego cyklu służy encja
`script.hoymiles_stop_scheduled_cycle` — kończy timery i wraca do Self-Use.

Pakiet oczekuje stabilnej encji integracji
`select.hoymiles_hit_ems_mode` z opcjami kanonicznymi:

- `self_use`,
- `grid_charge`,
- `grid_discharge`.

Starsze instalacje mogą nadal importować plik `dashboard_hoymiles.yaml`.
Instalator automatycznie aktualizuje go tylko wtedy, gdy plik nie został
zmodyfikowany przez użytkownika. Własne modyfikacje są zachowywane.
