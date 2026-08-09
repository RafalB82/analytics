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
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np

from .config import settings
from .logging import get_logger

if TYPE_CHECKING:
    from datetime import date

logger = get_logger(__name__)

# Okno wstecz (dni) dla ACWR chronic (28d) + margines
ACWR_LOOKBACK_DAYS = 35


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


@dataclass
class GapInfo:
    """Wykryta luka treningowa (detraining) — kończąca się w dniu target.

    Niezależna od ACWR ratio: ratio po powrocie z przerwy typowo pokazuje
    "niedociążenie" (acute niskie, bo mało dni treningowych w oknie 7d),
    co jest matematycznie poprawne, ale fizjologicznie mylące — pierwszy
    trening po dłuższej przerwie to często NAJWYŻSZE ryzyko (utrata
    tolerancji tkanki na obciążenie), nie najniższe. Acute:chronic ratio
    tego efektu nie modeluje (Gabbett 2016; krytyka: Impellizzeri i wsp.
    2020) — stąd osobna flaga zamiast liczenia na to, że ratio go złapie.
    """

    detected: bool
    gap_days: int               # dni bez treningu bezpośrednio przed target (0 = brak luki)
    severity: str                # "brak" | "krótka" | "długa"
    last_training_day: date | None   # ostatni dzień z load>0 przed target (None = brak historii)
    resuming_today: bool         # target sam jest dniem treningowym (load>0) po luce


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


def detect_training_gap(
    daily_series: list[SessionLoad],
    target: date,
    min_gap_days: int | None = None,
    long_gap_days: int | None = None,
) -> GapInfo:
    """
    Wykrywa lukę treningową (dni bez treningu z rzędu) bezpośrednio przed
    `target`, niezależnie od tego, co pokazuje ACWR ratio.

    daily_series musi być PEŁNYM szeregiem dziennym (patrz fill_missing_days) —
    ciągłym, bez dziur w kalendarzu, gdzie dni bez treningu mają load=0.0.
    Bez tego założenia nie da się odróżnić "dzień odpoczynku" od "brakuje
    wpisu w danych", więc ta funkcja NIE odgaduje nic poza to, co widać
    wprost w przekazanym oknie (tak samo jak compute_chronic_load — patrz
    komentarz COLD-START FIX o tym samym ograniczeniu).

    Liczy dni wstecz od (target - 1 dzień), bo target sam może być dniem
    treningowym (`resuming_today`) — luka to to, co było PRZED nim.

    min_gap_days: próg do uznania przerwy za wykrytą (domyślnie
    ACWR.gap_min_days = 7 — mniej niż tydzień to normalna przerwa
    międzytreningowa, nie detraining).
    long_gap_days: próg "długiej" luki (domyślnie ACWR.gap_long_days = 14)
    — ostrzejsza kategoria dla komunikatu/override.

    Zwraca GapInfo(detected=False, gap_days=0, severity="brak", ...) gdy
    luka krótsza niż min_gap_days albo gdy brak historii w ogóle.
    """
    if min_gap_days is None:
        min_gap_days = settings.ACWR.gap_min_days
    if long_gap_days is None:
        long_gap_days = settings.ACWR.gap_long_days

    if not daily_series:
        return GapInfo(detected=False, gap_days=0, severity="brak",
                        last_training_day=None, resuming_today=False)

    by_day = {p.day: p.load for p in daily_series}
    resuming_today = by_day.get(target, 0.0) > 0.0

    # dni wstecz od target-1: licz zera aż do pierwszego dnia treningowego
    # (load>0) albo do końca dostępnej historii
    gap_days = 0
    last_training_day: date | None = None
    cursor = target - timedelta(days=1)
    earliest = daily_series[0].day
    while cursor >= earliest:
        load = by_day.get(cursor, 0.0)
        if load > 0.0:
            last_training_day = cursor
            break
        gap_days += 1
        cursor -= timedelta(days=1)
    else:
        # pętla wyczerpała się bez break -> cała dostępna historia to same
        # zera; nie wiadomo, co było wcześniej (poza zasięgiem danych) —
        # nie raportujemy luki na podstawie samego braku danych (patrz
        # docstring: to samo ograniczenie co w compute_chronic_load)
        gap_days = 0
        last_training_day = None

    if gap_days < min_gap_days or last_training_day is None:
        return GapInfo(detected=False, gap_days=gap_days if last_training_day else 0,
                        severity="brak", last_training_day=last_training_day,
                        resuming_today=resuming_today)

    severity = "długa" if gap_days >= long_gap_days else "krótka"
    return GapInfo(detected=True, gap_days=gap_days, severity=severity,
                    last_training_day=last_training_day, resuming_today=resuming_today)


