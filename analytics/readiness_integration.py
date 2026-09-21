"""
readiness_integration.py
Spina moduły baseline / acwr / temperature w finalny scoring gotowości.
Wywoływane z zadania cron 08:30 (slot "Gotowość" z pipeline'u).

To jest warstwa deterministyczna — LLM dostaje już gotowy JSON i tylko
go opisuje. Zero liczenia po stronie modelu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .acwr import (
    ACWRResult,
    GapInfo,
    acwr_readiness_modifier,
    build_gap_override_message,
)
from .baseline import (
    MetricPoint,
    TrendResult,
    compute_ewma_baseline,
    compute_trend_slope,
    is_current,
)
from .config import settings
from .exceptions import InsufficientDataError, MissingBaselineError
from .logging import get_logger
from .temperature import TempAlert, build_temperature_alert_message

logger = get_logger(__name__)


@dataclass
class ReadinessOutput:
    base_score: int              # suma punktów HRV+RHR+sen (0-6, jak w oryginalnej logice)
    acwr_penalty: int            # 0-2, z modułu acwr
    total_score: int
    # UWAGA (faza 6.2b/10.4): `zone` to legacy scoring (zielona/żółta/czerwona,
    # z sumy total_score). Do REKOMENDACJI strefy używaj `verdict.zone` (green/
    # orange/red) — jest NADRZĘDNY, bo nie miesza load z fatigue (patrz build_verdict).
    zone: str                    # "zielona" | "żółta" | "czerwona" (legacy)
    max_rpe: str
    volume_note: str
    temperature_alert: str | None
    trend_note: str | None
    sleep_missing: bool            # True = brak danych o śnie (składnik snu pominięty w score)
    gap_note: str | None            # ostrzeżenie o powrocie po luce treningowej (nie zmienia total_score)

    # --- nowe: osie (faza 6.2b, additive — nie zmieniają powyższych pól) ---
    # Rozdzielenie LOAD (obciążenie treningowe) od RECOVERY (regeneracja/zmęczenie)
    # oraz DATA_QUALITY (wiarygodność). To realizuje postulat: sam „duży load"
    # bez oznak pogorszenia regeneracji NIE jest „czerwoną strefą".
    recovery: dict | None = None      # {status: ok|degraded|critical, signals:{...}}
    load: dict | None = None          # {status: low|moderate|high|very_high, components:[...]}
    data_quality: dict | None = None  # {status: high|medium|low, notes:[...]}
    verdict: dict | None = None       # {zone: green|orange|red|inconclusive, max_rpe, advice, rationale}
    # sygnały ("hrv"/"rhr") z nieaktualnym ostatnim odczytem — pominięte w scoringu
    stale_signals: list[str] = field(default_factory=list)
def score_hrv_rhr_sleep(
    hrv_deviation_pct: float | None,
    rhr_deviation_bpm: float | None,
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
    # None (nieaktualny/brakujący odczyt) = pominięcie składnika, jak przy śnie
    if hrv_deviation_pct is not None:
        if hrv_deviation_pct <= settings.READINESS.hrv_penalty_high:
            score += 2
        elif hrv_deviation_pct <= settings.READINESS.hrv_penalty_low:
            score += 1
    if rhr_deviation_bpm is not None:
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


def classify_recovery(
    base: int,
    temperature_alert: bool,
    rhr_trend: TrendResult | None = None,
) -> dict:
    """Oś RECOVERY: czy organizm pokazuje oznaki pogorszenia regeneracji.

    Składowe:
      - base (HRV+RHR+sen deviation) — główny sygnał;
      - znacząca temperatura wzmacnia recovery, ale nie tworzy samotnie critical;
      - rhr_trend (faza 6.2c): ROSNĄCY, wiarygodny trend RHR to sygnał
        OSTRZEGAWCZY (RHR rośnie od snu/stresu/odwodnienia/infekcji/alkoholu/
        pory pomiaru — nie diagnoza przeciążenia). Podbija recovery o jeden
        poziom: ok -> degraded, degraded -> critical, ale nigdy nie tworzy
        critical z czystego „ok" (to tylko sygnał, nie diagnoza).

    Status: ok | degraded | critical.
    """
    if base <= settings.READINESS.zone_green_max:   # 0-1 pkt -> brak oznak
        status = "ok"
    elif base <= settings.READINESS.zone_yellow_max:  # 2-3 pkt -> pojedyncze oznaki
        status = "degraded"
    else:                                             # 4+ pkt -> silne oznaki
        status = "critical"

    # Temperatura jest silnym sygnałem recovery, ale sama nie tworzy critical.
    if temperature_alert and status == "ok":
        status = "degraded"

    # RHR trend jako sygnał ostrzegawczy (faza 6.2c)
    rhr_warning = bool(
        rhr_trend is not None
        and rhr_trend.reliable
        and rhr_trend.direction == "rosnący"
    )
    if rhr_warning and status == "ok":
        status = "degraded"      # tylko podbicie o poziom, nie do critical
    elif rhr_warning and status == "degraded":
        status = "critical"

    return {
        "status": status,
        "base_score": base,
        "temperature_alert": temperature_alert,
        "rhr_trend_warning": rhr_warning,   # jawny sygnał ostrzegawczy
    }


def classify_load(
    strength_acute: float = 0.0,
    strength_chronic: float = 0.0,
    cardio_acute: float = 0.0,
    cardio_chronic: float = 0.0,
    cardio_7d_sessions: int = 0,
) -> dict:
    """Oś LOAD: obciążenie treningowe (bodźce), niezależnie od regeneracji.

    Klasyfikuje aktualne obciążenie na podstawie rzeczywistych acute loads.
    Kara ACWR jest osobnym sygnałem i nie jest używana jako proxy LOAD.
    """
    strength_spike = (
        strength_chronic > 0 and strength_acute / strength_chronic >= 1.5
    )
    cardio_spike = cardio_chronic > 0 and cardio_acute / cardio_chronic >= 1.5
    spike = strength_spike or cardio_spike
    if strength_acute <= 0 and cardio_acute <= 0 and cardio_7d_sessions == 0:
        status = "low"
    elif spike or cardio_7d_sessions >= settings.ACWR.cardio_7d_penalty_thresholds[1]:
        status = "high"
    else:
        status = "moderate"
    return {
        "status": status,
        "strength_absolute": round(strength_acute, 1),
        "cardio_absolute": round(cardio_acute, 1),
        "strength_spike": strength_spike,
        "cardio_spike": cardio_spike,
        "session_spike": spike,
    }


def classify_data_quality(
    sleep_missing: bool,
    cardio_insufficient: bool,
    rpe_coverage_pct: float | None = None,
    stale_signals: list[str] | None = None,
) -> dict:
    """Oś DATA_QUALITY: wiarygodność oceny.

    Składowe:
      - brak snu (sleep_missing) -> obniża,
      - cardio ACWR niewystarczające (za mało dni) -> obniża (ratio niemożliwe),
       - niskie pokrycie RPE -> obniża (rpe_weighted_tonnage mniej wiarygodny).
    Status: high | medium | low.
    """
    notes: list[str] = []
    if stale_signals:
        names = "/".join(s.upper() for s in stale_signals)
        notes.append(
            f"Nieaktualny odczyt {names} (starszy niż "
            f"{settings.BASELINE.max_current_age_days} d) — pominięty w scoringu"
        )
    if sleep_missing:
        notes.append("Brak danych o śnie — ocena regeneracji niepełna")
    if cardio_insufficient:
        notes.append("ACWR cardio niewystarczające dane (za mało dni) — ratio niekarzące, diagnostyczne")
    if rpe_coverage_pct is not None and rpe_coverage_pct < 80:
        notes.append(f"Pokrycie RPE ({rpe_coverage_pct:.0f}%) niskie — rpe_weighted_tonnage mniej wiarygodny")

    if not notes:
        status = "high"
    elif len(notes) == 1:
        status = "medium"
    else:
        status = "low"
    return {"status": status, "notes": notes}


_ZONE_RANK = {"green": 0, "orange": 1, "red": 2}


def _matrix_verdict(recovery: dict, load: dict) -> dict:
    """Macierz LOAD x RECOVERY (bez uwzględnienia legacy score).

    - LOAD niski/umiarkowany + RECOVERY ok/degraded -> green.
    - LOAD niski/umiarkowany + RECOVERY critical -> orange: silne oznaki
      pogorszenia regeneracji (HRV/RHR/sen/temperatura) nie mogą dać zgody na
      pełną objętość tylko dlatego, że obciążenie nie jest wysokie
      (infekcja / przemęczenie z innych źródeł niż trening).
    - LOAD wysoki + RECOVERY ok -> green z notą; degraded -> orange; critical -> red.
    """
    load_status = load["status"]
    rec_status = recovery["status"]
    if load_status in ("low", "moderate"):
        if rec_status == "critical":
            return {
                "zone": "orange",
                "max_rpe": "max RPE 8 wszędzie",
                "advice": "ogranicz objętość o ~30%, RPE ≤ 8",
                "rationale": (
                    f"Silne oznaki pogorszenia regeneracji przy obciążeniu {load_status} — "
                    "nie wymuszaj pełnej objętości."
                ),
            }
        return {
            "zone": "green",
            "max_rpe": "RPE 9 ostatnia seria / 8 reszta",
            "advice": "pełna objętość",
            "rationale": f"Obciążenie {load_status}, regeneracja {rec_status} — brak podstaw do ograniczeń.",
        }
    if rec_status == "ok":
        return {
            "zone": "green",
            "max_rpe": "RPE 8 wszędzie",
            "advice": "bez zmian objętości, ale obserwuj",
            "rationale": "Wysokie obciążenie, ale organizm nie pokazuje oznak pogorszenia regeneracji — ostrożność, nie czerwona strefa.",
        }
    if rec_status == "degraded":
        return {
            "zone": "orange",
            "max_rpe": "max RPE 8 wszędzie",
            "advice": "ogranicz objętość o ~30%, RPE ≤ 8",
            "rationale": "Wysokie obciążenie + pojedyncze oznaki pogorszenia regeneracji (HRV/RHR/sen/temperatura).",
        }
    return {
        "zone": "red",
        "max_rpe": "max RPE 7 lub regeneracja",
        "advice": "objętość -30-40%, RPE ≤ 7",
        "rationale": "Wysokie obciążenie + silne oznaki pogorszenia regeneracji.",
    }


def build_verdict(recovery: dict, load: dict, legacy_zone: str | None = None) -> dict:
    """Werdykt: semantyka stref wg połączenia LOAD i RECOVERY.

    NADRZĘDNOŚĆ (faza 10.4): to pole jest rekomendowaną interpretacją strefy
    dla warstwy LLM/prezentacji, ale NIE może być łagodniejsze niż twarde
    minimum z legacy score: gdy `legacy_zone == "czerwona"` (łączny score >= 4),
    werdykt jest podnoszony co najmniej do orange. Sama macierz LOAD x RECOVERY
    (patrz `_matrix_verdict`) nadal decyduje o red (wysoki load + critical).
    """
    verdict = _matrix_verdict(recovery, load)
    if legacy_zone == "czerwona" and _ZONE_RANK[verdict["zone"]] < _ZONE_RANK["orange"]:
        verdict = {
            "zone": "orange",
            "max_rpe": "max RPE 8 wszędzie",
            "advice": "ogranicz objętość o ~30%, RPE ≤ 8",
            "rationale": (
                verdict["rationale"]
                + " Łączny score gotowości wskazuje strefę czerwoną — werdykt podniesiony do orange."
            ),
        }
    return verdict


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
    rpe_coverage_pct: float | None = None,
    rhr_trend: TrendResult | None = None,
    target: date | None = None,
) -> ReadinessOutput:
    hrv_baseline = compute_ewma_baseline(hrv_series)
    rhr_baseline = compute_ewma_baseline(rhr_series)

    if hrv_baseline is None or rhr_baseline is None:
        raise MissingBaselineError("Za mało danych historycznych na baseline (min. 6 dni)")

    # ŚWIEŻOŚĆ: gdy podano target, ostatni odczyt musi być bieżący. Nieaktualny
    # sygnał jest pomijany w scoringu (jak brak snu) i flagowany; gdy oba są
    # nieaktualne, nie ma dzisiejszego sygnału regeneracji -> fallback.
    stale: list[str] = []
    if target is not None:
        if not is_current(hrv_series, target):
            stale.append("hrv")
        if not is_current(rhr_series, target):
            stale.append("rhr")
    if len(stale) == 2:
        raise InsufficientDataError(
            "stale_recovery_signals: ostatni odczyt HRV i RHR starszy niż "
            f"{settings.BASELINE.max_current_age_days} d względem {target}"
        )
    hrv_dev = None if "hrv" in stale else hrv_baseline.deviation_pct
    rhr_dev = None if "rhr" in stale else rhr_baseline.deviation_abs
    if "rhr" in stale:
        rhr_trend = None  # trend z nieaktualnych punktów nie jest sygnałem na dziś
    logger.info("readiness: HRV dev=%s%% RHR dev=%s bpm sen=%s stale=%s",
                hrv_dev, rhr_dev, sleep_hours_today, stale or "brak")
    base = score_hrv_rhr_sleep(
        hrv_deviation_pct=hrv_dev,
        rhr_deviation_bpm=rhr_dev,
        sleep_hours=sleep_hours_today,
    )

    # Kara ACWR siłowej z ratio (wiarygodne — siła regularna).
    acwr_penalty = acwr_readiness_modifier(acwr_result)

    # Kara cardio: przy nieregularnym ("szarpanym") cardio ratio jest niewiarygodne
    # (chronic zaniżone -> fałszywa strefa high). Dlatego cardio karzemy na
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
    trend = compute_trend_slope(hrv_series) if "hrv" not in stale else None
    trend_note = None
    if trend and trend.reliable and trend.direction == "spadający":
        trend_note = "HRV w trendzie spadkowym od kilku dni — obserwuj, niezależnie od dzisiejszego wyniku."

    # Temperatura pozostaje jawnym sygnałem, ale nie wymusza legacy strefy.
    temperature_message = build_temperature_alert_message(temp_alert, spo2_confirmed)

    # luka treningowa — OSTRZEŻENIE, nie modyfikator punktowy ani hard
    # override strefy. ACWR ratio po przerwie zwykle pokazuje strefę "low"
    # (matematycznie poprawne, fizjologicznie mylące — patrz GapInfo docstring),
    # więc nie chcemy podnosić total_score na podstawie samej luki (brak
    # podstaw ilościowych na konkretną liczbę punktów karnych). Zamiast tego:
    # jawna notatka tekstowa dla LLM/warstwy wyżej, niezależnie od zone.
    gap_note = build_gap_override_message(gap) if gap is not None else None

    # --- nowe osie (faza 6.2b, additive) ---
    # Rozdzielenie LOAD od RECOVERY + DATA_QUALITY + werdykt semantyczny.
    # Istniejący scoring (base/acwr_penalty/total/zone) zostaje BEZ ZMIAN —
    # osie i verdict to dodatkowa, nadrzędna interpretacja dla warstwy LLM.
    temperature_signal = temp_alert.severity == "znacząca"
    recovery = classify_recovery(base, temperature_signal, rhr_trend)
    load = classify_load(
        strength_acute=acwr_result.acute_load,
        strength_chronic=acwr_result.chronic_load,
        cardio_acute=cardio_acwr.acute_load if cardio_acwr else 0.0,
        cardio_chronic=cardio_acwr.chronic_load if cardio_acwr else 0.0,
        cardio_7d_sessions=cardio_7d_sessions,
    )

    # cardio ACWR w strefie „niewystarczające dane" = nie karze, ale obniża
    # wiarygodność oceny obciążenia cardio
    cardio_insufficient = bool(
        cardio_acwr is not None
        and cardio_acwr.zone in (settings.ACWR.zone_insufficient, "")
    )
    data_quality = classify_data_quality(
        sleep_missing=sleep_hours_today is None,
        cardio_insufficient=cardio_insufficient,
        rpe_coverage_pct=rpe_coverage_pct,
        stale_signals=stale,
    )
    verdict = build_verdict(recovery, load, legacy_zone=zone)

    return ReadinessOutput(
        base_score=base,
        acwr_penalty=acwr_penalty,
        total_score=total,
        zone=zone,
        max_rpe=max_rpe,
        volume_note=volume_note,
        temperature_alert=temperature_message,
        trend_note=trend_note,
        sleep_missing=sleep_hours_today is None,
        gap_note=gap_note,
        recovery=recovery,
        load=load,
        data_quality=data_quality,
        verdict=verdict,
        stale_signals=stale,
    )
