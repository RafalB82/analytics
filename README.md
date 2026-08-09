
# Analytics

Deterministyczny silnik analizy obciążenia treningowego, regeneracji i zapotrzebowania energetycznego.

Projekt został zaprojektowany do analizy osoby trenującej przede wszystkim **siłowo**, ale jednocześnie wykonującej **nieregularny trening wydolnościowy** oraz mającej zmienne obciążenie wynikające z codziennej aktywności.

System łączy dane z:

* Apple Health / Apple Watch
* Hevy
* MyFitnessPal

i przekształca je w jeden deterministyczny raport JSON.

## Główne cele

Projekt odpowiada na cztery podstawowe pytania:

1. **Jak duże jest aktualne obciążenie treningowe?**
2. **Czy organizm wykazuje oznaki pogorszonej regeneracji?**
3. **Jak wiarygodna jest aktualna ocena?**
4. **Ile energii należy dostarczyć przy aktualnym poziomie aktywności i celu sylwetkowym?**

Najważniejszą zasadą projektu jest rozdzielenie:

```text
LOAD       = ile bodźca treningowego otrzymał organizm
RECOVERY   = jak organizm reaguje na ten bodziec
DATA       = jak bardzo można ufać ocenie
NUTRITION  = ile energii potrzeba przy aktualnej aktywności
```

Dzięki temu wysoki poziom treningu nie jest automatycznie traktowany jako przemęczenie.

---

# Architektura

```text
                         ┌─────────────────────┐
                         │    Apple Health     │
                         │                     │
                         │ HRV                 │
                         │ RHR                 │
                         │ Sleep               │
                         │ Activity            │
                         │ Energy              │
                         │ Weight              │
                         │ Temperature         │
                         │ Apple Watch Cardio  │
                         └──────────┬──────────┘
                                    │
                                    ▼
┌──────────────┐           ┌───────────────────┐
│    Hevy      │──────────►│  Input / Models   │
│              │           │   Validation      │
│ Sets         │           └─────────┬─────────┘
│ Reps         │                     │
│ Weight       │                     ▼
│ RPE          │           ┌───────────────────┐
└──────────────┘           │     Analytics     │
                           │                   │
┌──────────────┐           │ Baseline          │
│ MyFitnessPal │──────────►│ Recovery          │
│              │           │ Strength Load     │
│ Calories     │           │ Cardio Load       │
└──────────────┘           │ Nutrition         │
                           │ Confidence        │
                           └─────────┬─────────┘
                                     │
                                     ▼
                           ┌───────────────────┐
                           │  Analysis Report  │
                           │       JSON        │
                           └─────────┬─────────┘
                                     │
                                     ▼
                           ┌───────────────────┐
                           │       LLM         │
                           │   interpretacja   │
                           └───────────────────┘
```

LLM nie wykonuje obliczeń analitycznych.

Otrzymuje gotowy raport i może:

* wyjaśnić wynik,
* przedstawić najważniejsze sygnały,
* sformułować rekomendację,
* wskazać ograniczenia jakości danych.

Obliczenia pozostają po stronie deterministycznego kodu.

---

# Źródła danych

## Apple Health / Apple Watch

Apple Health jest głównym źródłem danych fizjologicznych, aktywności oraz **referencyjnej masy ciała**.

Wykorzystywane dane:

| Dane                   | Zastosowanie                                              |
| ---------------------- | --------------------------------------------------------- |
| HRV                    | ocena regeneracji i baseline                              |
| RHR                    | ocena regeneracji i trend                                 |
| Sen                    | readiness                                                 |
| Basal Energy           | estymacja TDEE                                            |
| Active Energy          | estymacja TDEE                                            |
| Exercise Time          | charakterystyka aktywności                                |
| Stand Time             | charakterystyka aktywności                                |
| Physical Effort        | charakterystyka aktywności                                |
| **Masa ciała**         | **referencyjna masa ciała, trend, obliczenia żywieniowe** |
| Temperatura nadgarstka | dodatkowy sygnał recovery                                 |
| SpO₂                   | pomocnicze potwierdzenie alertu                           |
| Cardio workouts        | obciążenie wydolnościowe                                  |

