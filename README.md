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
| Waga (opcjonalnie, TDEE) | MFP MCP | `fetch_mfp.py` (z `mfp_get_measurements`) |

**Świadomie pominięte:** wellness-project, Garmin.

> Uwaga: starsza wersja README odsyłała do `mcp_tool.py mfp measurements` —
> nieaktualne. Waga pobierana jest przez MFP MCP, ale u Rafała MFP **nie
> przechowuje wagi** (pusty `measurements`), więc korekta TDEE i tak jest
> pomijana — patrz sekcja ograniczeń.

## Struktura
- `baseline.py` — rolowany baseline (EWMA) + trend + detekcja przesunięcia normy
- `acwr.py` — Acute:Chronic Workload Ratio (Gabbett 2016), sRPE-load = tonaż × RPE
- `temperature.py` — temp. nadgarstka jako twardy override (obecnie brak danych z Apple → no_data)
- `nutrition_adaptive.py` — korekta TDEE z trendu wagi + białko wg fazy
- `readiness_integration.py` — spina wszystko w finalny scoring + strefę
- `fetch_apple.py` / `fetch_hevy.py` / `fetch_mfp.py` — konwersja danych MCP → moduły
- `run_analysis.py` — **ręczny orchestrator** (wejście JSON, wyjście JSON)
- `demo_full.py` — powtarzalny test na danych syntetycznych zbliżonych do realnych

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
`baseline_trends` (HRV/RHR trend + R²), `inputs` (liczba punktów użytych w analizie).

## WAŻNE — RPE w danych Hevy (odczytana rzeczywistość)
- Treningi **od ~2026-08** mają logowane `rpe` per seria (np. 8, 8.5, 9.5, 10).
- Treningi **starsze (czerwiec/lipiec)** mają `rpe: null` — liczy się sam tonaż.
- Konsekwencja dla ACWR: chronic (28d EWMA) liczony na mieszance z/bez RPE będzie
  **zaniżony wobec acute**, co może zawyżać ratio → fałszywe "wysokie ryzyko".
  (Na danych demo treningowych 69% pokrycia daje ratio ≈ 3.3 — realnie
  nieufaj takiemu wynikowi.)
- Obowiązkowo sprawdzaj `acwr_detail.rpe_coverage` — jeśli <70%, traktuj wynik
  ACWR ze sceptycyzmem i nie podejmuj twardych decyzji na bazie samego ratio.

## Znane ograniczenia
- **Waga**: MFP nie przechowuje wagi Rafała (pusty `measurements`) —
  korekta TDEE pomijana (`skipped`). Gdy pojawią się dane, zadziała automatycznie.
- `run_analysis` wymaga min. 6 punktów HRV historycznych (baseline EWMA).
- **Brak danych o śnie** NIE jest traktowany jak 0h — składnik snu jest wtedy
  pomijany w scoringu (bez kary), a fakt braku sygnalizuje `readiness.sleep_missing`
  oraz `inputs.sleep_data: "missing"`. To celowe: brak pomiaru to nie to samo co
  zero snu, a niezauważony brak zaniżałby ocenę regeneracji. Jeśli sen jest realnie
  0 lub bardzo krótki, kara działa normalnie (<5.5h = +2, <6.5h = +1).

## Temperatura nadgarstka (AKTYWNA od 2026-08-07)
- Źródło: `apple__get_data(name='apple_sleeping_wrist_temperature')` — **osobne
  wywołanie MCP**, bo seria dzienna `get_daily_activity_range` NIE zawiera temperatury.
- Apple zwraca **bezwzględną** temperaturę (~35.7-36.2°C); baseline = średnia
  (okno 14d), alert przy odchyleniu ≥0.3°C; severity 'znacząca' ≥0.45°C
  lub przy jednoczesnym spadku HRV → twardy override na strefę CZERWONĄ.
- Temperatura jest zawsze z poprzedniej nocy (na dziś brak punktu) — bierz
  ostatni dostępny punkt.
