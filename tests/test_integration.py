"""Testy integracyjne: pełny przepływ run_analysis.run() na danych zbliżonych do realnych.

Weryfikuje:
- poprawną strukturę outputu (wszystkie sekcje)
- determinizm (te same wejścia -> identyczny output)
- politykę błędów: brak danych -> fallback, złe dane -> error
- zgodność liczb z oryginalną logiką (regresja)
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from analytics.exceptions import InsufficientDataError, InvalidMetricError
from analytics.run_analysis import parse_input, run
from analytics.validators import validate_input


def _mk_apple(n_days: int = 14, end: date | None = None) -> list[dict]:
    """Syntetyczna seria dzienna Apple (HRV/RHR/sen + aktywność energetyczna + punkt wagi)."""
    end = end or date(2026, 8, 7)
    out = []
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        day = {
            "date": d.isoformat(),
            "resting_heart_rate": 52.0 if i < n_days - 3 else 55.0,
            "heart_rate_variability": 50.0 if i < n_days - 3 else 42.0,
            "sleep": {"total_hours": 7.2},
            # aktywność (kJ) zbliżona do realnych danych Apple po dedup
            "basal_energy_burned": float(14000 + (i % 4) * 500),
            "active_energy": float(3000 + (i % 3) * 1500),
            "apple_exercise_time": float(30 + (i % 5) * 40),
            "apple_stand_time": 300.0,
            "physical_effort": 3.0,
        }
        # waga -> punkt kontrolny tylko na ostatni dzień
        if i == n_days - 1:
            day.update({"weight_body_mass": 71.05, "body_fat_percentage": 15.1,
                        "lean_body_mass": 60.3, "body_mass_index": 24.3, "height": 1.71})
        out.append(day)
    return out


def _mk_apple_without_energy(n_days: int = 14, end: date | None = None) -> list[dict]:
    """Seria Apple BEZ pól energii (basal/active) — do testu pominięcia TDEE."""
    end = end or date(2026, 8, 7)
    out = []
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        out.append({
            "date": d.isoformat(),
            "resting_heart_rate": 52.0,
            "heart_rate_variability": 48.0,
            "sleep": {"total_hours": 7.2},
        })
    return out


def _mk_hevy(end: date | None = None) -> list[dict]:
    """Kilka treningów z ciężarami (część z RPE, część bez)."""
    end = end or date(2026, 8, 7)
    workouts = []
    for offset in (2, 4, 6, 9, 12):
        d = end - timedelta(days=offset)
        workouts.append({
            "title": "Upper",
            "startTime": f"{d.isoformat()}T18:00:00Z",
            "exercises": [
                {"name": "Bench", "sets": [
                    {"type": "normal", "weight": 80.0, "reps": 5, "rpe": 8},
                    {"type": "normal", "weight": 75.0, "reps": 7, "rpe": 8.5},
                ]},
                {"name": "Row", "sets": [
                    {"type": "normal", "weight": 60.0, "reps": 10, "rpe": 9},
                ]},
            ],
        })
    return workouts


def _payload(**overrides) -> dict:
    p = {
        "source": "apple+hevy+mfp",
        "target_date": "2026-08-07",
        "apple_daily": _mk_apple(),
        "hevy_workouts": _mk_hevy(),
        "mfp_weight": None,
        "params": {"tdee_current": 2260, "phase": "utrzymanie",
                   "bodyweight_kg": 69.9, "target_trend_kg_per_week": 0.0},
    }
    p.update(overrides)
    return p


class TestRunEndToEnd:
    def test_success_returns_ok_with_all_sections(self):
        result = run(_payload())
        assert result["status"] == "ok"
        for key in ("readiness", "acwr", "acwr_detail", "temperature",
                    "nutrition", "baseline_trends", "inputs"):
            assert key in result
        assert "zone" in result["readiness"]
        assert "ratio" in result["acwr"]
        assert result["temperature"]["status"] == "no_data"
        assert result["nutrition"]["status"] == "ok"

    def test_deterministic_output(self):
        assert run(_payload()) == run(_payload())

    def test_json_serializable(self):
        result = run(_payload())
        json.dumps(result)  # nie rzuca

    def test_missing_apple_daily_fallback(self):
        result = run(_payload(apple_daily=[]))
        assert result["status"] == "fallback"

    def test_too_few_hrv_points_fallback(self):
        result = run(_payload(apple_daily=_mk_apple(n_days=3)))
        assert result["status"] == "fallback"
        assert "hrv_history" in result["reason"]

    def test_invalid_source_error(self):
        result = run(_payload(source="garmin"))
        assert result["status"] == "error"

    def test_temperature_present(self):
        p = _payload(apple_temp=[
            {"date": "2026-08-05", "value": 36.0},
            {"date": "2026-08-06", "value": 36.1},
            {"date": "2026-08-07", "value": 36.4},  # ponad baseline -> alert
        ])
        result = run(p)
        assert result["status"] == "ok"
        assert result["temperature"]["status"] == "ok"

    def test_nutrition_goal_from_activity(self):
        # cel kaloryczny liczony z aktywności Apple (TDEE + marża wg celu)
        result = run(_payload())
        n = result["nutrition"]
        assert n["status"] == "ok"
        assert n["goal"] == "utrzymanie"
        assert n["target_kcal"] == n["tdee_kcal"]  # marża 0 dla utrzymania
        assert n["tdee_kcal"] > 0
        assert n["window_days"] == 7
        # waga z Apple jako punkt kontrolny
        assert n["weight"]["present"] is True
        assert n["weight"]["weight_kg"] == 71.05
        # białko z wagi
        assert n["protein_g"] is not None

    def test_nutrition_redukcja_negative_margin(self):
        result = run(_payload(params={"phase": "redukcja", "bodyweight_kg": 69.9}))
        n = result["nutrition"]
        assert n["status"] == "ok"
        assert n["target_kcal"] < n["tdee_kcal"]
        assert n["margin_pct"] == -0.15

    def test_nutrition_skipped_without_energy(self):
        # brak basal/active w danych -> cel pominięty (nie error)
        p = _payload(apple_daily=_mk_apple_without_energy())
        result = run(p)
        assert result["status"] == "ok"
        assert result["nutrition"]["status"] == "skipped"

    def test_long_window_28d_present(self):
        result = run(_payload())
        long28 = result["nutrition"].get("long_window_28d")
        assert long28 is not None
        assert long28["window_days"] == 28

    def test_missing_baseline_returns_fallback_via_run(self):
        # Zbyt mało punktów -> MissingBaselineError łapany jako fallback
        result = run(_payload(apple_daily=_mk_apple(n_days=4)))
        assert result["status"] == "fallback"


class TestParseInput:
    def test_valid_json(self):
        payload = parse_input(json.dumps({"source": "apple+hevy+mfp", "apple_daily": [1]}))
        assert payload["source"] == "apple+hevy+mfp"

    def test_invalid_json_raises(self):
        import pytest
        with pytest.raises(InvalidMetricError):
            parse_input("{invalid json")

    def test_non_dict_raises(self):
        import pytest
        with pytest.raises(InvalidMetricError):
            parse_input("[1,2,3]")


class TestValidateInput:
    def test_valid_payload(self):
        source, target, params, apple_daily, hevy, apple_w, cardio, mfp, temp = validate_input(_payload())
        assert source == "apple+hevy+mfp"
        assert target == date(2026, 8, 7)
        assert cardio == []  # domyślnie brak sesji cardio
        assert apple_w == []  # domyślnie brak workoutów Apple

    def test_payload_with_cardio(self):
        p = _payload()
        p["cardio_sessions"] = [
            {"startTime": "2026-08-06T08:00:00", "duration_minutes": 90, "rpe": 6}
        ]
        p["apple_workouts"] = [{"name": "Outdoor Cycling", "start": "2026-08-06T08:00:00",
                                 "duration_min": 90, "avg_heart_rate_bpm": 140}]
        source, target, params, apple_daily, hevy, apple_w, cardio, mfp, temp = validate_input(p)
        assert len(cardio) == 1
        assert cardio[0]["rpe"] == 6
        assert len(apple_w) == 1
        assert apple_w[0]["name"] == "Outdoor Cycling"

    def test_bad_source_raises(self):
        import pytest
        with pytest.raises(InvalidMetricError):
            validate_input(_payload(source="bad"))

    def test_empty_apple_raises(self):
        import pytest
        with pytest.raises(InsufficientDataError):
            validate_input(_payload(apple_daily=[]))
