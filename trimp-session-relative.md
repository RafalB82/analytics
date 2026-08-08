# TRIMP: Session-Relative Heart Rate Reference

## Cel

Obecna implementacja TRIMP wykorzystuje maksymalne tętno z konkretnej sesji jako punkt odniesienia intensywności.

To zachowanie jest **zamierzone** i odpowiada charakterowi treningów kolarskich:

* większość jazd jest wykonywana z relatywnie wysoką intensywnością,
* `peak HR` z sesji jest użytecznym wskaźnikiem charakteru wysiłku,
* celem TRIMP w projekcie nie jest wyznaczanie absolutnego obciążenia fizjologicznego względem rzeczywistego HRmax,
* celem jest oszacowanie **względnego obciążenia układu krążenia wynikającego z konkretnej sesji**.

Jednocześnie metoda może zawyżyć względną intensywność podczas lekkiej jazdy tlenowej, gdy sesyjny peak HR będzie niski.

Przykład:

```text
Lekka jazda:
HRavg = 125
peak HR = 140

Mocna jazda:
HRavg = 155
peak HR = 180
```

Jeżeli `peak HR` jest bezpośrednio używany jako mianownik normalizacji, lekka jazda może otrzymać nieproporcjonalnie wysoką intensywność względną.

Celem zmian jest zachowanie obecnej metodologii bez udawania, że sesyjny peak HR jest fizjologicznym HRmax.

---

# 1. Zmiana semantyki parametru

## Obecnie

Kod używa pojęcia:

```python
hr_max
```

Problem polega na tym, że nazwa sugeruje:

> rzeczywiste maksymalne tętno użytkownika.

Tymczasem wartość pochodzi z konkretnego treningu:

```text
max_heart_rate_bpm
```

czyli jest maksymalnym tętnem **zaobserwowanym podczas sesji**.

## Docelowo

Zmienić nazewnictwo na:

```python
peak_hr_bpm
```

lub bardziej jednoznacznie:

```python
session_peak_hr_bpm
```

Preferowana wersja:

```python
session_peak_hr_bpm
```

### Przykład

Z:

```python
compute_trimp_session_load(
    avg_hr=avg_hr,
    duration_min=duration,
    hr_max=max_hr,
    resting_hr=resting_hr,
)
```

na:

```python
compute_trimp_session_load(
    avg_hr=avg_hr,
    duration_min=duration,
    session_peak_hr=session_peak_hr,
    resting_hr=resting_hr,
)
```

---

# 2. Zmiana definicji TRIMP

TRIMP powinien być w projekcie traktowany jako:

> **session-relative cardiovascular load**

a nie:

> absolute physiological strain based on true HRmax.

Oznacza to, że:

```text
session_peak_hr
```

jest punktem odniesienia dla charakteru konkretnej sesji.

Schemat:

```text
Apple Watch
    │
    ├── average HR
    ├── session peak HR
    └── duration
          │
          ▼
    Session-relative TRIMP
          │
          ▼
      Cardio load
          │
          ▼
    Cardio ACWR / EWMA
          │
          ▼
       Readiness
```

---

# 3. Zachowanie obecnej metodologii

Nie należy automatycznie zastępować:

```python
session_peak_hr
```

rzeczywistym:

```python
user_hr_max
```

To zmieniłoby charakter istniejących wyników i mogłoby zaburzyć ciągłość historycznych danych.

Obecna metoda pozostaje domyślna:

```text
reference HR = session peak HR
```

Dzięki temu obecne wyniki pozostają porównywalne.

---

# 4. Dodanie minimalnego HR reference

Należy dodać opcjonalny dolny limit dla wartości używanej jako punkt odniesienia.

Przykład:

```python
effective_reference_hr = max(
    session_peak_hr_bpm,
    configured_hr_reference_bpm,
)
```

Przykładowa konfiguracja:

```python
hr_reference_floor_bpm = 170
```

### Zachowanie

Dla mocnej sesji:

```text
session_peak_hr = 182
floor = 170

effective_reference_hr = 182
```

Dla lekkiej sesji:

```text
session_peak_hr = 142
floor = 170

effective_reference_hr = 170
```

Dzięki temu niski peak HR podczas lekkiej jazdy nie powoduje sztucznego zawyżenia intensywności względnej.

---

