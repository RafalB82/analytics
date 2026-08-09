#!/usr/bin/env python3
"""
mfp_normalize.py — konwersja surowego dziennika MFP do formatu akceptowanego
przez moduł bilansu energetycznego (`analytics.energy_balance`).

DLACZEGO TEGO POTRZEBUJEMY:
  - `energy_balance` ocenia, czy wydatek energetyczny (TDEE z Apple) jest
    pokryty ZJEDZONYMI kcal. Te kcal pochodzą z MFP (MyFitnessPal diary).
  - Rdzeń `analytics` jest celowo OFFLINE i nie woła MCP (patrz README §1).
    Dlatego pobieranie dziennika (agent woła `mfp__mfp_get_diary`) jest
    rozdzielone od konwersji (ten skrypt) — dokładnie jak apple_normalize.py
    / hevy_normalize.py.

FILOZOFIA: identyczna jak pozostałe *_normalize.py — czysta, deterministyczna,
offline konwersja stdin->stdout. Agent pobiera surowy diary (JSON) przez MCP
i wstrzykuje tu przez stdin.

WEJŚCIE (stdin) — JEDEN dziennik z `mfp__mfp_get_diary(data)`:
{
  "date": "2026-08-05",
  "meals": { "breakfast": {"totals": {"calories": 568.0}, ...}, ... },
  "daily_totals": { "calories": 2430.0, ... }
}
LUB lista dzienników (jeden na dzień) — np. dla całego okna 7d.

WYJŚCIE (stdout): lista zjedzonych kcal per dzień:
[
  { "day": "2026-08-05", "kcal": 2430.0 },
  ...
]

Użycie:
    cat mfp_diary_2026-08-05.json | python3 -m mcp_fetchers.mfp_normalize > tmp/mfp_kcal.json
"""
from __future__ import annotations

import json
import sys


def extract_day_kcal(diary: dict) -> dict | None:
    """Pojedynczy dziennik MFP -> {day, kcal}. Bierze daily_totals.calories
    (suma dzienna, zaufane pole agregatu) — nie sumuje posiłków ręcznie,
    żeby nie dublować logiki i nie pomylić się na licznych posiłkach."""
    day = (diary.get("date") or "")[:10]
    totals = diary.get("daily_totals") or {}
    kcal = totals.get("calories")
    if not day or kcal is None:
        return None
    try:
        kcal_f = float(kcal)
    except (TypeError, ValueError):
        return None
    if kcal_f < 0:
        return None
    return {"day": day, "kcal": round(kcal_f, 1)}


def normalize_diaries(raw: dict | list) -> list[dict]:
    """Akceptuje pojedynczy dziennik lub listę dzienników; zwraca {day, kcal}."""
    if isinstance(raw, dict):
        # pojedynczy dziennik MFP: {"date", "daily_totals", ...}
        if "daily_totals" in raw or "date" in raw:
            out = extract_day_kcal(raw)
            return [out] if out else []
        # może być obiekt {date: {daily_totals...}}? — nie, trzymamy prosty schemat
        return []
    if isinstance(raw, list):
        out = []
        for d in raw:
            r = extract_day_kcal(d)
            if r:
                out.append(r)
        # sortuj chronologicznie (determinizm)
        out.sort(key=lambda x: x["day"])
        return out
    return []


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

    out = normalize_diaries(data)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
