"""
apple_cardio.py — sesje wydolnościowe z Apple Watch jako obciążenie ACWR.

Schemat (decyzja z rozmowy 2026-08-07):
- Siłowe / kalisteniczne  -> WYŁĄCZNIE z Hevy (ignorowane z Apple).
- Cardio / wydolnościowe  -> z Apple Watch (Outdoor Cycling, Rowing, Walking,
  Running, Swimming, inne cardio).

Dubl nie powstaje, bo źródła obsługują rozłączne kategorie: Apple Watch
dostarcza tu TYLKO aktywności cardio, a siłowe z Apple są odrzucane na
starcie (biała lista typów cardio).

Obciążenie liczony przez TRIMP (Banister/Edwards) — oparty na tętnie,
więc w pełni automatyczny: nie trzeba ręcznie podawać RPE dla każdej jazdy.
Wartość to TRIMP (minut * współczynnik intensywności ze strefy HR), liczony
w OSOBNYM ACWR niż tonaż z Hevy (różne jednostki — patrz build_cardio_acwr).

Zależności: numpy (przez .acwr)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .acwr import SessionLoad
from .config import settings
from .logging import get_logger

logger = get_logger(__name__)


@dataclass
class CardioSession:
    """Jedna sesja cardio z Apple Watch, sprowadzona do pola load (TRIMP)."""

    day: date
    load: float        # TRIMP
    workout_type: str  # np. "Outdoor Cycling" — kontekst/diagnostyka


# Biała lista typów aktywności uznawanych za CARDIO (z Apple Watch).
# Wszystko spoza tej listy (siłowe, kalisteniczne, functional strength,
# cross training itd.) jest odrzucane — siła pochodzi wyłącznie z Hevy.
APPLE_CARDIO_TYPES: frozenset[str] = frozenset({
    "cycling", "outdoor cycling", "indoor cycling",
    "rowing", "indoor rowing",
    "walking", "outdoor walking", "indoor walking", "walking speed",
    "outdoor walk", "indoor walk",
    "running", "outdoor running", "indoor running",
    "swimming", "open water swimming", "outdoor swimming",
    "hiking",
    "elliptical", "stair stepper", "stair climbing",
    "dance", "mixed cardio", "other",
    # bieżnia i ergometr wiosłowy zgłaszane pod różnymi nazwami
    "treadmill", "functional training",
})


def _parse_date(iso: Any) -> date:
    """ISO 8601 (z 'Z' lub offset) -> date."""
    if not iso:
        return date.today()
    s = str(iso)[:10]
    return datetime.strptime(s, "%Y-%m-%d").date()


def _normalize_type(t: Any) -> str:
    """Normalizuje nazwę workoutu do lowercase (bez spacji z przodu/tyłu)."""
    return str(t or "").strip().lower()


def is_cardio_workout(workout: dict) -> bool:
    """
    Czy workout z Apple Watch to aktywność wydolnościowa (cardio)?
    Siłowe/kalisteniczne ze źródeł Apple są odrzucane na starcie — to
    wyklucza dubl z Hevy (siła wyłącznie z Hevy).
    """
    name = _normalize_type(workout.get("name") or workout.get("workout_type") or workout.get("type"))

    # 1) CZARNA lista siłowych/kalistenicznych słów kluczowych — MA PIERWSZEŃSTWO.
    # Chroni przed false-positive cardio, gdy custom nazwa zawiera cardio-słowo
    # (np. "Walking Lunges" ma "walk", ale to ćwiczenie siłowe). Nawet gdyby pole
    # name kiedykolwiek zawierało custom tekst, te frazy wykluczają cardio.
    _STRENGTH_KEYWORDS = (
        "strength", "lunge", "lunges", "press", "curl", "curls", "deadlift",
        "squat", "bench", "pull-up", "pull up", "push-up", "push up", "dip",
        "dips", "fly", "flies", "extension", "raises", "raise",
        "crunch", "plank", "weight", "barbell", "dumbbell", "kettlebell",
        "functional strength", "cross training", "bodyweight workout",
    )
    if any(kw in name for kw in _STRENGTH_KEYWORDS):
        return False

    # 2) dopasowanie po pełnej nazwie (zamknięty enum kategorii Apple)
    if name in APPLE_CARDIO_TYPES:
        return True
    # 3) dopasowanie po słowie kluczowym (np. "Outdoor Cycling" -> "cycling")
    for kw in ("cycling", "rowing", "running", "walk", "walking", "swimming", "hiking",
               "elliptical", "stepper", "stair", "treadmill", "cardio", "ergometer"):
        if kw in name:
            return True
    return False


def compute_trimp_session_load(
    avg_hr: float,
    duration_minutes: float,
    hr_rest: float | None = None,
    session_peak_hr: float | None = None,
    hr_reference_floor: float | None = None,
) -> float:
    """
    TRIMP (training impulse) — obciążenie sesji cardio z tętna (Banister).

    TRIMP = czas(min) * wspólczynnik intensywności wyznaczony z rezerwy tętna.
    Stosowany standardowy model Banistera:
      ratio = (HRavg - HRrest) / (reference_hr - HRrest)
      TRIMP = czas * ratio * 0.64 * e^(1.92 * ratio)
    Rekomendowany w literaturze do treningu wydolnościowego; w pełni
    automatyczny (bez ręcznego RPE).

    SEMANTYKA punktu odniesienia (session-relative):
    TRIMP wykorzystuje maksymalne tętno zaobserwowane podczas SESJI jako
    punkt odniesienia intensywności. Nie jest ono traktowane jako fizjologiczne
    HRmax użytkownika. Opcjonalny dolny limit punktu odniesienia
    (hr_reference_floor) zabezpiecza przed zawyżeniem względnej intensywności
    podczas lekkich treningów.

      effective_reference_hr = max(session_peak_hr, hr_reference_floor)

    avg_hr: średnie tętno sesji (bpm)
    duration_minutes: czas trwania (min)
    hr_rest: tętno spoczynkowe (bpm) — default z configu (ACWR.hr_rest_default)
    session_peak_hr: maksymalne HR zaobserwowane podczas sesji (bpm) — default
        z configu (ACWR.hr_peak_default)
    hr_reference_floor: minimalny punkt odniesienia (bpm) do normalizacji;
        None = brak dolnego limitu (domyślna metodologia: reference = peak HR
        sesji). Wartość konfiguracyjną przekazuje wywołujący (patrz
        apple_workout_daily_load / ACWR.hr_reference_floor_bpm).

    Zwraca TRIMP (min·wsp). Rząd wielkości: ~88-min rower @ 143bpm, rest 55,
    max 190 -> ok. 200-250. Osobna skala od tonażu (setki vs tysiące).
    """
    if duration_minutes <= 0:
        raise ValueError("duration_minutes musi być > 0")
    rest = hr_rest if hr_rest is not None else settings.ACWR.hr_rest_default
    peak = session_peak_hr if session_peak_hr is not None else settings.ACWR.hr_peak_default
    if session_peak_hr is not None and session_peak_hr < avg_hr:
        # peak HR < avg HR to niespójne dane wejściowe — kontrolowany błąd.
        raise ValueError("session_peak_hr musi być >= avg_hr")
    if peak <= rest:
        raise ValueError("session_peak_hr musi być > hr_rest")

    # dolny limit punktu odniesienia — None oznacza brak limitu (pure function),
    # wartość konfiguracyjną dostarcza wywołujący.
    if hr_reference_floor is not None:
        peak = max(peak, hr_reference_floor)

    # % rezerwy tętna (delta HR ratio, 0..1) — Banister TRIMP
    ratio = 0.0 if avg_hr <= rest else (avg_hr - rest) / (peak - rest)
    ratio = max(0.0, min(ratio, 1.0))

    # standardowy TRIMP Banistera (nieliniowy, uwzględnia rosnący koszt
    # wysiłku powyżej progu): TRIMP = min * ratio * 0.64 * e^(1.92 * ratio)
    import math
    intensity = ratio * 0.64 * math.exp(1.92 * ratio)
    return round(duration_minutes * intensity, 1)


def apple_workout_daily_load(workout: dict) -> tuple[date, float] | None:
    """
    Obciążenie (TRIMP) jednej sesji cardio z Apple Watch, przypięte do dnia.
    Odrzuca workouty siłowe (patrz is_cardio_workout) oraz brakujące dane.
    Zwraca (day, trimp) lub None gdy nie cardio / brak avg_hr / czasu.

    peak HR (mianownik Banistera) bierzemy z danej sesji treningowej
    (max_heart_rate_bpm), jeśli jest dostępny — pełniejszy, realny obraz
    pułapu osiągniętego w tej aktywności. Gdy brak, pada na config
    (ACWRSettings.hr_peak_default). Punkt odniesienia to peak HR z SESJI,
    NIE fizjologiczne HRmax — patrz compute_trimp_session_load. Opcjonalny
    dolny limit (hr_reference_floor z configu) zabezpiecza lekkie treningi
    przed zawyżeniem względnej intensywności. hr_rest zawsze z configu
    (poranne spoczynkowe — niezmienne per sesja).
    """
    if not is_cardio_workout(workout):
        logger.debug("apple workout nie-cardio (pominięty): %s", workout.get("name"))
        return None

    avg_hr = workout.get("avg_heart_rate_bpm")
    max_hr = workout.get("max_heart_rate_bpm")  # per-sesja, opcjonalnie
    duration = workout.get("duration_min") or workout.get("duration_s")
    if avg_hr is None or duration is None:
        return None
    try:
        avg_hr_f = float(avg_hr)
        max_hr_f = float(max_hr) if max_hr is not None else None
        duration_f = float(duration)
        if workout.get("duration_s") is not None and workout.get("duration_min") is None:
            duration_f = duration_f / 60.0  # sekundy -> minuty
    except (TypeError, ValueError):
        return None
    if avg_hr_f <= 0 or duration_f <= 0:
        return None
    # patologiczne max_hr (<= spoczynkowe) — odrzuć sesję, nie wywalaj pipeline
    if max_hr_f is not None and max_hr_f <= 0:
        return None

    day = _parse_date(workout.get("start") or workout.get("startTime"))
    try:
        trimp = compute_trimp_session_load(
            avg_hr_f, duration_f, session_peak_hr=max_hr_f,
            hr_reference_floor=settings.ACWR.hr_reference_floor_bpm,
        )
    except ValueError:
        logger.debug("apple cardio: odrzucono sesję z niepoprawnym tętnem (peak=%.1f)", max_hr_f)
        return None
    return day, round(trimp, 1)


def build_apple_cardio_series(
    workouts: list[dict],
    start: date,
    end: date,
) -> list[SessionLoad]:
    """
    Pełny szereg dzienny (start..end włącznie, dni bez cardio = 0.0) z listy
    workoutów wydolnościowych Apple Watch (każdy dict jak z apple__list_recent_workouts).

    Odrzuca: workouty siłowe/kalisteniczne (zostają w Hevy), workouty poza
    oknem [start, end], brak avg_hr/czasu. Serie cardio są ZAWSZE w TRIMP —
    w osobnym ACWR niż tonaż (patrz acwr_mod.build_cardio_acwr).
    """
    from .acwr import aggregate_daily_loads, fill_missing_days

    pairs: list[tuple[date, float]] = []
    for w in workouts:
        r = apple_workout_daily_load(w)
        if r is not None and start <= r[0] <= end:
            pairs.append(r)
    daily = aggregate_daily_loads(pairs)
    return fill_missing_days(daily, start, end)
