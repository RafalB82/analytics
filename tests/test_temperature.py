"""Testy modułu temperatury nadgarstka (override, alerty, komunikaty)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.baseline import MetricPoint
from analytics.temperature import (
    TempPoint,
    build_temp_alert,
    build_temp_override_message,
    compute_temp_baseline,
    serialize_temp_output,
    spo2_confirmation,
    temp_deviation_alert,
)


def _temps(values: list[float], start: date | None = None) -> list[TempPoint]:
    start = start or date(2026, 7, 25)
    return [TempPoint(day=start + timedelta(days=i), wrist_temp_c=v) for i, v in enumerate(values)]


def _hrv(values: list[float], start: date | None = None) -> list[MetricPoint]:
    """Serie HRV (ms) jako MetricPoint — do detekcji spadku w build_temp_alert."""
    start = start or date(2026, 7, 25)
    return [MetricPoint(day=start + timedelta(days=i), value=v) for i, v in enumerate(values)]


class TestComputeTempBaseline:
    def test_mean_of_window(self):
        series = _temps([36.0, 36.0, 36.0, 36.0])
        assert compute_temp_baseline(series) == 36.0

    def test_returns_zero_when_empty(self):
        assert compute_temp_baseline([]) == 0.0

    def test_respects_window_excludes_current_day(self):
        # window=3, ostatni element = dzień bieżący (NIE wchodzi do baseline),
        # spójnie z baseline.compute_ewma_baseline: bierze 3 z 4 poprzednich.
        series = _temps([35.0, 35.0, 36.0, 37.0, 38.0])
        assert compute_temp_baseline(series, window=3) == round((35 + 36 + 37) / 3, 3)

    def test_baseline_non_contaminated_by_current_spike(self):
        # dzisiejszy spike nie podnosi baseline (nie "goni" za samym sobą)
        series = _temps([36.0] * 13 + [36.5])
        assert compute_temp_baseline(series, window=14) == 36.0


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


class TestBuildTempAlert:
    """Testy build_temp_alert (przeniesiony _build_temp_status, krok 5/9)."""

    def test_no_series_returns_untiggered_alert(self):
        alert = build_temp_alert([], [], date(2026, 7, 28))
        assert alert.triggered is False
        assert alert.severity == "brak"
        assert alert.baseline_c == 0.0

    def test_no_temperature_change_untiggered(self):
        """Stabilna temperatura -> alert nietriggered, niezależnie od HRV."""
        temp_series = _temps([36.0, 36.0, 36.0, 36.0], start=date(2026, 7, 25))
        hrv_series = _hrv([60, 59, 58, 57], start=date(2026, 7, 25))
        alert = build_temp_alert(temp_series, hrv_series, date(2026, 7, 28))
        assert alert.triggered is False

    def test_elevated_temp_triggers(self):
        """Temperatura powyżej progu vs baseline -> alert triggerowany."""
        # baseline 36.0, ostatni dzień 36.5 (odchylenie 0.5 > próg)
        temp_series = _temps([36.0, 36.0, 36.0, 36.5], start=date(2026, 7, 25))
        alert = build_temp_alert(temp_series, [], date(2026, 7, 28))
        assert alert.triggered is True
        assert alert.severity in ("podwyższona", "znacząca")

    def test_hrv_drop_marks_combined(self):
        """Temp podwyższona (>= prog 0.3) + spadek HRV -> combined_with_hrv_drop=True."""
        # HRV: 7 punktów, wyraźny spadek na końcu (min_points=5 wymaga >=6)
        hrv_series = _hrv([60, 60, 59, 58, 57, 40], start=date(2026, 7, 25))
        # temp 36.6 vs baseline ~36.06 -> deviation ~0.54 > prog 0.3 (trigger)
        temp_series = _temps([36.0, 36.0, 36.0, 36.0, 36.0, 36.6], start=date(2026, 7, 25))
        alert = build_temp_alert(temp_series, hrv_series, date(2026, 7, 30))
        assert alert.triggered is True
        assert alert.combined_with_hrv_drop is True


class TestSerializeTempOutput:
    """Testy serialize_temp_output (przeniesiony _temp_output, krok 4/9)."""

    def test_no_data_when_empty_series(self):
        out = serialize_temp_output(None, [], date(2026, 7, 28))
        assert out == {"status": "no_data", "alert": None, "override_message": None}

    def test_full_serialization(self):
        temp_series = _temps([36.0, 36.0, 36.0, 36.5], start=date(2026, 7, 25))
        alert = build_temp_alert(temp_series, [], date(2026, 7, 28))
        out = serialize_temp_output(alert, temp_series, date(2026, 7, 28))
        assert out["status"] == "ok"
        assert out["current_c"] == 36.5
        # baseline = EWMA/średnia ostatnich punktów, nie sztywna pierwsza wartość
        assert out["deviation_c"] > 0
        assert out["alert"] is not None
        assert "alert" in out and "baseline_c" in out

    def test_uses_latest_point_when_target_missing(self):
        """Gdy target dnia brak w serii -> używa ostatniego punktu."""
        temp_series = _temps([36.0, 36.0, 36.0], start=date(2026, 7, 25))
        alert = build_temp_alert(temp_series, [], date(2026, 7, 25))
        out = serialize_temp_output(alert, temp_series, date(2026, 8, 5))
        assert out["status"] == "ok"
        assert out["current_c"] == 36.0
