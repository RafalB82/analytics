"""explain.py — Explainability Layer (faza 9.0).

Dla każdej metryki dostarcza listę STRUKTURALNYCH powodów (reason[]) —
czyli krótkich, deterministycznych wyjaśnień, które LLM może wprost użyć
zamiast reverse-engineeringowania obliczeń.

Zasada (wg roadmapy 2.0):
  „The LLM can later explain results using structured information instead of
   reverse-engineering calculations."

Dlatego ta warstwa zwraca tylko zwięzłe, konkretne przyczyny (stringi),
bez budowania prozy. Pełne zdania składa już LLM.
"""
from __future__ import annotations


def _hrv_reasons(hrv_deviation_pct: float, trend_note: str | None) -> list[str]:
    r = [f"HRV: {hrv_deviation_pct:+.1f}% względem baseline"]
    if trend_note:
        r.append(trend_note)
    return r


def _rhr_reasons(rhr_deviation_bpm: float) -> list[str]:
    return [f"RHR: {rhr_deviation_bpm:+.1f} bpm względem baseline"]


def _sleep_reasons(sleep_hours: float | None, sleep_missing: bool) -> list[str]:
    if sleep_missing or sleep_hours is None:
        return ["Brak danych o śnie na dziś"]
    return [f"Sen: {sleep_hours:.1f} h"]


def _acwr_reasons(acwr: dict, rpe_coverage: dict | None) -> list[str]:
    zone = acwr.get("zone", "?")
    ratio = acwr.get("ratio")
    reasons = [f"ACWR: strefa „{zone}\" (ratio={ratio:.2f})"]
    if rpe_coverage:
        cov = rpe_coverage.get("coverage_pct")
        if cov is not None:
            reasons.append(f"Pokrycie RPE: {cov:.0f}%")
    return reasons


def _temperature_reasons(temp: dict) -> list[str]:
    status = temp.get("status")
    if status == "no_data" or not temp.get("alert"):
        return ["Brak alertu temperatury (brak danych lub w normie)"]
    alert = temp["alert"]
    dev = alert.get("deviation_c")
    sev = alert.get("severity", "?")
    reasons = [f"Temperatura: odchylenie {dev:+.2f}°C (severity: {sev})"]
    if alert.get("combined_with_hrv_drop"):
        reasons.append("Połączone ze spadkiem HRV")
    return reasons


def _gap_reasons(gap: dict | None) -> list[str] | None:
    """Powody luki treningowej — None gdy brak sygnału (żeby nie zaśmiecać
    explanations kluczem bez treści, spójnie z resztą modułu, gdzie brak
    sygnału po prostu pomija sekcję)."""
    if not gap or not gap.get("detected"):
        return None
    days = gap.get("gap_days")
    severity = gap.get("severity", "?")
    reasons = [f"Luka treningowa: {days} dni bez treningu (severity: {severity})"]
    if gap.get("resuming_today"):
        reasons.append("Dziś pierwszy powrót po przerwie — ACWR ratio tego nie odzwierciedla")
    return reasons


def _tdee_reasons(goal: dict) -> list[str]:
    if goal.get("status") != "ok":
        return ["Cel kaloryczny niedostępny (brak danych energetycznych)"]
    reasons = [
        f"TDEE: {goal.get('tdee_kcal'):.0f} kcal (okno {goal.get('window_days')} dni)",
    ]
    goal_name = goal.get("goal", "?")
    reasons.append(f"Cel: {goal_name} -> target {goal.get('target_kcal'):.0f} kcal")
    return reasons


def build_explanations(
    *,
    hrv_deviation_pct: float | None,
    rhr_deviation_bpm: float | None,
    sleep_hours: float | None,
    sleep_missing: bool,
    trend_note: str | None,
    acwr: dict,
    rpe_coverage: dict | None,
    temperature: dict,
    goal: dict,
    gap: dict | None = None,
) -> dict[str, list[str]]:
    """Buduje explanations = {metric_name: [reason, ...]} dla AnalysisReport.

    Argumenty są już policzonymi wartościami (nie surowymi szeregami) —
    bierzemy je z PipelineContext po stage'u analitycznym.
    """
    ex: dict[str, list[str]] = {}

    if hrv_deviation_pct is not None:
        ex["hrv"] = _hrv_reasons(hrv_deviation_pct, trend_note)
    if rhr_deviation_bpm is not None:
        ex["rhr"] = _rhr_reasons(rhr_deviation_bpm)
    ex["sleep"] = _sleep_reasons(sleep_hours, sleep_missing)
    ex["acwr"] = _acwr_reasons(acwr, rpe_coverage)
    gap_reasons = _gap_reasons(gap)
    if gap_reasons is not None:
        ex["gap"] = gap_reasons
    ex["temperature"] = _temperature_reasons(temperature)
    ex["tdee"] = _tdee_reasons(goal)

    return ex


__all__ = ["build_explanations"]
