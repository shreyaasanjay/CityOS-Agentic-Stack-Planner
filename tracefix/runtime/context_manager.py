"""Session-scoped uploaded JSON context for local TraceFix UI runs."""

from __future__ import annotations

import os
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


DEFAULT_MAX_CONTEXT_BYTES = 5 * 1024 * 1024

_LOCK = threading.RLock()
_CONTEXT: dict[str, Any] | None = None

_DESIGN_TERMS = {
    "build",
    "cityos",
    "coordinate",
    "coordination",
    "create",
    "design",
    "generate",
    "multi-agent",
    "mult-agent",
    "planner",
    "protocol",
    "synthesize",
    "tracefix",
    "verify",
}

_COUNT_PATTERNS = (
    re.compile(r"\bhow\s+many\s+(?P<subject>[\w -]+?)\s+(?:are|is|were|was)\s+(?P<target>[\w -]+)\??", re.I),
    re.compile(r"\bnumber\s+of\s+(?P<target>[\w -]+)", re.I),
    re.compile(r"\bcount\s+(?P<target>[\w -]+)", re.I),
)

_ENTITY_COUNT_TERMS = {"person", "people", "pedestrian", "pedestrians"}
_PREPOSITIONAL_STARTS = (
    "in ",
    "inside ",
    "on ",
    "at ",
    "near ",
    "within ",
    "walking in ",
)


