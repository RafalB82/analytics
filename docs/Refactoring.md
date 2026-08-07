# Refactoring — co zostało wdrożone

Wdrożenie planu `analytics_refaktor/refactorig.md` na branchu `refactor/engineering-quality`.
**Celem nadrzędnym było: nie zmienić żadnego wyniku algorytmicznego** — tylko podnieść
utrzymywalność, testowalność i niezawodność. Podstawa (architektura fetch → analytics →
JSON → LLM oraz determinizm obliczeń) pozostała nietknięta.

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
├── fetch_apple.py / fetch_hevy.py / fetch_mfp.py
└── run_analysis.py        # F5  refaktor: parse_input / validate_input / build_* / run
tests/                     # F7  113 testów, pokrycie 80%+ (algorytmy 100%)
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
- **`models.py` jeszcze nie jest głównym nośnikiem outputu** — `run_analysis` nadal używa
  `dataclass asdict` (Pydantic modele są gotowe i testowane z osobna, ale wpięcie pełnego
  `AnalysisReport` to dalszy krok, bezpieczny i izolowany).
- **cron/integracja z harmonogramem** — logi (F4) nabiorą pełnej wartości dopiero po
  wpięciu analizy do zadania cron; to kolejny krok poza zakresem tego refaktoru.
