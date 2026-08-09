"""Testy finalnego scoringu gotowości (readiness_integration)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import ACWRResult, GapInfo
from analytics.baseline import MetricPoint, TrendResult
from analytics.exceptions import MissingBaselineError
from analytics.readiness_integration import (
    classify_recovery,
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


class TestAxesAndVerdict:
    """Faza 6.2b: osie RECOVERY/LOAD/DATA_QUALITY + werdykt (sekcja 6.2 review).

    Sedno: sam wysoki LOAD bez oznak pogorszenia regeneracji NIE jest czerwoną
    strefą. Czerwona wymaga loadu ORAZ recovery critical. To realizuje rozdzielenie
    load (obciążenie) od fatigue (zmęczenie).
    """

    def _readiness(self, *, acwr_zone="optymalna", acwr_ratio=1.0,
                   cardio_7d_sessions=0, temp=None, sleep=None, rpe_cov=None):
        common = dict(
            hrv_series=_series([45] * 8),
            rhr_series=_series([55] * 8),
            sleep_hours_today=sleep,
            acwr_result=_acwr(zone=acwr_zone, ratio=acwr_ratio),
            temp_alert=temp or _temp(),
            spo2_confirmed=False,
            cardio_7d_sessions=cardio_7d_sessions,
            rpe_coverage_pct=rpe_cov,
        )
        return compute_full_readiness(**common)

    def test_axes_present_and_old_fields_kept(self):
        out = self._readiness()
        # stare pola wciąż są
        assert hasattr(out, "base_score") and hasattr(out, "acwr_penalty")
        assert hasattr(out, "total_score") and hasattr(out, "zone")
        # nowe osie
        assert out.recovery is not None
        assert out.load is not None
        assert out.data_quality is not None
        assert out.verdict is not None
        assert "status" in out.recovery and "status" in out.load
        assert "status" in out.data_quality
        assert "zone" in out.verdict

    def test_high_load_ok_recovery_is_green_not_red(self):
        # kluczowe z review: wysoki load (kara obciążenia) + recovery ok -> GREEN
        # (duże obciążenie, ale organizm NIE pokazuje oznak problemu)
        # Wymuszamy wysoki load przez cardio_7d_sessions=3 (kara +2) przy dobrym
        # baseline regeneracyjnym (base=0).
        out = self._readiness(cardio_7d_sessions=3)
        assert out.acwr_penalty >= 2          # load faktycznie wysoki
        assert out.load["status"] in ("high", "very_high")
        assert out.verdict["zone"] == "green"   # NIE czerwona mimo loadu!
        assert "nie pokazuje oznak" in out.verdict["rationale"]

    def test_high_load_degraded_recovery_is_orange(self):
        # wysoki load + pojedyncze oznaki pogorszenia -> ORANGE
        # (delikatny spadek HRV -> base 2 -> degraded; plus wysoki load z cardio)
        hrv_series = _series([45] * 7 + [30])   # ostatni dzień mocno w dół -> base 2
        rhr_series = _series([55] * 8)
        out2 = compute_full_readiness(
            hrv_series=hrv_series, rhr_series=rhr_series, sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False, cardio_7d_sessions=3,
        )
        assert out2.recovery["status"] == "degraded"
        assert out2.load["status"] in ("high", "very_high")
        assert out2.verdict["zone"] == "orange"

    def test_high_load_critical_recovery_is_red(self):
        # wysoki load + silne oznaki pogorszenia -> RED
        hrv_series = _series([45] * 6 + [30, 28])   # mocny spadek HRV -> base 2+
        rhr_series = _series([55] * 6 + [62, 64])   # wzrost RHR -> base +
        out = compute_full_readiness(
            hrv_series=hrv_series, rhr_series=rhr_series, sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False, cardio_7d_sessions=3,
        )
        assert out.recovery["status"] == "critical"
        assert out.verdict["zone"] == "red"

    def test_low_load_green_even_if_recovery_degraded(self):
        # niski load -> green, nawet gdy regeneracja pogorszona (brak bodźców =
        # brak ostrych ograniczeń; to inny sygnał niż przeciążenie)
        hrv_series = _series([45] * 6 + [30, 28])
        rhr_series = _series([55] * 6 + [62, 64])
        out = compute_full_readiness(
            hrv_series=hrv_series, rhr_series=rhr_series, sleep_hours_today=8.0,
            acwr_result=_acwr(zone="niedociążenie", ratio=0.7),
            temp_alert=_temp(), spo2_confirmed=False, cardio_7d_sessions=0,
        )
        assert out.load["status"] == "low"
        assert out.verdict["zone"] == "green"

    def test_hard_override_forces_verdict_red(self):
        temp = TempAlert(triggered=True, deviation_c=0.6, baseline_c=36.0,
                         severity="znacząca", combined_with_hrv_drop=False)
        out = self._readiness(temp=temp)
        assert out.verdict["zone"] == "red"
        assert "override" in out.verdict["rationale"]

    def test_data_quality_flags(self):
        # brak snu + cardio niewystarczające -> nisza wiarygodność
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=None,   # brak snu
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            cardio_acwr=_acwr(zone="niewystarczające dane", ratio=2.76),
            temp_alert=_temp(), spo2_confirmed=False,
            rpe_coverage_pct=66.9,
        )
        assert out.data_quality["status"] == "low"
        joined = " ".join(out.data_quality["notes"])
        assert "śnie" in joined or "snu" in joined
        assert "niewystarczające" in joined

    def test_data_quality_high_when_clean(self):
        out = self._readiness(sleep=8.0, rpe_cov=100.0)
        assert out.data_quality["status"] == "high"

    # --- faza 6.2c: RHR trend jako sygnał ostrzegawczy w RECOVERY ---

    @staticmethod
    def _rising_rhr(reliable: bool = True, r2: float = 0.9, slope: float = 0.5):
        # rosnący, wiarygodny trend RHR
        return TrendResult(slope=slope, r_squared=r2, direction="rosnący", reliable=reliable)

    def test_rising_reliable_rhr_promotes_ok_to_degraded(self):
        # ostatni dzień wartości w normie (base ~0) + rosnący trend RHR
        # -> recovery podbite z ok do degraded (sygnał ostrzegawczy)
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False,
            rhr_trend=self._rising_rhr(),
        )
        assert out.recovery["base_score"] == 0        # sam base ok
        assert out.recovery["rhr_trend_warning"] is True
        assert out.recovery["status"] == "degraded"   # podbite z ok

    def test_rising_reliable_rhr_promotes_degraded_to_critical(self):
        # delikatny spadek HRV (base 2 -> degraded) + rosnący trend RHR
        # podbija degraded -> critical
        hrv_series = _series([45] * 7 + [30])   # base 2
        out = compute_full_readiness(
            hrv_series=hrv_series, rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False,
            rhr_trend=self._rising_rhr(),
        )
        assert out.recovery["base_score"] == 2        # z HRV
        assert out.recovery["rhr_trend_warning"] is True
        assert out.recovery["status"] == "critical"   # degraded + trend -> critical

    def test_rising_rhr_alone_never_reaches_critical_from_ok(self):
        # sam rosnący trend RHR (base ok=0) NIE tworzy critical — tylko degraded
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False,
            rhr_trend=self._rising_rhr(),
        )
        assert out.recovery["status"] == "degraded"   # NIE critical (z czystego ok)

    def test_unreliable_rhr_trend_does_not_promote(self):
        # R²=0.02 -> trend to szum, NIE ufać kierunkowi (nie podbija)
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False,
            rhr_trend=self._rising_rhr(reliable=False, r2=0.02),
        )
        assert out.recovery["rhr_trend_warning"] is False
        assert out.recovery["status"] == "ok"   # nie podbite

    def test_stable_rhr_trend_does_not_promote(self):
        # stabilny/spadający trend RHR -> brak ostrzeżenia
        stable = TrendResult(slope=0.0, r_squared=0.9, direction="stabilny", reliable=True)
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False,
            rhr_trend=stable,
        )
        assert out.recovery["rhr_trend_warning"] is False
        assert out.recovery["status"] == "ok"

    def test_rising_rhr_with_high_load_gives_orange_not_red(self):
        # rosnący trend RHR (pojedyncza oznaka) + wysoki load -> ORANGE (nie red,
        # bo to sygnał ostrzegawczy, nie silne oznaki)
        out = compute_full_readiness(
            hrv_series=_series([45] * 8), rhr_series=_series([55] * 8),
            sleep_hours_today=8.0,
            acwr_result=_acwr(zone="optymalna", ratio=1.0),
            temp_alert=_temp(), spo2_confirmed=False, cardio_7d_sessions=3,
            rhr_trend=self._rising_rhr(),
        )
        assert out.load["status"] in ("high", "very_high")
        assert out.recovery["status"] == "degraded"   # z trendu RHR
        assert out.verdict["zone"] == "orange"          # nie red