Apple Watch dostarcza również dane treningowe dla aktywności takich jak:

* cycling,
* running,
* walking,
* rowing,
* swimming,
* hiking,
* elliptical,
* stair climbing.

### Masa ciała

Masa ciała wykorzystywana przez Analytics pochodzi z Apple Health.

Docelowy przepływ danych:

```text
Renpho
  │
  │ pomiar masy + bioimpedancja
  ▼
Apple Health / HealthKit
  │
  │ canonical body weight
  ▼
Analytics
```

Renpho będzie źródłem automatycznych pomiarów masy ciała, które po synchronizacji z Apple Health będą dostępne dla Analytics.

Dzięki temu projekt nie wymaga ręcznego przepisywania aktualnej masy ciała do systemu analitycznego.

---

# Hevy

Hevy jest źródłem danych o treningu siłowym.

Dla każdej serii obciążenie jest określane jako:

```text
load = sets × reps × weight × RPE
```

Jeżeli RPE nie jest dostępne:

```text
load = sets × reps × weight
```

Brak RPE nie powoduje odrzucenia treningu, ale obniża wiarygodność oceny obciążenia.

System raportuje `rpe_coverage`, dzięki czemu można stwierdzić, jaka część danych treningowych posiada RPE.

---

# MyFitnessPal

MyFitnessPal jest źródłem **rzeczywiście spożytej energii**.

W Analytics dane z MyFitnessPal są wykorzystywane przede wszystkim jako:

```text
calories consumed
```

czyli rzeczywista liczba dostarczonych kcal wynikająca z prowadzonego dziennika żywieniowego.

### Masa ciała w MyFitnessPal

MyFitnessPal może przechowywać informacje o masie ciała, jednak **nie jest źródłem referencyjnym masy ciała dla tego projektu**.

Masa ciała używana przez Analytics pochodzi z:

```text
Renpho
   ↓
Apple Health / HealthKit
   ↓
Analytics
```

MFP i Apple Health mają więc różne role:

```text
Apple Health
    └── masa ciała → źródło referencyjne

MyFitnessPal
    └── kcal       → rzeczywiste spożycie energii
```

Dane nie są wzajemnie zastępowane ani mieszane.

---

# Bilans energetyczny

Rozdzielenie źródeł pozwala analizować zarówno **wydatek energetyczny**, jak i **spożycie energii**.

```text
Apple Health
    │
    ├── Basal Energy
    └── Active Energy
             │
             ▼
      estimated TDEE
             │
             │
MyFitnessPal │
    │        │
    └── kcal ┘
         │
         ▼
   Energy Balance
```

W uproszczeniu:

```text
energy balance =
calories consumed - estimated energy expenditure
```

gdzie:

```text
calories consumed
    = MyFitnessPal

estimated expenditure
    = Apple Health
```

Masa ciała z Apple Health jest niezależnym sygnałem zwrotnym pozwalającym oceniać długoterminową odpowiedź organizmu na obserwowany bilans energetyczny.

Docelowo system może analizować:

```text
deklarowane spożycie kcal
        vs
szacowany wydatek energetyczny
        vs
rzeczywisty trend masy ciała
```

Daje to możliwość późniejszej kalibracji estymacji TDEE na podstawie rzeczywistych zmian masy ciała.

---

# Model obciążenia treningowego

## Trening siłowy

Trening siłowy jest analizowany niezależnie od cardio.

Dzienny load jest sumą obciążeń wszystkich treningów siłowych wykonanych danego dnia.

Następnie obliczane są:

```text
acute load   = średnie obciążenie z ostatnich 7 dni
chronic load = EWMA z ostatnich 28 dni
ACWR         = acute / chronic
```

Dni bez treningu są reprezentowane jako `0`.

Dzięki temu system analizuje nie tylko liczbę treningów, ale również ich rozmieszczenie w czasie.

---

# Cardio

Cardio jest analizowane osobno od treningu siłowego.

Powodem jest różnica jednostek:

```text
siła   → tonaż / sRPE-load
cardio → TRIMP
```

Nie są one sumowane w jeden fizyczny wskaźnik obciążenia.

Cardio otrzymuje własny ACWR:

```text
acute cardio
chronic cardio
cardio ACWR
```

