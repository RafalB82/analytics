"""Testy modułu odżywiania adaptacyjnego (TDEE, trend wagi, białko)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.nutrition_adaptive import (
    TDEEAdjustment,
    WeightPoint,
    adjust_tdee,
    compute_protein_target,
    compute_weight_trend,
)


def _weights(values: list[float], start: date | None = None) -> list[WeightPoint]:
    start = start or date(2026, 7, 1)
    return [WeightPoint(day=start + timedelta(days=i), weight_kg=v) for i, v in enumerate(values)]


class TestComputeWeightTrend:
    def test_returns_none_when_too_few(self):
        assert compute_weight_trend(_weights([70.0] * 5), min_points=8) is None

    def test_rising_weight_positive_slope(self):
        series = _weights([70 + i * 0.1 for i in range(10)])  # 10 punktów, +0.1/dzień
        trend = compute_weight_trend(series, min_points=8)
        assert trend is not None
        assert trend > 0

    def test_falling_weight_negative_slope(self):
        series = _weights([80 - i * 0.2 for i in range(10)])
        trend = compute_weight_trend(series, min_points=8)
        assert trend is not None
        assert trend < 0


class TestAdjustTdee:
    def test_returns_same_when_no_trend(self):
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=None)
        assert isinstance(res, TDEEAdjustment)
        assert res.new_tdee == 2260
        assert res.adjustment_kcal == 0.0
        assert res.confidence == "niska"

    def test_utrzymanie_cap_applied(self):
        # trend -1 kg/dzień -> ogromna korekta, ale capped do +/-250
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=-1.0)
        assert abs(res.adjustment_kcal) <= 250

    def test_target_met_no_adjustment(self):
        # trend -0.3 kg/tydz, cel -0.3 -> gap 0 -> brak korekty, confidence średnia
        week_trend = -0.3  # kg/tydz
        day_trend = week_trend / 7
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=day_trend,
                          target_trend_kg_per_week=-0.3)
        assert abs(res.adjustment_kcal) <= 1.0  # ~0
        assert res.confidence == "średnia"

    def test_confidence_high_for_large_gap(self):
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=-0.5,
                          target_trend_kg_per_week=0.0)
        assert res.confidence == "wysoka"

    def test_kcal_per_kg_correctness(self):
        # -0.3 kg/tydz przy utrzymaniu -> ubytek energii 0.3*7700/7 = 330 kcal
        res = adjust_tdee(current_tdee=2260, weight_trend_kg_per_day=-0.3 / 7)
        # korekta idzie "w stronę przeciwną": waga spada -> podnieś TDEE (+330, capped? 330>250)
        assert res.new_tdee >= 2260


class TestComputeProteinTarget:
    def test_utrzymanie_18_g_per_kg(self):
        assert compute_protein_target(70.0, phase="utrzymanie") == round(70 * 1.8, 0)

    def test_deficyt_higher(self):
        assert compute_protein_target(70.0, phase="deficyt") == round(70 * 2.2, 0)

    def test_unknown_phase_default_18(self):
        assert compute_protein_target(70.0, phase="xyz") == round(70 * 1.8, 0)
