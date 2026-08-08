"""
validators/input.py — walidacja wejścia analizy (source + obecność danych).

Oddzielona od run_analysis (thin CLI) oraz pipeline (orkiestracja). Pipeline
woła validate_input z tego modułu; run_analysis ich tu nie importuje (brak
cyklu). Stałe liczbowe dziedzinowe (MIN_HRV_POINTS, ACWR_LOOKBACK_DAYS)
żyją w modułach, które je konsumują — nie tutaj.
"""

from __future__ import annotations

from datetime import date

from ..exceptions import InsufficientDataError, InvalidMetricError

ALLOWED_SOURCES = {"apple+hevy+mfp"}


def validate_input(payload: dict) -> tuple[str, date, dict, list, list, list, list, list, list]:
    """Weryfikuje źródło i obecność danych. Zwraca uporządkowane składowe.

    Zwraca: (source, target, params, apple_daily, hevy_workouts, apple_workouts,
    cardio_sessions, mfp_weight, apple_temp).
    """
    source = payload.get("source")
    if source not in ALLOWED_SOURCES:
        raise InvalidMetricError(
            "source", source, f"oczekiwano '{'apple+hevy+mfp'}', otrzymano '{source}'"
        )

    apple_daily = payload.get("apple_daily", [])
    if not apple_daily:
        raise InsufficientDataError("missing_apple_daily: brak danych z Apple")

    target = _parse_target(payload.get("target_date"))
    params = payload.get("params", {})
    hevy_workouts = payload.get("hevy_workouts", [])
    apple_workouts = payload.get("apple_workouts", [])
    cardio_sessions = payload.get("cardio_sessions", [])
    mfp_weight = payload.get("mfp_weight") or []
    apple_temp = payload.get("apple_temp") or []

    return (source, target, params, apple_daily, hevy_workouts, apple_workouts,
            cardio_sessions, mfp_weight, apple_temp)


def _parse_target(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError as e:
        raise InvalidMetricError("target_date", s, f"niepoprawna data: {e}") from e
