"""metrics.py — centralny rejestr metryk (faza 6.0).

Jedno źródło prawdy o metrykach: nazwa, jednostka, opis, normalny zakres.
Nie dubluje logiki obliczeniowej (ta jest w module per metryka) ani walidacji
(Pydantic w models.py / validators) — rejestruje METADANE + dostarcza
automatyczną dokumentację i łatwy dostęp do normal ranges dla raportów.

Każdy wpis odpowiada kluczowi używanemu w AnalysisReport / confidence,
żeby registry można było mapować 1:1 na output.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config.settings import RANGES


@dataclass(frozen=True)
class Metric:
    """Metadane pojedynczej metryki.

    - name: klucz w output / confidence (np. "hrv", "tdee").
    - description: krótki opis (do dokumentacji / LLM).
    - unit: jednostka (np. "ms", "kcal", "bpm", "C", "kg", "%", "-").
    - normal_range: (min, max) z configu, lub None gdy brak.
    - section: gdzie trafia w AnalysisReport (np. "readiness", "nutrition").
    """

    name: str
    description: str
    unit: str
    normal_range: tuple[float, float] | None
    section: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "normal_range": list(self.normal_range) if self.normal_range else None,
            "section": self.section,
        }


# Normalny zakres dla ACWR ratio (brak odpowiednika w RANGES) — definiujemy jawnie.
_ACWR_NORMAL = (0.8, 1.3)


METRICS: list[Metric] = [
    Metric("hrv", "Zmienność rytmu serca (regeneracja)", "ms", RANGES.hrv, "readiness"),
    Metric("rhr", "Tętno spoczynkowe", "bpm", RANGES.rhr, "readiness"),
    Metric("sleep", "Sen (godziny/noc)", "h", RANGES.sleep, "readiness"),
    Metric("temperature", "Temperatura nadgarstka", "C", RANGES.temperature, "temperature"),
    Metric("weight", "Masa ciała (punkt kontrolny)", "kg", RANGES.weight, "nutrition"),
    Metric("tdee", "Całkowity wydatek energetyczny (dziennie)", "kcal", None, "nutrition"),
    Metric("acwr", "Stosunek obciążenia ostrego do przewlekłego", "-", _ACWR_NORMAL, "acwr"),
    Metric("readiness", "Indeks gotowości (0-100)", "%", (0.0, 100.0), "readiness"),
    Metric("activity_stability", "Zmienność aktywności", "-", None, "activity_stability"),
    Metric("weight_trend", "Trend wagi (kg/tydzień)", "kg/week", None, "weight_trend"),
]


# Szybki słownik: name -> Metric (dla dokumentacji / raportów)
METRIC_BY_NAME: dict[str, Metric] = {m.name: m for m in METRICS}


def metrics_summary() -> list[dict]:
    """Podsumowanie wszystkich metryk (do automatycznej dokumentacji / raportu)."""
    return [m.to_dict() for m in METRICS]


__all__ = ["Metric", "METRICS", "METRIC_BY_NAME", "metrics_summary"]