# 5. Nazwa konfiguracji

Preferowana nazwa:

```python
hr_reference_floor_bpm
```

Nie należy nazywać tego:

```python
hr_max_bpm
```

ponieważ nie jest to fizjologiczne HRmax.

Alternatywna nazwa:

```python
minimum_trimp_reference_hr_bpm
```

jest bardziej jednoznaczna, ale zbyt długa jak na konfigurację używaną lokalnie.

Preferowane:

```python
hr_reference_floor_bpm
```

---

# 6. Zmiana funkcji TRIMP

Docelowy interfejs:

```python
def compute_trimp_session_load(
    avg_hr: float,
    duration_min: float,
    session_peak_hr: float,
    resting_hr: float,
    hr_reference_floor: float | None = None,
) -> float:
```

Logika:

```python
reference_hr = session_peak_hr

if hr_reference_floor is not None:
    reference_hr = max(
        session_peak_hr,
        hr_reference_floor,
    )
```

Następnie `reference_hr` jest używany do normalizacji TRIMP.

---

# 7. Walidacja

Należy zachować istniejące zabezpieczenia dotyczące:

```text
avg_hr
resting_hr
duration
session_peak_hr
```

Dodatkowo:

```python
session_peak_hr >= avg_hr
```

powinno być traktowane jako wymaganie danych wejściowych.

Jeżeli:

```text
peak HR < average HR
```

dane są niespójne i funkcja powinna zwrócić kontrolowany błąd albo `None`, zgodnie z istniejącą konwencją projektu.

Dla:

```python
hr_reference_floor = None
```

zachowanie powinno być identyczne z obecną implementacją.

---

# 8. Przykładowe przypadki

## Mocny trening

```text
HRrest = 55
HRavg = 155
peak HR = 182
floor = 170
```

Wynik:

```text
reference HR = 182
```

Brak zmiany względem obecnej metodologii.

---

## Lekki trening

```text
HRrest = 55
HRavg = 125
peak HR = 140
floor = 170
```

Wynik:

```text
reference HR = 170
```

Lekka jazda pozostaje lekka również w normalizacji TRIMP.

---

## Trening dokładnie na poziomie floor

```text
peak HR = 170
floor = 170
```

Wynik:

```text
reference HR = 170
```

---

## Bardzo mocny trening

```text
peak HR = 190
floor = 170
```

Wynik:

```text
reference HR = 190
```

Floor nie ogranicza mocnych sesji.

---

# 9. Testy jednostkowe

Należy dodać testy:

### 9.1 Brak floor

```python
compute_trimp_session_load(
    ...,
    session_peak_hr=180,
    hr_reference_floor=None,
)
```

Powinien używać:

```text
reference = 180
```

---

### 9.2 Peak powyżej floor

```text
peak = 180
floor = 170
```

Oczekiwane:

```text
reference = 180
```

---

### 9.3 Peak poniżej floor

```text
peak = 140
floor = 170
```

Oczekiwane:

```text
reference = 170
```

---

### 9.4 Peak równy floor

```text
peak = 170
floor = 170
```

Oczekiwane:

```text
reference = 170
```

---

### 9.5 Lekka jazda

Test powinien potwierdzić, że:

```text
HRavg = 125
peak = 140
floor = 170
```

nie generuje nieproporcjonalnie wysokiego obciążenia względnego.

---

### 9.6 Mocna jazda

Test powinien potwierdzić, że:

```text
HRavg = 155
peak = 182
floor = 170
```

zachowuje dotychczasową normalizację.

---

# 10. Dokumentacja

W dokumentacji modułu `apple_cardio` należy jasno zaznaczyć:

```text
TRIMP uses the session peak heart rate as an intensity reference.
It is intentionally not treated as the user's physiological HRmax.

A configurable minimum reference HR can be applied to prevent
low-intensity sessions from artificially inflating relative intensity.
```

W polskiej dokumentacji projektu:

> TRIMP wykorzystuje maksymalne tętno zaobserwowane podczas sesji jako punkt odniesienia intensywności. Nie jest ono traktowane jako fizjologiczne HRmax użytkownika. Opcjonalny dolny limit punktu odniesienia zabezpiecza przed zawyżeniem względnej intensywności podczas lekkich treningów.

---

# 11. Wpływ na ACWR

Nie należy zmieniać logiki ACWR.

