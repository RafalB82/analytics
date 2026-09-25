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

#: Klucze payloadu, które muszą być listą. Wszystkie są konsumowane przez
#: build_*/compute_* przez iterację i .get() na elementach, więc typ inny niż
#: lista dawał nieobsłużony AttributeError zamiast InvalidMetricError — CLI
#: wypisywał wtedy traceback zamiast JSON-a z błędem.
_LIST_FIELDS = (
    "apple_daily",
    "hevy_workouts",
    "apple_workouts",
    "cardio_sessions",
    "apple_temp",
    "mfp_daily_kcal",
)


def _as_list(payload: dict, name: str) -> list:
    """Wartość listy albo [] dla braku. `None`/`{}` NIE jest ciche, pustą listą:
    jawne `None` znaczy 'brak', ale np. `{}` to zły typ i musi zostać odrzucone."""
    value = payload.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidMetricError(name, type(value).__name__, "oczekiwano listy")
    return value


def validate_input(payload: dict) -> tuple[str, date, dict, list, list, list, list, list, list]:
    """Weryfikuje źródło i obecność danych. Zwraca uporządkowane składowe.

    Zwraca: (source, target, params, apple_daily, hevy_workouts, apple_workouts,
     cardio_sessions, apple_temp, mfp_daily_kcal).
    """
    source = payload.get("source")
    if source not in ALLOWED_SOURCES:
        raise InvalidMetricError(
            "source", source, f"oczekiwano '{'apple+hevy+mfp'}', otrzymano '{source}'"
        )

    lists = {name: _as_list(payload, name) for name in _LIST_FIELDS}
    if not lists["apple_daily"]:
        raise InsufficientDataError("missing_apple_daily: brak danych z Apple")

    target = _parse_target(payload.get("target_date"))
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise InvalidMetricError("params", type(params).__name__, "oczekiwano obiektu")

    return (
        source,
        target,
        params,
        lists["apple_daily"],
        lists["hevy_workouts"],
        lists["apple_workouts"],
        lists["cardio_sessions"],
        lists["apple_temp"],
        lists["mfp_daily_kcal"],
    )


def _parse_target(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError as e:
        raise InvalidMetricError("target_date", s, f"niepoprawna data: {e}") from e
