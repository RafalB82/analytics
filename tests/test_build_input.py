"""Testy build_input: ładowanie znormalizowanych plików, payload, agregacja objętości, CLI."""
from __future__ import annotations

import json

from mcp_fetchers import build_input


def _write(tmp, name, data):
    (tmp / "tmp").mkdir(exist_ok=True)
    (tmp / "tmp" / name).write_text(json.dumps(data), encoding="utf-8")


class TestLoadNormalized:
    def test_missing_files_return_empty_and_warn(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(build_input, "BASE", str(tmp_path))
        assert build_input.load_normalized() == ([], [], [], [], [])
        err = capsys.readouterr().err
        assert err.count("[warn]") == 3

    def test_loads_all_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(build_input, "BASE", str(tmp_path))
        _write(tmp_path, "hevy_workouts.json", [{"startTime": "2026-08-01T10:00:00Z"}])
        _write(tmp_path, "apple_input.json",
               {"apple_daily": [1], "apple_temp": [2], "apple_workouts": [3]})
        _write(tmp_path, "mfp_kcal.json", [{"day": "2026-08-01", "kcal": 2000}])
        hevy, daily, temp, wk, mfp = build_input.load_normalized()
        assert (len(hevy), daily, temp, wk, len(mfp)) == (1, [1], [2], [3], 1)


def test_build_payload_shape():
    p = build_input.build_payload("2026-08-09", "redukcja", 71.0, [1], [2], [3], [4], None)
    assert p["source"] == "apple+hevy+mfp"
    assert p["target_date"] == "2026-08-09"
    assert p["params"] == {"phase": "redukcja", "bodyweight_kg": 71.0}
    assert p["mfp_daily_kcal"] == []
    assert (p["hevy_workouts"], p["apple_daily"], p["apple_temp"], p["apple_workouts"]) == ([1], [2], [3], [4])


class TestAggregateVolume:
    def test_empty_and_no_volume(self):
        assert build_input.aggregate_volume([]) == {}
        assert build_input.aggregate_volume([{"title": "x"}, {"_volume": {}}]) == {}

    def test_sums_and_warmup_share(self):
        hevy = [{"_volume": {"tonnage_total": 1000.0, "tonnage_working": 800.0}},
                {"_volume": {"tonnage_total": 500.0, "tonnage_working": 500.0}}]
        v = build_input.aggregate_volume(hevy)
        assert v == {"n_workouts": 2, "tonnage_total": 1500.0, "tonnage_working": 1300.0,
                     "warmup_share_pct": 13.3}

    def test_zero_total_no_division_error(self):
        v = build_input.aggregate_volume([{"_volume": {"tonnage_total": 0.0, "tonnage_working": 0.0}}])
        assert v["warmup_share_pct"] == 0.0


def test_main_runs_analysis_and_writes_out(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build_input, "BASE", str(tmp_path))
    _write(tmp_path, "hevy_workouts.json",
           [{"_volume": {"tonnage_total": 10.0, "tonnage_working": 10.0}}])
    captured = {}

    def fake_run(payload):
        captured["payload"] = payload
        return {"status": "ok"}

    monkeypatch.setattr("analytics.run_analysis.run", fake_run)
    out = tmp_path / "res.json"
    monkeypatch.setattr("sys.argv", ["build_input", "--target", "2026-08-09",
                                     "--phase", "masa", "--weight", "72", "--out", str(out)])
    assert build_input.main() == 0
    assert captured["payload"]["params"] == {"phase": "masa", "bodyweight_kg": 72.0}
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert result["hevy_volume"]["n_workouts"] == 1
    assert json.loads(capsys.readouterr().out) == result
