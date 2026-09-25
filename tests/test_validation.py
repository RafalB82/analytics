"""Testy walidatorów metryk wejściowych (HRV, RHR, sen, temp, waga, tonaż, reps)."""
from __future__ import annotations

from datetime import date

import pytest

from analytics.exceptions import InsufficientDataError, InvalidMetricError
from analytics.validators import (
    coerce_float,
    ensure_sorted_ascending,
    hrv,
    parse_valid_rpe,
    reps,
    rhr,
    rpe,
    set_weight,
    sleep,
    temperature,
    validate_float,
    validate_input,
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

    def test_none_rejected_for_required_metric(self):
        # allow_none=False (domyślnie) odrzuca brak wymaganej wartości
        with pytest.raises(InvalidMetricError):
            coerce_float(None, "hrv")

    def test_none_passes_through_when_allowed(self):
        assert coerce_float(None, "hrv", allow_none=True) is None

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

    def test_validate_float_rejects_none_for_required_metric(self):
        with pytest.raises(InvalidMetricError):
            validate_float(None, "hrv", 30, 250)


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

    def test_fractional_reps_rejected(self):
        with pytest.raises(InvalidMetricError):
            reps(5.9)

    def test_boolean_reps_rejected(self):
        with pytest.raises(InvalidMetricError):
            reps(True)

    def test_integer_float_reps_allowed(self):
        assert reps(5.0) == 5

    def test_rpe_range(self):
        assert rpe("8") == 8.0
        with pytest.raises(InvalidMetricError):
            rpe("abc")
        with pytest.raises(InvalidMetricError):
            rpe(11)

    def test_zero_reps_rejected(self):
        with pytest.raises(InvalidMetricError):
            reps(0)

    @pytest.mark.parametrize("value, expected", [(None, None), (7, 7.0), ("7", 7.0), (7.0, 7.0)])
    def test_parse_valid_rpe(self, value, expected):
        assert parse_valid_rpe(value) == expected

    @pytest.mark.parametrize("value", [0, 11, "abc", float("nan"), float("inf")])
    def test_parse_invalid_rpe(self, value):
        with pytest.raises(InvalidMetricError):
            parse_valid_rpe(value)


class TestEnsureSortedAscending:
    def test_sorted_passes(self):
        days = [date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)]
        ensure_sorted_ascending(days, "hrv")  # nie rzuca

    def test_unsorted_raises(self):
        days = [date(2026, 8, 3), date(2026, 8, 1), date(2026, 8, 2)]
        with pytest.raises(InvalidMetricError):
            ensure_sorted_ascending(days, "hrv")


def _input_payload(**overrides) -> dict:
    """Bazowy poprawny payload dla validate_input."""
    payload = {
        "source": "apple+hevy+mfp",
        "target_date": "2026-08-07",
        "apple_daily": [{"date": "2026-08-07", "heart_rate_variability": 55}],
        "params": {},
    }
    payload.update(overrides)
    return payload


class TestValidateInput:
    """Testy validate_input (przeniesiony do validators/input.py, krok 2/9)."""

    def test_valid_returns_components(self):
        source, target, params, apple_daily, hevy, app_wk, cardio, temp, mfp = validate_input(_input_payload())
        assert source == "apple+hevy+mfp"
        assert target == date(2026, 8, 7)
        assert apple_daily
        assert params == {}
        assert hevy == []
        assert app_wk == []
        assert cardio == []
        assert temp == []
        assert mfp == []

    def test_bad_source_rejected(self):
        with pytest.raises(InvalidMetricError):
            validate_input(_input_payload(source="garmin"))

    def test_missing_source_rejected(self):
        with pytest.raises(InvalidMetricError):
            validate_input(_input_payload(source=None))

    def test_missing_apple_daily_fallback(self):
        with pytest.raises(InsufficientDataError):
            validate_input(_input_payload(apple_daily=[]))

    def test_invalid_target_date(self):
        with pytest.raises(InvalidMetricError):
            validate_input(_input_payload(target_date="not-a-date"))

    def test_default_target_today(self):
        _, target, *_ = validate_input(_input_payload(target_date=None))
        assert target == date.today()

    def test_optional_fields_default_empty(self):
        _, _, _, _, hevy, app_wk, cardio, temp, mfp = validate_input(_input_payload(target_date="2026-08-07"))
        assert hevy == [] and app_wk == [] and cardio == [] and temp == [] and mfp == []


class TestValidateInputListFields:
    """Regresja: `apple_temp` nie był walidowany, a `mfp_daily_kcal` w ogóle nie
    trafiał do validate_input. Zły typ dawał nieobsłużony AttributeError
    (traceback z CLI) zamiast InvalidMetricError (JSON z błędem)."""

    @pytest.mark.parametrize("field", ["apple_temp", "mfp_daily_kcal"])
    @pytest.mark.parametrize("value", [{"oops": "dict"}, "notalist", 42])
    def test_wrong_type_rejected(self, field, value):
        with pytest.raises(InvalidMetricError):
            validate_input(_input_payload(**{field: value}))

    @pytest.mark.parametrize("field", ["apple_temp", "mfp_daily_kcal"])
    def test_explicit_none_means_missing(self, field):
        # jawne None to "brak danych", nie zły typ
        assert validate_input(_input_payload(**{field: None}))[-1] == []

    def test_empty_dict_is_rejected_not_silently_empty(self):
        # {} jest falszywe, wiec `x or []` zamieniałoby je w pustą listę;
        # jawne None znaczy brak, a pusty dict to błąd typu
        with pytest.raises(InvalidMetricError):
            validate_input(_input_payload(apple_temp={}))

    def test_mfp_daily_kcal_is_returned(self):
        rows = [{"day": "2026-08-07", "kcal": 2400.0}]
        assert validate_input(_input_payload(mfp_daily_kcal=rows))[-1] == rows


class TestRemovedDeadKnobs:
    """Regresja na usunięcie martwych przełączników, które wyglądały na działające."""

    def test_compare_against_target_is_gone(self):
        # energy_balance porównuje z TDEE, nie z target_kcal — cel dietetyczny
        # nie może być raportowany jako wydatek. Pola nie ma już w settings,
        # bo nigdzie nie było czytane.
        from analytics.config import settings
        assert not hasattr(settings.ENERGY_BALANCE, "compare_against_target")

    def test_dead_exceptions_removed(self):
        from analytics import exceptions
        assert not hasattr(exceptions, "InvalidWorkoutError")
        assert not hasattr(exceptions, "ConfigError")
        assert "InvalidWorkoutError" not in exceptions.__all__
        # te, które FAKTYCZNIE są rzucane, zostały
        assert hasattr(exceptions, "MissingBaselineError")
