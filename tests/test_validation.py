"""Testy walidatorów metryk wejściowych (HRV, RHR, sen, temp, waga, tonaż, reps)."""
from __future__ import annotations

from datetime import date

import pytest

from analytics.exceptions import InvalidMetricError
from analytics.validators import (
    coerce_float,
    ensure_sorted_ascending,
    hrv,
    reps,
    rhr,
    set_weight,
    sleep,
    temperature,
    validate_float,
    weight,
)


class TestCoerceFloat:
    def test_valid_float(self):
        assert coerce_float("44.5", "hrv") == 44.5

    def test_nan_rejected(self):
        with pytest.raises(InvalidMetricError):
            coerce_float(float("nan"), "hrv")

    def test_inf_rejected(self):
        with pytest.raises(InvalidMetricError):
            coerce_float(float("inf"), "hrv")

    def test_none_passes_through(self):
        assert coerce_float(None, "hrv") is None

    def test_non_numeric_rejected(self):
        with pytest.raises(InvalidMetricError):
            coerce_float("abc", "hrv")


class TestRangeValidators:
    def test_hrv_in_range(self):
        assert hrv(50.0) == 50.0

    def test_hrv_below_range(self):
        with pytest.raises(InvalidMetricError):
            hrv(5.0)  # poniżej 30

    def test_hrv_above_range(self):
        with pytest.raises(InvalidMetricError):
            hrv(999.0)  # powyżej 250

    def test_rhr_in_range(self):
        assert rhr(55.0) == 55.0

    def test_rhr_out_of_range(self):
        with pytest.raises(InvalidMetricError):
            rhr(-300.0)  # ujemne

    def test_sleep_in_range(self):
        assert sleep(7.5) == 7.5

    def test_sleep_out_of_range(self):
        with pytest.raises(InvalidMetricError):
            sleep(24.0)

    def test_temperature_in_range(self):
        assert temperature(36.0) == 36.0

    def test_temperature_out_of_range(self):
        with pytest.raises(InvalidMetricError):
            temperature(50.0)

    def test_weight_in_range(self):
        assert weight(70.0) == 70.0

    def test_weight_out_of_range(self):
        with pytest.raises(InvalidMetricError):
            weight(999999.0)  # absurdalna

    def test_validate_float_allows_none(self):
        assert validate_float(None, "hrv", 30, 250, allow_none=True) is None


class TestSetWeightAndReps:
    def test_set_weight_positive(self):
        assert set_weight(80.0) == 80.0

    def test_set_weight_zero_rejected(self):
        with pytest.raises(InvalidMetricError):
            set_weight(0)

    def test_set_weight_negative_rejected(self):
        with pytest.raises(InvalidMetricError):
            set_weight(-5)

    def test_reps_integer(self):
        assert reps(10) == 10

    def test_reps_negative_rejected(self):
        with pytest.raises(InvalidMetricError):
            reps(-3)


class TestEnsureSortedAscending:
    def test_sorted_passes(self):
        days = [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)]
        ensure_sorted_ascending(days, "hrv")  # nie rzuca

    def test_unsorted_raises(self):
        days = [date(2026, 8, 3), date(2026, 8, 1), date(2026, 8, 2)]
        with pytest.raises(InvalidMetricError):
            ensure_sorted_ascending(days, "hrv")
