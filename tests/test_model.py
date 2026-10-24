"""Schema, distribution and loading tests."""

import json
import math
import random
import unittest
from pathlib import Path

from kc.distributions import SpecError, parse
from kc.model import ScenarioError, load_all, load_scenario, scenario_from_dict

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = sorted((ROOT / "scenarios").glob("*.json"))


def base_scenario():
    return {
        "id": "test",
        "title": "test chain",
        "target": {"label": "T-1", "classification": "hostile"},
        "evidence": {
            "prior_hostile": 0.2,
            "beta_hostile": {"dist": "beta", "a": 8, "b": 2},
            "beta_benign": {"dist": "beta", "a": 2, "b": 8},
        },
        "policy": {},
        "nodes": [
            {"id": "a", "kind": "sensor", "label": "A", "duration": 10},
            {"id": "g", "kind": "decision", "label": "G", "duration": 5, "phase": "target"},
            {"id": "z", "kind": "assessment", "label": "Z", "duration": 5, "phase": "assess"},
        ],
        "edges": [
            {"from": "a", "to": "g"},
            {"from": "g", "to": "z", "label": "approve"},
            {"from": "g", "to": "a", "label": "reject"},
            {"from": "g", "to": "END_DENIED", "label": "deny"},
            {"from": "z", "to": "END"},
        ],
    }


class DistributionTests(unittest.TestCase):
    def test_constant(self):
        dist = parse(45, "x")
        self.assertAlmostEqual(dist.mean(), 45.0)
        self.assertEqual(dist.sample(random.Random(1)), 45.0)

    def test_lognormal_mean_matches_closed_form(self):
        dist = parse({"dist": "lognormal", "median": 40, "sigma": 0.5}, "x")
        self.assertAlmostEqual(dist.mean(), 40 * math.exp(0.125), places=9)

    def test_triangular_mean(self):
        dist = parse({"dist": "triangular", "min": 1, "mode": 2, "max": 6}, "x")
        self.assertAlmostEqual(dist.mean(), 3.0)

    def test_beta_stays_in_unit_interval(self):
        dist = parse({"dist": "beta", "a": 0.5, "b": 0.5}, "x")
        rng = random.Random(3)
        samples = [dist.sample(rng) for _ in range(500)]
        self.assertTrue(all(0.0 <= value <= 1.0 for value in samples))

    def test_scaled_keeps_mean_ratio(self):
        dist = parse({"dist": "lognormal", "median": 40, "sigma": 0.5}, "x")
        self.assertAlmostEqual(dist.scaled(0.5).mean(), dist.mean() * 0.5)

    def test_rejects_bad_specs(self):
        bad = [
            -1,
            {"dist": "nope"},
            {"dist": "triangular", "min": 5, "mode": 1, "max": 2},
            {"dist": "lognormal", "median": 0},
            {"dist": "beta", "a": 0, "b": 1},
            "10",
        ]
        for raw in bad:
            with self.assertRaises(SpecError, msg=str(raw)):
                parse(raw, "where")


class ScenarioTests(unittest.TestCase):
    def test_all_shipped_scenarios_load(self):
        scenarios = load_all(ROOT / "scenarios")
        self.assertEqual(len(scenarios), 5)
        for scenario in scenarios:
            self.assertTrue(scenario.nodes)
            self.assertIsNotNone(scenario.evidence)
            self.assertTrue(scenario.source_fingerprint)

    def test_valid_scenario_parses(self):
        scenario = scenario_from_dict(base_scenario())
        self.assertEqual(scenario.id, "test")
        self.assertEqual(len(scenario.nodes), 3)

    def test_probabilities_must_sum_to_one(self):
        raw = base_scenario()
        raw["nodes"].append({"id": "b", "kind": "sensor", "label": "B", "duration": 1})
        raw["edges"] = [
            {"from": "a", "to": "b", "p": 0.6},
            {"from": "a", "to": "g", "p": 0.6},
            {"from": "b", "to": "g"},
            {"from": "g", "to": "z", "label": "approve"},
            {"from": "g", "to": "a", "label": "reject"},
            {"from": "z", "to": "END"},
        ]
        with self.assertRaises(ScenarioError):
            scenario_from_dict(raw)

    def test_decision_node_requires_approve_and_reject(self):
        raw = base_scenario()
        raw["edges"] = [edge for edge in raw["edges"] if edge.get("label") != "reject"]
        with self.assertRaises(ScenarioError) as ctx:
            scenario_from_dict(raw)
        self.assertIn("reject", str(ctx.exception))

    def test_decision_node_rejects_explicit_probability(self):
        raw = base_scenario()
        for edge in raw["edges"]:
            if edge.get("label") == "approve":
                edge["p"] = 0.5
        with self.assertRaises(ScenarioError) as ctx:
            scenario_from_dict(raw)
        self.assertIn("remove 'p'", str(ctx.exception))

    def test_second_entry_node_is_rejected(self):
        raw = base_scenario()
        raw["nodes"].append({"id": "orphan", "kind": "sensor", "label": "O", "duration": 1})
        raw["edges"].append({"from": "orphan", "to": "END"})
        with self.assertRaises(ScenarioError) as ctx:
            scenario_from_dict(raw)
        self.assertIn("entry node", str(ctx.exception))

    def test_unknown_node_reference(self):
        raw = base_scenario()
        raw["edges"].append({"from": "a", "to": "ghost"})
        with self.assertRaises(ScenarioError) as ctx:
            scenario_from_dict(raw)
        self.assertIn("ghost", str(ctx.exception))

    def test_bad_policy_is_reported_as_scenario_error(self):
        raw = base_scenario()
        raw["policy"] = {"pid_threshold": 1.5}
        with self.assertRaises(ScenarioError):
            scenario_from_dict(raw)

    def test_every_shipped_scenario_has_notes(self):
        for path in SCENARIOS:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(raw.get("notes"), path.name)
            self.assertTrue(raw.get("summary"), path.name)


if __name__ == "__main__":
    unittest.main()
