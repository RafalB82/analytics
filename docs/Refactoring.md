# Refactoring — co zostało wdrożone

Wdrożenie planu `analytics_refaktor/refactorig.md` na branchu `refactor/engineering-quality`.
**Celem nadrzędnym było: nie zmienić żadnego wyniku algorytmicznego** — tylko podnieść
utrzymywalność, testowalność i niezawodność. Podstawa (architektura fetch → analytics →
JSON → LLM oraz determinizm obliczeń) pozostała nietknięta. Niniejszy plik dokumentuje
zarówno refaktor inżynieryjny (1.0), jak i warstwę analityczną (2.0).

## Struktura docelowa

```
analytics/                 # pakiet
├── __init__.py
├── config/                # F1  centralna konfiguracja (okna, alphy, progi, zakresy)
│   └── settings.py
├── validators/            # F2  walidacja metryk wejściowych (NaN/inf/zakres)
│   ├── metrics.py         #     walidatory metryk fizjologicznych
│   └── input.py           # f10 validate_input/_parse_target/ALLOWED_SOURCES (z run_analysis)
├── exceptions.py          # F3  domenowe wyjątki (InsufficientData / InvalidMetric / ...)
├── logging.py             # F4  strukturalne logowanie (stderr, ANALYTICS_DEBUG=1)
├── models.py              # F6  typowane modele Pydantic (DailyMetrics, TempAlertStatus, ...)
├── baseline.py / acwr.py / temperature.py / nutrition_adaptive.py
│   └── (f10) build_acwr / build_temp_alert / serialize_temp_output / build_goal_output
├── readiness_integration.py
├── confidence.py            # faza 2.0: Confidence Score (0-100, High/Medium/Low)
├── stability.py             # faza 3.0: Activity Stability (Stable/Moderately/Highly)
├── metrics.py               # faza 6.0: centralny rejestr metryk
├── pipeline.py              # faza 7.0: Analytics Pipeline (6 stage'ów) — NIE importuje run_analysis
├── explain.py               # faza 9.0: Explainability Layer (reason[] dla LLM)
├── fetch_apple.py / fetch_hevy.py / fetch_mfp.py
│   └── (f10) fetch_apple: build_apple_models
└── run_analysis.py          # f10 thin CLI: parse_input / main / run->PIPELINE / _json_safe
tests/                     # F7  213 testów, pokrycie ~90% (algorytmy 100%)
.github/workflows/ci.yml   # F8  Ruff -> mypy -> pytest(coverage) na 3 wersjach Pythona
pyproject.toml             # definicja pakietu + narzędzia (ruff, mypy, pytest)
docs/
```

## Zrealizowane fazy (w nawiasach: plan — stan)

| Faza | Plan | Stan |
|------|------|------|
| F0 | (brak w planie) | pakiet `analytics/` + `pyproject.toml` — **wcześniej repo w ogóle się nie uruchamiało** (importy względne bez pakietu) |
| F1 Central configuration | P2 | `config/settings.py` — dataclass per obszar, wszystkie stałe w jednym miejscu |
| F2 Input validation | P1 | `validators/metrics.py` — zakresy z configu, rzuca `InvalidMetricError` na NaN/inf/poza zakres |
| F3 Domain exceptions | P2 | `exceptions.py` + świadoma polityka: `InsufficientDataError`→fallback, `InvalidMetricError`→error |
| F4 Logging | P3 | `logging.py` + logi INFO/DEBUG w modułach |
| F5 Refactor run_analysis | P2 | rozbicie na funkcje jednozadaniowe (parse/validate/build/run) |
| F6 Typed models | P3 | `models.py` (Pydantic v2); **usunięty hack `_temp_alert_obj`** (obiekt TempAlert przekazywany wprost) |
| F7 Unit tests | P1 | 113 testów (baseline/acwr/temp/nutrition/readiness/validators/integration), 80% coverage |
| F8 CI | P1 | GitHub Actions: ruff + mypy + pytest(--cov-fail-under=80) na 3.10/3.12/3.13 |
| F9 Documentation | P3 | `docs/` + ten plik |
| F10 Visualization | P4 | **nie wdrożone** — celowo (brak potrzeby przy ręcznym użyciu) |

## Zmiany jakości (bez zmiany wyników)

