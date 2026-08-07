#!/usr/bin/env python3
"""
fetch_mfp.py — warstwa pobierania wagi z MFP (przez mcp_tool.py).

MFP nie przechowuje obecnie wagi u Rafała (mcp measurements zwraca pustą
listę), więc ta warstwa jest OPCJONALNA — gdy brak danych, moduł TDEE
returns "niska" confidence bez korekty. Nie blokuje reszty analizy.

Nie woła MCP samodzielnie — przyjmuje listę punktów wagowych i konwertuje
na WeightPoint dla nutrition_adaptive.compute_weight_trend.
"""
from __future__ import annotations
from datetime import date
from typing import Any

from .nutrition_adaptive import WeightPoint


def to_weight_series(mfp_weight_rows: list[dict]) -> list[WeightPoint]:
    """
    Wiersze pomiarów MFP (każdy {date: ..., value: ...}) -> lista WeightPoint.
    Pomija wpisy bez sensownej wartości / z 'null'.
    """
    out = []
    for r in mfp_weight_rows:
        v = r.get("value")
        d = r.get("date")
        if v is None or d is None:
            continue
        try:
            wt = float(v)
        except (TypeError, ValueError):
            continue
        out.append(WeightPoint(day=date.fromisoformat(str(d)[:10]), weight_kg=wt))
    out.sort(key=lambda p: p.day)
    return out


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) < 2:
        print("usage: python3 -m analytics.fetch_mfp '<weight_rows_json>'")
        sys.exit(0)
    rows = json.loads(sys.argv[1])
    print(json.dumps(
        [{"day": str(p.day), "weight_kg": p.weight_kg} for p in to_weight_series(rows)],
        ensure_ascii=False, indent=2))
