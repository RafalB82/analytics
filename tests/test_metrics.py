"""Testy rejestru metryk (faza 6.0 — Metrics Registry)."""
from __future__ import annotations

from analytics.metrics import METRIC_BY_NAME, METRICS, metrics_summary


class TestMetricsRegistry:
    def test_non_empty(self):
        assert len(METRICS) > 0

    def test_unique_names(self):
        names = [m.name for m in METRICS]
        assert len(names) == len(set(names))

    def test_by_name_lookup(self):
        assert "hrv" in METRIC_BY_NAME
        assert METRIC_BY_NAME["hrv"].unit == "ms"
        assert METRIC_BY_NAME["readiness"].unit == "%"

    def test_required_fields_present(self):
        for m in METRICS:
            assert m.name
            assert m.description
            assert m.unit
            assert m.section

    def test_normal_range_shape(self):
        hrv = METRIC_BY_NAME["hrv"]
        assert hrv.normal_range == (30.0, 250.0)
        assert hrv.normal_range[0] < hrv.normal_range[1]
        assert METRIC_BY_NAME["tdee"].normal_range is None

    def test_covers_confidence_keys(self):
        # klucze używane w AnalysisReport.confidence powinny być w rejestrze
        for key in ("hrv", "rhr", "tdee", "acwr"):
            assert key in METRIC_BY_NAME

    def test_metrics_summary_shape(self):
        s = metrics_summary()
        assert isinstance(s, list)
        assert all("name" in m and "unit" in m for m in s)

    def test_to_dict_keys(self):
        hrv = METRIC_BY_NAME["hrv"]
        d = hrv.to_dict()
        for k in ("name", "description", "unit", "normal_range", "section"):
            assert k in d
