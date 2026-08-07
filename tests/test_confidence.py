"""Testy modułu confidence (faza 2.0 — Confidence Score)."""
from __future__ import annotations

from analytics.confidence import compute_confidence, hr_series_stability


class TestComputeConfidence:
    def test_high_confidence_full_data(self):
        c = compute_confidence(n_points=30, window_days=14, n_missing=0, stability=0.9)
        assert c is not None
        assert c.score >= 80
        assert c.label == "High"

    def test_low_confidence_sparse(self):
        c = compute_confidence(n_points=4, window_days=28, n_missing=20, stability=0.2)
        assert c is not None
        assert c.score < 60
        assert c.label == "Low"

    def test_medium_confidence(self):
        c = compute_confidence(n_points=8, window_days=14, n_missing=4, stability=0.6)
        assert c is not None
        assert 60 <= c.score <= 79
        assert c.label == "Medium"

    def test_none_below_min_points(self):
        c = compute_confidence(n_points=2, window_days=14)
        assert c is None

    def test_score_bounded_0_100(self):
        # ekstremalnie dobre i złe dane nie wychodzą poza zakres
        good = compute_confidence(n_points=1000, window_days=14, n_missing=0, stability=1.0)
        bad = compute_confidence(n_points=3, window_days=28, n_missing=100, stability=0.0)
        assert good.score <= 100 and good.score >= 80
        assert bad.score >= 0 and bad.score < 40

    def test_to_dict_has_expected_keys(self):
        c = compute_confidence(n_points=14, window_days=14, stability=0.8)
        d = c.to_dict()
        for k in ("score", "label", "n_points", "window_days",
                  "completeness", "stability", "coverage"):
            assert k in d

    def test_stability_none_becomes_neutral(self):
        c = compute_confidence(n_points=20, window_days=14, stability=None)
        assert c.stability == 0.5


class TestHRSeriesStability:
    def test_stable_series_high(self):
        assert hr_series_stability([50.0, 51.0, 50.5, 50.2, 50.8]) > 0.9

    def test_variable_series_lower(self):
        s_steady = hr_series_stability([50.0, 50.0, 50.0, 50.0])
        s_spiky = hr_series_stability([50.0, 80.0, 40.0, 90.0])
        assert s_spiky < s_steady

    def test_too_few_points_none(self):
        assert hr_series_stability([50.0, 51.0]) is None

    def test_zero_mean_none(self):
        assert hr_series_stability([0.0, 0.0, 0.0]) is None
