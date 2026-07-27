"""Canonical coordination-pattern vocabulary.

These names describe communication/coordination patterns between agents.  They
are shared by the LLM attribute extractor and protocol template data objects.
"""
from __future__ import annotations

from collections.abc import Iterable

COORDINATION_PATTERNS: tuple[str, ...] = (
    "Subscription",
    "Checkpoint",
    "Cross Validation",
    "Cascade",
    "Backup",
    "Split-and-Merge",
    "Fair Judgement",
    "Interruption Recovery",
    "Confirmation Loop",
    "Frozen Resource",
    "Priority Escort",
    "Progressive Disclosure",
    "Observer",
    "Dynamic Pairing",
    "Token Passing",
    "Opportunity Window",
    "Leader-Follower",
    "Isolate Agent",
    "Consensus",
    "Task Prioritization",
    "Verification",
    "Checkpoint Recovery",
    "Adaptive Yield",
    "Courtesy Yield",
    "Role Switching",
    "Replication",
    "Request-Grant",
    "Producer-Consumer",
    "Barrier Synchronization",
    "Sequential Handoff",
    "Broadcast",
    "Election",
    "Heartbeat Monitoring",
    "Request-Response",
    "Reservation",
    "Queue-Based Scheduling",
    "Exclusive Resource Access",
    "Majority Voting",
    "Retry with Timeout",
    "Publish-Subscribe",
)

_CANONICAL_PATTERN_SET = frozenset(COORDINATION_PATTERNS)

# Canonical logical message/phase sequences for patterns whose communication
# order is deterministic. The extractor may identify the pattern, but it does
# not need to rediscover these stable protocol steps.
COORDINATION_PATTERN_FLOWS: dict[str, tuple[str, ...]] = {
    "Exclusive Resource Access": ("request", "grant", "enter", "exit", "release"),
    "Queue-Based Scheduling": ("enqueue", "dequeue", "complete"),
    "Barrier Synchronization": ("arrive", "wait", "release"),
    "Request-Grant": ("request", "grant"),
    "Producer-Consumer": ("produce", "send", "receive", "consume"),
    "Sequential Handoff": ("work", "handoff", "receive", "continue"),
    "Broadcast": ("broadcast", "receive"),
    "Consensus": ("propose", "vote", "decide"),
}


def normalize_coordination_pattern(value: str) -> str:
    """Return canonical pattern spelling, or raise ValueError."""

    if not isinstance(value, str) or value not in _CANONICAL_PATTERN_SET:
        raise ValueError(f"unknown coordination pattern: {value!r}")
    return value


def normalize_coordination_patterns(values: Iterable[str]) -> list[str]:
    """Validate exact canonical spelling and reject duplicate patterns."""

    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = normalize_coordination_pattern(value)
        if canonical in seen:
            raise ValueError(f"duplicate coordination pattern: {canonical!r}")
        seen.add(canonical)
        normalized.append(canonical)
    return normalized


def is_valid_coordination_pattern(value: str) -> bool:
    """Return True when ``value`` is a known coordination pattern."""

    try:
        normalize_coordination_pattern(value)
    except ValueError:
        return False
    return True


def deterministic_communication_flow(patterns: Iterable[str]) -> list[str]:
    """Expand recognized patterns into one stable, deduplicated flow."""

    flow: list[str] = []
    seen: set[str] = set()
    for pattern in normalize_coordination_patterns(patterns):
        for step in COORDINATION_PATTERN_FLOWS.get(pattern, ()):
            if step not in seen:
                seen.add(step)
                flow.append(step)
    return flow
