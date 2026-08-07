"""pipeline.py — Analytics Pipeline (faza 7.0).

Jawna orkiestracja analizy jako sekwencji stage'ów zamiast jednej wielkiej
funkcji. Cele (wg roadmapy 2.0 / docs/Refactoring.md):
  - czystsza orkiestracja (InputValidation -> ModelBuilding -> Analytics
    -> Confidence -> Serialization -> Report)
  - łatwiejsze testy integracyjne (każdy stage testowalny osobno)
  - reużywalne, wymienialne stage'e

WAŻNE: pipeline NIE zmienia wyniku algorytmicznego — reorganizuje istniejące
kroki z run_analysis.run(). Determinizm outputu pozostaje nietknięty.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from . import baseline as baseline_mod
from . import confidence as conf_mod
from . import explain as explain_mod
from . import nutrition_adaptive as nutr_mod
from . import stability as stab_mod
from .config import settings
from .fetch_mfp import to_weight_series
from .readiness_integration import compute_full_readiness
from .run_analysis import (
    _build_temp_status,
    _compute_goal,
    _temp_output,
    build_acwr,
    build_apple_models,
    validate_input,
)


@dataclass
class PipelineContext:
    """Dane przenoszone między stage'ami pipeline'u."""

    # wejście (z validate_input)
    source: str = ""
    target: date | None = None
    params: dict = field(default_factory=dict)
    apple_daily: list = field(default_factory=list)
    hevy_workouts: list = field(default_factory=list)
    cardio_sessions: list = field(default_factory=list)
    apple_workouts: list = field(default_factory=list)
    mfp_weight: list = field(default_factory=list)
    apple_temp: list = field(default_factory=list)

    # modele / serie (ModelBuilding)
    models: dict = field(default_factory=dict)
    acwr_info: dict = field(default_factory=dict)

    # wyniki (Analytics / Confidence / Serialization)
    readiness: Any = None
    goal_info: dict = field(default_factory=dict)
    temp_alert: Any = None
    trend_hrv: Any = None
    trend_rhr: Any = None
    confidence: dict = field(default_factory=dict)
    activity_vals: list = field(default_factory=list)
    activity_stability: Any = None
    weight_trend: dict | None = None
    explanations: dict[str, list[str]] = field(default_factory=dict)

    # wyjście
    report: Any = None


# --- Stage'y -----------------------------------------------------------------


def input_validation_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 1: walidacja wejścia + rozbicie payloadu."""
    (ctx.source, ctx.target, ctx.params, ctx.apple_daily,
     ctx.hevy_workouts, ctx.apple_workouts, ctx.cardio_sessions,
     ctx.mfp_weight, ctx.apple_temp) = validate_input(
        {"source": ctx.source, "target_date": ctx.target,
         "apple_daily": ctx.apple_daily, "hevy_workouts": ctx.hevy_workouts,
         "apple_workouts": ctx.apple_workouts,
         "cardio_sessions": ctx.cardio_sessions,
         "mfp_weight": ctx.mfp_weight, "apple_temp": ctx.apple_temp,
         "params": ctx.params})
    return ctx


def model_building_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 2: budowa modeli / serii analitycznych (Apple + Hevy)."""
    assert ctx.target is not None, "target nie ustawiony po walidacji"
    ctx.models = build_apple_models(ctx.apple_daily, ctx.target, ctx.apple_temp)
    ctx.acwr_info = build_acwr(ctx.hevy_workouts, ctx.target,
                               cardio_sessions=ctx.cardio_sessions,
                               apple_workouts=ctx.apple_workouts)
    return ctx


def analytics_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 3: logika analityczna (readiness, temp, TDEE, trendy)."""
    m = ctx.models
    assert ctx.target is not None
    ctx.temp_alert = _build_temp_status(m["temp_series"], m["hrv_series"], ctx.target)
    ctx.readiness = compute_full_readiness(
        hrv_series=m["hrv_series"],
        rhr_series=m["rhr_series"],
        sleep_hours_today=m["sleep_hours_today"],
        acwr_result=ctx.acwr_info["result"],
        cardio_acwr=ctx.acwr_info.get("cardio"),
        temp_alert=ctx.temp_alert,
        spo2_confirmed=False,
    )
    ctx.goal_info = _compute_goal(m["energy_series"], m["weight_info"], ctx.params)
    ctx.trend_hrv = baseline_mod.compute_trend_slope(m["hrv_series"])
    ctx.trend_rhr = baseline_mod.compute_trend_slope(m["rhr_series"])
    return ctx