- **numpy bool → native bool**: `detect_baseline_shift` i `TrendResult.reliable` zwracają
  teraz `bool` zamiast `np.bool_` (kiedyś psuło `json.dumps`).
- **`_json_safe` zamiast `_clean`** w orchestratorze: konwersja numpy → natywne przed serializacją.
- **`float(value)` → walidatory**: wszystkie konwersje z surowych danych przechodzą przez
  `validators` (HRV/RHR/sen/temp/waga) — układają się z `InvalidMetricError` na złe dane.
- **sanity-check tonażu** w `fetch_hevy._set_load` (odrzucanie absurdalnych ciężarów/reps).
- **usunięty martwy blok `if __name__ == "__main__"`** z `readiness_integration.py`
  (miał zepsute absolutne importy; demo zastąpiły testy).

## Jak uruchomić

```bash
pip install -e ".[dev]"          # lub: pip install -e .
python -m analytics.demo_full    # pełny przebieg na danych syntetycznych
python -m analytics.run_analysis '<input_json>'   # ręczny orchestrator

# narzędzia
ruff check analytics tests
mypy analytics
pytest --cov=analytics --cov-fail-under=80

# debug (szczegółowe logi obliczeń)
ANALYTICS_DEBUG=1 python -m analytics.demo_full
```

## Sekcja „nie zrobiono"

- **F10 Visualization** — świadomie pominięte (brak realnej potrzeby przy podawaniu JSON).
- **cron/integracja z harmonogramem** — logi (F4) nabiorą pełnej wartości dopiero po
  wpięciu analizy do zadania cron; to kolejny krok poza zakresem tego refaktoru.

---

# Refaktor 2.0 — warstwa analityczna (analytics_refaktor/refactorig.md)

Po domknięciu inżynieryjnego refaktoru 1.0 wykonaliśmy **fazy 2.0** — rozszerzenia
analityczne, bez zmiany istniejących wyników algorytmicznych. Zasada nadrzędna pozostała:
**nowe pola domyślnie `None` / wstecznie kompatybilne, determinizm nietknięty**.

## Zrealizowane fazy 2.0

### Confidence Score (`confidence.py`)

Każda metryka dostaje wiarygodność 0-100 + etykietę **High / Medium / Low** z ważonych
składowych (historia 30%, kompletność 25%, stabilność 25%, pokrycie 20%):

```json
"tdee": {"score": 98, "label": "High", "n_points": 14, "window_days": 7,
         "completeness": 1.0, "stability": 0.928, "coverage": 1.0}
```

- `compute_confidence()` + `hr_series_stability()` (stabilność z współczynnika zmienności).
- `ConfidenceSettings` w configu (wagi + progi 80/60).
- Zwraca `None`, gdy za mało danych (< 3 pkt) — nie fabrykuje wiarygodności.
- Objęte: hrv, rhr, tdee, acwr.

### Activity Stability (`stability.py`)

Kategoryzacja zmienności aktywności z avg 7/14/28 dni:
`Stable` / `Moderately Variable` / `Highly Variable`.

```json
"activity_stability": {"avg_7d": 19214.3, "avg_14d": 19071.4,
                       "avg_28d": 19071.4, "variation": 0.007, "category": "Stable"}
```

- `activity_stability()` + `StabilitySettings` (progi variation 0.10 / 0.25).

### Weight Trend (rozszerzenie `nutrition_adaptive.compute_weight_trend`)

Trend wagi zwraca teraz ustrukturyzowany `WeightTrend` zamiast gołego slope:
`slope_kg_per_day`, `rolling_median_kg` (robustny środek ciężkości), `weekly_trend_kg`,
`n_points`, `window_days`. Wpięty do `AnalysisReport.weight_trend` (z serii wag MFP).

```json
"weight_trend": {"slope_kg_per_day": 0.1, "rolling_median_kg": 70.45,
                 "weekly_trend_kg": 0.7, "n_points": 10, "window_days": 14}
```

### Metrics Registry (`metrics.py`)

Centralny rejestr metryk (tylko metadane — nie dubluje logiki/walidacji):
`Metric(name, description, unit, normal_range, section)` + `METRICS` (10 metryk)
+ `METRIC_BY_NAME` + `metrics_summary()`.

### Analytics Pipeline (`pipeline.py`)

Jawna orkiestracja jako sekwencja stage'ów zamiast jednej wielkiej `run()`:

