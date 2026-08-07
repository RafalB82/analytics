"""Testy warstwy fetch_mfp (waga — rezerwa; MFP pełni rolę tylko kalorii/jedzenia)."""
from __future__ import annotations

from datetime import date

import pytest

from analytics.exceptions import InvalidMetricError
from analytics.fetch_mfp import WeightSample, to_weight_series


class TestToWeightSeries:
    def test_parses_valid_rows(self):
        rows = [{"date": "2026-08-01", "value": 70.5},
                {"date": "2026-08-07", "value": 71.0}]
        out = to_weight_series(rows)
        assert len(out) == 2
        assert isinstance(out[0], WeightSample)
        assert out[-1].weight_kg == 71.0

    def test_sorted_ascending(self):
        rows = [{"date": "2026-08-07", "value": 71.0},
                {"date": "2026-08-01", "value": 70.5}]
        out = to_weight_series(rows)
        assert [p.day for p in out] == [date(2026, 8, 1), date(2026, 8, 7)]

    def test_skips_none_and_missing(self):
        rows = [{"date": "2026-08-01", "value": None},
                {"date": None, "value": 70.0},
                {"date": "2026-08-02", "value": 70.6}]
        out = to_weight_series(rows)
        assert len(out) == 1
        assert out[0].weight_kg == 70.6

    def test_invalid_weight_raises(self):
        # wartości niekonwertowalne = błąd danych (zgodnie z walidatorem), nie ciche pominięcie
        with pytest.raises(InvalidMetricError):
            to_weight_series([{"date": "2026-08-01", "value": "abc"}])

    def test_empty_input(self):
        assert to_weight_series([]) == []
