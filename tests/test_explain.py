"""Testy Explainability Layer (faza 9.0)."""
from __future__ import annotations

from analytics.explain import build_explanations


def _full_payload():
    return dict(
        hrv_deviation_pct=-13.4,
        rhr_deviation_bpm=2.4,
        sleep_hours=7.2,
        sleep_missing=False,
        trend_note=None,
        acwr={"zone": "wysokie ryzyko", "ratio": 2.37},
        rpe_coverage={"coverage_pct": 100.0},
        temperature={"status": "no_data", "alert": None},
        goal={"status": "ok", "tdee_kcal": 4592, "window_days": 7,
              "goal": "utrzymanie", "target_kcal": 4592},
    )


class TestBuildExplanations:
    def test_returns_all_metrics(self):
        ex = build_explanations(**_full_payload())
        for key in ("hrv", "rhr", "sleep", "acwr", "temperature", "tdee"):
            assert key in ex
            assert isinstance(ex[key], list) and ex[key]

    def test_hrv_reason_has_deviation(self):
        ex = build_explanations(**_full_payload())
        assert any("-13.4%" in r for r in ex["hrv"])

    def test_acwr_reason_has_zone_and_rpe(self):
        ex = build_explanations(**_full_payload())
        joined = " ".join(ex["acwr"])
        assert "wysokie ryzyko" in joined
        assert "100" in joined

    def test_sleep_missing_reason(self):
        p = _full_payload()
        p["sleep_missing"] = True
        ex = build_explanations(**p)
        assert any("Brak danych" in r for r in ex["sleep"])

    def test_temperature_normal_when_no_data(self):
        ex = build_explanations(**_full_payload())
        assert any("Brak alertu" in r for r in ex["temperature"])

    def test_temperature_alert_reason(self):
        p = _full_payload()
        p["temperature"] = {"status": "ok", "alert": {
            "deviation_c": 0.5, "severity": "podwyższona", "combined_with_hrv_drop": True}}
        ex = build_explanations(**p)
        joined = " ".join(ex["temperature"])
        assert "+0.50" in joined or "0.50" in joined
        assert "Połączone ze spadkiem HRV" in joined

    def test_tdee_skipped_reason(self):
        p = _full_payload()
        p["goal"] = {"status": "skipped", "reason": "brak energii"}
        ex = build_explanations(**p)
        assert any("niedostępny" in r for r in ex["tdee"])

    def test_hrv_none_omitted(self):
        p = _full_payload()
        p["hrv_deviation_pct"] = None
        ex = build_explanations(**p)
        assert "hrv" not in ex

    def test_trend_note_included(self):
        p = _full_payload()
        p["trend_note"] = "HRV w trendzie spadkowym"
        ex = build_explanations(**p)
        assert any("trendzie spadkowym" in r for r in ex["hrv"])
