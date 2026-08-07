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
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, cast

from pydantic import ValidationError

from . import acwr as acwr_mod
from . import baseline as baseline_mod
from . import confidence as conf_mod
from . import nutrition_adaptive as nutr_mod
from . import stability as stab_mod
from . import temperature as temp_mod
from .config.settings import ACWR as ACWR_CFG
from .exceptions import InsufficientDataError, InvalidMetricError
from .fetch_apple import build_apple_input
from .fetch_hevy import build_daily_load_series, rpe_coverage
from .fetch_mfp import to_weight_series
from .logging import get_logger
from .models import AnalysisReport
from .readiness_integration import compute_full_readiness

logger = get_logger("run_analysis")

ALLOWED_SOURCES = {"apple+hevy+mfp"}

# Minimalna liczba punktów HRV do analizy (baseline + trend)
MIN_HRV_POINTS = 6
# Okno wstecz (dni) dla ACWR chronic (28d) + margines
ACWR_LOOKBACK_DAYS = 35


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


# --- Faza 2: validate_input --------------------------------------------------


def validate_input(payload: dict) -> tuple[str, date, dict, list, list, list, list]:
    """Weryfikuje źródło i obecność danych. Zwraca uporządkowane składowe."""
    source = payload.get("source")
    if source not in ALLOWED_SOURCES:
        raise InvalidMetricError(
            "source", source, f"oczekiwano '{'apple+hevy+mfp'}', otrzymano '{source}'"
        )

    apple_daily = payload.get("apple_daily", [])
    if not apple_daily:
        raise InsufficientDataError("missing_apple_daily: brak danych z Apple")

    target = _parse_target(payload.get("target_date"))
    params = payload.get("params", {})
    hevy_workouts = payload.get("hevy_workouts", [])
    mfp_weight = payload.get("mfp_weight") or []
    apple_temp = payload.get("apple_temp") or []

    return source, target, params, apple_daily, hevy_workouts, mfp_weight, apple_temp


