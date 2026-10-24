"""Audit-chain and CoT serialisation tests."""

import random
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from kc.audit import AuditLog, decision_records, load_jsonl, verify_records
from kc.cot import document, track_events
from kc.engine import monte_carlo, simulate
from kc.model import load_scenario

ROOT = Path(__file__).resolve().parents[1]


def sample_log():
    log = AuditLog()
    log.append({"type": "engagement_decision", "outcome": "reject", "confidence": 0.71})
    log.append({"type": "engagement_decision", "outcome": "approve", "confidence": 0.96})
    log.append({"type": "run_summary", "runs": 1000})
    return log


class AuditTests(unittest.TestCase):
    def test_chain_verifies(self):
        verification = sample_log().verify()
        self.assertTrue(verification.ok)
        self.assertEqual(verification.records, 3)

    def test_genesis_prev_is_zeroed(self):
        log = sample_log()
        self.assertEqual(log.records[0]["prev"], "0" * 64)

    def test_editing_a_record_is_detected(self):
        log = sample_log()
        log.records[1]["outcome"] = "deny"
        verification = log.verify()
        self.assertFalse(verification.ok)
        self.assertEqual(verification.first_bad_seq, 2)

    def test_deleting_a_record_is_detected(self):
        log = sample_log()
        del log.records[1]
        verification = log.verify()
        self.assertFalse(verification.ok)
        self.assertEqual(verification.first_bad_seq, 2)

    def test_reordering_is_detected(self):
        log = sample_log()
        log.records[0], log.records[1] = log.records[1], log.records[0]
        self.assertFalse(log.verify().ok)

    def test_round_trip_jsonl(self):
        log = sample_log()
        restored = load_jsonl(log.to_jsonl())
        self.assertEqual(len(restored), 3)
        self.assertTrue(verify_records(restored).ok)

    def test_keyed_chain_rejects_wrong_key(self):
        log = AuditLog(key=b"correct")
        log.append({"type": "x"})
        self.assertTrue(log.verify().ok)
        self.assertFalse(verify_records(log.records, b"wrong").ok)

    def test_reserved_keys_are_rejected(self):
        log = AuditLog()
        with self.assertRaises(ValueError):
            log.append({"hash": "deadbeef"})

    def test_decision_records_carry_context(self):
        records = decision_records(
            [],
            scenario_id="s",
            scenario_fingerprint="f",
            target="T",
            policy={"pid_threshold": 0.9},
            seed=1,
        )
        self.assertEqual(records, [])

    def test_records_from_a_real_run_verify(self):
        scenario = load_scenario(ROOT / "scenarios" / "04-tight-roe.json")
        result = simulate(scenario, random.Random(2))
        log = AuditLog()
        log.extend(
            decision_records(
                result.decisions,
                scenario_id=scenario.id,
                scenario_fingerprint=scenario.source_fingerprint,
                target=scenario.target.label,
                policy=scenario.policy.as_dict(),
                seed=2,
            )
        )
        self.assertTrue(log.verify().ok)
        for record in log.records:
            self.assertTrue(record["synthetic"])
            self.assertIn("reason", record)


class CoTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenario = load_scenario(ROOT / "scenarios" / "02-fused-killweb.json")

    def test_events_are_well_formed_xml(self):
        result = simulate(self.scenario, random.Random(4))
        root = ET.fromstring(document(track_events(self.scenario, result)))
        self.assertEqual(root.tag, "events")
        self.assertTrue(len(root) >= 1)

    def test_one_event_per_gate(self):
        result = simulate(self.scenario, random.Random(4))
        gates = [segment for segment in result.segments if segment.kind == "gate"]
        root = ET.fromstring(document(track_events(self.scenario, result)))
        self.assertEqual(len(root), len(gates))

    def test_released_track_changes_affiliation(self):
        released = None
        for seed in range(40):
            result = simulate(self.scenario, random.Random(seed))
            if result.outcome == "engaged":
                released = result
                break
        self.assertIsNotNone(released, "expected at least one engaged run")
        root = ET.fromstring(document(track_events(self.scenario, released)))
        types = [event.get("type") for event in root]
        self.assertIn("a-h-G", types)

    def test_events_carry_a_point_and_uid(self):
        result = simulate(self.scenario, random.Random(1))
        root = ET.fromstring(document(track_events(self.scenario, result)))
        for event in root:
            self.assertAlmostEqual(float(event.get("version")), 2.0)
            self.assertIn("TRK-1042", event.get("uid"))
            point = event.find("point")
            self.assertIsNotNone(point)
            self.assertIn("lat", point.attrib)
            self.assertIn("lon", point.attrib)

    def test_denied_track_stays_unknown(self):
        scenario = load_scenario(ROOT / "scenarios" / "05-cde-denied.json")
        result = simulate(scenario, random.Random(1))
        self.assertEqual(result.outcome, "denied")
        root = ET.fromstring(document(track_events(scenario, result)))
        self.assertEqual(root[0].get("type"), "a-u-S")


class ReportTests(unittest.TestCase):
    def test_report_payload_is_json_safe(self):
        import json

        from kc.analysis import build_report

        scenario = load_scenario(ROOT / "scenarios" / "05-cde-denied.json")
        report = build_report(scenario, runs=120, seed=3, sensitivity=False)
        encoded = json.dumps(report, allow_nan=False)
        self.assertIn("policy_rules", encoded)
        self.assertEqual(report["metrics"]["engaged"], 0)
        self.assertTrue(report["audit"]["verification"]["ok"])


if __name__ == "__main__":
    unittest.main()
