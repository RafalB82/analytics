"""Testy finalnego scoringu gotowości (readiness_integration)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import ACWRResult, GapInfo
from analytics.baseline import MetricPoint
from analytics.exceptions import MissingBaselineError
from analytics.readiness_integration import (
    classify_zone,
    compute_full_readiness,
    score_hrv_rhr_sleep,
)
from analytics.temperature import TempAlert


def _series(values: list[float], end: date | None = None) -> list[MetricPoint]:
    end = end or date(2026, 8, 7)
    return [MetricPoint(day=end - timedelta(days=len(values) - 1 - i), value=v)
            for i, v in enumerate(values)]


def _acwr(ratio: float = 1.0, zone: str = "optymalna") -> ACWRResult:
    return ACWRResult(acute_load=100, chronic_load=100, ratio=ratio, zone=zone)


def _temp(triggered: bool = False) -> TempAlert:
    return TempAlert(
        triggered=triggered, deviation_c=0.0, baseline_c=0.0,
        severity="brak", combined_with_hrv_drop=False,
    )


def _gap(detected: bool = False, gap_days: int = 0, severity: str = "brak",
         resuming_today: bool = False) -> GapInfo:
    return GapInfo(
        detected=detected, gap_days=gap_days, severity=severity,
        last_training_day=date(2026, 7, 1) if detected else None,
        resuming_today=resuming_today,
    )


class TestScoreHrvRhrSleep:
    def test_perfect_recovery_zero_score(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=0.0, sleep_hours=8.0) == 0

    def test_hrv_drop_high(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=-25.0, rhr_deviation_bpm=0.0, sleep_hours=8.0) == 2

    def test_hrv_drop_low(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=-12.0, rhr_deviation_bpm=0.0, sleep_hours=8.0) == 1

    def test_rhr_elevated_high(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=6.0, sleep_hours=8.0) == 2

    def test_rhr_elevated_low(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=3.0, sleep_hours=8.0) == 1

    def test_short_sleep_high(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=0.0, sleep_hours=5.0) == 2

    def test_medium_sleep_low(self):
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=0.0, sleep_hours=6.0) == 1

    def test_missing_sleep_no_penalty(self):
        # None nie kara — brak danych to nie 0h snu
        assert score_hrv_rhr_sleep(hrv_deviation_pct=0.0, rhr_deviation_bpm=0.0, sleep_hours=None) == 0


class TestClassifyZone:
    def test_green(self):
        assert classify_zone(1)[0] == "zielona"

    def test_yellow(self):
        assert classify_zone(3)[0] == "żółta"

    def test_red(self):
        assert classify_zone(4)[0] == "czerwona"


class TestComputeFullReadiness:
    def test_raises_missing_baseline(self):
        # za mało punktów -> MissingBaselineError (domenowy wyjątek)
        with pytest.raises(MissingBaselineError):
            compute_full_readiness(
                hrv_series=_series([50] * 3),
                rhr_series=_series([45] * 3),
                sleep_hours_today=7.0,
                acwr_result=_acwr(),
                temp_alert=_temp(),
                spo2_confirmed=False,
            )

    def test_readiness_output_fields(self):
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(),
            temp_alert=_temp(),
            spo2_confirmed=False,
        )
        assert out.base_score == 0
        assert out.acwr_penalty == 0
        assert out.total_score == 0
        assert out.zone == "zielona"
        assert out.sleep_missing is False

    def test_sleep_missing_flag(self):
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=None,
            acwr_result=_acwr(),
            temp_alert=_temp(),
            spo2_confirmed=False,
        )
        assert out.sleep_missing is True
        # sen nie dodał kary
        assert out.base_score == 0

    def test_temp_hard_override_forces_red(self):
        temp = TempAlert(
            triggered=True, deviation_c=0.5, baseline_c=0.0,
            severity="znacząca", combined_with_hrv_drop=False,
        )
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(),
            temp_alert=temp,
            spo2_confirmed=False,
        )
        assert out.hard_override is not None
        assert out.zone == "czerwona"

    def test_acwr_penalty_applied(self):
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="wysokie ryzyko"),
            temp_alert=_temp(),
            spo2_confirmed=False,
        )
        assert out.acwr_penalty == 2

    def test_gap_note_none_by_default(self):
        """Bez przekazania gap (domyślnie None) -> gap_note=None, wsteczna kompatybilność."""
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(),
            temp_alert=_temp(),
            spo2_confirmed=False,
        )
        assert out.gap_note is None

    def test_gap_note_none_when_not_detected(self):
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(),
            temp_alert=_temp(),
            spo2_confirmed=False,
            gap=_gap(detected=False),
        )
        assert out.gap_note is None

    def test_gap_note_present_on_resume(self):
        out = compute_full_readiness(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="niedociążenie", ratio=0.4),
            temp_alert=_temp(),
            spo2_confirmed=False,
            gap=_gap(detected=True, gap_days=10, severity="krótka", resuming_today=True),
        )
        assert out.gap_note is not None
        assert "10 dniach przerwy" in out.gap_note

    def test_gap_note_does_not_change_score_or_zone(self):
        """Kluczowa właściwość: gap_note to niezależne ostrzeżenie tekstowe,
        NIE modyfikator punktowy — total_score/zone identyczne z i bez gap,
        przy tym samym (niskim) ACWR ratio typowym dla powrotu po przerwie."""
        common = dict(
            hrv_series=_series([50] * 8),
            rhr_series=_series([45] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="niedociążenie", ratio=0.4),
            temp_alert=_temp(),
            spo2_confirmed=False,
        )
        out_without_gap = compute_full_readiness(**common, gap=None)
        out_with_gap = compute_full_readiness(
            **common, gap=_gap(detected=True, gap_days=10, severity="krótka", resuming_today=True),
        )
        assert out_without_gap.total_score == out_with_gap.total_score
        assert out_without_gap.zone == out_with_gap.zone
        assert out_without_gap.acwr_penalty == out_with_gap.acwr_penalty
        assert out_without_gap.gap_note is None
        assert out_with_gap.gap_note is not None
