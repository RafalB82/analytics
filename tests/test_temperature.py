"""Testy modułu temperatury nadgarstka (override, alerty, komunikaty)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.temperature import (
    TempPoint,
    build_temp_override_message,
    compute_temp_baseline,
    spo2_confirmation,
    temp_deviation_alert,
)


def _temps(values: list[float], start: date | None = None) -> list[TempPoint]:
    start = start or date(2026, 7, 25)
    return [TempPoint(day=start + timedelta(days=i), wrist_temp_c=v) for i, v in enumerate(values)]


class TestComputeTempBaseline:
    def test_mean_of_window(self):
        series = _temps([36.0, 36.0, 36.0, 36.0])
        assert compute_temp_baseline(series) == 36.0

    def test_returns_zero_when_empty(self):
        assert compute_temp_baseline([]) == 0.0

    def test_respects_window(self):
        # window=3 -> bierze ostatnie 3 z 5
        series = _temps([35.0, 35.0, 36.0, 37.0, 38.0])
        assert compute_temp_baseline(series, window=3) == round((37 + 38 + 36) / 3, 3)


class TestTempDeviationAlert:
    def test_no_alert_below_threshold(self):
        alert = temp_deviation_alert(current=36.0, baseline=35.9, threshold_c=0.3)
        assert alert.triggered is False
        assert alert.severity == "brak"

    def test_triggered_above_threshold(self):
        alert = temp_deviation_alert(current=36.4, baseline=36.0, threshold_c=0.3)
        assert alert.triggered is True
        assert alert.severity == "podwyższona"

    def test_significant_when_above_multiplier(self):
        # 0.5 > 0.3*1.5=0.45 -> znacząca
        alert = temp_deviation_alert(current=36.5, baseline=36.0, threshold_c=0.3)
        assert alert.severity == "znacząca"

    def test_significant_when_hrv_dropped(self):
        # triggered (0.35 >= 0.3) i hrv_dropped -> znacząca
        alert = temp_deviation_alert(current=36.35, baseline=36.0, threshold_c=0.3, hrv_dropped=True)
        assert alert.triggered is True
        assert alert.severity == "znacząca"
        assert alert.combined_with_hrv_drop is True

    def test_not_combined_without_hrv_drop(self):
        alert = temp_deviation_alert(current=36.35, baseline=36.0, threshold_c=0.3)
        assert alert.combined_with_hrv_drop is False


class TestSpo2Confirmation:
    def test_none_returns_false(self):
        assert spo2_confirmation(None) is False

    def test_drop_triggers_confirmation(self):
        assert spo2_confirmation(spo2_pct=93.0, baseline_spo2=96.0, threshold=2.0) is True

    def test_small_drop_no_confirmation(self):
        assert spo2_confirmation(spo2_pct=95.0, baseline_spo2=96.0, threshold=2.0) is False


class TestBuildTempOverrideMessage:
    def test_none_when_not_triggered(self):
        alert = temp_deviation_alert(current=36.0, baseline=35.9)
        assert build_temp_override_message(alert, spo2_confirmed=False) is None

    def test_message_when_significant_and_spo2(self):
        alert = temp_deviation_alert(current=36.6, baseline=36.0, threshold_c=0.3)
        msg = build_temp_override_message(alert, spo2_confirmed=True)
        assert msg is not None
        assert "infekcja" in msg

    def test_message_when_significant_with_hrv(self):
        alert = temp_deviation_alert(current=36.5, baseline=36.0, threshold_c=0.3, hrv_dropped=True)
        msg = build_temp_override_message(alert, spo2_confirmed=False)
        assert msg is not None
        assert "czerwoną" in msg

    def test_message_when_elevated_observational(self):
        alert = temp_deviation_alert(current=36.35, baseline=36.0, threshold_c=0.3)
        msg = build_temp_override_message(alert, spo2_confirmed=False)
        assert msg is not None
        assert "Obserwuj" in msg