def build_gap_override_message(gap: GapInfo) -> str | None:
    """
    Komunikat dla warstwy deterministycznej (analogicznie do
    temperature.build_temp_override_message) — treść i próg decyzji
    siedzi tutaj, LLM tylko formatuje.

    Ostrzega tylko gdy `resuming_today=True` (dziś jest pierwszy powrót
    po luce) — jeśli target sam nie jest dniem treningowym, nie ma
    czego ostrzegać "dzisiaj" (flaga i tak trafia do inputs/explain jako
    kontekst historyczny).
    """
    if not gap.detected or not gap.resuming_today:
        return None
    if gap.severity == "długa":
        return (
            f"UWAGA: pierwszy trening po {gap.gap_days} dniach przerwy. "
            f"ACWR ratio może pokazywać „niedociążenie\" (mało dni w oknie 7d) — "
            f"to nie znaczy bezpiecznie. Tolerancja tkanki na obciążenie spadła; "
            f"rozważ redukcję objętości/intensywności niezależnie od strefy ACWR."
        )
    return (
        f"Powrót po {gap.gap_days} dniach przerwy. Traktuj dzisiejszy trening "
        f"ostrożniej niż sugeruje sama strefa ACWR."
    )


def compute_acute_load(daily_series: list[SessionLoad], window: int | None = None) -> float:
    """Średnia dzienna z ostatnich `window` dni (nie suma — łatwiej interpretować i porównywać z chronic)."""
    if window is None:
        window = settings.ACWR.acute_window
    recent = daily_series[-window:]
    if not recent:
        return 0.0
    logger.debug("cumulate_acute: %d dni, śr. %.1f", len(recent), np.mean([p.load for p in recent]))
    return round(float(np.mean([p.load for p in recent])), 1)


def compute_chronic_load(
    daily_series: list[SessionLoad],
    window: int | None = None,
    use_ewma: bool | None = None,
    alpha: float | None = None,
) -> float:
    """
    Średnia dzienna z ostatnich `window` dni.
    use_ewma=True (zalecane): EWMA zamiast prostego rolling mean —
    unika gwałtownych skoków przy "wypadaniu" starego dnia z okna,
    co jest znaną wadą prostego rolling average w oryginalnej metodzie.

    COLD-START FIX: EWMA inicjalizowana pojedynczym punktem (`values[0]`)
    nadaje pierwszemu dniu okna wagę nieproporcjonalnie dużą względem
    `alpha` (przy alpha=0.05 ten punkt "waży" znacznie więcej niż powinien
    jeszcze po 20+ krokach rekurencji) — wynik ratio potrafi wtedy zależeć
    od TEGO, który konkretny dzień (trening czy odpoczynek) wypadł jako
    pierwszy w oknie, a nie od faktycznego rozkładu obciążenia.

    Rozważaliśmy rozgrzewkę EWMA na danych SPRZED okna (margines
    ACWR_LOOKBACK_DAYS > chronic_window w build_acwr) — odrzucone: fill_missing_days
    zeruje zarówno prawdziwe dni odpoczynkowe, jak i dni poza faktycznym
    zasięgiem historii treningowej użytkownika, i na poziomie SessionLoad
    nie da się tych dwóch przypadków odróżnić. Branie zer "spoza zasięgu
    danych" jako sygnału odpoczynku zaniżałoby chronic tak samo błędnie,
    jak cold-start zawyżał go wcześniej (patrz test_returns_full_structure -
    28 dni realnego treningu, ale margines przed nimi to same zera
    "brak danych", nie realny wypoczynek).

    Zamiast tego: seed EWMA to średnia z CAŁEGO właściwego okna (nie sam
    pierwszy punkt) — usuwa zależność wyniku od pozycji pierwszego dnia
    treningowego w oknie, bez ryzykownego zgadywania co jest poza oknem.
    """
    if window is None:
        window = settings.ACWR.chronic_window
    if use_ewma is None:
        use_ewma = settings.ACWR.chronic_use_ewma
    if alpha is None:
        alpha = settings.ACWR.chronic_alpha

    recent = daily_series[-window:]
    if not recent:
        return 0.0

    values = [p.load for p in recent]
    if not use_ewma:
        return round(float(np.mean(values)), 1)

    # seed = średnia całego okna (nie tylko pierwszy punkt) -> wynik nie
    # zależy od tego, czy okno akurat zaczyna się dniem treningowym czy
    # odpoczynkowym; EWMA wciąż "dojeżdża" przez pełne `values`, więc
    # nowsze dni nadal mają większą wagę zgodnie z alpha
    ewma = float(np.mean(values))
    for v in values:
        ewma = alpha * v + (1 - alpha) * ewma
    return round(float(ewma), 1)


