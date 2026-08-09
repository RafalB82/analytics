"""Testy sesji cardio z Apple Watch -> TRIMP / osobne ACWR cardio."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import acwr_readiness_modifier, build_acwr, build_cardio_acwr
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
        lo = compute_trimp_session_load(120, 60, session_peak_hr=190)
        hi = compute_trimp_session_load(160, 60, session_peak_hr=190)
        assert hi > lo

    def test_longer_session_gives_higher_trimp(self):
        short = compute_trimp_session_load(140, 30, session_peak_hr=190)
        long_ = compute_trimp_session_load(140, 90, session_peak_hr=190)
        assert long_ > short

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError):
            compute_trimp_session_load(140, 0, session_peak_hr=190)

    def test_peak_leq_rest_raises(self):
        with pytest.raises(ValueError):
            compute_trimp_session_load(140, 60, session_peak_hr=190, hr_rest=190)

    def test_typical_road_ride_magnitude(self):
        # 88-min rower @ 143 avg, rest 55, max 190 -> TRIMP rzędud setek (nie tysięcy)
        trimp = compute_trimp_session_load(143, 88, session_peak_hr=190)
        assert 50 <= trimp <= 300


class TestTrimpSessionRelative:
    """Specyfikacja trimp.md — session-relative HR reference z dolnym limitem."""

    def test_no_floor_uses_session_peak(self):
        # bez floor (None) -> reference = session_peak_hr
        # wymuszamy parametrem, żeby nie zależeć od configu
        t = compute_trimp_session_load(
            140, 60, session_peak_hr=180, hr_rest=55, hr_reference_floor=None,
        )
        assert 0 < t < 300

    def test_peak_above_floor_uses_peak(self):
        # peak 180 > floor 170 -> reference = 180 (bez wpływu floor)
        a = compute_trimp_session_load(
            150, 60, session_peak_hr=180, hr_rest=55, hr_reference_floor=170,
        )
        b = compute_trimp_session_load(
            150, 60, session_peak_hr=180, hr_rest=55, hr_reference_floor=None,
        )
        assert a == b

    def test_peak_below_floor_uses_floor(self):
        # peak 140 < floor 170 -> reference = 170 -> niższy TRIMP niż bez floor
        no_floor = compute_trimp_session_load(
            125, 60, session_peak_hr=140, hr_rest=55, hr_reference_floor=None,
        )
        floored = compute_trimp_session_load(
            125, 60, session_peak_hr=140, hr_rest=55, hr_reference_floor=170,
        )
        assert floored < no_floor  # lekka jazda mniej obciążona w normalizacji
        assert floored > 0

    def test_peak_equal_floor(self):
        # peak == floor -> brak zmiany
        a = compute_trimp_session_load(
            150, 60, session_peak_hr=170, hr_rest=55, hr_reference_floor=170,
        )
        b = compute_trimp_session_load(
            150, 60, session_peak_hr=170, hr_rest=55, hr_reference_floor=None,
        )
        assert a == b

    def test_light_ride_not_inflated(self):
        # lekka jazda: HRavg 125, peak 140, floor 170 -> niska względna intensywność
        light = compute_trimp_session_load(
            125, 60, session_peak_hr=140, hr_rest=55, hr_reference_floor=170,
        )
        # mocna jazda: HRavg 155, peak 182, floor 170 -> wyższe obciążenie
        hard = compute_trimp_session_load(
            155, 60, session_peak_hr=182, hr_rest=55, hr_reference_floor=170,
        )
        assert light < hard

    def test_peak_below_avg_raises(self):
        # peak HR < avg HR -> niespójne dane -> kontrolowany błąd
        with pytest.raises(ValueError):
            compute_trimp_session_load(160, 60, session_peak_hr=140, hr_rest=55)


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
        high_max = ac.compute_trimp_session_load(base_avg, 88.2, session_peak_hr=190)
        low_max = ac.compute_trimp_session_load(base_avg, 88.2, session_peak_hr=170)  # sesja ma max=170
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


class TestCardio7dStrongSessions:
    """cardio_7d_sessions liczy MOCNE sesje (TRIMP >= próg), nie dni z ruchem."""

    def test_strong_vs_all_days(self):
        today = date(2026, 8, 9)
        # 3 mocne (pon/śr/pt: TRIMP 149/190/210) + 1 lekka (wt: rowing 5.6min -> ~35)
        workouts = [
            {"name": "Outdoor Cycling", "start": "2026-08-04T17:00:00",
             "duration_min": 71.5, "avg_heart_rate_bpm": 146.8, "max_heart_rate_bpm": 176.0},
            {"name": "Rowing", "start": "2026-08-05T19:00:00",
             "duration_min": 5.6, "avg_heart_rate_bpm": 133.9, "max_heart_rate_bpm": 150.0},
            {"name": "Outdoor Cycling", "start": "2026-08-06T17:00:00",
             "duration_min": 88.2, "avg_heart_rate_bpm": 143.5, "max_heart_rate_bpm": 170.0},
            {"name": "Outdoor Cycling", "start": "2026-08-08T16:00:00",
             "duration_min": 150.1, "avg_heart_rate_bpm": 136.9, "max_heart_rate_bpm": 183.0},
        ]
        res = build_acwr([], today, apple_workouts=workouts)
        card = res.get("cardio_detail")
        assert card is not None
        # 4 dni z ruchem, ale tylko 3 MOCNE (lekki wtorek TRIMP ~35 < próg)
        assert card["cardio_7d_days"] == 4
        assert card["cardio_7d_sessions"] == 3
        # wszystkie cardio_7d_total większe niż sama suma mocnych (wlicza lekką)
        assert card["cardio_7d_total"] > 0
