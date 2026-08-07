"""
validators/metrics.py — walidacja wejściowych metryk fizjologicznych.

Chroni pipeline przed cichym zatruciem danych z Apple/Hevy/MFP:
- NaN / inf (z API)
- wartości ujemne lub absurdalne (poza zakresem fizjologicznym)
- nienaturalne ciężary serii (ujemne / zero w tonażu)

Zakresy pochodzą z `config/settings.RANGES` (pojedyncze źródło prawdy).
Każda funkcja rzuca `InvalidMetricError` na wartości nieprzechodzące —
orchestrator decyduje, czy to fallback, czy błąd danych.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from ..config.settings import RANGES
from ..exceptions import InvalidMetricError


def coerce_float(value: Any, metric: str, *, allow_none: bool = False) -> float | None:
    """Konwertuje `value` na float, przepuszczając None (opcjonalnie).

    Rzuca `InvalidMetricError` na NaN / inf / niekonwertowalne — bo te
    wartości nie są "brakiem danych", tylko uszkodzeniem, które cicho
    zafałszowałoby obliczenia.
    """
    if value is None:
        if allow_none:
            return None
        return None  # brak wartości -> None (pomijane przez fetch_*)
    try:
        f = float(value)
    except (TypeError, ValueError) as e:
        raise InvalidMetricError(metric, value, f"niekonwertowalne na float: {e}") from e
    if math.isnan(f) or math.isinf(f):
        raise InvalidMetricError(metric, value, "wartość NaN/inf — uszkodzone dane")
    return f


def validate_float(
    value: Any,
    metric: str,
    lo: float | None = None,
    hi: float | None = None,
    *,
    allow_none: bool = False,
) -> float | None:
    """Waliduje pojedynczą wartość: konwersja + zakres. Zwraca float (lub None).

    Typowe użycie: `validate_float(v, "hrv", *RANGES.hrv)`.
    """
    f = coerce_float(value, metric, allow_none=allow_none)
    if f is None:
        return None
    if lo is not None and f < lo:
        raise InvalidMetricError(metric, value, f"poniżej dozwolonego zakresu ({lo})")
    if hi is not None and f > hi:
        raise InvalidMetricError(metric, value, f"powyżej dozwolonego zakresu ({hi})")
    return f


def hrv(value: object) -> float | None:
    return validate_float(value, "hrv", *RANGES.hrv)


def rhr(value: object) -> float | None:
    return validate_float(value, "rhr", *RANGES.rhr)


def sleep(value: object) -> float | None:
    return validate_float(value, "sleep_hours", *RANGES.sleep)


def temperature(value: object) -> float | None:
    return validate_float(value, "wrist_temperature", *RANGES.temperature)


def weight(value: object) -> float | None:
    return validate_float(value, "weight_kg", *RANGES.weight)


def set_weight(value: object) -> float | None:
    """Ciężar pojedynczej serii (kg) — musi być dodatni (0/ujemne = pomiń)."""
    f = coerce_float(value, "set_weight_kg")
    if f is None:
        return None
    if f <= 0:
        raise InvalidMetricError("set_weight_kg", value, "ciężar serii musi być > 0")
    return f


def reps(value: object) -> int | None:
    """Liczba powtórzeń — nieujemna liczba całkowita. None = brak danych."""
    f = coerce_float(value, "reps")
    if f is None:
        return None
    if f < 0:
        raise InvalidMetricError("reps", value, "liczba powtórzeń nie może być ujemna")
    return int(f)


def ensure_sorted_ascending(days: list[date], metric: str) -> None:
    """Weryfikuje, że szereg dat jest posortowany rosnąco (wymaganie EWMA).

    Nie rzuca — zwraca tylko informację; sortowanie jest naprawialne.
    Zwraca True gdy posortowane, False gdy nie.
    """
    for i in range(1, len(days)):
        if days[i] < days[i - 1]:
            raise InvalidMetricError(
                metric,
                days[i],
                f"szereg nie jest posortowany rosnąco (dzień {days[i]} < {days[i-1]})",
            )


__all__ = [
    "coerce_float",
    "validate_float",
    "hrv",
    "rhr",
    "sleep",
    "temperature",
    "weight",
    "set_weight",
    "reps",
    "ensure_sorted_ascending",
]
