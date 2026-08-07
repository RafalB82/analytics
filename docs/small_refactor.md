# small_refactor — rozdzielenie sygnałów aktywności (trening vs ruch vs active_energy)

**Status:** propozycja — wrócimy do wdrożenia później (2026-08-07).
**Dotyczy:** pakiet `analytics` (moduł `nutrition_adaptive.py` + `run_analysis.py`).

---

## Problem

W obecnym modelu TDEE wszystkie sygnały aktywności traktowane są jako jeden worek:

```
TDEE = basal_energy_burned + active_energy
```

A `active_energy` (energia spalona ponad spoczynkową w ciągu dnia) to **suma wszystkiego**:
treningu + kroków + NEAT + codziennego ruchu. Nie rozróżnia, ile z niej to trening, a ile
ruch. Tymczasem Apple dostarcza osobne sygnały, które są rozłączne pod względem sensu:

| Sygnał | Co realnie mierzy | Charakterystyka (na danych 08-01..08-07) |
|---|---|---|
| `active_energy` | Całkowita energia > spoczynkowa w całym dniu | worek: trening + kroki + NEAT. Np. 08-05: trening (19-21h) to ~73% całości, reszta ~27% |
| `apple_exercise_time` | Minuty, gdy watch uznał intensywność za „ćwiczenie" | Tylko trening. NIE proporcjonalne do active_energy (08-01: 173 min→1598 kcal, ale 08-03: 191 min→673 kcal) |
| `apple_stand_time` | Minuty w godzinach z ruchem (przerwanie siedzenia) | Codzienny ruch/NEAT, nie trening. Nie koreluje z active_energy |
| `physical_effort` | kcal/hr·kg (intensywność względna) | kontekst, nie energia absolutna |

Kluczowa obserwacja: **`active_energy` nie pozwala wyodrębnić „samego treningu"** — to suma.
Natomiast `exercise_time` / `stand_time` mówią, ile minut było treningu vs ruchu.

## Cel

Rozdzielić sygnały tak, żeby:
- **TDEE / cel kaloryczny** opierał się na całkowitym wydatku (basal + active) — to zostaje,
- ale **aktualna struktura aktywności** (ile treningu, ile ruchu, ile energii z treningu)
  była widoczna jako osobny, czytelny blok — żeby nie mieszać treningu i dnia.

## Propozycja rozdzielenia (2 warianty)

### Wariant A — rozbicie informacyjne (raport, bez zmiany logiki TDEE) — rekomendowany na start

Nie zmienia algorytmu TDEE. Dodaje do outputu `nutrition.activity` pełne rozbicie:

```
activity:
  exercise_min: 352            # minuty treningu (apple_exercise_time)
  stand_min: 407               # minuty ruchu/NEAT (apple_stand_time)
  active_energy_kcal: 1209     # całość (basal+active już w tdee)
  est_training_energy_kcal: ~890   # oszacowanie energii z treningu (profil godzinowy)
  est_neat_energy_kcal: ~319   # reszta (kroki/codzienny ruch)
  training_share_pct: 73       # ile % active_energy to trening
```

**Jak estymować `est_training_energy_kcal` (deterministycznie, bez LLM):**
- zidentyfikować przedziały godzinowe, gdzie `active_energy` jest wyraźnie powyżej
  bazowego tła dnia (np. > 2–3× mediany godzinowej) — to kandydat na trening;
- zsumować `active_energy` w tych godzinach → `est_training_energy`;
- reszta = `active_energy - est_training_energy` → `est_neat`.

Jest to estymacja heurystyczna, nie pomiar — ale deterministyczna i wystarczająca
do rozbicia „trening vs ruch" w raporcie/LLM, bez modelowania intensity.

Zalety: niskie ryzyko, nie zmienia celu kalorycznego, daje LLM-owi pełny kontekst.
Wady: `est_training_energy` to szacunek (pewna niedokładność na granicy progów).

### Wariant B — zmiana logiki TDEE (nie rekomendowany na start)

Gdyby chcieć, by cel kaloryczny różnicował „dzień treningowy" vs „dzień ruchowy":
- n.p. wyliczać TDEE osobno dla dni treningowych (basal + trening + NEAT)
  vs spoczynkowych;
- wymaga modelu przypisania energii do źródła — bardziej złożone i ryzykowne
  (heurystyka w sercu celu kalorycznego).

Odrzucamy na start: cel kaloryczny powinien opierać się na **całkowitym,
zmierzonym wydatku**, a nie na estymowanym rozbiciu.

## Decyzje do potwierdzenia przy wdrożeniu

1. Wariant **A** (informacyjny) czy **B** (zmiana TDEE)? — rekomendacja: A.
2. Próg wykrywania treningu (godzinowy): ile× mediany godzinowej uznajemy za trening?
   (propozycja: `>= 2.0x` mediany godzinowej dnia; konfigurowalne w `NutritionSettings`).
3. Czy `training_share_pct` ma wejść do rekomendacji (RPE/objętość) w readiness,
   czy tylko do raportu `nutrition.activity`? (propozycja: na start tylko raport).

## Pliki do zmiany (przy wdrożeniu)

- `analytics/nutrition_adaptive.py` — dodać `estimate_training_vs_neat()` + pola
  `est_training_energy_kcal` / `est_neat_energy_kcal` / `training_share_pct` w `TDEEEstimate`.
- `analytics/fetch_apple.py` — w `to_energy_series` zagregować aktywność godzinowo
  (jeśli PREFIX zamiast dziennego) — wymaga dostępu do profili, nie tylko sum dnia.
  Uwaga: dziś `get_daily_activity_range` zwraca SUMY dzienne; profil godzinowy bierze
  się z `get_daily_series(day, 'active_energy', bucket='hour')`. Trzeba zdecydować,
  czy liczyć estymację w `run_analysis` (woła godzinowy profil) czy w fetch.
- `analytics/run_analysis.py` — `_compute_goal` → dodać `est_training/neat` do sekcji
  `nutrition.activity`.
- `config/settings.py` — `NutritionSettings`: próg wykrywania treningu.
- `tests/` — deterministyczne testy heurystyki (syntetyczny profil godzinowy z
  wyraźnym pikiem treningowym vs tło).
- `docs/` — dopisać do `Refactoring.md` po wdrożeniu.

## Ryzyka / uwagi

- `apple_exercise_time` (min treningu) nie jest proporcjonalne do energii treningu —
  watch liczy minuty intensywności, nie kcal. Stąd potrzeba estymacji z profilu
  godzinowego `active_energy`, a nie mnożenia minut przez stałą.
- `stand_time` to godziny-ruchu, nie energia — używane informacyjnie.
- Rozbicie jest **estymacją**; nie powinno sterować twardymi decyzjami (strefa),
  tylko kontekstem dla LLM i ewentualnie miękkimi rekomendacjami.
