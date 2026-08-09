"""
readiness_integration.py
Spina moduły baseline / acwr / temperature w finalny scoring gotowości.
Wywoływane z zadania cron 08:30 (slot "Gotowość" z pipeline'u).

To jest warstwa deterministyczna — LLM dostaje już gotowy JSON i tylko
go opisuje. Zero liczenia po stronie modelu.
"""

from __future__ import annotations

from dataclasses import dataclass

from .acwr import (
    ACWRResult,
    GapInfo,
    acwr_readiness_modifier,
    build_gap_override_message,
)
from .baseline import MetricPoint, compute_ewma_baseline, compute_trend_slope
from .config import settings
from .exceptions import MissingBaselineError
from .logging import get_logger
from .temperature import TempAlert, build_temp_override_message

logger = get_logger(__name__)


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
    gap_note: str | None            # ostrzeżenie o powrocie po luce treningowej (nie zmienia total_score)


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

    if hrv_deviation_pct <= settings.READINESS.hrv_penalty_high:
        score += 2
    elif hrv_deviation_pct <= settings.READINESS.hrv_penalty_low:
        score += 1

    if rhr_deviation_bpm >= settings.READINESS.rhr_penalty_high:
        score += 2
    elif rhr_deviation_bpm >= settings.READINESS.rhr_penalty_low:
        score += 1

    if sleep_hours is not None:
        if sleep_hours < settings.READINESS.sleep_penalty_high_h:
            score += 2
        elif sleep_hours < settings.READINESS.sleep_penalty_low_h:
            score += 1

    return score


def classify_zone(total_score: int) -> tuple[str, str, str]:
    if total_score <= settings.READINESS.zone_green_max:
        return "zielona", "RPE 9 ostatnia seria / 8 reszta", "pełna objętość"
    if total_score <= settings.READINESS.zone_yellow_max:
        return "żółta", "max RPE 8 wszędzie", "bez zmian objętości"
    return "czerwona", "max RPE 7 lub regeneracja", "objętość -30-40%"


def _cardio_7d_penalty(sessions_7d: int) -> int:
    """Kara gotowości z liczby mocnych sesji cardio w ostatnich 7 dniach.

    Przy nieregularnym ("szarpanym") cardio ratio jest niewiarygodne, więc
    realny sygnał obciążenia tygodnia to ile mocnych sesji wpadło. Próg z
    settings.ACWR.cardio_7d_penalty_thresholds=(lo, hi):
      0-1 sesji -> 0, 2 (lo) -> +1, 3+ (hi) -> +2.
    """
    lo, hi = settings.ACWR.cardio_7d_penalty_thresholds
    if sessions_7d >= hi:
        return 2
    if sessions_7d >= lo:
        return 1
    return 0


def compute_full_readiness(
    hrv_series: list[MetricPoint],
    rhr_series: list[MetricPoint],
    sleep_hours_today: float | None,
    acwr_result: ACWRResult,
    temp_alert: TempAlert,
    spo2_confirmed: bool,
    cardio_acwr: ACWRResult | None = None,
    cardio_7d_sessions: int = 0,
    gap: GapInfo | None = None,
) -> ReadinessOutput:

    hrv_baseline = compute_ewma_baseline(hrv_series)
    rhr_baseline = compute_ewma_baseline(rhr_series)

    if hrv_baseline is None or rhr_baseline is None:
        raise MissingBaselineError("Za mało danych historycznych na baseline (min. 6 dni)")

    logger.info("readiness: HRV dev=%.1f%% RHR dev=%.1f bpm sen=%s",
                hrv_baseline.deviation_pct, rhr_baseline.deviation_abs, sleep_hours_today)

    base = score_hrv_rhr_sleep(
        hrv_deviation_pct=hrv_baseline.deviation_pct,
        rhr_deviation_bpm=rhr_baseline.deviation_abs,
        sleep_hours=sleep_hours_today,
    )

    # Kara ACWR siłowej z ratio (wiarygodne — siła regularna).
    acwr_penalty = acwr_readiness_modifier(acwr_result)

    # Kara cardio: przy nieregularnym ("szarpanym") cardio ratio jest niewiarygodne
    # (chronic zaniżone -> fałszywe wysokie ryzyko). Dlatego cardio karzemy na
    # podstawie cardio_7d_sessions — ile MOCNYCH sesji wpadło w bieżący tydzień
    # (realny sygnał wpływu na blok). Ratio cardio służy tylko gdy chronic jest
    # wiarygodny (regularne cardio, patrz ACWR.cardio_min_valid_days); wtedy
    # bierzemy max(ratio_penalty, 7d_penalty).
    cardio_penalty = _cardio_7d_penalty(cardio_7d_sessions)
    if cardio_acwr is not None and cardio_acwr.zone not in (
        settings.ACWR.zone_insufficient, ""
    ):
        cardio_penalty = max(cardio_penalty, acwr_readiness_modifier(cardio_acwr))
    acwr_penalty = max(acwr_penalty, cardio_penalty)

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

    # luka treningowa — OSTRZEŻENIE, nie modyfikator punktowy ani hard
    # override strefy. ACWR ratio po przerwie zwykle pokazuje "niedociążenie"
    # (matematycznie poprawne, fizjologicznie mylące — patrz GapInfo docstring),
    # więc nie chcemy podnosić total_score na podstawie samej luki (brak
    # podstaw ilościowych na konkretną liczbę punktów karnych). Zamiast tego:
    # jawna notatka tekstowa dla LLM/warstwy wyżej, niezależnie od zone.
    gap_note = build_gap_override_message(gap) if gap is not None else None

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
        gap_note=gap_note,
    )
