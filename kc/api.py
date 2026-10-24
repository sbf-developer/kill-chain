"""HTTP API and static file server for the analyst console.

Standard library only: no build step, no dependency tree, no node_modules. Run
`python -m kc serve` and open the printed URL.

Endpoints
    GET  /api/scenarios                 list
    GET  /api/scenarios/<id>            one scenario, full chain
    POST /api/run                       {scenario, runs, seed, policy, sensitivity}
    POST /api/compare                   {a, b, runs, seed}
    GET  /api/cot/<id>                  Cursor-on-Target XML for one simulated run
    GET  /api/audit/<id>                hash-chained decision log
"""

from __future__ import annotations

import json
import threading
import webbrowser
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import BANNER, __version__
from . import cot as cot_mod
from .analysis import build_report, compare as build_comparison
from .audit import AuditLog, decision_records
from .doctrine import Policy, PolicyError, describe_policy
from .engine import monte_carlo, simulate
from .model import Scenario, ScenarioError, load_all

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios"
WEB_DIR = ROOT / "web"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".cot": "application/xml; charset=utf-8",
}


@lru_cache(maxsize=1)
def catalogue() -> tuple[Scenario, ...]:
    return tuple(load_all(SCENARIO_DIR))


def scenario_by_id(scenario_id: str) -> Scenario:
    for scenario in catalogue():
        if scenario.id == scenario_id:
            return scenario
    raise ScenarioError(f"unknown scenario '{scenario_id}'")


@lru_cache(maxsize=64)
def cached_report(
    scenario_id: str, runs: int, seed: int, policy_json: str, sensitivity: bool
) -> dict[str, Any]:
    scenario = scenario_by_id(scenario_id)
    policy = scenario.policy
    if policy_json:
        policy = Policy.from_dict(json.loads(policy_json), base=scenario.policy)
    return build_report(scenario, runs=runs, seed=seed, policy=policy, sensitivity=sensitivity)


def _int(payload: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    if not lo <= value <= hi:
        raise ValueError(f"'{key}' must be between {lo} and {hi}")
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = f"kchain/{__version__}"

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Keep the console quiet about API polling; log everything else."""
        if self.path.startswith("/api/"):
            return
        super().log_request(code, size)

    # -- helpers ---------------------------------------------------------- #

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, allow_nan=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        target = (WEB_DIR / rel).resolve()
        # is_relative_to, not a string prefix: a sibling directory whose name
        # starts with "web" must not be servable.
        if not target.is_relative_to(WEB_DIR.resolve()) or not target.is_file():
            self._error(404, f"not found: {path}")
            return
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), content_type)

    def _query_seed(self, parsed: Any) -> int:
        raw = parse_qs(parsed.query).get("seed", ["7"])[0]
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"seed must be an integer, got {raw!r}") from exc

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON body: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    # -- routes ----------------------------------------------------------- #

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/scenarios":
                self._json(
                    [
                        {
                            **scenario.as_summary(),
                            "policy_rules": describe_policy(scenario.policy),
                        }
                        for scenario in catalogue()
                    ]
                )
                return
            if path.startswith("/api/scenarios/"):
                scenario = scenario_by_id(path.rsplit("/", 1)[-1])
                self._json(
                    {
                        **scenario.as_summary(),
                        "nodes": [n.as_dict() for n in scenario.nodes],
                        "edges": [e.as_dict() for e in scenario.edges],
                        "policy_rules": describe_policy(scenario.policy),
                        "evidence": scenario.evidence.as_dict() if scenario.evidence else None,
                        "notes": scenario.notes,
                    }
                )
                return
            if path.startswith("/api/cot/"):
                scenario = scenario_by_id(path.rsplit("/", 1)[-1])
                seed = self._query_seed(parsed)
                result = simulate(scenario, __import__("random").Random(seed))
                self._send(
                    200,
                    cot_mod.scenario_cot(scenario, result).encode("utf-8"),
                    "application/xml; charset=utf-8",
                )
                return
            if path.startswith("/api/audit/"):
                scenario = scenario_by_id(path.rsplit("/", 1)[-1])
                seed = self._query_seed(parsed)
                result = simulate(scenario, __import__("random").Random(seed))
                log = AuditLog()
                log.extend(
                    decision_records(
                        result.decisions,
                        scenario_id=scenario.id,
                        scenario_fingerprint=scenario.source_fingerprint,
                        target=scenario.target.label,
                        policy=scenario.policy.as_dict(),
                        seed=seed,
                    )
                )
                self._json(
                    {
                        "banner": BANNER,
                        "records": log.records,
                        "verification": log.verify().as_dict(),
                        "jsonl": log.to_jsonl(),
                    }
                )
                return
            if path == "/api/health":
                self._json({"ok": True, "version": __version__, "scenarios": len(catalogue())})
                return
            self._static(path)
        except ScenarioError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything else to the client
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/run":
                scenario_id = str(payload.get("scenario", ""))
                scenario_by_id(scenario_id)  # validate early for a clean 404
                runs = _int(payload, "runs", 4000, 200, 60000)
                seed = _int(payload, "seed", 7, 0, 2**31 - 1)
                sensitivity = bool(payload.get("sensitivity", True))
                policy_json = json.dumps(payload.get("policy") or {}, sort_keys=True)
                try:
                    report = cached_report(scenario_id, runs, seed, policy_json, sensitivity)
                except PolicyError as exc:
                    self._error(400, f"policy: {exc}")
                    return
                self._json(report)
                return
            if path == "/api/compare":
                a = str(payload.get("a", ""))
                b = str(payload.get("b", ""))
                runs = _int(payload, "runs", 4000, 200, 60000)
                seed = _int(payload, "seed", 7, 0, 2**31 - 1)
                left = monte_carlo(scenario_by_id(a), runs=runs, seed=seed)
                right = monte_carlo(scenario_by_id(b), runs=runs, seed=seed)
                self._json(
                    {
                        "comparison": build_comparison(left, right),
                        "a": left.summary(),
                        "b": right.summary(),
                    }
                )
                return
            self._error(404, f"no route for POST {path}")
        except ValueError as exc:
            self._error(400, str(exc))
        except ScenarioError as exc:
            self._error(404, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"{type(exc).__name__}: {exc}")


def serve(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = False) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(BANNER)
    print(f"kchain {__version__} - analyst console")
    print(f"scenarios: {SCENARIO_DIR}  ({len(catalogue())} loaded)")
    print(f"listening on {url}")
    print("ctrl-c to stop")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