```text
InputValidation -> ModelBuilding -> Analytics -> Confidence -> Explain -> Serialization
```

- `PipelineContext` przenosi dane między stage'ami; `AnalyticsPipeline` + `PIPELINE`.
- Stage'e są testowalne osobno i wymienialne.
- `run()` deleguje do `PIPELINE`; test regresji potwierdza identyczny output.

### Explainability Layer (`explain.py`)

Każda metryka niesie listę strukturalnych powodów dla LLM:

```json
"explanations": {
  "acwr": ["ACWR: strefa wysokie ryzyko (ratio=2.37)", "Pokrycie RPE: 100%"],
  "tdee": ["TDEE: 4592 kcal (okno 7 dni)", "Cel: utrzymanie -> target 4592 kcal"]
}
```

- `build_explanations()` — LLM używa strukturalnych przyczyn zamiast reverse-engineeringu.
- Nowy stage `explain_stage` w pipeline.

## Poprawki i uwagi

- **Zakres wagi**: `40-200 kg` zamiast `20-400` (20 kg = kilkulatek, 400 = słoń).
- **Confidence ACWR**: używa REALNEJ liczby dni z treningiem (`load>0`), nie dni
  wypełnionych zerami przez `fill_missing_days` — unika sztucznego zawyżenia.
- Waga z Apple pozostaje punktem kontrolnym; `weight_trend` wymaga serii ≥ 8 pomiarów
  (aktualnie `None`, bo MFP nie dostarcza historii wagi).

## Wstrzymane / daleka kolejka

- **F10 / Visualization** — wstrzymane (brak konsumenta UI/CLI).
- **Strategy Pattern / Plugins** — wstrzymane (gdy urośnie liczba celów/ modułów).

## Nowy model odżywiania (cel kaloryczny z aktywności)

Osobny commit po refaktorze strukturalnym — zmiana logiki odżywiania:

- **TDEE z aktywności Apple** (nie z MFP): śr. basal_energy + active_energy z okna
  (7d domyślnie, 28d docelowo — oba liczone, aktywne 7d).
- **Cel kaloryczny = TDEE + marża % wg celu**: utrzymanie 0 / redukcja −15% / masa +10%.
- **MFP dostarcza tylko kalorie/jedzenie** — cel z MFP pomijany, waga nie bierze się z MFP.
- **Waga = punkt kontrolny z Apple** (w dni ważenia) — do białka i kontekstu, nie trend.
- Marży i okna konfigurowalne w `config/settings.NutritionSettings`.

Niezbędny bug w źródle: `health-auto-export-api` sumował te same pomiary zegarka
z wielu identyfikatorów źródła Health Kit (dubel ~3x energii). Naprawiony deduplikacją
po minucie (`_dedup_rows`) — osobny commit w tamtym repo (`2eaa491`).

## Poprawki z audytu inżynieryjnego (branch fix/engineering-quality-audit)

Naprawy po audycie kodu z 2026-08-07 (`audyt.md`), wszystkie zweryfikowane
testami (161 przechodzi, pokrycie 90%), `ruff check` i `mypy` czyste.

### 1. 🔴 Rozjazd faz TDEE ↔ białko (bug krytyczny)
- `goal_margin` używa kluczy `redukcja/masa`, a `protein_g_per_kg` `deficyt/nadwyżka`.
  `compute_tdee` przekazywał `goal` wprost do `compute_protein_target`, więc przy
  `phase="redukcja"` białko cicho spadało na fallback **1.8 g/kg zamiast 2.2 g/kg**
  (przy 71 kg = 28 g dziennie mniej, akurat w fazie ochrony mięśni).
- Fix: jawna mapa `_GOAL_TO_PROTEIN_PHASE` (`redukcja→deficyt`, `masa→nadwyżka`)
  używana w `compute_tdee`. Testy na PEŁNEJ ścieżce `compute_tdee(goal=..., bodyweight_kg=...)`
  z asercją na `protein_g` (nie tylko `margin_pct`).

### 2. 🟡 Eager-evaluated defaulty z configu
- Wzorzec `alpha: float = _CFG.ewma_alpha` wyliczał wartość RAZ przy imporcie —
  podmiana `settings.BASELINE`/`ACWR` w runtime nie miała żadnego efektu.