Następnie wpływ siły i cardio jest łączony dopiero na poziomie decyzji o aktualnym obciążeniu.

## Dlaczego?

Przykład:

```text
trening siłowy:
ACWR = 0.95

cardio:
ACWR = 1.65
```

Nie należy tworzyć:

```text
0.95 + 1.65
```

Ponieważ są to różne skale.

System traktuje cardio jako niezależny komponent obciążenia.

---

# Nieregularny trening wydolnościowy

Projekt jest przeznaczony również do sytuacji, w których cardio nie występuje regularnie.

Przykład:

```text
Pon  brak
Wt   brak
Śr   siła
Czw  brak
Pt   brak
Sob  3 h jazdy
Nd   odpoczynek
```

W takim przypadku sam ACWR cardio może być niewystarczający do opisania aktualnego obciążenia.

Dlatego system wykorzystuje również bezpośredni sygnał liczby mocnych sesji cardio w ostatnich 7 dniach.

Pozwala to wykryć sytuację:

```text
niska regularność
+
nagły duży bodziec
=
potencjalny spike obciążenia
```

Jest to szczególnie istotne przy nieregularnych jazdach rowerowych i innych długich sesjach wydolnościowych.

---

# Regeneracja

Regeneracja jest analizowana niezależnie od obciążenia treningowego.

Główne sygnały:

```text
HRV deviation
RHR deviation
sleep duration
RHR trend
wrist temperature
```

## HRV

Dla HRV tworzony jest osobisty baseline EWMA.

Bieżący dzień nie jest używany do budowania baseline'u.

Następnie obliczane jest:

```text
HRV deviation =
(current HRV - baseline HRV) / baseline HRV
```

Wynik jest wyrażany procentowo.

Przykład:

```text
baseline HRV = 50 ms
current HRV  = 42 ms

deviation = -16%
```

---

# RHR

RHR jest analizowane analogicznie, ale interpretacja jest odwrotna.

Wzrost RHR względem baseline może wskazywać na pogorszenie regeneracji.

System dodatkowo analizuje trend RHR.

Trend jest wykorzystywany tylko wtedy, gdy jest wystarczająco wiarygodny statystycznie.

Wzrost RHR nie jest traktowany jako diagnoza przeciążenia lub choroby.

Może być sygnałem związanym między innymi z:

* gorszym snem,
* stresem,
* odwodnieniem,
* alkoholem,
* infekcją,
* zmianą pory pomiaru.

---

# Sen

Sen jest osobnym składnikiem recovery.

Obecna logika:

```text
< 5.5 h  → +2 punkty
< 6.5 h  → +1 punkt
>= 6.5 h → 0 punktów
```

Brak danych o śnie nie oznacza:

```text
sleep = 0
```

Zamiast tego:

```text
sleep_missing = true
```

i obniżana jest jakość danych.

To rozróżnienie jest istotne.

---

# Temperatura nadgarstka

Temperatura nadgarstka jest traktowana jako niezależny sygnał.

Standardowy alert:

```text
deviation >= 0.3°C
```

Przy większym odchyleniu lub jednoczesnym spadku HRV alert może zostać sklasyfikowany jako `significant`.

Znaczący alert temperatury może uruchomić twardy override:

```text
verdict = red
```

niezależnie od podstawowego score.

Temperatura nie jest jednak diagnozą medyczną.

Jest sygnałem ostrzegawczym, który powinien być interpretowany w kontekście pozostałych danych.

---

# Readiness

Readiness nie jest pojedynczą metryką fizjologiczną.

Jest końcową oceną wynikającą z kilku niezależnych osi.

## RECOVERY

Opisuje oznaki pogorszenia regeneracji:

```text
ok
degraded
critical
```

## LOAD

Opisuje poziom aktualnego obciążenia:

```text
low
moderate
high
very_high
```

## DATA QUALITY

Opisuje wiarygodność wyniku:

```text
high
medium
low
```

Uwzględniane są między innymi:

* brak snu,
* niewystarczające dane cardio,
* niski poziom RPE coverage.

---

# Końcowy werdykt

Najważniejszym wynikiem analizy jest:

```text
verdict.zone
```

Możliwe wartości:

```text
green
orange
red
inconclusive
```

Logika jest następująca:

### Green

Organizm nie pokazuje istotnych oznak pogorszenia regeneracji.

Może występować zarówno niski, jak i wysoki load.

Wysoki load sam w sobie nie oznacza czerwonej strefy.

### Orange

Wysoki load występuje razem z oznakami pogorszonej regeneracji.

Zalecane jest ograniczenie objętości i kontrola intensywności.

### Red

Wysoki load występuje razem z silnymi oznakami pogorszonej regeneracji.

Możliwy jest również bezpośredni override przez znaczący sygnał temperatury.

### Inconclusive

Dane są niewystarczające do wiarygodnego określenia gotowości.

---

# Przykładowa interpretacja

System może znaleźć sytuację:

```text
LOAD:
high

RECOVERY:
ok

DATA QUALITY:
high
```

Wynik:

```text
GREEN
```

Interpretacja:

> Obciążenie jest wysokie, ale obecne dane fizjologiczne nie wskazują na pogorszenie regeneracji.

Inny przypadek:

```text
LOAD:
high

RECOVERY:
degraded

DATA QUALITY:
high
```

Wynik:

```text
ORANGE
```

Interpretacja:

> Aktualne obciążenie jest wysokie i pojawiają się oznaki pogorszenia regeneracji. Warto ograniczyć objętość treningową.

Jeszcze inny:

```text
LOAD:
very_high

RECOVERY:
critical
```

Wynik:

```text
RED
```

Interpretacja:

> Wysokie obciążenie występuje jednocześnie z silnymi sygnałami pogorszenia regeneracji.

---

# Zapotrzebowanie kaloryczne

Cel kaloryczny jest wyliczany na podstawie rzeczywistej aktywności.

Podstawowy model:

```text
estimated TDEE = average(basal_energy + active_energy)
```

Domyślne aktywne okno:

```text
7 dni
```

Dodatkowo system może obliczyć:

```text
28 dni
```

jako stabilniejsze odniesienie długoterminowe.

## Cele

### Utrzymanie

```text
target = TDEE
```

### Redukcja

```text
target = TDEE × 0.85
```

### Masa

```text
target = TDEE × 1.10
```

Marże są centralnie konfigurowalne.

---

# Dlaczego aktywność zamiast sztywnego współczynnika?

Użytkownik może mieć bardzo różne tygodnie:

```text
Tydzień A:
3 treningi siłowe
2 h roweru

Tydzień B:
2 treningi siłowe
8 h roweru

Tydzień C:
4 treningi siłowe
brak cardio
```

Stały współczynnik aktywności nie reaguje dobrze na takie zmiany.

Model wykorzystujący rzeczywiste dane aktywności może dostosować estymację wydatku energetycznego do aktualnego poziomu aktywności.

---

# Bilans energetyczny i sprzężenie zwrotne

Jednym z docelowych zastosowań projektu jest porównanie trzech niezależnych informacji:

```text
1. Ile energii zostało spożyte?
   → MyFitnessPal

2. Ile energii prawdopodobnie zostało wydatkowane?
   → Apple Health

3. Jak zmieniała się masa ciała?
   → Apple Health / Renpho
```

Pozwala to stworzyć długoterminowe sprzężenie zwrotne:

```text
              ┌─────────────────┐
              │  Apple Health   │
              │                 │
              │ Basal + Active │
              └────────┬────────┘
                       │
                       ▼
                estimated TDEE
                       │
                       │
                       ▼
MyFitnessPal ──► Energy Balance
  calories            │
  consumed             │
                       ▼
                 Weight Trend
                       │
                       │
                       └──────────────► TDEE calibration
```

W przyszłości może to pozwolić na adaptację estymacji TDEE na podstawie rzeczywistej odpowiedzi masy ciała.

Nie oznacza to, że pojedynczy pomiar masy może służyć do kalibracji. Analiza wymaga odpowiednio długiego i jakościowego trendu.

---

# Białko

Cel białkowy jest wyliczany z aktualnej masy ciała pochodzącej z Apple Health.

Domyślne wartości:

```text
redukcja   → 2.2 g/kg
utrzymanie → 1.8 g/kg
masa       → 1.8 g/kg
```

