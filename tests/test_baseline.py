"""Testy baseline (EWMA, trend, przesunięcie, kontekst treningowy)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

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

    def test_current_field_holds_last_point_value(self):
        # current musi być wartością OSTATNIEGO punktu (dzień bieżący),
        # nie baseline i nie odchyleniem — regresja na pole dodane dla
        # recovery_today (pipeline.serialization_stage).
        series = _series([50.0] * 6 + [63.4])
        res = compute_ewma_baseline(series)
        assert res is not None
        assert res.current == 63.4
        # current nie wchodzi do historii użytej w baseline (patrz
        # test_baseline_excludes_current_day) — baseline zostaje przy 50.0
        assert res.baseline == 50.0

    def test_flat_series_zero_deviation(self):
        series = _series([55.0] * 8)
        res = compute_ewma_baseline(series)
        assert res is not None
        assert res.baseline == 55.0
        assert res.deviation_pct == 0.0
        assert res.deviation_abs == 0.0

    def test_constant_series_converges_to_that_value(self):
        """Sanity: baseline na stałym szeregu zbiega do tej wartości (regresja
        dla cold-start fix — patrz test_deviation_independent_of_which_day_starts)."""
        res = compute_ewma_baseline(_series([50.0] * 8))
        assert res is not None
        assert res.baseline == pytest.approx(50.0, abs=0.1)

    def test_deviation_independent_of_which_day_starts_window(self):
        """AUDYT fix: identyczna rutyna HRV/RHR różniąca się tylko fazą (czy okno
        zaczyna się dniem wysokim czy niskim) musi dać zbliżone deviation_pct —
        nie zależne od przypadkowego pierwszego dnia okna.

        Stary kod (seed=values[0]) dawał ~6 pkt proc. różnicy na identycznym
        szeregu — to bezpośrednio fałszowało scoring gotowości, bo deviation_pct
        wchodzi do readiness_integration (strefa zielona/żółta/czerwona). Ten sam
        cold-start bug, który w acwr.py naprawiono w commicie 38167ef."""
        # 6 punktów historii na zmianę 45/40 (rutyna), current stały 44.5.
        # Seria A zaczyna okno dniem wysokim (45), seria B dniem niskim (40).
        hist_a = [45.0, 40.0, 45.0, 40.0, 45.0, 40.0]
        hist_b = [40.0, 45.0, 40.0, 45.0, 40.0, 45.0]
        cur = 44.5

        a = compute_ewma_baseline(_series(hist_a + [cur]), min_points=6)
        b = compute_ewma_baseline(_series(hist_b + [cur]), min_points=6)
        assert a is not None and b is not None
        # rutyna identyczna, różnica tylko fazy o 1 dzień -> mała różnica.
        # Stary kod (seed=values[0]) dawał tu ~6.25 pkt proc. rozjazdu; po fixie
        # (seed=średnia okna) resztkowa zależność fazowa jest tylko resztą z
        # samej metody EWMA na krótkim oknie. Próg 1.5 pkt proc. jest ~4x
        # ostrzejszy niż stary bug.
        assert abs(a.deviation_pct - b.deviation_pct) < 1.5

    def test_fresh_elevation_not_masked_by_seed(self):
        """AUDYT fix: świeże podwyższenie HRV w ostatnich dniach okna musi dać
        wyższy baseline niż to samo podwyższenie tylko na początku okna (EWMA
        z natury waży nowsze dane mocniej). Stary seed=values[0] zacierał ten
        sygnał, bo pierwszy punkt dostawał wagę nieproporcjonalną do alpha."""
        # 7 punktów historii: A = podwyższenie na końcu, B = na początku.
        vals_recent = [40.0] * 4 + [45.0] * 3   # podwyższenie świeże (koniec okna)
        vals_old = [45.0] * 3 + [40.0] * 4      # podwyższenie wygasłe (początek okna)
        cur = 44.0

        recent = compute_ewma_baseline(_series(vals_recent + [cur]), min_points=6)
        old = compute_ewma_baseline(_series(vals_old + [cur]), min_points=6)
        assert recent is not None and old is not None
        # świeże podwyższenie musi dać wyższy baseline niż wygasłe (EWMA waży
        # nowsze dane mocniej; stary seed=values[0] zacierał tę różnicę)
        assert recent.baseline > old.baseline


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
