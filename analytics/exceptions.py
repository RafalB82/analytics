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


class InsufficientDataError(AnalyticsError):
    """Za mało danych do przeprowadzenia analizy.

    Fallback-owalny: orchestrator może zwrócić status 'fallback' zamiast błędu.
    """


class MissingBaselineError(InsufficientDataError):
    """Brak wystarczającej historii do wyliczenia baseline."""


# Uwaga: nie ma tu `InvalidWorkoutError` ani `ConfigError`. Oba były
# zadeklarowane, ale nigdzie nie rzucane — żaden kod (poza samym tym modułem)
# ich nie wspominał. Obie zapowiadały tryb fail-fast, którego ten pipeline
# świadomie nie ma: pojedynczy niespójny workout jest filtrowany w
# build_daily_load_series, a nieznana faza celu dostaje wartość domyślną.
# Każdy `except InvalidWorkoutError` opierałby się na ścieżce, która nie
# może się wykonać. Usunięte, bo deklaracja bez rzucenia jest myląca.


__all__ = [
    "AnalyticsError",
    "InvalidMetricError",
    "MissingBaselineError",
    "InsufficientDataError",
]
