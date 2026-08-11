"""
models.py — typowane modele danych (Pydantic v2).

Zastępują komunikację przez surowe dict (zwłaszcza kruchy hack
`_temp_alert_obj` w run_analysis) typowanymi obiektami z walidacją.
Pydantic daje: automatyczną walidację zakresów, IDE autocompletion,
silne typowanie i czystą serializację (model_dump) — co eliminuje
potrzebę ręcznego `_clean()` w orchestratorze.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from .config import settings


class DailyMetrics(BaseModel):
    """Dzienne metryki fizjologiczne (jeden punkt szeregu czasowego).

    Obejmuje sygnały regeneracyjne (HRV/RHR/sen/temperatura) oraz aktywność
    energetyczną i skład ciała (z Apple Health MCP). Pola opcjonalne — dzień
    może mieć puste pola (np. waga tylko w dni ważenia).
    """

    model_config = ConfigDict(extra="ignore")

    date: date
    hrv: float | None = Field(default=None, ge=settings.RANGES.hrv[0], le=settings.RANGES.hrv[1])
    rhr: float | None = Field(default=None, ge=settings.RANGES.rhr[0], le=settings.RANGES.rhr[1])
    sleep: float | None = Field(default=None, ge=settings.RANGES.sleep[0], le=settings.RANGES.sleep[1])
    weight: float | None = Field(default=None, ge=settings.RANGES.weight[0], le=settings.RANGES.weight[1])
    temperature: float | None = Field(default=None, ge=settings.RANGES.temperature[0], le=settings.RANGES.temperature[1])

    # aktywność energetyczna (z get_daily_activity_range, po dedup w MCP)
    basal_kj: float | None = Field(default=None, ge=0)
    active_kj: float | None = Field(default=None, ge=0)
    exercise_min: float | None = Field(default=None, ge=0)
    stand_min: float | None = Field(default=None, ge=0)
    physical_effort: float | None = Field(default=None, ge=0)

    # kofeina — ESTYMACJA (patrz analytics.caffeine): liczba kaw z MFP * 70 mg.
    # Nie jest to pomiar; 1 wpis "caffè" w MFP = 1 kawa (ustalenie z Rafałem).
    # Surowiec dla docelowej wtórnej analizy (słaby sen -> kofeina poprzedniego dnia).
    caffeine_mg: float | None = Field(default=None, ge=0)

    # skład ciała (punkt kontrolny — tylko w dni ważenia)
    body_fat_pct: float | None = Field(default=None, ge=0, le=100)
    lean_kg: float | None = Field(default=None, ge=0)
    bmi: float | None = Field(default=None, ge=0)
    height: float | None = Field(default=None, ge=0)


class TempAlertPayload(BaseModel):
    """Serializowalny kształt alertu temperatury (bez atrybutów wewnętrznych)."""

    triggered: bool
    deviation_c: float
    baseline_c: float
    severity: str
    combined_with_hrv_drop: bool = False


class TempAlertStatus(BaseModel):
    """Status alertu temperatury nadgarstka — typowany zamiennik dict + `_temp_alert_obj`."""

    model_config = ConfigDict(extra="ignore")

    status: str = "no_data"  # "ok" | "no_data"
    alert: TempAlertPayload | None = None
    override_message: str | None = None
    baseline_c: float | None = None
    current_c: float | None = None
    deviation_c: float | None = None

    #: pole przenoszące obiekt TempAlert (temp_alert.py) do readiness — bez dict-hacka
    temp_alert_obj: object | None = Field(default=None, exclude=True)


class ReadinessResult(BaseModel):
    """Finalny wynik gotowości — nadbudowa nad ReadinessOutput (readiness_integration)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_score: int
    acwr_penalty: int
    total_score: int
    zone: str
    max_rpe: str
    volume_note: str
    hard_override: str | None = None
    trend_note: str | None = None
    sleep_missing: bool = False


class AnalysisReport(BaseModel):
    """Kompletny raport analizy — kształt outputu run_analysis.

    Pole sekcji żywieniowej to ``nutrition`` (spójnie z resztą kodu i testami),
    a NIE ``tdee_adaptive`` — to była rozbieżność między modelem a ``run()``.
    Pola opcjonalne (confidence / weight_trend / activity_stability / explanations)
    są domyślnie ``None`` i wchodzą w kolejnych fazach refaktoru 2.0,
    nie zmieniając istniejących sekcji (wsteczna kompatybilność outputu).
    """

    model_config = ConfigDict(extra="ignore")

    status: str
    source: str
    target_date: date
    readiness: dict
    acwr: dict
    acwr_detail: dict
    temperature: dict
    nutrition: dict
    baseline_trends: dict
    inputs: dict
    #: Dzisiejszy realny odczyt HRV/RHR + baseline + odchylenie + sen tej nocy.
    #: Uzupełnia baseline_trends (kierunek trendu) o wartość punktową "na dziś".
    recovery_today: dict | None = None

    # --- nowe sekcje (fazy 2.0), domyślnie None — wstecznie kompatybilne ---

    #: Faza 2 — Confidence Score per metryka: {metric_name: ConfidenceInfo}
    confidence: dict[str, dict] | None = None
    #: Faza 4 — Weight Trend (rolling median + slope)
    weight_trend: dict | None = None
    #: Faza 3 — Activity Stability (Stable / Moderately Variable / Highly Variable)
    activity_stability: dict | None = None
    #: Faza 9 — Explainability: {metric_name: [reason, ...]}
    explanations: dict[str, list[str]] | None = None
    #: Bilans energetyczny (wydatek vs zjedzone kcal) — ocena ryzyka niedoboru
    energy_balance: dict | None = None


__all__ = [
    "DailyMetrics",
    "TempAlertStatus",
    "TempAlertPayload",
    "ReadinessResult",
    "AnalysisReport",
]
