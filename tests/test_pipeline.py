"""Testy Analytics Pipeline (faza 7.0)."""
from __future__ import annotations

import importlib
import sys

from analytics.pipeline import (
    PIPELINE,
    AnalyticsPipeline,
    PipelineContext,
    analytics_stage,
    confidence_stage,
    explain_stage,
    input_validation_stage,
    model_building_stage,
    serialization_stage,
)
from analytics.run_analysis import run
from tests.test_integration import _payload


def _ctx(payload: dict) -> PipelineContext:
    return PipelineContext(
        source=payload.get("source", ""),
        target=payload.get("target_date"),
        params=payload.get("params", {}),
        apple_daily=payload.get("apple_daily", []),
        hevy_workouts=payload.get("hevy_workouts", []),
        mfp_weight=payload.get("mfp_weight") or [],
        apple_temp=payload.get("apple_temp", []),
    )


class TestPipeline:
    def test_pipeline_matches_run(self):
        """Pipeline złożony daje IDENTYCZNY output co run()."""
        payload = _payload()
        # run() zwraca już zserializowany dict
        expected = run(payload)

        # pipeline: wykonaj wszystkie stage'e + serialize
        ctx = PIPELINE.run(_ctx(payload))
        from analytics.run_analysis import _json_safe
        got = _json_safe(ctx.report.model_dump(mode="json"))

        assert got == expected

    def test_stages_sequential_state(self):
        """Stage'e wypełniają context krok po kroku."""
        ctx = input_validation_stage(_ctx(_payload()))
        assert ctx.target is not None
        assert ctx.apple_daily

        ctx = model_building_stage(ctx)
        assert ctx.models and ctx.acwr_info

        ctx = analytics_stage(ctx)
        assert ctx.readiness is not None
        assert ctx.goal_info

        ctx = confidence_stage(ctx)
        assert ctx.confidence  # non-empty

        ctx = explain_stage(ctx)
        assert ctx.explanations  # non-empty

        ctx = serialization_stage(ctx)
        assert ctx.report is not None

    def test_custom_pipeline_composition(self):
        """Można złożyć pipeline z podzbioru stage'ów."""
        pipe = AnalyticsPipeline(stages=(input_validation_stage, model_building_stage))
        ctx = pipe.run(_ctx(_payload()))
        assert ctx.models and ctx.acwr_info
        assert ctx.report is None  # serialization nie było

    def test_invalid_payload_fallback(self):
        """Błędne dane -> pipeline rzuca, run() łapie jako fallback."""
        bad = _payload(apple_daily=[])
        result = run(bad)
        assert result["status"] == "fallback"

    def test_context_fields_default(self):
        ctx = PipelineContext()
        assert ctx.apple_daily == []
        assert ctx.params == {}
        assert ctx.confidence == {}

    def test_recovery_today_has_current_hrv_rhr_sleep(self):
        """recovery_today musi nieść REALNY dzisiejszy odczyt (nie licznik
        punktów jak inputs.apple_points.hrv) — regresja na sekcję dodaną,
        żeby prompt Telegram mógł pokazać HRV/RHR z nocy zamiast n_points."""
        result = run(_payload())
        rt = result.get("recovery_today")
        assert rt is not None
        # wartości muszą być realnymi odczytami (ms/bpm), nie licznikiem dni —
        # sanity check zakresu odróżnia to od np. n_points_used (rzędu 6-28)
        assert rt["hrv_ms"] is not None
        assert rt["rhr_bpm"] is not None
        assert rt["hrv_baseline_ms"] is not None
        assert rt["rhr_baseline_bpm"] is not None
        assert "sleep_hours" in rt
        assert "sleep_missing" in rt
        # current != n_points_used na poziomie kontraktu: to pole osobne,
        # inputs.apple_points.hrv (licznik) musi zostać nietknięty
        assert result["inputs"]["apple_points"]["hrv"] != rt["hrv_ms"]

    def test_recovery_today_sleep_missing_flag(self):
        """Gdy brak snu na dziś, sleep_missing=True i sleep_hours=None
        (spójnie z inputs.sleep_data == 'missing')."""
        payload = _payload()
        # usuń sen z ostatniego (target) dnia, zostaw resztę historii
        target = payload["target_date"]
        for d in payload["apple_daily"]:
            if d.get("date") == target:
                d.pop("sleep", None)
        result = run(payload)
        rt = result["recovery_today"]
        assert rt["sleep_missing"] is True
        assert rt["sleep_hours"] is None


