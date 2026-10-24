"""Cursor-on-Target (CoT) emitter.

CoT is the actual interoperability format used by the TAK ecosystem (ATAK,
WinTAK, iTAK, TAK Server) and by a large share of US and allied tactical
applications. It is a small XML schema carried over TCP/TLS or UDP: an event
has a unique id, a type string, a time/stale window and a point.

Emitting it here means this simulator can put its synthetic track onto a real
common operational picture instead of inventing a private format. The CoT type
string itself carries the identification state, which is the point: a track
stays `a-u-S` (unknown surface) until an authority releases it.

Nothing here is a weapon model. It is a message serializer for a simulation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr

from .engine import RunResult
from .model import Scenario

# CoT type taxonomy (MIL-STD-2525 derived): a-<affiliation>-<battle dimension>.
# Only the two states this simulator needs are emitted: a track is unknown
# surface until an authority releases it, then it becomes hostile ground.
UNKNOWN_SURFACE = "a-u-S"
HOSTILE_GROUND = "a-h-G"

AFFILIATION_NAME = {"u": "Unknown", "h": "Hostile", "n": "Neutral", "f": "Friend"}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _offset(origin: tuple[float, float], metres_east: float, metres_north: float) -> tuple[float, float]:
    """Metres to a notional lat/lon offset. Good enough for a synthetic picture."""
    lat, lon = origin
    dlat = metres_north / 111_320.0
    dlon = metres_east / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def _event(
    *,
    uid: str,
    cot_type: str,
    when: datetime,
    lat: float,
    lon: float,
    detail: str,
    stale_s: int = 300,
) -> str:
    affiliation = cot_type.split("-")[1] if cot_type.count("-") >= 2 else "u"
    group = AFFILIATION_NAME.get(affiliation, "Unknown")
    stale = when + timedelta(seconds=stale_s)
    return (
        f'<event version="2.0" uid={quoteattr(uid)} type={quoteattr(cot_type)} '
        f'time={quoteattr(_stamp(when))} start={quoteattr(_stamp(when))} '
        f'stale={quoteattr(_stamp(stale))} how="m-g">\n'
        f'  <point lat="{lat:.6f}" lon="{lon:.6f}" hae="0.0" ce="9999999.0" le="9999999.0"/>\n'
        f'  <detail>\n'
        f'    <contact callsign={quoteattr(uid.split(".")[-1])}/>\n'
        f'    <__group name={quoteattr(group)} role="Team Member"/>\n'
        f"    {detail}\n"
        f"  </detail>\n"
        f"</event>"
    )


def track_events(
    scenario: Scenario, run: RunResult, start: datetime | None = None
) -> list[str]:
    """One CoT event per gate evaluation in a simulated run.

    A denial or escalation deliberately leaves the affiliation as unknown: the
    picture only changes when authority is granted. That is the whole argument
    for putting the decision layer at the centre of the system.
    """
    start = start or datetime.now(timezone.utc)
    uid = f"KC.{scenario.target.label}"
    events: list[str] = []

    for index, segment in enumerate(run.segments):
        if segment.kind != "gate":
            continue
        when = start + timedelta(seconds=segment.start)
        outcome = segment.label.split()[0] if segment.label else "GATE"
        released = outcome in ("APPROVE", "RELEASED")
        confidence = segment.label.split("p=")[-1] if "p=" in segment.label else ""
        detail = (
            f'<_kc synthetic="true" chain={quoteattr(scenario.id)} '
            f'phase="target" gate={quoteattr(segment.node_id or "")} outcome={quoteattr(outcome)} '
            f'confidence={quoteattr(confidence)} released="{str(released).lower()}"/>'
        )
        metres = 120.0 * index
        lat, lon = _offset(scenario.origin, metres, metres * 0.4)
        events.append(
            _event(
                uid=uid,
                cot_type=HOSTILE_GROUND if released else UNKNOWN_SURFACE,
                when=when,
                lat=lat,
                lon=lon,
                detail=detail,
            )
        )

    if not events:
        lat, lon = _offset(scenario.origin, 0.0, 0.0)
        events.append(
            _event(
                uid=uid,
                cot_type=UNKNOWN_SURFACE,
                when=start,
                lat=lat,
                lon=lon,
                detail=f'<_kc synthetic="true" chain={quoteattr(scenario.id)} outcome="no_decision"/>',
            )
        )
    return events


def document(events: Iterable[str]) -> str:
    body = "\n".join(events)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<events>\n' + body + "\n</events>\n"


def scenario_cot(scenario: Scenario, run: RunResult, start: datetime | None = None) -> str:
    return document(track_events(scenario, run, start))