def acwr_ratio(acute: float, chronic: float) -> ACWRResult:
    """Klasyfikacja strefy ryzyka na podstawie stosunku acute/chronic."""
    ratio = 0.0 if chronic == 0 else round(acute / chronic, 2)

    if ratio < settings.ACWR.zone_low:
        zone = "niedociążenie"
    elif ratio <= settings.ACWR.zone_optimal_high:
        zone = "optymalna"
    elif ratio <= settings.ACWR.zone_elevated_high:
        zone = "podwyższone ryzyko"
    else:
        zone = "wysokie ryzyko"

    logger.info("ACWR: acute=%.1f chronic=%.1f ratio=%.2f strefa=%s", acute, chronic, ratio, zone)

    return ACWRResult(acute_load=acute, chronic_load=chronic, ratio=ratio, zone=zone)


def acwr_readiness_modifier(acwr: ACWRResult) -> int:
    """
    Modyfikator do scoringu gotowości (dodaj do sumy punktów 0-2 z HRV/RHR/snu).
    Zwraca punkty karne niezależne od HRV — bo ACWR łapie kumulację
    zmęczenia mechanicznego, na które HRV może jeszcze nie zareagować.

    Strefa "niewystarczające dane" (za mało dni cardio do wiarygodnego ratio)
    daje 0 punktów — to brak informacji, nie ryzyko. Nie karzemy za brak danych.
    """
    if acwr.zone == settings.ACWR.zone_insufficient:
        return 0
    if acwr.zone == "wysokie ryzyko":
        return 2
    if acwr.zone == "podwyższone ryzyko":
        return 1
    return 0


def acwr_combined_modifier(strength: ACWRResult, cardio: ACWRResult | None) -> int:
    """
    Łączny modyfikator gotowości z OSOBNYCH ACWR siły i cardio.

    Siła i cardio są liczone w różnych skalach (tonaż vs TRIMP), więc mają
    osobne ACWR; ten modyfikator scala je na poziomie gotowości biorąc
    MAKSIMUM punktów karnych z obu źródeł — najwyższe ryzyko wygrywa.
    Gdy cardio brak (None), zwraca tylko modyfikator siłowy (backward-compat).
    """
    str_mod = acwr_readiness_modifier(strength)
    if cardio is None:
        return str_mod
    card_mod = acwr_readiness_modifier(cardio)
    return max(str_mod, card_mod)


