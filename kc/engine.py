"""Monte Carlo execution of a kill chain.

Each simulated run walks the graph from the entry node to a terminal state,
sampling a duration at every activity and a latency at every handoff. When a
decision node rejects, the chain follows the recycle edge back to the sensor:
that re-entry is re-sampled, so a re-task costs real time and a chain with a
weak classifier has a long right tail even when its median looks fine.

Three outcomes are possible: `engaged` (released and assessed), `denied`
(policy blocked it), and `abandoned` (the recycle cap was hit). Latency
statistics are computed over `engaged` runs only -- a fast denial is not a fast
kill chain, and averaging the two together is how people fool themselves.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Iterable

from . import doctrine
from .doctrine import Policy
from .model import Edge, Graph, Node, Scenario

MAX_STEPS = 800

# Only a representative run is ever needed for the timeline, so the per-run
# objects are capped rather than retained for every trial. Without this, a
# 60,000-run request holds 60,000 full timelines in memory.
SAMPLE_POOL = 4000


class SimulationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    kind: str  # activity | link | gate
    node_id: str | None
    label: str
    actor: str
    start: float
    end: float
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "node": self.node_id,
            "label": self.label,
            "actor": self.actor,
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "duration": round(self.end - self.start, 2),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DecisionRecord:
    t: float
    node_id: str
    confidence: float
    outcome: str
    rule: str
    reason: str
    authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": round(self.t, 2),
            "node": self.node_id,
            "confidence": round(self.confidence, 4),
            "outcome": self.outcome,
            "rule": self.rule,
            "reason": self.reason,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class RunResult:
    total_s: float
    outcome: str
    terminal: str
    visits: dict[str, int]
    time_in: dict[str, float]
    link_s: float
    segments: tuple[Segment, ...]
    decisions: tuple[DecisionRecord, ...]
    recycles: int
    confidences: tuple[float, ...]

    @property
    def engaged(self) -> bool:
        return self.outcome == "engaged"

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_s": round(self.total_s, 1),
            "outcome": self.outcome,
            "terminal": self.terminal,
            "recycles": self.recycles,
            "link_s": round(self.link_s, 1),
            "segments": [s.as_dict() for s in self.segments],
            "decisions": [d.as_dict() for d in self.decisions],
        }


@lru_cache(maxsize=64)
def graph_of(scenario: Scenario) -> Graph:
    return Graph(scenario)


def _pick_weighted(edges: list[Edge], rng: random.Random) -> Edge:
    r = rng.random()
    cumulative = 0.0
    for edge in edges:
        cumulative += edge.p if edge.p is not None else 1.0
        if r <= cumulative:
            return edge
    return edges[-1]


def _pick_label(edges: list[Edge], label: str, node: Node) -> Edge:
    for edge in edges:
        if edge.label == label:
            return edge
    raise SimulationError(
        f"node '{node.id}' has no edge for gate outcome '{label}'; "
        f"add one so the policy can be executed"
    )


def simulate(
    scenario: Scenario,
    rng: random.Random,
    policy: Policy | None = None,
    max_steps: int = MAX_STEPS,
) -> RunResult:
    """Execute one stochastic traversal of the chain."""
    graph = graph_of(scenario)
    policy = policy or scenario.policy
    evidence = scenario.evidence
    if evidence is None:
        raise SimulationError("scenario has no evidence model")

    node: Node = graph.start
    t = 0.0
    steps = 0
    visits: Counter[str] = Counter()
    time_in: Counter[str] = Counter()
    segments: list[Segment] = []
    decisions: list[DecisionRecord] = []
    confidences: list[float] = []
    recycles = 0
    link_s = 0.0
    confidence: float | None = None
    terminal = "END"
    abandoned = False

    while True:
        steps += 1
        if steps > max_steps:
            abandoned = True
            terminal = "END_TIMEOUT"
            break

        duration = node.duration.sample(rng)
        start = t
        t += duration
        visits[node.id] += 1
        time_in[node.id] += duration
        segments.append(
            Segment("activity", node.id, node.label, node.actor, start, t, node.phase)
        )

        if node.kind == "decision":
            if confidence is None:
                confidence = evidence.sample(rng, scenario.target.classification)
                confidences.append(confidence)
            decision = doctrine.decide(
                confidence, policy, scenario.target.classification, node.echelon
            )
            decisions.append(
                DecisionRecord(
                    t=t,
                    node_id=node.id,
                    confidence=confidence,
                    outcome=decision.outcome,
                    rule=decision.rule,
                    reason=decision.reason,
                    authority=decision.authority,
                )
            )
            segments.append(
                Segment(
                    "gate",
                    node.id,
                    f"{decision.outcome.upper()}  p={confidence:.2f}",
                    f"{decision.authority} authority",
                    t,
                    t,
                    decision.reason,
                )
            )
            edge = _pick_label(graph.out[node.id], decision.outcome, node)
        else:
            outs = graph.out[node.id]
            edge = outs[0] if len(outs) == 1 and outs[0].p is None else _pick_weighted(outs, rng)

        if edge.latency is not None:
            link = edge.latency.sample(rng)
            if link > 0:
                segments.append(
                    Segment(
                        "link", node.id, edge.note or f"handoff to {edge.to_id}",
                        "network", t, t + link,
                    )
                )
                link_s += link
                t += link

        if edge.label == "reject":
            recycles += 1
            confidence = None  # re-tasking produces a fresh observation
            if recycles > policy.max_recycles:
                abandoned = True
                terminal = "END_ABANDONED"
                break

        if edge.to_id not in graph.nodes:
            terminal = edge.to_id
            break

        node = graph.nodes[edge.to_id]

    outcome = "denied" if "DENIED" in terminal else ("abandoned" if abandoned else "engaged")
    return RunResult(
        total_s=t,
        outcome=outcome,
        terminal=terminal,
        visits=dict(visits),
        time_in=dict(time_in),
        link_s=link_s,
        segments=tuple(segments),
        decisions=tuple(decisions),
        recycles=recycles,
        confidences=tuple(confidences),
    )


def quantile(values: Iterable[float], q: float) -> float:
    """Linear-interpolated quantile. `values` must be sorted."""
    data = list(values)
    if not data:
        return float("nan")
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (pos - low)


@dataclass
class MonteCarlo:
    scenario: Scenario
    policy: Policy
    runs: int
    seed: int
    durations: list[float]  # engaged runs only, sorted
    outcomes: Counter[str]
    time_in: dict[str, float]  # mean seconds per engaged run
    visits: dict[str, float]  # mean visits per engaged run
    link_s: float
    recycles: float
    sample: RunResult | None
    confidences: list[float]
    phase_seconds: dict[str, float]

    @property
    def engaged(self) -> int:
        return self.outcomes.get("engaged", 0)

    @property
    def success_rate(self) -> float:
        return self.engaged / self.runs if self.runs else 0.0

    def pct(self, q: float) -> float:
        return quantile(self.durations, q)

    def summary(self) -> dict[str, Any]:
        total = sum(self.time_in.values()) + self.link_s
        contrib = [
            {
                "id": node.id,
                "label": node.label,
                "actor": node.actor,
                "phase": node.phase,
                "kind": node.kind,
                "mean_s": round(self.time_in.get(node.id, 0.0), 1),
                "visits": round(self.visits.get(node.id, 0.0), 2),
                "share": round(self.time_in.get(node.id, 0.0) / total, 4) if total else 0.0,
            }
            for node in self.scenario.nodes
        ]
        contrib.sort(key=lambda c: c["mean_s"], reverse=True)

        def pct(q: float) -> float | None:
            return round(self.pct(q), 1) if self.durations else None

        return {
            "runs": self.runs,
            "seed": self.seed,
            "engaged": self.engaged,
            "outcomes": dict(self.outcomes),
            "success_rate": round(self.success_rate, 4),
            "mean_s": round(sum(self.durations) / len(self.durations), 1) if self.durations else None,
            "p50_s": pct(0.50),
            "p90_s": pct(0.90),
            "p95_s": pct(0.95),
            "p99_s": pct(0.99),
            "max_s": round(self.durations[-1], 1) if self.durations else None,
            "link_s": round(self.link_s, 1),
            "recycles_mean": round(self.recycles, 2),
            "phase_seconds": {k: round(v, 1) for k, v in self.phase_seconds.items()},
            "contributions": contrib,
            "total_s": round(total, 1),
        }


def monte_carlo(
    scenario: Scenario,
    runs: int = 4000,
    seed: int = 7,
    policy: Policy | None = None,
    max_steps: int = MAX_STEPS,
) -> MonteCarlo:
    if runs <= 0:
        raise ValueError("runs must be positive")
    policy = policy or scenario.policy
    rng = random.Random(seed)

    outcomes: Counter[str] = Counter()
    durations: list[float] = []
    time_in: Counter[str] = Counter()
    visits: Counter[str] = Counter()
    phase: Counter[str] = Counter()
    confidences: list[float] = []
    link_total = 0.0
    recycle_total = 0
    engaged = 0
    samples: list[tuple[float, RunResult]] = []
    everything: list[tuple[float, RunResult]] = []

    phases = {n.id: n.phase for n in scenario.nodes}

    for _ in range(runs):
        result = simulate(scenario, rng, policy, max_steps=max_steps)
        outcomes[result.outcome] += 1
        confidences.extend(result.confidences)
        recycle_total += result.recycles
        if len(everything) < SAMPLE_POOL:
            everything.append((result.total_s, result))
        if not result.engaged:
            continue
        engaged += 1
        durations.append(result.total_s)
        link_total += result.link_s
        for node_id, seconds in result.time_in.items():
            time_in[node_id] += seconds
            phase[phases.get(node_id, "track")] += seconds
        for node_id, count in result.visits.items():
            visits[node_id] += count
        if len(samples) < SAMPLE_POOL:
            samples.append((result.total_s, result))

    durations.sort()
    denominator = engaged or 1
    pool = samples or everything
    sample: RunResult | None = None
    if pool:
        pool.sort(key=lambda item: item[0])
        sample = pool[len(pool) // 2][1]

    return MonteCarlo(
        scenario=scenario,
        policy=policy,
        runs=runs,
        seed=seed,
        durations=durations,
        outcomes=outcomes,
        time_in={k: v / denominator for k, v in time_in.items()},
        visits={k: v / denominator for k, v in visits.items()},
        link_s=link_total / denominator,
        recycles=recycle_total / runs,
        sample=sample,
        confidences=confidences,
        phase_seconds={p: phase.get(p, 0.0) / denominator for p in ("find", "fix", "track", "target", "engage", "assess")},
    )


def with_scaled_node(scenario: Scenario, node_id: str, factor: float) -> Scenario:
    """Copy of the scenario with one activity's durations multiplied by `factor`."""
    nodes = tuple(
        replace(node, duration=node.duration.scaled(factor)) if node.id == node_id else node
        for node in scenario.nodes
    )
    return replace(scenario, nodes=nodes)


