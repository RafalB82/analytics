"""Testy mfp_normalize.py — ekstrakcja zjedzonych kcal z dziennika MFP."""
from __future__ import annotations

from mcp_fetchers import mfp_normalize


class TestExtractDayKcal:
    def test_single_diary(self):
        diary = {"date": "2026-08-05", "daily_totals": {"calories": 2430.0}}
        assert mfp_normalize.extract_day_kcal(diary) == {
            "day": "2026-08-05", "kcal": 2430.0, "coffee_count": 0}

    def test_missing_totals(self):
        assert mfp_normalize.extract_day_kcal({"date": "2026-08-05"}) is None

    def test_missing_date(self):
        assert mfp_normalize.extract_day_kcal({"daily_totals": {"calories": 100}}) is None

    def test_negative_kcal_rejected(self):
        d = {"date": "2026-08-05", "daily_totals": {"calories": -5}}
        assert mfp_normalize.extract_day_kcal(d) is None


class TestCountCoffee:
    def _diary_with_entries(self, names: list[str]) -> dict:
        return {
            "date": "2026-08-05",
            "daily_totals": {"calories": 100.0},
            "meals": {
                "breakfast": {"entries": [{"name": n, "nutrition": {}} for n in names]},
            },
        }

    def test_counts_caffe_entries(self):
        d = self._diary_with_entries(["caffè - caffè", "caffè - caffè", "Jajka M"])
        assert mfp_normalize.count_coffee_entries(d) == 2

    def test_zero_when_no_coffee(self):
        d = self._diary_with_entries(["Jajka M", "Maslo"])
        assert mfp_normalize.count_coffee_entries(d) == 0

    def test_case_insensitive_and_variants(self):
        d = self._diary_with_entries(["Coffee", "KAWA", "espresso", "Latte"])
        assert mfp_normalize.count_coffee_entries(d) == 4

    def test_word_boundary_no_false_positive(self):
        # "kałam" NIE powinno zaliczyć "kawa"; "caffe latte" TAK (2 napoje).
        d = self._diary_with_entries(["kałam ciasto", "caffe latte"])
        assert mfp_normalize.count_coffee_entries(d) == 1

    def test_short_name_fallback(self):
        d = {
            "date": "2026-08-05",
            "daily_totals": {"calories": 100.0},
            "meals": {"breakfast": {"entries": [
                {"name": "Pełna nazwa", "short_name": "caffè - skrót", "nutrition": {}}
            ]}},
        }
        assert mfp_normalize.count_coffee_entries(d) == 1

    def test_coffee_count_in_extract(self):
        d = self._diary_with_entries(["caffè - caffè"] * 3)
        out = mfp_normalize.extract_day_kcal(d)
        assert out["coffee_count"] == 3
        assert out["kcal"] == 100.0


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
        assert out == [{"day": "2026-08-05", "kcal": 2430.0, "coffee_count": 0}]

    def test_skips_invalid(self):
        raw = [
            {"date": "2026-08-05", "daily_totals": {"calories": 2430.0}},
            {"date": "2026-08-06"},  # brak totals -> pominięte
        ]
        out = mfp_normalize.normalize_diaries(raw)
        assert len(out) == 1
