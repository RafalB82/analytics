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


# --- Odżywianie (TDEE adaptive + białko) ------------------------------------


@dataclass(frozen=True)
class NutritionSettings:
    """Parametry pętli zwrotnej TDEE i celu białkowego."""

    #: standardowa wartość energetyczna 1 kg tkanki tłuszczowej (kcal)
    kcal_per_kg_fat: int = 7700
    #: okno trendu wagi (dni)
    weight_trend_window_days: int = 14
    #: minimalna liczba punktów wagi do policzenia trendu
    weight_min_points: int = 8
    #: maks. pojedyncza korekta TDEE (kcal) — chroni przed zaszumioną korektą
    max_single_adjustment_kcal: float = 250
    #: próg |trend_gap| (kg/tydz) do klasyfikacji confidence "wysoka"
    confidence_gap_kg_per_week: float = 0.1
    #: białko g/kg wg fazy
    protein_g_per_kg: dict = field(
        default_factory=lambda: {"deficyt": 2.2, "utrzymanie": 1.8, "nadwyżka": 1.8}
    )


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
    #: waga (kg)
    weight: tuple[float, float] = (20.0, 400.0)
    #: tonaż pojedynczej serii (kg) — ciężar ujemny/zera wykluczamy w fetch
    set_weight_non_negative: bool = True


# --- Singletony (bezpieczne do importu z każdego modułu) ---------------------

BASELINE = BaselineSettings()
ACWR = ACWRSettings()
TEMPERATURE = TemperatureSettings()
NUTRITION = NutritionSettings()
READINESS = ReadinessSettings()
RANGES = MetricRanges()

__all__ = [
    "BaselineSettings",
    "ACWRSettings",
    "TemperatureSettings",
    "NutritionSettings",
    "ReadinessSettings",
    "MetricRanges",
    "BASELINE",
    "ACWR",
    "TEMPERATURE",
    "NUTRITION",
    "READINESS",
    "RANGES",
]
