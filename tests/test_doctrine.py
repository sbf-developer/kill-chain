"""Doctrine / policy tests.

These are the rules that must never quietly change, so they get explicit
coverage rather than being asserted indirectly through the simulator.
"""

import unittest

from kc.doctrine import (
    APPROVE,
    DENY,
    ESCALATE,
    REJECT,
    Policy,
    PolicyError,
    cde_rank,
    decide,
    describe_policy,
)


class PolicyValidationTests(unittest.TestCase):
    def test_threshold_bounds(self):
        with self.assertRaises(PolicyError):
            Policy(pid_threshold=1.2)
        with self.assertRaises(PolicyError):
            Policy(pid_threshold=0.9, escalation_threshold=0.8)

    def test_authority_and_roe_vocabularies(self):
        with self.assertRaises(PolicyError):
            Policy(release_authority="supreme")
        with self.assertRaises(PolicyError):
            Policy(roe_posture="aggressive")

    def test_cde_rank(self):
        self.assertEqual(cde_rank("CDE-1"), 1)
        self.assertEqual(cde_rank("cde-4"), 4)
        with self.assertRaises(PolicyError):
            cde_rank("high")

    def test_unknown_override_key(self):
        with self.assertRaises(PolicyError):
            Policy.from_dict({"pid_threshhold": 0.9})

    def test_describe_policy_mentions_threshold(self):
        text = " ".join(describe_policy(Policy(pid_threshold=0.83)))
        self.assertIn("0.83", text)


class GateTests(unittest.TestCase):
    def test_below_threshold_rejects_and_asks_for_retask(self):
        decision = decide(0.60, Policy(pid_threshold=0.85), target_class="hostile")
        self.assertEqual(decision.outcome, REJECT)
        self.assertEqual(decision.rule, "pid-insufficient")
        self.assertIn("re-task", decision.reason)

    def test_owning_echelon_approves_at_threshold(self):
        decision = decide(
            0.86, Policy(pid_threshold=0.85, release_authority="tactical"), target_class="hostile"
        )
        self.assertEqual(decision.outcome, APPROVE)
        self.assertEqual(decision.rule, "released")

    def test_subordinate_escalates_between_threshold_and_delegation_limit(self):
        policy = Policy(pid_threshold=0.85, escalation_threshold=0.95, release_authority="operational")
        decision = decide(0.90, policy, target_class="hostile", echelon="tactical")
        self.assertEqual(decision.outcome, ESCALATE)
        self.assertEqual(decision.rule, "authority-escalation")

    def test_subordinate_may_act_when_delegation_limit_cleared(self):
        policy = Policy(pid_threshold=0.85, escalation_threshold=0.95, release_authority="operational")
        decision = decide(0.97, policy, target_class="hostile", echelon="tactical")
        self.assertEqual(decision.outcome, APPROVE)
        self.assertEqual(decision.rule, "released-delegated")

    def test_owning_echelon_ignores_delegation_limit(self):
        policy = Policy(pid_threshold=0.85, escalation_threshold=0.95, release_authority="operational")
        decision = decide(0.88, policy, target_class="hostile", echelon="operational")
        self.assertEqual(decision.outcome, APPROVE)
        self.assertEqual(decision.rule, "released")

    def test_no_strike_list_beats_everything(self):
        policy = Policy(pid_threshold=0.5, no_strike=True)
        decision = decide(0.99, policy, target_class="hostile")
        self.assertEqual(decision.outcome, DENY)
        self.assertEqual(decision.rule, "no-strike-list")

    def test_protected_entity_beats_everything(self):
        decision = decide(0.99, Policy(protected=True), target_class="hostile")
        self.assertEqual(decision.outcome, DENY)
        self.assertEqual(decision.rule, "protected-entity")

    def test_roe_hold_denies(self):
        decision = decide(0.99, Policy(roe_posture="hold"), target_class="hostile")
        self.assertEqual(decision.outcome, DENY)
        self.assertEqual(decision.rule, "roe-hold")

    def test_collateral_above_allowance_denies_regardless_of_confidence(self):
        decision = decide(
            0.99, Policy(cde_level="CDE-3", cde_allowance="CDE-2"), target_class="hostile"
        )
        self.assertEqual(decision.outcome, DENY)
        self.assertEqual(decision.rule, "cde-exceeds-allowance")

    def test_defensive_posture_rejects_non_hostile_track(self):
        decision = decide(0.99, Policy(roe_posture="defensive"), target_class="unknown")
        self.assertEqual(decision.outcome, REJECT)
        self.assertEqual(decision.rule, "roe-defensive")

    def test_legal_filter_runs_before_pid(self):
        # A protected entity with terrible confidence is denied, not re-tasked.
        decision = decide(0.10, Policy(protected=True, pid_threshold=0.9), target_class="hostile")
        self.assertEqual(decision.outcome, DENY)


if __name__ == "__main__":
    unittest.main()
