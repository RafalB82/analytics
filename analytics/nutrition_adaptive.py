"""
nutrition_adaptive.py — cel kaloryczny z aktywności (TDEE) + cel białkowy.

NOWY MODEL (zastępuje martwą korektę TDEE z MFP):
- TDEE liczony z REALNEJ aktywności Apple Health: basal_energy + active_energy
  (średnia z okna — domyślnie 7 dni, docelowo 28). Żadnego celu z MFP.
- Cel kaloryczny = TDEE_okno + marża(%) wg celu: utrzymanie / redukcja / masa.
- Waga (punkt kontrolny z Apple) jest OPCJONALNA — nie jest wymagana do
  wyliczenia TDEE z aktywności; służy jako kontekst (masa do białka) oraz
  rezerwa na przyszły, wielopunktowy trend (modyfikacja marży).

Deterministyczne: wszystkie wejścia to serie aktywności (kJ) + cel + masa.
MFP dostarcza WYŁĄCZNIE zjedzone kalorie/jedzenie — nie uczestniczy w TDEE.

Zależności: numpy
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from .config.settings import NUTRITION as _CFG
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class DailyEnergy:
    """Dzienny bilans energetyczny (wskaźniki aktywności z Apple)."""

    day: date
    basal_kj: float | None = None   # basal_energy_burned (kJ)
    active_kj: float | None = None  # active_energy (kJ)
    exercise_min: float | None = None   # apple_exercise_time (min)
    stand_min: float | None = None      # apple_stand_time (min)
    physical_effort: float | None = None  # kcal/hr·kg


@dataclass
class TDEEEstimate:
    """Oszacowanie TDEE z aktywności + wynikający cel kaloryczny."""

    tdee_kcal: float            # średnie całkowite wydatkowanie (basal+active) / dzień
    basal_kcal: float           # średni basal / dzień
    active_kcal: float          # średni active / dzień
    window_days: int            # użyte okno (7 lub 28)
    n_days: int                 # ile dni z kompletem danych weszło do średniej
    goal: str                   # "utrzymanie" | "redukcja" | "masa"
    margin_pct: float           # marża % (0 / -0.15 / +0.10)
    target_kcal: float          # tdee + marża → CEL KALORYCZNY
    protein_g: float | None     # cel białkowy (jeśli masa podana)

    # sygnały pomocnicze aktywności (kontekst, nie drugie źródło spalania)
    avg_exercise_min: float     # średnie minuty ćwiczeń / dzień w oknie
    avg_stand_min: float        # średnie minuty stania / dzień w oknie
    avg_physical_effort: float  # średni effort / dzień
    training_days_ratio: float  # odsetek dni z wyraźną aktywnością (exercise>0)


def _kj_to_kcal(kj: float) -> float:
    return kj / _CFG.kj_per_kcal


def compute_tdee(
    energy_series: list[DailyEnergy],
    goal: str = "utrzymanie",
    bodyweight_kg: float | None = None,
    window_days: int | None = None,
    compute_long: bool | None = None,
) -> TDEEEstimate:
    """
    TDEE z aktywności Apple (basal + active) + marża wg celu.

    energy_series: lista DailyEnergy (dzień, basal_kj, active_kj, ...) —
    posortowana rosnąco wg dnia.

    window_days: okno do średniej (default z configu: 7). compute_long:
    czy liczyć też długie okno (28d) przy defaultzie 7d — zwracane jako
    pole pomocnicze w output (patrz run_analysis).

    Rzuca ValueError, gdy brak wystarczającej liczby dni z kompletem energii.
    """
    window = window_days or _CFG.activity_window_days
    compute_long = _CFG.compute_long_window if compute_long is None else compute_long

    # wybierz ostatnie `window` dni
    recent = energy_series[-window:]

    basal = [d.basal_kj for d in recent if d.basal_kj is not None]
    active = [d.active_kj for d in recent if d.active_kj is not None]
    if not basal or not active:
        raise ValueError("brak wystarczających danych energii (basal/active) do TDEE")

    # średnie dzienne w kcal
    basal_kcal = _kj_to_kcal(float(np.mean(basal)))
    active_kcal = _kj_to_kcal(float(np.mean(active)))
    tdee = basal_kcal + active_kcal

    # sygnały pomocnicze aktywności
    exercise = [d.exercise_min for d in recent if d.exercise_min is not None]
    stand = [d.stand_min for d in recent if d.stand_min is not None]
    effort = [d.physical_effort for d in recent if d.physical_effort is not None]
    avg_exercise = round(float(np.mean(exercise)), 1) if exercise else 0.0
    avg_stand = round(float(np.mean(stand)), 1) if stand else 0.0
    avg_effort = round(float(np.mean(effort)), 3) if effort else 0.0
    n_training = sum(1 for d in recent if (d.exercise_min or 0) > 0)
    training_ratio = round(n_training / len(recent), 2) if recent else 0.0

    margin = float(_CFG.goal_margin.get(goal, _CFG.margin_default))
    target = round(tdee * (1 + margin), 0)

    protein = None
    if bodyweight_kg:
        protein = compute_protein_target(bodyweight_kg, phase=goal)

    logger.info("TDEE(%dd): basal=%.0f active=%.0f -> %.0f kcal, marża %.0f%% -> cel %.0f kcal",
                window, basal_kcal, active_kcal, tdee, margin * 100, target)

    return TDEEEstimate(
        tdee_kcal=round(tdee, 0),
        basal_kcal=round(basal_kcal, 0),
        active_kcal=round(active_kcal, 0),
        window_days=window,
        n_days=len(recent),
        goal=goal,
        margin_pct=margin,
        target_kcal=target,
        protein_g=protein,
        avg_exercise_min=avg_exercise,
        avg_stand_min=avg_stand,
        avg_physical_effort=avg_effort,
        training_days_ratio=training_ratio,
    )


def compute_long_window_tdee(
    energy_series: list[DailyEnergy],
    goal: str = "utrzymanie",
    bodyweight_kg: float | None = None,
) -> TDEEEstimate | None:
    """
    TDEE na dłuższym oknie (28d) — stabilniejsza średnica miesięczna.
    Zwraca None, gdy za mało danych. Używane jako porównanie do 7d.
    """
    long_window = _CFG.activity_window_long_days
    if len(energy_series) < _CFG.weight_min_points:  # reużyj progu min punktów
        return None
    try:
        return compute_tdee(
            energy_series, goal=goal, bodyweight_kg=bodyweight_kg,
            window_days=long_window,
        )
    except ValueError:
        return None


def compute_weight_trend(
    series: list[Any],
    window_days: int = _CFG.weight_trend_window_days,
    min_points: int = _CFG.weight_min_points,
) -> float | None:
    """
    Trend liniowy wagi w kg/dzień (rezerwa na wielopunktowy trend z Apple).

    Obecnie waga z Apple jest PUNKTEM (die punkt kontrolny), nie trendem —
    ta funkcja zostaje na przyszłość, gdy zbierze się >= min_points punktów.
    Przyjmuje dowolną serię z atrybutami .day/.weight_kg (WeightPoint lub
    odpowiednik z fetch).
    """
    if len(series) < min_points:
        logger.debug("za mało punktów wagi do trendu: %d (min %d)", len(series), min_points)
        return None
    recent = series[-window_days:]
    x = np.arange(len(recent))
    y = np.array([p.weight_kg for p in recent])
    slope, _ = np.polyfit(x, y, 1)
    return round(float(slope), 4)


def adjust_tdee(
    current_tdee: float,
    weight_trend_kg_per_day: float | None,
    target_trend_kg_per_week: float = 0.0,
    max_single_adjustment_kcal: float = _CFG.max_single_adjustment_kcal,
) -> TDEEAdjustment:
    """(Rezerwa) korekta celu na bazie trendu wagi — gdy trend będzie dostępny.

    Zachowana dla kompatybilności; w nowym modelu TDEE z aktywności ta
    korekta zwykle nie jest potrzebna. Zwraca no-op gdy brak trendu.
    """
    if weight_trend_kg_per_day is None:
        return TDEEAdjustment(
            old_tdee=current_tdee, new_tdee=current_tdee,
            weekly_trend_kg=0.0, adjustment_kcal=0.0, confidence="niska",
        )
    weekly = weight_trend_kg_per_day * 7
    gap = weekly - target_trend_kg_per_week
    raw = (gap * _CFG.kcal_per_kg_fat) / 7
    adj = -raw
    adj = max(-max_single_adjustment_kcal, min(max_single_adjustment_kcal, adj))
    new_tdee = round(current_tdee + adj, 0)
    confidence = "wysoka" if abs(gap) > _CFG.confidence_gap_kg_per_week else "średnia"
    return TDEEAdjustment(
        old_tdee=current_tdee, new_tdee=new_tdee,
        weekly_trend_kg=round(weekly, 3), adjustment_kcal=round(adj, 0),
        confidence=confidence,
    )


@dataclass
class TDEEAdjustment:
    """(Rezerwa) wynik korekty TDEE z trendu wagi."""

    old_tdee: float
    new_tdee: float
    weekly_trend_kg: float
    adjustment_kcal: float
    confidence: str


def compute_protein_target(
    bodyweight_kg: float,
    phase: str = "utrzymanie",   # "deficyt" | "utrzymanie" | "nadwyżka"
) -> float:
    """
    Skalowanie celu białka do wagi zamiast sztywnej wartości.
    Deficyt wymaga górnej granicy (ochrona masy mięśniowej).
    """
    ranges = _CFG.protein_g_per_kg
    g_per_kg = float(ranges.get(phase, 1.8))
    return round(bodyweight_kg * g_per_kg, 0)
