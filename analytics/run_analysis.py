#!/usr/bin/env python3
"""
run_analysis.py — RĘCZNY orchestrator rozszerzonej analizy gotowości.

Łączy moduły (baseline / acwr / temperature / nutrition_adaptive /
readiness_integration) w jedną, deterministyczną analizę.

ŹRÓDŁA DANYCH (TYLKO Apple + Hevy + MFP):
    - Apple MCP  -> HRV, RHR, sen          (apple__get_daily_activity_range)
    - Hevy  MCP  -> tonaż + RPE (sRPE-load)-> ACWR
    - MFP        -> waga (OPCJONALNA, obecnie brak danych u Rafała)

SKRYPT NIE WOŁA MCP — agent wstrzykuje dane jako JSON. Dzięki temu jest
deterministyczny, testowalny offline i NIE ZASTĘPUJE istniejącego
readiness_apple.py (oba współistnieją). Uruchamiasz go ręcznie:

    python3 -m analytics.run_analysis '<input_json>'

INPUT JSON:
{
  "source": "apple+hevy+mfp",
  "target_date": "2026-08-07",          // domyślnie dziś
  "apple_daily": [ {date, resting_heart_rate, heart_rate_variability,
                     sleep:{total_hours}} ],
  "apple_temp":  [ {date, value} ],   // z apple__get_data(name='apple_sleeping_wrist_temperature')
  "hevy_workouts": [ {...} ],            // z hevy__get-workouts (strony sklejone)
  "mfp_weight":    [ {date, value} ] | null,   // opcjonalne
  "params": { "tdee_current": 2260, "phase": "utrzymanie", "bodyweight_kg": 69.9,
              "target_trend_kg_per_week": 0.0 }
}

OUTPUT: JSON z sekcjami readiness / acwr / temperature / tdee / baseline_trends.

Struktura (każda funkcja ma jedno zadanie):
    parse_input -> validate_input -> build_apple_models -> calculate_metrics
                -> serialize_output -> (save_report)
Błędy: `InsufficientDataError` -> fallback (nie fatal); `InvalidMetricError`
-> błąd danych (propagowany). Logi strukturalne przez analytics.logging.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any, cast

from pydantic import ValidationError

from . import baseline as baseline_mod
from . import temperature as temp_mod
from .config import settings
from .exceptions import InsufficientDataError, InvalidMetricError
from .logging import get_logger

logger = get_logger("run_analysis")


def _json_safe(o: Any) -> Any:
    """Rekurencyjnie zamienia typy numpy (np.bool_, np.float64, itd.) na natywne,
    żeby json.dumps nie pękał na asdict() dataclass z wartościami numpy.
    Klucze zaczynające się od '_' (wewnętrzne obiekty pomocnicze) są pomijane."""
    import numpy as np
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


# --- Faza 1: parse_input -----------------------------------------------------


def parse_input(raw: str) -> dict:
    """Parsuje wejściowy JSON do dict. Rzuca InvalidMetricError na zły JSON."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvalidMetricError("input_json", raw[:80], f"invalid_json: {e}") from e
    if not isinstance(payload, dict):
        raise InvalidMetricError("input_json", type(payload).__name__, "oczekiwano obiektu JSON")
    return payload


# --- Faza 4: analyse (logika analityczna) ------------------------------------


def _build_temp_status(temp_series, hrv_series, target: date) -> temp_mod.TempAlert:
    """Buduje obiekt TempAlert z serii temperatury i HRV (override dla readiness)."""
    if not temp_series:
        return temp_mod.TempAlert(
            triggered=False, deviation_c=0.0, baseline_c=0.0, severity="brak",
            combined_with_hrv_drop=False,
        )

    bl = temp_mod.compute_temp_baseline(temp_series)
    current_points = [p for p in temp_series if p.day == target]
    current = current_points[0].wrist_temp_c if current_points else temp_series[-1].wrist_temp_c

    hrv_dropped = False
    if hrv_series:
        bl_hrv = baseline_mod.compute_ewma_baseline(hrv_series)
        if bl_hrv and bl_hrv.deviation_pct <= -10:
            hrv_dropped = True

    alert = temp_mod.temp_deviation_alert(current=current, baseline=bl, hrv_dropped=hrv_dropped)
    msg = temp_mod.build_temp_override_message(alert, spo2_confirmed=False)
    logger.debug("temp override: %s", msg if msg else "brak")
    return alert


def run(payload: dict) -> dict[str, Any]:
    """Główny punkt wejścia analizy. Przyjmuje dict (już sparsowany JSON).

    Deleguję do AnalyticsPipeline (analytics/pipeline.py) — 5 stage'ów:
    InputValidation -> ModelBuilding -> Analytics -> Confidence -> Serialization.

    - `InsufficientDataError`  -> status fallback (brak danych, nie fatal)
    - `InvalidMetricError`     -> status error (problem z danymi/konfiguracją)
    """
    from .pipeline import PIPELINE, PipelineContext

    try:
        ctx = PIPELINE.run(PipelineContext(
            source=payload.get("source", ""),
            target=payload.get("target_date"),
            params=payload.get("params", {}),
            apple_daily=payload.get("apple_daily", []),
            hevy_workouts=payload.get("hevy_workouts", []),
            apple_workouts=payload.get("apple_workouts", []),
            cardio_sessions=payload.get("cardio_sessions", []),
            mfp_weight=payload.get("mfp_weight") or [],
            apple_temp=payload.get("apple_temp", []),
        ))
        target = ctx.target
        readiness = ctx.readiness
        # Serializacja przez Pydantic (model_dump mode=json) + _json_safe na wszelki
        # wypadek gdyby zagnieżdżone wartości numpy przeszły przez dict-y (np. ACWR).
        out = cast(dict[str, Any], _json_safe(ctx.report.model_dump(mode="json")))
        logger.info("analiza OK dla %s (strefa %s)", target, readiness.zone)
        return out

    except InsufficientDataError as e:
        logger.warning("fallback: %s", e)
        return {"status": "fallback", "reason": str(e)}
    except InvalidMetricError as e:
        logger.error("błąd danych: %s", e)
        return {"status": "error", "error": str(e)}
    except ValidationError as e:
        logger.error("błąd walidacji modelu: %s", e)
        return {"status": "error", "error": f"validation_error: {e}"}


def main(argv: list[str] | None = None) -> int:
    """CLI: python3 -m analytics.run_analysis '<input_json>'."""
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 1:
        print(json.dumps({
            "status": "error",
            "error": "usage: python3 -m analytics.run_analysis '<input_json>'",
            "schema": "apple_daily + hevy_workouts + mfp_weight(opt) + params",
        }, ensure_ascii=False))
        return 0
    try:
        payload = parse_input(argv[0])
    except InvalidMetricError as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 1
    result = run(payload)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
