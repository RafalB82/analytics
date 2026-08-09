# mcp_fetchers — odczyt danych z MCP i konwersja do silnika analytics

Ten pakiet zawiera **deterministyczne konwersje** między tym, co zwracają
MCP (Apple/Hevy), a tym, czego oczekuje rdzeń analityczny `analytics`.
To brakujące ogniwo: silnik `analytics` jest celowo offline i nie woła MCP —
to **agent** musi mu dostarczyć dane w dokładnie oczekiwanym formacie.
Ten pakiet formalizuje tę konwersję zamiast robić ją "w locie" w rozmowie.

---

## 1. Filozofia działania

### Zasada nadrzędna — rozdzielenie pobierania od przetwarzania

```
  AGENT (ręcznie / cron)
      │  woła MCP: apple__get_daily_activity_range, apple__get_data,
      │  apple__list_recent_workouts, hevy__get-workouts, hevy__get-workout
      ▼
  surowy JSON z MCP  ──►  mcp_fetchers/*_normalize.py  (czysty, offline, stdin→stdout)
      │                        │
      │                        ▼
      │                  tmp/*.json   (znormalizowane pliki pośrednie)
      ▼                        │
  build_input.py ─────────────┘  składa payload i odpala:
      ▼
  analytics.run_analysis  ──►  wynik JSON (strefa gotowości, ACWR, TDEE, trendy)
```

Kluczowe filary:

1. **Pobieranie i konwersja są rozdzielone.**
   - Pobieranie = agent przez MCP (stanowe, wymaga narzędzi, sieci, sesji).
   - Konwersja = `*_normalize.py` (czysta funkcja, zero MCP, offline, testowalna).
   Dzięki temu normalizację można testować, versionować i powtarzać bez
   martwienia się o stan MCP/Camoufox/limitów.

2. **Każda warstwa ma jedną odpowiedzialność.**
   - `hevy_normalize.py` — tylko Hevy: surowy workout MCP → format tonażu ACWR.
   - `apple_normalize.py` — tylko Apple: daily/temp/workouts → format fizjologii.
   - `build_input.py` — tylko składanie payloadu z gotowych plików + uruchomienie.
   Zero podwójnej odpowiedzialności, zero magii rozproszonej po kodzie.

3. **Determinizm i idempotencja.**
   Te same dane wejściowe dają zawsze to samo wyjście. Skrypty nie mają stanu,
   nie zapisują historii, nie dzwonią donikąd. Można uruchamiać wielokrotnie
   bez skutków ubocznych.

4. **Stdin → stdout (filtry uniksowe).**
   Każde `*_normalize.py` czyta JSON ze standardowego wejścia i wypisuje JSON
   na standardowe wyjście. To czyni je linkowalnymi, testowalnymi przez pipe'y
   i niezależnymi od siebie:
   ```bash
   cat raw_hevy.json | python3 -m mcp_fetchers.hevy_normalize
   ```

5. **Tymczasowe pliki jako bufory między warstwami (`tmp/`).**
   `tmp/hevy_workouts.json` i `tmp/apple_input.json` to punkt kontrolny między
   "pobraniem" a "analizą". Można je odtworzyć, podglądać i debugować bez
   ponownego wołania MCP.

### Dlaczego tak, a nie np. agent wołający analytics bezpośrednio

Rdzeń `analytics` jest celowo zaprojektowany jako „LLM nigdy nie liczy" —
agent dostaje gotowy JSON i tylko go interpretuje. Gdyby konwersja formatu
siedziała w głowie agenta przy każdym uruchomieniu, byłaby:
- **niedeterministyczna** (każdy przebieg może inaczej zmapować pola),
- **nietestowalna** (nie da się jej sprawdzić jednostkowo),
- **drogą w tokenach** (każda analiza na nowo "odkrywałaby" format).

`mcp_fetchers` przenosi tę konwersję do kodu: jeden raz, przetestowany,
deterministyczny. Agent wtedy tylko: pobiera surowe dane → podaje je do
skryptu → interpretuje wynik.

---

## 2. Problem, który ten pakiet rozwiązuje — niezgodność formatów MCP vs analytics

Najważniejsza rzecz, która wymusiła ten pakiet — **dwa różne słowniki pól**
między tym, co zwracają MCP, a tym, co czyta rdzeń analityczny.

### 2.1 Hevy: `weight_kg` / `start_time` vs `weight` / `startTime`

