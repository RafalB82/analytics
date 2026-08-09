#!/usr/bin/env python3
"""
fetch_hevy.py — warstwa pobierania danych treningowych z Hevy MCP.

Hevy udostępnia ćwiczenia/serie/ciężary/RPE przez hevy__get-workouts (MCP).
Ten moduł dostarcza funkcję `build_daily_load_series()` zwracającą listę
SessionLoad (dzienne obciążenie sRPE-load) do modułu ACWR.

WAŻNE: sam nie woła MCP — zakłada, że agent wstrzyknie treningi jako ciąg
JSON (z hevy__get-workouts, strona po stronie). To trzyma warstwę
deterministyczną i testowalną bez zależności od MCP w środku.

Zależności: numpy (przez .acwr)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .acwr import SessionLoad, compute_session_load
from .logging import get_logger

logger = get_logger(__name__)

# Typy serii pomijane w liczeniu tonażu (rozgrzewki / serie bez obciążenia)
SKIP_SET_TYPES = {"warmup"}
# Serie bez reps (Farmers Walk mierzone dystansem) — pomijamy w tonażu,
# bo nie mają klasycznego "reps", ale można je policzyć osobno przy
# customMetric (patrz UWAGA na końcu).
DISTANCE_BASED = False


def _parse_date(iso: Any) -> date | None:
    """ISO 8601 (z 'Z' lub offset) -> date. Bezpiecznie dla endTime/null.

    Zwraca None gdy brak daty lub niezdatny format — NIE domyślna data.today().
    Trening bez sensownego startupu nie może trafić na "dziś" (fałszowałby
    acute_load w oknie 7d i ACWR ratio); brak daty = odrzuć rekord, tak samo
    jak hevy_normalize.normalize_workout (if not start.startswith("20") -> None).
    """
    if not iso:
        return None
    # obetnij offset strefowy / 'Z' i część ułamkową
    s = str(iso)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        # niezdatny format daty — nie zgaduj, odrzuć
        return None


def _set_load(s: dict) -> float | None:
    """
    Obciążenie pojedynczej serii = sets(1) * reps * weight * rpe.
    Zwraca None, jeśli seria nie nadaje się do ACWR (rozgrzewka, brak reps,
    brak ciężaru lub typ dystansowy).
    """
    if s.get("type") in SKIP_SET_TYPES:
        return None
    weight = s.get("weight")
    reps = s.get("reps")
    if weight is None or reps is None or weight == 0:
        return None
    # sanity-check: odrzucamy ewidentnie zepsute wartości (nie legalne pominięcie)
    try:
        weight_f = float(weight)
        reps_f = int(reps)
    except (TypeError, ValueError):
        return None
    if weight_f <= 0 or reps_f <= 0:
        return None
    if weight_f > 1000 or reps_f > 1000:  # absurdalne — uszkodzone dane
        return None
    rpe = s.get("rpe")
    # compute_session_load(sets, reps, weight_kg, rpe); sets=1 (per seria)
    return compute_session_load(sets=1, reps=reps_f, weight_kg=weight_f, rpe=rpe)


def workout_daily_load(workout: dict) -> tuple[date, float] | None:
    """
    Suma sRPE-load całego treningu (wszystkie serie robocze), przypięta
    do dnia startu treningu. Zwraca (day, total_load) lub None gdy brak
    jakichkolwiek liczonych serii.
    """
    day = _parse_date(workout.get("startTime"))
    if day is None:
        # brak sensownej daty startu — odrzuć trening (nie „dziś", nie zgaduj)
        logger.debug("workout bez daty startu (startTime=None/niezdatny) -> odrzucony")
        return None
    total = 0.0
    any_counted = False
    for ex in workout.get("exercises", []):
        for s in ex.get("sets", []):
            load = _set_load(s)
            if load is not None:
                total += load
                any_counted = True
    if not any_counted:
        return None
    return day, round(total, 1)


def compute_cardio_session_load(duration_minutes: float, rpe: float) -> float:
    """
    Obciążenie sesji cardio (np. MTB) w TEJ SAMEJ skali co siłownia.
    sRPE-load = czas (min) * RPE — analogicznie do tonaż * RPE w
    compute_session_load. Czas jest tu odpowiednikiem "tonażu" (objętości),
    RPE skaluje go do subiektywnego wysiłku.

    duration_minutes: czas trwania sesji w minutach (np. 90 dla 1.5h MTB).
    rpe: subiektywny wysiłek 1-10 (np. 6 = umiarkowanie ciężko).

    Zwraca load w jednostkach "min·RPE", sumowalny per dzień razem
    z tonażem z Hevy (oba to objętość-ważona-wysiłkiem).
    """
    if duration_minutes <= 0:
        raise ValueError("duration_minutes musi być > 0")
    if not (1 <= rpe <= 10):
        raise ValueError("RPE musi być w 1-10")
    return round(duration_minutes * rpe, 1)


def cardio_session_daily_load(cardio_session: dict) -> tuple[date, float] | None:
    """
    Obciążenie jednej sesji cardio (MTB) przypięte do dnia.
    Dict jak: {"startTime": "2026-08-05T08:00:00", "duration_minutes": 90, "rpe": 6}.
    Zwraca (day, load) lub None gdy brak wymaganych pól.
    """
    duration = cardio_session.get("duration_minutes")
    rpe = cardio_session.get("rpe")
    if duration is None or rpe is None:
        return None
    try:
        duration_f = float(duration)
        rpe_f = float(rpe)
    except (TypeError, ValueError):
        return None
    if duration_f <= 0 or not (1 <= rpe_f <= 10):
        return None
    day = _parse_date(cardio_session.get("startTime"))
    if day is None:
        # brak daty -> odrzuć sesję (nie przypisuj do „dziś")
        logger.debug("cardio bez daty startu -> odrzucone")
        return None
    return day, compute_cardio_session_load(duration_f, rpe_f)


def build_daily_load_series(
    workouts: list[dict],
    start: date,
    end: date,
    cardio_sessions: list[dict] | None = None,
) -> list[SessionLoad]:
    """
    Buduje pełny szereg dzienny (start..end włącznie, dni bez treningu = 0.0)
    z listy treningów Hevy (każdy dict jak z hevy__get-workouts) ORAZ
    opcjonalnych sesji cardio (np. MTB).

    cardio_sessions: lista dictów {"startTime", "duration_minutes", "rpe"} —
    obciążenie cardio (min·RPE) sumuje się per dzień razem z tonażem z Hevy,
    więc wieczór siłowy + poranna jazda MTB obciążają ACWR łącznie.

    workouts: wszystkie treningi (można ciągnąć stronami i skleić).
    start/end: okno dla rolling window ACWR (np. end=today, start=today-28d).
    """
    from .acwr import aggregate_daily_loads, fill_missing_days

    # agreguj loady po dniu (wiele sesji/dzień się sumuje)
    pairs = []
    for w in workouts:
        r = workout_daily_load(w)
        if r is not None and start <= r[0] <= end:
            pairs.append(r)

    # dołóż sesje cardio (MTB) do tego samego dziennego obciążenia
    for c in (cardio_sessions or []):
        r = cardio_session_daily_load(c)
        if r is not None and start <= r[0] <= end:
            pairs.append(r)

    daily = aggregate_daily_loads(pairs)
    return fill_missing_days(daily, start, end)


def rpe_coverage(workouts: list[dict]) -> dict:
    """
    Pokrycie RPE w dostarczonych treningach: ile serii roboczych (non-warmup)
    ma ustawione RPE vs łącznie. Przydatne, bo starsze treningi Rafała nie mają
    RPE (rpe=None) — chronic ACWR będzie wtedy liczony na samym tonażu,
    co zaniża obciążenie względem nowszych treningów z RPE.
    Zwraca {total_working, with_rpe, coverage_pct}.
    """
    total = 0
    with_rpe = 0
    for w in workouts:
        for ex in w.get("exercises", []):
            for s in ex.get("sets", []):
                if s.get("type") in SKIP_SET_TYPES:
                    continue
                if s.get("reps") is None or s.get("weight") in (None, 0):
                    continue
                total += 1
                if s.get("rpe") is not None:
                    with_rpe += 1
    coverage = round(with_rpe / total * 100, 1) if total else 0.0
    return {"total_working": total, "with_rpe": with_rpe, "coverage_pct": coverage}


def compute_volume_breakdown(workouts: list[dict]) -> dict:
    """Rozbicie wolumenu treningowego (faza 6.3) dla warstwy interpretacyjnej.

    Sedno z review: sam tonaż jest SŁABYM wskaźnikiem zmęczenia (10k kg z
    przysiadów vs 10k kg z lateral raise to zupełnie inne obciążenie
    fizjologiczne). RPE-weighted volume (tonaż × RPE) normalizuje ciężar przez
    subiektywny wysiłek i jest znacznie bardziej „trenersko" istotne.

    Liczy, bez zmiany scoringu (scoring nadal używa sRPE-load przez
    `_set_load`/`compute_session_load`):
      - working_tonnage: suma tonażu serii ROBOCZYCH (non-warmup), bez RPE
      - rpe_weighted_volume: suma tonaż×RPE dla serii z RPE
      - rpe_coverage_pct / rpe_weighted_reliable: gdy pokrycie RPE niskie,
        RPE-weighted jest mniej wiarygodny (nie nadinterpretować)
      - warmup_tonnage: dla kontekstu (rozgrzewki nie wchodzą do obciążenia roboczego)

    Zwraca dict gotowy do raportu (acwr_detail.volume_breakdown).
    """
    working_tonnage = 0.0
    rpe_weighted = 0.0
    warmup_tonnage = 0.0
    working_sets = 0
    with_rpe = 0

    for w in workouts:
        for ex in w.get("exercises", []):
            for s in ex.get("sets", []):
                wt = s.get("weight")
                reps = s.get("reps")
                if wt is None or reps is None or wt == 0:
                    continue
                try:
                    wt_f = float(wt)
                    reps_f = int(reps)
                except (TypeError, ValueError):
                    continue
                if wt_f <= 0 or reps_f <= 0 or wt_f > 1000 or reps_f > 1000:
                    continue
                tonnage = wt_f * reps_f
                if s.get("type") in SKIP_SET_TYPES:
                    warmup_tonnage += tonnage
                    continue
                working_tonnage += tonnage
                working_sets += 1
                rpe = s.get("rpe")
                if rpe is not None:
                    rpe_weighted += tonnage * float(rpe)
                    with_rpe += 1

    coverage = round(with_rpe / working_sets * 100, 1) if working_sets else 0.0
    return {
        "working_tonnage": round(working_tonnage, 0),
        "rpe_weighted_volume": round(rpe_weighted, 0),
        "warmup_tonnage": round(warmup_tonnage, 0),
        "working_sets": working_sets,
        "rpe_coverage_pct": coverage,
        "rpe_weighted_reliable": coverage >= 80.0,  # próg pokrycia RPE
    }


def fetch_workouts_impl(
    get_workouts_callable,
    pages: int = 10,
    page_size: int = 10,
    end: date | None = None,
    days_back: int = 35,
) -> list[dict]:
    """
    Pomocnik: pobiera treningi przez przekazany callable MCP (np. hevy__get-workouts)
    i zwraca listę workout dictów OGRANICZONĄ do okna [end-days_back, end].
    Ułatwia test ręczny — callable wstrzykuje agent (nie da się go 'zaimportować'
    z poziomu skryptu, bo to narzędzie OpenClaw).
    """
    from datetime import timedelta

    end = end or date.today()
    start = end - timedelta(days=days_back)
    out = []
    for page in range(1, pages + 1):
        batch = get_workouts_callable(page=page, pageSize=page_size)
        if not batch:
            break
        for w in batch:
            d = _parse_date(w.get("startTime"))
            if d is not None and start <= d <= end:
                out.append(w)
        # koniec jeśli strona była krótsza niż page_size
        if len(batch) < page_size:
            break
    return out


if __name__ == "__main__":
    import json
    import sys
    # Użycie testowe: python3 -m analytics.fetch_hevy '<json_treningow>' <start> <end>
    if len(sys.argv) < 4:
        print("usage: python3 -m analytics.fetch_hevy '<workouts_json>' <start> <end>")
        sys.exit(0)
    workouts = json.loads(sys.argv[1])
    start = date.fromisoformat(sys.argv[2])
    end = date.fromisoformat(sys.argv[3])
    series = build_daily_load_series(workouts, start, end)
    print(json.dumps(
        {"days": [{"day": str(s.day), "load": s.load} for s in series]},
        ensure_ascii=False, indent=2))
