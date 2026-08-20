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

1. Open **Settings → Devices & services → EMS for Hoymiles HIT-(5–20)L-G3**.
2. Open the integration entry menu and choose **Download diagnostics**.
3. Attach the downloaded JSON file to the issue together with the exact local
   time of the fault and a short description of the expected behaviour.

The report contains integration/firmware versions, entity coverage, current
Hoymiles states, calculation attributes and 24 hours of significant control
changes. Fast telemetry history is deliberately omitted so the report remains
small and does not overload Recorder.

Every Home Assistant installation receives one random UUID v4 named
`anonymous_installation_id`. It is generated without using device, network,
account or config-entry data and is stored by Home Assistant in
`/config/.storage/hoymiles_hit_modbus.installation_identity`. It survives
restarts and integration updates, is shared by every Hoymiles config entry in
the same Home Assistant, and is deliberately retained in support reports only
to correlate archives from that installation over time. Its current
`installation_id_schema_version` is `1`; it is not exposed as an entity.

For offline comparison of many dashboard ZIPs, use the privacy-aware batch
analyzer described in [DIAGNOSTICS_ANALYZER.md](DIAGNOSTICS_ANALYZER.md). It
groups successive archives by the anonymous installation ID, evaluates RCE,
RCEm and tariff charging with versioned rules, and produces JSON, CSV,
Markdown and HTML reports without extracting the input archives.

### Planner readiness

Execution authority comes from the optimizer entity itself: `result_current`,
plan report age and source-freshness attributes. A recently updated derived
proxy does not make an old plan executable. For RCEm, battery capacity is a
stable property and its report age is diagnostic; live BMS voltage and current
limits remain fail-closed freshness inputs. The control-owner sensor state
reports active actuator ownership, while `owner_code=manual` remains the stable
machine-readable compatibility fallback when no automatic writer is active;
the `*_policy_enabled` attributes show configuration only. Optional Solcast Day
3 exposes its selected entity, signed age, freshness and fallback reason in the
planner attributes.

RCE and tariff-plan attributes also expose `forecast_learning_enabled`,
`forecast_learning_mode`, `forecast_learning_excluded_reason` and the effective
`forecast_factor_used`. On a physically verified exact 0% GCF export limit the
contract is `false / fixed_zero_export / zero_export / 0.80`. Missing or stale
GCF evidence has its own conservative reason and must not be interpreted as a
confirmed zero-export site.

### Master changes mode but a Slave does not

First inspect the physical external Modbus/RS485 bus. The ESP32 converter,
Master and every Slave must share the same A/B/reference bus; address `0` is a
wire-level broadcast and the Master does not relay it over the inverter's
internal parallel network. Check polarity, continuity and end termination
against the manufacturer manual. A fresh topology, system-wide telemetry and
a matching Master FC03 do not verify the Slave branch. During a conservative
Grid Discharge and return-to-Self-Use test, record each inverter separately in
the manufacturer application.

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

1. Otwórz **Ustawienia → Urządzenia oraz usługi → EMS for Hoymiles HIT-(5–20)L-G3**.
2. Otwórz menu wpisu integracji i wybierz **Pobierz diagnostykę**.
3. Dołącz pobrany plik JSON do zgłoszenia razem z dokładną lokalną godziną
   wystąpienia błędu i krótkim opisem oczekiwanego działania.

Raport zawiera wersje integracji i firmware, kompletność encji, bieżące stany
Hoymiles, parametry obliczeń oraz 24 godziny istotnych zmian sterowania. Historia
szybkiej telemetrii jest celowo pomijana, aby raport pozostał mały i nie
obciążał bazy Recorder.

Każda instalacja Home Assistanta otrzymuje jeden losowy UUID v4 o nazwie
`anonymous_installation_id`. Powstaje on bez użycia danych urządzenia, sieci,
konta ani config entry i jest przechowywany przez Home Assistanta w
`/config/.storage/hoymiles_hit_modbus.installation_identity`. Przetrwa restart
i aktualizację integracji, jest wspólny dla wszystkich wpisów Hoymiles w tym
samym HA i celowo pozostaje w raportach wsparcia wyłącznie do łączenia kolejnych
paczek z tej instalacji. Bieżąca wartość `installation_id_schema_version` to
`1`; identyfikator nie jest wystawiany jako encja.

Do porównywania wielu ZIP-ów z dashboardu służy prywatnościowy analizator
zbiorczy opisany w [DIAGNOSTICS_ANALYZER.md](DIAGNOSTICS_ANALYZER.md). Łączy on
kolejne paczki według anonimowego ID instalacji, ocenia RCE, RCEm i ładowanie
taryfowe za pomocą wersjonowanych reguł oraz tworzy raporty JSON, CSV, Markdown
i HTML bez rozpakowywania archiwów wejściowych.

### Gotowość planera

Uprawnienie do wykonania pochodzi z samej encji optymalizatora:
`result_current`, wieku raportu planu i atrybutów świeżości źródeł. Niedawno
odświeżona encja pośrednia nie nadaje staremu planowi prawa wykonania. W RCEm
pojemność baterii jest stabilną cechą, a wiek jej raportu ma znaczenie
diagnostyczne; bieżące napięcie i limity prądowe BMS nadal działają fail-closed.
Stan sensora właściciela sterowania pokazuje aktywną własność aktuatora,
natomiast `owner_code=manual` pozostaje stabilnym fallbackiem maszynowym dla
zgodności, gdy żaden automatyczny sterownik nie jest aktywny; atrybuty
`*_policy_enabled` opisują wyłącznie konfigurację. Opcjonalny Dzień 3 Solcast
publikuje wybraną encję, wiek ze znakiem, świeżość i przyczynę fallbacku.

Atrybuty planów RCE i taryfy publikują też `forecast_learning_enabled`,
`forecast_learning_mode`, `forecast_learning_excluded_reason` oraz rzeczywiście
użyty `forecast_factor_used`. Dla fizycznie potwierdzonego, dokładnego limitu
GCF 0% kontrakt ma wartości `false / fixed_zero_export / zero_export / 0.80`.
Brak albo nieświeżość GCF ma osobną konserwatywną przyczynę i nie może być
interpretowana jako potwierdzony zero-export.

### Master zmienia tryb, a Slave nie

Najpierw sprawdź fizyczną zewnętrzną magistralę Modbus/RS485. Konwerter ESP32,
Master i każdy Slave muszą korzystać z tej samej pary A/B i odniesienia;
adres `0` jest rozgłoszeniem na przewodzie, a Master nie przekazuje go przez
wewnętrzną sieć równoległą falowników. Sprawdź polaryzację, ciągłość oraz
terminację końców według instrukcji producenta. Świeża topologia, telemetria
sumaryczna i zgodny FC03 Mastera nie weryfikują odgałęzienia do Slave'a. Podczas
ostrożnego testu Grid Discharge i powrotu do Self-Use zapisz stan każdego
falownika osobno w aplikacji producenta.

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
