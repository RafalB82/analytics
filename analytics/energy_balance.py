"""
energy_balance.py — ocena zaspokojenia wydatku energetycznego (TDEE)
przez dostarczone kalorie (zjedzone z MFP).

DLACZEGO ISTNIEJE (luka, którą domyka):
  - `nutrition_adaptive` liczy TDEE (wydatek) i cel kaloryczny, ale NIE ocenia
    czy wydatek jest realnie pokryty zjedzonymi kcal.
  - Kumulujący się niedobór (zjedzone < wydatek) przez kilka dni upośledza
    regenerację, obniża odporność i zwiększa ryzyko kontuzji/urazu/infekcji —
    to celowe powiązanie, które wymaga JAWNEGO sygnału, nie domyślania w głowie
    agenta.

FILOZOFIA (zgodna z repozytorium):
  - Deterministyczna, offline, bez UI. Wejście = serie liczb (wydatek dzienny,
    zjedzone dziennie) + konfig. Wyjście = bilans + skumulowany niedobór +
    klasyfikacja ryzyka. Agent tylko interpretuje wynik.
  - Nie liczy TDEE ani nie pobiera kcal — bierze gotowe wartości (TDEE z
    nutrition_adaptive, kcal z MFP diary przez agenta/MCP).

WEJŚCIE:
  eaten:  lista {day: date, kcal: float} — zjedzone kcal z MFP (dziennik).
  tdee:   średni dzienny wydatek (kcal) z nutrition_adaptive (lub target_kcal,
          jeśli compare_against_target).
  target: target_date (data referencyjna; domyślnie ostatni dzień serii).

WYJŚCIE (dict):
  status, window_days, n_valid_days, eaten_mean/target_mean (średnie),
  cumulative_deficit_kcal (skumulowany niedobór w oknie),
  deficit_risk (niski/średni/wysoki), covering_pct (jaki % wydatku pokryty),
  daily: lista dziennych bilansów (dla diagnostyki).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class DailyBalance:
    """Dzienny bilans energetyczny (zjedzone - wydatek)."""

    day: date
    eaten_kcal: float | None     # zjedzone (MFP)
    expenditure_kcal: float      # wydatek (TDEE dzienny — stały w oknie)
    balance_kcal: float          # eaten - expenditure (ujemny = niedobór)


@dataclass
class EnergyBalanceResult:
    """Ocena zaspokojenia wydatku przez kcal."""

    status: str                  # ok | niewystarczające dane
    n_valid_days: int            # dni z kompletnym bilansem w oknie
    n_incomplete_days: int       # dni niepełnego logu (kcal < próg) — nie liczone w niedoborze
    window_days: int
    eaten_mean_kcal: float       # średnie zjedzone / dzień (tylko pełne dni)
    expenditure_mean_kcal: float # średni wydatek / dzień (stały = TDEE_ref)
    covering_pct: float          # eaten/expenditure * 100
    cumulative_deficit_kcal: float  # skumulowany niedobór (tylko pełne dni)
    deficit_risk: str            # niski | średni | wysoki
    daily: list[dict]            # per-day (diagnostyka) + flaga incomplete
    # --- faza 6.2d: jawna jakość danych bilansu ---
    # Nie tylko cicho wyłączamy niekompletne dni — jawnie sygnalizujemy, że
    # ocena bilansu opiera się na niepełnych danych (data_quality obniżona).
    data_quality: str = "high"   # high | medium | low
    data_quality_notes: list[str] = None  # powody obniżenia (np. niekompletne dni)

    def __post_init__(self):
        if self.data_quality_notes is None:
            self.data_quality_notes = []


def compute_energy_balance(
    eaten: list[dict],
    expenditure_kcal: float,
    window_days: int | None = None,
    min_valid_days: int | None = None,
) -> EnergyBalanceResult:
    """
    Bilans energetyczny: sumuje dzienné (zjedzone - wydatek) w oknie wstecz
    od ostatniego dnia z danymi i klasyfikuje ryzyko skumulowanego niedoboru.

    eaten: lista {day, kcal} (zjedzone z MFP), może być niepełna / pusta.
    expenditure_kcal: średni dzienny wydatek (TDEE lub target) — stały.
    window_days / min_valid_days: z configu ENERGY_BALANCE.
    """
    window = window_days or settings.ENERGY_BALANCE.balance_window_days
    min_valid = min_valid_days or settings.ENERGY_BALANCE.min_valid_days

    if expenditure_kcal <= 0 or not eaten:
        return _insufficient(eaten, window)

    # sortuj i weź ostatnie `window` dni z jedzeniem
    eaten_sorted = sorted(eaten, key=lambda e: (e.get("day") or ""))
    if not eaten_sorted:
        return _insufficient(eaten, window)
    ref = eaten_sorted[-1].get("day")
    recent = eaten_sorted
    if window < len(recent):
        # okno wstecz od ostatniego dnia z danymi
        ref_date = date.fromisoformat(str(ref)[:10])
        cutoff = ref_date - timedelta(days=window - 1)
        recent = [e for e in recent if date.fromisoformat(str(e["day"])[:10]) >= cutoff]

    n_valid = 0
    n_incomplete = 0
    cumulative = 0.0
    eaten_sum = 0.0
    daily: list[dict] = []

    # polskie 3-literowe skróty dni tygodnia (0=pon..6=nd) — jawnie w raporcie,
    # żeby nie liczyć dnia tygodnia z ISO przy interpretacji (źródło pomyłek).
    _DOW = ("pon", "wt", "śr", "czw", "pt", "sb", "nd")

    # niepełny log: kcal < wydatek * frac (np. wydatek 2500, frac 0.5 -> próg 1250).
    # Dzień z mniej niż połową wydatku to niemal na pewno niepełny log (wyjazd/
    # weekend/problemy z notowaniem), nie celowy post — nie liczymy go do niedoboru.
    floor = expenditure_kcal * settings.ENERGY_BALANCE.incomplete_frac_of_expenditure
    for e in recent:
        kcal = e.get("kcal")
        if kcal is None:
            continue
        kcal_f = float(kcal)
        bal = kcal_f - expenditure_kcal
        incomplete = kcal_f < floor
        if incomplete:
            n_incomplete += 1
        else:
            cumulative += bal
            eaten_sum += kcal_f
            n_valid += 1
        day_iso = str(e.get("day"))[:10]
        try:
            dow = _DOW[date.fromisoformat(day_iso).weekday()]
        except (ValueError, TypeError):
            dow = "?"
        daily.append({
            "day": day_iso,
            "day_of_week": dow,
            "eaten_kcal": round(kcal_f, 0),
            "expenditure_kcal": round(expenditure_kcal, 0),
            "balance_kcal": round(bal, 0),
            "incomplete": incomplete,
        })

    if n_valid < min_valid:
        return _insufficient(eaten, window, n_valid=n_valid, daily=daily)

    eaten_mean = eaten_sum / n_valid if n_valid else 0.0
    covering = (eaten_mean / expenditure_kcal * 100) if expenditure_kcal else 0.0
    risk = classify_deficit_risk(cumulative)

    logger.info(
        "energiabalans: %d pełnych + %d niepełnych dni/%d okno, śr.zjedzone=%.0f "
        "vs wydatek=%.0f (%.0f%% pokrycia), skumulowany niedobór=%.0f kcal -> %s",
        n_valid, n_incomplete, window, eaten_mean, expenditure_kcal, covering,
        cumulative, risk,
    )

    return EnergyBalanceResult(
        status="ok",
        n_valid_days=n_valid,
        n_incomplete_days=n_incomplete,
        window_days=window,
        eaten_mean_kcal=round(eaten_mean, 0),
        expenditure_mean_kcal=round(expenditure_kcal, 0),
        covering_pct=round(covering, 1),
        cumulative_deficit_kcal=round(cumulative, 0),
        deficit_risk=risk,
        daily=daily,
        **classify_balance_data_quality(n_incomplete, n_valid, window),
    )


def classify_balance_data_quality(
    n_incomplete_days: int, n_valid_days: int, window_days: int
) -> dict:
    """Jawna jakość danych bilansu energetycznego (faza 6.2d).

    Niekompletne dni (kcal < próg) są już wyłączane z kumulowanego niedoboru;
    tu dodajemy jawny wskaźnik, że ocena opiera się na niepełnych danych, żeby
    warstwa wyższa nie prezentowała bilansu jako w pełni wiarygodnego.

    Progi: 0 dni niekompletnych -> high; 1 -> medium; 2+ -> low. Jeśli niepełne
    dni stanowią większość okna (>50%), status low niezależnie od liczby.
    """
    notes: list[str] = []
    if n_incomplete_days > 0:
        notes.append(
            f"{n_incomplete_days} dzień/dni niepełnego logu — wyłączone ze skumulowanego niedoboru"
        )

    # większość okna niekompletna => ocena mało wiarygodna
    majority_incomplete = window_days > 0 and n_incomplete_days > window_days / 2
    if majority_incomplete:
        notes.append("Większość dni okna to niepełne logi — bilans mało wiarygodny")

    if majority_incomplete or n_incomplete_days >= 2:
        quality = "low"
    elif n_incomplete_days == 1:
        quality = "medium"
    else:
        quality = "high"

    return {"data_quality": quality, "data_quality_notes": notes}


def classify_deficit_risk(cumulative_deficit_kcal: float) -> str:
    """Strefa ryzyka z skumulowanego niedoboru w oknie 7d (kcal).

    cumulative_deficit_kcal < 0 = niedobór (zjedzone < wydatek).
    Ocena na wartości bezwzględnej niedoboru (progi dodatnie w settings):
    - niski:    |deficit| < deficit_low_kcal   (pokrywa wydatek lub mały niedobór)
    - średni:   deficit_low .. high            (wyraźny deficyt, obserwuj)
    - wysoki:   > deficit_high_kcal (~0.45 kg/tydz; przy treningu = ryzyko urazu)
    """
    lo = settings.ENERGY_BALANCE.deficit_low_kcal
    hi = settings.ENERGY_BALANCE.deficit_high_kcal
    deficit = -min(cumulative_deficit_kcal, 0.0)  # tylko niedobór (nadwyżka = 0)
    if deficit >= hi:
        return "wysoki"
    if deficit >= lo:
        return "średni"
    return "niski"


def _insufficient(
    eaten: list[dict], window: int, n_valid: int = 0, daily: list | None = None
) -> EnergyBalanceResult:
    """Wynik, gdy za mało danych (brak sprawdzenia — nie karać, tylko zgłosić)."""
    logger.info("energiabalans: niewystarczające dane (brak zjedzonych kcal / za mało dni)")
    return EnergyBalanceResult(
        status="niewystarczające dane",
        n_valid_days=n_valid,
        n_incomplete_days=0,
        window_days=window,
        eaten_mean_kcal=0.0,
        expenditure_mean_kcal=0.0,
        covering_pct=0.0,
        cumulative_deficit_kcal=0.0,
        deficit_risk="niewystarczające dane",
        daily=daily or [],
        data_quality="low",
        data_quality_notes=["Brak wystarczających danych do oceny bilansu energetycznego"],
    )


def build_energy_balance_output(
    eaten_series: list[dict],
    expenditure_kcal: float,
) -> dict:
    """Opcja wejścia dla build_input/pipeline: bierze serie zjedzonych kcal
    i średni wydatek (target_kcal z nutrition), zwraca dict gotowy do raportu."""
    res = compute_energy_balance(eaten_series, expenditure_kcal)
    return {
        "status": res.status,
        "window_days": res.window_days,
        "n_valid_days": res.n_valid_days,
        "n_incomplete_days": res.n_incomplete_days,
        "eaten_mean_kcal": res.eaten_mean_kcal,
        "expenditure_mean_kcal": res.expenditure_mean_kcal,
        "covering_pct": res.covering_pct,
        "data_quality": res.data_quality,          # faza 6.2d: jawny wskaźnik
        "data_quality_notes": res.data_quality_notes,
        "cumulative_deficit_kcal": res.cumulative_deficit_kcal,
        "deficit_risk": res.deficit_risk,
        "daily": res.daily,
    }


# re-export dla czytelności importów
__all__ = [
    "DailyBalance",
    "EnergyBalanceResult",
    "compute_energy_balance",
    "classify_deficit_risk",
    "build_energy_balance_output",
]
