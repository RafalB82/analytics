# analytics/ — Rozszerzona analiza gotowości (wdrożenie RÓWNOLEGŁE)

Pakiet z pliku `files4.zip` (Health Auto Export REST API repo), zaadaptowany
do środowiska OpenClaw. **Nic, co już działa, nie zastępuje** — istniejący
`readiness_apple.py` (prosty scoring HRV/RHR/sen) pozostaje nienaruszony i
współistnieje z tym pakietem. Ten pakiet dostarcza pełniejszą, deterministyczną
analizę i jest **wywoływany ręcznie** (po testach może przejść do cron).

## Źródła danych (WYŁĄCZNIE)
| Obszar | Źródło | Metoda MCP |
|---|---|---|
| HRV, RHR, sen | Apple MCP | `apple__get_daily_activity_range` |
| Tonaż + RPE (ACWR) | Hevy MCP | `hevy__get-workouts` |
| Waga (opcjonalnie, TDEE) | MFP | `python3 mcp_tool.py mfp measurements` |

**Świadomie pominięte:** wellness-project, Garmin.

## Struktura
- `baseline.py` — rolowany baseline (EWMA) + trend + detekcja przesunięcia normy
- `acwr.py` — Acute:Chronic Workload Ratio (Gabbett 2016), sRPE-load = tonaż × RPE
- `temperature.py` — temp. nadgarstka jako twardy override (obecnie brak danych z Apple → no_data)
- `nutrition_adaptive.py` — korekta TDEE z trendu wagi + białko wg fazy
- `readiness_integration.py` — spina wszystko w finalny scoring + strefę
- `fetch_apple.py` / `fetch_hevy.py` / `fetch_mfp.py` — konwersja danych MCP → moduły
- `run_analysis.py` — **ręczny orchestrator** (wejście JSON, wyjście JSON)

## Uruchomienie (ręczne)
Agent (ten MCP) musi najpierw zebrać dane, a następnie przekazać je do skryptu:

```bash
python3 -m analytics.run_analysis '<json>'
```

### Schemat wejścia JSON
```json
{
  "source": "apple+hevy+mfp",
  "target_date": "2026-08-07",
  "apple_daily": [
    {"date": "2026-08-07", "resting_heart_rate": 53.0,
     "heart_rate_variability": 44.33, "sleep": {"total_hours": 6.43}}
  ],
  "apple_temp": [ {"date": "2026-08-06", "value": 35.98} ],  // opcjonalne
  "hevy_workouts": [ /* z hevy__get-workouts, strony sklejone */ ],
  "mfp_weight": [ {"date": "2026-08-01", "value": 69.9} ],   // null = pominąć
  "params": {
    "tdee_current": 2260,
    "phase": "utrzymanie",
    "bodyweight_kg": 69.9,
    "target_trend_kg_per_week": 0.0
  }
}
```

### Wyjście JSON
Sekcje: `readiness` (strefa/RPE/objętość), `acwr` (acute/chronic/ratio/zone),
`acwr_detail.rpe_coverage` (ważne!), `temperature`, `tdee_adaptive`,
`baseline_trends` (HRV/RHR trend + R²).

## WAŻNE — RPE w danych Hevy (odczytana rzeczywistość)
- Treningi **od ~2026-08** mają logowane `rpe` per seria (np. 8, 8.5, 9.5, 10).
- Treningi **starsze (czerwiec/lipiec)** mają `rpe: null` — liczy się sam tonaż.
- Konsekwencja dla ACWR: chronic (28d EWMA) liczony na mieszance z/bez RPE będzie
  **zaniżony wobec acute**, co może zawyżać ratio → fałszywe "wysokie ryzyko".
- Obowiązkowo sprawdzaj `acwr_detail.rpe_coverage` — jeśli <70%, traktuj wynik
  ACWR ze sceptycyzmem i nie podejmuj twardych decyzji na bazie samego ratio.

## Znane ograniczenia
- **Waga**: MFP nie przechowuje wagi Rafała (pusty `measurements`) —
  korekta TDEE pomijana (`skipped`). Gdy pojawią się dane, zadziała automatycznie.
- `run_analysis` wymaga min. 6 punktów HRV historycznych (baseline EWMA).

## Temperatura nadgarstka (AKTYWNA od 2026-08-07)
- Źródło: `apple__get_data(name='apple_sleeping_wrist_temperature')` — **osobne
  wywołanie MCP**, bo seria dzienna `get_daily_activity_range` NIE zawiera temperatury.
- Apple zwraca **bezwzględną** temperaturę (~35.7-36.2°C); baseline = średnia
  (okno 14d), alert przy odchyleniu ≥0.3°C; severity 'znacząca' ≥0.45°C
  lub przy jednoczesnym spadku HRV → twardy override na strefę CZERWONĄ.
- Temperatura jest zawsze z poprzedniej nocy (na dziś brak punktu) — bierz
  ostatni dostępny punkt.
- Input JSON: `"apple_temp": [ {"date": "2026-08-06", "value": 35.98} ]`.

## Testy
```bash
python3 -c "from analytics import baseline, acwr, temperature, nutrition_adaptive, readiness_integration; print('imports OK')"
# full flow na realnej próbce:
python3 -m analytics.run_analysis "$(cat /tmp/input_test.json)"
```
Weryfikacja sanity ACWR i sRPE-load znajduje się w historii sesji (commit tego pliku).
