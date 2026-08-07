"""Testy ACWR (obciążenie treningowe, strefy ryzyka, modyfikator readiness)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.acwr import (
    SessionLoad,
    acwr_ratio,
    acwr_readiness_modifier,
    aggregate_daily_loads,
    compute_acute_load,
    compute_chronic_load,
    compute_session_load,
    fill_missing_days,
)


def _daily(loads: list[float], start: date) -> list[SessionLoad]:
    """Buduje ciągły szereg dzienny zaczynając od `start`."""
    return [SessionLoad(day=start + timedelta(days=i), load=load) for i, load in enumerate(loads)]


class TestComputeSessionLoad:
    def test_tonnage_only_without_rpe(self):
        assert compute_session_load(sets=3, reps=5, weight_kg=100.0) == 1500.0

    def test_srpe_load_multiplies_by_rpe(self):
        assert compute_session_load(sets=3, reps=5, weight_kg=100.0, rpe=8) == 12000.0

    def test_rpe_zero_returns_zero(self):
        # RPE=0 -> tonaż*0 = 0 (RPE jest jawnie podane, więc mnożymy)
        assert compute_session_load(sets=1, reps=10, weight_kg=20.0, rpe=0) == 0.0


class TestAggregateAndFill:
    def test_aggregate_sums_multiple_sessions_per_day(self):
        start = date(2026, 8, 1)
        pairs = [(start, 100.0), (start, 50.0), (start + timedelta(days=1), 200.0)]
        daily = aggregate_daily_loads(pairs)
        assert daily[start] == 150.0
        assert daily[start + timedelta(days=1)] == 200.0

    def test_fill_missing_days_adds_zeros(self):
        start = date(2026, 8, 1)
        end = date(2026, 8, 5)
        daily = {start: 100.0, start + timedelta(days=3): 200.0}
        filled = fill_missing_days(daily, start, end)
        assert len(filled) == 5  # 01..05 włącznie
        assert filled[0].load == 100.0
        assert filled[1].load == 0.0
        assert filled[3].load == 200.0


class TestAcuteChronic:
    def test_acute_is_mean_of_last_7(self):
        start = date(2026, 8, 1)
        series = _daily([0, 100, 0, 100, 0, 100, 0, 100], start)  # 8 dni
        # ostatnie 7 -> [100,0,100,0,100,0,100] mean = 400/7
        assert compute_acute_load(series, window=7) == round(400.0 / 7, 1)

    def test_acute_returns_zero_when_empty(self):
        assert compute_acute_load([], window=7) == 0.0

    def test_chronic_ewma_differs_from_rolling_mean(self):
        start = date(2026, 8, 1)
        series = _daily([100.0] * 28 + [0.0], start)  # potem gwałtowny spadek
        ewma = compute_chronic_load(series, window=28, use_ewma=True)
        plain = compute_chronic_load(series, window=28, use_ewma=False)
        # EWMA (alpha=0.05) na oknie [100*27 + 0] ciągnie wynik w dół przez ostatnie 0,
        # a prosta średnia rozmywa go równomiernie -> wartości się różnią
        assert ewma != plain
        assert isinstance(ewma, float)
        assert isinstance(plain, float)

    def test_chronic_returns_zero_when_empty(self):
        assert compute_chronic_load([], window=28) == 0.0


class TestAcwrRatio:
    def test_optimal_zone(self):
        res = acwr_ratio(acute=100, chronic=100)
        assert res.ratio == 1.0
        assert res.zone == "optymalna"

    def test_underload_zone(self):
        res = acwr_ratio(acute=50, chronic=100)
        assert res.zone == "niedociążenie"

    def test_elevated_zone(self):
        res = acwr_ratio(acute=140, chronic=100)
        assert res.zone == "podwyższone ryzyko"

    def test_high_risk_zone(self):
        res = acwr_ratio(acute=160, chronic=100)
        assert res.zone == "wysokie ryzyko"

    def test_chronic_zero_sets_ratio_zero(self):
        res = acwr_ratio(acute=100, chronic=0)
        assert res.ratio == 0.0
        assert res.zone == "niedociążenie"


class TestReadinessModifier:
    def test_high_risk_adds_2(self):
        res = acwr_ratio(acute=160, chronic=100)
        assert acwr_readiness_modifier(res) == 2

    def test_elevated_adds_1(self):
        res = acwr_ratio(acute=140, chronic=100)
        assert acwr_readiness_modifier(res) == 1

    def test_optimal_adds_0(self):
        res = acwr_ratio(acute=100, chronic=100)
        assert acwr_readiness_modifier(res) == 0