def _parse_target(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError as e:
        raise InvalidMetricError("target_date", s, f"niepoprawna data: {e}") from e


# --- Faza 3: build_models (dane -> serie analityczne) ------------------------


def build_apple_models(apple_daily: list, target: date, apple_temp: list) -> dict:
    """Konwertuje surowe dict z Apple na serie MetricPoint / sleep / temp."""
    apple_in = build_apple_input(apple_daily, target, temp_points=apple_temp)

    hrv_series = apple_in["hrv_series"]
    if not hrv_series or len(hrv_series) < MIN_HRV_POINTS:
        raise InsufficientDataError(
            f"insufficient_hrv_history: {len(hrv_series)} punktów (min. {MIN_HRV_POINTS})"
        )

    logger.info("Apple: %d HRV, %d RHR, sen=%s, temp=%d",
                len(hrv_series), len(apple_in["rhr_series"]),
                apple_in["sleep_hours_today"], len(apple_in["temp_series"]))
    return apple_in


def build_acwr(hevy_workouts: list, target: date) -> dict:
    """Oblicza ACWR z treningów Hevy (acute/chronic/ratio + pokrycie RPE)."""
    start = target - timedelta(days=ACWR_LOOKBACK_DAYS)
    daily_loads = build_daily_load_series(hevy_workouts, start, target)
    acute = acwr_mod.compute_acute_load(daily_loads, window=ACWR_CFG.acute_window)
    chronic = acwr_mod.compute_chronic_load(daily_loads, window=ACWR_CFG.chronic_window,
                                            use_ewma=ACWR_CFG.chronic_use_ewma)
    acwr_res = acwr_mod.acwr_ratio(acute, chronic)
    rpe_cov = rpe_coverage(hevy_workouts)
    logger.info("ACWR: ratio=%.2f (%s), pokrycie RPE=%.1f%%",
                acwr_res.ratio, acwr_res.zone, rpe_cov["coverage_pct"])
    return {
        "result": acwr_res,
        "acute": acute,
        "chronic": chronic,
        "rpe_coverage": rpe_cov,
        "daily_loads": daily_loads,
    }


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


def _compute_goal(energy_series, weight_info: dict, params: dict) -> dict:
    """Cel kaloryczny z aktywności Apple (TDEE + marża wg celu).

    TDEE = średnie basal + active z okna (7d, docelowo 28d) — z Apple Health.
    MFP nie uczestniczy: dostarcza tylko zjedzone kalorie/jedzenie.
    Waga (punkt kontrolny z Apple, opcjonalna) służy do białka + kontekstu.
    """
    goal = str(params.get("phase", "utrzymanie"))  # 'phase' = aktualny cel
    bodyweight = None
    if weight_info.get("present"):
        bodyweight = weight_info.get("weight_kg")

    try:
        est = nutr_mod.compute_tdee(
            energy_series=energy_series,
            goal=goal,
            bodyweight_kg=bodyweight,
        )
    except ValueError as e:
        logger.warning("TDEE: %s", e)
        return {"status": "skipped", "reason": str(e)}

    # dłuższe okno (28d) jako porównanie do aktywnego (7d)
    long_est = nutr_mod.compute_long_window_tdee(
        energy_series, goal=goal, bodyweight_kg=bodyweight,
    )
    long_info = None
    if long_est is not None:
        long_info = {
            "tdee_kcal": long_est.tdee_kcal,
            "target_kcal": long_est.target_kcal,
            "window_days": long_est.window_days,
            "n_days": long_est.n_days,
        }

    logger.info("CEL: %s -> target=%.0f kcal (TDEE 7d), long28: %s",
                goal, est.target_kcal, long_info["tdee_kcal"] if long_info else None)

    return {
        "status": "ok",
        "goal": goal,
        "tdee_kcal": est.tdee_kcal,
        "basal_kcal": est.basal_kcal,
        "active_kcal": est.active_kcal,
        "window_days": est.window_days,
        "n_days": est.n_days,
        "margin_pct": est.margin_pct,
        "target_kcal": est.target_kcal,
        "protein_g": est.protein_g,
        "activity": {
            "avg_exercise_min": est.avg_exercise_min,
            "avg_stand_min": est.avg_stand_min,
            "avg_physical_effort": est.avg_physical_effort,
            "training_days_ratio": est.training_days_ratio,
        },
        "long_window_28d": long_info,
        "weight": weight_info if weight_info.get("present") else {"present": False},
    }


# --- Faza 5: serialize_output ------------------------------------------------


def _temp_output(alert: temp_mod.TempAlert, temp_series, target: date) -> dict:
    """Serializuje alert temperatury do dictu outputu (bez obiektu wewnątrz)."""
    if not temp_series:
        return {"status": "no_data", "alert": None, "override_message": None}

    bl = temp_mod.compute_temp_baseline(temp_series)
    current_points = [p for p in temp_series if p.day == target]
    current = current_points[0].wrist_temp_c if current_points else temp_series[-1].wrist_temp_c
    return {
        "status": "ok",
        "baseline_c": bl,
        "current_c": current,
        "deviation_c": round(current - bl, 3),
        "alert": asdict(alert),
        "override_message": temp_mod.build_temp_override_message(alert, spo2_confirmed=False),
    }


def run(payload: dict) -> dict[str, Any]:
    """Główny punkt wejścia analizy. Przyjmuje dict (już sparsowany JSON).

    - `InsufficientDataError`  -> status fallback (brak danych, nie fatal)
    - `InvalidMetricError`     -> status error (problem z danymi/konfiguracją)
    """
    try:
        source, target, params, apple_daily, hevy_workouts, mfp_weight, apple_temp = \
            validate_input(payload)
        models = build_apple_models(apple_daily, target, apple_temp)
        acwr_info = build_acwr(hevy_workouts, target)
        temp_alert = _build_temp_status(models["temp_series"], models["hrv_series"], target)

        readiness = compute_full_readiness(
            hrv_series=models["hrv_series"],
            rhr_series=models["rhr_series"],
            sleep_hours_today=models["sleep_hours_today"],
            acwr_result=acwr_info["result"],
            temp_alert=temp_alert,
            spo2_confirmed=False,
        )
        goal_info = _compute_goal(models["energy_series"], models["weight_info"], params)

        trend_hrv = baseline_mod.compute_trend_slope(models["hrv_series"])
        trend_rhr = baseline_mod.compute_trend_slope(models["rhr_series"])

        # --- Confidence Score (faza 2.0) ---------------------------------------
        hrv_vals = [p.value for p in models["hrv_series"]]
        rhr_vals = [p.value for p in models["rhr_series"]]
        # aktywność: suma basal+active (kJ) per dzień -> stabilność
        activity_vals = [
            (e.basal_kj or 0.0) + (e.active_kj or 0.0)
            for e in models["energy_series"]
        ]
        act_stability = conf_mod.hr_series_stability(activity_vals) if activity_vals else None
        n_window = (
            goal_info.get("window_days", 7)
            if goal_info.get("status") == "ok"
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
        if goal_info.get("status") == "ok" and n_window and activity_vals:
            c_tdee = conf_mod.compute_confidence(
                n_points=len(activity_vals), window_days=n_window,
                stability=act_stability,
            )
            if c_tdee:
                confidence["tdee"] = c_tdee.to_dict()
        # ACWR: używamy REALNEJ liczby dni z treningiem (load>0), a nie
        # wypełnionych zerami dni (fill_missing_days) — inaczej confidence
        # byłoby sztucznie zawyżone do „pełnych danych".
        training_days = sum(1 for d in acwr_info["daily_loads"] if d.load > 0)
        c_acwr = conf_mod.compute_confidence(
            n_points=training_days, window_days=ACWR_CFG.chronic_window,
        )
        if c_acwr:
            confidence["acwr"] = c_acwr.to_dict()

        # --- Activity Stability (faza 3.0) -------------------------------------
        activity_stability = stab_mod.activity_stability(activity_vals) if activity_vals else None

        # --- Weight Trend (faza 4.0) z serii MFP (jedyna seria wielopunktowa) ---
        weight_trend = None
        if mfp_weight:
            w_series = to_weight_series(mfp_weight)
            wt = nutr_mod.compute_weight_trend(w_series)
            if wt is not None:
                weight_trend = wt.to_dict()
                logger.info("trend wagi: %+.3f kg/dzień (mediana %.2f kg, %d pkt)",
                            wt.slope_kg_per_day, wt.rolling_median_kg, wt.n_points)

        report = AnalysisReport(
            status="ok",
            source=source,
            target_date=target,
            readiness=asdict(readiness),
            acwr=asdict(acwr_info["result"]),
            acwr_detail={
                "acute_7d": acwr_info["acute"],
                "chronic_28d_ewma": acwr_info["chronic"],
                "rpe_coverage": acwr_info["rpe_coverage"],
                "daily_loads_last14": [
                    {"day": str(d.day), "load": d.load}
                    for d in acwr_info["daily_loads"][-14:]
                ],
            },
            temperature=_temp_output(temp_alert, models["temp_series"], target),
            nutrition=goal_info,
            baseline_trends={
                "hrv": (asdict(trend_hrv) if trend_hrv else None),
                "rhr": (asdict(trend_rhr) if trend_rhr else None),
            },
            confidence=confidence or None,
            weight_trend=weight_trend,
            activity_stability=(
                activity_stability.to_dict() if activity_stability else None
            ),
            inputs={
                "apple_points": {
                    "hrv": len(models["hrv_series"]),
                    "rhr": len(models["rhr_series"]),
                    "sleep_today_h": models["sleep_hours_today"],
                    "temp": len(models["temp_series"]),
                },
                "sleep_data": ("ok" if models["sleep_hours_today"] is not None else "missing"),
                "hevy_workouts_count": len(hevy_workouts),
                "mfp_weight_points": len(mfp_weight),
            },
        )
        # Serializacja przez Pydantic (model_dump mode=json) + _json_safe na wszelki
        # wypadek gdyby zagnieżdżone wartości numpy przeszły przez dict-y (np. ACWR).
        out = cast(dict[str, Any], _json_safe(report.model_dump(mode="json")))
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
