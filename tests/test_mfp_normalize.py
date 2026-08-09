"""Testy mfp_normalize.py — ekstrakcja zjedzonych kcal z dziennika MFP."""
from __future__ import annotations

from mcp_fetchers import mfp_normalize


class TestExtractDayKcal:
    def test_single_diary(self):
        diary = {"date": "2026-08-05", "daily_totals": {"calories": 2430.0}}
        assert mfp_normalize.extract_day_kcal(diary) == {"day": "2026-08-05", "kcal": 2430.0}

    def test_missing_totals(self):
        assert mfp_normalize.extract_day_kcal({"date": "2026-08-05"}) is None

    def test_missing_date(self):
        assert mfp_normalize.extract_day_kcal({"daily_totals": {"calories": 100}}) is None

    def test_negative_kcal_rejected(self):
        d = {"date": "2026-08-05", "daily_totals": {"calories": -5}}
        assert mfp_normalize.extract_day_kcal(d) is None


class TestNormalizeDiaries:
    def test_list_sorted(self):
        raw = [
            {"date": "2026-08-08", "daily_totals": {"calories": 2561.0}},
            {"date": "2026-08-05", "daily_totals": {"calories": 2430.0}},
        ]
        out = mfp_normalize.normalize_diaries(raw)
        assert [d["day"] for d in out] == ["2026-08-05", "2026-08-08"]  # chronologicznie

    def test_single_dict(self):
        out = mfp_normalize.normalize_diaries({"date": "2026-08-05", "daily_totals": {"calories": 2430.0}})
        assert out == [{"day": "2026-08-05", "kcal": 2430.0}]

    def test_skips_invalid(self):
        raw = [
            {"date": "2026-08-05", "daily_totals": {"calories": 2430.0}},
            {"date": "2026-08-06"},  # brak totals -> pominięte
        ]
        out = mfp_normalize.normalize_diaries(raw)
        assert len(out) == 1
