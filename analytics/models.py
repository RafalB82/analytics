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

from .config.settings import RANGES


class DailyMetrics(BaseModel):
    """Dzienne metryki fizjologiczne (jeden punkt szeregu czasowego)."""

    model_config = ConfigDict(extra="ignore")

    date: date
    hrv: float | None = Field(default=None, ge=RANGES.hrv[0], le=RANGES.hrv[1])
    rhr: float | None = Field(default=None, ge=RANGES.rhr[0], le=RANGES.rhr[1])
    sleep: float | None = Field(default=None, ge=RANGES.sleep[0], le=RANGES.sleep[1])
    weight: float | None = Field(default=None, ge=RANGES.weight[0], le=RANGES.weight[1])
    temperature: float | None = Field(default=None, ge=RANGES.temperature[0], le=RANGES.temperature[1])


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


class TempAlertPayload(BaseModel):
    """Serializowalny kształt alertu temperatury (bez atrybutów wewnętrznych)."""

    triggered: bool
    deviation_c: float
    baseline_c: float
    severity: str
    combined_with_hrv_drop: bool = False


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
    """Kompletny raport analizy — kształt outputu run_analysis."""

    model_config = ConfigDict(extra="ignore")

    status: str
    source: str
    target_date: date
    readiness: dict
    acwr: dict
    acwr_detail: dict
    temperature: dict
    tdee_adaptive: dict
    baseline_trends: dict
    inputs: dict


__all__ = [
    "DailyMetrics",
    "TempAlertStatus",
    "TempAlertPayload",
    "ReadinessResult",
    "AnalysisReport",
]
