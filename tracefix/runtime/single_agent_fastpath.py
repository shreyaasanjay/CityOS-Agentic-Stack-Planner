"""Conservative deterministic IR generation for simple single-agent tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_STRUCTURED_MARKER = "Structured task specification:"
_COORDINATION_PATTERN = re.compile(
    r"\b("
    r"multi[- ]agent|multiple agents?|coordinate|coordination|collaborat(?:e|ion)|"
    r"approval|approve|review workflow|quorum|vote|voting|negotiate|negotiation|"
    r"authori[sz](?:e|ation)|handoff|consensus|peer[- ]to[- ]peer|"
    r"inter[- ]agent|deployment|cityos apps?|dockerized apps?"
    r")\b",
    re.IGNORECASE,
)
_MULTI_SOURCE_PATTERN = re.compile(
    r"\b("
    r"cross[- ]check|cross[- ]reference|compare|correlate|reconcile|"
    r"against badge|badge logs?|meeting attendance|multiple (?:data )?sources|"
    r"two (?:data )?sources|several (?:data )?sources"
    r")\b",
    re.IGNORECASE,
)
_MULTI_STEP_PATTERN = re.compile(
    r"(?:\bthen\b|\bafter that\b|\bnext,\b|;)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FastPathDecision:
    considered: bool
    eligible: bool
    reason: str
    task_text: str = ""
    agent_id: str = ""
    structured_input: bool = False


def assess_single_agent_fast_path(task: str) -> FastPathDecision:
    """Return a fail-closed eligibility decision for deterministic IR."""
    text = str(task or "").strip()
    if not text:
        return FastPathDecision(True, False, "task is empty")

    structured = _extract_structured_task(text)
    if structured is not None:
        query = str(structured.get("user_query") or "").strip()
        if not query:
            return FastPathDecision(
                True, False, "structured TeLLMe task has no user_query",
                structured_input=True,
            )
        route = str(structured.get("route") or "").strip().lower()
        if route != "single_agent":
            return FastPathDecision(
                True, False, f"TeLLMe route is {route or 'unspecified'}, not single_agent",
                structured_input=True,
            )
        rejection = _text_rejection(query)
        if rejection:
            return FastPathDecision(
                True, False, rejection, structured_input=True,
            )

        modalities = _string_list(structured.get("required_modalities"))
        if len(modalities) > 1:
            return FastPathDecision(
                True, False, "task requires multiple sensor modalities",
                structured_input=True,
            )

        evidence = structured.get("evidence_plan")
        evidence = evidence if isinstance(evidence, dict) else {}
        if len(_string_list(evidence.get("primary_evidence"))) > 1:
            return FastPathDecision(
                True, False, "task requires multiple primary evidence sources",
                structured_input=True,
            )
        if _string_list(evidence.get("supporting_evidence")):
            return FastPathDecision(
                True, False, "task requires supporting evidence reconciliation",
                structured_input=True,
            )
        if _string_list(evidence.get("conflicting_evidence_checks")):
            return FastPathDecision(
                True, False, "task requires conflicting-evidence checks",
                structured_input=True,
            )

        data_harnesses = [
            harness
            for harness in _string_list(structured.get("candidate_harnesses"))
            if not _is_output_harness(harness)
        ]
        if len(data_harnesses) > 1:
            return FastPathDecision(
                True, False, "task requires multiple data/tool harnesses",
                structured_input=True,
            )

        goal = structured.get("application_goal")
        goal_type = str(goal.get("goal_type") or "") if isinstance(goal, dict) else ""
        return FastPathDecision(
            True,
            True,
            "structured TeLLMe task is a single-agent, single-source operation",
            task_text=query,
            agent_id=_agent_id(query, goal_type),
            structured_input=True,
        )

    rejection = _text_rejection(text)
    if rejection:
        return FastPathDecision(True, False, rejection)
    if len(text) > 500:
        return FastPathDecision(True, False, "plain task is too long for safe classification")
    if len([line for line in text.splitlines() if line.strip()]) > 2:
        return FastPathDecision(True, False, "plain task contains multiple instruction lines")
    return FastPathDecision(
        True,
        True,
        "plain task has one operation and no coordination or multi-source signals",
        task_text=text,
        agent_id=_agent_id(text, ""),
    )


def generate_single_agent_ir(decision: FastPathDecision) -> dict[str, Any]:
    """Generate the minimal schema-valid topology for an eligible decision."""
    if not decision.eligible or not decision.agent_id or not decision.task_text:
        raise ValueError("single-agent IR requires an eligible fast-path decision")
    return {
        "agents": [{"id": decision.agent_id}],
        "resources": [],
        "channels": [],
        "state_tasks": {
            f"{decision.agent_id}_start": decision.task_text,
        },
    }


def build_single_agent_generation_input(
    task: str,
    task_spec: dict[str, Any] | None = None,
) -> FastPathDecision:
    """Build deterministic generation input after canonical routing selected one agent."""

    spec = task_spec if isinstance(task_spec, dict) else {}
    task_text = str(
        spec.get("user_query")
        or spec.get("task")
        or spec.get("description")
        or task
        or ""
    ).strip()
    if not task_text:
        raise ValueError("single-agent generation requires non-empty task text")
    goal = spec.get("application_goal")
    goal_type = str(goal.get("goal_type") or "") if isinstance(goal, dict) else ""
    return FastPathDecision(
        considered=True,
        eligible=True,
        reason="canonical attribute routing selected single_agent_generation",
        task_text=task_text,
        agent_id=_agent_id(task_text, goal_type),
        structured_input=bool(spec),
    )


def render_verified_runtime_prompt(
    decision: FastPathDecision,
    cityos_plan: dict[str, Any],
) -> str:
    """Render a minimal runtime prompt from a verified intermediary plan."""
    verification = cityos_plan.get("verification")
    if not isinstance(verification, dict) or not verification.get("production_ready"):
        raise ValueError("runtime prompt requires a production-ready verified plan")
    agents = cityos_plan.get("agents")
    agent_names = {
        str(agent.get("name"))
        for agent in agents
        if isinstance(agent, dict) and agent.get("name")
    } if isinstance(agents, list) else set()
    if decision.agent_id not in agent_names:
        raise ValueError(
            f"verified plan does not contain fast-path agent {decision.agent_id}"
        )

    protocol = cityos_plan.get("protocol")
    transitions = protocol.get("allowed_transitions", []) \
        if isinstance(protocol, dict) else []
    agent_transitions = [
        transition
        for transition in transitions
        if isinstance(transition, dict)
        and (
            transition.get("agent") == decision.agent_id
            or str(transition.get("from", "")).startswith(f"{decision.agent_id}_")
        )
    ]
    state_lines = [
        f"- `{transition.get('from')}` -> `{transition.get('to')}`"
        for transition in agent_transitions
        if transition.get("from") and transition.get("to")
    ] or ["- Follow the state sequence declared in `spec/states.json`."]

    return "\n".join([
        f"# {decision.agent_id} Runtime Workflow",
        "",
        "## Verified Task",
        decision.task_text,
        "",
        "## Verified State Flow",
        *state_lines,
        "",
        "## Runtime Rules",
        "1. Execute only the verified task above.",
        "2. Use only context, tools, and permissions attached by CityOS.",
        "3. Do not access raw sensors or undeclared resources directly.",
        "4. Produce one bounded result with confidence, evidence references, and limitations when available.",
        "5. Follow `spec/states.json` and signal completion after producing the result.",
        "",
        "This prompt was generated from `spec/cityos_module_plan.json` after PlusCal/TLC verification.",
        "",
    ])


def _extract_structured_task(text: str) -> dict[str, Any] | None:
    marker_index = text.find(_STRUCTURED_MARKER)
    if marker_index < 0:
        return None
    object_index = text.find("{", marker_index + len(_STRUCTURED_MARKER))
    if object_index < 0:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[object_index:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _text_rejection(text: str) -> str | None:
    match = _COORDINATION_PATTERN.search(text)
    if match:
        return f"coordination signal detected: {match.group(0)}"
    match = _MULTI_SOURCE_PATTERN.search(text)
    if match:
        return f"multi-source signal detected: {match.group(0)}"
    match = _MULTI_STEP_PATTERN.search(text)
    if match:
        return f"multi-step signal detected: {match.group(0)}"
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_output_harness(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("answer", "output", "response", "synthesis"))


def _agent_id(task: str, goal_type: str) -> str:
    lowered = f"{goal_type} {task}".lower()
    if "occupancy" in lowered or "people" in lowered or "person count" in lowered:
        return "OCCUPANCY_ANALYZER"
    if "email" in lowered or "extract" in lowered:
        return "EXTRACTION_AGENT"
    if "summar" in lowered:
        return "SUMMARY_AGENT"
    if "root cause" in lowered or "error log" in lowered:
        return "LOG_ANALYZER"
    return "TASK_AGENT"
