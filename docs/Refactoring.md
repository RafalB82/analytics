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
│   └── metrics.py
├── exceptions.py          # F3  domenowe wyjątki (InsufficientData / InvalidMetric / ...)
├── logging.py             # F4  strukturalne logowanie (stderr, ANALYTICS_DEBUG=1)
├── models.py              # F6  typowane modele Pydantic (DailyMetrics, TempAlertStatus, ...)
├── baseline.py / acwr.py / temperature.py / nutrition_adaptive.py
├── readiness_integration.py
├── confidence.py            # faza 2.0: Confidence Score (0-100, High/Medium/Low)
├── stability.py             # faza 3.0: Activity Stability (Stable/Moderately/Highly)
├── metrics.py               # faza 6.0: centralny rejestr metryk
├── pipeline.py              # faza 7.0: Analytics Pipeline (6 stage'ów)
├── explain.py               # faza 9.0: Explainability Layer (reason[] dla LLM)
├── fetch_apple.py / fetch_hevy.py / fetch_mfp.py
└── run_analysis.py          # F5  refaktor: parse_input / validate_input / build_* / run
tests/                     # F7  190 testów, pokrycie ~90% (algorytmy 100%)
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
- Fix: domyślne `None` + lazy lookup w ciele funkcji (`alpha = alpha if alpha is not None
  else _CFG.ewma_alpha`) w `baseline.py` (6×), `acwr.py` (5×), `temperature.py` (2×),
  `nutrition_adaptive.py` (2×). Zweryfikowane empirycznie przez `inspect.signature` i podmianę configu.

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
