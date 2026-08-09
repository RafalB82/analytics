"""pipeline.py — Analytics Pipeline (faza 7.0).

Jawna orkiestracja analizy jako sekwencji stage'ów zamiast jednej wielkiej
funkcji. Cele:
  - czystsza orkiestracja (InputValidation -> ModelBuilding -> Analytics
    -> Confidence -> Serialization -> Report)
  - łatwiejsze testy integracyjne (każdy stage testowalny osobno)
  - reużywalne, wymienialne stage'e

WAŻNE: pipeline NIE zmienia wyniku algorytmicznego — reorganizuje istniejące
kroki analizy w jawną sekwencję stage'ów. Zależność jest jednostronna:
run_analysis.py (thin CLI) deleguje do PIPELINE; pipeline NIE importuje
run_analysis (zero cyklu — patrz test_pipeline_does_not_import_run_analysis).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from . import acwr as acwr_mod
from . import baseline as baseline_mod
from . import confidence as conf_mod
from . import explain as explain_mod
from . import nutrition_adaptive as nutr_mod
from . import stability as stab_mod
from . import temperature as temp_mod
from .config import settings
from .fetch_apple import build_apple_models
from .fetch_mfp import to_weight_series
from .readiness_integration import compute_full_readiness
from .validators import validate_input

_PL_DOW = {
    0: "pon", 1: "wt", 2: "śr", 3: "czw", 4: "pt", 5: "sb", 6: "nd",
}


def _dow_label(d: date) -> str:
    """Zwraca polski, 3-literowy skrót dnia tygodnia (np. 'wt', 'czw', 'sb').
    Używany w raportach, żeby uniknąć pomyłek przy interpretacji dat (agent/LLM
    nie musi liczyć dnia tygodnia z ISO — źródło pomyłek)."""
    return _PL_DOW.get(d.weekday(), "?")


def _trend_confidence_label(reliable: bool, r_squared: float | None) -> str:
    """Etykieta WIARYGODNOŚCI TRENDU (nie próbki).

    Rozdziela dwie rzeczy, które łatwo pomylić (sekcja 6.2a review):
      - „High confidence" (z ConfidenceScore) mówi o ilości/kompletności PRÓBKI
        (dużo punktów, mało luk) — ale NIE o tym, czy TREND jest wiarygodny.
      - R² trendu (z compute_trend_slope) mówi, ile wariancji wyjaśnia linia —
        przy R²=0.02 trend jest szumem, niezależnie od liczby punktów.

    Tu zwracamy etykietę dla TRENDU:
      - reliable=True -> „High" (linia dobrze opisuje dane),
      - reliable=False -> „Low" (trend szumny/niepewny — nie ufać kierunkowi),
      - brak trendu (None) -> „brak danych".
    """
    if reliable is None:
        return "brak danych"
    return "High" if reliable else "Low"


def _serialize_trend(trend: Any, sample_conf: dict | None) -> dict | None:
    """Serializuje TrendResult + jawne rozbicie confidence (trend vs próbka).

    Wstecznie kompatybilne: zachowuje oryginalne pola (slope, r_squared,
    direction, reliable) i DODAJE rozbicie, żeby etykieta „High" nie była
    mylnie interpretowana jako „wiarygodny trend" gdy R² jest niski.
    """
    if trend is None:
        return None
    d = asdict(trend)
    d["trend_confidence"] = _trend_confidence_label(
        trend.reliable, trend.r_squared
    )
    # jawny alias wiarygodności trendu (reliable już jest, ale czytelnie w rozbiciu)
    d["trend_reliable"] = bool(trend.reliable)
    # pewność PRÓBKI (ilość/kompletność danych) — może być High przy Low trendu
    d["sample_confidence"] = sample_conf
    return d


def _volume_breakdown(hevy_workouts: list) -> dict | None:
    """Rozbicie wolumenu treningowego (faza 6.3) dla acwr_detail.

    Leniwa delegacja do fetch_hevy.compute_volume_breakdown (unika zależności
    na górze pliku). Zwraca None, gdy brak treningów do analizy.
    """
    if not hevy_workouts:
        return None
    from .fetch_hevy import compute_volume_breakdown
    return compute_volume_breakdown(hevy_workouts)


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
    mfp_daily_kcal: list = field(default_factory=list)  # zjedzone kcal z MFP diary
    apple_temp: list = field(default_factory=list)

    # modele / serie (ModelBuilding)
    models: dict = field(default_factory=dict)
    acwr_info: dict = field(default_factory=dict)

    # wyniki (Analytics / Confidence / Serialization)
    readiness: Any = None
    goal_info: dict = field(default_factory=dict)
    energy_balance: dict = field(default_factory=dict)
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
    ctx.acwr_info = acwr_mod.build_acwr(ctx.hevy_workouts, ctx.target,
                                        cardio_sessions=ctx.cardio_sessions,
                                        apple_workouts=ctx.apple_workouts)
    return ctx


def analytics_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 3: logika analityczna (readiness, temp, TDEE, trendy)."""
    m = ctx.models
    assert ctx.target is not None
    # trendy obliczane PRZED readiness (potrzebny rhr_trend w osi RECOVERY)
    ctx.trend_hrv = baseline_mod.compute_trend_slope(m["hrv_series"])
    ctx.trend_rhr = baseline_mod.compute_trend_slope(m["rhr_series"])
    ctx.temp_alert = temp_mod.build_temp_alert(m["temp_series"], m["hrv_series"], ctx.target)
    ctx.readiness = compute_full_readiness(
        hrv_series=m["hrv_series"],
        rhr_series=m["rhr_series"],
        sleep_hours_today=m["sleep_hours_today"],
        acwr_result=ctx.acwr_info["result"],
        cardio_acwr=ctx.acwr_info.get("cardio"),
        cardio_7d_sessions=(ctx.acwr_info.get("cardio_detail") or {}).get("cardio_7d_sessions", 0),
        temp_alert=ctx.temp_alert,
        spo2_confirmed=False,
        gap=ctx.acwr_info.get("gap"),
        rpe_coverage_pct=(ctx.acwr_info.get("rpe_coverage") or {}).get("coverage_pct"),
        rhr_trend=ctx.trend_rhr,
    )
    ctx.goal_info = nutr_mod.build_goal_output(m["energy_series"], m["weight_info"], ctx.params)

    # bilans energetyczny: zjedzone kcal (MFP) vs wydatek (target_kcal z TDEE).
    # Kumulujący się niedobór = sygnał ryzyka urazu/infekcji (patrz energy_balance).
    from . import energy_balance as eb_mod
    target_kcal = ctx.goal_info.get("target_kcal") if ctx.goal_info.get("status") == "ok" else None
    ctx.energy_balance = eb_mod.build_energy_balance_output(
        ctx.mfp_daily_kcal, target_kcal,
    ) if target_kcal else {"status": "skipped", "reason": "brak target_kcal (TDEE niedostępny)"}

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

    # n_points musi być liczbą punktów FAKTYCZNIE leżących w oknie kalendarzowym
    # metryki (ostatnie window_days dni), NIE rozmiarem całej dostarczonej serii.
    # Payload obejmuje ACWR_LOOKBACK_DAYS=35d (dla okna chronic ACWR); to_hrv_series
    # / to_rhr_series zwracają rzadką listę (tylko dni z danymi), więc sam cap
    # rozmiaru (min(len, window)) NIE wystarcza — punkt może leżeć poza oknem
    # (np. użytkownik nie nosił zegarka w ostatnich 14d). Ten sam wzorzec błędu
    # co nutrition_adaptive.window_actual_days (rozpiętość kalendarzowa vs liczba
    # punktów) — tu filtrujemy po dacie, analogicznie do acwr.compute_acute_load
    # na wypełnionym szeregu dziennym.
    def _points_in_window(points, window: int, target) -> int:
        cutoff = target - timedelta(days=window - 1)
        return sum(1 for p in points if p.day >= cutoff)

    c_hrv = conf_mod.compute_confidence(
        n_points=_points_in_window(m["hrv_series"], 14, ctx.target), window_days=14,
        stability=conf_mod.hr_series_stability(hrv_vals),
    )
    if c_hrv:
        confidence["hrv"] = c_hrv.to_dict()
    c_rhr = conf_mod.compute_confidence(
        n_points=_points_in_window(m["rhr_series"], 14, ctx.target), window_days=14,
        stability=conf_mod.hr_series_stability(rhr_vals),
    )
    if c_rhr:
        confidence["rhr"] = c_rhr.to_dict()
    if ctx.goal_info.get("status") == "ok" and n_window and ctx.activity_vals:
        # TDEE: n_days z build_goal_output = liczba dni FAKTYCZNIE użytych w oknie
        # (komplet danych). Cap na window_days gwarantuje niezmiennik
        # n_points <= window_days niezależnie od źródła.
        tdee_n = min(ctx.goal_info.get("n_days") or n_window, n_window)
        c_tdee = conf_mod.compute_confidence(
            n_points=tdee_n, window_days=n_window,
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

    gap_info = ctx.acwr_info.get("gap")
    ctx.explanations = explain_mod.build_explanations(
        hrv_deviation_pct=hrv_dev,
        rhr_deviation_bpm=rhr_dev,
        sleep_hours=m.get("sleep_hours_today"),
        sleep_missing=sleep_missing,
        trend_note=trend_note,
        acwr=asdict(ctx.acwr_info["result"]),
        rpe_coverage=rpe_cov,
        temperature=temp_mod.serialize_temp_output(ctx.temp_alert, m["temp_series"], ctx.target),
        goal=ctx.goal_info,
        gap=asdict(gap_info) if gap_info is not None else None,
        acwr_penalty=getattr(ctx.readiness, "acwr_penalty", 0),
        cardio_7d_sessions=(ctx.acwr_info.get("cardio_detail") or {}).get("cardio_7d_sessions", 0),
        cardio_7d=ctx.acwr_info.get("cardio_detail"),
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
            "volume_breakdown": _volume_breakdown(ctx.hevy_workouts),
            "cardio": ctx.acwr_info.get("cardio_detail"),
            "gap": (asdict(ctx.acwr_info["gap"]) if ctx.acwr_info.get("gap") is not None else None),
            "daily_loads_last14": [
                {"day": str(d.day), "day_of_week": _dow_label(d.day), "load": d.load}
                for d in ctx.acwr_info["daily_loads"][-14:]
            ],
        },
        temperature=temp_mod.serialize_temp_output(ctx.temp_alert, m["temp_series"], ctx.target),
        nutrition=ctx.goal_info,
        energy_balance=ctx.energy_balance or None,
        baseline_trends={
            "hrv": _serialize_trend(ctx.trend_hrv, (ctx.confidence or {}).get("hrv")),
            "rhr": _serialize_trend(ctx.trend_rhr, (ctx.confidence or {}).get("rhr")),
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
