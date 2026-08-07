# analytics

Deterministyczna analiza gotowości treningowej — warstwa obliczeniowa
pod LLM. **LLM nigdy nie liczy**: dostaje gotowy JSON i tylko go interpretuje.

Kod jest w pełni offline i testowalny — nie woła żadnego MCP bezpośrednio.
Dane (Apple, Hevy, MFP) zbiera i wstrzykuje agent jako JSON.

## Idea

Z wielu sygnałów fizjologicznych i treningowych składa się jeden, policzalny
scoring gotowości (strefa + zalecenia RPE/objętość). Obliczenia są w 100%
deterministyczne; każdy moduł ma jedną odpowiedzialność.

```
Apple (HRV/RHR/sen + aktywność + waga) ─┐
Hevy (tonaż+RPE) ────────────────────────┤→ run_analysis → JSON → LLM (interpretacja)
MFP (zjedzone kalorie/jedzenie) ─────────┘
```

| Sygnał | Źródło | Co liczy |
|---|---|---|
| HRV, RHR, sen | Apple MCP | rolowany baseline (EWMA), trend, scoring |
| Tonaż + RPE | Hevy MCP | ACWR (acute:chronic workload ratio) |
| Aktywność + waga | Apple MCP | **cel kaloryczny** (TDEE z aktywności + marża wg celu) |
| Zjedzone kalorie | MFP MCP | rejestracja jedzenia (nie bierze udziału w TDEE) |
| Temperatura nadgarstka | Apple MCP | twardy override strefy |

## Struktura

- `config/` — centralna konfiguracja (okna, alphy, progi, zakresy metryk)
- `validators/` — walidacja metryk wejściowych (NaN/inf/zakres)
- `exceptions.py`, `logging.py`, `models.py` — wyjątki domenowe, logowanie, modele Pydantic
- `baseline.py` — baseline EWMA + trend + detekcja przesunięcia normy
- `acwr.py` — Acute:Chronic Workload Ratio (Gabbett 2016)
- `temperature.py` — alert temperatury nadgarstka (twardy override)
- `nutrition_adaptive.py` — cel kaloryczny (TDEE z aktywności + marża wg celu) + białko
- `readiness_integration.py` — finalny scoring + strefa
- `fetch_*.py` — konwersja danych z MCP na serie analityczne
- `run_analysis.py` — orchestrator (wejście JSON → wyjście JSON)
- `tests/` — testy jednostkowe + integracyjne · `docs/` — dokumentacja

## Uruchomienie

```bash
pip install -e .            # zależności: numpy, pydantic

python -m analytics.demo_full            # przebieg na danych syntetycznych
python -m analytics.run_analysis '<json>'  # ręczny orchestrator
```

### Wejście JSON

```json
{
  "source": "apple+hevy+mfp",
  "target_date": "2026-08-07",
  "apple_daily": [
    {"date": "2026-08-07", "resting_heart_rate": 53.0,
     "heart_rate_variability": 44.33, "sleep": {"total_hours": 6.43},
     "basal_energy_burned": 7200.0, "active_energy": 4200.0,  // kJ (TDEE)
     "weight_body_mass": 71.05, "body_fat_percentage": 15.1}  // punkty kontrolne
  ],
  "apple_temp": [ {"date": "2026-08-06", "value": 35.98} ],
  "hevy_workouts": [ /* z hevy__get-workouts, strony sklejone */ ],
  "cardio_sessions": [ /* opcjonalne sesje cardio/MTB, sumowane do obciążenia ACWR */
    {"startTime": "2026-08-05T08:00:00", "duration_minutes": 90, "rpe": 6}
  ],
  "params": {
    "phase": "utrzymanie",
    "bodyweight_kg": 71.0
  }
}
```

> `phase` = aktualny cel: `utrzymanie` (marża 0) / `redukcja` (−15%) / `masa` (+10%).
> `bodyweight_kg` opcjonalny (gdy brak punktu wagi z Apple w danym dniu).

### Wyjście JSON

Sekcje: `readiness` (strefa/RPE/objętość), `acwr` (acute/chronic/ratio/zone),
`acwr_detail.rpe_coverage`, `temperature`, `nutrition` (cel kaloryczny z aktywności),
`baseline_trends` (HRV/RHR trend + R²), `inputs` (ile punktów użyto).

## Ważne zachowania

- **Brak snu ≠ 0h.** Pominięty składnik snu to nie kara; brak jest jawnie
  sygnalizowany (`sleep_missing` / `inputs.sleep_data: "missing"`). Realnie
  krótki sen (<5.5h = +2, <6.5h = +1) karze normalnie.
- **RPE coverage.** Starsze treningi mogą nie mieć RPE (liczy się sam tonaż),
  co zaniża chronic ACWR i zawyża ratio. **Sprawdzaj `rpe_coverage`** — przy
  <70% traktuj wynik ACWR ze sceptycyzmem.
- **Temperatura** nadgarstka to osobne źródło (`apple__get_data(name=...wrist_temperature)`),
  z poprzedniej nocy; alert ≥0.3°C od baseline, severity „znacząca" → twardy
  override na strefę czerwoną.
- `run_analysis` wymaga min. 6 punktów HRV do baseline (EWMA).
- **Cel kaloryczny z aktywności** (nie z MFP): TDEE = średnie basal+active z okna
  (7d, docelowo 28d) z Apple; cel = TDEE + marża % wg `phase`. MFP rejestruje
  jedzenie, ale nie wyznacza celu.
- **Waga** to punkt kontrolny z Apple (w dni ważenia), nie trend — służy do
  białka i kontekstu; bez niej cel kaloryczny i tak działa (z samej aktywności).

## Jakość / CI

- 113 testów, pokrycie 80%+ (algorytmy ~100%)
- Ruff + mypy czyste; GitHub Actions na Python 3.10/3.12/3.13
- Szczegóły refaktoru: `docs/Refactoring.md`