Przykład:

```text
70 kg × 2.2 g/kg = 154 g
```

Masa wykorzystywana do tego obliczenia pochodzi z Apple Health, a nie z MFP.

---

# Trend masy ciała

Trend masy ciała jest analizowany na podstawie danych z Apple Health.

Źródłem pomiarów jest docelowo:

```text
Renpho
   ↓
Apple Health / HealthKit
   ↓
Analytics
```

System wykorzystuje:

* rolling median,
* regresję liniową,
* slope kg/dzień,
* slope kg/tydzień.

Przykładowy wynik:

```json
{
  "weekly_trend_kg": -0.42,
  "n_points": 10
}
```

Oznacza to trend około:

```text
-0.42 kg / tydzień
```

Trend masy jest przede wszystkim sygnałem diagnostycznym i długoterminowym.

Nie powinien być automatycznie traktowany jako dowód, że całe odchylenie masy oznacza zmianę tkanki tłuszczowej.

Na masę ciała wpływają między innymi:

* woda,
* glikogen,
* zawartość przewodu pokarmowego,
* sól,
* trening,
* nawodnienie.

---

# Bioimpedancja

Planowana integracja z Renpho może dostarczyć dodatkowych danych z pomiarów bioimpedancyjnych.

Dane BIA powinny być traktowane jako **dodatkowy kontekst**, a nie jako bezpośredni pomiar rzeczywistego składu ciała.

Głównym stabilnym sygnałem pozostaje trend masy ciała.

Dane BIA mogą być wykorzystywane do obserwacji trendów, ale wymagają ostrożnej interpretacji ze względu na wpływ:

* nawodnienia,
* pory pomiaru,
* spożycia posiłków,
* treningu,
* temperatury,
* warunków pomiaru.

---

# Activity Stability

System analizuje również zmienność aktywności.

Porównywane są średnie:

```text
7d
14d
28d
```

Na tej podstawie aktywność może zostać sklasyfikowana jako:

```text
Stable
Moderately Variable
Highly Variable
```

Jest to szczególnie istotne przy nieregularnym trybie treningowym.

Duża zmienność oznacza, że krótkoterminowe średnie mogą być mniej reprezentatywne dla typowego poziomu aktywności.

---

# Confidence

Każda ważna analiza powinna być oceniana razem z informacją:

> Jak bardzo możemy ufać temu wynikowi?

Confidence uwzględnia:

* liczbę punktów,
* kompletność okna,
* stabilność danych,
* występowanie luk.

Wynik:

```text
High
Medium
Low
```

Brak wystarczającej liczby danych nie jest zamieniany na sztuczną pewność.

System może zwrócić:

```text
confidence = null
```

gdy danych jest zbyt mało.

---

# Pipeline

Cała analiza przebiega przez pięć głównych etapów:

```text
1. InputValidation
        ↓
2. ModelBuilding
        ↓
3. Analytics
        ↓
4. Confidence / Explain
        ↓
5. Serialization
```

## InputValidation

Sprawdzenie poprawności danych wejściowych.

## ModelBuilding

Konwersja surowych danych do typowanych modeli i serii analitycznych.

## Analytics

Wykonywane są właściwe obliczenia:

* baseline,
* recovery,
* ACWR,
* cardio load,
* temperature,
* nutrition,
* weight trend,
* activity stability.

## Confidence / Explain

Wyznaczenie jakości danych i przygotowanie wyjaśnień poszczególnych sygnałów.

## Serialization

Wynik jest serializowany do JSON.

---

# Struktura projektu

```text
analytics/
├── config/
│   └── settings.py
│
├── validators/
│   ├── input.py
│   └── metrics.py
│
├── acwr.py
├── apple_cardio.py
├── baseline.py
├── confidence.py
├── demo_full.py
├── exceptions.py
├── explain.py
├── fetch_apple.py
├── fetch_hevy.py
├── fetch_mfp.py
├── logging.py
├── metrics.py
├── models.py
├── nutrition_adaptive.py
├── pipeline.py
├── readiness_integration.py
├── run_analysis.py
├── stability.py
└── temperature.py

tests/
├── test_acwr.py
├── test_apple_cardio.py
├── test_baseline.py
├── test_confidence.py
├── test_explain.py
├── test_fetch_apple.py
├── test_fetch_mfp.py
├── test_integration.py
├── test_metrics.py
├── test_models.py
├── test_nutrition.py
├── test_pipeline.py
├── test_readiness.py
├── test_stability.py
├── test_temperature.py
└── test_validation.py
```

