"""Targeting authority, PID and collateral-damage gating.

In a real targeting cycle the slow, decisive step is rarely the sensor. It is
the moment where someone with legal authority has to answer three questions in
writing:

  1. Is this the thing we think it is, to a stated confidence? (PID)
  2. Is striking it lawful and proportionate here? (ROE, CDE, no-strike list)
  3. Am I the person allowed to say yes? (release authority)

This module encodes those three questions as an executable, inspectable policy
so the simulator can show how much of the end-to-end timeline they consume and
how thresholds trade latency against the risk of acting on a bad track.

Terminology follows US/NATO joint targeting practice (JP 3-60): PID = positive
identification, CDE = collateral damage estimation, ROE = rules of engagement,
and release authority is the echelon empowered to approve engagement.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

AUTHORITY_ORDER = {"tactical": 0, "operational": 1, "strategic": 2}
ROE_POSTURES = ("hold", "defensive", "offensive")

# Outcomes the simulation graph must be able to route.
APPROVE = "approve"
ESCALATE = "escalate"
REJECT = "reject"
DENY = "deny"

OUTCOMES = (APPROVE, ESCALATE, REJECT, DENY)


class PolicyError(ValueError):
    pass


def cde_rank(level: str) -> int:
    """'CDE-2' -> 2. Higher is more collateral. CDE-5 means 'not estimable'."""
    if not isinstance(level, str) or not level.upper().startswith("CDE-"):
        raise PolicyError(f"collateral estimate must look like 'CDE-2', got {level!r}")
    try:
        return int(level.split("-", 1)[1])
    except ValueError as exc:
        raise PolicyError(f"collateral estimate must look like 'CDE-2', got {level!r}") from exc


@dataclass(frozen=True)
class Policy:
    """The commander's constraints. Frozen so a run cannot mutate its own rules."""

    pid_threshold: float = 0.85
    """Below this confidence the track is not positively identified: re-task."""

    escalation_threshold: float = 0.95
    """The delegation limit: a subordinate echelon may release on its own only
    at or above this confidence. It is always >= pid_threshold."""

    release_authority: str = "tactical"
    """The echelon that owns the release decision for this class of target."""

    roe_posture: str = "defensive"
    """hold: no engagements. defensive: engage only on clear hostile act/intent.
    offensive: engage declared targets subject to the rest of the policy."""

    cde_level: str = "CDE-2"
    """Estimated collateral damage for this particular engagement."""

    cde_allowance: str = "CDE-2"
    """The most collateral the approving commander may accept at this echelon."""

    no_strike: bool = False
    """Entity appears on the no-strike list (schools, hospitals, cultural sites)."""

    protected: bool = False
    """Entity is a protected person/object under the law of armed conflict."""

    max_recycles: int = 6
    """Policy cap on re-attack of the sensor loop before the chain is abandoned."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.pid_threshold <= 1.0:
            raise PolicyError("pid_threshold must be within [0, 1]")
        if not 0.0 <= self.escalation_threshold <= 1.0:
            raise PolicyError("escalation_threshold must be within [0, 1]")
        if self.escalation_threshold < self.pid_threshold:
            raise PolicyError("escalation_threshold must be >= pid_threshold")
        if self.release_authority not in AUTHORITY_ORDER:
            raise PolicyError(f"release_authority must be one of {sorted(AUTHORITY_ORDER)}")
        if self.roe_posture not in ROE_POSTURES:
            raise PolicyError(f"roe_posture must be one of {list(ROE_POSTURES)}")
        cde_rank(self.cde_level)
        cde_rank(self.cde_allowance)
        if self.max_recycles < 0:
            raise PolicyError("max_recycles must be >= 0")

    def with_threshold(self, threshold: float) -> "Policy":
        return replace(self, pid_threshold=threshold)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid_threshold": self.pid_threshold,
            "escalation_threshold": self.escalation_threshold,
            "release_authority": self.release_authority,
            "roe_posture": self.roe_posture,
            "cde_level": self.cde_level,
            "cde_allowance": self.cde_allowance,
            "no_strike": self.no_strike,
            "protected": self.protected,
            "max_recycles": self.max_recycles,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], base: "Policy | None" = None) -> "Policy":
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise PolicyError(f"unknown policy keys: {sorted(unknown)}")
        if base is None:
            return cls(**raw)
        return replace(base, **raw)


@dataclass(frozen=True)
class Decision:
    """One gate outcome, with enough context to be auditable later."""

    outcome: str
    rule: str
    reason: str
    authority: str
    confidence: float

    @property
    def released(self) -> bool:
        return self.outcome == APPROVE

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "rule": self.rule,
            "reason": self.reason,
            "authority": self.authority,
            "confidence": round(self.confidence, 4),
        }


def decide(
    confidence: float,
    policy: Policy,
    target_class: str = "unknown",
    echelon: str = "tactical",
) -> Decision:
    """Evaluate one engagement proposal at a given echelon.

    Order matters and is legally meaningful: protected-entity and ROE checks
    precede PID, because no amount of confidence authorises an unlawful
    engagement. `target_class` is the track's currently declared classification
    ('unknown', 'hostile', 'benign') and `echelon` is the level of the staff
    evaluating the proposal.

    The delegation logic is the part people get wrong. The echelon that *owns*
    the decision applies one threshold. An echelon below it can only forward the
    proposal, unless confidence clears the delegation limit, in which case it is
    pre-authorised to act. Raising the limit therefore buys speed on high-quality
    tracks and buys nothing on marginal ones.
    """
    if policy.no_strike:
        return Decision(
            DENY, "no-strike-list",
            "Entity appears on the no-strike list; engagement prohibited regardless of PID.",
            echelon, confidence,
        )
    if policy.protected:
        return Decision(
            DENY, "protected-entity",
            "Entity is a protected person or object under the law of armed conflict.",
            echelon, confidence,
        )
    if policy.roe_posture == "hold":
        return Decision(
            DENY, "roe-hold",
            "ROE posture is HOLD; no engagements are authorised at this time.",
            echelon, confidence,
        )
    if cde_rank(policy.cde_level) > cde_rank(policy.cde_allowance):
        return Decision(
            DENY, "cde-exceeds-allowance",
            f"Estimated collateral ({policy.cde_level}) exceeds the allowance "
            f"({policy.cde_allowance}); engagement not authorised at any echelon.",
            echelon, confidence,
        )
    if policy.roe_posture == "defensive" and target_class != "hostile":
        return Decision(
            REJECT, "roe-defensive",
            "Defensive posture requires a demonstrated hostile act or hostile intent; "
            f"track classification is '{target_class}'.",
            echelon, confidence,
        )
    if confidence < policy.pid_threshold:
        return Decision(
            REJECT, "pid-insufficient",
            f"PID confidence {confidence:.2f} is below the {policy.pid_threshold:.2f} "
            "threshold; re-task the sensor and re-evaluate.",
            echelon, confidence,
        )

    owns_release = AUTHORITY_ORDER[echelon] >= AUTHORITY_ORDER[policy.release_authority]
    if owns_release:
        return Decision(
            APPROVE, "released",
            f"PID confidence {confidence:.2f} meets the {policy.pid_threshold:.2f} "
            f"threshold and {echelon} echelon holds release authority.",
            echelon, confidence,
        )
    if confidence >= policy.escalation_threshold:
        return Decision(
            APPROVE, "released-delegated",
            f"PID confidence {confidence:.2f} clears the {policy.escalation_threshold:.2f} "
            f"delegation limit, so {echelon} echelon is pre-authorised to release.",
            echelon, confidence,
        )
    return Decision(
        ESCALATE, "authority-escalation",
        f"PID confidence {confidence:.2f} meets the {policy.pid_threshold:.2f} threshold "
        f"but is below the {policy.escalation_threshold:.2f} delegation limit; "
        f"release authority is retained at {policy.release_authority} echelon.",
        echelon, confidence,
    )


def describe_policy(policy: Policy) -> list[str]:
    """Human-readable rule list, shown in the UI so the logic is never a black box."""
    rules = [
        "Deny if the entity is on the no-strike list or is otherwise protected.",
        "Deny if ROE posture is HOLD.",
        f"Deny if estimated collateral ({policy.cde_level}) exceeds the "
        f"{policy.cde_allowance} allowance.",
        f"Reject and re-task if PID confidence < {policy.pid_threshold:.2f}.",
        f"Release at {policy.release_authority} echelon if confidence >= "
        f"{policy.pid_threshold:.2f} (the owning echelon applies one threshold).",
    ]
    if policy.release_authority == "tactical":
        rules.append(
            "Release authority is delegated to the tactical echelon: there is no "
            "escalation path, and the delegation limit is therefore inert."
        )
    else:
        rules.append(
            f"An echelon below {policy.release_authority} may act on its own only at "
            f"confidence >= {policy.escalation_threshold:.2f}; between "
            f"{policy.pid_threshold:.2f} and {policy.escalation_threshold:.2f} it must elevate."
        )
    rules.append(f"Abandon the chain after {policy.max_recycles} failed identification cycles.")
    return rules
