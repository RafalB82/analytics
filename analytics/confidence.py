"""
confidence.py — Confidence Score (faza 2.0).

System liczy wartości metryk (TDEE, HRV trend, readiness, ACWR, ...), ale nie
oszacowuje, jak bardzo można tym wartościom ufać. Ten moduł dostarcza
deterministyczny, czysty (bez I/O) wskaźnik wiarygodności 0-100 + etykietę
High / Medium / Low.

Składowe (ważone — patrz ConfidenceSettings):
  - ilość danych historycznych (n_points / target_n_points)
  - kompletność okna (dni z danymi / dni okna)
  - stabilność aktywności (1 - min(cv,1)), cv = std/mean
  - brak luk (1 - missing/total)

Jeśli danych jest zbyt mało (< min_points_for_confidence), zwracamy None —
nie fabrykujemy wiarygodności tam, gdzie nie ma podstaw.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config.settings import CONFIDENCE as _CFG


@dataclass(frozen=True)
class ConfidenceInfo:
    """Wynik confidence dla jednej metryki."""

    score: int
    label: str          # "High" | "Medium" | "Low"
    n_points: int
    window_days: int
    completeness: float  # 0.0-1.0
    stability: float | None  # 0.0-1.0 (None gdy brak podstaw do liczenia)
    coverage: float      # 0.0-1.0 (1 - missing/total)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "label": self.label,
            "n_points": self.n_points,
            "window_days": self.window_days,
            "completeness": round(self.completeness, 3),
            "stability": round(self.stability, 3) if self.stability is not None else None,
            "coverage": round(self.coverage, 3),
        }


def _label(score: int) -> str:
    if score >= _CFG.high_min:
        return "High"
    if score >= _CFG.medium_min:
        return "Medium"
    return "Low"


def compute_confidence(
    *,
    n_points: int,
    window_days: int,
    n_missing: int = 0,
    stability: float | None = None,
) -> ConfidenceInfo | None:
    """Liczy confidence na podstawie ilości/kompletności danych i stabilności.

    Args:
        n_points: liczba punktów danych (np. len(hrv_series)).
        window_days: długość okna analizy (np. 7/14/28).
        n_missing: liczba luk w oknie.
        stability: 0.0-1.0 miara stabilności (1=pełna stabilność); None gdy
            nie da się policzyć (np. za mało punktów do odchylenia).

    Returns:
        ConfidenceInfo, albo None gdy zbyt mało danych (min_points_for_confidence).
    """
    if n_points < _CFG.min_points_for_confidence:
        return None

    total = max(window_days, n_points, 1)

    # 1) ilość danych historycznych
    h = min(n_points / max(_CFG.target_n_points, 1), 1.0)

    # 2) kompletność okna (dni z danymi / dni okna; cap dniem liczby punktów)
    effective_days = min(n_points, window_days) if window_days else n_points
    completeness = min(effective_days / window_days, 1.0) if window_days else 1.0

    # 3) stabilność (jeśli podano; inaczej traktujemy jako neutralne 0.5)
    stab = stability if stability is not None else 0.5
    stab = max(0.0, min(stab, 1.0))

    # 4) pokrycie (brak luk)
    coverage = 1.0 - min(n_missing / max(total, 1), 1.0)

    raw = (
        _CFG.w_history * h
        + _CFG.w_completeness * completeness
        + _CFG.w_stability * stab
        + _CFG.w_coverage * coverage
    )
    score = int(round(raw * 100))
    score = max(0, min(score, 100))

    return ConfidenceInfo(
        score=score,
        label=_label(score),
        n_points=n_points,
        window_days=window_days,
        completeness=round(completeness, 3),
        stability=round(stab, 3),
        coverage=round(coverage, 3),
    )


# --- wygodne skróty dla typowych metryk -------------------------------------


def hr_series_stability(values: list[float]) -> float | None:
    """Stabilność szeregu (0.0-1.0) na podstawie współczynnika zmienności.

    Zwraca None, gdy za mało punktów (<3) albo średnia = 0 (brak podstaw).
    """
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = float(var ** 0.5)
    cv = std / mean
    return max(0.0, 1.0 - min(cv, 1.0))


__all__ = ["ConfidenceInfo", "compute_confidence", "hr_series_stability"]
