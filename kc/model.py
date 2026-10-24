"""Scenario schema, validation and graph construction.

A scenario is a directed graph of typed activities. Each node does work and
takes time; each edge is a handoff and takes time. Cycles are legal and are the
interesting part: a failed identification sends the chain back to the sensor,
which is why real kill chains have fat tails.

Phases use the US joint F2T2EA vocabulary -- find, fix, track, target, engage,
assess -- so the model lines up with doctrine rather than with a UI designer's
imagination.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import doctrine
from .distributions import Distribution, SpecError, parse as parse_dist

PHASES = ("find", "fix", "track", "target", "engage", "assess")
NODE_KINDS = {
    "sensor",
    "network",
    "fusion",
    "analysis",
    "decision",
    "tasking",
    "effector",
    "assessment",
}
DECISION_LABELS = {"approve", "escalate", "reject", "deny"}


class ScenarioError(ValueError):
    """Raised for anything wrong with a scenario file. Message is user-facing."""


@dataclass(frozen=True)
class Target:
    label: str
    kind: str = "unknown"
    classification: str = "unknown"  # unknown | hostile | benign
    mobility: str = "unknown"
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "classification": self.classification,
            "mobility": self.mobility,
            "description": self.description,
        }


@dataclass(frozen=True)
class Evidence:
    """How good the classification evidence is, split by ground truth.

    Both distributions are the confidence a classifier would report on the same
    track, once if the track really is hostile and once if it is benign. They
    overlap -- that overlap is the entire problem. Everything in this model is
    synthetic.
    """

    prior_hostile: float
    beta_hostile: Distribution
    beta_benign: Distribution

    def sample(self, rng, classification: str) -> float:
        dist = self.beta_hostile if classification == "hostile" else self.beta_benign
        return max(0.0, min(1.0, dist.sample(rng)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "prior_hostile": self.prior_hostile,
            "beta_hostile": self.beta_hostile.describe(),
            "beta_benign": self.beta_benign.describe(),
        }


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    label: str
    actor: str
    phase: str
    duration: Distribution
    echelon: str = "tactical"
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "actor": self.actor,
            "phase": self.phase,
            "echelon": self.echelon,
            "duration": self.duration.describe(),
            "mean_s": round(self.duration.mean(), 1),
            "note": self.note,
        }


@dataclass(frozen=True)
class Edge:
    from_id: str
    to_id: str
    label: str = "flow"
    p: float | None = None
    latency: Distribution | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "label": self.label,
            "p": self.p,
            "latency": self.latency.describe() if self.latency else "0s",
            "mean_link_s": round(self.latency.mean(), 1) if self.latency else 0.0,
            "note": self.note,
        }


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    target: Target
    policy: doctrine.Policy
    evidence: Evidence | None
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    tags: tuple[str, ...] = ()
    origin: tuple[float, float] = (0.0, 0.0)
    notes: str = ""
    source_fingerprint: str = ""
    path: str = ""

    def as_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "nodes": len(self.nodes),
            "phase_span": sorted({n.phase for n in self.nodes}, key=PHASES.index),
            "target": self.target.as_dict(),
            "policy": self.policy.as_dict(),
        }


class Graph:
    """Adjacency, start node and display layout for one scenario."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.nodes = {n.id: n for n in scenario.nodes}
        self.out: dict[str, list[Edge]] = {n.id: [] for n in scenario.nodes}
        self.inbound: dict[str, list[Edge]] = {n.id: [] for n in scenario.nodes}
        self.forward_inbound: dict[str, list[Edge]] = {n.id: [] for n in scenario.nodes}
        for edge in scenario.edges:
            self.out[edge.from_id].append(edge)
            if edge.to_id in self.nodes:
                self.inbound[edge.to_id].append(edge)
                if edge.label != "reject":
                    self.forward_inbound[edge.to_id].append(edge)

        starts = [n for n in scenario.nodes if not self.forward_inbound[n.id]]
        if len(starts) != 1:
            raise ScenarioError(
                "chain must have exactly one entry node; found "
                + (", ".join(n.id for n in starts) or "none")
                + " (recycle edges labelled 'reject' do not count as entry)"
            )
        self.start = starts[0]
        self.layers = self._layers()

    def _layers(self) -> dict[str, int]:
        """Longest-path layering over forward edges (recycle edges excluded)."""
        layer = {n.id: 0 for n in self.scenario.nodes}
        for _ in range(len(self.scenario.nodes)):
            changed = False
            for edge in self.scenario.edges:
                if edge.label in ("reject", "deny"):
                    continue
                target = edge.to_id
                if target not in layer:
                    continue
                candidate = layer[edge.from_id] + 1
                if candidate > layer[target]:
                    layer[target] = candidate
                    changed = True
            if not changed:
                break
        return layer

    def order(self) -> list[str]:
        """Stable ordering by layer, then declaration order."""
        index = {n.id: i for i, n in enumerate(self.scenario.nodes)}
        return sorted(self.nodes, key=lambda nid: (self.layers[nid], index[nid]))

    def reachable(self) -> set[str]:
        seen: set[str] = set()
        queue: deque[str] = deque([self.start.id])
        while queue:
            node_id = queue.popleft()
            if node_id in seen:
                continue
            seen.add(node_id)
            for edge in self.out[node_id]:
                if edge.to_id in self.nodes:
                    queue.append(edge.to_id)
        return seen

    def layout(self, contributions: dict[str, float] | None = None) -> dict[str, Any]:
        """Node positions and edge routing hints for the console."""
        contributions = contributions or {}
        index = {n.id: i for i, n in enumerate(self.scenario.nodes)}
        by_layer: dict[int, list[str]] = {}
        for nid in self.nodes:
            by_layer.setdefault(self.layers[nid], []).append(nid)
        for ids in by_layer.values():
            ids.sort(key=lambda n: index[n])
        positions: dict[str, tuple[int, int]] = {}
        for layer, ids in by_layer.items():
            for row, nid in enumerate(ids):
                positions[nid] = (layer, row)
        height = max(len(ids) for ids in by_layer.values())

        nodes = []
        for nid, (layer, row) in positions.items():
            node = self.nodes[nid]
            nodes.append(
                {
                    "id": nid,
                    "layer": layer,
                    "row": row,
                    "rows_in_layer": len(by_layer[layer]),
                    "label": node.label,
                    "actor": node.actor,
                    "kind": node.kind,
                    "phase": node.phase,
                    "mean_s": round(node.duration.mean(), 1),
                    "share": round(contributions.get(nid, 0.0), 4),
                }
            )

        edges = []
        for edge in self.scenario.edges:
            source = positions.get(edge.from_id)
            target = positions.get(edge.to_id)
            edges.append(
                {
                    "from": edge.from_id,
                    "to": edge.to_id,
                    "label": edge.label,
                    "p": edge.p,
                    "mean_link_s": round(edge.latency.mean(), 1) if edge.latency else 0.0,
                    "recycle": edge.label == "reject",
                    "terminal": edge.to_id not in self.nodes,
                    "source_pos": list(source) if source else None,
                    "target_pos": list(target) if target else None,
                }
            )

        return {"nodes": nodes, "edges": edges, "layers": max(self.layers.values()) + 1, "rows": height}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _require(raw: dict[str, Any], key: str, where: str) -> Any:
    if key not in raw:
        raise ScenarioError(f"{where}: missing required key '{key}'")
    return raw[key]