**DECYZJA (2026-08-09, „Czy warmupy mają znaczenie?”):** warmupy są
celowo WYŁĄCZONE z loadu ACWR (regeneracja), ale ich objętość mechaniczna
NIE ginie — jest raportowana osobno jako `_volume.tonnage_total`
(z warmupami) vs `tonnage_working` (bez). To metryka obciążenia tkanek
obok ACWR, nie mieszana w load gotowości. Szczegóły rozumowania w §3.

| Pole | Surowy Hevy MCP (`hevy__get-workout`) | Oczekuje `fetch_hevy.py` |
|------|---------------------------------------|--------------------------|
| Timestamp workoutu | `start_time` np. `2026-08-05T17:12:48+00:00` | `startTime` |
| Ciężar serii | `weight_kg` | `weight` |
| Serie rozgrzewkowe | `type: "warmup"` | pomijane (SKIP_SET_TYPES) |
| Serie dystansowe (Farmers Walk) | `weight_kg` + `distance_meters`, **brak `reps`** | musi być pominięte (brak reps) |

Konsekwencje gdyby tego nie mapować:
- `fetch_hevy._set_load` czyta `s.get("weight")` → zawsze `None` dla `weight_kg`
  → **cały tonaż = 0 → ACWR nieaktywny** (cichy błąd, bez ostrzeżenia).
- `_parse_date(workout.get("startTime"))` → `None` → trening przypięty do
  `date.today()` → **przemieszczenie obciążenia w czasie**, fałszuje ACWR.

`hevy_normalize.py` rozwiązuje to: przepisuje `weight_kg→weight`,
`start_time→startTime` (normalizacja do `...Z`), usuwa `warmup` i serie
bez `reps` (dystansowe).

### 2.2 Apple: wszystko w jednym źródle vs analityka potrzebująca podzbiorów

| Dane | Zrób | Konieczna selekcja/normalizacja |
|------|------|----------------------------------|
| `apple__get_daily_activity_range` | `apple_daily` | bierzemy tylko date/RHR/HRV/sleep/energia/waga — reszta (kroki, dystans, AZM, effort) zbędna |
| `apple__get_data(name='apple_sleeping_wrist_temperature')` | `apple_temp` | osobne źródło, pole `value`; **nie ma go w daily** |
| `apple__list_recent_workouts` | `apple_workouts` | z ~8 workoutów zostają **tylko cardio**; siłowe (Traditional Strength Training) odrzucane |

