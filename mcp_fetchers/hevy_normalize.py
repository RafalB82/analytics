#!/usr/bin/env python3
"""
hevy_normalize.py — konwersja surowych workoutów Hevy (MCP) do formatu akceptowanego
przez moduł analytics (fetch_hevy.build_daily_load_series / rpe_coverage).

DLACZEGO TEGO POTRZEBUJEMY (rozbieżność formatów, patrz README -> Problemy):
  - Hevy MCP (`hevy__get-workout`) zwraca serie z polem `weight_kg` i timestampem
    w kluczu `start_time` (ISO z offsetem "+00:00").
  - Moduł analityczny `analytics/fetch_hevy.py` czyta wyłącznie `weight` oraz
    `startTime`. Nie ma warstwy normalizującej — to agent musi dostarczyć dane
    w oczekiwanym kształcie.
  - Ten skrypt jest TĄ warstwą: bierze to, co daje MCP, i przekształca do tego,
    czego oczekuje deterministyczny rdzeń.

FILOZOFIA (zgodna z repo analytics):
  - Skrypt NIE woła MCP samodzielnie — przyjmuje surowe dane (dict/lista) na stdin
    i zwraca znormalizowany JSON na stdout. Dzięki temu jest deterministyczny,
    offline i testowalny, a pobieranie (agent przez MCP) jest rozdzielone od
    konwersji (tu).
  - Idempotentny: te same wejście -> to samo wyjście. Można przepuszczać te same
    workouty wielokrotnie bez skutków ubocznych (zero stanu).

WEJŚCIE (stdin):
  Lista surowych workoutów z `hevy__get-workout` (każdy dict ma polem `workout`),
  lub — dla wygody — sama lista pod kluczem. Akceptujemy:
    - [ {"workout": {...}}, ... ]            (pełna odpowiedź MCP)
    - [ {...workout...}, ... ]               (sama lista workoutów)
    - {"workout": {...}}                     (pojedyńczy workout)

WYJŚCIE (stdout): lista workoutów w formacie analytics:
    { "startTime": "YYYY-MM-DDTHH:MM:SSZ", "title": "...",
      "_volume": {"tonnage_total": 1234.5, "tonnage_working": 890.0},  # objętość mechaniczna
      "exercises": [ { "title": "...", "sets": [
          {"type":"normal","weight":25.0,"reps":6,"rpe":8.5}, ... ] } ] }

`_volume` to objętość mechaniczna w kg×reps: `tonnage_total` (z warmupami)
i `tonnage_working` (bez). Raportowana OBOK ACWR jako metryka obciążenia
tkanek — celowo NIE wchodzi w load gotowości (fetch_hevy._set_load pomija
warmupy). Szczegóły w README §3.

Użycie:
    cat raw_hevy.json | python3 -m mcp_fetchers.hevy_normalize > tmp/hevy_workouts.json
"""
from __future__ import annotations

import json
import sys
from contextlib import suppress

# Typy serii, które analytics i tak pomija (rozgrzewki) — rzucamy je, żeby nie
# zaśmiecać wejścia (fetch_hevy sam je odrzuca przez SKIP_SET_TYPES, ale czysto
# jest nie wciągać ich w ogóle).
DROP_SET_TYPES = {"warmup"}


def _normalize_time(iso: str) -> str:
    """'2026-08-05T17:12:48+00:00' -> '2026-08-05T17:12:48Z'.
    Analizuje tylko start workoutu; reszta (fractions/offset) obcinana do 'Z'."""
    if not iso:
        return iso
    # zostaw do sekund + Z (obetnij ułamki i offset)
    body = iso.split(".")[0]
    # przytnij ewentualny offset +00:00 / Z na końcu
    if body.endswith("Z"):
        return body
    sep = "+" if "+" in body else ("-" if "-" in body[10:] else None)
    if sep and sep in body:
        # trzymaj tylko do czasu (pierwsze 19 znaków: YYYY-MM-DDTHH:MM:SS)
        return body[:19] + "Z"
    return body + "Z"


