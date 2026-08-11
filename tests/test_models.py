"""Testy modeli Pydantic (DailyMetrics — walidacja zakresów i pól aktywności)."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from analytics.models import DailyMetrics, TempAlertStatus


class TestDailyMetrics:
    def test_valid_full(self):
        m = DailyMetrics(
            date=date(2026, 8, 7),
            hrv=44.0, rhr=52.0, sleep=7.2, weight=71.0, temperature=36.0,
            basal_kj=14000.0, active_kj=3000.0, exercise_min=60.0,
            stand_min=300.0, physical_effort=3.0, caffeine_mg=210.0,
            body_fat_pct=15.1, lean_kg=60.3, bmi=24.3, height=1.71,
        )
        assert m.date == date(2026, 8, 7)
        assert m.basal_kj == 14000.0
        assert m.body_fat_pct == 15.1
        assert m.caffeine_mg == 210.0

    def test_hrv_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            DailyMetrics(date=date(2026, 8, 7), hrv=9999.0)

    def test_body_fat_percent_range(self):
        with pytest.raises(ValidationError):
            DailyMetrics(date=date(2026, 8, 7), body_fat_pct=150.0)

    def test_defaults_to_none(self):
        m = DailyMetrics(date=date(2026, 8, 7))
        assert m.hrv is None
        assert m.active_kj is None
        assert m.exercise_min is None
        assert m.caffeine_mg is None

    def test_negative_caffeine_rejected(self):
        with pytest.raises(ValidationError):
            DailyMetrics(date=date(2026, 8, 7), caffeine_mg=-5.0)

    def test_extra_fields_ignored(self):
        # extra="ignore" → nieznane pola nie powodują błędu i nie są zapisywane
        m = DailyMetrics(date=date(2026, 8, 7), unknown_field=123)
        assert "unknown_field" not in m.model_dump()

    def test_negative_energy_rejected(self):
        with pytest.raises(ValidationError):
            DailyMetrics(date=date(2026, 8, 7), active_kj=-5.0)


class TestTempAlertStatus:
    def test_defaults(self):
        t = TempAlertStatus()
        assert t.status == "no_data"
        assert t.alert is None
        assert t.override_message is None

    def test_temperature_alert_excluded_from_dump(self):
        # temp_alert_obj ma exclude=True → nie wchodzi do model_dump()
        t = TempAlertStatus(temp_alert_obj=object())
        dumped = t.model_dump()
        assert "temp_alert_obj" not in dumped
