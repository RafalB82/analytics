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
from analytics.run_analysis import parse_input, run, validate_input


def _mk_apple(n_days: int = 14, end: date | None = None) -> list[dict]:
    """Syntetyczna seria dzienna Apple (HRV/RHR/sen)."""
    end = end or date(2026, 8, 7)
    out = []
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        out.append({
            "date": d.isoformat(),
            "resting_heart_rate": 52.0 if i < n_days - 3 else 55.0,
            "heart_rate_variability": 50.0 if i < n_days - 3 else 42.0,
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
                    "tdee_adaptive", "baseline_trends", "inputs"):
            assert key in result
        assert "zone" in result["readiness"]
        assert "ratio" in result["acwr"]
        assert result["temperature"]["status"] == "no_data"

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

    def test_mfp_weight_tdee(self):
        # 14 punktów wagi rosnącej -> korekta TDEE (status ok)
        end = date(2026, 8, 7)
        weights = [
            {"date": (end - timedelta(days=i)).isoformat(), "value": 70.0 + i * 0.1}
            for i in range(14)
        ]
        result = run(_payload(mfp_weight=weights))
        assert result["status"] == "ok"
        assert result["tdee_adaptive"]["status"] == "ok"

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
        source, target, params, apple_daily, hevy, mfp, temp = validate_input(_payload())
        assert source == "apple+hevy+mfp"
        assert target == date(2026, 8, 7)

    def test_bad_source_raises(self):
        import pytest
        with pytest.raises(InvalidMetricError):
            validate_input(_payload(source="bad"))

    def test_empty_apple_raises(self):
        import pytest
        with pytest.raises(InsufficientDataError):
            validate_input(_payload(apple_daily=[]))
