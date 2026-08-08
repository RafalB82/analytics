#!/usr/bin/env python3
"""
fetch_apple.py — warstwa pobierania danych fizjologicznych z Apple MCP.

Apple MCP (lokalny) udostępnia przez apple__get_daily_activity_range dziennie:
    { date, resting_heart_rate, heart_rate_variability, sleep:{total_hours,...}, ... }

Ta warstwa przekształca ten ciąg w formaty oczekiwane przez moduły analityczne:
    - MetricPoint (dla baseline/HRV/RHR)
    - sleep_h (dla readiness)
    - TempPoint (dla temperatury — UWAGA: Apple MCP obecnie nie raportuje
      apple_sleeping_wrist_temperature (wartości null), więc temperatura jest
      wstrzykiwana jako pusta seria i moduł temperature działa w trybie "brak sygnału").

Nie woła MCP samodzielnie — przyjmuje daily[] (listę dict z MCP) i przetwarza.
Dzięki temu jest deterministyczny i testowalny offline.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .baseline import MetricPoint
from .exceptions import InsufficientDataError
from .logging import get_logger
from .nutrition_adaptive import DailyEnergy
from .validators import hrv as _val_hrv
from .validators import rhr as _val_rhr
from .validators import sleep as _val_sleep
from .validators import temperature as _val_temp

logger = get_logger(__name__)

# Minimalna liczba punktów HRV do analizy (baseline + trend) — konsumowana
# przez build_apple_models. Wędruje z właścicielem (nie do walidatora).
MIN_HRV_POINTS = 6


def _d(d: dict) -> date:
    return date.fromisoformat(str(d.get("date"))[:10])


def to_hrv_series(daily: list[dict]) -> list[MetricPoint]:
    """HRV (ms) jako szereg MetricPoint. Pomija dni z None; waliduje zakres (rzuca InvalidMetricError)."""
    out = []
    for d in daily:
        v = d.get("heart_rate_variability")
        if v is None:
            continue
        out.append(MetricPoint(day=_d(d), value=_val_hrv(v) or 0.0))
    out.sort(key=lambda p: p.day)
    return out


def to_rhr_series(daily: list[dict]) -> list[MetricPoint]:
    """RHR (bpm) jako szereg MetricPoint. Pomija dni z None; waliduje zakres."""
    out = []
    for d in daily:
        v = d.get("resting_heart_rate")
        if v is None:
            continue
        out.append(MetricPoint(day=_d(d), value=_val_rhr(v) or 0.0))
    out.sort(key=lambda p: p.day)
    return out


def to_sleep_hours(daily: list[dict], target_date: date) -> float | None:
    """Godziny snu dla wskazanego dnia (total_hours z sleep). None gdy brak."""
    for d in daily:
        if _d(d) == target_date:
            s = d.get("sleep") or {}
            v = s.get("total_hours")
            return _val_sleep(v) if v is not None else None
    return None


def to_temp_series(daily: list[dict]) -> list[Any]:
    """
    Temperatura nadgarstka z serii dziennej `daily` (jeśli pole obecne).
    UWAGA: apple__get_daily_activity_range NIE zwraca temperatury — to pole
    nie pojawia się w serii dziennej u Rafała. Realnie temperaturę trzeba
    pobrać OSOBNO przez apple__get_data(name='apple_sleeping_wrist_temperature')
    i przekazać przez `apple_temp` (patrz to_temp_series_from_points).
    Ta funkcja zostaje tylko jako fallback, gdyby pole kiedyś się pojawiło.
    """
    from .temperature import TempPoint
    out = []
    for d in daily:
        v = d.get("apple_sleeping_wrist_temperature")
        if v is not None:
            out.append(TempPoint(day=_d(d), wrist_temp_c=_val_temp(v) or 0.0))
    out.sort(key=lambda p: p.day)
    return out


def to_temp_series_from_points(temp_points: list[dict]) -> list[Any]:
    """
    Temperatura z apple__get_data(name='apple_sleeping_wrist_temperature').
    Format z MCP: [ {"date": "2026-08-06", "value": 35.98}, ... ]
    Zwraca listę TempPoint (dla temperature.py). Pomija dni z None.
    """
    from .temperature import TempPoint
    out = []
    for p in temp_points or []:
        v = p.get("value")
        d = p.get("date")
        if v is None or d is None:
            continue
        out.append(TempPoint(day=date.fromisoformat(str(d)[:10]), wrist_temp_c=_val_temp(v) or 0.0))
    out.sort(key=lambda p: p.day)
    return out


def _energy_field(d: dict, key: str) -> float | None:
    """Bezpieczny odczyt numerycznego pola dziennego (None jeśli puste/złe)."""
    v = d.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def to_energy_series(daily: list[dict]) -> list[DailyEnergy]:
    """
    Serie dziennej aktywności (basal/active kJ + minuty ćwiczeń/stania + effort)
    z apple_daily -> lista DailyEnergy (dla nutrition_adaptive TDEE).
    Dni bez basal/active zostają z None w tych polach (liczone tylko kompletne).
    """
    out = []
    for d in daily:
        out.append(DailyEnergy(
            day=_d(d),
            basal_kj=_energy_field(d, "basal_energy_burned"),
            active_kj=_energy_field(d, "active_energy"),
            exercise_min=_energy_field(d, "apple_exercise_time"),
            stand_min=_energy_field(d, "apple_stand_time"),
            physical_effort=_energy_field(d, "physical_effort"),
        ))
    out.sort(key=lambda e: e.day)
    return out


def latest_weight(daily: list[dict]) -> dict:
    """
    Najnowszy punkt kontrolny wagi/składu ciała z apple_daily (jeśli obecny).
    Apple zwraca wagę tylko w dni, gdy użytkownik się zważył (reszta null) —
    stąd bierzemy OSTATNI dzień z niepustym weight_body_mass. Zwraca dict
    z polami weight_kg / body_fat_pct / lean_kg / bmi / height / date lub
    {'present': False} gdy brak.
    """
    for d in reversed(daily):
        w = d.get("weight_body_mass")
        if w is not None:
            return {
                "present": True,
                "date": _d(d).isoformat(),
                "weight_kg": float(w),
                "body_fat_pct": d.get("body_fat_percentage"),
                "lean_kg": d.get("lean_body_mass"),
                "bmi": d.get("body_mass_index"),
                "height": d.get("height"),
            }
    return {"present": False}


def build_apple_input(
    daily: list[dict],
    target_date: date | None = None,
    temp_points: list[dict] | None = None,
) -> dict:
    """
    Składa kompletny input pod readiness_integration.compute_full_readiness
    oraz nutrition_adaptive (TDEE z aktywności).
    Zwraca dict z kluczami: hrv_series, rhr_series, sleep_hours_today,
    temp_series, energy_series (lista DailyEnergy), weight_info (punkt/kontrola).

    temp_points: opcjonalna lista punktów temperatury z apple__get_data
    (name='apple_sleeping_wrist_temperature') — osobne źródło.
    """

    target_date = target_date or (daily[-1]["date"] if daily else date.today())
    tdate = target_date if isinstance(target_date, date) else date.fromisoformat(str(target_date)[:10])

    return {
        "hrv_series": to_hrv_series(daily),
        "rhr_series": to_rhr_series(daily),
        "sleep_hours_today": to_sleep_hours(daily, tdate),
        "temp_series": to_temp_series_from_points(temp_points) if temp_points else to_temp_series(daily),
        "energy_series": to_energy_series(daily),
        "weight_info": latest_weight(daily),
    }


def build_apple_models(apple_daily: list, target: date, apple_temp: list) -> dict:
    """Konwertuje surowe dict z Apple na serie MetricPoint / sleep / temp.

    Wrapper na build_apple_input + kontrola minimalnej liczby punktów HRV
    (MIN_HRV_POINTS) + logowanie podsumowania.
    """
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


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python3 -m analytics.fetch_apple '<daily_json>' [target_date]")
        sys.exit(0)
    daily = json.loads(sys.argv[1])
    raw_target = sys.argv[2] if len(sys.argv) > 2 else None
    if raw_target:
        from datetime import date as _date
        target_date_arg: date | None = _date.fromisoformat(raw_target)
    else:
        target_date_arg = None
    print(json.dumps(build_apple_input(daily, target_date_arg), ensure_ascii=False, indent=2, default=str))