def max_context_bytes() -> int:
    raw = os.getenv("TRACEFIX_CONTEXT_MAX_BYTES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_CONTEXT_BYTES
    except ValueError:
        value = DEFAULT_MAX_CONTEXT_BYTES
    return max(1024, value)


def set_context(data: Any, *, filename: str = "", size_bytes: int | None = None) -> dict[str, Any]:
    """Replace the current uploaded JSON context."""

    record = {
        "filename": filename or "uploaded.json",
        "size_bytes": int(size_bytes or 0),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "data": deepcopy(data),
    }
    with _LOCK:
        global _CONTEXT
        _CONTEXT = record
    return summary(record)


def get_context() -> dict[str, Any] | None:
    with _LOCK:
        return deepcopy(_CONTEXT)


def clear_context() -> None:
    with _LOCK:
        global _CONTEXT
        _CONTEXT = None


def summary(record: dict[str, Any] | None = None) -> dict[str, Any]:
    current = record if record is not None else get_context()
    if not current:
        return {"loaded": False}
    data = current.get("data")
    return {
        "loaded": True,
        "filename": current.get("filename") or "uploaded.json",
        "size_bytes": current.get("size_bytes") or 0,
        "loaded_at": current.get("loaded_at"),
        "root_type": type(data).__name__,
    }


def is_coordination_request(query: str) -> bool:
    text = query.lower()
    return any(term in text for term in _DESIGN_TERMS)


def answer_uploaded_context_query(
    query: str,
    *,
    space_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    """Return a deterministic answer packet from uploaded JSON, when appropriate.

    The uploaded context is runtime evidence only. Coordination/design prompts
    intentionally return None so the existing TraceFix protocol path remains in
    control.
    """

    if is_coordination_request(query):
        return None
    record = get_context()
    if not record:
        return None
    target = _extract_count_target(query)
    if not target:
        return None
    data = record.get("data")
    count, basis = _count_target(data, target)
    filename = record.get("filename") or "uploaded.json"
    if count is None:
        evidence = _evidence_hint(data, target)
        answer_text = (
            f"I found {target} evidence in {filename}, but the uploaded JSON does not contain "
            f"an explicit {target} count field. {evidence}"
        ).strip()
    else:
        answer_text = (
            f"{count} {target}."
            if count != 1
            else f"1 {target.rstrip('s')}."
        )
    return {
        "query_id": f"uploaded_json_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "query": query,
        "mode": "deterministic",
        "model": "uploaded-json-context",
        "api_key_detected": False,
        "status": "answered",
        "route_decision": {
            "route": "single_agent",
            "selected_agent": "uploaded_json_context_agent",
            "selected_tool": "uploaded_json_context_lookup",
            "rationale": "Answered deterministically from uploaded JSON context.",
        },
        "privacy_guardrail": {
            "status": "passed",
            "scope": "uploaded_json_context_only",
        },
        "intent_decomposition": {
            "source": "uploaded_json_context",
            "requested_count": target,
            "space_id": space_id,
            "timestamp": timestamp,
        },
        "tracefix_task_spec": {
            "route": "single_agent",
            "user_query": query,
            "uploaded_context": summary(record),
            "application_goal": {
                "goal_type": "structured_answer",
                "success_condition": "Return a deterministic answer from uploaded JSON context.",
            },
            "candidate_harnesses": ["uploaded_json_context_lookup"],
        },
        "answer_summary": answer_text,
        "chat_answer": answer_text,
        "answer_packet": {
            "answer": {
                "question": query,
                "text": answer_text,
                "answer": answer_text,
                "chatAnswer": answer_text,
                "source": filename,
                "basis": basis,
            },
            "raw_outputs": {
                "uploaded_json_context": summary(record),
                "count_basis": basis,
            },
        },
        "warnings": [],
        "run_dir": "",
        "context": summary(record),
    }


def _extract_count_target(query: str) -> str:
    stripped = query.strip().rstrip("?")
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(stripped)
        if match:
            target = (match.groupdict().get("target") or "").strip().lower()
            subject = (match.groupdict().get("subject") or "").strip().lower()
            subject_norm = _normalize_phrase(subject)
            target_norm = _normalize_phrase(target)
            if subject_norm in _ENTITY_COUNT_TERMS and (
                not target_norm or target_norm in _ENTITY_COUNT_TERMS or target.lower().startswith(_PREPOSITIONAL_STARTS)
            ):
                return _singular_count_term(subject_norm)
            if target:
                return _singular_count_term(target_norm)
            if subject:
                return _singular_count_term(subject_norm)
    return ""


def _normalize_phrase(value: str) -> str:
    value = re.sub(r"\b(right now|currently|today|in the room|the room)\b", "", value, flags=re.I)
    value = re.sub(r"[^a-z0-9_ -]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _singular_count_term(value: str) -> str:
    if value == "people":
        return "person"
    if value == "pedestrians":
        return "pedestrian"
    return value


def _count_target(data: Any, target: str) -> tuple[int | None, list[dict[str, Any]]]:
    explicit = _find_explicit_count(data, target)
    if explicit is not None:
        return explicit, [{"kind": "explicit_count", "target": target}]
    matches: list[dict[str, Any]] = []
    for path, value in _walk(data):
        if isinstance(value, dict) and _dict_matches(value, target):
            matches.append({"path": path, "kind": "object_match"})
    if matches:
        return len(matches), matches[:25]
    scalar_matches = [
        {"path": path, "kind": "scalar_match"}
        for path, value in _walk(data)
        if not isinstance(value, (dict, list)) and target in str(value).lower()
    ]
    if not scalar_matches and target in _ENTITY_COUNT_TERMS and _has_pixel_or_mask_evidence(data, target):
        return None, [{"kind": "pixel_or_mask_evidence_without_count", "target": target}]
    return len(scalar_matches), scalar_matches[:25]


def _find_explicit_count(data: Any, target: str) -> int | None:
    target_key = target.replace(" ", "_")
    for _path, value in _walk(data):
        if not isinstance(value, dict):
            continue
        lowered = {str(key).lower(): item for key, item in value.items()}
        for key, item in lowered.items():
            key_norm = key.replace("-", "_").replace(" ", "_")
            if key_norm in {target_key, f"{target_key}_count", f"num_{target_key}", f"{target_key}s"} and isinstance(item, int):
                return item
            if key_norm in {"activity_counts", "counts", "requested_activity_counts"} and isinstance(item, dict):
                for nested_key, nested_value in item.items():
                    if _normalize_phrase(str(nested_key)) == target and isinstance(nested_value, int):
                        return nested_value
        if target in _ENTITY_COUNT_TERMS:
            entity_keys = (
                "people_count",
                "person_count",
                "pedestrian_count",
                "num_people",
                "num_person",
                "num_pedestrians",
                "occupancy",
                "occupancy_count",
            )
            for key in entity_keys:
                if isinstance(lowered.get(key), int):
                    return lowered[key]
    return None


def _has_pixel_or_mask_evidence(data: Any, target: str) -> bool:
    needles = ("pedestrian_pixels", "person_pixels", "pedestrian_fraction", "person_fraction", "crosswalk_occupied")
    if target not in _ENTITY_COUNT_TERMS:
        return False
    for _path, value in _walk(data):
        if isinstance(value, dict):
            for key in value:
                if any(needle in str(key).lower() for needle in needles):
                    return True
    return False


def _evidence_hint(data: Any, target: str) -> str:
    if target in _ENTITY_COUNT_TERMS and _has_pixel_or_mask_evidence(data, target):
        return "It has segmentation/pixel occupancy fields, which prove presence but do not deterministically encode a person count."
    return "No matching numeric count field was found."


def _dict_matches(value: dict[str, Any], target: str) -> bool:
    searchable_keys = {
        "activity",
        "activities",
        "action",
        "actions",
        "label",
        "labels",
        "state",
        "status",
        "pose",
        "event",
        "events",
    }
    for key, item in value.items():
        key_text = str(key).lower()
        if key_text in searchable_keys or any(part in key_text for part in ("activity", "action", "label", "state")):
            if _contains_target(item, target):
                return True
    return False


def _contains_target(value: Any, target: str) -> bool:
    if isinstance(value, str):
        return target in _normalize_phrase(value)
    if isinstance(value, list):
        return any(_contains_target(item, target) for item in value)
    if isinstance(value, dict):
        return any(_contains_target(item, target) for item in value.values())
    return target in _normalize_phrase(str(value))


def _walk(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
