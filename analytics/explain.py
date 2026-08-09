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


def _acwr_penalty_reasons(
    acwr_penalty: int,
    acwr: dict,
    rpe_coverage: dict | None,
    cardio_7d_sessions: int = 0,
    cardio_7d: dict | None = None,
) -> list[str]:
    """Samowystarczalna eksplanacja ŹRÓDŁA kary ACWR w readiness.

    Problem, który to rozwiązuje: `_acwr_reasons` pokazuje tylko strefę i ratio,
    przez co LLM/czytelnik mylnie przypisuje karę do ACWR ratio (np. „kara +2 za
    ACWR 0.77 niedociążenie") — podczas gdy niedociążenie (ratio < 0.8) w ogóle
    nie karze (`acwr_readiness_modifier` => 0). Realna kara pochodzi z liczby
    MOCNYCH sesji w tygodniu (`cardio_7d_sessions` / ogólny load), nie z ratio.

    Ta funkcja jawnie rozbija, ile punktów daje każda składowa:
      - ratio siłowe: niedociążenie => 0 (nie karze), wysokie ryzyko => 2 itd.
      - cardio 7d: liczba mocnych sesji w tygodniu (przykład: 2 => +1, 3+ => +2).
    Dzięki temu eksplanacja jest trafna niezależnie od tego, czy kara faktycznie
    wystąpiła — a gdy wystąpiła, widać dokładnie skąd.
    """
    reasons = []
    zone = acwr.get("zone", "?")
    ratio = acwr.get("ratio")

    # 1) ratio siłowe — co daje (a czego NIE daje) samo ratio
    if ratio is not None:
        if zone == "niedociążenie":
            reasons.append(
                f"ACWR siła: ratio {ratio:.2f} (niedociążenie) — NIE karze gotowości "
                "(chronic > acute to argument przeciw przeciążeniu, nie za)"
            )
        elif zone == "wysokie ryzyko":
            reasons.append(f"ACWR siła: ratio {ratio:.2f} (wysokie ryzyko) — kara +2")
        elif zone == "podwyższone ryzyko":
            reasons.append(f"ACWR siła: ratio {ratio:.2f} (podwyższone ryzyko) — kara +1")
        else:  # "optymalna" lub "niewystarczające dane"
            reasons.append(f"ACWR siła: ratio {ratio:.2f} (strefa {zone}) — kara 0")

    # 2) cardio 7d — realny sygnał obciążenia tygodnia (liczba mocnych sesji)
    if cardio_7d_sessions > 0:
        from .config import settings as _s
        lo, hi = _s.ACWR.cardio_7d_penalty_thresholds
        card_pen = 2 if cardio_7d_sessions >= hi else (1 if cardio_7d_sessions >= lo else 0)
        if card_pen > 0:
            reasons.append(
                f"Kara +{card_pen} z {cardio_7d_sessions} mocnych sesji cardio w 7d "
                "(realny sygnał obciążenia tygodnia)"
            )
        else:
            reasons.append(
                f"Cardio 7d: {cardio_7d_sessions} mocnych sesji — poniżej progu kary (0 pkt)"
            )
    elif cardio_7d_sessions == 0:
        reasons.append("Cardio 7d: brak mocnych sesji — nie karze")

    # 3) podsumowanie faktycznie naliczonej kary
    if acwr_penalty > 0:
        reasons.append(f"Łączna kara obciążenia: +{acwr_penalty}")
    else:
        reasons.append("Łączna kara obciążenia: 0 (niedociążenie / brak wystarczających danych)")

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
    acwr_penalty: int = 0,
    cardio_7d_sessions: int = 0,
    cardio_7d: dict | None = None,
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
    ex["acwr_penalty"] = _acwr_penalty_reasons(
        acwr_penalty, acwr, rpe_coverage, cardio_7d_sessions, cardio_7d
    )
    gap_reasons = _gap_reasons(gap)
    if gap_reasons is not None:
        ex["gap"] = gap_reasons
    ex["temperature"] = _temperature_reasons(temperature)
    ex["tdee"] = _tdee_reasons(goal)

    return ex


__all__ = ["build_explanations"]
