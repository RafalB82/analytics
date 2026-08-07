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

# Typy serii pomijane w liczeniu tonażu (rozgrzewki / serie bez obciążenia)
SKIP_SET_TYPES = {"warmup"}
# Serie bez reps (Farmers Walk mierzone dystansem) — pomijamy w tonażu,
# bo nie mają klasycznego "reps", ale można je policzyć osobno przy
# customMetric (patrz UWAGA na końcu).
DISTANCE_BASED = False


def _parse_date(iso: str) -> date:
    """ISO 8601 (z 'Z' lub offset) -> date. Bezpiecznie dla endTime/null."""
    if not iso:
        return date.today()
    # obetnij offset strefowy / 'Z' i część ułamkową
    s = iso[:10]
    return datetime.strptime(s, "%Y-%m-%d").date()


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
    rpe = s.get("rpe")
    # compute_session_load(sets, reps, weight_kg, rpe); sets=1 (per seria)
    return compute_session_load(sets=1, reps=reps, weight_kg=weight, rpe=rpe)


def workout_daily_load(workout: dict) -> tuple[date, float] | None:
    """
    Suma sRPE-load całego treningu (wszystkie serie robocze), przypięta
    do dnia startu treningu. Zwraca (day, total_load) lub None gdy brak
    jakichkolwiek liczonych serii.
    """
    day = _parse_date(workout.get("startTime"))
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


def build_daily_load_series(
    workouts: list[dict],
    start: date,
    end: date,
) -> list[SessionLoad]:
    """
    Buduje pełny szereg dzienny (start..end włącznie, dni bez treningu = 0.0)
    z listy treningów Hevy (każdy dict jak z hevy__get-workouts).

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
            if start <= d <= end:
                out.append(w)
        # koniec jeśli strona była krótsza niż page_size
        if len(batch) < page_size:
            break
    return out


if __name__ == "__main__":
    import json, sys
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
