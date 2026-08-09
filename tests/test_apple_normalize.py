"""Testy apple_normalize.py — konwersja surowych workoutów Apple -> cardio dla
analytics, ze szczególnym uwzględnieniem fixa dedupe (audyt Rafała 2026-08-09).

AUDYT: dedupe workoutów po `id` opierał się na globalnym _SEEN_IDS set na
poziomie modułu, czyszczonym tylko w main(). fetch_mcp.py::_run_analysis woła
normalize_workout bezpośrednio (z pominięciem main()), więc globalny set
przeżywał między uruchomieniami długożyjącego procesu i drugi trening z tym
samym id, ale INNĄ DATĄ, był błędnie odrzucany jako duplikat.

Fix: seen_ids to jawny parametr funkcji (domyślnie świeży set per wywołanie).
Testy poniżej potwierdzają: brak współdzielenia -> te same id w różnych
wywołaniach przechodzą; współdzielony set -> prawdziwe duplikaty w obrębie
jednego przebiegu są odrzucane.
"""
from __future__ import annotations

from mcp_fetchers.apple_normalize import normalize_workout


def _cardio(id_, start="2026-08-01T10:00:00", dur=60, hr=140):
    return {
        "id": id_, "name": "Outdoor Cycling", "start": start,
        "duration_min": dur, "avg_heart_rate_bpm": hr,
    }


class TestNormalizeWorkoutDeterminism:
    def test_same_id_different_calls_not_deduplicated(self):
        """AUDYT fix: bez współdzielonego setu, dwa wywołania z tym samym id
        (ale różnymi datami) są NIEZALEŻNE — drugie NIE jest błędnie odrzucone
        jako duplikat."""
        w1 = _cardio("X1", start="2026-08-01T10:00:00")
        w2 = _cardio("X1", start="2026-08-05T10:00:00")
        assert normalize_workout(w1) is not None
        assert normalize_workout(w2) is not None  # inna data -> OK

    def test_shared_seen_set_deduplicates_within_run(self):
        """Współdzielony set w obrębie jednego przebiegu: prawdziwy duplikat
        (ta sama sesja, deduped_copies>1) jest odrzucany po pierwszej kopii."""
        seen: set[str] = set()
        w1 = _cardio("D1")
        w2 = _cardio("D1")
        assert normalize_workout(w1, seen) is not None
        assert normalize_workout(w2, seen) is None  # duplikat w tej samej partii

    def test_duplicate_across_shared_set_excluded_but_other_id_ok(self):
        """W obrębie jednego przebiegu duplikaty danego id są odrzucane,
        ale inne id przechodzą."""
        seen: set[str] = set()
        assert normalize_workout(_cardio("A"), seen) is not None
        assert normalize_workout(_cardio("A"), seen) is None
        assert normalize_workout(_cardio("B"), seen) is not None

    def test_strength_workout_rejected(self):
        w = {"id": "S1", "name": "Traditional Strength Training",
             "start": "2026-08-01T10:00:00", "duration_min": 60, "avg_heart_rate_bpm": 140}
        assert normalize_workout(w) is None

    def test_missing_hr_rejected(self):
        w = {"id": "H1", "name": "Outdoor Cycling", "start": "2026-08-01T10:00:00",
             "duration_min": 60, "avg_heart_rate_bpm": None}
        assert normalize_workout(w) is None

    def test_duration_from_seconds(self):
        w = {"id": "T1", "name": "Outdoor Cycling", "start": "2026-08-01T10:00:00",
             "duration_s": 3600, "avg_heart_rate_bpm": 140}
        out = normalize_workout(w)
        assert out is not None
        assert out["duration_min"] == 60.0
