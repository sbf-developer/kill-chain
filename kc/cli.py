"""Command line interface.

    python -m kc list
    python -m kc check
    python -m kc run 01-legacy-sequential --runs 20000
    python -m kc compare 01-legacy-sequential 02-fused-killweb
    python -m kc sweep 04-tight-roe
    python -m kc cot 02-fused-killweb --out track.cot
    python -m kc audit 04-tight-roe
    python -m kc serve --port 8787
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import BANNER, __version__
from . import cot as cot_mod
from .analysis import build_report, compare, sweep
from .audit import AuditLog, decision_records, load_jsonl, verify_records
from .engine import monte_carlo, simulate
from .model import Scenario, ScenarioError, load_all, load_scenario

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios"


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #


def hms(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.2f}h"


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], right: Sequence[int] = ()) -> str:
    rows = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    head = "  ".join(
        h.rjust(widths[i]) if i in right else h.ljust(widths[i]) for i, h in enumerate(headers)
    )
    lines.append(head)
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(
            "  ".join(
                cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
                for i, cell in enumerate(row)
            )
        )
    return "\n".join(lines)


def rule(title: str) -> str:
    return f"\n\033[1m{title}\033[0m" if sys.stdout.isatty() else f"\n{title}"


def load_any(identifier: str) -> Scenario:
    candidate = Path(identifier)
    if candidate.suffix == ".json" or candidate.exists():
        return load_scenario(candidate)
    for scenario in load_all(SCENARIO_DIR):
        if scenario.id == identifier or scenario.id.startswith(identifier):
            return scenario
    known = ", ".join(s.id for s in load_all(SCENARIO_DIR))
    raise ScenarioError(f"no scenario matching '{identifier}'. Known: {known}")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_list(args: argparse.Namespace) -> int:
    print(BANNER)
    print(f"kchain {__version__} - {SCENARIO_DIR}\n")
    rows = []
    for scenario in load_all(SCENARIO_DIR):
        policy = scenario.policy
        rows.append(
            [
                scenario.id,
                scenario.title,
                len(scenario.nodes),
                f"{policy.pid_threshold:.2f}",
                policy.release_authority,
                policy.cde_level,
                ",".join(scenario.tags[:3]),
            ]
        )
    print(table(["id", "title", "nodes", "pid", "authority", "cde", "tags"], rows, right=(2, 3)))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    failures = 0
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        try:
            scenario = load_scenario(path)
        except ScenarioError as exc:
            failures += 1
            print(f"FAIL {path.name}: {exc}")
            continue
        # A short smoke run catches routing holes the structural checks cannot.
        try:
            result = simulate(scenario, __import__("random").Random(1))
        except Exception as exc:  # noqa: BLE001 - report and continue
            failures += 1
            print(f"FAIL {path.name}: simulation error: {exc}")
            continue
        print(
            f"ok   {path.name:28s} {len(scenario.nodes):2d} nodes  "
            f"smoke run {result.outcome:9s} {hms(result.total_s):>8s}"
        )
    print(f"\n{len(list(SCENARIO_DIR.glob('*.json'))) - failures} scenario(s) valid, {failures} failure(s)")
    return 1 if failures else 0


def cmd_run(args: argparse.Namespace) -> int:
    scenario = load_any(args.scenario)
    report = build_report(
        scenario, runs=args.runs, seed=args.seed, sensitivity=not args.no_sensitivity
    )
    metrics = report["metrics"]
    print(BANNER)
    print(f"{scenario.id}  {scenario.title}")
    print(f"{args.runs:,} runs, seed {args.seed}, target {scenario.target.label}\n")

    print(
        table(
            ["outcome", "runs", "share"],
            [
                [name, f"{count:,}", f"{count / args.runs:.1%}"]
                for name, count in sorted(metrics["outcomes"].items())
            ],
            right=(1, 2),
        )
    )

    if metrics["engaged"]:
        print(
            rule("Latency (engaged runs)")
            + "\n"
            + table(
                ["statistic", "time", "seconds"],
                [
                    ["mean", hms(metrics["mean_s"]), f"{metrics['mean_s']:.0f}"],
                    ["p50", hms(metrics["p50_s"]), f"{metrics['p50_s']:.0f}"],
                    ["p90", hms(metrics["p90_s"]), f"{metrics['p90_s']:.0f}"],
                    ["p95", hms(metrics["p95_s"]), f"{metrics['p95_s']:.0f}"],
                    ["p99", hms(metrics["p99_s"]), f"{metrics['p99_s']:.0f}"],
                    ["max", hms(metrics["max_s"]), f"{metrics['max_s']:.0f}"],
                ],
                right=(1, 2),
            )
        )
        print(f"\nmean re-cycles per attempted engagement: {metrics['recycles_mean']:.2f}")

        print(rule("Where the clock goes"))
        rows = [
            [
                c["phase"],
                c["label"],
                f"{c['mean_s']:.0f}s",
                f"{c['share']:.1%}",
                f"{c['visits']:.2f}",
            ]
            for c in metrics["contributions"]
            if c["mean_s"] > 0
        ]
        print(table(["phase", "activity", "mean", "share", "visits"], rows, right=(2, 3, 4)))

        print(rule("F2T2EA phase totals"))
        print(
            table(
                ["phase", "seconds", "share"],
                [[p["phase"], f"{p['seconds']:.0f}", f"{p['share']:.1%}"] for p in report["phases"]],
                right=(1, 2),
            )
        )

    print(rule("Findings"))
    for line in report["insights"]:
        print(f"  - {line}")

    print(rule("Policy in force"))
    for line in report["scenario"]["policy_rules"]:
        print(f"  - {line}")

    if report["tornado"]:
        print(rule("If this activity were twice as fast (p95 effect)"))
        rows = [
            [row["label"], f"{row['delta_p95_s']:+.1f}s", f"{row['delta_mean_s']:+.1f}s"]
            for row in report["tornado"][:6]
        ]
        print(table(["activity", "p95 change", "mean change"], rows, right=(1, 2)))

    verification = report["audit"]["verification"]
    print(
        rule("Audit")
        + f"\n{verification['records']} decision record(s), "
        + ("chain intact" if verification["ok"] else f"CHAIN BROKEN at {verification['first_bad_seq']}")
    )

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    left = monte_carlo(load_any(args.a), runs=args.runs, seed=args.seed)
    right = monte_carlo(load_any(args.b), runs=args.runs, seed=args.seed)
    diff = compare(left, right)
    print(BANNER)
    print(f"A: {diff['a']['title']}\nB: {diff['b']['title']}\n{args.runs:,} runs each, seed {args.seed}\n")
    rows = []
    for row in diff["rows"]:
        def show(value: Any) -> str:
            if value is None:
                return "-"
            return hms(value) if row["unit"] == "s" else f"{value:g}"

        if row["delta"] is None:
            delta = "-"
        elif row["unit"] == "s":
            delta = f"{'-' if row['delta'] < 0 else '+'}{hms(abs(row['delta']))}"
        else:
            delta = f"{row['delta']:+.3f}"
        rows.append([row["metric"], show(row["a"]), show(row["b"]), delta, row["better"] or "-"])
    print(table(["metric", "A", "B", "B-A", "faster"], rows, right=(1, 2, 3)))
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    scenario = load_any(args.scenario)
    print(BANNER)
    print(f"{scenario.id}  PID threshold trade-off (evidence model + full simulation)\n")
    rows = []
    for point in sweep(scenario):
        rows.append(
            [
                f"{point['threshold']:.2f}",
                hms(point["p95_s"]),
                f"{point['success_rate']:.1%}",
                f"{point['abandoned_rate']:.1%}",
                f"{point['p_escalated']:.0%}",
                f"{point['p_release_hostile']:.2f}",
                f"{point['p_release_benign']:.3f}",
                f"{point['posterior_hostile']:.2f}",
            ]
        )
    print(
        table(
            ["pid", "p95", "success", "abandon", "escalate", "P(rel|hostile)", "P(rel|benign)", "P(hostile|rel)"],
            rows,
            right=(1, 2, 3, 4, 5, 6, 7),
        )
    )
    print(
        "\nP(hostile|rel) is Bayesian, using the scenario prior. Half of a PID problem is the "
        "prior, not the sensor."
    )
    return 0


def cmd_cot(args: argparse.Namespace) -> int:
    scenario = load_any(args.scenario)
    result = simulate(scenario, __import__("random").Random(args.seed))
    xml = cot_mod.scenario_cot(scenario, result)
    if args.out:
        Path(args.out).write_text(xml, encoding="utf-8")
        print(f"wrote {len(xml.splitlines())} lines to {args.out}")
    else:
        print(xml)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    scenario = load_any(args.scenario)
    result = simulate(scenario, __import__("random").Random(args.seed))
    log = AuditLog()
    log.extend(
        decision_records(
            result.decisions,
            scenario_id=scenario.id,
            scenario_fingerprint=scenario.source_fingerprint,
            target=scenario.target.label,
            policy=scenario.policy.as_dict(),
            seed=args.seed,
        )
    )
    print(BANNER)
    for record in log.records:
        print(
            f"#{record['seq']}  {record['ts']}  conf={record['confidence']:.2f}  "
            f"{record['outcome']:9s} {record['rule']:24s} {record['authority']}"
        )
    verification = log.verify()
    print(f"\nverify: {'intact' if verification.ok else 'BROKEN'} ({verification.records} records)")

    if args.out:
        path = log.save(args.out)
        print(f"written to {path}")
        reloaded = verify_records(load_jsonl(Path(path).read_text(encoding="utf-8")))
        print(f"reload+verify: {'intact' if reloaded.ok else 'BROKEN'}")

    if args.tamper_demo:
        records = json.loads(json.dumps(log.records))
        if len(records) >= 2:
            records[0]["outcome"] = "approve" if records[0]["outcome"] != "approve" else "deny"
            check = verify_records(records)
            print(
                f"tamper demo: editing record #1 -> verify "
                f"{'failed as expected' if not check.ok else 'DID NOT DETECT'} "
                f"(seq {check.first_bad_seq}: {check.detail})"
            )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .api import serve

    serve(host=args.host, port=args.port, open_browser=args.open)
    return 0


# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kc",
        description="Kill-chain latency and authority simulator (synthetic data only).",
    )
    parser.add_argument("--version", action="version", version=f"kchain {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list scenarios")
    p_list.set_defaults(func=cmd_list)

    p_check = sub.add_parser("check", help="validate every scenario")
    p_check.set_defaults(func=cmd_check)

    p_run = sub.add_parser("run", help="run one scenario")
    p_run.add_argument("scenario")
    p_run.add_argument("--runs", type=int, default=20000)
    p_run.add_argument("--seed", type=int, default=7)
    p_run.add_argument("--no-sensitivity", action="store_true", help="skip the p95 tornado")
    p_run.add_argument("--json", help="write the full report as JSON")
    p_run.set_defaults(func=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare two scenarios")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    p_cmp.add_argument("--runs", type=int, default=20000)
    p_cmp.add_argument("--seed", type=int, default=7)
    p_cmp.set_defaults(func=cmd_compare)

    p_sweep = sub.add_parser("sweep", help="PID threshold trade-off curve")
    p_sweep.add_argument("scenario")
    p_sweep.set_defaults(func=cmd_sweep)

    p_cot = sub.add_parser("cot", help="emit Cursor-on-Target XML for one simulated run")
    p_cot.add_argument("scenario")
    p_cot.add_argument("--seed", type=int, default=7)
    p_cot.add_argument("--out")
    p_cot.set_defaults(func=cmd_cot)

    p_audit = sub.add_parser("audit", help="write and verify a decision log")
    p_audit.add_argument("scenario")
    p_audit.add_argument("--seed", type=int, default=7)
    p_audit.add_argument("--out")
    p_audit.add_argument("--tamper-demo", action="store_true")
    p_audit.set_defaults(func=cmd_audit)

    p_serve = sub.add_parser("serve", help="start the analyst console")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--open", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