def build_cardio_acwr(daily_series: list[SessionLoad]) -> ACWRResult:
    """
    ACWR dla sesji wydolnościowych (cardio) — OSOBNY osobnego od siłowego.

    Cardio (z Apple Watch) liczone jest w skali TRIMP (setki), a siła
    (z Hevy) w skali tonażu (tysiące) — to różne jednostki, więc NIE można
    ich mieszać w jednym stosunku. Tę funkcję wołaj na szeregu dziennym
    TRIMP (patrz apple_cardio.build_apple_cardio_series); siłowe ACWR
    licz osobnym wywołaniem na tonarażu z Hevy, a oba połącz na poziomie
    gotowości (np. maksimum stref / suma punktów karnych).

    Zwraca ACWRResult w strefach 0.8-1.3 (stosunek — jednostki bez znaczenia).

    Guard na próbkę: gdy w oknie chronic jest zbyt mało dni z realnym
    obciążeniem (load>0), chronic jest zdominowany przez zera i ratio jest
    niewiarygodnym artefaktem (np. 3.32 z 4 sesji w 28d). Wtedy zwracamy
    strefę "niewystarczające dane" zamiast fałszywej strefy ryzyka — to
    decyzja diagnostyczna, nie karząca (readiness_modifier => 0).
    """
    n_valid = sum(1 for s in daily_series if s.load > 0)
    if n_valid < settings.ACWR.cardio_min_valid_days:
        logger.info(
            "ACWR cardio: tylko %d dni z obciążeniem w oknie chronic (< %d) "
            "-> strefa '%s' (niewiarygodne ratio)",
            n_valid, settings.ACWR.cardio_min_valid_days,
            settings.ACWR.zone_insufficient,
        )
        acute = compute_acute_load(daily_series)
        chronic = compute_chronic_load(daily_series)
        ratio = 0.0 if chronic == 0 else round(acute / chronic, 2)
        return ACWRResult(
            acute_load=acute, chronic_load=chronic, ratio=ratio,
            zone=settings.ACWR.zone_insufficient,
        )

    acute = compute_acute_load(daily_series)
    chronic = compute_chronic_load(daily_series)
    return acwr_ratio(acute, chronic)


