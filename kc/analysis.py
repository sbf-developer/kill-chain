"""Derived analytics: distributions, sensitivity, policy trade-offs, insight text.

The two questions this file exists to answer:

  1. Where does the clock actually go?  (contribution + phase rollup + tornado)
  2. What does tightening the identification policy buy, and what does it cost?
     (threshold sweep, with the release operating characteristic)
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from typing import Any

from . import doctrine
from .audit import AuditLog, decision_records
from .doctrine import Policy
from .engine import (
    MonteCarlo,
    confidence_matrix,
    graph_of,
    monte_carlo,
    phase_table,
    quantile,
    survival,
    with_scaled_links,
    with_scaled_node,
)
from .model import PHASES, Scenario


def histogram(values: list[float], bins: int = 34, upper: float | None = None) -> list[dict[str, float]]:
    if not values:
        return []
    top = upper if upper is not None else quantile(values, 0.995)
    top = max(top, 1e-6)
    width = top / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int(value / width))
        counts[index] += 1
    return [
        {"x0": round(i * width, 1), "x1": round((i + 1) * width, 1), "count": c}
        for i, c in enumerate(counts)
    ]


def tornado(
    scenario: Scenario, runs: int = 1200, seed: int = 7, policy: Policy | None = None
) -> list[dict[str, Any]]:
    """P95 sensitivity: what happens if each activity gets twice as fast.

    Half-runs are used deliberately and the same seed is reused, so the deltas
    are comparable rather than being swamped by sampling noise.
    """
    base = monte_carlo(scenario, runs=runs, seed=seed, policy=policy)
    if not base.durations:
        return []
    base_p95 = base.pct(0.95)
    base_mean = sum(base.durations) / len(base.durations)
    rows: list[dict[str, Any]] = []

    def add(node_id: str, label: str, phase: str, kind: str, alt: MonteCarlo) -> None:
        if not alt.durations:
            return
        alt_p95 = alt.pct(0.95)
        alt_mean = sum(alt.durations) / len(alt.durations)
        rows.append(
            {
                "id": node_id,
                "label": label,
                "phase": phase,
                "kind": kind,
                "delta_p95_s": round(base_p95 - alt_p95, 1),
                "delta_mean_s": round(base_mean - alt_mean, 1),
                "alt_p95_s": round(alt_p95, 1),
            }
        )

    for node in scenario.nodes:
        alt = monte_carlo(
            with_scaled_node(scenario, node.id, 0.5), runs=runs, seed=seed, policy=policy
        )
        add(node.id, node.label, node.phase, node.kind, alt)

    add("__links__", "All handoffs (network)", "track", "network", monte_carlo(
        with_scaled_links(scenario, 0.5), runs=runs, seed=seed, policy=policy
    ))

    rows.sort(key=lambda r: r["delta_p95_s"], reverse=True)
    return rows


@lru_cache(maxsize=24)
def sweep(scenario: Scenario, runs: int = 1200, seed: int = 7, steps: int = 23) -> tuple[dict[str, Any], ...]:
    """PID threshold trade-off curve.

    For each candidate threshold this reports the simulated tail latency and the
    evidence-model release rates. `posterior_hostile` is the Bayesian share of
    releases that land on a genuinely hostile track given the scenario's prior --
    the number that actually matters, and the one nobody quotes in a slide.
    """
    matrix = confidence_matrix(scenario)
    points: list[dict[str, Any]] = []
    base_policy = scenario.policy
    for i in range(steps):
        threshold = round(0.50 + 0.49 * i / (steps - 1), 3)
        policy = replace(
            base_policy,
            pid_threshold=threshold,
            escalation_threshold=max(threshold, base_policy.escalation_threshold),
        )
        try:
            mc = monte_carlo(scenario, runs=runs, seed=seed, policy=policy)
        except Exception:  # pragma: no cover - a policy edit should never break the sweep
            continue
        release_floor = threshold
        p_hostile = survival(matrix["hostile"], release_floor) if matrix["hostile"] else 0.0
        p_benign = survival(matrix["benign"], release_floor) if matrix["benign"] else 0.0
        p_escalated = 0.0
        if (
            base_policy.escalation_threshold > threshold
            and base_policy.release_authority != "tactical"
            and matrix["hostile"]
        ):
            p_escalated = survival(matrix["hostile"], threshold) - survival(
                matrix["hostile"], base_policy.escalation_threshold
            )
        prior = min(max(scenario.evidence.prior_hostile, 1e-6), 1 - 1e-6) if scenario.evidence else 0.5
        numerator = p_hostile * prior
        denominator = numerator + p_benign * (1 - prior)
        posterior = numerator / denominator if denominator > 0 else 0.0
        points.append(
            {
                "threshold": threshold,
                "release_floor": round(release_floor, 3),
                "p95_s": round(mc.pct(0.95), 1) if mc.durations else None,
                "mean_s": round(sum(mc.durations) / len(mc.durations), 1) if mc.durations else None,
                "success_rate": round(mc.success_rate, 4),
                "abandoned_rate": round(mc.outcomes.get("abandoned", 0) / mc.runs, 4),
                "p_release_hostile": round(p_hostile, 4),
                "p_release_benign": round(p_benign, 4),
                "p_escalated": round(max(0.0, p_escalated), 4),
                "false_releases_per_1000": round(p_benign * 1000, 1),
                "posterior_hostile": round(posterior, 4),
            }
        )
    return tuple(points)


def compare(a: MonteCarlo, b: MonteCarlo) -> dict[str, Any]:
    def row(metric: str, va, vb, unit: str = "s", lower_is_better: bool = True) -> dict[str, Any]:
        delta = None if va is None or vb is None else round(vb - va, 1)
        return {
            "metric": metric,
            "a": va,
            "b": vb,
            "delta": delta,
            "unit": unit,
            "better": None if delta is None else ("b" if (delta < 0) == lower_is_better else "a"),
        }

    rows = [
        row("Median (p50)", round(a.pct(0.50), 1) if a.durations else None, round(b.pct(0.50), 1) if b.durations else None),
        row("p90", round(a.pct(0.90), 1) if a.durations else None, round(b.pct(0.90), 1) if b.durations else None),
        row("p95", round(a.pct(0.95), 1) if a.durations else None, round(b.pct(0.95), 1) if b.durations else None),
        row("p99", round(a.pct(0.99), 1) if a.durations else None, round(b.pct(0.99), 1) if b.durations else None),
        row("Chain success", round(a.success_rate, 3), round(b.success_rate, 3), unit="", lower_is_better=False),
        row("Mean re-cycles", round(a.recycles, 2), round(b.recycles, 2), unit="", lower_is_better=True),
    ]
    return {
        "a": {"id": a.scenario.id, "title": a.scenario.title},
        "b": {"id": b.scenario.id, "title": b.scenario.title},
        "rows": rows,
    }


def insights(mc: MonteCarlo, scenario: Scenario) -> list[str]:
    """Short analyst-style findings, generated from the run, not hard-coded."""
    if not mc.durations:
        blocked = mc.outcomes.most_common(1)[0] if mc.outcomes else ("none", 0)
        out = [
            f"No run reached the end of the chain: {blocked[1] / mc.runs:.0%} of attempts "
            f"ended '{blocked[0]}'."
        ]
        if mc.sample is not None and mc.sample.decisions:
            first = mc.sample.decisions[0]
            out.append(f"First gate: {first.rule} - {first.reason}")
        out.append(
            "Latency is not the constraint here. This is a feasibility finding: no sensor, "
            "network or tasking improvement changes the outcome."
        )
        return out

    out: list[str] = []

    summary = mc.summary()
    top = summary["contributions"][0]
    out.append(
        f"{top['label']} ({top['actor'] or 'unmanned'}) is the single largest consumer of the "
        f"cycle: {top['mean_s']:.0f}s per run, {top['share']:.0%} of the engaged timeline."
    )

    phases = phase_table(mc)
    dominant = max(phases, key=lambda p: p["seconds"])
    out.append(
        f"F2T2EA phase '{dominant['phase']}' dominates at {dominant['seconds']:.0f}s "
        f"({dominant['share']:.0%} of the cycle)."
    )

    p50, p95 = mc.pct(0.50), mc.pct(0.95)
    if p50 > 0 and p95 / p50 > 1.6:
        out.append(
            f"The distribution is tail-heavy: p95 is {p95 / p50:.1f}x the median, driven by "
            f"{mc.recycles:.2f} re-tasking cycles per run."
        )
    else:
        out.append(
            f"The distribution is well-behaved for a kill chain (p95/median = {p95 / p50:.1f}x, "
            f"{mc.recycles:.2f} re-cycles per run)."
        )

    total = summary["total_s"] or 1.0
    out.append(
        f"Handoffs and network transport account for {mc.link_s:.0f}s of the mean "
        f"({mc.link_s / total:.0%}); activity time accounts for the rest."
    )

    denied = mc.outcomes.get("denied", 0)
    abandoned = mc.outcomes.get("abandoned", 0)
    if denied or abandoned:
        out.append(
            f"{denied / mc.runs:.1%} of runs were denied by policy and {abandoned / mc.runs:.1%} "
            "were abandoned after exhausting the re-task allowance."
        )

    if scenario.evidence is not None and scenario.evidence.prior_hostile < 0.35:
        out.append(
            f"Prior hostile rate is low ({scenario.evidence.prior_hostile:.0%}), so a fixed PID "
            "threshold mostly filters benign tracks -- see the trade-off curve for the release "
            "rate on benign tracks at the current setting."
        )
    return out


def build_report(
    scenario: Scenario,
    runs: int = 4000,
    seed: int = 7,
    policy: Policy | None = None,
    sensitivity: bool = True,
) -> dict[str, Any]:
    from . import BANNER

    mc = monte_carlo(scenario, runs=runs, seed=seed, policy=policy)
    policy = policy or scenario.policy
    summary = mc.summary()
    contributions = {c["id"]: c["share"] for c in summary["contributions"]}
    layout = graph_of(scenario).layout(contributions)

    audit = AuditLog()
    if mc.sample is not None:
        audit.extend(
            decision_records(
                mc.sample.decisions,
                scenario_id=scenario.id,
                scenario_fingerprint=scenario.source_fingerprint,
                target=scenario.target.label,
                policy=policy.as_dict(),
                seed=seed,
            )
        )
    audit.append(
        {
            "type": "run_summary",
            "scenario": scenario.id,
            "scenario_fingerprint": scenario.source_fingerprint,
            "runs": runs,
            "seed": seed,
            "engaged": mc.engaged,
            "denied": mc.outcomes.get("denied", 0),
            "abandoned": mc.outcomes.get("abandoned", 0),
            "p95_s": summary["p95_s"],
            "synthetic": True,
        }
    )

    report: dict[str, Any] = {
        "banner": BANNER,
        "scenario": {
            **scenario.as_summary(),
            "policy": policy.as_dict(),
            "policy_rules": doctrine.describe_policy(policy),
            "evidence": scenario.evidence.as_dict() if scenario.evidence else None,
            "notes": scenario.notes,
            "fingerprint": scenario.source_fingerprint,
            "origin": {"lat": scenario.origin[0], "lon": scenario.origin[1]},
        },
        "nodes": [n.as_dict() for n in scenario.nodes],
        "edges": [e.as_dict() for e in scenario.edges],
        "layout": layout,
        "metrics": summary,
        "histogram": histogram(mc.durations),
        "phases": phase_table(mc),
        "insights": insights(mc, scenario),
        "sample_run": mc.sample.as_dict() if mc.sample else None,
        "tradeoff": list(sweep(scenario)),
        "tornado": tornado(scenario, policy=policy) if sensitivity else [],
        "audit": {
            "records": audit.records,
            "verification": audit.verify().as_dict(),
        },
        "meta": {
            "runs": runs,
            "seed": seed,
            "phase_order": list(PHASES),
            "model": "stochastic activity network, Monte Carlo, F2T2EA phases",
        },
    }
    return report
