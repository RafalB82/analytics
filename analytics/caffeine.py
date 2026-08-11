#!/usr/bin/env python3
"""
caffeine.py — estymacja kofeiny z liczby wypitych kaw (z MFP diary).

DLACZEGO TO JEST ESTYMACJA, NIE POMIAR:
  - MyFitnessPal nie raportuje wartości kofeiny (aplikacja nie trzyma tego
    pola dla wpisów; patrz `mfp_fetchers.mfp_normalize` → struktura nutrition).
  - Pita kawa występuje w RAFALE wyłącznie w MFP (jako wpis "caffè").
  - Stąd: liczymy WPISY kawy (coffee_count z mfp_normalize) i mnożymy przez
    stałą mg/kawę. To nie jest wartością z etykiety — to świadome założenie.

USTALENIE 2026-08-11 (z Rafałem):
  - 1 wpis "caffè" w MFP = 1 faktycznie wypita kawa (bez rozgraniczania
    1/2 ziaren na ekspresie Saeco Moltio HD8768).
  - Stała MG_PER_COFFEE = 70.0 na KAŻDĄ kawę (zakres realistyczny dla
    superautomatu ~8 g na porcję: pojedyncze ~63-80 mg; Rafał pije zwykle
    2×\"2 ziarna\" + 2×\"1 ziarno\" = 3-4 kawy dziennie — średnia 70 mg zostaje
    w szumie i nie zmienia żadnej korelacji).
  - Kawa = czysty czarny napój; mleko (białko) i tak logowane osobno w MFP,
    więc kofeina dotyczy wyłącznie wpisów kawy.

MA TRZY ZADANIA:
  1. `caffeine_mg_from_coffee(count)` — liczba kaw -> mg kofeiny (surowiec).
  2. `apply_caffeine_to_daily(daily_kcal)` — dosypuje caffeine_mg do listy
     {day, kcal, coffee_count} z mfp_normalize (docelowy surowiec dla
     metryki / wtórnej analizy).
  3. Metryka `caffeine_mg` w DailyMetrics — patrz analytics.models.

DOCELOWO (wtórna analiza, na razie NIE implementowana): przy słabym śnie
sprawdź kofeinę dnia POPRZEDZAJĄCEGO noc — czysta funkcja odczytu na bazie
tej metryki. Ten moduł dostarcza tylko samą wartość, bez korelacji.
"""
from __future__ import annotations

# mg kofeiny na 1 kawę (estymacja dla Saeco Moltio, patrz docstring).
MG_PER_COFFEE = 70.0


def caffeine_mg_from_coffee(coffee_count: int | float | None) -> float:
    """Liczba kaw -> mg kofeiny (estymacja). Ujemne/brak -> 0.0."""
    if coffee_count is None:
        return 0.0
    try:
        n = float(coffee_count)
    except (TypeError, ValueError):
        return 0.0
    if n < 0:
        return 0.0
    return round(n * MG_PER_COFFEE, 1)


def apply_caffeine_to_daily(daily_kcal: list[dict]) -> list[dict]:
    """Dosypuje `caffeine_mg` do każdego wpisu {day, kcal, coffee_count}
    (wyjście mfp_normalize). Wpisy bez coffee_count traktowane jako 0 kaw.
    Zwraca nową listę (nie mutuje wejścia)."""
    out = []
    for item in daily_kcal or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["caffeine_mg"] = caffeine_mg_from_coffee(row.get("coffee_count"))
        out.append(row)
    return out
