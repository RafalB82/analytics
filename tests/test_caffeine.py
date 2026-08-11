"""Testy analytics.caffeine — estymacja kofeiny z liczby kaw."""
from __future__ import annotations

from analytics.caffeine import MG_PER_COFFEE, apply_caffeine_to_daily, caffeine_mg_from_coffee


class TestCaffeineMgFromCoffee:
    def test_three_coffees(self):
        assert caffeine_mg_from_coffee(3) == 3 * MG_PER_COFFEE

    def test_zero(self):
        assert caffeine_mg_from_coffee(0) == 0.0

    def test_none(self):
        assert caffeine_mg_from_coffee(None) == 0.0

    def test_negative(self):
        assert caffeine_mg_from_coffee(-1) == 0.0

    def test_float_count(self):
        assert caffeine_mg_from_coffee(3.5) == round(3.5 * MG_PER_COFFEE, 1)

    def test_bad_type(self):
        assert caffeine_mg_from_coffee("garbage") == 0.0

    def test_constant_value(self):
        assert MG_PER_COFFEE == 70.0


class TestApplyCaffeineToDaily:
    def test_adds_mg_per_day(self):
        daily = [
            {"day": "2026-08-05", "kcal": 2406.0, "coffee_count": 3},
            {"day": "2026-08-06", "kcal": 2500.0, "coffee_count": 0},
        ]
        out = apply_caffeine_to_daily(daily)
        assert out[0]["caffeine_mg"] == 210.0
        assert out[1]["caffeine_mg"] == 0.0
        # zachowuje resztę pól
        assert out[0]["day"] == "2026-08-05"
        assert out[0]["kcal"] == 2406.0
        assert out[0]["coffee_count"] == 3

    def test_missing_coffee_count_defaults_zero(self):
        daily = [{"day": "2026-08-05", "kcal": 100.0}]
        out = apply_caffeine_to_daily(daily)
        assert out[0]["caffeine_mg"] == 0.0

    def test_empty(self):
        assert apply_caffeine_to_daily([]) == []

    def test_does_not_mutate_input(self):
        daily = [{"day": "2026-08-05", "kcal": 100.0, "coffee_count": 2}]
        snapshot = [dict(d) for d in daily]
        apply_caffeine_to_daily(daily)
        assert daily == snapshot