def _str(raw: dict[str, Any], key: str, where: str, default: str | None = None) -> str:
    if key not in raw:
        if default is None:
            raise ScenarioError(f"{where}: missing required key '{key}'")
        return default
    value = raw[key]
    if not isinstance(value, str):
        raise ScenarioError(f"{where}: '{key}' must be a string")
    return value


def _parse_node(raw: Any, index: int) -> Node:
    where = f"nodes[{index}]"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: must be an object")
    node_id = _str(raw, "id", where)
    where = f"node '{node_id}'"
    kind = _str(raw, "kind", where)
    if kind not in NODE_KINDS:
        raise ScenarioError(f"{where}: kind must be one of {sorted(NODE_KINDS)}")
    phase = _str(raw, "phase", where, default="track")
    if phase not in PHASES:
        raise ScenarioError(f"{where}: phase must be one of {list(PHASES)}")
    echelon = _str(raw, "echelon", where, default="tactical")
    if echelon not in doctrine.AUTHORITY_ORDER:
        raise ScenarioError(
            f"{where}: echelon must be one of {sorted(doctrine.AUTHORITY_ORDER)}"
        )
    try:
        duration = parse_dist(_require(raw, "duration", where), f"{where}.duration")
    except SpecError as exc:
        raise ScenarioError(str(exc)) from exc
    return Node(
        id=node_id,
        kind=kind,
        label=_str(raw, "label", where),
        actor=_str(raw, "actor", where, default=""),
        phase=phase,
        duration=duration,
        echelon=echelon,
        note=_str(raw, "note", where, default=""),
    )


