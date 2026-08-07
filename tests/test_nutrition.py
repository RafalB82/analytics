"""Testy modułu odżywiania: TDEE z aktywności, cel kaloryczny wg celu, białko."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from analytics.nutrition_adaptive import (
    DailyEnergy,
    TDEEAdjustment,
    TDEEEstimate,
    adjust_tdee,
    compute_long_window_tdee,
    compute_protein_target,
    compute_tdee,
)


def _energy(values_basal: list[float] | None = None,
            values_active: list[float] | None = None,
            exercise: list[float] | None = None,
            start: date | None = None) -> list[DailyEnergy]:
    """Buduje serię DailyEnergy (kJ). Bazowa: basal=15000, active=4000 na dzień."""
    n = max(len(values_basal or []), len(values_active or []), 1)
    start = start or date(2026, 8, 1)
    basal = (values_basal or [15000.0] * n)
    active = (values_active or [4000.0] * n)
    ex = (exercise or [60.0] * n)
    return [
        DailyEnergy(
            day=start + timedelta(days=i),
            basal_kj=basal[min(i, len(basal) - 1)],
            active_kj=active[min(i, len(active) - 1)],
            exercise_min=ex[min(i, len(ex) - 1)],
            stand_min=300.0,
            physical_effort=3.0,
        )
        for i in range(n)
    ]


class TestComputeTdee:
    def test_tdee_is_basal_plus_active(self):
        # basal=15000 kJ=3585 kcal, active=4000 kJ=956 kcal -> TDEE ≈ 4541
        est = compute_tdee(_energy(), goal="utrzymanie")
        assert isinstance(est, TDEEEstimate)
        expected = round(15000 / 4.184 + 4000 / 4.184, 0)
        assert est.tdee_kcal == expected

    def test_utrzymanie_no_margin(self):
        est = compute_tdee(_energy(), goal="utrzymanie")
        assert est.margin_pct == 0.0
        assert est.target_kcal == est.tdee_kcal

    def test_redukcja_negative_margin(self):
        est = compute_tdee(_energy(), goal="redukcja")
        assert est.margin_pct == pytest.approx(-0.15)
        assert est.target_kcal == round(est.tdee_kcal * (1 - 0.15), 0)

    def test_masa_positive_margin(self):
        est = compute_tdee(_energy(), goal="masa")
        assert est.margin_pct == pytest.approx(0.10)
        assert est.target_kcal == round(est.tdee_kcal * (1 + 0.10), 0)

    def test_protein_from_bodyweight(self):
        est = compute_tdee(_energy(), goal="utrzymanie", bodyweight_kg=70.0)
        assert est.protein_g == round(70 * 1.8, 0)

    def test_protein_none_without_bodyweight(self):
        est = compute_tdee(_energy(), goal="utrzymanie")
        assert est.protein_g is None

    def test_activity_signals(self):
        # 2 dni z jawną basal/active, rózne minuty ćwiczeń
        est = compute_tdee(
            _energy(values_basal=[15000.0, 15000.0], values_active=[4000.0, 4000.0],
                    exercise=[30.0, 90.0]),
            goal="utrzymanie",
        )
        assert est.avg_exercise_min == 60.0
        assert est.training_days_ratio == 1.0

    def test_raises_without_energy_data(self):
        # same dni bez basal/active -> ValueError
        from analytics.nutrition_adaptive import DailyEnergy
        series = [DailyEnergy(day=date(2026, 8, 1))]
        with pytest.raises(ValueError):
            compute_tdee(series, goal="utrzymanie")

    def test_window_respected(self):
        # tylko ostatnie 3 dni wchodzą do średniej
        n = 10
        basal = [10000.0] * n
        active = [2000.0] * n
        est = compute_tdee(_energy(basal, active), goal="utrzymanie", window_days=3)
        assert est.window_days == 3


class TestComputeLongWindowTdee:
    def test_returns_none_with_too_few(self):
        assert compute_long_window_tdee(_energy(values_basal=[1.0] * 3, values_active=[1.0] * 3)) is None

    def test_returns_estimate_with_enough(self):
        est = compute_long_window_tdee(_energy(values_basal=[1.0] * 30, values_active=[1.0] * 30))
        assert est is not None
        assert est.window_days == 28


class TestComputeWeightTrend:
    """Rezerwa: trend wagi na wypadek wielopunktowej serii (obecnie waga = punkt)."""

    def _w(self, values):
        return [SimpleNamespace(day=date(2026, 7, 1) + timedelta(days=i), weight_kg=v)
                for i, v in enumerate(values)]

    def test_returns_none_when_too_few(self):
        from analytics.nutrition_adaptive import compute_weight_trend
        assert compute_weight_trend(self._w([70.0] * 5), min_points=8) is None

    def test_rising_weight_positive_slope(self):
        from analytics.nutrition_adaptive import compute_weight_trend
        trend = compute_weight_trend(self._w([70 + i * 0.1 for i in range(10)]), min_points=8)
        assert trend is not None and trend > 0


class TestAdjustTdee:
    """Rezerwa: korekta celu z trendu wagi (gdy trend będzie dostępny)."""

    def test_no_trend_no_change(self):
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=None)
        assert isinstance(res, TDEEAdjustment)
        assert res.new_tdee == 2260
        assert res.adjustment_kcal == 0.0
        assert res.confidence == "niska"

    def test_cap_applied(self):
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=-1.0)
        assert abs(res.adjustment_kcal) <= 250

    def test_confidence_high_for_large_gap(self):
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=-0.5)
        assert res.confidence == "wysoka"


class TestComputeProteinTarget:
    def test_utrzymanie(self):
        assert compute_protein_target(70.0, phase="utrzymanie") == round(70 * 1.8, 0)

    def test_deficyt(self):
        assert compute_protein_target(70.0, phase="deficyt") == round(70 * 2.2, 0)

    def test_unknown_default(self):
        assert compute_protein_target(70.0, phase="xyz") == round(70 * 1.8, 0)
