"""Simulation and analysis tests."""

import math
import random
import unittest
from dataclasses import replace
from pathlib import Path

from kc.analysis import compare, histogram, insights, sweep, tornado
from kc.engine import (
    monte_carlo,
    phase_table,
    quantile,
    simulate,
    with_scaled_node,
)
from kc.model import load_scenario

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scenarios" / "01-legacy-sequential.json"
MODERN = ROOT / "scenarios" / "02-fused-killweb.json"


class SingleRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(BASELINE)

    def test_deterministic_for_a_fixed_seed(self):
        a = simulate(self.scenario, random.Random(42))
        b = simulate(self.scenario, random.Random(42))
        self.assertEqual(a.total_s, b.total_s)
        self.assertEqual(a.visits, b.visits)

    def test_time_accounting_is_additive(self):
        result = simulate(self.scenario, random.Random(3))
        activity = sum(result.time_in.values())
        self.assertAlmostEqual(activity + result.link_s, result.total_s, places=6)

    def test_timeline_is_monotonic(self):
        result = simulate(self.scenario, random.Random(5))
        previous = -1.0
        for segment in result.segments:
            self.assertGreaterEqual(segment.start, previous - 1e-9)
            self.assertGreaterEqual(segment.end, segment.start)
            previous = segment.end

    def test_outcome_is_one_of_three_states(self):
        for seed in range(20):
            result = simulate(self.scenario, random.Random(seed))
            self.assertIn(result.outcome, {"engaged", "denied", "abandoned"})

    def test_gate_decision_is_recorded_with_reasoning(self):
        result = simulate(self.scenario, random.Random(0))
        self.assertTrue(result.decisions)
        for decision in result.decisions:
            self.assertIn(decision.outcome, {"approve", "reject", "escalate", "deny"})
            self.assertTrue(decision.reason)
            self.assertTrue(decision.rule)

    def test_recycle_cap_abandons_the_chain(self):
        impossible = replace(
            self.scenario.policy,
            pid_threshold=1.0,
            escalation_threshold=1.0,
            max_recycles=0,
        )
        result = simulate(self.scenario, random.Random(1), policy=impossible)
        self.assertEqual(result.outcome, "abandoned")
        self.assertEqual(result.terminal, "END_ABANDONED")

    def test_denied_outcome_when_collateral_exceeds_allowance(self):
        blocked = replace(self.scenario.policy, cde_level="CDE-4")
        result = simulate(self.scenario, random.Random(1), policy=blocked)
        self.assertEqual(result.outcome, "denied")
        self.assertEqual(result.decisions[0].rule, "cde-exceeds-allowance")


class MonteCarloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(BASELINE)
        cls.mc = monte_carlo(cls.scenario, runs=600, seed=7)

    def test_outcome_counts_sum_to_runs(self):
        self.assertEqual(sum(self.mc.outcomes.values()), 600)

    def test_engaged_count_matches_durations(self):
        self.assertEqual(len(self.mc.durations), self.mc.engaged)

    def test_durations_are_sorted(self):
        self.assertEqual(self.mc.durations, sorted(self.mc.durations))

    def test_percentiles_are_ordered(self):
        p50 = self.mc.pct(0.50)
        p95 = self.mc.pct(0.95)
        p99 = self.mc.pct(0.99)
        self.assertLessEqual(p50, p95)
        self.assertLessEqual(p95, p99)

    def test_summary_shares_are_sane(self):
        summary = self.mc.summary()
        total = sum(column["mean_s"] for column in summary["contributions"])
        self.assertGreater(total, 0)
        self.assertAlmostEqual(summary["total_s"], total + self.mc.link_s, delta=1.0)

    def test_phase_table_covers_all_phases(self):
        table = phase_table(self.mc)
        self.assertEqual(len(table), 6)
        self.assertAlmostEqual(
            sum(row["seconds"] for row in table),
            sum(self.mc.phase_seconds.values()),
            delta=0.5,
        )

    def test_quantile_interpolates(self):
        self.assertAlmostEqual(quantile([0.0, 10.0], 0.5), 5.0)
        self.assertAlmostEqual(quantile([4.0], 0.9), 4.0)

    def test_sample_run_is_representative(self):
        self.assertIsNotNone(self.mc.sample)
        typical = self.mc.pct(0.50)
        self.assertLess(abs(self.mc.sample.total_s - typical), typical * 0.5)

    def test_no_engaged_runs_is_handled(self):
        blocked = replace(self.scenario.policy, cde_level="CDE-4")
        mc = monte_carlo(self.scenario, runs=50, seed=2, policy=blocked)
        self.assertEqual(mc.engaged, 0)
        summary = mc.summary()
        self.assertIsNone(summary["p95_s"])
        self.assertIsNone(summary["mean_s"])
        self.assertTrue(math.isnan(mc.pct(0.5)))
        self.assertTrue(insights(mc, self.scenario))


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(BASELINE)
        cls.modern = load_scenario(MODERN)

    def test_histogram_accounts_for_every_sample(self):
        mc = monte_carlo(self.scenario, runs=400, seed=3)
        bins = histogram(mc.durations)
        self.assertEqual(sum(bin_["count"] for bin_ in bins), len(mc.durations))

    def test_speedup_reduces_latency(self):
        base = monte_carlo(self.scenario, runs=400, seed=3)
        faster = monte_carlo(with_scaled_node(self.scenario, "detect", 0.1), runs=400, seed=3)
        self.assertLess(faster.pct(0.5), base.pct(0.5))

    def test_modern_chain_is_faster_than_baseline(self):
        base = monte_carlo(self.scenario, runs=800, seed=7)
        modern = monte_carlo(self.modern, runs=800, seed=7)
        self.assertLess(modern.pct(0.5), base.pct(0.5))
        self.assertLess(modern.pct(0.95), base.pct(0.95))

    def test_sweep_release_rate_falls_with_threshold(self):
        points = sweep(self.scenario, runs=250, steps=7)
        self.assertEqual(len(points), 7)
        for earlier, later in zip(points, points[1:]):
            self.assertLessEqual(later["p_release_hostile"], earlier["p_release_hostile"] + 1e-9)
            self.assertLessEqual(later["p_release_benign"], earlier["p_release_benign"] + 1e-9)

    def test_sweep_posterior_is_a_probability(self):
        for point in sweep(self.scenario, runs=200, steps=7):
            self.assertGreaterEqual(point["posterior_hostile"], 0.0)
            self.assertLessEqual(point["posterior_hostile"], 1.0)

    def test_tornado_is_ranked_and_non_negative_for_the_network(self):
        rows = tornado(self.scenario, runs=200, seed=7)
        self.assertTrue(rows)
        deltas = [row["delta_p95_s"] for row in rows]
        self.assertEqual(deltas, sorted(deltas, reverse=True))
        self.assertTrue(any(row["id"] == "__links__" for row in rows))

    def test_compare_reports_deltas(self):
        base = monte_carlo(self.scenario, runs=300, seed=7)
        modern = monte_carlo(self.modern, runs=300, seed=7)
        diff = compare(base, modern)
        p95 = next(row for row in diff["rows"] if row["metric"] == "p95")
        self.assertLess(p95["delta"], 0)
        self.assertEqual(p95["better"], "b")

    def test_insights_are_generated_from_the_data(self):
        mc = monte_carlo(self.scenario, runs=300, seed=7)
        text = insights(mc, self.scenario)
        self.assertGreaterEqual(len(text), 3)
        self.assertTrue(all(isinstance(line, str) and line for line in text))


if __name__ == "__main__":
    unittest.main()
