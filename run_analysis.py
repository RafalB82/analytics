#!/usr/bin/env python3
"""
run_analysis.py — RĘCZNY orchestrator rozszerzonej analizy gotowości.

Łączy moduły z files4 (baseline / acwr / temperature / nutrition_adaptive /
readiness_integration) w jedną, deterministyczną analizę.

ŹRÓDŁA DANYCH (wg polecenia: TYLKO Apple + Hevy + MFP):
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
"""
from __future__ import annotations
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta

from . import baseline as baseline_mod
from . import acwr as acwr_mod
from . import temperature as temp_mod
from . import nutrition_adaptive as nutr_mod
from .readiness_integration import compute_full_readiness, ReadinessOutput
from .fetch_apple import build_apple_input
from .fetch_hevy import build_daily_load_series, rpe_coverage
from .fetch_mfp import to_weight_series

ALLOWED_SOURCES = {"apple+hevy+mfp"}


def _clean(o):
    """Rekurencyjnie zamienia typy numpy (np.bool_, np.float64, itd.) na natywne,
    żeby json.dumps nie pękał na asdict() dataclass z wartościami numpy.
    Klucze zaczynające się od '_' (wewnętrzne obiekty pomocnicze) są pomijane."""
    import numpy as np
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    return o


def _parse_target(s: str | None) -> date:
    if not s:
        return date.today()
    return date.fromisoformat(str(s)[:10])


def _temp_alert(temp_series, hrv_series, target_date) -> dict:
    """Ocenia temperaturę nadgarstka jeśli są dane; inaczej 'brak sygnału'.
    Uwaga: Apple zwraca BEZWZGLĘDNĄ temperaturę (~35.7-36.2°C), więc
    compute_temp_baseline liczy średnią, a temp_deviation_alert odchylenie
    od tej średniej (próg 0.3°C) — poprawny tryb dla wartości bezwzględnych."""
    if not temp_series:
        return {
            "status": "no_data",
            "alert": None,
            "override_message": None,
        }
    bl = temp_mod.compute_temp_baseline(temp_series)
    # aktualna = ostatni punkt (dzień docelowy, jeśli w serii; inaczej ostatni dostępny)
    current_points = [p for p in temp_series if p.day == target_date]
    current = current_points[0].wrist_temp_c if current_points else temp_series[-1].wrist_temp_c
    # hrv_dropped = czy HRV poniżej baseline dziś (sprzężenie dwóch sygnałów)
    hrv_dropped = False
    if hrv_series:
        bl_hrv = baseline_mod.compute_ewma_baseline(hrv_series)
        if bl_hrv and bl_hrv.deviation_pct <= -10:
            hrv_dropped = True
    alert = temp_mod.temp_deviation_alert(current=current, baseline=bl, hrv_dropped=hrv_dropped)
    msg = temp_mod.build_temp_override_message(alert, spo2_confirmed=False)
    return {
        "status": "ok",
        "baseline_c": bl,
        "current_c": current,
        "deviation_c": round(current - bl, 3),
        "alert": asdict(alert),
        "override_message": msg,
        "_temp_alert_obj": alert,   # obiekt dla compute_full_readiness (atrybuty .triggered itd.)
    }