class TestConfidenceStageNPointsInWindow:
    """AUDYT: n_points w confidence_stage musi być ograniczone do okna metryki,
    nie do długości całej dostarczonej serii (payload ma ACWR_LOOKBACK_DAYS=35d,
    a okno HRV/RHR=14, TDEE=7). Wcześniej len(cała seria) zawyżało completeness
    i składową 'ilość danych', maskując realne luki."""

    def _energy(self, n: int = 1):
        from datetime import date, timedelta

        from analytics.nutrition_adaptive import DailyEnergy

        return [
            DailyEnergy(day=date(2026, 8, 1) + timedelta(days=i), basal_kj=15000.0,
                        active_kj=4000.0, exercise_min=60.0, stand_min=300.0,
                        physical_effort=3.0)
            for i in range(n)
        ]

    def _ctx(self, hrv_len: int, energy_series, goal_n_days, hrv_start=None) -> PipelineContext:
        from datetime import date, timedelta

        from analytics.acwr import SessionLoad
        from analytics.baseline import MetricPoint

        start = hrv_start or date(2026, 8, 1)
        hrv = [MetricPoint(day=start + timedelta(days=i), value=50.0) for i in range(hrv_len)]
        rhr = hrv  # ta sama długość dla RHR
        ctx = PipelineContext()
        ctx.target = date(2026, 8, 7)  # target dla filtra okna kalendarzowego
        ctx.models = {
            "hrv_series": hrv,
            "rhr_series": rhr,
            "energy_series": energy_series,
        }
        ctx.goal_info = {
            "status": "ok",
            "window_days": 7,
            "n_days": goal_n_days,
            "tdee_kcal": 2500,
            "target_kcal": 2500,
        }
        # ACWR: pełny, ciągły szereg 28 dni z 5 dniami treningowymi (jak demo)
        ctx.acwr_info = {
            "daily_loads": [
                SessionLoad(day=start + timedelta(days=i), load=1000.0 if i % 6 == 0 else 0.0)
                for i in range(28)
            ]
        }
        return ctx

    def test_tdee_n_points_from_actual_days_not_full_series(self):
        """TDEE z serią długości 35 (payload ACWR) i tylko 4 dniami kompletu danych
        w oknie 7 -> n_points=4, window_days=7 -> completeness < 1.0 (realna luka
        widoczna), a nie fałszywe 1.0."""
        from analytics.pipeline import confidence_stage

        ctx = self._ctx(hrv_len=14, energy_series=self._energy(1), goal_n_days=4)
        confidence_stage(ctx)
        tdee = ctx.confidence["tdee"]
        assert tdee["n_points"] == 4
        assert tdee["window_days"] == 7
        assert tdee["completeness"] < 1.0  # luka w oknie widoczna, nie maskowana

    def test_hrv_rhr_n_points_capped_to_window(self):
        """HRV/RHR z serią DŁUŻSZĄ niż okno 14d (payload 35d) -> n_points=14 (okno),
        nie 35. Seria pokrywa okno, więc wszystkie 14 dni okna mają dane."""
        from datetime import date, timedelta

        from analytics.pipeline import confidence_stage

        # seria 35 dni wstecz od target (jak payload ACWR) — kończy się na target
        start = date(2026, 8, 7) - timedelta(days=34)
        ctx = self._ctx(hrv_len=35, energy_series=self._energy(1), goal_n_days=7,
                        hrv_start=start)
        confidence_stage(ctx)
        assert ctx.confidence["hrv"]["n_points"] == 14  # punkty w oknie 14d
        assert ctx.confidence["rhr"]["n_points"] == 14
        # n_points nigdy nie przekracza window_days
        assert ctx.confidence["hrv"]["n_points"] <= ctx.confidence["hrv"]["window_days"]

    def test_hrv_points_outside_window_not_counted(self):
        """AUDYT follow-up: HRV obecne TYLKO PRZED oknem 14d (użytkownik nie nosił
        zegarka w ostatnich 14 dniach) -> w oknie kalendarzowym nie ma ani jednego
        punktu. compute_confidence zwraca None (n_points=0 < min_points=3), więc
        wpis 'hrv' NIE powstaje — NIE raportujemy fałszywego 14/1.0/High."""
        from datetime import date, timedelta

        from analytics.pipeline import confidence_stage

        # punkty 15..35 dni wstecz — poza oknem 14d (brak pomiaru w oknie)
        start = date(2026, 8, 7) - timedelta(days=35)
        ctx = self._ctx(hrv_len=21, energy_series=self._energy(1), goal_n_days=7,
                        hrv_start=start)
        confidence_stage(ctx)
        # brak danych w oknie -> brak wpisu (nie fałszywa pewność High)
        assert "hrv" not in ctx.confidence or ctx.confidence["hrv"].get("n_points", 0) == 0

    def test_hrv_partial_coverage_in_window(self):
        """HRV w oknie 14d tylko przez część dni -> completeness < 1.0, nie fałszywe 1.0."""
        from datetime import date, timedelta

        from analytics.pipeline import confidence_stage

        # 7 punktów w oknie 14d (czyli 50% okna) — zaczynając 5 dni przed target
        start = date(2026, 8, 7) - timedelta(days=4)
        ctx = self._ctx(hrv_len=5, energy_series=self._energy(1), goal_n_days=7,
                        hrv_start=start)
        confidence_stage(ctx)
        hrv = ctx.confidence["hrv"]
        assert hrv["n_points"] <= 5  # max 5 punktów (seria ma 5)
        assert hrv["completeness"] < 1.0  # nie pełne okno

    def test_tdee_n_points_never_exceeds_window(self):
        """n_days zgłaszane przez build_goal_output nie może przekroczyć window_days
        (gdyby goal_info miał zawyżone n_days, cap na okno)."""
        from analytics.pipeline import confidence_stage

        ctx = self._ctx(hrv_len=14, energy_series=self._energy(1), goal_n_days=99)  # zawyżone
        confidence_stage(ctx)
        assert ctx.confidence["tdee"]["n_points"] <= ctx.confidence["tdee"]["window_days"]


