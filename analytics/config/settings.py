"""
config/settings.py — centralna konfiguracja pakietu analytics.

Jedno źródło prawdy dla wszystkich stałych algorytmicznych (okna, alphy,
progi, zakresy). Moduły analityczne czytają stąd wartości zamiast trzymać
magiczne liczby w kodzie.

Podział na dataclass per obszar (baseline / ACWR / temperatura / odżywianie /
readiness), bo każdy obszar ma własne okna i progi. Wszystkie obiekty są
frozen (immutable) — konfiguracja nie powinna zmieniać się w trakcie działania.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Baseline (HRV / RHR / trend) -------------------------------------------


@dataclass(frozen=True)
class BaselineSettings:
    """Parametry rolowanego baseline EWMA i detekcji trendu."""

    #: waga EWMA dla krótkiego/standardowego baseline (domyślne alpha)
    ewma_alpha: float = 0.1
    #: minimalna liczba punktów (poza dniem bieżącym) do wyliczenia baseline
    min_points: int = 5
    #: osobny baseline treningowy — minimalna liczba punktów
    min_points_context: int = 4
    #: okno trendu (dni) do regresji liniowej
    trend_window_days: int = 7
    #: rozmiar wygładzania medianą ruchomą przed regresją
    trend_smoothing: int = 3
    #: minimalne R^2, by trend uznać za wiarygodny
    trend_min_r_squared: float = 0.3
    #: próg (% różnicy) dla detekcji przesunięcia baseline (krótkie vs długie)
    shift_threshold_pct: float = 8.0
    #: alphy dla detekcji przesunięcia (krótkie ~7d, długie ~28d)
    shift_alpha_short: float = 0.2
    shift_alpha_long: float = 0.05


# --- ACWR (obciążenie treningowe) -------------------------------------------


@dataclass(frozen=True)
class ACWRSettings:
    """Parametry Acute:Chronic Workload Ratio (Gabbett 2016)."""

    #: okno acute (dni)
    acute_window: int = 7
    #: okno chronic (dni)
    chronic_window: int = 28
    #: czy chronic liczyć przez EWMA (zalecane) zamiast prostego rolling mean
    chronic_use_ewma: bool = True
    #: waga EWMA dla chronic load
    chronic_alpha: float = 0.05
    #: granice stref ryzyka ACWR [dolny próg, górny próg, wysoki próg]
    zone_low: float = 0.8
    zone_optimal_high: float = 1.3
    zone_elevated_high: float = 1.5

    #: tętno spoczynkowe (bpm) do TRIMP (cardio z Apple Watch).
    #: Używane, gdy sesja nie ma własnego hr_rest. Dopasuj do siebie:
    #: hr_rest to Twoje poranne spoczynkowe.
    hr_rest_default: int = 55
    #: domyślny punkt odniesienia intensywności TRIMP (bpm), gdy sesja nie ma
    #: własnego peak HR. NIE jest to fizjologiczne HRmax — patrz apple_cardio.
    hr_peak_default: int = 190
    #: minimalny punkt odniesienia (bpm) do normalizacji TRIMP. Zabezpiecza
    #: przed zawyżeniem względnej intensywności podczas lekkich treningów
    #: (gdy sesyjny peak HR jest niski). None = brak dolnego limitu (domyślna
    #: metodologia: reference = peak HR sesji). Parametr konfiguracyjny, nie
    #: element wzoru TRIMP.
    hr_reference_floor_bpm: int | None = 170

    #: próg (dni bez treningu z rzędu, licząc wstecz od target) do wykrycia
    #: "luki treningowej" (detraining gap) — ACWR ratio samo w sobie NIE
    #: łapie ryzyka pierwszego powrotu po przerwie (Gabbett 2016 / Impellizzeri
    #: 2020: acute:chronic nie modeluje utraty tolerancji tkanki). Patrz
    #: detect_training_gap.
    gap_min_days: int = 7
    #: powyżej tego progu luka jest klasyfikowana jako "długa" (ostrzejszy
    #: override w komunikacie) zamiast "krótka".
    gap_long_days: int = 14

    #: minimalna liczba dni z obciążeniem (load>0) w oknie chronic, aby RATIO
    #: cardio był wiarygodny do klasyfikacji strefy ryzyka. Użytkownik ma cardio
    #: "szarpane" (nieregularne, ale mocne/submaksymalne) — ratio przy rzadkich
    #: sesjach jest zawsze zaniżone chronic i fałszuje w górę (np. 3.32 z 4 sesji).
    #: Dlatego próg jest WYSOKI (regularne cardio przez ~2 tyg.): poniżej niego
    #: ratio NIE karze w readiness (strefa "niewystarczające dane"), a realny
    #: sygnał obciążenia cardio bierze cardio_7d (liczba mocnych sesji w tygodniu).
    cardio_min_valid_days: int = 12

    #: próg liczby mocnych sesji cardio w oknie acute (7d) do karencji w gotowości.
    #: 0 = brak karencji cardio; kara rośnie z liczbą mocnych sesji w tygodniu
    #: (np. 1 sesja = +0, 2 = +1, 3+ = +2). To realny sygnał "ile mocnego cardio
    #: wpadło w ten tydzień" — przy nieregularnym cardio ważniejszy niż ratio.
    cardio_7d_penalty_thresholds: tuple[int, int] = (2, 3)

    #: nazwa strefy dla ACWR cardio z za małą próbką (nie jest strefą ryzyka)
    zone_insufficient: str = "niewystarczające dane"


# --- Temperatura nadgarstka --------------------------------------------------


@dataclass(frozen=True)
class TemperatureSettings:
    """Parametry alertu temperatury nadgarstka (Apple Watch Ultra 2)."""

    #: okno baseline (dni)
    baseline_window: int = 14
    #: próg odchylenia (°C) do triggera alertu
    threshold_c: float = 0.3
    #: mnożnik progu do uznania alertu za "znaczący" (znacząca <= threshold*1.5)
    significant_multiplier: float = 1.5


# --- Odżywianie (TDEE z aktywności + białko) --------------------------------


@dataclass(frozen=True)
class NutritionSettings:
    """Parametry wyliczania celu kalorycznego (TDEE z aktywności) i celu białkowego.

    TDEE liczony jest z rzeczywistej aktywności Apple Health (basal + active),
    NIE z celu MFP. Marża kaloryczna jest procentowa względem TDEE, zależna
    od aktualnego celu (utrzymanie / redukcja / masa).
    """

    #: okno aktywności (dni) do średniego TDEE — 7 = bieżąca forma
    activity_window_days: int = 7
    #: okno docelowe (dłuższe = większa stabilność) — aktywne po zwiększeniu window
    activity_window_long_days: int = 28
    #: czy liczyć też długie okno (28d) obok aktywnego (7d) dla porównania
    compute_long_window: bool = True

    #: marża kaloryczna (% od TDEE) wg celu — ujemna = deficyt (redukcja),
    #: dodatnia = nadwyżka (masa). Mnożnik względem TDEE.
    goal_margin: dict = field(
        default_factory=lambda: {"utrzymanie": 0.0, "redukcja": -0.15, "masa": 0.10}
    )
    #: domyślna marża, gdy cel nieznany
    margin_default: float = 0.0

    #: kcal/dzień z 1 kg tkanki tłuszczowej (kontekst trendu wagi, rezerwa)
    kcal_per_kg_fat: int = 7700

    #: okno trendu wagi (dni) — rezerwa na przyszły, wielopunktowy trend
    weight_trend_window_days: int = 14
    #: minimalna liczba punktów wagi do policzenia trendu (rezerwa)
    weight_min_points: int = 8
    #: maks. pojedyncza korekta marży (kcal) z trendu wagi (rezerwa)
    max_single_adjustment_kcal: float = 250
    #: próg |trend_gap| (kg/tydz) do klasyfikacji confidence "wysoka" (rezerwa)
    confidence_gap_kg_per_week: float = 0.1

    #: konwersja kJ -> kcal (1 kcal = 4.184 kJ)
    kj_per_kcal: float = 4.184

    #: białko g/kg wg fazy
    protein_g_per_kg: dict = field(
        default_factory=lambda: {"deficyt": 2.2, "utrzymanie": 1.8, "nadwyżka": 1.8}
    )


# --- Bilans energetyczny (wydatek vs zjedzone) ------------------------------


@dataclass(frozen=True)
class EnergyBalanceSettings:
    """Ocena zaspokojenia wydatku energetycznego przez dostarczone kcal.

    TDEE (wydatek) liczony z aktywności Apple; zjedzone kcal z MFP. Kumulujący
    się niedobór (zjedzone < wydatek) przez kilka dni upośledza regenerację
    i zwiększa ryzyko kontuzji/urazu/infekcji — to celowe powiązanie.
    """

    #: okno oceny bilansu (dni wstecz od targetu)
    balance_window_days: int = 7
    #: ile dni z kompletem (zjedzone+wydatek) potrzeba, żeby ocena była wiarygodna
    min_valid_days: int = 3

    #: skumulowany niedobór (kcal) w oknie do strefy ryzyka.
    #: (niedobór_średni, niedobór_wysoki): poniżej = niski, powyżej = wysoki.
    #: ~7700 kcal/kg fat -> 1500-2000 kcal/tydz to wyraźny deficyt; >= 3500
    #: (~0.45 kg/tydz) przy obciążeniu treningowym to realny sygnał ryzyka.
    deficit_low_kcal: int = 1500
    deficit_high_kcal: int = 3500

    #: czy uwzględniać cel (marżę) — jeśli target_kcal < tdee (redukcja), ocena
    #: niedoboru względem celu jest łagodniejsza niż względem surowego TDEE.
    compare_against_target: bool = True


# --- Readiness (finalny scoring) --------------------------------------------


@dataclass(frozen=True)
class ReadinessSettings:
    """Progi scoringu gotowości (HRV / RHR / sen) i klasyfikacji stref."""

    #: HRV: punkty karne gdy odchylenie <= -20% / -10%
    hrv_penalty_high: float = -20.0
    hrv_penalty_low: float = -10.0
    #: RHR: punkty karne gdy odchylenie >= 5 / 3 bpm
    rhr_penalty_high: float = 5.0
    rhr_penalty_low: float = 3.0
    #: sen: punkty karne gdy < / <  (godziny)
    sleep_penalty_high_h: float = 5.5
    sleep_penalty_low_h: float = 6.5
    #: granice stref po liczbie punktów (zielona <= g1, żółta <= g2, reszta czerwona)
    zone_green_max: int = 1
    zone_yellow_max: int = 3


# --- Confidence (faza 2.0: wiarygodność metryk) ----------------------------


@dataclass(frozen=True)
class ConfidenceSettings:
    """Wagi i progi dla Confidence Score (0-100) per metryka.

    Score = ważona suma składowych, każda w zakresie [0,1] * 100.
    Etykiety: >= HIGH_MIN -> High, >= MED_MIN -> Medium, w innym razie Low.
    """

    #: waga: ilość danych historycznych (n_points / target_n_points, cap 1.0)
    w_history: float = 0.30
    #: waga: kompletność okna (dni z danymi / dni okna)
    w_completeness: float = 0.25
    #: waga: stabilność (1 - min(cv, 1)), gdzie cv = std/mean aktywności
    w_stability: float = 0.25
    #: waga: brak luk (1 - missing/total)
    w_coverage: float = 0.20

    #: docelowa liczba punktów do pełnej „history" składowej
    target_n_points: int = 14
    #: docelowa liczba dni okna do pełnej „completeness"
    target_window_days: int = 14

    #: progi etykiet
    high_min: int = 80
    medium_min: int = 60

    #: minimalna liczba punktów, poniżej której confidence = None (brak podstaw)
    min_points_for_confidence: int = 3


# --- Stability (faza 3.0: zmienność aktywności) ----------------------------


@dataclass(frozen=True)
class StabilitySettings:
    """Progi kategoryzacji zmienności aktywności.

    variation = (max(avg) - min(avg)) / max(avg) dla okien 7/14/28 dni.
    """

    #: poniżej -> Stable
    stable_max_variation: float = 0.10
    #: poniżej -> Moderately Variable; powyżej -> Highly Variable
    moderate_max_variation: float = 0.25


# --- Walidacja (zakresy metryk) ---------------------------------------------


@dataclass(frozen=True)
class MetricRanges:
    """Dopuszczalne (min, max) dla metryk — używane przez validators/."""

    #: HRV (ms)
    hrv: tuple[float, float] = (30.0, 250.0)
    #: RHR (bpm)
    rhr: tuple[float, float] = (20.0, 120.0)
    #: sen (godziny)
    sleep: tuple[float, float] = (2.0, 18.0)
    #: temperatura nadgarstka (°C)
    temperature: tuple[float, float] = (34.0, 42.0)
    #: waga (kg) — realistyczny zakres człowieka dorosłego (nie kilkulatka ani słonia)
    weight: tuple[float, float] = (40.0, 200.0)
    #: tonaż pojedynczej serii (kg) — ciężar ujemny/zera wykluczamy w fetch
    set_weight_non_negative: bool = True


# --- Singletony (bezpieczne do importu z każdego modułu) ---------------------

BASELINE = BaselineSettings()
ACWR = ACWRSettings()
TEMPERATURE = TemperatureSettings()
NUTRITION = NutritionSettings()
ENERGY_BALANCE = EnergyBalanceSettings()
READINESS = ReadinessSettings()
CONFIDENCE = ConfidenceSettings()
STABILITY = StabilitySettings()
RANGES = MetricRanges()

__all__ = [
    "BaselineSettings",
    "ACWRSettings",
    "TemperatureSettings",
    "NutritionSettings",
    "EnergyBalanceSettings",
    "ReadinessSettings",
    "ConfidenceSettings",
    "StabilitySettings",
    "MetricRanges",
    "BASELINE",
    "ACWR",
    "TEMPERATURE",
    "NUTRITION",
    "ENERGY_BALANCE",
    "READINESS",
    "CONFIDENCE",
    "STABILITY",
    "RANGES",
]
