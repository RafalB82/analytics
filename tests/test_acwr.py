"""Testy ACWR (obciążenie treningowe, strefy ryzyka, modyfikator readiness)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.acwr import (
    ACWR_LOOKBACK_DAYS,
    SessionLoad,
    acwr_ratio,
    acwr_readiness_modifier,
    aggregate_daily_loads,
    build_acwr,
    compute_acute_load,
    compute_chronic_load,
    compute_session_load,
    fill_missing_days,
)
from analytics.fetch_hevy import (
    build_daily_load_series,
    cardio_session_daily_load,
    compute_cardio_session_load,
)


def _daily(loads: list[float], start: date) -> list[SessionLoad]:
    """Buduje ciągły szereg dzienny zaczynając od `start`."""
    return [SessionLoad(day=start + timedelta(days=i), load=load) for i, load in enumerate(loads)]


class TestComputeSessionLoad:
    def test_tonnage_only_without_rpe(self):
        assert compute_session_load(sets=3, reps=5, weight_kg=100.0) == 1500.0

    def test_srpe_load_multiplies_by_rpe(self):
        assert compute_session_load(sets=3, reps=5, weight_kg=100.0, rpe=8) == 12000.0

    def test_rpe_zero_returns_zero(self):
        # RPE=0 -> tonaż*0 = 0 (RPE jest jawnie podane, więc mnożymy)
        assert compute_session_load(sets=1, reps=10, weight_kg=20.0, rpe=0) == 0.0


class TestAggregateAndFill:
    def test_aggregate_sums_multiple_sessions_per_day(self):
        start = date(2026, 8, 1)
        pairs = [(start, 100.0), (start, 50.0), (start + timedelta(days=1), 200.0)]
        daily = aggregate_daily_loads(pairs)
        assert daily[start] == 150.0
        assert daily[start + timedelta(days=1)] == 200.0

    def test_fill_missing_days_adds_zeros(self):
        start = date(2026, 8, 1)
        end = date(2026, 8, 5)
        daily = {start: 100.0, start + timedelta(days=3): 200.0}
        filled = fill_missing_days(daily, start, end)
        assert len(filled) == 5  # 01..05 włącznie
        assert filled[0].load == 100.0
        assert filled[1].load == 0.0
        assert filled[3].load == 200.0


class TestAcuteChronic:
    def test_acute_is_mean_of_last_7(self):
        start = date(2026, 8, 1)
        series = _daily([0, 100, 0, 100, 0, 100, 0, 100], start)  # 8 dni
        # ostatnie 7 -> [100,0,100,0,100,0,100] mean = 400/7
        assert compute_acute_load(series, window=7) == round(400.0 / 7, 1)

    def test_acute_returns_zero_when_empty(self):
        assert compute_acute_load([], window=7) == 0.0

    def test_chronic_ewma_differs_from_rolling_mean(self):
        start = date(2026, 8, 1)
        series = _daily([100.0] * 28 + [0.0], start)  # potem gwałtowny spadek
        ewma = compute_chronic_load(series, window=28, use_ewma=True)
        plain = compute_chronic_load(series, window=28, use_ewma=False)
        # EWMA (alpha=0.05) na oknie [100*27 + 0] ciągnie wynik w dół przez ostatnie 0,
        # a prosta średnia rozmywa go równomiernie -> wartości się różnią
        assert ewma != plain
        assert isinstance(ewma, float)
        assert isinstance(plain, float)

    def test_chronic_returns_zero_when_empty(self):
        assert compute_chronic_load([], window=28) == 0.0

    def test_chronic_constant_load_converges_to_that_load(self):
        """Sanity: chronic na stałym obciążeniu powinien zbiec blisko tej wartości
        (regresja dla poprawki cold-start — patrz test_cold_start_position_independence)."""
        start = date(2026, 7, 1)
        series = _daily([100.0] * 28, start)
        chronic = compute_chronic_load(series, window=28)
        assert chronic == pytest.approx(100.0, abs=0.1)

    def test_cold_start_bug_position_no_longer_shrinks_gap(self):
        """AUDYT fix: EWMA inicjalizowana values[0] (stara implementacja) sztucznie
        ZBLIŻAŁA wyniki dwóch serii o identycznej sumie, ale przeciwnym rozkładzie
        w czasie -- bo pierwszy punkt okna dostawał wagę nieproporcjonalną do alpha,
        maskując to, GDZIE w oknie leżało obciążenie. EWMA poprawnie liczona powinna
        różnicować świeże obciążenie (wysoki chronic) od dawno wygasłego (niski chronic),
        nie zacierać tę różnicę.

        Seria A: trening skupiony w OSTATNIM tygodniu 28-dniowego okna (świeży).
        Seria B: identyczna suma load, ale skupiona w PIERWSZYM dniu okna (wygasły).
        Oczekiwanie: chronic(A) musi być wyraźnie WYŻSZY niż chronic(B) -- świeże
        obciążenie waży więcej w EWMA z natury metody, nie mniej.
        """
        start = date(2026, 7, 1)
        vals_recent = [0.0] * 20 + [2000.0] * 8   # trening w ostatnim tygodniu okna
        vals_old = [2000.0] + [0.0] * 27          # cały load w dniu 1, potem cisza

        series_recent = _daily(vals_recent, start)
        series_old = _daily(vals_old, start)

        chronic_recent = compute_chronic_load(series_recent, window=28)
        chronic_old = compute_chronic_load(series_old, window=28)

        assert chronic_recent > chronic_old
        # różnica musi być wyraźna (nie tylko formalnie większa) -- stary kod
        # (values[0] jako seed) dawał 673 vs 501, czyli różnicę ~172 mimo,
        # że fizjologicznie to skrajnie różne sytuacje (świeży trening vs
        # miesiąc ciszy po jednej sesji). Wymagamy wyraźnego rozjazdu.
        assert chronic_recent - chronic_old > 400

    def test_chronic_independent_of_which_day_starts_window(self):
        """Dwie serie o TEJ SAMEJ regularnej rutynie (trening co 2 dni, stały
        load), różniące się tylko tym, czy okno zaczyna się dniem treningowym
        czy odpoczynkowym, powinny dać zbliżony chronic -- nie zależny od
        przypadkowej fazy cyklu w momencie odcięcia okna (stary bug: seed=
        values[0] robił wynik wrażliwym właśnie na to przesunięcie fazowe)."""
        start = date(2026, 7, 1)
        # rutyna: trening (150) / odpoczynek (0), naprzemiennie, 28 dni
        pattern_start_training = [150.0 if i % 2 == 0 else 0.0 for i in range(28)]
        pattern_start_rest = [150.0 if i % 2 == 1 else 0.0 for i in range(28)]

        c1 = compute_chronic_load(_daily(pattern_start_training, start), window=28)
        c2 = compute_chronic_load(_daily(pattern_start_rest, start), window=28)

        # różnica powinna być mała względem skali wartości (rutyna identyczna,
        # różni się tylko przesunięciem fazowym o 1 dzień)
        assert abs(c1 - c2) < 0.15 * max(c1, c2)


class TestAcwrRatio:
    def test_optimal_zone(self):
        res = acwr_ratio(acute=100, chronic=100)
        assert res.ratio == 1.0
        assert res.zone == "optymalna"

    def test_underload_zone(self):
        res = acwr_ratio(acute=50, chronic=100)
        assert res.zone == "niedociążenie"

    def test_elevated_zone(self):
        res = acwr_ratio(acute=140, chronic=100)
        assert res.zone == "podwyższone ryzyko"

    def test_high_risk_zone(self):
        res = acwr_ratio(acute=160, chronic=100)
        assert res.zone == "wysokie ryzyko"

    def test_chronic_zero_sets_ratio_zero(self):
        res = acwr_ratio(acute=100, chronic=0)
        assert res.ratio == 0.0
        assert res.zone == "niedociążenie"


class TestReadinessModifier:
    def test_high_risk_adds_2(self):
        res = acwr_ratio(acute=160, chronic=100)
        assert acwr_readiness_modifier(res) == 2

    def test_elevated_adds_1(self):
        res = acwr_ratio(acute=140, chronic=100)
        assert acwr_readiness_modifier(res) == 1

    def test_optimal_adds_0(self):
        res = acwr_ratio(acute=100, chronic=100)
        assert acwr_readiness_modifier(res) == 0


class TestCardioSessionLoad:
    """Obciążenie cardio (MTB) w tej samej skali co tonaż (audyt #3)."""

    def test_duration_times_rpe(self):
        assert compute_cardio_session_load(duration_minutes=90, rpe=6) == 540.0

    def test_shorter_lighter_session(self):
        assert compute_cardio_session_load(duration_minutes=45, rpe=4) == 180.0

    def test_invalid_duration_raises(self):
        import pytest
        with pytest.raises(ValueError):
            compute_cardio_session_load(duration_minutes=0, rpe=6)

    def test_invalid_rpe_raises(self):
        import pytest
        with pytest.raises(ValueError):
            compute_cardio_session_load(duration_minutes=90, rpe=11)

    def test_cardio_session_daily_load(self):
        day, load = cardio_session_daily_load(
            {"startTime": "2026-08-06T08:00:00", "duration_minutes": 90, "rpe": 6}
        )
        assert day == date(2026, 8, 6)
        assert load == 540.0

    def test_cardio_session_missing_fields_returns_none(self):
        assert cardio_session_daily_load({"startTime": "2026-08-06T08:00:00"}) is None

    def test_cardio_merged_into_daily_load_series(self):
        """Sesja cardio sumuje się z tonażem Hevy tego samego dnia (audyt #3)."""
        today = date(2026, 8, 7)
        start = today - timedelta(days=14)
        hevy = [
            {"startTime": str(today - timedelta(days=1)) + "T18:00:00",
             "exercises": [{"sets": [{"type": "normal", "reps": 5, "weight": 100.0, "rpe": 8}]}]},
        ]
        cardio = [
            {"startTime": str(today - timedelta(days=1)) + "T08:00:00",
             "duration_minutes": 90, "rpe": 6},
            # poza oknem — pomijana
            {"startTime": str(today - timedelta(days=40)) + "T08:00:00",
             "duration_minutes": 120, "rpe": 7},
        ]
        series = build_daily_load_series(hevy, start, today, cardio_sessions=cardio)
        by_day = {s.day: s.load for s in series}
        # tonaż 5*100*8=4000 + cardio 90*6=540 -> 4540 tego dnia
        assert by_day[today - timedelta(days=1)] == pytest.approx(4000 + 540, abs=0.1)
        # sesja cardio poza oknem nie weszła do serii (poza start..end)
        assert (today - timedelta(days=40)) not in by_day

    def test_cardio_only_day(self):
        """Dzień bez siłowni, tylko MTB — load z samego cardio."""
        today = date(2026, 8, 7)
        start = today - timedelta(days=14)
        cardio = [
            {"startTime": str(today - timedelta(days=2)) + "T08:00:00",
             "duration_minutes": 60, "rpe": 5},
        ]
        series = build_daily_load_series([], start, today, cardio_sessions=cardio)
        by_day = {s.day: s.load for s in series}
        assert by_day[today - timedelta(days=2)] == 300.0


def _hevy_workout(day: date, load_series: list[tuple[float, int, float]]) -> dict:
    """Buduje workout Hevy o zadanym sRPE-load (weight*reps*rpe)."""
    return {
        "startTime": day.isoformat(),
        "exercises": [{
            "sets": [
                {"type": "normal", "weight": w, "reps": r, "rpe": rpe}
                for w, r, rpe in load_series
            ],
        }],
    }


def _hevy_load(day: date, weight: float = 100.0) -> dict:
    """Pojedynczy trening o load = weight (1 seria: weight kg x 1 rep x rpe=1)."""
    return _hevy_workout(day, [(weight, 1, 1.0)])


class TestBuildAcwr:
    def test_returns_full_structure(self):
        """build_acwr zwraca dict z result/acute/chronic/rpe_coverage/daily_loads."""
        target = date(2026, 8, 7)
        workouts = [_hevy_load(target - timedelta(days=i), weight=100.0) for i in range(28)]
        out = build_acwr(workouts, target)
        assert set(out) == {"result", "acute", "chronic", "rpe_coverage", "daily_loads"}
        assert out["result"].ratio == 1.0
        assert out["result"].zone == "optymalna"
        # daily_loads: okno start..end włącznie = ACWR_LOOKBACK_DAYS + 1 dni
        assert len(out["daily_loads"]) == ACWR_LOOKBACK_DAYS + 1

    def test_high_risk_zone_with_spike(self):
        """Gwałtowny wzrost obciążenia -> wysokie ryzyko."""
        target = date(2026, 8, 7)
        workouts = []
        # ostatnie 7 dni: ciężkie (1000 kg load), wcześniejsze lekkie (100)
        for i in range(28):
            w = 1000.0 if i < 7 else 100.0
            workouts.append(_hevy_load(target - timedelta(days=i), weight=w))
        out = build_acwr(workouts, target)
        assert out["result"].zone in ("podwyższone ryzyko", "wysokie ryzyko")

    def test_empty_workouts_underload(self):
        """Brak treningów -> niedociążenie, ratio 0."""
        out = build_acwr([], date(2026, 8, 7))
        assert out["result"].zone == "niedociążenie"
        assert out["result"].ratio == 0.0

    def test_rpe_coverage_reported(self):
        """Pokrycie RPE liczone z dostarczonych treningów."""
        target = date(2026, 8, 7)
        w_mixed = {
            "startTime": target.isoformat(),
            "exercises": [
                {"sets": [
                    {"type": "normal", "weight": 100, "reps": 5, "rpe": 8},
                    {"type": "normal", "weight": 100, "reps": 5, "rpe": None},
                ]},
            ],
        }
        out = build_acwr([w_mixed], target)
        assert out["rpe_coverage"]["total_working"] == 2
        assert out["rpe_coverage"]["with_rpe"] == 1
        assert out["rpe_coverage"]["coverage_pct"] == 50.0
