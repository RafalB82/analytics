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


def _d(d: dict) -> date:
    return date.fromisoformat(str(d.get("date"))[:10])


def to_hrv_series(daily: list[dict]) -> list[MetricPoint]:
    """HRV (ms) jako szereg MetricPoint. Pomija dni z None."""
    out = []
    for d in daily:
        v = d.get("heart_rate_variability")
        if v is not None:
            out.append(MetricPoint(day=_d(d), value=float(v)))
    out.sort(key=lambda p: p.day)
    return out


def to_rhr_series(daily: list[dict]) -> list[MetricPoint]:
    """RHR (bpm) jako szereg MetricPoint. Pomija dni z None."""
    out = []
    for d in daily:
        v = d.get("resting_heart_rate")
        if v is not None:
            out.append(MetricPoint(day=_d(d), value=float(v)))
    out.sort(key=lambda p: p.day)
    return out


def to_sleep_hours(daily: list[dict], target_date: date) -> float | None:
    """Godziny snu dla wskazanego dnia (total_hours z sleep). None gdy brak."""
    for d in daily:
        if _d(d) == target_date:
            s = d.get("sleep") or {}
            v = s.get("total_hours")
            return float(v) if v is not None else None
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
            out.append(TempPoint(day=_d(d), wrist_temp_c=float(v)))
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
        out.append(TempPoint(day=date.fromisoformat(str(d)[:10]), wrist_temp_c=float(v)))
    out.sort(key=lambda p: p.day)
    return out


def build_apple_input(
    daily: list[dict],
    target_date: date | None = None,
    temp_points: list[dict] | None = None,
) -> dict:
    """
    Składa kompletny input pod readiness_integration.compute_full_readiness,
    z wyjątkiem acwr_result i temp_alert (te pochodzą z Hevy / temperatury).
    Zwraca dict z kluczami: hrv_series, rhr_series, sleep_hours_today,
    temp_series.

    temp_points: opcjonalna lista punktów temperatury z apple__get_data
    (name='apple_sleeping_wrist_temperature') — osobne źródło, bo seria
    dzienna (get_daily_activity_range) nie zawiera temperatury.
    """
    from datetime import timedelta

    target_date = target_date or (daily[-1]["date"] if daily else date.today())
    tdate = target_date if isinstance(target_date, date) else date.fromisoformat(str(target_date)[:10])

    return {
        "hrv_series": to_hrv_series(daily),
        "rhr_series": to_rhr_series(daily),
        "sleep_hours_today": to_sleep_hours(daily, tdate),
        "temp_series": to_temp_series_from_points(temp_points) if temp_points else to_temp_series(daily),
    }


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 2:
        print("usage: python3 -m analytics.fetch_apple '<daily_json>' [target_date]")
        sys.exit(0)
    daily = json.loads(sys.argv[1])
    target = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(build_apple_input(daily, target), ensure_ascii=False, indent=2, default=str))
