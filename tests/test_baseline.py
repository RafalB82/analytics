"""Testy baseline (EWMA, trend, przesunięcie, kontekst treningowy)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.baseline import (
    BaselineResult,
    MetricPoint,
    TrendResult,
    baseline_by_context,
    compute_ewma_baseline,
    compute_trend_slope,
    detect_baseline_shift,
)


def _days(n: int, end: date | None = None) -> list[date]:
    """Zwraca n kolejnych dni kończących się na `end` (domyślnie dziś)."""
    end = end or date(2026, 8, 7)
    return [end - timedelta(days=i) for i in range(n - 1, -1, -1)]


def _series(values: list[float], end: date | None = None) -> list[MetricPoint]:
    return [MetricPoint(day=d, value=v) for d, v in zip(_days(len(values), end), values, strict=True)]


class TestComputeEwmaBaseline:
    def test_returns_none_when_too_few_points(self):
        # min_points+1 = 6; dajemy 5 -> None
        assert compute_ewma_baseline(_series([50] * 5)) is None

    def test_baseline_excludes_current_day(self):
        # Ostatni punkt nie wchodzi do baseline tylko liczy odchylenie.
        series = _series([50.0] * 6 + [60.0])  # 7 punktów, ostatni = 60
        res = compute_ewma_baseline(series)
        assert res is not None
        # history = pierwsze 6 (wszystkie 50) -> baseline 50
        assert res.baseline == 50.0
        # deviation = 60 - 50 = 10
        assert res.deviation_abs == 10.0
        assert res.deviation_pct == 20.0
        assert res.n_points_used == 6

    def test_ewma_tracks_rising_values(self):
        # Wartości rosnące -> baseline wyższy niż punkt startowy (świeższe ważą więcej).
        # current = ostatni (80); historia = [40,40,40,40,40,50,60,70]; EWMA alpha=0.1
        series = _series([40.0] * 5 + [50.0, 60.0, 70.0, 80.0])
        res = compute_ewma_baseline(series)
        assert res is not None
        # EWMA podciąga się powyżej początkowego 40 (nie do 60+)
        assert res.baseline > 40.0
        assert res.deviation_abs > 0  # current (80) powyżej baseline

    def test_returns_baseline_result_type(self):
        res = compute_ewma_baseline(_series([50.0] * 8))
        assert isinstance(res, BaselineResult)

    def test_flat_series_zero_deviation(self):
        series = _series([55.0] * 8)
        res = compute_ewma_baseline(series)
        assert res is not None
        assert res.baseline == 55.0
        assert res.deviation_pct == 0.0
        assert res.deviation_abs == 0.0


class TestComputeTrendSlope:
    def test_returns_none_with_too_few(self):
        assert compute_trend_slope(_series([50] * 3)) is None

    def test_rising_trend_detected(self):
        series = _series([float(i) for i in range(45, 60)])  # 15 punktów rosnących
        res = compute_trend_slope(series)
        assert isinstance(res, TrendResult)
        assert res.slope > 0
        assert res.direction == "rosnący"
        assert res.reliable is True

    def test_falling_trend_detected(self):
        series = _series([float(i) for i in range(60, 45, -1)])
        res = compute_trend_slope(series)
        assert res.direction == "spadający"
        assert res.slope < 0

    def test_noisy_series_flagged_unreliable(self):
        # Duży szum -> niskie R^2 -> reliable=False i kierunek "stabilny"
        import random
        random.seed(1)
        series = _series([50 + random.uniform(-20, 20) for _ in range(14)])
        res = compute_trend_slope(series)
        assert res is not None
        assert res.reliable is False
        assert res.direction == "stabilny"


class TestDetectBaselineShift:
    def test_no_shift_when_series_similar(self):
        short = _series([50.0] * 8)
        long = _series([50.0] * 30)
        assert detect_baseline_shift(short, long) is False

    def test_shift_detected_when_divergent(self):
        short = _series([70.0] * 8)   # krótkie: wyższy baseline
        long = _series([50.0] * 30)   # długie: niższy baseline (>8% różnicy)
        assert detect_baseline_shift(short, long) is True

    def test_returns_false_when_baseline_zero(self):
        short = _series([0.0] * 8)
        long = _series([0.0] * 30)
        assert detect_baseline_shift(short, long) is False

    def test_returns_native_bool(self):
        # regresja: nie może zwracać np.bool_
        short = _series([50.0] * 8)
        long = _series([50.0] * 30)
        assert type(detect_baseline_shift(short, long)) is bool


class TestBaselineByContext:
    def test_filters_by_training_flag(self):
        days = _days(8)
        # Ostatni punkt = treningowy; tylko dni treningowe wchodzą do baseline
        series = [
            MetricPoint(day=d, value=50.0, is_training_day=(i % 2 == 0))
            for i, d in enumerate(days)
        ]
        res = baseline_by_context(series, current_is_training_day=True)
        assert res is not None

    def test_context_baseline_matches_naive_for_single_context(self):
        days = _days(8)
        series = [
            MetricPoint(day=d, value=50.0, is_training_day=True)
            for d in days
        ]
        res = baseline_by_context(series, current_is_training_day=True)
        assert res is not None
        assert res.baseline == 50.0
