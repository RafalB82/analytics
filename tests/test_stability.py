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

    def test_short_history_returns_none_instead_of_false_stable(self):
        # 10 dni: avg7/avg14/avg28 zaciskają się do TEJ SAMEJ średniej, więc
        # spread między oknami to 0 artefaktem, a nie miarą stabilności.
        # Kategoria wychodziła wtedy bezwarunkowo "Stable" dla dowolnie
        # chaotycznych danych — brak podstaw, więc brak oceny (None).
        # Pipeline serializuje to jako null (`if ctx.activity_stability`).
        assert activity_stability(_steady(n=10)) is None

    def test_wildly_variable_short_series_is_not_reported_stable(self):
        # regresja: chaotyczne dane poniżej 14 dni nie mogą dać "Stable"
        chaotic = [100, 300, 50, 400, 20, 350, 60, 10, 90, 25, 400, 33, 70]
        assert activity_stability(chaotic) is None
        assert activity_stability([100, 200]) is None

    def test_two_saturated_windows_are_enough(self):
        # 14 dni nasyca okna 14 i 7 -> da się już porównać dwie różne średnie
        s = activity_stability([1000.0] * 7 + [5000.0] * 7)
        assert s is not None
        assert s.variation > 0
        assert s.category != "Stable"
        assert s.avg_7d > 0
        assert s.avg_14d > 0
        assert s.avg_28d > 0
