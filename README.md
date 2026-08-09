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
| Tonaż + RPE (siła) | Hevy MCP | ACWR siłowy (acute:chronic) |
| Cardio (TRIMP) | Apple Watch | ACWR cardio (osobny, inna skala) |
| Aktywność + waga | Apple MCP | **cel kaloryczny** (TDEE z aktywności + marża wg celu), białko |
| Zjedzone kalorie | MFP MCP | rejestracja jedzenia (nie bierze udziału w TDEE) |
| Trend wagi | MFP MCP (opcjonalne) | sekcja `weight_trend` (roll. median + slope) |
| Temperatura nadgarstka | Apple MCP | twardy override strefy (+ SpO₂ jako wsparcie) |

## Struktura

- `config/` — centralna konfiguracja (okna, alphy, progi, marże, zakresy metryk)
- `validators/` — walidacja wejścia (`input.py`) i metryk (`metrics.py`, NaN/inf/zakres)
- `exceptions.py`, `logging.py` — wyjątki domenowe, logowanie strukturalne
- `models.py` — typowane modele danych (Pydantic v2)
- `metrics.py` — centralny rejestr metryk (nazwa/jednostka/normalny zakres → raporty/LLM)
- `baseline.py` — baseline EWMA + trend + detekcja przesunięcia normy
- `acwr.py` — Acute:Chronic Workload Ratio (Gabbett 2016), osobno siła (Hevy) i cardio (Apple)
- `apple_cardio.py` — klasyfikacja sesji cardio z Apple Watch + TRIMP
- `energy_balance.py` — bilans wydatek vs zjedzone kcal (ryzyko urazu/infekcji)
- `temperature.py` — alert temperatury nadgarstka (twardy override) + potwierdzenie SpO₂
- `nutrition_adaptive.py` — cel kaloryczny (TDEE z aktywności + marża wg celu), białko, trend wagi
- `readiness_integration.py` — finalny scoring gotowości + strefa
- `confidence.py` — Confidence Score per metryka (punkty, stabilność, okno)
- `stability.py` — stabilność aktywności (Stable / Moderately / Highly Variable)
- `explain.py` — warstwa wyjaśniająca: `reason[]` per metryka dla LLM
- `pipeline.py` — orkiestracja (5 stage'ów: InputValidation → ModelBuilding → Analytics → Confidence/Explain → Serialization)
- `fetch_*.py` — konwersja danych z MCP na serie analityczne
- `run_analysis.py` — cienki CLI/orchestrator (wejście JSON → wyjście JSON)
- `mcp_fetchers/` — warstwa POBIERANIA z MCP (Hevy/Apple/MFP) + konwersja formatu
  (patrz `mcp_fetchers/README.md` oraz `docs/ARCHITECTURE.md` — mapa warstw)
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
  "apple_workouts": [ /* z apple__list_recent_workouts — cardio z Apple Watch,
     liczone TRIMP w OSOBNYM ACWR; siłowe z Apple są ignorowane (siła = Hevy) */
    {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
     "duration_min": 88.2, "avg_heart_rate_bpm": 143.5}
  ],
  "cardio_sessions": [ /* legacy, ręczne {"startTime","duration_minutes","rpe"} */
    {"startTime": "2026-08-05T08:00:00", "duration_minutes": 90, "rpe": 6}
  ],
  "mfp_daily_kcal": [ /* zjedzone kcal z MFP diary: {day, kcal} — dla energy_balance */
    {"day": "2026-08-06", "kcal": 2603.0}
  ],
  "mfp_weight": [ /* opcjonalne, punkty wagi z MFP — tylko do trendu wagi */
    {"date": "2026-08-07", "value": 71.2}
  ],
  "params": {
    "phase": "utrzymanie",
    "bodyweight_kg": 71.0,
    "tdee_current": 2260,            /* opcjonalne */
    "target_trend_kg_per_week": 0.0  /* opcjonalne */
  }
}
```

> `apple_workouts`: sesje wydolnościowe z Apple Watch (cycling/rowing/walking/running/swimming).
> Liczone jako TRIMP z tętna (automatycznie, bez ręcznego RPE) w **osobnym ACWR cardio**, bo
> skala TRIMP (setki) różni się od tonażu z Hevy (tysiące). Siłowe/kalisteniczne z Apple są
> odrzucane — siła pochodzi wyłącznie z Hevy (zero dubli). Oba ACWR łączone na poziomie
> gotowości zestawem `acwr_combined_modifier` (maksimum stref).
>
> TRIMP: referencją intensywności jest peak HR z danej sesji (`max_heart_rate_bpm`), gdy
> dostępne — pełniejszy obraz pułapu osiągniętego w tej aktywności; NIE jest to fizjologiczne
> HRmax. Opcjonalny dolny limit punktu odniesienia (`ACWR.hr_reference_floor_bpm`, domyślnie
> 170) zabezpiecza lekkie treningi przed zawyżeniem względnej intensywności. `hr_rest` zawsze
> z configu (poranne spoczynkowe).
> Klasyfikacja cardio ma czarną listę siłowych słów kluczowych z pierwszeństwem, więc custom
> nazwa typu "Walking Lunges" nie zostanie błędnie uznana za cardio.

> `phase` = aktualny cel: `utrzymanie` (marża 0) / `redukcja` (−15%) / `masa` (+10%).
> `bodyweight_kg` opcjonalny (gdy brak punktu wagi z Apple w danym dniu).
> `mfp_weight` (opcjonalne) służy **wyłącznie** do `weight_trend` — waga do białka/TDEE
> wzięta jest z punktu kontrolnego `weight_body_mass` z Apple (`apple_daily`).
> `source` musi być `"apple+hevy+mfp"` (jedyna dozwolona wartość).

### Wyjście JSON

Sekcje (z `AnalysisReport`, Pydantic v2): `readiness` (strefa/RPE/objętość),
`acwr` (acute/chronic/ratio/zone), `acwr_detail` (acute_7d, chronic_28d_ewma,
rpe_coverage, cardio + `cardio_7d_sessions`, daily_loads_last14), `temperature`,
`nutrition` (cel kaloryczny z aktywności), `energy_balance` (wydatek vs zjedzone
kcal — ryzyko niedoboru), `baseline_trends` (HRV/RHR trend + R²), `inputs`
(ile punktów użyto) — plus sekcje pomocnicze, obecne gdy dane wystarczają:
- `confidence` — Confidence Score per metryka (hrv/rhr/tdee/acwr): punkty, stabilność, okno
- `weight_trend` — trend wagi (roll. median + slope), tylko gdy podano `mfp_weight`
- `activity_stability` — zbiorcza zmienność aktywności (Stable / Moderately / Highly Variable)
- `explanations` — `reason[]` per metryka (gotowe do interpretacji przez LLM)

`status`: `"ok"` / `"fallback"` (niewystarczające dane — `InsufficientDataError`) /
`"error"` (uszkodzone dane wejściowe lub błąd walidacji).

## Ważne zachowania

- **Brak snu ≠ 0h.** Pominięty składnik snu to nie kara; brak jest jawnie
  sygnalizowany (`sleep_missing` / `inputs.sleep_data: "missing"`). Realnie
  krótki sen (<5.5h = +2, <6.5h = +1) karze normalnie.
- **RPE coverage.** Starsze treningi mogą nie mieć RPE (liczy się sam tonaż),
  co zaniża chronic ACWR i zawyża ratio. **Sprawdzaj `rpe_coverage`** — przy
  <70% traktuj wynik ACWR ze sceptycyzmem.
- **Temperatura** nadgarstka to osobne źródło (`apple__get_data(name=...wrist_temperature)`),
  z poprzedniej nocy; alert ≥0.3°C od baseline — „podwyższona” vs „znacząca” (przy
  jednoczesnym spadku HRV lub odchyleniu ≥0.3°C×multiplier). „Znacząca” → twardy override
  na strefę czerwoną (niezależnie od `total_score`). Dostępna jest też funkcja
  `spo2_confirmation` jako drugi sygnał (spadek SpO₂ ≥2 pkt proc.) — w obecnym pipeline
  przekazywana jest twardo jako `False` (konfiguracja po stronie warstwy zbierającej).
- `run_analysis` wymaga min. 6 punktów HRV do baseline (EWMA).
- **Cel kaloryczny z aktywności** (nie z MFP): TDEE = średnie basal+active z okna
- **Cel kaloryczny z aktywności** (nie z MFP): TDEE = średnie basal+active z okna
  (aktywne 7d, porównawcze 28d) z Apple; cel = TDEE + marża % wg `phase`. MFP
  rejestruje jedzenie, ale nie wyznacza celu.
- **Luki w danych a okno TDEE.** `window_actual_days` raportuje rozpiętość
  kalendarzową objętą oknem (od najstarszego do najnowszego punktu + 1). Gdy Apple
  Health ma dziury w danych, `window_days=7` może oznaczać 7 punktów rozciągniętych
  na więcej dni kalendarzowych — sprawdzaj `window_actual_days > window_days` i
  traktuj średnią jako mniej reprezentatywną dla „tygodnia" w takim przypadku
  (nie zgadujemy, czy dziura to celowy post, czy brak zapisu — tylko jawnie
  sygnalizujemy rozjazd).
- **Waga — dwa niezależne źródła.** Punkt kontrolny z Apple (`weight_body_mass`, w dni
  ważenia) służy do białka i TDEE; osobno `mfp_weight` (opcjonalne) służy wyłącznie do
  `weight_trend` (rolling median + slope). Bez wagi z Apple cel kaloryczny i tak działa
  (z samej aktywności); bez `mfp_weight` po prostu nie ma sekcji `weight_trend`.
- **Cardio ACWR** przy nieregularnym cardio jest oznaczany „niewystarczające dane"
  (za mało dni w chronic), a realną karę gotowości niesie `cardio_7d_sessions`
  (ile mocnych sesji w tygodniu). Nie interpretuj ratio cardio jako ryzyka,
  gdy strefa to „niewystarczające dane".
- **Bilans energetyczny** (`energy_balance`): bez danych MFP (zjedzone kcal)
  sekcja zwraca „niewystarczające dane" — nie raportuj ryzyka niedoboru bez
  realnych kcal. Przy min. 3 ważnych dniach w oknie 7d liczy pokrycie wydatku
  i skumulowany niedobór (próg ryzyka w `config.ENERGY_BALANCE`).

## Jakość / CI

- **319 testów** (jednostkowe + integracyjne) — `pytest`
- Coverage `analytics/`: **~90%** (line) — `pytest --cov=analytics`
- Coverage `mcp_fetchers/`: częściowy — testowane są `mfp_normalize` (63%)
  i `apple_normalize` (53%); `fetch_mcp.py` / `build_input.py` / `hevy_normalize.py`
  nie mają jeszcze skryptów testowych w `tests/` (to warstwa pobierania z MCP —
  offline testowalna, ale wymaga mockowania transportu). Wyraźnie odnotowane,
  żeby liczba nie wyglądała na niedopilnowaną.
- Ruff + mypy czyste; GitHub Actions: Ruff i mypy na 3.12, pytest+coverage na 3.10/3.12/3.13
