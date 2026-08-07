"""
baseline.py
Rolling baseline (EWMA) + wykrywanie trendu dla szeregów fizjologicznych
(HRV, RHR, temperatura nadgarstka, waga).

Zależności: numpy
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from .config.settings import BASELINE as _CFG
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class MetricPoint:
    day: date
    value: float
    is_training_day: bool = False


@dataclass
class BaselineResult:
    baseline: float
    deviation_pct: float          # (aktualna - baseline) / baseline * 100
    deviation_abs: float
    n_points_used: int


@dataclass
class TrendResult:
    slope: float                  # jednostka / dzień
    r_squared: float
    direction: str                # "rosnący" | "spadający" | "stabilny"
    reliable: bool                # czy R^2 i liczba punktów wystarczające


def compute_ewma_baseline(
    series: list[MetricPoint],
    alpha: float | None = None,
    min_points: int | None = None,
) -> BaselineResult | None:
    """
    Wykładniczo ważona średnia krocząca jako baseline.
    Nowsze obserwacje mają większą wagę -> baseline "podąża" za trwałą
    adaptacją fizjologiczną zamiast być sztywnym oknem z przeszłości.

    series musi być posortowane chronologicznie rosnąco, ostatni element
    = dzień, dla którego liczysz odchylenie (nie wchodzi do baseline).
    """
    if alpha is None:
        alpha = _CFG.ewma_alpha
    if min_points is None:
        min_points = _CFG.min_points
    if len(series) < min_points + 1:
        logger.debug("za mało punktów do baseline: %d (min %d)", len(series), min_points + 1)
        return None

    history = series[:-1]          # wszystko poza dniem bieżącym
    current = series[-1].value

    logger.debug("baseline EWMA: %d punktów historii (min %d)", len(history), min_points)

    values = np.array([p.value for p in history], dtype=float)
    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma

    deviation_abs = current - ewma
    deviation_pct = (deviation_abs / ewma) * 100 if ewma != 0 else 0.0

    return BaselineResult(
        baseline=round(ewma, 2),
        deviation_pct=round(deviation_pct, 2),
        deviation_abs=round(deviation_abs, 2),
        n_points_used=len(history),
    )


def baseline_by_context(
    series: list[MetricPoint],
    current_is_training_day: bool,
    alpha: float | None = None,
    min_points: int | None = None,
) -> BaselineResult | None:
    """
    Osobny baseline dla dni treningowych i nietreningowych.
    RHR/HRV fizjologicznie różnią się w zależności od tego, czy dzień
    poprzedzał trening czy odpoczynek — mieszanie ich w jednym baseline
    zaszumia sygnał.
    """
    if min_points is None:
        min_points = _CFG.min_points_context
    filtered = [p for p in series[:-1] if p.is_training_day == current_is_training_day]
    filtered.append(series[-1])
    return compute_ewma_baseline(filtered, alpha=alpha, min_points=min_points)


def compute_trend_slope(
    series: list[MetricPoint],
    window_days: int | None = None,
    smoothing_window: int | None = None,
    min_r_squared: float | None = None,
) -> TrendResult | None:
    """
    Regresja liniowa na oknie window_days (domyślnie ostatnie 7 dni),
    po wygładzeniu medianą ruchomą (smoothing_window) żeby odciąć szum
    dobowy HRV.

    Zwraca nachylenie w jednostkach metryki / dzień oraz klasyfikację
    kierunku. `reliable=False` gdy R^2 zbyt niski (dane zbyt szumiące,
    trend niepewny) — w takim wypadku traktuj jako "stabilny" / brak
    sygnału, nie ufaj kierunkowi.
    """
    if window_days is None:
        window_days = _CFG.trend_window_days
    if smoothing_window is None:
        smoothing_window = _CFG.trend_smoothing
    if min_r_squared is None:
        min_r_squared = _CFG.trend_min_r_squared
    recent = series[-window_days:]
    if len(recent) < max(4, smoothing_window + 2):
        return None

    raw = np.array([p.value for p in recent], dtype=float)

    # wygładzenie medianą ruchomą
    smoothed = np.array([
        np.median(raw[max(0, i - smoothing_window + 1):i + 1])
        for i in range(len(raw))
    ])

    x = np.arange(len(smoothed))
    slope, intercept = np.polyfit(x, smoothed, 1)
    pred = slope * x + intercept
    ss_res = np.sum((smoothed - pred) ** 2)
    ss_tot = np.sum((smoothed - np.mean(smoothed)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot != 0 else 0.0

    reliable = bool(r_squared >= min_r_squared)

    if not reliable:
        direction = "stabilny"
    elif slope > 0:
        direction = "rosnący"
    elif slope < 0:
        direction = "spadający"
    else:
        direction = "stabilny"

    return TrendResult(
        slope=round(float(slope), 4),
        r_squared=round(float(r_squared), 3),
        direction=direction,
        reliable=reliable,
    )


def detect_baseline_shift(
    short_series: list[MetricPoint],
    long_series: list[MetricPoint],
    threshold_pct: float | None = None,
    alpha_short: float | None = None,
    alpha_long: float | None = None,
) -> bool:
    """
    Porównanie EWMA(krótkie, ~7d) vs EWMA(długie, ~28d).
    Jeśli różnica > threshold_pct, oznacza to trwałe przesunięcie
    baseline (np. adaptacja treningowa po miesiącu progresji), a nie
    dobowe odchylenie. Użyj tego jako flagę do ręcznego review configu
    baseline, nie jako automatyczną korektę bez nadzoru.
    """
    if threshold_pct is None:
        threshold_pct = _CFG.shift_threshold_pct
    if alpha_short is None:
        alpha_short = _CFG.shift_alpha_short
    if alpha_long is None:
        alpha_long = _CFG.shift_alpha_long
    short_bl = compute_ewma_baseline(short_series, alpha=alpha_short, min_points=4)
    long_bl = compute_ewma_baseline(long_series, alpha=alpha_long, min_points=10)

    if short_bl is None or long_bl is None or long_bl.baseline == 0:
        return False

    diff_pct = abs(short_bl.baseline - long_bl.baseline) / long_bl.baseline * 100
    return bool(diff_pct > threshold_pct)
