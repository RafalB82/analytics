"""Testy fetch_hevy.py — warstwa konwersji treningów Hevy -> dzienne obciążenie.

Jedyny fetcher w analytics/ bez dedykowanych testów (audyt Rafała 2026-08-09).
Obejmuje w szczególności fix: _parse_date NIE zwraca już date.today() dla braku
daty — trening bez sensownego startTime jest odrzucany (None), a nie przypisany
do bieżącego dnia (co fałszowałoby acute_load w oknie 7d i ACWR ratio).
"""
from __future__ import annotations

from datetime import date, timedelta

from analytics.fetch_hevy import (
    _parse_date,
    build_daily_load_series,
    cardio_session_daily_load,
    compute_volume_breakdown,
    workout_daily_load,
)


def _workout(startTime, sets=None):
    return {"startTime": startTime, "exercises": [{"sets": sets or []}]}


def _set(weight, reps, rpe=None, type_=None):
    s = {"weight": weight, "reps": reps}
    if rpe is not None:
        s["rpe"] = rpe
    if type_ is not None:
        s["type"] = type_
    return s


class TestParseDate:
    def test_valid_iso(self):
        assert _parse_date("2026-08-05T10:00:00Z") == date(2026, 8, 5)

    def test_date_only(self):
        assert _parse_date("2026-08-05") == date(2026, 8, 5)

    def test_none_returns_none(self):
        # AUDYT fix: brak daty -> None, NIE date.today()
        assert _parse_date(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_date("") is None

    def test_garbage_returns_none(self):
        # niezdatny format -> None (fail-safe, nie zgaduj)
        assert _parse_date("nie-data") is None


class TestWorkoutDailyLoad:
    def test_load_from_working_sets(self):
        w = _workout("2026-08-05T10:00:00Z", [_set(100, 5, rpe=8)])
        day, load = workout_daily_load(w)
        assert (day, load) == (date(2026, 8, 5), 4000.0)

    def test_no_date_rejected(self):
        # AUDYT fix: workout bez startTime -> None (odrzucony, nie na dziś)
        w = _workout(None, [_set(100, 5, rpe=8)])
        assert workout_daily_load(w) is None

    def test_bad_date_rejected(self):
        w = _workout("garbage", [_set(100, 5, rpe=8)])
        assert workout_daily_load(w) is None

    def test_no_counted_sets_returns_none(self):
        w = _workout("2026-08-05T10:00:00Z", [])
        assert workout_daily_load(w) is None


class TestCardioSessionDailyLoad:
    def test_load(self):
        r = cardio_session_daily_load(
            {"startTime": "2026-08-05T08:00:00", "duration_minutes": 90, "rpe": 6})
        assert r == (date(2026, 8, 5), 540.0)

    def test_missing_duration_rejected(self):
        assert cardio_session_daily_load({"startTime": "2026-08-05", "rpe": 6}) is None

    def test_no_date_rejected(self):
        # AUDYT fix: brak daty -> None (nie na dziś)
        r = cardio_session_daily_load({"duration_minutes": 90, "rpe": 6})
        assert r is None


class TestBuildDailyLoadSeries:
    def _window(self, end):
        return end - timedelta(days=13), end

    def test_within_window_included(self):
        end = date(2026, 8, 9)
        start, e = self._window(end)
        w = _workout("2026-08-05T10:00:00Z", [_set(100, 5, rpe=8)])
        series = build_daily_load_series([w], start, e)
        by_day = {s.day: s.load for s in series}
        assert by_day.get(date(2026, 8, 5)) == 4000.0
        assert sum(1 for s in series) == (e - start).days + 1  # pełny szereg

    def test_outside_window_excluded(self):
        start, e = self._window(date(2026, 8, 9))
        w = _workout("2020-01-01T10:00:00Z", [_set(100, 5, rpe=8)])  # poza oknem
        series = build_daily_load_series([w], start, e)
        assert all(s.load == 0.0 for s in series)

    def test_no_date_workout_not_on_today(self):
        # AUDYT fix: workout bez startTime NIE może trafić na dzisiaj w oknie
        start, e = self._window(date(2026, 8, 9))
        w = _workout(None, [_set(100, 5, rpe=8)])
        series = build_daily_load_series([w], start, e)
        assert all(s.load == 0.0 for s in series)  # nic nie trafiło na dziś


class TestComputeVolumeBreakdown:
    """Faza 6.3: rozbicie wolumenu (working tonnage vs RPE-weighted volume)."""

    def test_working_vs_rpe_weighted(self):
        # seria 100kg x 5 z RPE 8: tonaż roboczy 500, RPE-weighted 4000
        w = _workout("2026-08-09T10:00:00Z", [_set(100, 5, rpe=8)])
        b = compute_volume_breakdown([w])
        assert b["working_tonnage"] == 500
        assert b["rpe_weighted_volume"] == 4000
        assert b["working_sets"] == 1
        assert b["rpe_coverage_pct"] == 100.0
        assert b["rpe_weighted_reliable"] is True

    def test_warmup_excluded_from_working(self):
        # rozgrzewka wchodzi do warmup_tonnage, NIE do working
        w = _workout("2026-08-09T10:00:00Z", [
            _set(60, 5, rpe=None, type_="warmup"),
            _set(100, 5, rpe=8),
        ])
        b = compute_volume_breakdown([w])
        assert b["warmup_tonnage"] == 300
        assert b["working_tonnage"] == 500
        assert b["working_sets"] == 1

    def test_missing_rpe_lowers_coverage_and_reliability(self):
        # 1 z 2 serii ma RPE -> coverage 50% -> rpe_weighted_reliable False
        w = _workout("2026-08-09T10:00:00Z", [
            _set(100, 5, rpe=8),
            _set(80, 5),   # bez RPE
        ])
        b = compute_volume_breakdown([w])
        assert b["rpe_coverage_pct"] == 50.0
        assert b["rpe_weighted_reliable"] is False

    def test_empty_workouts(self):
        b = compute_volume_breakdown([])
        assert b["working_tonnage"] == 0.0
        assert b["working_sets"] == 0
        assert b["rpe_coverage_pct"] == 0.0
        assert b["rpe_weighted_reliable"] is False

    def test_bad_sets_skipped(self):
        # serie bez reps/ciężaru pomijane (odporne na uszkodzone dane)
        w = _workout("2026-08-09T10:00:00Z", [
            _set(None, 5),
            _set(100, None),
            _set(100, 5, rpe=7),
        ])
        b = compute_volume_breakdown([w])
        assert b["working_sets"] == 1
        assert b["working_tonnage"] == 500
