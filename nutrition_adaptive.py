"""
nutrition_adaptive.py
Pętla zwrotna TDEE (korekta na podstawie faktycznego trendu wagi)
oraz skalowanie celu białka do masy ciała i fazy.

Zależności: numpy
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import numpy as np

KCAL_PER_KG_FAT = 7700  # standardowa wartość energetyczna 1 kg tkanki tłuszczowej


@dataclass
class WeightPoint:
    day: date
    weight_kg: float


@dataclass
class TDEEAdjustment:
    old_tdee: float
    new_tdee: float
    weekly_trend_kg: float
    adjustment_kcal: float
    confidence: str          # "niska" | "średnia" | "wysoka" — zależna od liczby punktów i szumu


def compute_weight_trend(
    series: list[WeightPoint],
    window_days: int = 14,
    min_points: int = 8,
) -> float | None:
    """
    Trend liniowy wagi w kg/dzień na oknie window_days.
    Waga dzień do dnia jest bardzo zaszumiona (woda, glikogen, jedzenie
    w jelitach) — stąd wymóg min_points i dłuższe okno (14d) niż przy HRV.
    Zwraca None jeśli za mało danych.
    """
    recent = series[-window_days:]
    if len(recent) < min_points:
        return None

    x = np.arange(len(recent))
    y = np.array([p.weight_kg for p in recent])
    slope, _ = np.polyfit(x, y, 1)
    return round(float(slope), 4)  # kg/dzień


def adjust_tdee(
    current_tdee: float,
    weight_trend_kg_per_day: float | None,
    target_trend_kg_per_week: float = 0.0,
    max_single_adjustment_kcal: float = 250,
) -> TDEEAdjustment:
    """
    Korekta TDEE na podstawie rzeczywistej zmiany wagi vs cel.

    Przykład: cel utrzymania (target=0), realny trend -0.3 kg/tydz
    -> ubytek energii = 0.3 * 7700 / 7 = 330 kcal/dzień niedoszacowania
    -> podnieś TDEE o tyle (capped przez max_single_adjustment_kcal,
    żeby jedna zaszumiona korekta nie wywróciła configu o 500+ kcal).

    Uruchamiać jako zadanie cron raz na 1-2 tygodnie, NIE codziennie —
    szum dobowy wagi zrobi z tego losowy generator.
    """
    if weight_trend_kg_per_day is None:
        return TDEEAdjustment(
            old_tdee=current_tdee,
            new_tdee=current_tdee,
            weekly_trend_kg=0.0,
            adjustment_kcal=0.0,
            confidence="niska",
        )

    weekly_trend = weight_trend_kg_per_day * 7
    trend_gap = weekly_trend - target_trend_kg_per_week   # kg/tydz odchylenia od celu
    raw_adjustment = (trend_gap * KCAL_PER_KG_FAT) / 7      # kcal/dzień

    # jeśli waga rośnie szybciej niż cel -> TDEE w rzeczywistości wyższe niż
    # zakładane tylko gdy jemy MNIEJ niż myślimy; tu zakładamy że target_kcal
    # było w miarę stabilne w oknie, więc korekta idzie w stronę przeciwną
    # do nadwyżki/deficytu
    adjustment = -raw_adjustment
    adjustment = max(-max_single_adjustment_kcal, min(max_single_adjustment_kcal, adjustment))

    new_tdee = round(current_tdee + adjustment, 0)

    confidence = "wysoka" if abs(trend_gap) > 0.1 else "średnia"

    return TDEEAdjustment(
        old_tdee=current_tdee,
        new_tdee=new_tdee,
        weekly_trend_kg=round(weekly_trend, 3),
        adjustment_kcal=round(adjustment, 0),
        confidence=confidence,
    )


def compute_protein_target(
    bodyweight_kg: float,
    phase: str = "utrzymanie",   # "deficyt" | "utrzymanie" | "nadwyżka"
) -> float:
    """
    Skalowanie celu białka do wagi zamiast sztywnej wartości.
    Zakresy oparte o literaturę dla sportów siłowych (Helms i in.,
    ISSN position stand) — deficyt wymaga górnej granicy ze względu
    na ochronę masy mięśniowej.
    """
    ranges = {
        "deficyt": 2.2,
        "utrzymanie": 1.8,
        "nadwyżka": 1.8,
    }
    g_per_kg = ranges.get(phase, 1.8)
    return round(bodyweight_kg * g_per_kg, 0)
