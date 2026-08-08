#!/usr/bin/env python3
"""
fetch_mfp.py — warstwa odczytu danych MFP (MyFitnessPal).

ROLA (po redesignie): MFP dostarcza WYŁĄCZNIE zjedzone kalorie/jedzenie.
Nie jest już źródłem wagi (waga pochodzi z Apple Health) ani źródłem
celu kalorycznego (cel liczony z aktywności Apple — patrz nutrition_adaptive).

`to_weight_series` pozostaje jako REZERWA/backward-compat na wypadek, gdyby
kiedyś chciano trendować wagę z MFP (format: [{date, value}] -> obiekty
z atrybutami .day/.weight_kg, kompatybilne z compute_weight_trend).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .validators import weight as _val_weight


@dataclass
class WeightSample:
    """Punkt wagowy (kompatybilny z rezerwowym compute_weight_trend)."""

    day: date
    weight_kg: float


def to_weight_series(mfp_weight_rows: list[dict]) -> list[WeightSample]:
    """
    Wiersze pomiarów MFP (każdy {date: ..., value: ...}) -> lista WeightSample.
    Pomija wpisy bez sensownej wartości / z 'null'.
    """
    out = []
    for r in mfp_weight_rows:
        v = r.get("value")
        d = r.get("date")
        if v is None or d is None:
            continue
        wt = _val_weight(v)
        if wt is None:
            continue
        out.append(WeightSample(day=date.fromisoformat(str(d)[:10]), weight_kg=wt))
    out.sort(key=lambda p: p.day)
    return out


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: python3 -m analytics.fetch_mfp '<weight_rows_json>'")
        sys.exit(0)
    rows = json.loads(sys.argv[1])
    print(json.dumps(
        [{"day": str(p.day), "weight_kg": p.weight_kg} for p in to_weight_series(rows)],
        ensure_ascii=False, indent=2))