ACWR powinien nadal otrzymywać:

```text
session-relative TRIMP
        ↓
daily cardio load
        ↓
acute load
        ↓
chronic EWMA
        ↓
ACWR
```

Zmiana dotyczy wyłącznie sposobu wyliczenia obciążenia pojedynczej sesji.

Nie należy mieszać:

```text
TRIMP
```

z:

```text
ACWR
```

w jednej funkcji.

---

# 12. Wpływ na readiness

Readiness nie wymaga zmian.

Obecny przepływ pozostaje:

```text
Cardio session
      ↓
Session-relative TRIMP
      ↓
Cardio daily load
      ↓
Cardio ACWR
      ↓
Cardio modifier
      ↓
Readiness
```

Dzięki temu zmiana pozostaje lokalna i nie powoduje refaktoryzacji całego pipeline'u.

---

# 13. Historyczna kompatybilność

Istotne jest zachowanie kompatybilności wyników.

Dla:

```python
hr_reference_floor_bpm = None
```

nowa implementacja powinna dawać taki sam wynik jak obecna:

```text
reference HR = session peak HR
```

Dzięki temu:

* istniejące dane pozostają porównywalne,
* testy historyczne nie powinny zmienić wyników,
* można wprowadzić floor bez zmiany podstawowej metodologii,
* konfigurację można wdrożyć stopniowo.

---

# 14. Rekomendowana konfiguracja początkowa

Na pierwszym etapie:

```python
hr_reference_floor_bpm = 170
```

ale warto traktować tę wartość jako **parametr konfiguracyjny**, a nie element wzoru TRIMP.

Docelowo:

```text
settings
    │
    └── hr_reference_floor_bpm
              │
              ▼
       apple_cardio
              │
              ▼
        TRIMP calculation
```

Nie należy hardkodować wartości `170` bezpośrednio w funkcji.

---

# 15. Ostateczna semantyka

Po zmianie system powinien mieć następujące znaczenie:

```text
session_peak_hr
    =
maksymalne HR zaobserwowane podczas konkretnej sesji
```

```text
hr_reference_floor
    =
minimalny punkt odniesienia używany do normalizacji TRIMP
```

```text
effective_reference_hr
    =
max(session_peak_hr, hr_reference_floor)
```

```text
TRIMP
    =
względne obciążenie układu krążenia podczas sesji
```

Nie:

```text
TRIMP
    =
absolutny pomiar fizjologicznego obciążenia względem HRmax
```

---

# 16. Zakres zmian

### Pliki prawdopodobnie wymagające zmian

```text
analytics/apple_cardio.py
analytics/config/settings.py
tests/test_apple_cardio.py
```

### Opcjonalnie

```text
README.md
docs/
```

jeżeli metodologia TRIMP jest opisana również poza modułem.

### Nie zmieniać

```text
analytics/acwr.py
analytics/readiness.py
analytics/pipeline.py
```

poza koniecznością przekazania nowego parametru konfiguracyjnego.

---

# 17. Podsumowanie

Proponowana zmiana **nie odrzuca obecnej metodologii**.

Porządkuje jej znaczenie:

```text
                 OBECNIE

session peak HR
       ↓
   TRIMP
       ↓
 cardio load
```

po zmianie:

```text
              DOCELOWO

session peak HR
       │
       ├──── minimum reference HR
       │
       ▼
effective reference HR
       │
       ▼
session-relative TRIMP
       │
       ▼
cardio load
       │
       ▼
cardio ACWR
       │
       ▼
readiness
```

Najważniejsze założenie pozostaje niezmienione:

> **Dla typowej, mocnej jazdy sesyjny peak HR jest dobrym punktem odniesienia charakteru wysiłku.**

Dodany `hr_reference_floor_bpm` zabezpiecza wyłącznie przypadek odstający, czyli lekką jazdę tlenową, podczas której niski peak HR mógłby sztucznie zwiększyć względną intensywność TRIMP.

Zmiana jest lokalna, zachowuje kompatybilność historyczną i nie wymaga przebudowy modelu ACWR ani readiness.

To jest celowo napisane jako **specyfikacja zmiany**, a nie kolejny ogólny refactor. Dzięki temu można ją praktycznie 1:1 przekazać do implementacji i potem sprawdzić audytem kodu.