def confidence_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 4: Confidence Score + Activity Stability + Weight Trend."""
    m = ctx.models
    hrv_vals = [p.value for p in m["hrv_series"]]
    rhr_vals = [p.value for p in m["rhr_series"]]
    ctx.activity_vals = [
        (e.basal_kj or 0.0) + (e.active_kj or 0.0)
        for e in m["energy_series"]
    ]
    act_stability = conf_mod.hr_series_stability(ctx.activity_vals) if ctx.activity_vals else None
    n_window = (
        ctx.goal_info.get("window_days", 7)
        if ctx.goal_info.get("status") == "ok"
        else None
    )

    confidence: dict[str, dict] = {}
    c_hrv = conf_mod.compute_confidence(
        n_points=len(hrv_vals), window_days=14,
        stability=conf_mod.hr_series_stability(hrv_vals),
    )
    if c_hrv:
        confidence["hrv"] = c_hrv.to_dict()
    c_rhr = conf_mod.compute_confidence(
        n_points=len(rhr_vals), window_days=14,
        stability=conf_mod.hr_series_stability(rhr_vals),
    )
    if c_rhr:
        confidence["rhr"] = c_rhr.to_dict()
    if ctx.goal_info.get("status") == "ok" and n_window and ctx.activity_vals:
        c_tdee = conf_mod.compute_confidence(
            n_points=len(ctx.activity_vals), window_days=n_window,
            stability=act_stability,
        )
        if c_tdee:
            confidence["tdee"] = c_tdee.to_dict()
    training_days = sum(1 for d in ctx.acwr_info["daily_loads"] if d.load > 0)
    c_acwr = conf_mod.compute_confidence(
        n_points=training_days, window_days=settings.ACWR.chronic_window,
    )
    if c_acwr:
        confidence["acwr"] = c_acwr.to_dict()
    ctx.confidence = confidence

    ctx.activity_stability = (
        stab_mod.activity_stability(ctx.activity_vals) if ctx.activity_vals else None
    )

    ctx.weight_trend = None
    if ctx.mfp_weight:
        w_series = to_weight_series(ctx.mfp_weight)
        wt = nutr_mod.compute_weight_trend(w_series)
        if wt is not None:
            ctx.weight_trend = wt.to_dict()
    return ctx


def explain_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage: Explainability Layer (faza 9.0) — reason[] per metryka dla LLM."""
    m = ctx.models

    # deviacje HRV/RHR względem baseline (tak jak liczy readiness)
    hrv_dev = rhr_dev = None
    hrv_bl = baseline_mod.compute_ewma_baseline(m["hrv_series"])
    rhr_bl = baseline_mod.compute_ewma_baseline(m["rhr_series"])
    if hrv_bl is not None:
        hrv_dev = hrv_bl.deviation_pct
    if rhr_bl is not None:
        rhr_dev = rhr_bl.deviation_abs

    trend_note = getattr(ctx.readiness, "trend_note", None)
    sleep_missing = getattr(ctx.readiness, "sleep_missing", False)
    rpe_cov = ctx.acwr_info.get("rpe_coverage")
    assert ctx.target is not None

    ctx.explanations = explain_mod.build_explanations(
        hrv_deviation_pct=hrv_dev,
        rhr_deviation_bpm=rhr_dev,
        sleep_hours=m.get("sleep_hours_today"),
        sleep_missing=sleep_missing,
        trend_note=trend_note,
        acwr=asdict(ctx.acwr_info["result"]),
        rpe_coverage=rpe_cov,
        temperature=_temp_output(ctx.temp_alert, m["temp_series"], ctx.target),
        goal=ctx.goal_info,
    )
    return ctx


def serialization_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 5: złożenie AnalysisReport + (kwazi)serializacja."""
    from .models import AnalysisReport

    m = ctx.models
    assert ctx.target is not None
    ctx.report = AnalysisReport(
        status="ok",
        source=ctx.source,
        target_date=ctx.target,
        readiness=asdict(ctx.readiness),
        acwr=asdict(ctx.acwr_info["result"]),
        acwr_detail={
            "acute_7d": ctx.acwr_info["acute"],
            "chronic_28d_ewma": ctx.acwr_info["chronic"],
            "rpe_coverage": ctx.acwr_info["rpe_coverage"],
            "cardio": ctx.acwr_info.get("cardio_detail"),
            "daily_loads_last14": [
                {"day": str(d.day), "load": d.load}
                for d in ctx.acwr_info["daily_loads"][-14:]
            ],
        },
        temperature=_temp_output(ctx.temp_alert, m["temp_series"], ctx.target),
        nutrition=ctx.goal_info,
        baseline_trends={
            "hrv": (asdict(ctx.trend_hrv) if ctx.trend_hrv else None),
            "rhr": (asdict(ctx.trend_rhr) if ctx.trend_rhr else None),
        },
        confidence=ctx.confidence or None,
        weight_trend=ctx.weight_trend,
        activity_stability=(
            ctx.activity_stability.to_dict() if ctx.activity_stability else None
        ),
        explanations=ctx.explanations or None,
        inputs={
            "apple_points": {
                "hrv": len(m["hrv_series"]),
                "rhr": len(m["rhr_series"]),
                "sleep_today_h": m["sleep_hours_today"],
                "temp": len(m["temp_series"]),
            },
            "sleep_data": ("ok" if m["sleep_hours_today"] is not None else "missing"),
            "hevy_workouts_count": len(ctx.hevy_workouts),
            "mfp_weight_points": len(ctx.mfp_weight),
        },
    )
    return ctx


# --- Pipeline ----------------------------------------------------------------


Stage = Callable[[PipelineContext], PipelineContext]


@dataclass(frozen=True)
class AnalyticsPipeline:
    """Sekwencyjna orkiestracja stage'ów analizy."""

    stages: tuple[Stage, ...]

    def run(self, ctx: PipelineContext) -> PipelineContext:
        for stage in self.stages:
            ctx = stage(ctx)
        return ctx


#: Domylślny pipeline (5 stage'ów wg roadmapy)
PIPELINE = AnalyticsPipeline(
    stages=(
        input_validation_stage,
        model_building_stage,
        analytics_stage,
        confidence_stage,
        explain_stage,
        serialization_stage,
    )
)


__all__ = [
    "PipelineContext",
    "AnalyticsPipeline",
    "PIPELINE",
    "input_validation_stage",
    "model_building_stage",
    "analytics_stage",
    "confidence_stage",
    "explain_stage",
    "serialization_stage",
]