def _normalize_set(s: dict, for_tonnage: bool = False) -> dict | None:
    """Pojedyncza seria: weight_kg -> weight.

    for_tonnage=False -> format ACWR (warmupy i serie dystansowe WYRZUCANE,
    bo nie tworzą spaczonego loadu — patrz README §3 „Czy warmupy mają
    znaczenie"). for_tonnage=True -> zwraca te samą serię, ale używaną do
    zliczenia objętości mechanicznej tonażu (z warmupami), osobno od ACWR.
    """
    set_type = s.get("type", "normal")
    if not for_tonnage and set_type in DROP_SET_TYPES:
        return None
    weight = s.get("weight_kg")
    reps = s.get("reps")
    # Farmers Walk / serie dystansowe: brak reps -> nie nadają się do tonażu
    if weight is None or reps is None:
        return None
    try:
        weight_f = float(weight)
        reps_i = int(reps)
    except (TypeError, ValueError):
        return None
    if weight_f <= 0 or reps_i <= 0:
        return None
    out: dict = {"type": set_type, "weight": weight_f, "reps": reps_i}
    if s.get("rpe") is not None:
        with suppress(TypeError, ValueError):
            out["rpe"] = float(s["rpe"])  # zły RPE dorzucamy jako brak
    return out


def _set_tonnage(s: dict) -> float:
    """Tonaż jednej serii = waga × reps (tylko serie z ciężarem i reps).
    Używane do objętości mechanicznej, NIE do ACWR."""
    weight = s.get("weight_kg")
    reps = s.get("reps")
    if weight is None or reps is None:
        return 0.0
    try:
        return float(weight) * float(reps)
    except (TypeError, ValueError):
        return 0.0


def _normalize_exercise(ex: dict) -> dict:
    return {
        "title": ex.get("title", ""),
        "sets": [x for x in (_normalize_set(s) for s in ex.get("sets", [])) if x is not None],
    }


def _workout_tonnage(wk: dict) -> dict:
    """Objętość mechaniczna workoutu (kg × reps), osobno total (z warmupami)
    i working (bez warmupów). To metryka obciążenia tkanek, raportowana obok
    ACWR — NIE miesza się w load gotowości (fetch_hevy._set_load)."""
    total = 0.0
    working = 0.0
    for ex in wk.get("exercises", []):
        for s in ex.get("sets", []):
            tg = _set_tonnage(s)
            total += tg
            if s.get("type") not in DROP_SET_TYPES:
                working += tg
    return {"tonnage_total": round(total, 1), "tonnage_working": round(working, 1)}


def normalize_workout(raw: dict) -> dict | None:
    """Pojedynczy surowy workout -> format analytics.
    Dołącza też `_volume`: objętość mechaniczną (tonnage_total z warmupami,
    tonnage_working bez) — patrz README §3. None gdy pusty/nieodwzorowalny."""
    w = raw.get("workout") if isinstance(raw, dict) and "workout" in raw else raw
    if not isinstance(w, dict):
        return None
    start = _normalize_time(w.get("start_time") or "")
    if not start.startswith("20"):
        return None  # brak sensownego timestampu -> odrzuć
    exercises = [_normalize_exercise(ex) for ex in w.get("exercises", [])]
    return {
        "startTime": start,
        "title": w.get("title", ""),
        "exercises": exercises,
        "_volume": _workout_tonnage(w),
    }


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print(json.dumps({"status": "error", "error": "brak danych na stdin"},
                         ensure_ascii=False))
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"invalid json: {e}"},
                         ensure_ascii=False))
        return 1

    # normalizacja listy (elastycznie co do kształtu wejścia)
    if isinstance(data, dict) and "workout" in data:
        workouts = [data]
    elif isinstance(data, list):
        workouts = data
    else:
        print(json.dumps({"status": "error",
                          "error": "oczekiwano listy workoutów lub {'workout': {...}}"},
                         ensure_ascii=False))
        return 1

    out = [n for n in (normalize_workout(w) for w in workouts) if n is not None]
    # sortuj chronologicznie po startTime (determinizm niezależny od kolejności
    # wejścia — fetch_hevy zakłada rosnący porządek dla rolling window)
    out.sort(key=lambda w: w.get("startTime") or "")
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