def _parse_edge(raw: Any, index: int, node_ids: set[str]) -> Edge:
    where = f"edges[{index}]"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: must be an object")
    from_id = _str(raw, "from", where)
    to_id = _str(raw, "to", where)
    if from_id not in node_ids:
        raise ScenarioError(f"{where}: 'from' references unknown node '{from_id}'")
    if to_id not in node_ids and not to_id.startswith("END"):
        raise ScenarioError(
            f"{where}: 'to' must be a known node id or a terminal 'END*', got '{to_id}'"
        )
    label = _str(raw, "label", where, default="flow")
    if label not in DECISION_LABELS and label != "flow":
        raise ScenarioError(
            f"{where}: label must be 'flow' or one of {sorted(DECISION_LABELS)}"
        )
    p = raw.get("p")
    if p is not None:
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            raise ScenarioError(f"{where}: 'p' must be a number")
        if not 0.0 < float(p) <= 1.0:
            raise ScenarioError(f"{where}: 'p' must be in (0, 1]")
        p = float(p)
    latency = None
    if "latency" in raw:
        try:
            latency = parse_dist(raw["latency"], f"{where}.latency")
        except SpecError as exc:
            raise ScenarioError(str(exc)) from exc
    return Edge(
        from_id=from_id,
        to_id=to_id,
        label=label,
        p=p,
        latency=latency,
        note=_str(raw, "note", where, default=""),
    )


def _parse_evidence(raw: Any) -> Evidence:
    if not isinstance(raw, dict):
        raise ScenarioError("evidence: must be an object")
    where = "evidence"
    prior = raw.get("prior_hostile", 0.5)
    if not isinstance(prior, (int, float)) or not 0.0 <= float(prior) <= 1.0:
        raise ScenarioError(f"{where}: 'prior_hostile' must be within [0, 1]")
    try:
        hostile = parse_dist(_require(raw, "beta_hostile", where), f"{where}.beta_hostile")
        benign = parse_dist(_require(raw, "beta_benign", where), f"{where}.beta_benign")
    except SpecError as exc:
        raise ScenarioError(str(exc)) from exc
    return Evidence(prior_hostile=float(prior), beta_hostile=hostile, beta_benign=benign)


