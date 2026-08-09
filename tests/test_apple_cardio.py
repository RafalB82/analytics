"""Testy sesji cardio z Apple Watch -> TRIMP / osobne ACWR cardio."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import acwr_readiness_modifier, build_cardio_acwr
from analytics.apple_cardio import (
    apple_workout_daily_load,
    build_apple_cardio_series,
    compute_trimp_session_load,
    is_cardio_workout,
)
from analytics.config import settings


class TestIsCardioWorkout:
    def test_cycling_is_cardio(self):
        assert is_cardio_workout({"name": "Outdoor Cycling"})

    def test_rowing_is_cardio(self):
        assert is_cardio_workout({"name": "Rowing"})

    def test_walking_is_cardio(self):
        assert is_cardio_workout({"name": "Outdoor Walk"})

    def test_running_is_cardio(self):
        assert is_cardio_workout({"name": "Outdoor Running"})

    def test_strength_is_not_cardio(self):
        # siłowe/kalisteniczne z Apple są ignorowane — siła wyłącznie z Hevy
        assert not is_cardio_workout({"name": "Traditional Strength Training"})
        assert not is_cardio_workout({"name": "Functional Strength Training"})
        assert not is_cardio_workout({"name": "Cross Training"})
        assert not is_cardio_workout({"name": "Bodyweight Workout"})

    def test_custom_strength_name_with_cardio_word_is_not_cardio(self):
        # przypadki graniczne: custom nazwa siłowa zawierająca cardio-słowo.
        # Czarna lista siłowych słów kluczowych MA PIERWSZEŃSTWO.
        assert not is_cardio_workout({"name": "Walking Lunges"})
        assert not is_cardio_workout({"name": "Walking Curls"})
        assert not is_cardio_workout({"name": "Stair Lunges"})

    def test_rowing_still_cardio_despite_row_keyword(self):
        # "rowing" (ergometr/cardio) nie może być zablokowane przez siłowe "row"
        assert is_cardio_workout({"name": "Rowing"})
        assert is_cardio_workout({"name": "Indoor Rowing"})


class TestComputeTrimp:
    def test_higher_hr_gives_higher_trimp(self):
        lo = compute_trimp_session_load(120, 60, hr_rest=55, hr_max=190)
        hi = compute_trimp_session_load(160, 60, hr_rest=55, hr_max=190)
        assert hi > lo

    def test_longer_session_gives_higher_trimp(self):
        short = compute_trimp_session_load(140, 30, hr_rest=55, hr_max=190)
        long_ = compute_trimp_session_load(140, 90, hr_rest=55, hr_max=190)
        assert long_ > short

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError):
            compute_trimp_session_load(140, 0, hr_rest=55, hr_max=190)

    def test_hr_max_leq_rest_raises(self):
        with pytest.raises(ValueError):
            compute_trimp_session_load(140, 60, hr_rest=190, hr_max=190)

    def test_typical_road_ride_magnitude(self):
        # 88-min rower @ 143 avg, rest 55, max 190 -> TRIMP rzędud setek (nie tysięcy)
        trimp = compute_trimp_session_load(143, 88, hr_rest=55, hr_max=190)
        assert 50 <= trimp <= 300


class TestAppleWorkoutDailyLoad:
    def test_cycling_session(self):
        day, load = apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
             "duration_min": 88.2, "avg_heart_rate_bpm": 143.5}
        )
        assert day == date(2026, 8, 6)
        assert load > 0

    def test_strength_rejected(self):
        assert apple_workout_daily_load(
            {"name": "Traditional Strength Training", "start": "2026-08-05T19:12:49",
             "duration_min": 102.9, "avg_heart_rate_bpm": 117.2}
        ) is None

    def test_uses_session_max_hr_when_available(self):
        # zastrzeżenie 1: hr_max brane z SESJI (max_heart_rate_bpm), nie stałej configu.
        # Wyższy max sesji => niższy TRIMP (mniejsza frakcja HRmax przy tym samym avg).
        import analytics.apple_cardio as ac
        base_avg = 143.5
        high_max = ac.compute_trimp_session_load(base_avg, 88.2, hr_max=190)
        low_max = ac.compute_trimp_session_load(base_avg, 88.2, hr_max=170)  # sesja ma max=170
        assert low_max > high_max  # mniejszy mianownik => większy TRIMP
        # przez apple_workout_daily_load z max sesji 170
        day, load = apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
             "duration_min": 88.2, "avg_heart_rate_bpm": 143.5, "max_heart_rate_bpm": 170.0}
        )
        # musi się różnić od wariantu bez max sesji (który używa configu 190)
        _, load_cfg = apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
             "duration_min": 88.2, "avg_heart_rate_bpm": 143.5}
        )
        assert load > load_cfg  # max sesji 170 < 190 => wyższy TRIMP
        assert load > 0

    def test_patologic_max_hr_rejected(self):
        # max_hr <= 0 -> sesja odrzucana, nie wywala pipeline
        assert apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
             "duration_min": 88.2, "avg_heart_rate_bpm": 143.5, "max_heart_rate_bpm": 0}
        ) is None

    def test_missing_hr_returns_none(self):
        assert apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17", "duration_min": 88.2}
        ) is None

    def test_duration_in_seconds_converted(self):
        day, load = apple_workout_daily_load(
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:37:17",
             "duration_s": 5289.9, "avg_heart_rate_bpm": 143.5}
        )
        assert day == date(2026, 8, 6)
        assert 100 <= load <= 400  # ~88 min


class TestCardio7dPenalty:
    """Kara gotowości z liczby mocnych sesji cardio w ostatnich 7d."""

    def test_thresholds(self):
        from analytics.readiness_integration import _cardio_7d_penalty
        # (lo, hi) = (2, 3): 0-1 -> 0, 2 -> +1, 3+ -> +2
        assert _cardio_7d_penalty(0) == 0
        assert _cardio_7d_penalty(1) == 0
        assert _cardio_7d_penalty(2) == 1
        assert _cardio_7d_penalty(3) == 2
        assert _cardio_7d_penalty(5) == 2


class TestBuildCardioAcwr:
    def test_build_cardio_acwr_on_cycling(self):
        today = date(2026, 8, 7)
        start = today - timedelta(days=28)
        # za mało realnych dni cardio w oknie chronic (2 < cardio_min_valid_days=3)
        # -> chronic zdominowany zerami, ratio to artefakt -> strefa "niewystarczające dane"
        rides = [
            {"name": "Outdoor Cycling", "start": str(today - timedelta(days=1)) + "T17:00:00",
             "duration_min": 90, "avg_heart_rate_bpm": 145},
            {"name": "Outdoor Cycling", "start": str(today - timedelta(days=6)) + "T17:00:00",
             "duration_min": 90, "avg_heart_rate_bpm": 145},
        ]
        series = build_apple_cardio_series(rides, start, today)
        res = build_cardio_acwr(series)
        # ratio liczony, ale strefa jawnie sygnalizuje niewiarygodność próbki,
        # a nie fałszywe "wysokie ryzyko" (3.36 z 3 sesji w 28d to artefakt)
        assert res.ratio > 0
        assert res.zone == settings.ACWR.zone_insufficient
        assert acwr_readiness_modifier(res) == 0  # brak danych -> 0 punktów karnych

    def test_build_cardio_acwr_trustworthy_sample(self):
        today = date(2026, 8, 7)
        window = settings.ACWR.chronic_window
        start = today - timedelta(days=window)
        # >= cardio_min_valid_days realnych dni -> normalna klasyfikacja stref
        n = settings.ACWR.cardio_min_valid_days
        rides = [
            {"name": "Outdoor Cycling",
             "start": str(today - timedelta(days=(i * 2) + 1)) + "T17:00:00",
             "duration_min": 60, "avg_heart_rate_bpm": 150}
            for i in range(n)  # dni 1,3,5,... -> n dni cardio w oknie chronic
        ]
        series = build_apple_cardio_series(rides, start, today)
        res = build_cardio_acwr(series)
        # próbka wystarczająca -> klasyczne strefy ryzyka (nie "niewystarczające dane")
        assert res.zone != settings.ACWR.zone_insufficient
        assert res.zone in ("niedociążenie", "optymalna", "podwyższone ryzyko", "wysokie ryzyko")

    def test_build_series_ignores_strength(self):
        today = date(2026, 8, 7)
        start = today - timedelta(days=14)
        workouts = [
            {"name": "Outdoor Cycling", "start": str(today - timedelta(days=1)) + "T17:00:00",
             "duration_min": 90, "avg_heart_rate_bpm": 145},
            {"name": "Traditional Strength Training", "start": str(today - timedelta(days=2)) + "T19:00:00",
             "duration_min": 100, "avg_heart_rate_bpm": 115},
        ]
        series = build_apple_cardio_series(workouts, start, today)
        loads = {s.day: s.load for s in series}
        # tylko dzień z rowerem ma load>0; siłownia z Apple odrzucona
        assert loads[today - timedelta(days=1)] > 0
        assert loads[today - timedelta(days=2)] == 0
