"""
readiness_integration.py
Spina moduły baseline / acwr / temperature w finalny scoring gotowości.
Wywoływane z zadania cron 08:30 (slot "Gotowość" z pipeline'u).

To jest warstwa deterministyczna — LLM dostaje już gotowy JSON i tylko
go opisuje. Zero liczenia po stronie modelu.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict

from .baseline import MetricPoint, compute_ewma_baseline, compute_trend_slope
from .acwr import ACWRResult, acwr_readiness_modifier
from .temperature import TempAlert, build_temp_override_message


@dataclass
class ReadinessOutput:
    base_score: int              # suma punktów HRV+RHR+sen (0-6, jak w oryginalnej logice)
    acwr_penalty: int            # 0-2, z modułu acwr
    total_score: int
    zone: str                    # "zielona" | "żółta" | "czerwona"
    max_rpe: str
    volume_note: str
    hard_override: str | None    # np. z temperatury — nadpisuje strefę niezależnie od total_score
    trend_note: str | None
    sleep_missing: bool            # True = brak danych o śnie (składnik snu pominięty w score)


def score_hrv_rhr_sleep(
    hrv_deviation_pct: float,
    rhr_deviation_bpm: float,
    sleep_hours: float | None,
) -> int:
    """
    Oryginalna logika 0-2 pkt / metrykę, bez zmian względem obecnego pipeline'u.

    sleep_hours=None (BRAK DANYCH o śnie) NIE dodaje kar — brak informacji
    nie jest tym samym co 0 godzin snu. Gdy sen nie został zmierzony, składnik
    snu jest pomijany, a fakt braku sygnalizuje osobna flaga w ReadinessOutput
    (sleep_missing=True), żeby warstwa wyższa mogła zaznaczyć niepełną
    ocenę regeneracji zamiast po cichu liczyć jak dla pełnych danych.
    """
    score = 0

    if hrv_deviation_pct <= -20:
        score += 2
    elif hrv_deviation_pct <= -10:
        score += 1

    if rhr_deviation_bpm >= 5:
        score += 2
    elif rhr_deviation_bpm >= 3:
        score += 1

    if sleep_hours is not None:
        if sleep_hours < 5.5:
            score += 2
        elif sleep_hours < 6.5:
            score += 1

    return score


def classify_zone(total_score: int) -> tuple[str, str, str]:
    if total_score <= 1:
        return "zielona", "RPE 9 ostatnia seria / 8 reszta", "pełna objętość"
    if total_score <= 3:
        return "żółta", "max RPE 8 wszędzie", "bez zmian objętości"
    return "czerwona", "max RPE 7 lub regeneracja", "objętość -30-40%"


def compute_full_readiness(
    hrv_series: list[MetricPoint],
    rhr_series: list[MetricPoint],
    sleep_hours_today: float,
    acwr_result: ACWRResult,
    temp_alert: TempAlert,
    spo2_confirmed: bool,
) -> ReadinessOutput:

    hrv_baseline = compute_ewma_baseline(hrv_series)
    rhr_baseline = compute_ewma_baseline(rhr_series)

    if hrv_baseline is None or rhr_baseline is None:
        raise ValueError("Za mało danych historycznych na baseline (min. 6 dni)")

    base = score_hrv_rhr_sleep(
        hrv_deviation_pct=hrv_baseline.deviation_pct,
        rhr_deviation_bpm=rhr_baseline.deviation_abs,
        sleep_hours=sleep_hours_today,
    )

    acwr_penalty = acwr_readiness_modifier(acwr_result)
    total = base + acwr_penalty

    zone, max_rpe, volume_note = classify_zone(total)

    # trend HRV jako dodatkowa informacja (nie zmienia wprost scoringu,
    # ale wpływa na notatkę tekstową dla LLM)
    trend = compute_trend_slope(hrv_series)
    trend_note = None
    if trend and trend.reliable and trend.direction == "spadający":
        trend_note = "HRV w trendzie spadkowym od kilku dni — obserwuj, niezależnie od dzisiejszego wyniku."

    # twardy override z temperatury — nadpisuje strefę niezależnie od total_score
    hard_override = build_temp_override_message(temp_alert, spo2_confirmed)
    if hard_override and temp_alert.severity == "znacząca":
        zone = "czerwona"
        max_rpe = "RPE 7 lub regeneracja"
        volume_note = "objętość -30-40% (override: temperatura)"

    return ReadinessOutput(
        base_score=base,
        acwr_penalty=acwr_penalty,
        total_score=total,
        zone=zone,
        max_rpe=max_rpe,
        volume_note=volume_note,
        hard_override=hard_override,
        trend_note=trend_note,
        sleep_missing=sleep_hours_today is None,
    )


if __name__ == "__main__":
    import json
    from datetime import date, timedelta
    from acwr import (
        SessionLoad, compute_acute_load, compute_chronic_load, acwr_ratio
    )
    from temperature import temp_deviation_alert

    today = date.today()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]

    # przykładowe dane syntetyczne — podmień na realne fetch_* z Apple Health
    hrv_series = [MetricPoint(d, v) for d, v in zip(days, [55,54,56,53,52,50,48,47,45,44,43,42,41,38])]
    rhr_series = [MetricPoint(d, v) for d, v in zip(days, [46,46,45,47,47,48,48,49,49,50,50,51,51,54])]

    daily_loads = [SessionLoad(d, l) for d, l in zip(days, [3200,0,4100,0,3800,0,4500,0,4900,0,5100,0,5300,0])]
    acute = compute_acute_load(daily_loads, window=7)
    chronic = compute_chronic_load(daily_loads, window=28)
    acwr = acwr_ratio(acute, chronic)

    temp_alert = temp_deviation_alert(current=0.4, baseline=0.05, hrv_dropped=True)

    result = compute_full_readiness(
        hrv_series=hrv_series,
        rhr_series=rhr_series,
        sleep_hours_today=5.2,
        acwr_result=acwr,
        temp_alert=temp_alert,
        spo2_confirmed=False,
    )

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
