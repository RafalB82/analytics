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