def _validate(scenario: Scenario, graph: Graph) -> None:
    ids = [n.id for n in scenario.nodes]
    if len(set(ids)) != len(ids):
        raise ScenarioError("node ids must be unique")

    # Terminal reachability: every path must be able to end.
    terminals = [e for e in scenario.edges if e.to_id.startswith("END")]
    if not terminals:
        raise ScenarioError("chain has no terminal edge (an edge whose 'to' starts with 'END')")

    unreachable = graph.nodes.keys() - graph.reachable()
    if unreachable:
        raise ScenarioError(f"unreachable nodes: {', '.join(sorted(unreachable))}")

    for node in scenario.nodes:
        outs = graph.out[node.id]
        if not outs:
            raise ScenarioError(f"node '{node.id}' has no outgoing edge; add an END edge")

        labels = [e.label for e in outs]
        if node.kind == "decision":
            for edge in outs:
                if edge.p is not None:
                    raise ScenarioError(
                        f"node '{node.id}': decision edges take their probability from "
                        "policy, so remove 'p'"
                    )
            missing = {"approve", "reject"} - set(labels)
            if missing:
                raise ScenarioError(
                    f"node '{node.id}': decision node needs edges labelled "
                    f"{sorted(missing)} (routing for gate outcomes)"
                )
            extra = [lbl for lbl in labels if lbl not in DECISION_LABELS]
            if extra:
                raise ScenarioError(
                    f"node '{node.id}': decision edges must be labelled from "
                    f"{sorted(DECISION_LABELS)}"
                )
            if labels.count("approve") != 1 or labels.count("reject") != 1:
                raise ScenarioError(
                    f"node '{node.id}': exactly one 'approve' and one 'reject' edge"
                )
        else:
            if any(e.label != "flow" for e in outs):
                raise ScenarioError(
                    f"node '{node.id}': only decision nodes may have labelled (gate) edges"
                )
            if len(outs) == 1 and outs[0].p is None:
                continue
            total = sum(e.p or 0.0 for e in outs)
            if any(e.p is None for e in outs):
                raise ScenarioError(
                    f"node '{node.id}': every outgoing edge needs a probability 'p'"
                )
            if abs(total - 1.0) > 1e-6:
                raise ScenarioError(
                    f"node '{node.id}': outgoing probabilities sum to {total:g}, expected 1"
                )

    if scenario.evidence is None:
        raise ScenarioError("scenario needs an 'evidence' block for the classification model")


def scenario_from_dict(raw: dict[str, Any], path: str = "") -> Scenario:
    if not isinstance(raw, dict):
        raise ScenarioError("scenario root must be an object")
    where = "scenario"
    scenario_id = _str(raw, "id", where)
    title = _str(raw, "title", where)

    target_raw = _require(raw, "target", where)
    if not isinstance(target_raw, dict):
        raise ScenarioError("target: must be an object")
    classification = target_raw.get("classification", "unknown")
    if classification not in ("unknown", "hostile", "benign"):
        raise ScenarioError("target.classification must be unknown, hostile or benign")
    target = Target(
        label=_str(target_raw, "label", "target"),
        kind=_str(target_raw, "kind", "target", default="unknown"),
        classification=classification,
        mobility=_str(target_raw, "mobility", "target", default="unknown"),
        description=_str(target_raw, "description", "target", default=""),
    )

    try:
        policy = doctrine.Policy.from_dict(raw.get("policy", {}))
    except doctrine.PolicyError as exc:
        raise ScenarioError(f"policy: {exc}") from exc

    nodes_raw = _require(raw, "nodes", where)
    edges_raw = _require(raw, "edges", where)
    if not isinstance(nodes_raw, list) or not isinstance(edges_raw, list):
        raise ScenarioError("nodes and edges must be arrays")
    nodes = tuple(_parse_node(n, i) for i, n in enumerate(nodes_raw))
    node_ids = {n.id for n in nodes}
    edges = tuple(_parse_edge(e, i, node_ids) for i, e in enumerate(edges_raw))

    tags = raw.get("tags", [])
    if not isinstance(tags, list):
        raise ScenarioError("tags must be an array")
    tags = tuple(str(t) for t in tags)

    origin = raw.get("origin", {})
    try:
        lat = float(origin.get("lat", 0.0))
        lon = float(origin.get("lon", 0.0))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ScenarioError("origin must be {'lat': number, 'lon': number}") from exc

    scenario = Scenario(
        id=scenario_id,
        title=title,
        summary=_str(raw, "summary", where, default=""),
        target=target,
        policy=policy,
        evidence=_parse_evidence(raw.get("evidence")),
        nodes=nodes,
        edges=edges,
        tags=tags,
        origin=(lat, lon),
        notes=_str(raw, "notes", where, default=""),
        source_fingerprint=hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16],
        path=path,
    )
    graph = Graph(scenario)
    _validate(scenario, graph)
    return scenario


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioError(f"scenario file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{path.name}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
    return scenario_from_dict(raw, path=str(path))


def load_all(directory: str | Path) -> list[Scenario]:
    directory = Path(directory)
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.json"))]
    return scenarios
