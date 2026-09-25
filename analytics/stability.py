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

from .config import settings


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


def _saturated_windows(values: list[float], windows=(28, 14, 7)) -> list[float]:
    """Średnie z okien, które są FAKTYCZNIE nasycone danymi.

    `_avg_last` zaciska okno do dostępnej listy, więc dla serii krótszej niż
    14 dni `avg7`, `avg14` i `avg28` to ta sama liczba. Wtedy spread między
    oknami wynosi 0 nie dlatego, że aktywność jest stabilna, ale dlatego, że
    porównujemy wartość z samą sobą — i kategoria wychodziła bezwarunkowo
    "Stable" dla dowolnie chaotycznych danych. Do oceny używamy tylko okien
    nasyconych, czyli takich, do których starczyło punktów.
    """
    return [
        avg
        for n, avg in ((n, _avg_last(values, n)) for n in windows)
        if avg is not None and len(values) >= n
    ]


def activity_stability(values: list[float]) -> ActivityStability | None:
    """Kategoryzuje zmienność aktywności z pojedynczej serii dziennej.

    Args:
        values: dzienna aktywność (np. basal+active w kJ albo kcal) —
            oczekiwana jako lista uporządkowana chronologicznie (najstarszy->najnowszy).

    Returns:
        ActivityStability, albo None gdy danych za mało do porównania dwóch
        okien — czyli mniej niż 14 dni, bo wtedy wszystkie okna zaciskają się do
        tej samej średniej, a spread między nimi jest artefaktem, nie miarą
        stabilności. Wymagamy >= 2 okien nasyconych.
    """
    if not values:
        return None

    avg7 = _avg_last(values, 7)
    if avg7 is None:
        return None

    saturated = _saturated_windows(values)
    if len(saturated) < 2:
        return None

    ref = max(max(saturated), 1.0)
    variation = (max(saturated) - min(saturated)) / ref

    if variation < settings.STABILITY.stable_max_variation:
        category = "Stable"
    elif variation < settings.STABILITY.moderate_max_variation:
        category = "Moderately Variable"
    else:
        category = "Highly Variable"

    return ActivityStability(
        avg_7d=avg7,
        avg_14d=_avg_last(values, 14) or 0.0,
        avg_28d=_avg_last(values, 28) or 0.0,
        variation=variation,
        category=category,
    )


__all__ = ["ActivityStability", "activity_stability"]
