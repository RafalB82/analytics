"""Testy bilansu energetycznego (energy_balance.py)."""
from __future__ import annotations

from datetime import date, timedelta

from analytics.config import settings
from analytics.energy_balance import (
    classify_deficit_risk,
    compute_energy_balance,
)


def _eaten(days_back: int, kcal: float) -> dict:
    d = date(2026, 8, 9) - timedelta(days=days_back)
    return {"day": d.isoformat(), "kcal": kcal}


class TestComputeEnergyBalance:
    def test_surplus_no_deficit(self):
        # wydatek 2500, zjada 2800 przez 7 dni -> nadwyżka (bilans dodatni), ryzyko niskie
        eaten = [_eaten(i, 2800) for i in range(7)]
        res = compute_energy_balance(eaten, expenditure_kcal=2500)
        assert res.status == "ok"
        assert res.cumulative_deficit_kcal > 0  # nadwyżka -> bilans dodatni
        assert res.deficit_risk == "niski"
        assert res.covering_pct > 100

    def test_large_deficit_high_risk(self):
        # wydatek 2500, zjada 1800 przez 7 dni -> skumulowany -4900 kcal -> wysoki
        eaten = [_eaten(i, 1800) for i in range(7)]
        res = compute_energy_balance(eaten, expenditure_kcal=2500)
        assert res.status == "ok"
        assert res.cumulative_deficit_kcal < -3500
        assert res.deficit_risk == "wysoki"

    def test_insufficient_data(self):
        # tylko 1 dzień z danymi < min_valid_days=3 -> niewystarczające dane
        eaten = [_eaten(0, 2000), _eaten(1, 2100)]  # 2 dni < 3
        res = compute_energy_balance(eaten, expenditure_kcal=2500)
        assert res.status == "niewystarczające dane"
        assert res.deficit_risk == "niewystarczające dane"

    def test_empty_eaten(self):
        res = compute_energy_balance([], expenditure_kcal=2500)
        assert res.status == "niewystarczające dane"

    def test_window_respects_recent(self):
        # 10 dni danych, ale okno 7 -> liczy tylko ostatnie 7
        eaten = [_eaten(i, 3000) for i in range(10)]
        res = compute_energy_balance(eaten, expenditure_kcal=2500)
        assert res.status == "ok"
        assert res.n_valid_days == 7  # okno domyślne 7


class TestClassifyDeficitRisk:
    def test_thresholds(self):
        lo = settings.ENERGY_BALANCE.deficit_low_kcal
        hi = settings.ENERGY_BALANCE.deficit_high_kcal
        assert classify_deficit_risk(-100) == "niski"            # mały / nadwyżka
        assert classify_deficit_risk(-(lo + 500)) == "średni"    # wyraźny deficyt
        assert classify_deficit_risk(-(hi + 1000)) == "wysoki"   # duży deficyt