---

# Wejście

Podstawowym interfejsem jest JSON.

Przykład:

```json
{
  "source": "apple+hevy+mfp",
  "target_date": "2026-08-07",

  "apple_daily": [
    {
      "date": "2026-08-07",
      "resting_heart_rate": 53,
      "heart_rate_variability": 44.33,
      "sleep": {
        "total_hours": 6.43
      },
      "basal_energy_burned": 7200,
      "active_energy": 4200,
      "weight_body_mass": 71.05
    }
  ],

  "apple_workouts": [
    {
      "name": "Outdoor Cycling",
      "start": "2026-08-06T17:37:17",
      "duration_min": 88.2,
      "avg_heart_rate_bpm": 143.5
    }
  ],

  "hevy_workouts": [],

  "mfp_calories": [],

  "params": {
    "phase": "utrzymanie",
    "bodyweight_kg": 71
  }
}
```

> `bodyweight_kg` powinno docelowo być pobierane z Apple Health jako źródła referencyjnego, a nie ręcznie przekazywane jako wartość pochodząca z MFP.

---

# Wyjście

Raport zawiera między innymi:

```text
readiness
acwr
acwr_detail
temperature
nutrition
baseline_trends
confidence
weight_trend
activity_stability
explanations
inputs
```

Najważniejsza struktura logiczna:

```text
readiness
├── recovery
├── load
├── data_quality
├── verdict
└── legacy score
```

---

# Status analizy

System rozróżnia:

```text
ok
fallback
error
```

### ok

Dane są wystarczające do wykonania analizy.

### fallback

Dane są niewystarczające do części analizy.

System nie powinien wymyślać brakujących wartości.

### error

Dane wejściowe są uszkodzone lub nie spełniają wymagań walidacji.

---

# Ważne założenia

## Brak danych ≠ wartość zero

Przykład:

```text
brak snu
```

nie oznacza:

```text
0 godzin snu
```

Analogicznie brak RPE nie oznacza automatycznie RPE = 0.

---

## Load ≠ fatigue

Wysokie obciążenie treningowe oznacza:

```text
dużo bodźca
```

nie:

```text
organizm jest przemęczony
```

Dlatego `LOAD` i `RECOVERY` są analizowane oddzielnie.

---

## ACWR ≠ bezpośredni pomiar zmęczenia

ACWR jest wskaźnikiem relacji aktualnego obciążenia do obciążenia historycznego.

Nie jest bezpośrednim pomiarem:

* zmęczenia mięśni,
* regeneracji układu nerwowego,
* ryzyka kontuzji,
* przetrenowania.

Dlatego ACWR jest tylko jednym z komponentów systemu.

---

## TDEE ≠ bezpośredni pomiar wydatku energetycznego

TDEE jest estymacją opartą na danych z Apple Health.

W szczególności:

```text
TDEE ≈ Basal Energy + Active Energy
```

jest modelem estymacyjnym, a nie bezpośrednim pomiarem rzeczywistego wydatku energetycznego.

Długoterminowy trend masy ciała i rzeczywiste spożycie kcal mogą w przyszłości służyć jako dodatkowe sprzężenie zwrotne dla kalibracji tej estymacji.

---

## Confidence jest częścią wyniku

Wynik bez informacji o jakości danych jest niepełny.

Przykład:

```text
READINESS = GREEN
CONFIDENCE = LOW
```

jest zupełnie innym komunikatem niż:

```text
READINESS = GREEN
CONFIDENCE = HIGH
```

---

# Ograniczenia metodologiczne

Projekt jest systemem analitycznym, a nie narzędziem diagnostycznym.

W szczególności:

