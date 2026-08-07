"""
acwr.py
Acute:Chronic Workload Ratio na podstawie danych z Hevy.

Referencja metodologiczna: Gabbett (2016), "The training-injury
prevention paradox". Strefa 0.8-1.3 = optymalna, >1.5 = podwyższone
ryzyko przeciążenia/kontuzji.

Zależności: numpy
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import numpy as np


@dataclass
class SessionLoad:
    day: date
    load: float                # sRPE-load (tonaż x RPE) albo sam tonaż


@dataclass
class ACWRResult:
    acute_load: float          # średnia dzienna z ostatnich 7 dni
    chronic_load: float        # średnia dzienna z ostatnich 28 dni (EWMA)
    ratio: float
    zone: str                  # "niedociążenie" | "optymalna" | "podwyższone ryzyko" | "wysokie ryzyko"


def compute_session_load(
    sets: int,
    reps: int,
    weight_kg: float,
    rpe: float | None = None,
) -> float:
    """
    Obciążenie pojedynczej sesji.
    Jeśli masz RPE z Hevy (loguj je przy każdej serii, jeśli jeszcze
    nie logujesz) -> sRPE-load = tonaż * RPE, lepiej koreluje z
    faktycznym zmęczeniem niż sam tonaż.
    Bez RPE: zwraca sam tonaż (sets * reps * weight_kg).
    """
    tonnage = sets * reps * weight_kg
    if rpe is not None:
        return tonnage * rpe
    return tonnage


def aggregate_daily_loads(sessions: list[tuple[date, float]]) -> dict[date, float]:
    """
    Sumuje obciążenia w obrębie dnia (jeśli więcej niż jedna sesja/dzień,
    np. trening + cardio). Dni bez treningu nie pojawiają się w wyniku —
    uzupełnij je zerami przed dalszym przetwarzaniem (patrz fill_missing_days).
    """
    daily: dict[date, float] = {}
    for day, load in sessions:
        daily[day] = daily.get(day, 0.0) + load
    return daily


def fill_missing_days(daily_loads: dict[date, float], start: date, end: date) -> list[SessionLoad]:
    """Uzupełnia dni bez treningu wartością 0 — konieczne dla poprawnego rolling window."""
    result = []
    current = start
    while current <= end:
        result.append(SessionLoad(day=current, load=daily_loads.get(current, 0.0)))
        current = date.fromordinal(current.toordinal() + 1)
    return result


def compute_acute_load(daily_series: list[SessionLoad], window: int = 7) -> float:
    """Średnia dzienna z ostatnich `window` dni (nie suma — łatwiej interpretować i porównywać z chronic)."""
    recent = daily_series[-window:]
    if not recent:
        return 0.0
    return round(float(np.mean([p.load for p in recent])), 1)


def compute_chronic_load(
    daily_series: list[SessionLoad],
    window: int = 28,
    use_ewma: bool = True,
    alpha: float = 0.05,
) -> float:
    """
    Średnia dzienna z ostatnich `window` dni.
    use_ewma=True (zalecane): EWMA zamiast prostego rolling mean —
    unika gwałtownych skoków przy "wypadaniu" starego dnia z okna,
    co jest znaną wadą prostego rolling average w oryginalnej metodzie.
    """
    recent = daily_series[-window:]
    if not recent:
        return 0.0

    values = [p.load for p in recent]
    if not use_ewma:
        return round(float(np.mean(values)), 1)

    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma
    return round(float(ewma), 1)


def acwr_ratio(acute: float, chronic: float) -> ACWRResult:
    """Klasyfikacja strefy ryzyka na podstawie stosunku acute/chronic."""
    if chronic == 0:
        ratio = 0.0
    else:
        ratio = round(acute / chronic, 2)

    if ratio < 0.8:
        zone = "niedociążenie"
    elif ratio <= 1.3:
        zone = "optymalna"
    elif ratio <= 1.5:
        zone = "podwyższone ryzyko"
    else:
        zone = "wysokie ryzyko"

    return ACWRResult(acute_load=acute, chronic_load=chronic, ratio=ratio, zone=zone)


def acwr_readiness_modifier(acwr: ACWRResult) -> int:
    """
    Modyfikator do scoringu gotowości (dodaj do sumy punktów 0-2 z HRV/RHR/snu).
    Zwraca punkty karne niezależne od HRV — bo ACWR łapie kumulację
    zmęczenia mechanicznego, na które HRV może jeszcze nie zareagować.
    """
    if acwr.zone == "wysokie ryzyko":
        return 2
    if acwr.zone == "podwyższone ryzyko":
        return 1
    return 0
