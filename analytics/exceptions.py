"""
exceptions.py — domenowe wyjątki pakietu analytics.

Zastępują wieloznaczne zwracanie `None` i gołe `ValueError` w modułach
analitycznych. Dzielą błędy na dwie klasy semantyczne:

- *brak danych / za mało danych*  -> `InsufficientDataError` (fallback, nie fatal)
- *dane niepoprawne / nieprzechodzące walidacji* -> `InvalidMetricError` (błąd danych,
  NIE chowaj w fallback — sygnalizuj problem ze źródłem)

Orchestrator (run_analysis) decyduje przy granicy: łapie `InsufficientDataError`
i zwraca fallback, a `InvalidMetricError` propaguje jako błąd.
"""

from __future__ import annotations


class AnalyticsError(Exception):
    """Bazowy wyjątek całego pakietu. Nie używany bezpośrednio."""


class InvalidMetricError(AnalyticsError):
    """Wartość metryki nie przechodzi walidacji (NaN, inf, poza zakresem).

    Oznacza problem z danymi źródłowymi — nie chowaj jako fallback.
    """

    def __init__(self, metric: str, value: object, message: str | None = None):
        self.metric = metric
        self.value = value
        super().__init__(
            message or f"Nieprawidłowa wartość metryki '{metric}': {value!r}"
        )


class MissingBaselineError(AnalyticsError):
    """Brak wystarczającej historii do wyliczenia baseline.

    Szczególny przypadek braku danych — używany, gdy wymagane jest minimum
    punktów historycznych, których nie ma.
    """


class InsufficientDataError(AnalyticsError):
    """Za mało danych do przeprowadzenia analizy.

    Fallback-owalny: orchestrator może zwrócić status 'fallback' zamiast błędu.
    """


class InvalidWorkoutError(AnalyticsError):
    """Trening z Hevy ma niespójne/pominięte dane uniemożliwiające ACWR."""


class ConfigError(AnalyticsError):
    """Błąd konfiguracji (np. niepoprawna strefa, zła faza w settings)."""


__all__ = [
    "AnalyticsError",
    "InvalidMetricError",
    "MissingBaselineError",
    "InsufficientDataError",
    "InvalidWorkoutError",
    "ConfigError",
]
