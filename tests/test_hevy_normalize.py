"""Testy hevy_normalize.py — konwersja surowych workoutów Hevy na format analytics.

Audyt Rafała (2026-08-09) — plik nie miał testów; pokrywa oba fixy:

#1 _normalize_time: offset czasowy PRZELICZANY do UTC (wcześniej ucinany do 'Z'
   bez konwersji — trening blisko północy w strefie z dużym offsetem lądował
   pod złym dniem kalendarzowym, przesuwając load w oknie ACWR).
#2 _set_tonnage: sanity-checki spójne z _normalize_set (odrzucaj waga/reps <= 0
   oraz > 1000) — uszkodzony rekord nie zaniża/zawyża raportowanego tonażu.
"""
from __future__ import annotations

from mcp_fetchers.hevy_normalize import (
    _normalize_time,
    _set_tonnage,
    _workout_tonnage,
    normalize_workout,
)


class TestNormalizeTime:
    def test_utc_offset_no_change(self):
        assert _normalize_time("2026-08-05T17:12:48+00:00") == "2026-08-05T17:12:48Z"

    def test_z_no_change(self):
        assert _normalize_time("2026-08-05T17:12:48Z") == "2026-08-05T17:12:48Z"

    def test_negative_offset_converted_to_utc(self):
        # AUDYT fix: -05:00 -> przelicz do UTC (22:12), nie tnij (17:12)
        assert _normalize_time("2026-08-05T17:12:48-05:00") == "2026-08-05T22:12:48Z"

    def test_positive_offset_converted_to_utc(self):
        # +02:00 -> odejmij 2h -> 15:12
        assert _normalize_time("2026-08-05T17:12:48+02:00") == "2026-08-05T15:12:48Z"

    def test_offset_crosses_midnight(self):
        # trening blisko północy lokalnej: 23:30 -05:00 = 04:30 UTC NASTĘPNEGO dnia
        assert _normalize_time("2026-08-05T23:30:00-05:00") == "2026-08-06T04:30:00Z"

    def test_fractional_seconds_stripped(self):
        assert _normalize_time("2026-08-05T17:12:48.123Z") == "2026-08-05T17:12:48Z"

    def test_empty_unchanged(self):
        assert _normalize_time("") == ""

    def test_naive_input_treated_as_utc_not_local_tz(self):
        """AUDYT regresja: naiwny string (bez strefy) NIE może zależeć od TZ
        procesu (.astimezone na naiwnym obiekcie zakładał lokalną strefę systemu
        -> 9h rozjazdu między kontenerem UTC a strefą Tokio). Fix: naiwne wejście
        traktujemy deterministycznie JAKO UTC, niezależnie od środowiska."""
        import os
        import time
        from contextlib import suppress

        naive = "2026-08-05T17:12:48"
        results = {}
        for tz in ("UTC", "Asia/Tokyo", "America/New_York"):
            os.environ["TZ"] = tz
            with suppress(AttributeError):  # Windows: brak tzset
                time.tzset()
            results[tz] = _normalize_time(naive)
        # deterministyczny niezależnie od strefy procesu
        assert len(set(results.values())) == 1
        assert results["UTC"] == "2026-08-05T17:12:48Z"


class TestSetTonnage:
    def test_normal(self):
        assert _set_tonnage({"weight_kg": 100, "reps": 5}) == 500.0

    def test_zero_and_negative_weight_rejected(self):
        # AUDYT fix: waga <= 0 -> 0 (wcześniej zaniżało: -50*3 = -150)
        assert _set_tonnage({"weight_kg": 0, "reps": 5}) == 0.0
        assert _set_tonnage({"weight_kg": -50, "reps": 3}) == 0.0

    def test_zero_and_negative_reps_rejected(self):
        assert _set_tonnage({"weight_kg": 100, "reps": 0}) == 0.0
        assert _set_tonnage({"weight_kg": 100, "reps": -3}) == 0.0

    def test_absurd_weight_rejected(self):
        # AUDYT fix: waga > 1000 -> 0 (sanity-check jak w fetch_hevy._set_load)
        assert _set_tonnage({"weight_kg": 5000, "reps": 2}) == 0.0

    def test_missing_fields_return_zero(self):
        assert _set_tonnage({"reps": 5}) == 0.0
        assert _set_tonnage({"weight_kg": 100}) == 0.0

    def test_bad_types_return_zero(self):
        assert _set_tonnage({"weight_kg": "x", "reps": 3}) == 0.0


class TestWorkoutTonnage:
    def test_corrupted_set_does_not_skew_total(self):
        # AUDYT fix: jedna uszkodzona seria (ujemna waga) pomijana — total 500, nie 350
        wk = {"exercises": [{"sets": [
            {"type": "normal", "weight_kg": 100, "reps": 5},
            {"type": "normal", "weight_kg": -50, "reps": 3},
        ]}]}
        assert _workout_tonnage(wk) == {"tonnage_total": 500.0, "tonnage_working": 500.0}

    def test_warmup_excluded_from_working(self):
        wk = {"exercises": [{"sets": [
            {"type": "warmup", "weight_kg": 100, "reps": 5},
            {"type": "normal", "weight_kg": 100, "reps": 5},
        ]}]}
        assert _workout_tonnage(wk) == {"tonnage_total": 1000.0, "tonnage_working": 500.0}


class TestNormalizeWorkout:
    def test_missing_date_rejected(self):
        # spójnie z resztą modułu: brak sensownego timestampu -> None
        assert normalize_workout({"start_time": None, "exercises": []}) is None

    def test_valid_workout_passes(self):
        w = {"workout": {"start_time": "2026-08-05T17:12:48+00:00",
                          "title": "Push", "exercises": []}}
        out = normalize_workout(w)
        assert out is not None
        assert out["startTime"] == "2026-08-05T17:12:48Z"