def with_scaled_links(scenario: Scenario, factor: float) -> Scenario:
    edges = tuple(
        replace(edge, latency=edge.latency.scaled(factor)) if edge.latency else edge
        for edge in scenario.edges
    )
    return replace(scenario, edges=edges)


def confidence_matrix(
    scenario: Scenario, samples: int = 20000, seed: int = 11
) -> dict[str, list[float]]:
    """Sorted confidence draws under each ground truth, for the threshold sweep."""
    evidence = scenario.evidence
    if evidence is None:
        return {"hostile": [], "benign": []}
    rng = random.Random(seed)
    hostile = sorted(evidence.sample(rng, "hostile") for _ in range(samples))
    benign = sorted(evidence.sample(rng, "benign") for _ in range(samples))
    return {"hostile": hostile, "benign": benign}


def survival(sorted_values: list[float], threshold: float) -> float:
    """P(X >= threshold) for an already-sorted sample."""
    if not sorted_values:
        return float("nan")
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < threshold:
            lo = mid + 1
        else:
            hi = mid
    return (len(sorted_values) - lo) / len(sorted_values)


def phase_table(mc: MonteCarlo) -> list[dict[str, Any]]:
    total = sum(mc.phase_seconds.values()) or 1.0
    return [
        {"phase": name, "seconds": round(seconds, 1), "share": round(seconds / total, 4)}
        for name, seconds in mc.phase_seconds.items()
    ]
