"""
temperature.py
Nocna temperatura nadgarstka (Apple Watch Ultra 2) jako niezależny
override w scoringu gotowości. Temperatura często reaguje na
infekcję/przetrenowanie wcześniej niż HRV/RHR.

HealthKit identifier: HKQuantityTypeIdentifierAppleSleepingWristTemperature
(dostępne od watchOS 9+, wymaga snu w opasce przez całą noc).

Zależności: numpy
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class TempPoint:
    day: date
    wrist_temp_c: float        # odchylenie względem osobistego baseline, jak zwraca HealthKit
    spo2_pct: float | None = None


@dataclass
class TempAlert:
    triggered: bool
    deviation_c: float
    baseline_c: float
    severity: str               # "brak" | "podwyższona" | "znacząca"
    combined_with_hrv_drop: bool = False


def compute_temp_baseline(series: list[TempPoint], window: int | None = None) -> float:
    """
    Baseline nocnej temperatury nadgarstka.
    Uwaga: HealthKit zwraca już wartość jako odchylenie od bazowej
    (baseline Apple liczy wewnętrznie z ok. 30 dni) — jeśli pobierasz
    surowe odchylenie od Apple, ten baseline będzie bliski 0 i służy
    raczej do wygładzenia szumu niż wyznaczenia nowej normy od zera.
    Jeśli pobierasz bezwzględną temperaturę, licz normalnie.
    """
    if window is None:
        window = settings.TEMPERATURE.baseline_window
    recent = [p.wrist_temp_c for p in series[-window:]]
    if not recent:
        return 0.0
    return round(float(np.mean(recent)), 3)


def temp_deviation_alert(
    current: float,
    baseline: float,
    threshold_c: float | None = None,
    hrv_dropped: bool = False,
) -> TempAlert:
    """
    Twardy override, nie punkt do sumy scoringu.
    threshold_c=0.3 to konserwatywny próg startowy (Apple sam raportuje
    już wygładzone odchylenie) — obserwuj swoje dane przez 2-3 tyg. i
    skoryguj próg do swojej faktycznej wariancji.

    Jeśli hrv_dropped=True jednocześnie z podwyższoną temperaturą,
    oznacz jako "znacząca" — dwa niezależne sygnały fizjologiczne
    razem znacznie zwiększają pewność, że to nie szum pomiarowy.
    """
    if threshold_c is None:
        threshold_c = settings.TEMPERATURE.threshold_c
    deviation = round(current - baseline, 3)
    triggered = deviation >= threshold_c

    if not triggered:
        severity = "brak"
    elif deviation >= threshold_c * settings.TEMPERATURE.significant_multiplier or (triggered and hrv_dropped):
        severity = "znacząca"
    else:
        severity = "podwyższona"

    logger.debug("temp alert: dev=%.3f prog=%.2f triggered=%s severity=%s", deviation, threshold_c, triggered, severity)

    return TempAlert(
        triggered=triggered,
        deviation_c=deviation,
        baseline_c=baseline,
        severity=severity,
        combined_with_hrv_drop=triggered and hrv_dropped,
    )


def spo2_confirmation(spo2_pct: float | None, baseline_spo2: float = 96.0, threshold: float = 2.0) -> bool:
    """
    Drugi sygnał potwierdzający obok temperatury.
    Spadek SpO2 o >= threshold pkt proc. poniżej typowego baseline
    (u zdrowej osoby zwykle 95-98%) wspiera hipotezę infekcji/przemęczenia
    zamiast pojedynczego artefaktu pomiaru temperatury.
    """
    if spo2_pct is None:
        return False
    return (baseline_spo2 - spo2_pct) >= threshold


def build_temp_override_message(alert: TempAlert, spo2_confirmed: bool) -> str | None:
    """
    Generuje treść dla warstwy deterministycznej (nie LLM) — LLM tylko
    sformatuje to ładniej, ale sama treść i próg decyzji siedzi tutaj.
    """
    if not alert.triggered:
        return None

    if alert.severity == "znacząca" and spo2_confirmed:
        return (
            f"UWAGA: temperatura nadgarstka +{alert.deviation_c}°C vs baseline, "
            f"potwierdzone spadkiem SpO2. Sugestia: możliwa infekcja/silne przemęczenie. "
            f"Rozważ pominięcie treningu niezależnie od wyniku HRV."
        )
    if alert.severity == "znacząca":
        return (
            f"UWAGA: temperatura nadgarstka +{alert.deviation_c}°C vs baseline, "
            f"jednocześnie ze spadkiem HRV. Traktuj gotowość jako czerwoną "
            f"niezależnie od sumy punktów."
        )
    return (
        f"Podwyższona temperatura nadgarstka (+{alert.deviation_c}°C). "
        f"Obserwuj, bez automatycznej zmiany strefy."
    )


def serialize_temp_output(alert, temp_series, target: date) -> dict:
    """Serializuje alert temperatury do dictu outputu (bez obiektu wewnątrz)."""
    if not temp_series:
        return {"status": "no_data", "alert": None, "override_message": None}

    bl = compute_temp_baseline(temp_series)
    current_points = [p for p in temp_series if p.day == target]
    current = current_points[0].wrist_temp_c if current_points else temp_series[-1].wrist_temp_c
    return {
        "status": "ok",
        "baseline_c": bl,
        "current_c": current,
        "deviation_c": round(current - bl, 3),
        "alert": asdict(alert),
        "override_message": build_temp_override_message(alert, spo2_confirmed=False),
    }