* HRV jest podatne na wpływ wielu czynników,
* RHR zależy od warunków pomiaru,
* sen z wearables jest estymacją,
* aktywna energia Apple jest estymacją,
* TDEE jest estymacją,
* ACWR jest wskaźnikiem obciążenia, a nie bezpośrednim pomiarem zmęczenia,
* temperatura nadgarstka może mieć wiele przyczyn,
* trend masy ciała zawiera wodę, glikogen i inne krótkoterminowe zmiany,
* pomiary bioimpedancji są podatne na warunki pomiaru,
* pojedynczy pomiar masy lub BIA nie powinien być interpretowany jako zmiana tkanki tłuszczowej.

System ma pomagać w podejmowaniu decyzji treningowych i żywieniowych na podstawie wielu sygnałów, a nie zastępować ocenę stanu zdrowia.

---

# Uruchomienie

Instalacja:

```bash
pip install -e .
```

Demo:

```bash
python -m analytics.demo_full
```

Analiza JSON:

```bash
python -m analytics.run_analysis '<json>'
```

---

# Testy

Projekt posiada testy jednostkowe i integracyjne dla głównych modułów.

Uruchomienie:

```bash
pytest
```

Kontrola jakości:

```bash
ruff check .
mypy analytics
```

---

# Filozofia projektu

Projekt został zaprojektowany według kilku zasad:

### 1. Determinizm

Te same dane wejściowe powinny dawać ten sam wynik.

### 2. Rozdzielenie odpowiedzialności

Każdy moduł odpowiada za jeden obszar analizy.

### 3. Brak ukrytego liczenia przez LLM

LLM interpretuje wynik, ale nie wylicza metryk.

### 4. Jawne braki danych

System nie powinien uzupełniać brakujących danych zgadywaniem.

### 5. Rozdzielenie load i recovery

Obciążenie nie jest automatycznie utożsamiane ze zmęczeniem.

### 6. Ocena jakości danych

Każdy wynik powinien mieć możliwość określenia swojej wiarygodności.

### 7. Rozdzielenie źródeł danych

Każde źródło powinno odpowiadać za dane, które najlepiej reprezentuje:

```text
Apple Health
→ fizjologia, aktywność, wydatek energetyczny, masa ciała

Hevy
→ trening siłowy

MyFitnessPal
→ rzeczywiste spożycie kcal

Renpho
→ pomiar masy ciała i dane BIA
  przez synchronizację z Apple Health
```

### 8. Adaptacja do zmienności

System powinien działać zarówno przy regularnym treningu siłowym, jak i przy nieregularnym treningu wydolnościowym oraz zmiennej aktywności codziennej.

---

# Docelowy model decyzyjny

Najważniejszym celem projektu nie jest stworzenie jednej magicznej liczby.

Celem jest stworzenie modelu:

```text
                ┌───────────────┐
                │   PHYSIOLOGY  │
                │ HRV / RHR /   │
                │ sleep / temp  │
                └───────┬───────┘
                        │
                        ▼
                   RECOVERY
                        │
                        │
LOAD ◄──────────────────┼──────────────────► NUTRITION
                        │
                        │
                        ▼
                  DATA QUALITY
                        │
                        ▼
                    VERDICT
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
        GREEN         ORANGE         RED
          │             │             │
          ▼             ▼             ▼
       normal       reduce load    recover
```

Równolegle system żywieniowy tworzy niezależną pętlę:

```text
        APPLE HEALTH
        Basal + Active
              │
              ▼
        estimated TDEE
              │
              │
              ▼
MFP ─────► Energy Balance
kcal            │
consumed        ▼
          Weight Trend
              │
              ▼
       TDEE calibration
```

System ma odpowiedzieć nie tylko:

> „Jaki mam readiness score?”

ale przede wszystkim:

> **„Co obecnie dzieje się z moim obciążeniem, regeneracją i zapotrzebowaniem energetycznym oraz jak bardzo możemy ufać tej ocenie?”**

W dłuższym okresie celem jest stworzenie adaptacyjnego modelu, który łączy:

```text
obciążenie treningowe
        +
regenerację
        +
codzienną aktywność
        +
spożycie energii
        +
wydatek energetyczny
        +
trend masy ciała
```

i na tej podstawie dostarcza możliwie stabilnej, przejrzystej oraz mierzalnej oceny aktualnego stanu organizmu.