- Input JSON: `"apple_temp": [ {"date": "2026-08-06", "value": 35.98} ]`.

> Uwaga implementacyjna: `temperature.TempPoint.wrist_temp_c` jest w docstringu
> opisane jako "odchylenie", ale w tym pakiecie faktycznie trzyma **bezwzględną**
> temperaturę, a `run_analysis` liczy odchylenie od średniej baseline. To zamierzone
> dla danych Apple. Jeśli kiedyś przejdziesz na odchylenia z HealthKit, zmień tryb
> w `compute_temp_baseline`.

## Testy
```bash
# importy (wymaga katalogu o nazwie `analytics` w PYTHONPATH / cwd):
PYTHONPATH=. python3 -c "from analytics import baseline, acwr, temperature, nutrition_adaptive, readiness_integration; print('imports OK')"

# pełny flow na danych syntetycznych (bez MCP):
python3 -m analytics.demo_full

# pełny flow na realnej próbce:
python3 -m analytics.run_analysis "$(cat /tmp/input_test.json)"
```
Weryfikacja sanity ACWR i sRPE-load znajduje się w historii sesji (commit tego pliku).

## Audyt kodu (2026-08-07)
Stan: **działa end-to-end** — wszystkie moduły importują się, kompilują
(`py_compile`) i poprawnie przechodzą demo + obsługę błędów (invalid source,
missing apple, fallback na za mało HRV). Kod jest deterministyczny i testowalny
offline (żaden moduł nie woła MCP bezpośrednio — dane wstrzykuje agent).

### Znalezione problemy (bez zmian behawioralnych w tym commicie)
1. **Metodologia ACWR mieszana** — `compute_acute_load` używa zwykłej średniej
   (7d), a `compute_chronic_load` EWMA (28d, alpha=0.05). To celowe żeby uniknąć
   skoków przy wypadaniu dnia z okna, ale pola w `ACWRResult` opisują oba jako
   "średnią dzienną" — ujednolić nazewnictwo (np. `acute_mean` vs `chronic_ewma`)
   albo udokumentować różnicę wprost w polach.
2. **RPE coverage < 70% fałszuje ratio** — patrz sekcja wyżej. To największe
   ryzyko praktyczne; nie podejmuj decyzji o strefie na samym ACWR przy niskim
   pokryciu RPE.
3. ~~Brak snu = kara +2~~ **WDROŻONE** — składnik snu jest teraz pomijany gdy
   brak danych, a brak jest jawnie sygnalizowany (`sleep_missing` /
   `sleep_data: "missing"`); realnie krótki sen nadal karze normalnie.
4. **`fetch_hevy._parse_date`** — puste / `null` `startTime` zwraca `date.today()`
   (może przypiąć trening do złego dnia). Obecnie nieużywane w tym scenariuszu,
   ale potencjalna pułapka przy niespójnych danych Hevy.
5. **Martwa stała `DISTANCE_BASED = False`** w `fetch_hevy.py` — nieużywana,
   do usunięcia lub podpięcia pod logikę dystansową (Farmers Walk).
6. **Niespójność docstringa temperatury** — patrz uwaga w sekcji temperatury
   (bezwzględna vs odchylenie).

### Rekomendacje (kolejność wg wagi)
- [ ] Przejść na jednolite pokrycie RPE w oknie 28d przed zaufaniem ACWR
      (backfill RPE dla starych treningów albo zawężenie okna chronic).
      *(TODO — najważniejsze, wymaga danych)*
- [x] Nie karać za brak snu (pkt 3) — **WDROŻONE 2026-08-07**.
- [ ] Ujednolicić nazwy pól acute/chronic (pkt 1) — kosmetyka, ale ułatwia
      interpretację outputu.
- [ ] Usunąć `DISTANCE_BASED` i albo obsłużyć serie dystansowe, albo wywalić.
- [ ] Po tych poprawkach rozważyć przejście z ręcznego na cron (08:30, slot "Gotowość").
