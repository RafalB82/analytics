"""Testy modułu stability (faza 3.0 — Activity Stability)."""
from __future__ import annotations

from analytics.stability import activity_stability


def _steady(n=28, base=10000.0):
    return [base + (i % 3) * 50 for i in range(n)]


def _variable(n=28, base=10000.0):
    # Połowa historii niska, połowa wysoka -> okna 7/14/28 różnią się,
    # więc variation jest duże (Highly Variable).
    half = n // 2
    return [base for _ in range(half)] + [base + 12000 for _ in range(n - half)]


class TestActivityStability:
    def test_stable_series(self):
        s = activity_stability(_steady())
        assert s is not None
        assert s.category == "Stable"
        assert s.variation < 0.10

    def test_highly_variable_series(self):
        s = activity_stability(_variable())
        assert s is not None
        assert s.category == "Highly Variable"
        assert s.variation > 0.25

    def test_none_on_empty(self):
        assert activity_stability([]) is None

    def test_none_on_too_few(self):
        # < 2 punktów w najkrótszym oknie -> None
        assert activity_stability([10000.0]) is None

    def test_avg_windows_computed(self):
        s = activity_stability(_steady(n=28))
        assert s.avg_7d > 0
        assert s.avg_14d > 0
        assert s.avg_28d > 0
        # 28-dniowa średnia powinna być zbliżona do bazowej (dla steady)
        assert abs(s.avg_28d - 10000.0) < 100

    def test_to_dict_keys(self):
        s = activity_stability(_steady())
        d = s.to_dict()
        for k in ("avg_7d", "avg_14d", "avg_28d", "variation", "category"):
            assert k in d

    def test_short_history_uses_available(self):
        # 10 dni -> wszystkie okna liczą się z dostępnych punktów
        # (_avg_last bierze średnią z tego, co jest; nie 0.0)
        s = activity_stability(_steady(n=10))
        assert s is not None
        assert s.avg_7d > 0
        assert s.avg_14d > 0
        assert s.avg_28d > 0
        assert s.category  # nie rzuca