def build_acwr(
    hevy_workouts: list,
    target: date,
    cardio_sessions: list | None = None,
    apple_workouts: list | None = None,
) -> dict:
    """Oblicza ACWR: siłowe z Hevy + wydolnościowe (cardio) z Apple Watch.

    Schemat (decyzja 2026-08-07):
    - SIŁA: wyłącznie z Hevy (tonaż·RPE). Dubl nie powstaje.
    - CARDIO: z Apple Watch (TRIMP z tętna, automatyczny).
    Oba liczone w OSOBNYM ACWR (różne jednostki — tonaż tysiące, TRIMP
    setki), łączone na poziomie gotowości przez `acwr_combined_modifier`
    (maksimum stref) gdziekolwiek jest konsumowane.

    cardio_sessions: legacy, ręczne {"startTime", "duration_minutes", "rpe"}
    — sumowane do dziennego loadu siłowego (zachowane dla kompatybilności).
    apple_workouts: list workoutów z Apple Watch (jak apple__list_recent_workouts)
    — filtrowane do cardio (ignorowane siłowe/kalisteniczne) i liczone TRIMP.

    Zależności (importowane lokalnie): build_daily_load_series / rpe_coverage
    z fetch_hevy, build_apple_cardio_series z apple_cardio. Funkcja nie
    importuje run_analysis — unikamy cyklu zależności.
    """
    from .fetch_hevy import build_daily_load_series, rpe_coverage

    start = target - timedelta(days=ACWR_LOOKBACK_DAYS)

    # --- SIŁA (Hevy) ---
    daily_loads = build_daily_load_series(hevy_workouts, start, target, cardio_sessions=cardio_sessions)
    acute = compute_acute_load(daily_loads, window=settings.ACWR.acute_window)
    chronic = compute_chronic_load(daily_loads, window=settings.ACWR.chronic_window,
                                   use_ewma=settings.ACWR.chronic_use_ewma)
    acwr_res = acwr_ratio(acute, chronic)
    rpe_cov = rpe_coverage(hevy_workouts)
    gap_strength = detect_training_gap(daily_loads, target)

    # --- CARDIO (Apple Watch) ---
    cardio_res = None
    cardio_detail = None
    gap_cardio = None
    if apple_workouts:
        from .apple_cardio import build_apple_cardio_series
        cardio_series = build_apple_cardio_series(apple_workouts, start, target)
        cardio_res = build_cardio_acwr(cardio_series)
        # cardio_7d: obciążenie cardio z ostatnich 7 dni (okno acute).
        # cardio_7d_total = suma TRIMP (całkowita objętość, lekka + mocna).
        # cardio_7d_days = dni z JAKIMKOLWIEK TRIMP (ruch uzupełniający wliczony).
        # cardio_7d_sessions = MOCNE sesje (TRIMP >= cardio_strong_trimp_floor) —
        #   to realny sygnał "ile mocnego cardio wpadło w tydzień" (model: mocne,
        #   submaksymalne cardio obciąża blok; lekki ruch nie). Dni bez cardio = 0.
        acute_start = target - timedelta(days=settings.ACWR.acute_window - 1)
        cardio_7d = sum(s.load for s in cardio_series if s.day >= acute_start)
        strong_floor = settings.ACWR.cardio_strong_trimp_floor
        cardio_7d_sessions = sum(1 for s in cardio_series
                                 if s.day >= acute_start and s.load >= strong_floor)
        cardio_7d_days = sum(1 for s in cardio_series
                             if s.day >= acute_start and s.load > 0)
        # mocne sesje z jawnym dniem tygodnia (żeby nie liczyć go z ISO przy
        # interpretacji): lista {day, day_of_week, trimp} dla TRIMP >= próg.
        _dow = ("pon", "wt", "śr", "czw", "pt", "sb", "nd")
        strong_sessions = [
            {
                "day": s.day.isoformat(),
                "day_of_week": _dow[s.day.weekday()],
                "trimp": round(s.load, 1),
            }
            for s in cardio_series if s.day >= acute_start and s.load >= strong_floor
        ]
        gap_cardio = detect_training_gap(cardio_series, target)
        cardio_detail = {
            "acute": cardio_res.acute_load,
            "chronic": cardio_res.chronic_load,
            "ratio": cardio_res.ratio,
            "zone": cardio_res.zone,
            "n_cardio_days": sum(1 for s in cardio_series if s.load > 0),
            "cardio_7d_total": round(cardio_7d, 1),      # TRIMP w ostatnich 7d (lekka+mocna)
            "cardio_7d_days": cardio_7d_days,             # dni z jakimkolwiek cardio w 7d
            "cardio_7d_sessions": cardio_7d_sessions,     # MOCNE sesje (TRIMP>=próg) w 7d
            "strong_sessions": strong_sessions,           # list mocnych sesji + dzień tygodnia
        }

    # luka łączona: bierz tor, który faktycznie wykrył przerwę i akurat dziś
    # wznawia (resuming_today) — jeśli oba wznawiają, dłuższa luka wygrywa
    # (poważniejszy sygnał ostrożności). Gdy żaden nie wznawia dziś, ale
    # jeden wykrył lukę w historii, ten trafia do outputu jako kontekst.
    gap = gap_strength
    if gap_cardio is not None:
        both_resuming = gap_strength.resuming_today and gap_cardio.resuming_today
        if both_resuming:
            gap = gap_strength if gap_strength.gap_days >= gap_cardio.gap_days else gap_cardio
        elif gap_cardio.resuming_today and not gap_strength.resuming_today:
            gap = gap_cardio
        # domyślnie (gap_strength.resuming_today lub żaden) zostaje gap_strength

    logger.info("ACWR siła: ratio=%.2f (%s), pokrycie RPE=%.1f%% | cardio: %s | gap: %s",
                acwr_res.ratio, acwr_res.zone, rpe_cov["coverage_pct"],
                cardio_res.ratio if cardio_res else "brak",
                f"{gap.gap_days}d ({gap.severity})" if gap.detected else "brak")
    result = {
        "result": acwr_res,
        "acute": acute,
        "chronic": chronic,
        "rpe_coverage": rpe_cov,
        "daily_loads": daily_loads,
        "gap": gap,
    }
    if cardio_res is not None:
        result["cardio"] = cardio_res
        result["cardio_detail"] = cardio_detail
    return result