`apple_normalize.py` robi: selekcję pól dziennych, przepuszczenie temperatury,
**filtrowanie cardio** (czarna lista siłowa ma pierwszeństwo, by "Walking
Lunges" nie wpadło jako cardio tylko po słowie "walk").

### 2.3 Dlaczego nie naprawić `fetch_hevy.py`/`fetch_apple.py` zamiast tworzyć nowe

Refaktor rdzenia analytics (żeby czytał `weight_kg`/`start_time`) byłby
poprawny, ale:
- naruszałby czystość rdzenia (który jest celowo generyczny względem formatu),
- require'owałby zmian testów + CI pakietu `files4.zip`,
- mieszałby **dane** (co zwraca MCP) z **logiką** (jak liczyć ACWR).

Konwersja przy brzegu (adapter) jest czystsza: rdzeń zostaje nietknięty,
a adapter absorbuje specyfikę MCP. Sugestia: ewentualnie przenieść te mapy
pól DO rdzenia jako oficjalne adaptery (`fetch_hevy` mógłby mieć `from_mcp()`),
ale to temat na osobną decyzję — obecne podejście adapterowe jest bezpieczne.

---

## 3. Problemy zauważone przy odczycie (empirycznie, 2026-08-09)

### 3.1 Brak paginacji/pelnych danych cardio → ACWR cardio niewiarygodny
- `apple__list_recent_workouts(limit=20)` zwróciło w tej sesji treningi
  **tylko z ostatnich ~7 dni** (4.08–8.08), mimo że w bazie są starsze.
- Silnik liczy cardio ACWR w oknie 28d (chronic). Przy chronic opartym na
  4–5 sesjach z 7 dni ratio wyszło **3.32 (wysokie ryzyko)**, ale to
  **artefakt zaniżonego chronic**, nie realne przeciążenie.
- Confidence ACWR = 69 (Medium), completeness 0.43 — układ sam to sygnalizuje.
- **Skutek:** cardio ACWR obecnie NIE jest godne zaufania. Siłowe OK.

### 3.2 Zmienne pokrycie RPE w Hevy → ACWR siłowy liczony częściowo na tonażu
- Starsze treningi (przed ~06.07) nie mają `rpe` w seriach — tylko tonaż.
- `compute_session_load(set,reps,kg,rpe=None)` wtedy zwraca sam tonaż
  (bez mnożenia przez RPE), więc starsze treningi mają **zaniżony load**
  względem nowszych (z RPE).
- `rpe_coverage = 66.9%` — zgodnie z notatką w MEMORY, przy <70% ACWR
  siłowy traktuj ze sceptycyzmem. Ratio 0.91 i tak w optymalnej strefie,
  ale chronic może być lekko zaniżony.

### 3.3 Energia Apple w kJ, pole nazwane kcal (znany bug, potwierdzony)
- `active_energy`/`basal_energy_burned` z `apple__get_daily_activity_range`
  są w **kJ**, choć nazwane jak kalorie (agregacja 1:1 z surowego exportu SMB).
- Skrypt **nie konwertuje** kJ→kcal sam, bo `nutrition_adaptive` robi to
  wewnętrznie (TDEE wyszedł 2156 kcal poprawnie: basal 1425+active 731 kcal
  po podziale przez 4.184 — sprawdzone).
- **Uwaga:** przy własnych obliczeniach zawsze dziel kJ przez 4.184.

### 3.4 Brak RHR dla ostatnich dni
- `resting_heart_rate` jest `null` dla 8.08 i 9.08 (RHR dochodzi z opóźnieniem
  po nocnym pomiarze). RHR trend (rosnący, R²=0.97) policzony na 29 punktach
  — pominięcie 2 dni nie zmienia kierunku.

### 3.5 Sen 9.08 to wciąż bieżący (niepełny) dzień
- `sleep.total_hours: 5.48` dla 9.08 to noc zakończona dziś rano — OK dla
  gotowości, ale pamiętaj, że `active_energy`/`basal` dla 9.08 są niepełne
  (dzień trwa), więc TDEE 7d uwzględnia pełny dzień 9.08 jako mały — to
  lekko zaniża średnią aktywności.

### 3.6 Waga w MFP pusta → TDEE nie korzysta z punktów kontrolnych
- `mfp_weight` = None (brak danych) — TDEE liczony tylko z aktywności Apple.
- Waga 71.05 / BF 15.1% z 7.08 dostępna jest w `apple_daily`, ale moduł
  MDEE jej nie używa do korekty — tylko jako kontekst (białko).

### 3.7 Limit ćwiczeń bez mapowania NAZWA → nic, ale...
- Hevy zwraca `exercise_template_id`; my używamy tylko `title` (do niczego
  analitycznego nie jest potrzebny poza kontekstem). Brak wpływu na wynik.

### 3.8 Czy objętość z rozgrzewki ma znaczenie? (decyzja architektoniczna)

**Pytanie:** `hevy_normalize` pomija serie `warmup` — czy to nie gubi realnej
objętości treningu?

**Rozstrzygnięcie — dwie metryki, dwa cele:**

| Metryka | Liczy | Warmupy wchodzą? | Cel |
|---------|-------|------------------|-----|
| ACWR load (`fetch_hevy._set_load`) | tonaż × RPE | **Nie** | kumulacja zmęczenia → decyzja o treningu |
| `_volume.tonnage_total` (nowe) | kg × reps | **Tak** | obciążenie mechaniczne tkanek / ryzyko |

- **W warmupach (bez RPE) pominięcie w ACWR JEST poprawne:** rozgrzewka nie
tworzy istotnego zmęczenia mięśniowego/anautonomicznego (wysokie RIR, niskie
%1RM), a ich doliczenie do loadu bez RPE zepsułoby spójność skali
(mieszanka „sam tonaż” i „tonaż × RPE”)
- **Ale objętość mechaniczna nie jest bez znaczenia:** w przysiadzie warmupy
potrafią dawać >50% dziennego tonażu (np. 20×10+40×10+60×5+75×3 vs serie
robocze). Dla ryzyka kontuzji / faktycznego obciążenia stawów te kg są realne.
- **Rozwiązanie wdrożone (opcja 2):** `hevy_normalize` raportuje `_volume`
= {tonnage_total (z warmupami), tonnage_working (bez)} per workout;
`build_input` agreguje do sekcji `hevy_volume` w wyniku. ACWR zostaje czysty,
a objętość mechaniczna nie ginie. UWAGA: `_volume` pojawia się TYLKO gdy
dane przejdą przez obecną normalizację (nowe pliki tmp); stare pliki bez
`_volume` dadzą pustą sekcję.

---

## 4. Bieżące braki (gaps)

1. **Brak pełnego pobrania cardio (strony/wiek).** `apple__list_recent_workouts`
   nie zwróciło starszych sesji rowerowych — bez nich chronic cardio jest
   zaniżone. Brakuje albo paginacji w narzędziu, albo ręcznego dozbierania
   starszych workoutów przez inne narzędzie (np. `list_data` + zakres dat).

2. **Brak normalizacji starego formatu Hevy (czerwiec/lipiec).** Niektóre
   workouty (np. 13.07, 9.07, 6.07) nie miały RPE. Skrypt przepuszcza je
   (rpe=None), ale chronic pozostaje niżej niż mógłby. Wymaga dozbierania RPE
   albo akceptacji ograniczenia.

3. **Brak automatycznego łączenia stron Hevy.** `build_input` czyta jeden
   plik `tmp/hevy_workouts.json` — agent musi wcześniej skleić strony
   `hevy__get-workouts` w jedną listę. Przy 12+ workoutach to łatwo pominąć
   starsze. Sugestia: skrypcik `fetch_hevy_pages` (patrz §5).

4. **Brak walidacji nazw/duplikatów workoutów.** Apple i Hevy mogą mieć
   zdublowane sesje (np. ten sam trening zalogowany w obu). Filtrowanie cardio
   po nazwie + osobne źródła siły (tylko Hevy) ograniczają duble, ale nie ma
   jawnej dedupe per (data, nazwa).

5. **bodyweight_kg w params nie jawny.** Budujemy z `--weight`, ale realnie
   weight jest w `apple_daily` (7.08). `nutrition` wziął to z daily poprawnie
   (weight present). Brak kosmetyczny.

---

## 5. Sugestie na przyszłość

1. **Dopracować pobieranie cardio — dokończyć chronic.**
   Dodać `fetch_apple_cardio.py`, który:
   - woła `apple__list_recent_workouts` z szerszym limitem / po zakresach dat,
   - przywołuje `apple__get_data(name='...')` lub stare exporty SMB dla sesji
     sprzed okna, żeby wypełnić chronic 28d.
   Wtedy cardio ACWR przestanie fałszować (3.32 → realne ~1-1.5).

2. **Połączyć strony Hevy automatycznie.**
   `fetch_hevy_pages(max_pages, days_back)` (analog: `fetch_hevy.fetch_workouts_impl`,
   który już istnieje w rdzeniu!) — niech agent woła go zamiast ręcznie sklejać.
   Rdzeń ma `fetch_workouts_impl(get_workouts_callable, pages, days_back)` —
   można go wystawić jako narzędzie.

3. **Wpisać normalizację do rdzenia (optional).**
   Ewentualnie dodać w `fetch_hevy.py`/`fetch_apple.py` oficjalne `*_from_mcp()`
   adaptery, żeby rdzeń umiał przyjąć format MCP wprost. Zmniejszyłoby liczbę
   plików, ale wprowadziłoby zależność od specyfiki MCP w rdzeniu — decyzja
   architektoniczna; obecnie adaptery na brzegu są bezpieczniejsze.

4. **Walidacja sanity-check na wyjściu.**
   `build_input` mógłby ostrzegać, gdy: `rpe_coverage < 70%`, `n_cardio_days`
   z chronic < połowy okna, `HRV points < 6` — zamiast czekać aż to
   objawi się w `confidence`. Dziś to robi confidence, ale czytelny warning
   na starcie ułatwia interpretację LLM.

5. **Ustawić cron porannej analizy.**
   Skrypt jest w pełni deterministyczny → nadaje się do zaplanowanego
   uruchomienia (np. 09:00, jak job „Readiness check"). Agent pobiera surowe
   dane przez MCP, przekazuje do `mcp_fetchers`, interpretuje wynik i raportuje.
   To usunie ręczne budowanie danych przy każdej analizie.

---

## 6. Pliki

### 6.0 Mapa odpowiedzialności warstw (pobieranie vs analiza)

**Zasada nadrzędna: `mcp_fetchers/` NIE liczy — tylko pobiera z MCP i przepisuje
format. Cała logika obliczeniowa (tonaż, TRIMP/ACWR, TDEE, bilans kaloryczny)
siedzi w rdzeniu `analytics/analytics/`, który jest celowo offline (README §1).**

| Dane | Pobieranie (mcp_fetchers / agent) | Konwersja formatu (czysta) | Analiza / liczenie (rdzeń analytics) |
|---|---|---|---|
| Workouty siłowe | `fetch_mcp.py` → Hevy stdio | `hevy_normalize.py` (weight_kg→weight, start_time→startTime, kasuje warmupy) | tonaż/objętość `_volume` → `acwr.py`/`fetch_hevy.py`; ACWR siła → `acwr.py` |
| Cardio (Apple) | `fetch_mcp.py` → Apple HTTP | `apple_normalize.py` (filtruje cardio, dedup po id, sortuje) | obciążenie TRIMP z tętna → `apple_cardio.py`; ACWR cardio + guard próbki → `acwr.py` |
| Fizjologia dzienna | `fetch_mcp.py` → Apple HTTP | `apple_normalize.py` (selekcja pól, temp, waga) | HRV/RHR/sen/trendy → `baseline.py`; readiness → `readiness_integration.py` |
| Zjedzone kalorie | `fetch_mcp.py` → MFP MCP (HTTP, `get_diary`) | `mfp_normalize.py` (wyciąga daily_totals.calories) | bilans wydatek vs kcal, ryzyko niedoboru → `energy_balance.py` |
| Wydatek energetyczny | — (z Apple powyżej) | — | TDEE + cel kcal + białko → `nutrition_adaptive.py` |

Kluczowe: `mcp_fetchers` dostarcza dane (transport + deterministyczna konwersja),
rdzeń `analytics` je interpretuje (testowalna, wersjonowana logika). Żadna
logika obliczeniowa nie wycieka do warstwy pobierania; żadne wołanie MCP nie
wchodzi do rdzenia.

| Plik | Rola |
|------|------|
| `fetch_mcp.py` | **cron-ready**: sam łączy się z MCP (Hevy stdio + Apple HTTP), pobiera, normalizuje i odpala analizę — bez agenta |
| `hevy_normalize.py` | surowy workout Hevy MCP → `tmp/hevy_workouts.json` (format ACWR + `_volume` objętości mechanicznej) |
| `apple_normalize.py` | surowe daily/temp/workouts z Apple → `tmp/apple_input.json` |
| `mfp_normalize.py` | surowe diary MFP → `tmp/mfp_kcal.json` (zjedzone kcal/dzień dla bilansu energetycznego) |
| `build_input.py` | składa payload z `tmp/*.json` i odpala `analytics.run_analysis` (+ agreguje `hevy_volume`) |
| `__init__.py` | doc pakietu |
| `tmp/` | bufory pośrednie (gitignore'owane) |

### 6.1 Automatyczny odczyt MCP — `fetch_mcp.py` (cron-ready)

Zamyka lukę „agent ręcznie woła MCP i wkleja JSON”. Skrypt sam:
1. **Hevy** — odpala izolowany proces `node standalone.mjs` (stdio, `HEVY_API_KEY`
   z env), prowadzi JSON-RPC handshake, woła `get-workouts` (paginacja do okna
   ACWR) i `get-workout` (szczegóły z exercises/sets).
2. **Apple** — łączy się przez HTTP streamable na `127.0.0.1:8766/mcp`
   (`get_daily_activity_range`, `get_data` temp, `list_recent_workouts`).
3. **MFP** — łączy się przez HTTP streamable (`MFP_MCP_URL`, domyślnie
   `http://localhost:8000/mcp` — kontener mfp-mcp, port 8000) i woła
   `get_diary` per dzień okna (zjedzone kcal dla bilansu energetycznego).
4. Normalizuje (przez `hevy_normalize`/`apple_normalize`/`mfp_normalize`),
   składa payload (przez `build_input`) i odpala `analytics.run_analysis`.

```bash
python3 -m mcp_fetchers.fetch_mcp --target 2026-08-09 --out /tmp/r.json
# lub (domyślnie dziś):
python3 -m mcp_fetchers.fetch_mcp
```
Flagi: `--phase redukcja|masa`, `--weight`, `--skip-hevy`, `--skip-apple`,
`--skip-mfp`, `--days N`, `--only-cardio`.

**Szczegóły specyficzne dla standalone Hevy (zaobserwowane empirycznie):**
- Narzędzia mają nazwy **kebab-case bez prefiksu**: `get-workouts`, `get-workout`
  (NIE `hevy_get_workouts` — to nazwy w openclaw, nie w standalone).
- `get-workouts` zwraca **gołą listę 5 workoutów/stronę** i akceptuje tylko
  `{page}`; `pageSize` w camelCase jest odrzucane jako invalid input.
- `get-workout` przyjmuje `workout_id` i zwraca workout z "workout" wrapperem.
- Apple notifications (`notifications/initialized`) odpowiada 202/no-content —
  klient HTTP musi to obsłużyć bez próby parsowania wyniku.

### Szybki przepływ użycia (dla agenta)

```bash
# 1) pobierz surowe dane przez MCP i zapisz:
#    hevy:  hevy__get-workout (pojedynczo, strony sklejone)
#    apple: apple__get_daily_activity_range, apple__get_data(temp),
#           apple__list_recent_workouts
#    -> zapisz jako raw_hevy.json / raw_apple.json

# 2) znormalizuj:
cat raw_hevy.json  | python3 -m mcp_fetchers.hevy_normalize  > tmp/hevy_workouts.json
cat raw_apple.json | python3 -m mcp_fetchers.apple_normalize > tmp/apple_input.json

# 3) uruchom analizę:
python3 -m mcp_fetchers.build_input --target 2026-08-09 --out /tmp/result.json
```

---

## 7. Dziennik zmian (2026-08-09) — kopia robocza `analytics-review`

Poprawki naniesione na kopii (nie w ORYGINALE — do przeglądu/zatwierdzenia):

### 7.1 KLUCZOWE odkrycie: baza cardio Apple jest po prostu pusta przed 4.08

Weryfikacja narzędziami MCP (`list_recent_workouts` z `start_date=2026-06-01`
zwrócił te same ~10 workoutów co limit=20; `start_date=2026-01-01` -> 0):
**w bazie Apple Watch NIE MA starszych sesji cardio**. `n_cardio_days` cardio
ACWR = 4-5 to NIE artefakt paginacji (paginacji nie potrzeba — dane są wszystkie),
tylko realny brak danych. Watch był używany do siły (zapis przez Hevy/Garmin),
a cardio (kolarki itd.) logowano z przerwami dopiero od 4.08.

→ Wniosek: żaden `fetch_apple_cardio.py` do stron/paginacji NIE uzupełni chronic
28d, bo danych fizycznie nie ma. Realna poprawka to guard na próbkę (7.2).
Z czasem, gdy baz będzie przyrastać, chronic cardio się samo wypełni.

### 7.2 Guard cardio ACWR na wiarygodność próbki + kara oparta na cardio_7d (rdzeń)

Dwie powiązane zmiany, dopasowane do modelu użytkownika (cardio "szarpane":
nieregularne, ale mocne/submaksymalne, celowo wpływające na tygodniowy blok):

**a) `analytics/acwr.py::build_cardio_acwr` + `ACWRSettings.cardio_min_valid_days=12`:**
ratio cardio jest wiarygodne do strefy ryzyka TYLKO przy regularnym cardio
(>= 12 dni z obciążeniem w 28d). Przy nieregularnym — zawsze zaniżone chronic
fałszuje w górę (np. 3.32 z 4 sesji) → strefa "niewystarczające dane" zamiast
fałszywego "wysokie ryzyko". `acwr_readiness_modifier` daje tej strefie 0.

**b) `analytics/readiness_integration.py` — kara cardio z `cardio_7d_sessions`:**
skoro ratio cardio jest niewiarygodne, realny sygnał obciążenia tygodnia to
ile MOCNYCH sesji wpadło w ostatnie 7d. `_cardio_7d_penalty` daje: 0-1 sesji = 0,
2 = +1, 3+ = +2 (progi `ACWR.cardio_7d_penalty_thresholds=(2,3)`). Ratio cardio
karze tylko gdy chronic wiarygodny (regularne cardio); wtedy max(ratio, 7d).
`cardio_detail` raportuje `cardio_7d_total` (TRIMP) + `cardio_7d_sessions`.

Efekt (realne dane 2026-08-09): cardio ratio 3.32 = "niewystarczające dane",
aleg 4 mocne sesje w 7d dają kara +2 → readiness czerwona z POPRAWNEGO powodu
(4 mocne sesje w tygodniu), nie z artefaktu statystycznego chronic.

### 7.3 Dedupe workoutów Apple po `id` + sortowanie

`apple_normalize.py`: filtruje zdublowane kopie tej samej sesji (pole
`deduped_copies` > 1) po `id` — przepuszcza tylko pierwszą kopię. Sortuje
cardio chronologicznie po `start`. `_SEEN_IDS` resetowany per uruchomienie
(determinizm).

### 7.4 Sortowanie workoutów Hevy po `startTime`

`hevy_normalize.py` + `fetch_mcp.py::fetch_hevy`: sortują workouty rosnąco po
`startTime`/`start_time` — determinizm niezależny od kolejności stron; rolling
window w `build_daily_load_series` zakłada porządek chronologiczny.

### 7.5 `fetch_mcp` — nowe flagi `--days`, `--only-cardio`; zakres dat dla Apple

- `--days N` — okno wstecz (domyślnie 35 = chronic).
- `--only-cardio` — pomija Hevy, tylko Apple cardio (implikuje `--skip-hevy`).
- `fetch_apple` przekazuje `start_date`/`end_date` do `list_recent_workouts`
  (zamiast tylko `limit=20`) — przy dłuższym lookbacku nie polega na "ostatnich N".

### 7.6 Drobiazgi

`build_input.load_normalized()` → poprawna sygnatura `tuple[list,list,list,list]`.
(było `tuple[list,list,list]` przy faktycznym zwrocie 4 elementów).

### 7.7 Dodatkowe testy

`test_apple_cardio.py`: `test_build_cardio_acwr_on_cycling` zaktualizowany
(2 sesje -> "niewystarczające dane", nie "wysokie ryzyko"),
`test_build_cardio_acwr_trustworthy_sample` (>= progu regularności -> normalne
strefy) + nowy `TestCardio7dPenalty` (progi karencji 7d: 0/1/2).
Wszystkie 236 testów rdzenia przechodzi (245 z energy_balance — patrz 7.8).

### Nadal otwarte (z §4, niezmienione)

- RPE coverage < 70% dla siły (starsze treningi bez RPE) — sceptycyzm, nie fix.
- `mfp_weight` puste → TDEE bez punktów kontrolnych wagi.
- Dubl Apple↔Hevy na poziomie (data, nazwa) — teoretycznie nie powstaje
  (rozłączne kategorie: cardio vs siła), dedupe na `id` w Apple to jedyne
  realne zabezpieczenie.

### 7.8 Ocena bilansu energetycznego (wydatek vs zjedzone) — NOWY moduł

`analytics/energy_balance.py` — domyka lukę, o którą pytano: rdzeń liczył TDEE
(wydatek) i cel kcal, ale NIE oceniał czy wydatek jest pokryty zjedzonymi kcal.
Kumulujący się niedobór upośledza regenerację i zwiększa ryzyko kontuzji/
urazu/infekcji — teraz to JAWNY sygnał, nie domyślanie w głowie agenta.

**Wejście:** `mfp_daily_kcal` = lista `{day, kcal}` (zjedzone z MFP diary),
+ `target_kcal` z nutrition (TDEE 7d). **Wyjście** (sekcja `energy_balance`
w raporcie): średnie zjedzone/wydatek, `covering_pct` (jaki % wydatku pokryty),
`cumulative_deficit_kcal` (skumulowany niedobór w oknie 7d) + `deficit_risk`
(niski/średni/wysoki) + `daily` (per-day bilans).

**Progi (`ENERGY_BALANCE`):** `balance_window_days=7`, `min_valid_days=3`
(za mało danych -> "niewystarczające dane", nie kara), `deficit_low_kcal=1500`,
`deficit_high_kcal=3500`. Przykłady (TDEE 2156):

| zjedzone/dzień | pokrycie | niedobór/tydz | ryzyko |
|---|---|---|---|
| 2600 | 120% | +3108 (nadwyżka) | niski |
| 1900 | 88% | -1792 | średni |
| 1500 | 70% | -4592 | wysoki |

**Ważne dla agenta:** bez danych MFP (`mfp_daily_kcal` puste) sekcja zwraca
"niewystarczające dane" — nie wolno raportować ryzyka niedoboru bez realnych
zjedzonych kcal. Dane zjedzone dostarcza `mfp__mfp_get_diary` (suma kcal/dzień
dla każdego dnia okna). To łączy TDEE (Apple) z intake (MFP) w jedną ocenę.

Testy: `tests/test_energy_balance.py` (6 testów). Pełny zestaw: 245 testów OK.

### 7.9 Fetcher zjedzonych kcal — `mfp_normalize.py` (pobieranie, nie rdzeń)

Zgodnie ze stanem architektury: **pobieranie danych z MCP żyje w `mcp_fetchers/`,
NIE w rdzeniu `analytics/`** (rdzeń jest celowo offline — README §1). Dlatego
skrypt dostarczający zjedzone kcal to `mcp_fetchers/mfp_normalize.py`, nie
funkcja w `nutrition_adaptive`/`energy_balance`.

Czyta surowy dziennik z `mfp__mfp_get_diary` (pojedynczy lub lista = okno dni)
przez stdin → wyciąga `daily_totals.calories` per dzień → `tmp/mfp_kcal.json`
(lista `{day, kcal}`). `build_input.load_normalized()` wczytuje ten plik i
wstawia w `mfp_daily_kcal` payloadu; `energy_balance` (rdzeń) liczy bilans.

```bash
# agent pobiera diary per dzień okna (MFP MCP), skleja w listę, np. mfp_diaries.json
cat mfp_diaries.json | python3 -m mcp_fetchers.mfp_normalize > tmp/mfp_kcal.json
python3 -m mcp_fetchers.build_input --target 2026-08-09   # wczyta mfp_kcal.json
```

Walidacja na REALNYCH danych (05-08.08): 2430/2603/1908/2561 kcal vs TDEE 2156
→ pokrycie 110%, bilans +878 kcal (nadwyżka), ryzyko niskie; per-day widzi
dzień z niedoborem (07.08: -248), ale tydzień jako całość pokryty. Przy < 3
ważnych dniach MFP w oknie sekcja zwraca "niewystarczające dane" (nie fałszuje).

Testy: `tests/test_mfp_normalize.py` (7 testów). Pełny zestaw: 252 testy OK.

### 7.10 `fetch_mcp` pobiera MFP samodzielnie (spójny transport, bez agenta)

Wcześniejsza wersja tabeli (7.8/6.0) zakładała, że zjedzone kcal dostarcza
agent ręcznie (`mfp_get_diary`). To było niespójne z resztą `mcp_fetchers`,
ktora sama łączy się z MCP (Hevy stdio, Apple HTTP). Poprawka: `fetch_mcp.py`
ma teraz trzeci transport — **MFP MCP (HTTP streamable, `MFP_MCP_URL`,
domyślnie `http://localhost:8000/mcp` — kontener mfp-mcp na porcie 8000)** —
woła `mfp_get_diary` per dzień okna, normalizuje przez `mfp_normalize` do
`tmp/mfp_kcal.json` i `build_input` go wczytuje. Flaga `--skip-mfp`.

**Dwie rzeczy wyłapane live (10:12):**
- narzędzie nazywa się **`mfp_get_diary`** (prefiks `mfp_`, snake_case), nie
  `get_diary` (to nazwa w narzędziach OpenClaw; w standalone MCP jest z prefiksem).
- trzeba przekazać **`response_format: "json"`** — bez niego serwer zwraca
  markdown (`{"raw": "## Food Diary..."}`), którego nie da się sparsować do
  dziennika z `daily_totals`. Parametr date jest w `{params: {date}}`.

Walidacja live (2026-08-09): `fetch_mcp` pobrał 27/35 dni z kcal; energy_balance
widzi 6 ważnych dni w oknie 7d, śr. 2090 vs wydatek 2156 (pokrycie 96.9%),
skumulowany niedobór -395 kcal -> ryzyko niskie.

Wzorzec jest teraz spójny: **żaden krok nie wymaga agenta w pętli** —
`fetch_mcp` łączy się z Hevy + Apple + MFP, normalizuje i odpala analizę.
Agent/cron tylko interpretuje wynik. (MFP przez HTTP nadal dostępna ręcznie
przez narzędzia `mfp_*` — np. do selektywnego dozbierania dni.)

