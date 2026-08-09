#!/usr/bin/env python3
"""
apple_normalize.py — konwersja surowych danych Apple MCP do formatu akceptowanego
przez moduł analytics (fetch_apple.build_apple_models / pipeline).

ROZBIEŻNOŚĆ FORMATÓW (patrz README -> Problemy):
  - `apple__get_daily_activity_range` zwraca WSZYSTKIE pola dzienne (kroki, dystans,
    energy, HRV, RHR, sen, waga, effort...). Analytics potrzebuje tylko podzbioru
    (date, resting_heart_rate, heart_rate_variability, sleep.total_hours,
    active_energy/basal_energy_burned [kJ], weight_body_mass, body_fat_percentage).
  - Temperatura nadgarstka NIE pochodzi z daily — trzeba ją pobrać OSOBNO przez
    `apple__get_data(name='apple_sleeping_wrist_temperature')` z polem `value`,
    a analytics oczekuje `apple_temp` = [{"date","value"}].
  - Workouty cardio i siłowe siedzą w jednym źródle; analytics chce WYŁĄCZNIE
    cardio (`apple_workouts`), bo siła pochodzi z Hevy. Ten skrypt filtruje.

FILOZOFIA: tak samo jak hevy_normalize — czysta deterministyczna konwersja,
bez wołania MCP. Agent pobiera surowe dane przez MCP, wstrzykuje tu przez stdin.

WEJŚCIE (stdin) — obiekt:
{
  "daily":   [ z apple__get_daily_activity_range ],
  "temp":    [ z apple__get_data(name=...wrist_temperature) ] | null,
  "workouts":[ z apple__list_recent_workouts ] | null
}

WYJŚCIE (stdout): obiekt { "apple_daily": [...], "apple_temp": [...], "apple_workouts": [...] }

Użycie:
    cat raw_apple.json | python3 -m mcp_fetchers.apple_normalize > tmp/apple_input.json
"""
from __future__ import annotations

import json
import sys

# Nazwy aktywności z Apple Watch, które NIE są cardio (siłowe/kalisteniczne).
# Apple Watch zgłasza "Traditional Strength Training" — musimy je odrzucić,
# bo siła w naszym modelu pochodzi wyłącznie z Hevy (zero dubli).
STRENGTH_KEYWORDS = (
    "strength", "lunge", "press", "curl", "deadlift", "squat", "bench",
    "pull-up", "pull up", "push-up", "push up", "dip", "fly", "extension",
    "raise", "weight", "barbell", "dumbbell", "kettlebell", "functional",
    "cross training", "rowing machine", "core",
)
# Jawna biała lista cardio (nazwy z API Apple Watch — case-insensitive)
CARDIO_NAMES = (
    "cycling", "rowing", "walk", "walking", "running", "swimming", "hiking",
    "elliptical", "stepper", "stair", "treadmill", "cardio", "ergometer",
)

# Set id workoutów już przepuszczonych — do dedupe zdublowanych kopii tej samej
# sesji (Apple zgłasza `deduped_copies` > 1). Resetowany w main(), żeby każde
# uruchomienie było czyste (determinizm/idempotencja).
_SEEN_IDS: set[str] = set()


def _keep_energy(v) -> float | None:
    """active_energy / basal_energy_burned mogą być null lub float. -> float|None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize_daily_point(d: dict) -> dict:
    """Jeden punkt dzienny z get_daily_activity_range -> podzbiór dla analytics."""
    sleep = d.get("sleep") or {}
    return {
        "date": (d.get("date") or "")[:10],
        "resting_heart_rate": d.get("resting_heart_rate"),
        "heart_rate_variability": d.get("heart_rate_variability"),
        "sleep": {"total_hours": sleep.get("total_hours")},
        "active_energy": _keep_energy(d.get("active_energy")),
        "basal_energy_burned": _keep_energy(d.get("basal_energy_burned")),
        "weight_body_mass": d.get("weight_body_mass"),
        "body_fat_percentage": d.get("body_fat_percentage"),
    }


def normalize_temp_point(p: dict) -> dict | None:
    """Punkt temperatury: {"date","value"} — przepuszcza jak jest, odrzuca null value."""
    if p.get("value") is None or p.get("date") is None:
        return None
    return {"date": (p["date"])[:10], "value": float(p["value"])}


def is_cardio(workout: dict) -> bool:
    """Czy workout z Apple to aktywność wydolnościowa (a nie siłowa)?
    Czarna lista siłowa ma pierwszeństwo — chroni przed false-positive cardio
    przy custom nazwach (np. "Walking Lunges" zawiera 'walk', ale to siła)."""
    name = (workout.get("name") or workout.get("workout_type") or "").strip().lower()
    if any(kw in name for kw in STRENGTH_KEYWORDS):
        return False
    return any(kw in name for kw in CARDIO_NAMES)


def normalize_workout(w: dict) -> dict | None:
    """Workout z apple__list_recent_workouts -> format analytics (tylko cardio).

    Dedupe po `id`: surowe dane Apple mogą zawierać zdublowane kopie tej samej
    sesji (pole `deduped_copies` > 1). Skrypt przepuszcza tylko pierwszą kopię
    per id — reszta to artefakt, nie osobne treningi.
    """
    if not is_cardio(w):
        return None
    w_id = w.get("id")
    if w_id is not None:
        if w_id in _SEEN_IDS:
            return None
        _SEEN_IDS.add(w_id)
    avg = w.get("avg_heart_rate_bpm")
    if avg is None:
        return None
    dur = w.get("duration_min")
    if dur is None and w.get("duration_s") is not None:
        dur = float(w["duration_s"]) / 60.0
    if dur is None:
        return None
    out = {
        "name": w.get("name"),
        "start": (w.get("start") or "")[:19],
        "duration_min": float(dur),
        "avg_heart_rate_bpm": float(avg),
    }
    if w.get("max_heart_rate_bpm") is not None:
        out["max_heart_rate_bpm"] = float(w["max_heart_rate_bpm"])
    return out


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

    apple_daily = [normalize_daily_point(d) for d in (data.get("daily") or [])]
    _SEEN_IDS.clear()  # czysty dedupe per uruchomienie
    apple_temp = [x for x in (normalize_temp_point(p) for p in (data.get("temp") or [])) if x]
    apple_workouts = [x for x in (normalize_workout(w) for w in (data.get("workouts") or [])) if x]
    # sortuj cardio chronologicznie (list_recent_workouts zwraca najnowsze pierwszy)
    apple_workouts.sort(key=lambda w: (w.get("start") or ""))

    print(json.dumps({
        "apple_daily": apple_daily,
        "apple_temp": apple_temp,
        "apple_workouts": apple_workouts,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
