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

WYJŚCIE (stdout): lista per dzień z zjedzonymi kcal i licznikiem kaw:
[
  { "day": "2026-08-05", "kcal": 2430.0, "coffee_count": 3 },
  ...
]

KAWA (kofeina — estymacja): MFP nie raportuje wartości kofeiny, więc
`coffee_count` liczy WPISY kawy (po nazwie) w posiłkach danego dnia.

ZAŁOŻENIE: 1 wpis kawy = 1 faktycznie wypita kawa. U Rafała: wpis "caffè"
na każdą kawę z ekspresu (Saeco Moltio, bez rozgraniczania 1/2 ziaren),
1 wpis = 1 kawa. Sam przelicznik mg na wpis żyje w `analytics.caffeine`
(MG_PER_COFFEE = 70.0), nie tutaj — ten skrypt tylko zlicza wystąpienia.

Użycie:
    cat mfp_diary_2026-08-05.json | python3 -m mcp_fetchers.mfp_normalize > tmp/mfp_kcal.json
"""
from __future__ import annotations

import json
import re
import sys

# Słowa kluczowe wykrywające wpis kawy (case-insensitive, po nazwie pozycji).
# Pokrywa polskie (kawa, espresso), angielskie (coffee, latte, americano,
# cappuccino) oraz formę obecną u Rafała ("caffè").
COFFEE_KEYWORDS = ("caffè", "cafe", "kawa", "coffee", "espresso", "latte",
                   "americano", "cappuccino")

# Prekompilowany wzorzec: szukaj słowa kluczowego jako osobnego słowa
# (granice słowa), żeby np. "kałam" nie zaliczyło "kawa", ale "caffe latte" już tak.
_COFFEE_RE = re.compile(
    r"(?:^|[^a-ząćęłńóśźż])(" + "|".join(map(re.escape, COFFEE_KEYWORDS))
    + r")(?:$|[^a-ząćęłńóśźż])",
    re.IGNORECASE,
)


def count_coffee_entries(diary: dict) -> int:
    """Liczba wpisów kawy w dzienniku MFP (po nazwie pozycji w posiłkach).

    Przechodzi przez meals[*].entries[] i zlicza pozycje, których `name`
    zawiera słowo kluczowe kawy (np. "caffè"). Bonus: dopuszczamy też sparowane
    `short_name` — MFP czasem ma obciętą nazwę w `name` a pełną w `short_name`.
    """
    meals = diary.get("meals") or {}
    count = 0
    for meal in meals.values():
        if not isinstance(meal, dict):
            continue
        for entry in meal.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "")
            short = (entry.get("short_name") or "")
            if _COFFEE_RE.search(name) or _COFFEE_RE.search(short):
                count += 1
    return count


def extract_day_kcal(diary: dict) -> dict | None:
    """Pojedynczy dziennik MFP -> {day, kcal, coffee_count}. Bierze
    daily_totals.calories (suma dzienna, zaufane pole agregatu) — nie sumuje
    posiłków ręcznie, żeby nie dublować logiki i nie pomylić się na licznych
    posiłkach. coffee_count = liczba wpisów kawy (1 wpis = 1 kawa)."""
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
    return {"day": day, "kcal": round(kcal_f, 1),
            "coffee_count": count_coffee_entries(diary)}


def normalize_diaries(raw: dict | list) -> list[dict]:
    """Akceptuje pojedynczy dziennik lub listę dzienników; zwraca
    {day, kcal, coffee_count} (coffee_count=0 gdy brak kawy)."""
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
