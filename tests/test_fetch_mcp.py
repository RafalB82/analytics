"""Testy warstwy pobierania (fetch_mcp): logika stronicowania/okien na fałszywym kliencie
oraz parsowanie odpowiedzi JSON-RPC/SSE. Bez sieci."""
from __future__ import annotations

import json

import pytest

from mcp_fetchers import fetch_mcp
from mcp_fetchers.fetch_mcp import (
    JsonRpcError,
    McpHttpClient,
    fetch_apple,
    fetch_hevy,
    fetch_mfp,
    make_workout_clickable_name,
    write_stdin_json,
)


class FakeClient:
    """call_tool(tool, args) -> odpowiedź z tabeli; zapisuje wywołania."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, tool, arguments):
        self.calls.append((tool, arguments))
        r = self.responses[tool]
        return r(arguments) if callable(r) else r


class TestFetchHevy:
    def test_paginates_until_window_and_fetches_details(self):
        pages = {
            1: [{"id": "a", "start_time": "2026-08-08T10:00:00Z"}],
            2: [{"id": "b", "start_time": "2026-05-01T10:00:00Z"}],  # poza oknem
        }
        detail = lambda a: {"workout": {"id": a["workout_id"], "exercises": [{"sets": [1]}]}}  # noqa: E731
        c = FakeClient({
            "get-workouts": lambda a: pages.get(a["page"], []),
            "get-workout": detail,
        })
        out = fetch_hevy(c, "2026-08-09", lookback_days=35)
        assert [w["workout"]["id"] for w in out] == ["a"]  # stary pominięty
        assert sum(1 for t, _ in c.calls if t == "get-workouts") == 2  # stop po wyjściu z okna

    def test_page_without_start_time_does_not_crash(self):
        c = FakeClient({"get-workouts": lambda a: [{"id": "x"}] if a["page"] == 1 else [],
                        "get-workout": {"workout": {"id": "x", "exercises": []}}})
        assert fetch_hevy(c, "2026-08-09") == []  # brak exercises -> odrzucony, bez wyjątku

    def test_dict_response_and_empty_first_page(self):
        c = FakeClient({"get-workouts": {"workouts": []}})
        assert fetch_hevy(c, "2026-08-09") == []

    def test_sorted_ascending(self):
        c = FakeClient({
            "get-workouts": lambda a: (
                [{"id": "new", "start_time": "2026-08-08T10:00:00Z"},
                 {"id": "old", "start_time": "2026-08-01T10:00:00Z"}] if a["page"] == 1 else []),
            "get-workout": lambda a: {"workout": {"id": a["workout_id"], "exercises": [1]}},
        })
        ids = [w["workout"]["id"] for w in fetch_hevy(c, "2026-08-09")]
        assert ids == ["old", "new"]


class TestFetchApple:
    def test_window_and_shapes(self):
        c = FakeClient({
            "get_daily_activity_range": {"result": [{"date": "2026-08-08"}]},
            "get_data": {"points": [{"date": "2026-08-08", "value": 35.9},
                                    {"date": None, "value": 1.0}, {"value": 2}, "junk"]},
            "list_recent_workouts": [{"id": 1}],
        })
        out = fetch_apple(c, "2026-08-09", lookback_days=7)
        assert out["daily"] == [{"date": "2026-08-08"}]
        assert out["temp"] == [{"date": "2026-08-08", "value": 35.9}]
        assert out["workouts"] == [{"id": 1}]
        args = dict(c.calls)["get_daily_activity_range"]
        assert args == {"start_date": "2026-08-02", "end_date": "2026-08-09"}


class TestFetchMfp:
    def test_window_ends_on_target_and_filters_empty(self):
        def diary(a):
            d = a["params"]["date"]
            return {"date": d, "daily_totals": {"calories": 2000}} if d != "2026-08-07" else {}
        c = FakeClient({"mfp_get_diary": diary})
        out = fetch_mfp(c, "2026-08-09", days=3)
        assert [x["date"] for x in out] == ["2026-08-06", "2026-08-08", "2026-08-09"]
        assert all(a["params"]["response_format"] == "json" for _, a in c.calls)

    def test_rpc_error_skips_day(self, capsys):
        def diary(a):
            if a["params"]["date"] == "2026-08-08":
                raise JsonRpcError("boom")
            return [{"date": a["params"]["date"]}, "junk", {"nodate": 1}]
        out = fetch_mfp(FakeClient({"mfp_get_diary": diary}), "2026-08-09", days=1)
        assert [x["date"] for x in out] == ["2026-08-09"]
        assert "boom" in capsys.readouterr().err


class TestHttpClientParsing:
    def test_plain_json_body(self):
        c = McpHttpClient("http://x")
        assert c._parse_sse_or_json('{"result": 1}', {}) == {"result": 1}

    def test_sse_takes_last_valid_data_block(self):
        body = 'event: message\ndata: not-json\ndata: {"result": {"ok": true}}\n'
        assert McpHttpClient("http://x")._parse_sse_or_json(body, {}) == {"result": {"ok": True}}

    def test_sse_error_is_raised(self):
        body = 'data: {"error": {"code": -1}}\n'
        with pytest.raises(JsonRpcError):
            McpHttpClient("http://x")._parse_sse_or_json(body, {})

    def test_sse_without_result_raises(self):
        with pytest.raises(JsonRpcError):
            McpHttpClient("http://x")._parse_sse_or_json("event: ping\n", {})

    def test_call_tool_decodes_text_content(self, monkeypatch):
        c = McpHttpClient("http://x")
        payload = {"result": {"content": [{"type": "text", "text": json.dumps({"a": 1})}]}}
        monkeypatch.setattr(c, "_call", lambda *a, **k: payload)
        assert c.call_tool("t", {}) == {"a": 1}

    def test_call_tool_non_json_text_returns_raw(self, monkeypatch):
        c = McpHttpClient("http://x")
        payload = {"result": {"content": [{"type": "text", "text": "## markdown"}]}}
        monkeypatch.setattr(c, "_call", lambda *a, **k: payload)
        assert c.call_tool("t", {}) == {"raw": "## markdown"}

    def test_call_tool_without_text_returns_result(self, monkeypatch):
        c = McpHttpClient("http://x")
        monkeypatch.setattr(c, "_call", lambda *a, **k: {"result": {"content": []}})
        assert c.call_tool("t", {}) == {"content": []}


def test_helpers(tmp_path):
    assert make_workout_clickable_name({"start_time": "2026-08-08T10:00", "title": "Push"}) == "2026-08-08 Push"
    target = tmp_path / "sub" / "x.json"
    write_stdin_json({"ż": 1}, str(target))
    assert json.loads(target.read_text(encoding="utf-8")) == {"ż": 1}
    assert fetch_mcp.ACWR_LOOKBACK_DAYS == 35


class TestClientEdgeCases:
    def test_plain_json_rpc_error_is_raised(self):
        with pytest.raises(JsonRpcError):
            McpHttpClient("http://x")._parse_sse_or_json('{"error": {"code": -32000}}', {})

    def test_call_tool_empty_response_raises(self, monkeypatch):
        c = McpHttpClient("http://x")
        monkeypatch.setattr(c, "_call", lambda *a, **k: None)
        with pytest.raises(JsonRpcError):
            c.call_tool("t", {})


class _FakeHttp:
    """Podmiana McpHttpClient: odpowiedzi zależne od URL serwera."""

    fail_urls: set[str] = set()

    def __init__(self, url, timeout=30.0):
        self.url = url

    def initialize(self):
        if self.url in self.fail_urls:
            raise OSError("connection refused")

    def call_tool(self, tool, arguments):
        if tool == "get-workouts":
            return [{"id": "w1", "start_time": "2026-08-08T10:00:00+00:00"}] if arguments["page"] == 1 else []
        if tool == "get-workout":
            return {"workout": {"id": "w1", "start_time": "2026-08-08T10:00:00+00:00",
                                "title": "Push", "exercises": [{"title": "Bench", "sets": [
                                    {"type": "normal", "weight_kg": 100, "reps": 5, "rpe": 8}]}]}}
        if tool == "get_daily_activity_range":
            return [{"date": "2026-08-08", "resting_heart_rate": 52, "heart_rate_variability": 45}]
        if tool == "get_data":
            return {"points": [{"date": "2026-08-08", "value": 35.9}]}
        if tool == "list_recent_workouts":
            return [{"id": "a1", "name": "Running", "start": "2026-08-07T17:00:00",
                     "duration_min": 30, "avg_heart_rate_bpm": 150}]
        if tool == "mfp_get_diary":
            d = arguments["params"]["date"]
            return {"date": d, "daily_totals": {"calories": 2400.0}}
        raise AssertionError(tool)


class TestMainOrchestration:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        from mcp_fetchers import build_input

        monkeypatch.setattr(fetch_mcp, "BASE", str(tmp_path))
        monkeypatch.setattr(build_input, "BASE", str(tmp_path))
        monkeypatch.setattr(fetch_mcp, "McpHttpClient", _FakeHttp)
        monkeypatch.setattr(_FakeHttp, "fail_urls", set())
        captured = {}
        monkeypatch.setattr("analytics.run_analysis.run",
                            lambda payload: captured.setdefault("payload", payload) and {"status": "ok"})
        return tmp_path, captured

    def _run(self, monkeypatch, *args):
        monkeypatch.setattr("sys.argv", ["fetch_mcp", "--target", "2026-08-09", *args])
        return fetch_mcp.main()

    def test_full_run_builds_payload_from_all_sources(self, env, monkeypatch, capsys):
        tmp_path, captured = env
        assert self._run(monkeypatch, "--days", "14") == 0
        p = captured["payload"]
        assert len(p["hevy_workouts"]) == 1
        assert len(p["apple_daily"]) == 1 and len(p["apple_temp"]) == 1
        assert len(p["apple_workouts"]) == 1
        assert len(p["mfp_daily_kcal"]) >= 1
        assert '"status": "ok"' in capsys.readouterr().out.replace("'", '"')

    def test_stale_artifacts_are_removed_between_runs(self, env, monkeypatch):
        tmp_path, captured = env
        (tmp_path / "tmp").mkdir()
        (tmp_path / "tmp" / "hevy_workouts.json").write_text('[{"stale": true}]')
        (tmp_path / "tmp" / "keep.txt").write_text("user file")
        self._run(monkeypatch, "--skip-hevy", "--skip-apple", "--skip-mfp")
        assert captured["payload"]["hevy_workouts"] == []
        assert (tmp_path / "tmp" / "keep.txt").exists()

    def test_only_cardio_skips_hevy(self, env, monkeypatch):
        tmp_path, captured = env
        self._run(monkeypatch, "--only-cardio", "--skip-mfp")
        assert captured["payload"]["hevy_workouts"] == []
        assert len(captured["payload"]["apple_workouts"]) == 1

    def test_apple_failure_does_not_block_result(self, env, monkeypatch, capsys):
        tmp_path, captured = env
        _FakeHttp.fail_urls = {fetch_mcp.APPLE_MCP_URL}
        assert self._run(monkeypatch, "--skip-mfp") == 0
        assert captured["payload"]["apple_daily"] == []
        assert "apple pominięty" in capsys.readouterr().err

    def test_hevy_down_without_api_key_returns_2(self, env, monkeypatch):
        _FakeHttp.fail_urls = {fetch_mcp.HEVY_MCP_URL}
        monkeypatch.setattr(fetch_mcp, "HEVY_API_KEY", "")
        assert self._run(monkeypatch) == 2

    def test_out_file_written(self, env, monkeypatch):
        tmp_path, _ = env
        out = tmp_path / "result.json"
        self._run(monkeypatch, "--skip-hevy", "--skip-apple", "--skip-mfp", "--out", str(out))
        assert json.loads(out.read_text(encoding="utf-8")) == {"status": "ok"}
