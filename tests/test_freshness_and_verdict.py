"""Testy PR1: werdykt nie może być łagodniejszy niż stan regeneracji +
nieaktualne odczyty nie są traktowane jako „dzisiejsze”."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import ACWRResult
from analytics.baseline import MetricPoint, is_current
from analytics.energy_balance import compute_energy_balance
from analytics.exceptions import InsufficientDataError
from analytics.readiness_integration import build_verdict, compute_full_readiness
from analytics.run_analysis import run
from analytics.temperature import TempAlert, TempPoint, build_temp_alert, serialize_temp_output

T = date(2026, 8, 20)


def _series(values, end=T):
    return [MetricPoint(day=end - timedelta(days=len(values) - 1 - i), value=v)
            for i, v in enumerate(values)]


def _acwr():
    return ACWRResult(acute_load=100, chronic_load=100, ratio=1.0, zone="reference")


def _no_temp():
    return TempAlert(triggered=False, deviation_c=0.0, baseline_c=0.0,
                     severity="brak", combined_with_hrv_drop=False)


def _rec(status):
    return {"status": status}


def _load(status):
    return {"status": status}


# ---------------------------------------------------------------- werdykt
class TestVerdictMatrix:
    @pytest.mark.parametrize("load", ["low", "moderate"])
    def test_critical_recovery_never_green(self, load):
        v = build_verdict(_rec("critical"), _load(load))
        assert v["zone"] == "orange"
        assert "pełna objętość" not in v["advice"]

    @pytest.mark.parametrize("rec", ["ok", "degraded"])
    @pytest.mark.parametrize("load", ["low", "moderate"])
    def test_non_critical_recovery_low_load_stays_green(self, rec, load):
        assert build_verdict(_rec(rec), _load(load))["zone"] == "green"

    def test_high_load_critical_is_red(self):
        assert build_verdict(_rec("critical"), _load("high"))["zone"] == "red"

    def test_high_load_degraded_is_orange(self):
        assert build_verdict(_rec("degraded"), _load("high"))["zone"] == "orange"

    def test_legacy_red_floors_green_verdict_to_orange(self):
        v = build_verdict(_rec("degraded"), _load("moderate"), legacy_zone="czerwona")
        assert v["zone"] == "orange"
        assert "czerwon" in v["rationale"]

    def test_legacy_red_does_not_downgrade_red(self):
        v = build_verdict(_rec("critical"), _load("high"), legacy_zone="czerwona")
        assert v["zone"] == "red"

    @pytest.mark.parametrize("legacy", [None, "zielona", "żółta"])
    def test_non_red_legacy_does_not_change_verdict(self, legacy):
        v = build_verdict(_rec("ok"), _load("moderate"), legacy_zone=legacy)
        assert v["zone"] == "green"


# ------------------------------------------------------------- is_current
class TestIsCurrent:
    def test_today_and_yesterday_are_current(self):
        assert is_current(_series([1.0]), T)
        assert is_current(_series([1.0], end=T - timedelta(days=1)), T)

    def test_two_days_old_is_stale(self):
        assert not is_current(_series([1.0], end=T - timedelta(days=2)), T)

    def test_empty_and_future_are_not_current(self):
        assert not is_current([], T)
        assert not is_current(_series([1.0], end=T + timedelta(days=1)), T)

    def test_custom_max_age(self):
        s = _series([1.0], end=T - timedelta(days=3))
        assert is_current(s, T, max_age_days=3)


# ------------------------------------------- compute_full_readiness + target
class TestReadinessFreshness:
    def _call(self, hrv, rhr, **kw):
        return compute_full_readiness(
            hrv_series=hrv, rhr_series=rhr, sleep_hours_today=8.0,
            acwr_result=_acwr(), temp_alert=_no_temp(), spo2_confirmed=False, **kw)

    def test_both_stale_raises_insufficient_data(self):
        old = T - timedelta(days=6)
        with pytest.raises(InsufficientDataError, match="stale_recovery_signals"):
            self._call(_series([45] * 8, end=old), _series([55] * 8, end=old), target=T)

    def test_one_stale_signal_is_skipped_and_flagged(self):
        old = T - timedelta(days=5)
        hrv = _series([45] * 6 + [30, 28], end=old)      # załamanie, ale sprzed 5 dni
        rhr = _series([55] * 8)                           # bieżące i płaskie
        with_target = self._call(hrv, rhr, target=T)
        without_target = self._call(hrv, rhr)             # zachowanie legacy
        assert with_target.stale_signals == ["hrv"]
        assert with_target.base_score == 0                # stare HRV nie karze
        assert without_target.base_score >= 2             # legacy: stary odczyt karał
        assert any("HRV" in n for n in with_target.data_quality["notes"])
        assert with_target.data_quality["status"] != "high"

    def test_fresh_signals_have_no_stale_flags(self):
        out = self._call(_series([45] * 8), _series([55] * 8), target=T)
        assert out.stale_signals == []

    def test_target_none_keeps_legacy_behaviour(self):
        old = T - timedelta(days=30)
        out = self._call(_series([45] * 8, end=old), _series([55] * 8, end=old))
        assert out.stale_signals == []


# --------------------------------------------------------------- temperatura
def _temps(values, end=T):
    return [TempPoint(day=end - timedelta(days=len(values) - 1 - i), wrist_temp_c=v)
            for i, v in enumerate(values)]


class TestTemperatureFreshness:
    def test_stale_series_gives_no_alert(self):
        s = _temps([36.0] * 10 + [36.8], end=T - timedelta(days=5))
        alert = build_temp_alert(s, [], T)
        assert alert.triggered is False
        assert serialize_temp_output(alert, s, T)["status"] == "stale"

    def test_stale_hrv_drop_does_not_escalate_severity(self):
        temp = _temps([36.0] * 10 + [36.4])               # +0.4: podwyższona (< 0.45)
        stale_hrv = _series([45] * 10 + [30], end=T - timedelta(days=5))
        fresh_hrv = _series([45] * 10 + [30])
        assert build_temp_alert(temp, stale_hrv, T).severity == "podwyższona"
        assert build_temp_alert(temp, fresh_hrv, T).severity == "znacząca"


    def test_explain_marks_stale_temperature_instead_of_saying_normal(self):
        from analytics.explain import _temperature_reasons
        s = _temps([36.0] * 5, end=T - timedelta(days=6))
        out = serialize_temp_output(build_temp_alert(s, [], T), s, T)
        reasons = _temperature_reasons(out)
        assert any("nieaktualny" in r for r in reasons)
        assert not any(r.startswith("Brak alertu temperatury") for r in reasons)


# ------------------------------------------------------------ energy_balance
def _eaten(day, kcal=2000):
    return {"day": day.isoformat(), "kcal": kcal}


class TestEnergyBalanceAnchoredOnTarget:
    def test_old_entries_do_not_pose_as_current_window(self):
        # scenariusz z audytu: wpisy rozrzucone na ~20 dni, okno 7d
        eaten = [_eaten(T - timedelta(days=d)) for d in (19, 15, 10, 5, 0)]
        res = compute_energy_balance(eaten, 2500.0, target=T)
        assert res.status == "niewystarczające dane"       # w oknie tylko 1 dzień

    def test_stale_logs_are_insufficient_not_current(self):
        eaten = [_eaten(T - timedelta(days=10 + i)) for i in range(7)]
        res = compute_energy_balance(eaten, 2500.0, target=T)
        assert res.status == "niewystarczające dane"

    def test_window_ends_on_target_and_excludes_older(self):
        eaten = [_eaten(T - timedelta(days=d)) for d in range(0, 7)]
        eaten.append(_eaten(T - timedelta(days=20)))
        res = compute_energy_balance(eaten, 2500.0, target=T)
        assert res.n_valid_days == 7
        assert res.cumulative_deficit_kcal == -3500.0

    def test_entry_with_bad_date_is_skipped(self):
        eaten = [_eaten(T - timedelta(days=d)) for d in range(0, 4)]
        eaten.append({"day": "not-a-date", "kcal": 1000})
        res = compute_energy_balance(eaten, 2500.0, target=T)
        assert res.n_valid_days == 4

    def test_without_target_legacy_behaviour_unchanged(self):
        eaten = [_eaten(T - timedelta(days=d)) for d in range(0, 4)]
        res = compute_energy_balance(eaten, 2500.0)
        assert res.n_valid_days == 4


# ------------------------------------------------------------------ e2e (run)
def _daily(hrv_last=44.0, rhr_last=53.0, sleep=7.0, n=20, skip_hrv_last=0, skip_all_last=0):
    out = []
    for i in range(n, -1, -1):
        if i < skip_all_last:
            continue
        row = {
            "date": (T - timedelta(days=i)).isoformat(),
            "heart_rate_variability": hrv_last if i == 0 else 45.0 + (i % 3),
            "resting_heart_rate": rhr_last if i == 0 else 52.0 + (i % 2),
            "sleep": {"total_hours": sleep},
            "basal_energy_burned": 7200.0, "active_energy": 3000.0,
        }
        if i < skip_hrv_last:
            row["heart_rate_variability"] = None
        out.append(row)
    return out


def _workout(days_ago, rpe=8, sets=5):
    return {
        "startTime": (T - timedelta(days=days_ago)).isoformat() + "T08:00:00Z",
        "exercises": [{"sets": [{"type": "normal", "weight": 100, "reps": 5, "rpe": rpe}
                                for _ in range(sets)]}],
    }


def _payload(apple_daily, hevy=None):
    return {
        "source": "apple+hevy+mfp", "target_date": T.isoformat(),
        "apple_daily": apple_daily,
        "hevy_workouts": hevy if hevy is not None else [_workout(i) for i in range(0, 28, 3)],
        "params": {"phase": "utrzymanie", "bodyweight_kg": 71},
    }


class TestEndToEnd:
    def test_bad_recovery_with_regular_training_is_not_green(self):
        # HRV -33%, RHR +8.5, sen 4h przy regularnych treningach (scenariusz D z audytu)
        r = run(_payload(_daily(hrv_last=31, rhr_last=61, sleep=4.0)))
        rd = r["readiness"]
        assert r["status"] == "ok"
        assert rd["zone"] == "czerwona"
        assert rd["recovery"]["status"] == "critical"
        assert rd["load"]["status"] == "moderate"
        assert rd["verdict"]["zone"] in ("orange", "red")

    def test_normal_day_is_still_green(self):
        r = run(_payload(_daily()))
        assert r["status"] == "ok"
        assert r["readiness"]["verdict"]["zone"] == "green"
        assert r["recovery_today"]["stale_signals"] == []

    def test_old_hrv_rhr_gives_fallback_not_fake_today(self):
        # scenariusz E z audytu: ostatnie HRV/RHR sprzed 6 dni, dziś tylko sen
        daily = _daily(skip_all_last=6) + [
            {"date": T.isoformat(), "sleep": {"total_hours": 7.0}}]
        r = run(_payload(daily))
        assert r["status"] == "fallback"
        assert "stale_recovery_signals" in r["reason"]

    def test_only_hrv_stale_is_reported_transparently(self):
        r = run(_payload(_daily(skip_hrv_last=6)))
        rt = r["recovery_today"]
        assert r["status"] == "ok"
        assert rt["hrv_ms"] is None and rt["hrv_deviation_pct"] is None
        assert rt["hrv_date"] == (T - timedelta(days=6)).isoformat()
        assert rt["rhr_bpm"] is not None
        assert rt["stale_signals"] == ["hrv"]
        assert r["readiness"]["stale_signals"] == ["hrv"]
        assert any("HRV" in n for n in r["readiness"]["data_quality"]["notes"])
