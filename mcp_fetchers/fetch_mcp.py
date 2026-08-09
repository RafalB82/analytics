#!/usr/bin/env python3
"""
fetch_mcp.py — PROGRAMISTYCZNY odczyt danych z MCP (Hevy + Apple) i uruchomienie
analizy gotowości. Zaprojektowany tak, żeby działał jako job cron BEZ agenta w pętli:
  skrypt sam łączy się z MCP, pobiera dane, normalizuje i odpala analytics.

DLACZEGO ISTNIEJE (zastępuje ręczne "agent woła MCP i wkleja JSON"):
  MCP serwery są osiągalne programistycznie:
    - Apple MCP: HTTP streamable na http://127.0.0.1:8766/mcp (mcp_server.py --http)
    - Hevy MCP:  HTTP streamable na http://127.0.0.1:3000/mcp (hevy-mcp.service)
                 — serwer long-running na loopbacku, bez startu subprocesu per-run.
                 Fallback (jeśli HTTP niedostępny → connection refused): subprocess
                 stdio `node standalone.mjs` (HEVY_API_KEY z env) z izolowanym
                 procesem i JSON-RPC handshake.
  Dzięki temu pipeline agenta (ręczne pobieranie + wklejanie) zmienia się w
  czysty: `python3 -m mcp_fetchers.fetch_mcp --target YYYY-MM-DD`.

FIRST RUN: Apple MCP ok (HTTP). Hevy domyślnie przez HTTP (hevy-mcp.service na
3000); stdio tylko jako fallback, gdy serwer nie odpowiada.

ARCHITEKTURA (zgodna z README/mcp_fetchers):
  Ta warstwa = "pobieranie przez MCP". Wykorzystuje istniejące *_normalize.py
  (hevy/apple) do konwersji i build_input.py do składania payloadu. Nie duplikuje
  logiki — tylko dodaje programistyczny transport do MCP.

Użycie:
  python3 -m mcp_fetchers.fetch_mcp --target 2026-08-09 [--out /tmp/r.json]
  python3 -m mcp_fetchers.fetch_mcp                      # domyślnie dziś
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import suppress

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # analytics/
sys.path.insert(0, BASE)

# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------
APPLE_MCP_URL = os.environ.get("APPLE_MCP_URL", "http://127.0.0.1:8766/mcp")
# MFP MCP — streamable HTTP (jak Apple). Kontener mfp-mcp nasłuchuje na
# localhost:8000 (port 8000, `ss`/docker ps: 0.0.0.0:8000->8000).
MFP_MCP_URL = os.environ.get("MFP_MCP_URL", "http://localhost:8000/mcp")
HEVY_BIN = os.environ.get("HEVY_BIN", "/home/rafal/hevy-mcp/packages/node/dist/standalone.mjs")
HEVY_API_KEY = os.environ.get("HEVY_API_KEY", "")
# Hevy MCP przez streamable HTTP (hevy-mcp.service na loopbacku, port 3000).
# Domyślny transport; stdio (HEVY_BIN) tylko jako fallback, gdy serwer nie odpowiada.
HEVY_MCP_URL = os.environ.get("HEVY_MCP_URL", "http://127.0.0.1:3000/mcp")
ACWR_LOOKBACK_DAYS = 35  # okno chronic ACWR (jak w analytics.acwr)
# MFP: okno do bilansu energetycznego — energy_balance używa 7d, więc pobieramy
# 7 + zapas 5 = 12 dni, żeby pokryć niepełne/mocno niskokaloryczne dni przy
# wyjeździe/weekendzie. NIE używamy ACWR_LOOKBACK_DAYS (35) — to marnowanie
# (MFP woła get_diary per dzień = wolne).
MFP_LOOKBACK_DAYS = int(os.environ.get("MFP_LOOKBACK_DAYS", "12"))

APPLE_TOOLS = {
    "daily_range": "get_daily_activity_range",
    "wrist_temp": "get_data",
    "workouts": "list_recent_workouts",
}
HEVY_TOOLS = {
    "workouts": "get-workouts",
    "workout_detail": "get-workout",
}


# ---------------------------------------------------------------------------
# Minimalny klient MCP transportu JSON-RPC
# ---------------------------------------------------------------------------
class JsonRpcError(Exception):
    pass


class McpHttpClient:
    """Klient MCP streamable HTTP (Apple mcp_server.py --http)."""
    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None

    def _call(self, payload: dict, expect_body: bool = True) -> dict | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if "mcp-session-id" in resp.headers:
                self.session_id = resp.headers["mcp-session-id"]
            body = resp.read().decode()
        if not expect_body or not body.strip():
            return None  # notification / 202 no-content
        return self._parse_sse_or_json(body, payload)

    def _parse_sse_or_json(self, body: str, payload: dict) -> dict:
        if body.lstrip().startswith("{"):
            return json.loads(body)
        # SSE: wiele bloków "data: {...}"
        result = None
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    result = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        if result is None:
            raise JsonRpcError(f"brak wyniku MCP HTTP: {body[:200]}")
        return self._check(result, payload)

    @staticmethod
    def _check(msg: dict, payload: dict) -> dict:
        if "error" in msg:
            raise JsonRpcError(f"MCP error: {msg['error']}")
        return msg

    def initialize(self) -> None:
        self._call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05",
                               "capabilities": {},
                               "clientInfo": {"name": "fetch_mcp", "version": "1"}}})
        # notification initialized — serwer odpowiada 202/no-content, nie czekamy
        with suppress(JsonRpcError):
            self._call({"jsonrpc": "2.0", "method": "notifications/initialized"},
                       expect_body=False)

    def call_tool(self, tool: str, arguments: dict) -> dict:
        msg = self._call({"jsonrpc": "2.0", "id": int(time.time() * 1000) % 100000,
                          "method": "tools/call",
                          "params": {"name": tool, "arguments": arguments}})
        # wynik tools/call: {"result":{"content":[{type:"text",text:"..."}],...}}
        result = msg.get("result", {})
        text = ""
        for c in result.get("content", []):
            if c.get("type") == "text":
                text += c.get("text", "")
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
        return result


class McpStdioClient:
    """Klient MCP przez subprocess stdio (Hevy standalone.mjs)."""
    def __init__(self, bin_path: str, api_key: str, timeout: float = 60.0):
        self._id = 0
        self._timeout = timeout
        env = dict(os.environ)
        env["HEVY_API_KEY"] = api_key
        self._proc = subprocess.Popen(
            ["node", bin_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env,
        )

    def _send(self, method: str, params: dict, notify: bool = False) -> dict | None:
        self._id += 1
        payload: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            payload["id"] = self._id
        line = (json.dumps(payload) + "\n").encode()
        assert self._proc.stdin
        self._proc.stdin.write(line)
        self._proc.stdin.flush()
        if notify:
            return None
        # czytaj stdout aż do matching id
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            out = self._proc.stdout.readline()
            if not out:
                time.sleep(0.05)
                continue
            try:
                msg = json.loads(out)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise JsonRpcError(f"Hevy MCP error: {msg['error']}")
                return msg.get("result", {})
        raise JsonRpcError("timeout czekając na odpowiedź Hevy")

    def initialize(self) -> None:
        self._send("initialize", {"protocolVersion": "2024-11-05",
                                  "capabilities": {},
                                  "clientInfo": {"name": "fetch_mcp", "version": "1"}})
        self._send("notifications/initialized", {}, notify=True)

    def call_tool(self, tool: str, arguments: dict) -> dict:
        result = self._send("tools/call", {"name": tool, "arguments": arguments})
        text = ""
        for c in (result or {}).get("content", []):
            if c.get("type") == "text":
                text += c.get("text", "")
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}
        return result or {}

    def close(self) -> None:
        with suppress(Exception):
            self._proc.terminate()


# ---------------------------------------------------------------------------
# Pobieranie danych z MCP
# ---------------------------------------------------------------------------
def make_workout_clickable_name(w: dict) -> str:
    return f"{w.get('start_time','')[:10]} {w.get('title','')}"


def fetch_hevy(client, target: str,
                lookback_days: int = ACWR_LOOKBACK_DAYS) -> list:
    """Pobiera workouty Hevy (lista) + szczegóły (exercises/sets) z okna
    [target-lookback, target]. Zwraca surowe workouty w formacie MCP.
    client: McpHttpClient (HTTP) lub McpStdioClient (fallback stdio) — oba mają
    call_tool o tej samej sygnaturze, więc ciało jest wspólne."""
    from datetime import date, timedelta
    window_start = (date.fromisoformat(target) - timedelta(days=lookback_days)).isoformat()

    # 1) lista workoutów — standalone zwraca GOŁĄ LISTĘ 5/sztukę na stronę i
    #    akceptuje tylko {page}. Iterujemy aż strona wyjdzie poza okno ACWR.
    all_summaries = []
    for page in range(1, 10):  # safety cap
        res = client.call_tool("get-workouts", {"page": page})
        workouts = res if isinstance(res, list) else res.get("workouts", [])
        if not workouts:
            break
        all_summaries.extend(workouts)
        oldest = min((w.get("start_time") or "")[:10] for w in workouts if w.get("start_time"))
        if oldest and oldest < window_start:
            break  # kolejne strony coraz starsze — wyjdź

    # sortuj rosnąco po start_time — determinizm niezależny od kolejności stron;
    # build_daily_load_series zakłada porządek chronologiczny dla rolling window
    all_summaries.sort(key=lambda w: (w.get("start_time") or ""))

    # 2) szczegóły workouts w oknie -> raw dict z exercises/sets
    raw = []
    for w in all_summaries:
        st = (w.get("start_time") or "")[:10]
        if st and st < window_start:
            continue
        wid = w.get("id")
        detail = client.call_tool("get-workout", {"workout_id": wid})
        wd = detail.get("workout", detail)
        if wd and wd.get("exercises"):
            raw.append({"workout": wd})
    return raw


def fetch_apple(client: McpHttpClient, target: str, lookback_days: int = ACWR_LOOKBACK_DAYS) -> dict:
    """Pobiera daily/temp/workouts z Apple MCP. Zwraca surowe struktury."""
    from datetime import date, timedelta
    t = date.fromisoformat(target)
    start = (t - timedelta(days=lookback_days)).isoformat()

    daily = client.call_tool("get_daily_activity_range",
                             {"start_date": start, "end_date": target})
    temp = client.call_tool("get_data", {"name": "apple_sleeping_wrist_temperature"})
    # zakres dat dla workoutów, żeby przy dłuższym lookbacku nie polegać tylko
    # na "ostatnich N" (list_recent_workouts przyjmuje start_date/end_date)
    workouts = client.call_tool("list_recent_workouts",
                                {"limit": 50, "start_date": start, "end_date": target})

    temp_points = temp.get("points", [])
    return {
        "daily": daily if isinstance(daily, list) else daily.get("result", []),
        "temp": [{"date": p["date"], "value": p["value"]} for p in temp_points],
        "workouts": workouts if isinstance(workouts, list) else workouts.get("result", []),
    }


def fetch_mfp(client: McpHttpClient, target: str, days: int = 7) -> list:
    """Pobiera dzienniki MFP (zjedzone kcal) z okna [target-days+1, target].

    Woła narzędzie MCP `mfp_get_diary` (parametr `{params: {date}}`) per dzień
    i zwraca listę surowych dzienników ({date, meals, daily_totals}).

    WAŻNE (fix 2026-08-09): okno MUSI kończyć się NA `target` (range days..0),
    inaczej najnowszy dzień wypada z analizy — przez co energy_balance pokazywał
    zaniżone zjedzone kcal i fałszywy niedobór. Dodatkowo odsiewamy wpisy bez
    sensownego `date` (serwer zwraca pusty obiekt dla dni bez danych, zamiast
    błędu)."""
    from datetime import date, timedelta
    t = date.fromisoformat(target)
    diaries = []
    for i in range(days, -1, -1):  # days..0 → włącznie z target
        d = (t - timedelta(days=i)).isoformat()
        try:
            # response_format='json' jest KLUCZOWE: bez niego mfp_get_diary
            # zwraca markdown ({"raw": "## Food Diary..."}), którego nie da się
            # sparsować do dziennika z daily_totals.
            res = client.call_tool(
                "mfp_get_diary",
                {"params": {"date": d, "response_format": "json"}},
            )
        except JsonRpcError as e:
            print(f"[fetch_mcp] mfp {d}: {e}", file=sys.stderr)
            continue
        if isinstance(res, list):
            for x in res:
                if isinstance(x, dict) and x.get("date"):
                    diaries.append(x)
        elif isinstance(res, dict) and res.get("date"):
            diaries.append(res)
    return diaries


def write_stdin_json(data: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def main() -> int:
    import argparse
    from datetime import date

    ap = argparse.ArgumentParser(description="Odczyt MCP + analiza gotowości (cron-ready)")
    ap.add_argument("--target", default=str(date.today()))
    ap.add_argument("--phase", default="utrzymanie",
                    choices=["utrzymanie", "redukcja", "masa"])
    ap.add_argument("--weight", type=float, default=None)
    ap.add_argument("--days", type=int, default=ACWR_LOOKBACK_DAYS,
                    help="okno wstecz w dniach (chronic ACWR)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--skip-apple", action="store_true")
    ap.add_argument("--skip-hevy", action="store_true")
    ap.add_argument("--skip-mfp", action="store_true",
                    help="pomiń pobieranie dziennika MFP (zjedzone kcal)")
    ap.add_argument("--only-cardio", action="store_true",
                    help="pobierz TYLKO cardio z Apple (pomija Hevy) — np. dozbieranie chronic 28d")
    args = ap.parse_args()

    tmp = os.path.join(BASE, "tmp")
    os.makedirs(tmp, exist_ok=True)

    # --only-cardio implikuje pominięcie Hevy (siła z Hevy nas tu nie interesuje)
    if args.only_cardio:
        args.skip_hevy = True

    # ---- Hevy (HTTP streamable; fallback: stdio) ----
    if not args.skip_hevy:
        # Domyślnie HTTP (hevy-mcp.service na 127.0.0.1:3000). Jeśli serwer nie
        # odpowiada (connection refused), wróć do stdio z izolowanym procesem.
        h = None
        if HEVY_MCP_URL:
            try:
                print(f"[fetch_mcp] łączę się z Hevy (HTTP {HEVY_MCP_URL})...", file=sys.stderr)
                h = McpHttpClient(HEVY_MCP_URL)
                h.initialize()
            except Exception as e:
                print(f"[fetch_mcp] Hevy HTTP niedostępny ({e}); fallback do stdio", file=sys.stderr)
                h = None
        if h is None:
            if not HEVY_API_KEY:
                print("ERROR: brak HEVY_API_KEY w env (i HEVY_MCP_URL nie odpowiada)", file=sys.stderr)
                return 2
            print("[fetch_mcp] łączę się z Hevy (fallback stdio)...", file=sys.stderr)
            h = McpStdioClient(HEVY_BIN, HEVY_API_KEY)
            h.initialize()
        try:
            raw_hevy = fetch_hevy(h, args.target, lookback_days=args.days)
            print(f"[fetch_mcp] pobrano {len(raw_hevy)} workoutów Hevy", file=sys.stderr)
            write_stdin_json(raw_hevy, os.path.join(tmp, "raw_hevy.json"))
        finally:
            if isinstance(h, McpStdioClient):
                h.close()

    # ---- Apple (HTTP) ----
    if not args.skip_apple:
        print("[fetch_mcp] łączę się z Apple MCP (HTTP)...", file=sys.stderr)
        a = McpHttpClient(APPLE_MCP_URL)
        a.initialize()
        apple_raw = fetch_apple(a, args.target, lookback_days=args.days)
        print(f"[fetch_mcp] apple: {len(apple_raw['daily'])} dni, "
              f"{len(apple_raw['workouts'])} workoutów, {len(apple_raw['temp'])} temp",
              file=sys.stderr)
        write_stdin_json(apple_raw, os.path.join(tmp, "raw_apple.json"))

    # ---- MFP (HTTP) — zjedzone kcal dla bilansu energetycznego ----
    if not args.skip_mfp:
        print("[fetch_mcp] łączę się z MFP MCP (HTTP)...", file=sys.stderr)
        try:
            m = McpHttpClient(MFP_MCP_URL)
            m.initialize()
            raw_mfp = fetch_mfp(m, args.target, days=MFP_LOOKBACK_DAYS)
            print(f"[fetch_mcp] mfp: {len(raw_mfp)} dni z dziennikiem", file=sys.stderr)
            write_stdin_json(raw_mfp, os.path.join(tmp, "raw_mfp.json"))
        except JsonRpcError as e:
            print(f"[fetch_mcp] mfp pominięty: {e}", file=sys.stderr)

    # ---- Normalizacja (przez istniejące skrypty) ----
    return _run_analysis(tmp, args)


def _run_analysis(tmp: str, args) -> int:
    from . import apple_normalize, build_input, hevy_normalize

    # normalizacja surowych plików (jeśli pobrane) przez bezpośrednie API
    hevy_path = os.path.join(tmp, "raw_hevy.json")
    if os.path.exists(hevy_path):
        with open(hevy_path) as f:
            raw_hevy = json.load(f)
        workouts = [hn for hn in
                    (hevy_normalize.normalize_workout(w) for w in raw_hevy)
                    if hn is not None]
        with open(os.path.join(tmp, "hevy_workouts.json"), "w") as f:
            json.dump(workouts, f, ensure_ascii=False)
        print(f"[fetch_mcp] znormalizowano {len(workouts)} workoutów Hevy", file=sys.stderr)

    apple_path = os.path.join(tmp, "raw_apple.json")
    if os.path.exists(apple_path):
        with open(apple_path) as f:
            raw_apple = json.load(f)
        apple_daily = [apple_normalize.normalize_daily_point(d)
                       for d in (raw_apple.get("daily") or [])]
        apple_temp = [x for x in (apple_normalize.normalize_temp_point(p)
                                  for p in (raw_apple.get("temp") or [])) if x]
        # współdzielony set dedupe na czas JEDNEGO przebiegu _run_analysis —
        # zamiast globalnego _SEEN_IDS (który przeżywał między uruchomieniami
        # długożyjącego procesu i błędnie odrzucał treningi z tym samym id
        # ale inną datą). Zob. apple_normalize.normalize_workout(seen_ids=...).
        _apple_seen: set[str] = set()
        apple_workouts = [x for x in (apple_normalize.normalize_workout(w, _apple_seen)
                                     for w in (raw_apple.get("workouts") or [])) if x]
        with open(os.path.join(tmp, "apple_input.json"), "w") as f:
            json.dump({"apple_daily": apple_daily, "apple_temp": apple_temp,
                       "apple_workouts": apple_workouts}, f, ensure_ascii=False)
        print(f"[fetch_mcp] apple: {len(apple_daily)} dni, {len(apple_workouts)} cardio",
              file=sys.stderr)

    # MFP: surowy dziennik -> zjedzone kcal (mfp_normalize), jesli pobrany
    mfp_path = os.path.join(tmp, "raw_mfp.json")
    if os.path.exists(mfp_path):
        from . import mfp_normalize
        with open(mfp_path) as f:
            raw_mfp = json.load(f)
        kcal = mfp_normalize.normalize_diaries(raw_mfp)
        with open(os.path.join(tmp, "mfp_kcal.json"), "w") as f:
            json.dump(kcal, f, ensure_ascii=False)
        print(f"[fetch_mcp] mfp: {len(kcal)} dni z kcal", file=sys.stderr)

    # ---- build_input - czyta z globalnego katalogu BASE/tmp ----
    hevy, apple_daily, apple_temp, apple_workouts, mfp_kcal = build_input.load_normalized()
    payload = build_input.build_payload(args.target, args.phase, args.weight,
                                        hevy, apple_daily, apple_temp, apple_workouts,
                                        mfp_daily_kcal=mfp_kcal)
    from analytics.run_analysis import run
    result = run(payload)
    vol = build_input.aggregate_volume(hevy)
    if vol:
        result["hevy_volume"] = vol

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
