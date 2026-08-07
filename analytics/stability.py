"""
stability.py — Activity Stability (faza 3.0).

Mierzy zmienność aktywności energetycznej w czasie (avg 7/14/28 dni) i
kategoryzuje ją jako Stable / Moderately Variable / Highly Variable.

To czyste, deterministyczne funkcje (bez I/O), kompatybilne z resztą
analytics. Wynik może być używany m.in. jako składowa Confidence Score
(stabilność) oraz jako niezależny wskaźnik w raporcie.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config.settings import STABILITY as _CFG


@dataclass(frozen=True)
class ActivityStability:
    """Miary zmienności aktywności + kategoria."""

    avg_7d: float
    avg_14d: float
    avg_28d: float
    variation: float      # (max(avg)-min(avg))/max(avg,1)  — prosty spread
    category: str         # "Stable" | "Moderately Variable" | "Highly Variable"

    def to_dict(self) -> dict:
        return {
            "avg_7d": round(self.avg_7d, 1),
            "avg_14d": round(self.avg_14d, 1),
            "avg_28d": round(self.avg_28d, 1),
            "variation": round(self.variation, 3),
            "category": self.category,
        }


def _avg_last(values: list[float], n: int) -> float | None:
    """Średnia ostatnich n wartości (od końca listy). None gdy za mało danych."""
    if not values or n <= 0:
        return None
    window = values[-n:]
    if len(window) < 2:  # potrzeba min. 2 punktów do sensownej średniej
        return None
    return sum(window) / len(window)


def activity_stability(values: list[float]) -> ActivityStability | None:
    """Kategoryzuje zmienność aktywności z pojedynczej serii dziennej.

    Args:
        values: dzienna aktywność (np. basal+active w kJ albo kcal) —
            oczekiwana jako lista uporządkowana chronologicznie (najstarszy->najnowszy).

    Returns:
        ActivityStability, albo None gdy danych za mało (< 7 dni / < 2 w najkrótszym oknie).
    """
    if not values:
        return None

    avg7 = _avg_last(values, 7)
    avg14 = _avg_last(values, 14)
    avg28 = _avg_last(values, 28)

    if avg7 is None:
        return None

    # fallback dla krótszych okien: bierzemy dostępne średnie
    avgs = [a for a in (avg28, avg14, avg7) if a is not None]
    ref = max(max(avgs), 1.0)
    variation = (max(avgs) - min(avgs)) / ref

    if variation < _CFG.stable_max_variation:
        category = "Stable"
    elif variation < _CFG.moderate_max_variation:
        category = "Moderately Variable"
    else:
        category = "Highly Variable"

    return ActivityStability(
        avg_7d=avg7,
        avg_14d=avg14 if avg14 is not None else 0.0,
        avg_28d=avg28 if avg28 is not None else 0.0,
        variation=variation,
        category=category,
    )


__all__ = ["ActivityStability", "activity_stability"]
