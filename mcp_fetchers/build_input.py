#!/usr/bin/env python3
"""
build_input.py — składa pełny payload JSON dla `analytics.run_analysis`
ze znormalizowanych plików (tmp/hevy_workouts.json, tmp/apple_input.json)
i uruchamia analizę.

PIPELINE ODCZYTU (pełna ścieżka, patrz README -> Filozofia):
  agent (MCP) --surowy JSON--> mcp_fetchers/*_normalize.py --> tmp/*.json
                                              |
                                              v
                                build_input.py --payload--> analytics.run_analysis -> wynik JSON

Ten skrypt jest OSTATNIĄ milą: bierze gotowe, znormalizowane pliki i odpala
deterministyczny rdzeń. Nie pobiera danych (to robota agenta przez MCP) ani
nie normalizuje (to robota *_normalize). Zero podwójnej odpowiedzialności.

PARAMETRY (opcjonalne przez zmienne środowiskowe / CLI):
  --target 2026-08-09   data docelowa (domyślnie dziś)
  --phase utrzymanie    cel (utrzymanie|redukcja|masa); domyślnie utrzymanie
  --weight 71.0         bodyweight kg (fallback; zwykle brakowane z apple_daily)
  --out /path.json      zapis wyniku do pliku (opcjonalnie)

WYJŚCIE: JSON (strefa gotowości, ACWR, TDEE, trendy) na stdout.

Użycie:
    python3 -m mcp_fetchers.build_input --target 2026-08-09 --out /tmp/result.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # katalog analytics/
sys.path.insert(0, BASE)


def load_normalized() -> tuple[list, list, list, list, list]:
    """Wczytuje znormalizowane pliki z tmp/. Brak pliku -> pusta lista + warning."""
    tmp = os.path.join(BASE, "tmp")
    hevy_file = os.path.join(tmp, "hevy_workouts.json")
    apple_file = os.path.join(tmp, "apple_input.json")
    mfp_file = os.path.join(tmp, "mfp_kcal.json")

    hevy: list = []
    apple_daily: list = []
    apple_temp: list = []
    apple_workouts: list = []
    mfp_kcal: list = []

    if os.path.exists(hevy_file):
        with open(hevy_file) as f:
            hevy = json.load(f)
    else:
        print(f"[warn] brak {hevy_file} — ACWR siłowy pominięty", file=sys.stderr)

    if os.path.exists(apple_file):
        with open(apple_file) as f:
            a = json.load(f)
        apple_daily = a.get("apple_daily", [])
        apple_temp = a.get("apple_temp", [])
        apple_workouts = a.get("apple_workouts", [])
    else:
        print(f"[warn] brak {apple_file} — warstwa fizjologiczna pominięta", file=sys.stderr)

    if os.path.exists(mfp_file):
        with open(mfp_file) as f:
            mfp_kcal = json.load(f)
    else:
        print(f"[warn] brak {mfp_file} — bilans energetyczny pominięty (brak zjedzonych kcal)",
              file=sys.stderr)

    return hevy, apple_daily, apple_temp, apple_workouts, mfp_kcal


def build_payload(target: str, phase: str, weight: float | None,
                  hevy, apple_daily, apple_temp, apple_workouts,
                  mfp_daily_kcal: list | None = None) -> dict:
    return {
        "source": "apple+hevy+mfp",
        "target_date": target,
        "apple_daily": apple_daily,
        "apple_temp": apple_temp,
        "apple_workouts": apple_workouts,
        "hevy_workouts": hevy,
        "cardio_sessions": [],
        "mfp_weight": None,
        "mfp_daily_kcal": mfp_daily_kcal or [],  # zjedzone kcal/dzień z MFP diary
        "params": {
            "phase": phase,
            "bodyweight_kg": weight,
        },
    }


def aggregate_volume(hevy: list) -> dict:
    """Sumuje objętość mechaniczną (tonnage) ze wszytyckich workoutów.
    `_volume` jest opcjonalne (obecne tylko gdy normalizacja przez
    hevy_normalize). Zwraca pusty dict gdy brak danych. Metryka obciążenia
    tkanek — NIE miesza się w load ACWR (patrz README §3)."""
    total = 0.0
    working = 0.0
    n = 0
    for w in hevy:
        v = w.get("_volume")
        if not v:
            continue
        total += v.get("tonnage_total", 0.0)
        working += v.get("tonnage_working", 0.0)
        n += 1
    if n == 0:
        return {}
    return {
        "n_workouts": n,
        "tonnage_total": round(total, 1),    # z warmupami
        "tonnage_working": round(working, 1),  # bez warmupów
        "warmup_share_pct": round((total - working) / total * 100, 1) if total else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Składanie payloadu i uruchomienie analytics")
    ap.add_argument("--target", default=str(date.today()))
    ap.add_argument("--phase", default="utrzymanie",
                    choices=["utrzymanie", "redukcja", "masa"])
    ap.add_argument("--weight", type=float, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    hevy, apple_daily, apple_temp, apple_workouts, mfp_kcal = load_normalized()
    payload = build_payload(args.target, args.phase, args.weight,
                            hevy, apple_daily, apple_temp, apple_workouts,
                            mfp_daily_kcal=mfp_kcal)

    from analytics.run_analysis import run
    result = run(payload)
    # dołącz objętość mechaniczna (z warmupami) do wyniku jako sekcję dodatkową
    vol = aggregate_volume(hevy)
    if vol:
        result["hevy_volume"] = vol

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