def run(payload: dict) -> dict:
    source = payload.get("source")
    if source not in ALLOWED_SOURCES:
        return {"status": "error",
                "error": f"invalid_source: oczekiwano '{'apple+hevy+mfp'}', otrzymano '{source}'"}

    target = _parse_target(payload.get("target_date"))
    params = payload.get("params", {})
    apple_daily = payload.get("apple_daily", [])
    hevy_workouts = payload.get("hevy_workouts", [])
    mfp_weight = payload.get("mfp_weight") or []
    # temperatura: osobne źródło przez apple__get_data(name='apple_sleeping_wrist_temperature')
    apple_temp = payload.get("apple_temp") or []

    if not apple_daily:
        return {"status": "error", "error": "missing_apple_daily"}

    # --- 1. APPLE: HRV / RHR / sen / temperatura ---
    apple_in = build_apple_input(apple_daily, target, temp_points=apple_temp)
    hrv_series = apple_in["hrv_series"]
    rhr_series = apple_in["rhr_series"]
    sleep_h = apple_in["sleep_hours_today"]
    temp_series = apple_in["temp_series"]

    if not hrv_series or len(hrv_series) < 6:
        return {"status": "fallback",
                "reason": f"insufficient_hrv_history: {len(hrv_series)} punktów (min. 6)"}

    # --- 2. HEVY: ACWR (tonaż + RPE) ---
    start = target - timedelta(days=35)
    daily_loads = build_daily_load_series(hevy_workouts, start, target)
    acute = acwr_mod.compute_acute_load(daily_loads, window=7)
    chronic = acwr_mod.compute_chronic_load(daily_loads, window=28, use_ewma=True)
    acwr_res = acwr_mod.acwr_ratio(acute, chronic)
    rpe_cov = rpe_coverage(hevy_workouts)

    # --- 3. TEMPERATURA (override) ---
    temp_info = _temp_alert(temp_series, hrv_series, target)

    # --- 4. READINESS (spina wszystko) ---
    # temp_alert musi być obiektem TempAlert (nie słownikiem) — compute_full_readiness
    # czyta atrybuty .triggered/.severity na obiekcie.
    temp_alert_obj = temp_info.get("_temp_alert_obj")
    if temp_alert_obj is None:
        temp_alert_obj = temp_mod.TempAlert(
            triggered=False, deviation_c=0.0, baseline_c=0.0, severity="brak",
            combined_with_hrv_drop=False)
    try:
        readiness: ReadinessOutput = compute_full_readiness(
            hrv_series=hrv_series,
            rhr_series=rhr_series,
            sleep_hours_today=sleep_h,  # None = brak danych -> składnik snu pominięty (nie kara za brak)
            acwr_result=acwr_res,
            temp_alert=temp_alert_obj,
            spo2_confirmed=False,
        )
    except ValueError as e:
        return {"status": "fallback", "reason": str(e)}

    # --- 5. TDEE adaptive (MFP waga — opcjonalne) ---
    tdee_info = {"status": "skipped"}
    if mfp_weight:
        weight_series = to_weight_series(mfp_weight)
        trend = nutr_mod.compute_weight_trend(weight_series)
        adj = nutr_mod.adjust_tdee(
            current_tdee=float(params.get("tdee_current", 2260)),
            weight_trend_kg_per_day=trend,
            target_trend_kg_per_week=float(params.get("target_trend_kg_per_week", 0.0)),
        )
        tdee_info = asdict(adj)
        tdee_info["status"] = "ok"
    else:
        tdee_info["note"] = "brak danych o wadze z MFP — korekta TDEE pominięta"

    # --- 6. BASELINE TRENDS (info dodatkowe) ---
    trend_hrv = baseline_mod.compute_trend_slope(hrv_series)
    trend_rhr = baseline_mod.compute_trend_slope(rhr_series)

    result = {
        "status": "ok",
        "source": source,
        "target_date": target.isoformat(),
        "readiness": asdict(readiness),
        "acwr": asdict(acwr_res),
        "acwr_detail": {
            "acute_7d": acute,
            "chronic_28d_ewma": chronic,
            "rpe_coverage": rpe_cov,
            "daily_loads_last14": [
                {"day": str(d.day), "load": d.load}
                for d in daily_loads[-14:]
            ],
        },
        "temperature": temp_info,
        "tdee_adaptive": tdee_info,
        "baseline_trends": {
            "hrv": (asdict(trend_hrv) if trend_hrv else None),
            "rhr": (asdict(trend_rhr) if trend_rhr else None),
        },
        "inputs": {
            "apple_points": {
                "hrv": len(hrv_series),
                "rhr": len(rhr_series),
                "sleep_today_h": sleep_h,
                "temp": len(temp_series),
            },
            "sleep_data": ("ok" if sleep_h is not None else "missing"),
            "hevy_workouts_count": len(hevy_workouts),
            "mfp_weight_points": len(mfp_weight),
        },
    }
    return _clean(result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "error": "usage: python3 -m analytics.run_analysis '<input_json>'",
            "schema": "apple_daily + hevy_workouts + mfp_weight(opt) + params",
        }, ensure_ascii=False))
        sys.exit(0)
    try:
        payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"invalid_json: {e}"}, ensure_ascii=False))
        sys.exit(1)
    result = run(payload)
    print(json.dumps(_clean(result), ensure_ascii=False))
