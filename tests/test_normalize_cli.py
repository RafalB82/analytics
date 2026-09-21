"""Testy wejść CLI (stdin -> stdout) dla *_normalize oraz luk w apple_normalize."""
from __future__ import annotations

import io
import json

import pytest

from analytics.exceptions import InvalidMetricError
from mcp_fetchers import apple_normalize, hevy_normalize, mfp_normalize

MODULES = [apple_normalize, hevy_normalize, mfp_normalize]


def _run(mod, monkeypatch, capsys, text):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    rc = mod.main()
    return rc, capsys.readouterr().out


@pytest.mark.parametrize("mod", MODULES)
def test_empty_stdin_is_error(mod, monkeypatch, capsys):
    rc, out = _run(mod, monkeypatch, capsys, "   ")
    assert rc == 1
    assert json.loads(out)["status"] == "error"


@pytest.mark.parametrize("mod", MODULES)
def test_invalid_json_is_error(mod, monkeypatch, capsys):
    rc, out = _run(mod, monkeypatch, capsys, "{not json")
    assert rc == 1
    assert "invalid json" in json.loads(out)["error"]


class TestAppleMain:
    def test_end_to_end(self, monkeypatch, capsys):
        raw = {
            "daily": [{"date": "2026-08-08T00:00:00", "resting_heart_rate": 52,
                       "heart_rate_variability": 45, "sleep": {"total_hours": 7.1},
                       "active_energy": "4200", "basal_energy_burned": None}],
            "temp": [{"date": "2026-08-08", "value": 35.9}, {"date": "2026-08-07", "value": None}],
            "workouts": [
                {"id": "2", "name": "Outdoor Cycling", "start": "2026-08-08T17:00:00+02:00",
                 "duration_min": 60, "avg_heart_rate_bpm": 140},
                {"id": "1", "name": "Running", "start": "2026-08-06T17:00:00+02:00",
                 "duration_s": 1800, "avg_heart_rate_bpm": 150, "max_heart_rate_bpm": 180},
                {"id": "1", "name": "Running", "start": "2026-08-06T17:00:00+02:00",
                 "duration_min": 30, "avg_heart_rate_bpm": 150},   # duplikat id
                {"id": "3", "name": "Traditional Strength Training", "duration_min": 50,
                 "avg_heart_rate_bpm": 110},
            ],
        }
        rc, out = _run(apple_normalize, monkeypatch, capsys, json.dumps(raw))
        data = json.loads(out)
        assert rc == 0
        assert data["apple_daily"][0]["date"] == "2026-08-08"
        assert data["apple_daily"][0]["active_energy"] == 4200.0
        assert data["apple_daily"][0]["basal_energy_burned"] is None
        assert data["apple_temp"] == [{"date": "2026-08-08", "value": 35.9}]
        assert [w["name"] for w in data["apple_workouts"]] == ["Running", "Outdoor Cycling"]
        assert data["apple_workouts"][0]["duration_min"] == 30.0
        assert data["apple_workouts"][0]["max_heart_rate_bpm"] == 180.0

    def test_missing_sections_are_ok(self, monkeypatch, capsys):
        rc, out = _run(apple_normalize, monkeypatch, capsys, "{}")
        assert rc == 0
        assert json.loads(out) == {"apple_daily": [], "apple_temp": [], "apple_workouts": []}

    @pytest.mark.parametrize("w", [
        {"name": "Running", "duration_min": 30},                                   # brak avg HR
        {"name": "Running", "avg_heart_rate_bpm": 140},                            # brak czasu
        {"name": "Running", "duration_min": "x", "avg_heart_rate_bpm": 140},
        {"name": "Running", "duration_min": 0, "avg_heart_rate_bpm": 140},
        {"name": "Running", "duration_min": 30, "avg_heart_rate_bpm": float("nan")},
        {"name": "Running", "duration_min": 30, "avg_heart_rate_bpm": 140,
         "max_heart_rate_bpm": float("inf")},
        {"name": "Walking Lunges", "duration_min": 30, "avg_heart_rate_bpm": 120},  # czarna lista
    ])
    def test_rejected_workouts(self, w):
        assert apple_normalize.normalize_workout(w) is None

    @pytest.mark.parametrize("p", [
        {"date": "2026-08-08", "value": "x"},
        {"date": "2026-08-08", "value": float("nan")},
        {"date": None, "value": 1},
    ])
    def test_rejected_temp_points(self, p):
        assert apple_normalize.normalize_temp_point(p) is None

    def test_non_string_date_does_not_crash(self):
        assert apple_normalize.normalize_temp_point({"date": 20260808, "value": 35.9}) == {
            "date": "20260808", "value": 35.9}
        assert apple_normalize.normalize_daily_point({"date": None})["date"] == ""

    def test_keep_energy_garbage(self):
        assert apple_normalize._keep_energy("abc") is None


