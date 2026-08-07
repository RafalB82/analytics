#!/usr/bin/env python3
"""
demo_full.py — powtarzalny test pakietu analytics na SYNTETYCZNYCH danych
zbliżonych do realnych (Apple HRV/RHR/sen + Hevy tonaż/RPE + brak wagi MFP).

Cel: pokazać kompletny output run_analysis bez konieczności uruchamiania MCP.
Po wdrożeniu, do "prawdziwego" użycia, agent wstrzykuje realne dane zebrane
z apple__get_daily_activity_range + hevy__get-workouts.

Uruchomienie:
    python3 -m analytics.demo_full
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from .run_analysis import run


def _mk_hevy(days: list[date], rpe_mode: bool) -> list[dict]:
    """Generuje treningi w dni `days`. rpe_mode=True -> loguje RPE (nowsze)."""
    out = []
    for d in days:
        # Template treningów: kilka ćwiczeń z seriami
        # CIĘŻARY w kg (sztanga/maszyny)
        ex_sets = []
        if rpe_mode:
            # nowsze treningi: RPE logowane
            ex_sets = [
                [{"type": "warmup", "weight": 20, "reps": 10, "rpe": None},
                 {"type": "normal", "weight": 80, "reps": 5, "rpe": 8},
                 {"type": "normal", "weight": 75, "reps": 7, "rpe": 8.5}],
                [{"type": "normal", "weight": 20, "reps": 6, "rpe": 9},
                 {"type": "normal", "weight": 20, "reps": 6, "rpe": 9.5}],
                [{"type": "normal", "weight": 17.5, "reps": 10, "rpe": 8},
                 {"type": "normal", "weight": 17.5, "reps": 10, "rpe": 9}],
            ]
        else:
            # starsze treningi: brak RPE (sam tonaż)
            ex_sets = [
                [{"type": "warmup", "weight": 20, "reps": 10, "rpe": None},
                 {"type": "normal", "weight": 75, "reps": 6, "rpe": None},
                 {"type": "normal", "weight": 75, "reps": 6, "rpe": None}],
                [{"type": "normal", "weight": 18, "reps": 10, "rpe": None}],
                [{"type": "normal", "weight": 15, "reps": 12, "rpe": None}],
            ]
        exercises = [{"name": f"Exercise {i+1}", "sets": s} for i, s in enumerate(ex_sets)]
        out.append({
            "title": "Upper" if rpe_mode else "Upper (old)",
            "startTime": d.strftime("%Y-%m-%d") + "T17:00:00+00:00",
            "exercises": exercises,
        })
    return out


def _mk_apple(days: list[date], hrv_base=40.0, sleep_base=7.0) -> list[dict]:
    """Syntetyczna seria Apple (HRV/RHR/sen + aktywność energetyczna + punkt wagi).

    Aktywność: basal ~14500-21700 kJ, active ~860-13500 kJ (jak realne dane
    z get_daily_activity_range). Waga: punkt kontrolny tylko na OSTATNI dzień
    (Apple zwraca wagę tylko w dni ważenia, reszta None).
    """
    out = []
    for i, d in enumerate(days):
        # lekki trend spadkowy HRV + lekkie sinusowe odchylenia
        hrv = hrv_base - i * 0.3 + (5 if i % 4 == 3 else 0)
        rhr = 51 + (2 if i % 4 == 3 else 0)
        sleep = sleep_base + (-1.2 if i % 4 == 3 else 0.2)
        # aktywność zbliżona do zdeduplikowanych realnych pomiarów Apple (basal ~7000-7400 kJ
        # = ~1700 kcal BMR; active 1500-5000 kJ zależnie od dnia)
        basal = 7000 + (i % 4) * 300
        active = 1500 + (i % 3) * 1200 + (2000 if i % 4 == 3 else 0)
        exercise = (5 + (i % 4) * 60) if i % 2 == 0 else 0
        stand = 200 + (i % 3) * 50
        day = {
            "date": d.strftime("%Y-%m-%d"),
            "resting_heart_rate": round(rhr, 1),
            "heart_rate_variability": round(hrv, 2),
            "sleep": {"total_hours": round(sleep, 2)},
            "basal_energy_burned": float(basal),
            "active_energy": float(active),
            "apple_exercise_time": float(exercise),
            "apple_stand_time": float(stand),
            "physical_effort": round(2.8 + (i % 3) * 0.2, 2),
        }
        # waga tylko na ostatni dzień (punkt kontrolny)
        if i == len(days) - 1:
            day.update({
                "weight_body_mass": 71.05,
                "body_fat_percentage": 15.1,
                "lean_body_mass": 60.3,
                "body_mass_index": 24.3,
                "height": 1.71,
            })
        out.append(day)
    return out


def main():
    today = date(2026, 8, 7)
    days14 = [today - timedelta(days=i) for i in range(13, -1, -1)]

    # Apple: 14 dni historii (min. 6 wymagane)
    apple_daily = _mk_apple(days14)

    # Temperatura nadgarstka: realne wartości z apple__get_data (bezwzględna °C)
    apple_temp = [
        {"date": "2026-07-30", "value": 36.04},
        {"date": "2026-07-31", "value": 36.09},
        {"date": "2026-08-01", "value": 36.11},
        {"date": "2026-08-02", "value": 35.86},
        {"date": "2026-08-03", "value": 36.16},
        {"date": "2026-08-04", "value": 36.01},
        {"date": "2026-08-05", "value": 35.71},
        {"date": "2026-08-06", "value": 35.98},
    ]

    # Hevy: nowsze treningi z RPE (co ~3 dni, jak 08-01/03/05), starsze bez RPE
    hevy_workouts = []
    hevy_workouts += _mk_hevy([today - timedelta(days=2), today - timedelta(days=4), today - timedelta(days=6)], rpe_mode=True)
    hevy_workouts += _mk_hevy([today - timedelta(days=9), today - timedelta(days=12)], rpe_mode=False)

    payload = {
        "source": "apple+hevy+mfp",
        "target_date": today.strftime("%Y-%m-%d"),
        "apple_daily": apple_daily,
        "apple_temp": apple_temp,
        "hevy_workouts": hevy_workouts,
        "mfp_weight": None,   # brak wagi w MFP
        "params": {
            "tdee_current": 2260,
            "phase": "utrzymanie",
            "bodyweight_kg": 69.9,
            "target_trend_kg_per_week": 0.0,
        },
    }

    result = run(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
