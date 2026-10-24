"""Append-only, hash-chained decision log.

A targeting decision that cannot be reconstructed afterwards is not a decision,
it is an incident. This log records every gate evaluation in order, links each
record to its predecessor by hash, and can be verified offline.

Honest limitation: a plain hash chain detects accidental corruption and casual
editing. Anyone who controls the file can rewrite history and recompute every
hash from the edit forward. Real systems anchor the head somewhere they do not
control (signature, notarised timestamp, an external service). Set
KC_AUDIT_KEY to switch to keyed hashing, which raises the bar but still is not
a substitute for a signature and an external anchor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

GENESIS = "0" * 64


def canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(record: dict[str, Any], key: bytes | None) -> str:
    payload = canonical(record)
    if key:
        return hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Verification:
    ok: bool
    records: int
    first_bad_seq: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "records": self.records,
            "first_bad_seq": self.first_bad_seq,
            "detail": self.detail,
        }


class AuditLog:
    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or (os.environ.get("KC_AUDIT_KEY", "").encode() or None)
        self.records: list[dict[str, Any]] = []

    @property
    def head(self) -> str:
        return self.records[-1]["hash"] if self.records else GENESIS

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        if "hash" in record or "prev" in record or "seq" in record:
            raise ValueError("audit records may not carry reserved keys (hash, prev, seq)")
        entry = dict(record)
        entry["seq"] = len(self.records) + 1
        entry["ts"] = _now()
        entry["prev"] = self.head
        entry["hash"] = _digest(entry, self.key)
        self.records.append(entry)
        return entry

    def extend(self, records: Iterable[dict[str, Any]]) -> None:
        for record in records:
            self.append(record)

    def verify(self) -> Verification:
        return verify_records(self.records, self.key)

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r, sort_keys=True) for r in self.records) + ("\n" if self.records else "")

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl(), encoding="utf-8")
        return path


def load_jsonl(text: str) -> list[dict[str, Any]]:
    records = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_no}: invalid JSON ({exc.msg})") from exc
    return records


def verify_records(records: list[dict[str, Any]], key: bytes | None = None) -> Verification:
    previous = GENESIS
    for index, record in enumerate(records, 1):
        if record.get("seq") != index:
            return Verification(False, len(records), index, "sequence number out of order")
        if record.get("prev") != previous:
            return Verification(False, len(records), index, "broken chain: prev hash mismatch")
        body = {k: v for k, v in record.items() if k != "hash"}
        if _digest(body, key) != record.get("hash"):
            return Verification(False, len(records), index, "record hash mismatch (edited record)")
        previous = record["hash"]
    return Verification(True, len(records), None, "chain intact")


def decision_records(
    decisions: Iterable[Any],
    *,
    scenario_id: str,
    scenario_fingerprint: str,
    target: str,
    policy: dict[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    """Turn one run's gate evaluations into auditable records."""
    out = []
    for decision in decisions:
        out.append(
            {
                "type": "engagement_decision",
                "scenario": scenario_id,
                "scenario_fingerprint": scenario_fingerprint,
                "target": target,
                "sim_seed": seed,
                "sim_t": decision.t,
                "gate": decision.node_id,
                "confidence": round(decision.confidence, 4),
                "outcome": decision.outcome,
                "rule": decision.rule,
                "reason": decision.reason,
                "authority": decision.authority,
                "policy": policy,
                "synthetic": True,
            }
        )
    return out