class TestHevyMain:
    def _workout(self, start="2026-08-08T10:00:00+00:00"):
        return {"start_time": start, "title": "Push",
                "exercises": [{"title": "Bench", "sets": [
                    {"type": "normal", "weight_kg": 100, "reps": 5, "rpe": 8},
                    {"type": "warmup", "weight_kg": 40, "reps": 10}]}]}

    def test_list_sorted_and_filtered(self, monkeypatch, capsys):
        raw = [self._workout("2026-08-08T10:00:00+00:00"),
               self._workout("2026-08-01T10:00:00+00:00"),
               {"start_time": "", "exercises": []}, "junk"]
        rc, out = _run(hevy_normalize, monkeypatch, capsys, json.dumps(raw))
        data = json.loads(out)
        assert rc == 0
        assert [w["startTime"][:10] for w in data] == ["2026-08-01", "2026-08-08"]
        assert data[0]["_volume"] == {"tonnage_total": 900.0, "tonnage_working": 500.0}
        assert [s["type"] for s in data[0]["exercises"][0]["sets"]] == ["normal"]  # warmup poza ACWR

    def test_single_wrapped_workout(self, monkeypatch, capsys):
        rc, out = _run(hevy_normalize, monkeypatch, capsys, json.dumps({"workout": self._workout()}))
        assert rc == 0 and len(json.loads(out)) == 1

    def test_wrong_shape_is_error(self, monkeypatch, capsys):
        rc, out = _run(hevy_normalize, monkeypatch, capsys, json.dumps({"foo": 1}))
        assert rc == 1 and json.loads(out)["status"] == "error"

    def test_invalid_reps_gives_json_error_not_traceback(self, monkeypatch, capsys):
        w = self._workout()
        w["exercises"][0]["sets"].append({"type": "normal", "weight_kg": 60, "reps": 0})
        rc, out = _run(hevy_normalize, monkeypatch, capsys, json.dumps([w]))
        assert rc == 1
        assert json.loads(out)["status"] == "error"

    def test_normalize_set_edge_cases(self):
        f = hevy_normalize._normalize_set
        assert f({"weight_kg": None, "reps": 5}) is None
        assert f({"weight_kg": "x", "reps": 5}) is None
        with pytest.raises(InvalidMetricError):  # kontrakt: zepsute reps nie są cicho pomijane
            f({"weight_kg": 50, "reps": 0})
        assert f({"weight_kg": 50, "reps": 5, "rpe": 99}) == {"type": "normal", "weight": 50.0, "reps": 5}
        assert f({"type": "warmup", "weight_kg": 20, "reps": 5}) is None
        assert f({"type": "warmup", "weight_kg": 20, "reps": 5}, for_tonnage=True)["weight"] == 20.0

    def test_naive_timestamp_treated_as_utc(self):
        assert hevy_normalize._normalize_time("2026-08-05T17:12:48") == "2026-08-05T17:12:48Z"

    def test_unparseable_time_returned_unchanged(self):
        assert hevy_normalize._normalize_time("not-a-date") == "not-a-date"
        assert hevy_normalize._normalize_time("") == ""


class TestMfpMain:
    def test_end_to_end(self, monkeypatch, capsys):
        raw = [{"date": "2026-08-05", "daily_totals": {"calories": 2430.0},
                "meals": {"breakfast": {"entries": [{"name": "Caffè"}, {"name": "Owsianka"}]}}},
               {"date": "2026-08-05", "daily_totals": {"calories": 2500.0}}]  # nakładka
        rc, out = _run(mfp_normalize, monkeypatch, capsys, json.dumps(raw))
        assert rc == 0
        assert json.loads(out) == [{"day": "2026-08-05", "kcal": 2500.0, "coffee_count": 0}]

    def test_non_dict_list_items_are_skipped(self):
        raw = ["junk", None, {"date": "2026-08-05", "daily_totals": {"calories": 2000}}]
        assert mfp_normalize.normalize_diaries(raw) == [
            {"day": "2026-08-05", "kcal": 2000.0, "coffee_count": 0}]

    def test_bad_shapes(self):
        assert mfp_normalize.normalize_diaries("str") == []
        assert mfp_normalize.normalize_diaries({"foo": 1}) == []
        assert mfp_normalize.extract_day_kcal({"date": "2026-08-05", "daily_totals": {"calories": "x"}}) is None
        assert mfp_normalize.extract_day_kcal({"date": "2026-08-05", "daily_totals": {"calories": -5}}) is None