- Fix (cz. 1): domyślne `None` + lazy lookup w ciele funkcji (`alpha = alpha if alpha is not None
  else _CFG.ewma_alpha`) w `baseline.py` (6×), `acwr.py` (5×), `temperature.py` (2×),
  `nutrition_adaptive.py` (2×).
- Fix (cz. 2, głębszy wariant tego samego korzenia): moduły importowały
  `from .config.settings import BASELINE as _CFG` — podmiana całego obiektu
  `settings.BASELINE = dataclasses.replace(...)` nie docierała do modułów, bo
  `_CFG` to osobny alias na stary obiekt. Zamieniono na `from .config import settings`
  + odwołania `settings.BASELINE.ewma_alpha` we wszystkich modułach
  (`baseline`, `acwr`, `confidence`, `nutrition_adaptive`, `readiness_integration`,
  `stability`, `temperature`) oraz w `run_analysis`/`pipeline` (były `ACWR_CFG`).
  Podmiana configu w runtime działa teraz wszędzie — warunek dla przyszłej
  konfiguracji per-sport/per-profil (np. inny próg ACWR dla MTB niż siłowe).

### 3. 🟡 MTB / cardio brak w ACWR
- ACWR liczył tylko tonaż z Hevy — jazda MTB nie wchodziła do obciążenia, zaniżając
  chronic w tygodniach z rowerem.
- Fix: `compute_cardio_session_load(duration_minutes, rpe)` w skali `min·RPE`
  (analogicznie do tonaż×RPE), `cardio_session_daily_load(dict)`, oraz nowy slot
  `cardio_sessions` w schema wejścia (`{"startTime","duration_minutes","rpe"}`)
  sumowany do dziennego loadu przez `build_daily_load_series(..., cardio_sessions=...)`,
  przeciągnięty przez `run_analysis.build_acwr` → `pipeline` → `run()`.

### 4. 🟢 Drobne
- `models.py`: `TempAlertPayload` przeniesiona przed `TempAlertStatus` (usuwa zależność
  od `from __future__ import annotations`).
- `validators/metrics.py`: docstring `ensure_sorted_ascending` zaktualizowany
  (rzuca `InvalidMetricError`, nie zwraca bool).
- `readiness_integration.py`: `sleep_hours_today: float | None` zamiast kłamiącego `float`.
- `config/settings.py`: `CONFIDENCE`/`STABILITY` (i klasy) dopisane do `__all__`.

## Wydolnościowe (cardio) z Apple Watch w osobnym ACWR

Rozszerzenie ACWR o sesje wydolnościowe (decyzja 2026-08-07).

### Schemat
- **Siłowe / kalisteniczne** → **wyłącznie z Hevy** (tonaż·RPE).
- **Cardio / wydolnościowe** → z **Apple Watch** (Outdoor Cycling, Rowing,
  Walking, Running, Swimming itd.), liczone **TRIMP** z tętna.
- Dubl nie powstaje, bo źródła obsługują rozłączne kategorie: `apple_cardio.py`
  odrzuca siłowe z Apple na starcie (biała lista typów cardio).

### Dlaczego osobne ACWR
Siła (tonaż: tysiące) i cardio (TRIMP: setki) to **różne jednostki** — nie można
ich mieszać w jednym stosunku. Dlatego:
- `build_cardio_acwr()` — osobny ACWR dla szeregu TRIMP,
- `acwr_combined_modifier(strength, cardio)` — scala oba na poziomie gotowości
  (maksimum punktów karnych), używane w `compute_full_readiness`.

### TRIMP (Banister)
`compute_trimp_session_load(avg_hr, duration_min, hr_rest, hr_max)`:
```
ratio = (HRavg - HRrest) / (HRmax - HRrest)
TRIMP = czas * ratio * 0.64 * e^(1.92 * ratio)
```
W pełni automatyczny (bez ręcznego RPE).

**hr_max z danej sesji treningowej** (`max_heart_rate_bpm`), gdy dostępny — pełniejszy obraz
pułapu osiągniętego w danej aktywności, a nie teoretyczna stała osobowego max. Gdy brak,
pada na `hr_max_default` z configu. `hr_rest` zawsze z configu (`hr_rest_default` — poranne
spoczynkowe, niezmienne per sesja).

