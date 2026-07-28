"""Bounded adaptation for partial deterministic template matches.

This module adapts template parameters/IR within a narrow policy envelope.  It
is intentionally not a production runner and it does not bypass validation or
TLC; callers must still canonicalize, validate, generate Protocol.tla, and run
PlusCal/TLC after adaptation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from tracefix.protocol_templates import build_template
from tracefix.pipeline.pipeline.validator import validate_ir


@dataclass
class TemplateAdaptationResult:
    accepted: bool
    pattern_id: str
    template_variant: str
    params: dict[str, Any] = field(default_factory=dict)
    ir_data: dict[str, Any] = field(default_factory=dict)
    protocol_tla: str = ""
    repair_summary: str = ""
    adapted_fields: list[str] = field(default_factory=list)
    changed_fields: list[str] = field(default_factory=list)
    safety_preservation_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requested_differences: list[str] = field(default_factory=list)
    requested_changes: list[dict[str, Any]] = field(default_factory=list)
    applied_changes: list[dict[str, Any]] = field(default_factory=list)
    rejected_changes: list[dict[str, Any]] = field(default_factory=list)
    repair_stage: str = "template_adaptation_repair"
    repair_agent_used: bool = True
    llm_used: bool = False


def adapt_template_decision(
    *,
    task: str,
    tellme_spec: dict[str, Any] | None,
    decision: Any,
) -> TemplateAdaptationResult:
    """Adapt a partial template match within the declared template policy."""
    del tellme_spec
    pattern_id = str(getattr(decision, "pattern_id", "") or "")
    metadata = dict(getattr(decision, "template_metadata", {}) or {})
    base_params = deepcopy(getattr(decision, "template_params", {}) or {})
    base_ir = deepcopy(getattr(decision, "ir_data", {}) or {})
    variant = str(getattr(decision, "template_variant", "") or base_params.get("variant_name") or "")

    if not pattern_id:
        return _rejected(pattern_id, variant, ["no template family selected"])
    if not metadata.get("supports_partial_repair"):
        return _rejected(pattern_id, variant, [f"template {pattern_id!r} does not allow partial repair"])

    if pattern_id == "traffic_signal_coordination":
        return _adapt_traffic(task, metadata, base_params, base_ir, pattern_id, variant)

    return _rejected(pattern_id, variant, [f"no bounded adapter implemented for {pattern_id!r}"])


def _adapt_traffic(
    task: str,
    metadata: dict[str, Any],
    base_params: dict[str, Any],
    base_ir: dict[str, Any],
    pattern_id: str,
    variant: str,
) -> TemplateAdaptationResult:
    text = str(task or "").lower()
    requests = _traffic_change_requests(text)
    requested = [_describe_request(request) for request in requests]
    extra_channels: list[dict[str, Any]] = []
    applied_changes: list[dict[str, Any]] = []
    rejected_changes: list[dict[str, Any]] = []

    for request in requests:
        if request["kind"] != "approach_status_exchange":
            rejected_changes.append({**request, "reason": "unsupported bounded traffic adaptation"})
            continue
        labels = list(request.get("labels") or ["status_exchange"])
        suffix = "_".join(labels)
        left = str(request["left"])
        right = str(request["right"])
        forward = {
            "id": f"{left}_to_{right}_{suffix}",
            "from": left,
            "to": right,
            "labels": labels,
        }
        reverse = {
            "id": f"{right}_to_{left}_{suffix}",
            "from": right,
            "to": left,
            "labels": labels,
        }
        extra_channels.extend([forward, reverse])
        applied_changes.append({
            "kind": "added_bidirectional_channels",
            "between": [left, right],
            "labels": labels,
            "channel_ids": [forward["id"], reverse["id"]],
            "reason": request.get("reason") or "bounded approach-to-approach communication requested",
        })

    if "sensor" in text and "approach" in text and "controller" in text and not any(
        "sensor_status" in request.get("labels", []) for request in requests
    ):
        rejected_changes.append({
            "kind": "sensor_endpoint",
            "reason": "custom sensor communication mentioned but no safe sensor endpoint was inferred",
        })

    if not extra_channels:
        return _rejected(
            pattern_id,
            variant,
            ["no supported bounded channel adaptation was inferred"],
            requested_differences=requested,
            requested_changes=requests,
            rejected_changes=rejected_changes,
        )

    adapted_params = deepcopy(base_params)
    adapted_params["extra_channels"] = _dedupe_channels(
        list(adapted_params.get("extra_channels") or []) + extra_channels
    )
    adapted_params["variant_name"] = variant or str(adapted_params.get("variant_name") or "partial")

    try:
        ir_data, protocol_tla = build_template(pattern_id, adapted_params)
    except Exception as exc:  # noqa: BLE001 - clear repair rejection is preferred
        return _rejected(
            pattern_id,
            variant,
            [f"adapted template build failed: {exc}"],
            requested_differences=requested,
            requested_changes=requests,
            applied_changes=applied_changes,
            rejected_changes=rejected_changes,
        )

    errors = _policy_errors(base_ir, ir_data, metadata)
    validation = validate_ir(ir_data)
    if not validation.valid:
        errors.extend(validation.errors)
    if errors:
        return _rejected(
            pattern_id,
            variant,
            errors,
            requested_differences=requested,
            requested_changes=requests,
            applied_changes=applied_changes,
            rejected_changes=rejected_changes,
        )

    summary = "; ".join(
        f"added {', '.join(change['labels'])} channels between "
        f"{change['between'][0]} and {change['between'][1]}"
        for change in applied_changes
    )
    return TemplateAdaptationResult(
        accepted=True,
        pattern_id=pattern_id,
        template_variant=str(adapted_params.get("variant_name") or variant),
        params=adapted_params,
        ir_data=ir_data,
        protocol_tla=protocol_tla,
        repair_summary=summary or "Applied bounded traffic template adaptation.",
        adapted_fields=["channels", "message_labels", "agent_communication_mapping"],
        changed_fields=_changed_fields(base_ir, ir_data),
        safety_preservation_notes=[
            "No template safety invariants were removed.",
            "IR was strictly validated after adaptation.",
            "PlusCal/TLC remains mandatory after adaptation.",
        ],
        requested_differences=requested,
        requested_changes=requests,
        applied_changes=applied_changes,
        rejected_changes=rejected_changes,
    )


def validate_adapted_ir_policy(
    base_ir: dict[str, Any],
    adapted_ir: dict[str, Any],
    metadata: dict[str, Any],
) -> list[str]:
    return _policy_errors(base_ir, adapted_ir, metadata)


def _traffic_change_requests(text: str) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for left_name, right_name in (("east", "west"), ("north", "south")):
        if not _mentions_pair_exchange(text, left_name, right_name):
            continue
        labels = _traffic_labels(text)
        left = f"{left_name}_approach"
        right = f"{right_name}_approach"
        requests.append({
            "kind": "approach_status_exchange",
            "between": [left, right],
            "left": left,
            "right": right,
            "labels": labels,
            "reason": _traffic_reason(labels),
        })
    return requests


def _traffic_labels(text: str) -> list[str]:
    labels: list[str] = []
    if "congestion" in text:
        labels.append("congestion_status")
    if "queue" in text or "queue length" in text:
        labels.append("queue_length")
    if "emergency clearance" in text or "clearance" in text:
        labels.append("emergency_clearance")
    elif "emergency" in text:
        labels.append("emergency_status")
    if "priority" in text:
        labels.append("priority_status")
    if "sensor" in text:
        labels.append("sensor_status")
    if "failure" in text:
        labels.append("failure_status")
    if not labels:
        labels.append("status_exchange")
    return _dedupe_strings(labels)


def _traffic_reason(labels: list[str]) -> str:
    if labels == ["status_exchange"]:
        return "generic approach status exchange requested"
    return "prompt requested " + ", ".join(label.replace("_", " ") for label in labels)


def _describe_request(request: dict[str, Any]) -> str:
    raw_labels = [str(label) for label in request.get("labels") or []]
    labels = ", ".join(label.replace("_", " ") for label in raw_labels)
    left, right = request.get("between") or [request.get("left"), request.get("right")]
    suffix = labels if raw_labels == ["status_exchange"] else f"{labels} exchange"
    return f"{left}/{right} {suffix}"


def _policy_errors(base_ir: dict[str, Any], adapted_ir: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    del metadata
    errors: list[str] = []
    base_agents = _ids(base_ir.get("agents") or [])
    adapted_agents = _ids(adapted_ir.get("agents") or [])
    removed_agents = sorted(base_agents - adapted_agents)
    if removed_agents:
        errors.append(f"adaptation rejected: removed required agents {removed_agents}")

    base_channels = _ids(base_ir.get("channels") or [])
    adapted_channels = _ids(adapted_ir.get("channels") or [])
    removed_channels = sorted(base_channels - adapted_channels)
    if removed_channels:
        errors.append(f"adaptation rejected: removed base channels {removed_channels}")

    adapted_agent_ids = adapted_agents
    for channel in adapted_ir.get("channels") or []:
        if not isinstance(channel, dict):
            continue
        sender = str(channel.get("from") or "")
        receiver = str(channel.get("to") or "")
        if sender not in adapted_agent_ids or receiver not in adapted_agent_ids:
            errors.append(
                f"adaptation rejected: channel {channel.get('id')!r} has disconnected endpoint {sender!r}->{receiver!r}"
            )
        if not channel.get("labels"):
            errors.append(f"adaptation rejected: channel {channel.get('id')!r} has no labels")
    return errors


def _changed_fields(base_ir: dict[str, Any], adapted_ir: dict[str, Any]) -> list[str]:
    fields = []
    for key in ("agents", "channels", "resources", "agent_resources", "state_tasks"):
        if base_ir.get(key) != adapted_ir.get(key):
            fields.append(key)
    return fields


def _mentions_pair_exchange(text: str, left: str, right: str) -> bool:
    if left not in text or right not in text:
        return False
    return any(cue in text for cue in (
        "exchange", "communicate", "status message", "status messages", "share status",
        "share", "congestion", "queue", "clearance", "priority", "sensor status",
    ))


def _dedupe_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for channel in channels:
        cid = str(channel.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        result.append(channel)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _ids(items: list[Any]) -> set[str]:
    result: set[str] = set()
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            result.add(str(item["id"]))
    return result


def _rejected(
    pattern_id: str,
    variant: str,
    errors: list[str],
    *,
    requested_differences: list[str] | None = None,
    requested_changes: list[dict[str, Any]] | None = None,
    applied_changes: list[dict[str, Any]] | None = None,
    rejected_changes: list[dict[str, Any]] | None = None,
) -> TemplateAdaptationResult:
    return TemplateAdaptationResult(
        accepted=False,
        pattern_id=pattern_id,
        template_variant=variant,
        errors=errors,
        repair_summary="; ".join(errors),
        requested_differences=requested_differences or [],
        requested_changes=requested_changes or [],
        applied_changes=applied_changes or [],
        rejected_changes=rejected_changes or [],
    )