def test_pipeline_does_not_import_run_analysis():
    """Pipeline (ani żaden jego moduł dziedzinowy) NIE może importować run_analysis.

    To zabezpiecza przed odtworzeniem cyklicznej zależności
    pipeline -> run_analysis -> pipeline (krok 5/9 usunął ją całkowicie).
    Test analizuje AST (realne importy), nie tekst — komentarze/docstringi
    mogą opisywać architekturę.
    """
    import ast

    # moduły bazowe + pipeline + wszystkie moduły dziedzinowe
    _targets = ["pipeline", "acwr", "baseline", "confidence", "explain",
                "fetch_apple", "fetch_hevy", "fetch_mfp", "models",
                "nutrition_adaptive", "readiness_integration", "stability",
                "temperature", "validators.metrics", "validators.input"]
    for target in _targets:
        mod = importlib.import_module(f"analytics.{target}")
        src_path = sys.modules[mod.__name__].__file__ or ""
        with open(src_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=src_path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            else:
                continue
            base = module.split(".")[0]
            if base == "run_analysis" or (base == "analytics" and "run_analysis" in module.split(".")):
                raise AssertionError(
                    f"{target} importuje run_analysis ({module}) — cykl!"
                )


# --- sekcja 6.2a: rozbicie confidence (trend vs próbka) ---------------------

from analytics.baseline import TrendResult
from analytics.pipeline import _trend_confidence_label, _serialize_trend


class TestTrendConfidenceBreakdown:
    def test_label_high_when_reliable(self):
        assert _trend_confidence_label(True, 0.9) == "High"

    def test_label_low_when_unreliable(self):
        # R²=0.02 -> trend to szum, niezależnie od liczby punktów próbki
        assert _trend_confidence_label(False, 0.02) == "Low"

    def test_label_brak_danych_when_none(self):
        assert _trend_confidence_label(None, None) == "brak danych"

    def test_serialize_trend_none(self):
        assert _serialize_trend(None, {}) is None

    def test_serialize_trend_adds_breakdown(self):
        t = TrendResult(slope=0.15, r_squared=0.02, direction="stabilny", reliable=False)
        d = _serialize_trend(t, {"label": "High", "score": 95})
        # oryginalne pola zachowane
        assert d["slope"] == 0.15 and d["r_squared"] == 0.02
        assert d["reliable"] is False
        # rozbicie
        assert d["trend_confidence"] == "Low"     # trend szumny (R²=0.02)
        assert d["trend_reliable"] is False
        assert d["sample_confidence"] == {"label": "High", "score": 95}  # próbka dobra

    def test_serialize_trend_high_trend_high_sample(self):
        # poprawny przypadek: i trend wiarygodny, i próbka bogata
        t = TrendResult(slope=0.3, r_squared=0.9, direction="rosnący", reliable=True)
        d = _serialize_trend(t, {"label": "High", "score": 90})
        assert d["trend_confidence"] == "High"
        assert d["trend_reliable"] is True
        assert d["sample_confidence"]["label"] == "High"