### Wejście
Nowy slot `apple_workouts` w schema (lista z `apple__list_recent_workouts`),
przeciągnięty przez `validate_input` → `build_acwr` → `pipeline` → `run()`.
Raport: `acwr_detail.cardio` (acute/chronic/ratio/zone/n_cardio_days).

### Odporność klasyfikacji cardio
`is_cardio_workout()` ma (1) **czarną listę siłowych/kalistenicznych słów kluczowych**
(strength, lunge, press, curl, squat itd.) z **pierwszeństwem** — chroni przed false-positive
cardio, gdy custom nazwa zawiera cardio-słowo (np. "Walking Lunges" + "walk"); (2) dopasowanie
pełnej nazwy (zamknięty enum kategorii HealthKit); (3) dopasowanie po słowie kluczowym.
„Rowing" (cardio) nie jest blokowane przez siłowe „row" — usunięte z czarnej listy.

---

# Faza 10.0 — odwrócenie zależności `run_analysis` → pipeline (thin CLI)

**Branch:** `refactor/run-analysis-thin` (9 komitów). **Cel:** `run_analysis.py` jako
cienki CLI, a `pipeline.py` całkowicie odłączony od `run_analysis` (zero cyklu).
Zasada nadrzędna bez zmian: **zero modyfikacji logiki algorytmicznej** — tylko przenosiny
kodu między plikami, gwarantowane bramką `test_pipeline_matches_run`.

## Co zrobiono (kroki)

| Krok | Zmiana | Plik docelowy |
|------|--------|---------------|
| 1 | `build_acwr()` + `ACWR_LOOKBACK_DAYS` | `acwr.py` |
| 2 | `validate_input` + `_parse_target` + `ALLOWED_SOURCES` | `validators/input.py` |
| 3 | `build_apple_models` + `MIN_HRV_POINTS` | `fetch_apple.py` |
| 4 | `_compute_goal` → `build_goal_output`, `_temp_output` → `serialize_temp_output` | `nutrition_adaptive.py`, `temperature.py` |
| 5 | `_build_temp_status` → `build_temp_alert` (**cykl znika** — ostatni import) | `temperature.py` |
| 6 | `run_analysis.py` jako thin CLI (docstring) | — |
| 7 | sprzątanie resztek + test braku cyklu | `test_pipeline.py` |
| 8 | testy jednostkowe przeniesionych funkcji w nowych domach | `test_acwr/nutrition/temperature/validation` |
| 9 | CI + dokumentacja (ten plik) | — |

## Architektura po fazie 10.0

```
run_analysis.py  (thin CLI: parse_input / main / run->PIPELINE / _json_safe)
        │
        ▼  (run() deleguje lokalnym importem do PIPELINE)
pipeline.py      (orkiestracja 6 stage'ów) — NIE importuje run_analysis
        │
        ▼  (importuje TYLKO moduły dziedzinowe)
acwr / fetch_apple / temperature / nutrition_adaptive /
validators / baseline / confidence / stability / explain / readiness_integration
```

Zależność jest **jednostronna**: `run_analysis → pipeline → moduły dziedzinowe`.
`pipeline.py` nie zawiera żadnego importu z `run_analysis` — zależność cykliczna
`pipeline → run_analysis → pipeline` (źródło potencjalnych problemów) całkowicie
usunięta. Stan przed: pipeline importował 6 symboli z `run_analysis`.

## Stan po fazie 10.0

- **`run_analysis.py`: 389 → ~144 linie** (thin CLI).
- **213 testów** zielone (było 190), w tym nowy `test_pipeline_does_not_import_run_analysis`
  (analiza AST wszystkich modułów dziedzinowych — cykl nie wróci) + testy jednostkowe
  przeniesionych funkcji w nowych domach.
- **CI** (`.github/workflows/ci.yml`): ruff → mypy → `pytest --cov --cov-fail-under=80`
  na Python 3.10/3.12/3.13, odpala się na `refactor/**` — ten branch objęty.
- Deterministyczny output (bramka `test_pipeline_matches_run`) nietknięty.

## Uwagi

- `small_refactor.md` (rozdzielenie sygnałów aktywności) pozostaje **propozycją**
  (wariant A; status niezmieniony) — niezależne od fazy 10.0.
- `validators/input.py` jest osobnym modułem walidacji wejścia analizy (nie metryk);
  `validators/__init__.py` re-exportuje `validate_input` dla wygody pipeline'u.
