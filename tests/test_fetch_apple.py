"""Testy warstwy fetch_apple: ekstrakcja aktywności (energy) i punktu wagi."""
from __future__ import annotations

from datetime import date

import pytest

from analytics.exceptions import InvalidMetricError
from analytics.fetch_apple import (
    build_apple_input,
    latest_weight,
    to_energy_series,
    to_hrv_series,
    to_rhr_series,
    to_sleep_hours,
    to_temp_series,
    to_temp_series_from_points,
)


def _day(d: str, **overrides) -> dict:
    base = {
        "date": d,
        "resting_heart_rate": 52.0,
        "heart_rate_variability": 48.0,
        "sleep": {"total_hours": 7.2},
    }
    base.update(overrides)
    return base


class TestToHrvRhr:
    def test_hrv_skips_none(self):
        daily = [_day("2026-08-01", heart_rate_variability=None),
                 _day("2026-08-02", heart_rate_variability=42.0)]
        assert len(to_hrv_series(daily)) == 1

    def test_rhr_sorted_ascending(self):
        daily = [_day("2026-08-02", resting_heart_rate=55.0),
                 _day("2026-08-01", resting_heart_rate=52.0)]
        series = to_rhr_series(daily)
        assert [p.day for p in series] == [date(2026, 8, 1), date(2026, 8, 2)]

    def test_hrv_invalid_raises(self):
        daily = [_day("2026-08-01", heart_rate_variability=9999.0)]  # poza zakresem
        with pytest.raises(InvalidMetricError):
            to_hrv_series(daily)


class TestToSleepHours:
    def test_returns_hours_for_target(self):
        daily = [_day("2026-08-07", sleep={"total_hours": 6.4})]
        assert to_sleep_hours(daily, date(2026, 8, 7)) == 6.4

    def test_none_when_missing(self):
        daily = [_day("2026-08-07")]
        assert to_sleep_hours(daily, date(2026, 8, 6)) is None


class TestToEnergySeries:
    def test_parses_energy_fields(self):
        daily = [_day("2026-08-01", basal_energy_burned=14000.0, active_energy=3000.0,
                      apple_exercise_time=60.0, apple_stand_time=300.0, physical_effort=3.0)]
        es = to_energy_series(daily)
        assert len(es) == 1
        assert es[0].basal_kj == 14000.0
        assert es[0].active_kj == 3000.0
        assert es[0].exercise_min == 60.0
        assert es[0].stand_min == 300.0

    def test_none_when_fields_missing(self):
        daily = [_day("2026-08-01")]
        es = to_energy_series(daily)
        assert es[0].basal_kj is None
        assert es[0].active_kj is None

    def test_bad_numeric_returns_none(self):
        daily = [_day("2026-08-01", active_energy="abc")]
        es = to_energy_series(daily)
        assert es[0].active_kj is None


class TestLatestWeight:
    def test_returns_most_recent_weight_point(self):
        daily = [
            _day("2026-08-06", weight_body_mass=None),
            _day("2026-08-05", weight_body_mass=70.5, body_fat_percentage=15.5),
            _day("2026-08-07", weight_body_mass=71.0, body_fat_percentage=15.1),
        ]
        w = latest_weight(daily)
        assert w["present"] is True
        assert w["date"] == "2026-08-07"
        assert w["weight_kg"] == 71.0

    def test_present_false_when_no_weight(self):
        daily = [_day("2026-08-01")]
        assert latest_weight(daily)["present"] is False

    def test_out_of_range_weight_raises(self):
        """AUDYT fix: literówka (710 zamiast 71.0) nie może cicho przejść do
        compute_protein_target/TDEE — musi rzucić, spójnie z to_hrv_series
        (test_hrv_invalid_raises) i fetch_mfp.to_weight_series
        (test_invalid_weight_raises), które już tak się zachowują dla
        analogicznych uszkodzonych wartości."""
        daily = [_day("2026-08-07", weight_body_mass=710.0)]
        with pytest.raises(InvalidMetricError):
            latest_weight(daily)


class TestTempSeries:
    def test_to_temp_series_from_points(self):
        pts = [{"date": "2026-08-06", "value": 35.98}]
        ts = to_temp_series_from_points(pts)
        assert len(ts) == 1
        assert ts[0].wrist_temp_c == 35.98

    def test_to_temp_series_skips_none(self):
        daily = [_day("2026-08-01", apple_sleeping_wrist_temperature=None),
                 _day("2026-08-02", apple_sleeping_wrist_temperature=36.0)]
        ts = to_temp_series(daily)
        assert len(ts) == 1


class TestBuildAppleInput:
    def test_contains_all_keys(self):
        daily = [_day("2026-08-07", basal_energy_burned=14000.0, active_energy=3000.0,
                      weight_body_mass=71.0)]
        inp = build_apple_input(daily, date(2026, 8, 7))
        for key in ("hrv_series", "rhr_series", "sleep_hours_today", "temp_series",
                    "energy_series", "weight_info"):
            assert key in inp
        assert len(inp["energy_series"]) == 1
        assert inp["weight_info"]["present"] is True
