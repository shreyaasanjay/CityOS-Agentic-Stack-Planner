"""Deterministic traffic signal coordination protocol template.

This template is parameterized by intersection shape.  It keeps the model out
of fragile PlusCal syntax for common traffic-control topologies while still
falling back to OpenCode when the requested traffic variant is ambiguous or
unsupported.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Any

from tracefix.pipeline.pipeline.pluscal_generator import (
    _agent_id_to_const,
    _sanitize_id,
)

PATTERN_ID = "traffic_signal_coordination"
DESCRIPTION = (
    "Intersection approaches submit traffic requests to a signal controller. "
    "The controller permits bounded phases, can prioritize emergency vehicles, "
    "can coordinate pedestrian crossings, and terminates in all-red on failure."
)

TEMPLATE_METADATA = {
    "pattern_id": PATTERN_ID,
    "description": DESCRIPTION,
    "shape": "parameterized",
    "supported_variants": [
        "standard_four_way",
        "four_way_emergency",
        "four_way_pedestrian",
        "four_way_emergency_pedestrian",
        "n_approach",
        "n_approach_emergency",
        "n_approach_pedestrian",
        "n_approach_emergency_pedestrian",
    ],
    "required_inputs": [
        "approach_ids or approach_count",
        "controller_id",
        "include_emergency_detector",
        "include_pedestrian_agent",
    ],
    "generated_agent_pattern": (
        "signal_controller + one approach agent per selected approach + optional "
        "emergency_detector + optional pedestrian_crossing_agent"
    ),
    "generated_channel_pattern": (
        "bidirectional controller/approach command channels, plus optional "
        "emergency and pedestrian request/command channels"
    ),
    "generated_resource_pattern": (
        "intersection_green_lock, optional emergency_override_lock, optional "
        "pedestrian_phase_lock"
    ),
    "family": "traffic_control",
    "mode": "parameterized",
    "supports_partial_repair": True,
    "adaptable_sections": [
        "channels",
        "message_labels",
        "resources",
        "timeouts",
        "failure_transitions",
        "agent_communication_mapping",
    ],
    "forbidden_repair_sections": [
        "safety_invariants",
        "verification_requirements",
        "module_header",
        "algorithm_wrapper",
    ],
    "safety_invariants": [
        "NoConflictingGreens",
        "AllRedOnCompletion",
        "NoOrphanLocks",
        "ChannelsDrained",
        "ChannelBound",
    ],
}

_TRAFFIC_TERMS = (
    "traffic",
    "intersection",
    "signal",
    "green light",
    "four-way",
    "four way",
    "approach",
)
_SAFETY_TERMS = (
    "conflicting green",
    "prevent conflict",
    "safely",
    "safe",
    "all-red",
    "all red",
)
_CONTROL_TERMS = (
    "coordinate",
    "management",
    "controller",
    "emergency vehicle",
    "prioritize emergency",
    "pedestrian",
)


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    del agent_count_hint, keywords
    text = task_lower.lower()
    traffic = sum(term in text for term in _TRAFFIC_TERMS)
    safety = sum(term in text for term in _SAFETY_TERMS)
    control = sum(term in text for term in _CONTROL_TERMS)
    if traffic >= 3 and safety >= 1 and control >= 1:
        return 0.98
    if traffic >= 2 and safety >= 1 and control >= 1:
        return 0.90
    if traffic >= 2 and (safety >= 1 or control >= 2):
        return 0.78
    if "traffic" in text and "controller" in text and "approach" in text:
        if any(cue in text for cue in (
            "exchange", "communicate", "custom", "sensor", "status message",
            "share", "congestion", "queue", "clearance", "priority",
        )):
            return 0.62
    return 0.0


def build_template(params: dict) -> tuple[dict, str]:
    approach_ids = _approach_ids(params)
    controller_id = _safe_id(params.get("controller_id") or "signal_controller")
    if controller_id in approach_ids:
        raise ValueError("traffic controller must be distinct from approach agents")

    include_emergency = bool(params.get("include_emergency_detector"))
    include_pedestrian = bool(params.get("include_pedestrian_agent"))
    emergency_id = _safe_id(params.get("emergency_detector_id") or "emergency_detector")
    pedestrian_id = _safe_id(params.get("pedestrian_agent_id") or "pedestrian_crossing_agent")
    extra_ids = [agent_id for agent_id, enabled in (
        (emergency_id, include_emergency),
        (pedestrian_id, include_pedestrian),
    ) if enabled]
    if len(set([controller_id, *approach_ids, *extra_ids])) != 1 + len(approach_ids) + len(extra_ids):
        raise ValueError("traffic template agent ids must be distinct")

    channel_bound = int(params.get("channel_bound", 3))
    variant_name = str(params.get("variant_name") or _variant_name(approach_ids, include_emergency, include_pedestrian))
    status_label = _safe_label(params.get("status_label") or "status_report")
    timeout_label = _safe_label(params.get("timeout_label") or "timeout")
    failure_label = _safe_label(params.get("failure_label") or "failure_notice")
    grant_label = _safe_label(params.get("grant_label") or "grant_green")
    hold_label = _safe_label(params.get("hold_label") or "set_red")
    stop_label = _safe_label(params.get("stop_label") or "all_red")
    primary_lock_id = _safe_id(params.get("primary_lock_id") or "intersection_green_lock")
    phase_var_suffix = _safe_id(params.get("phase_var_suffix") or "green")

    agents = [{"id": approach_id} for approach_id in approach_ids]
    if include_emergency:
        agents.append({"id": emergency_id})
    if include_pedestrian:
        agents.append({"id": pedestrian_id})
    agents.append({"id": controller_id})

    inbound_channels = [
        {
            "id": f"{approach_id}_to_{controller_id}",
            "from": approach_id,
            "to": controller_id,
            "labels": [status_label, timeout_label, failure_label],
        }
        for approach_id in approach_ids
    ]
    outbound_channels = [
        {
            "id": f"{controller_id}_to_{approach_id}",
            "from": controller_id,
            "to": approach_id,
            "labels": [grant_label, hold_label, stop_label],
        }
        for approach_id in approach_ids
    ]
    channels = inbound_channels + outbound_channels
    if include_emergency:
        channels.append({
            "id": f"{emergency_id}_to_{controller_id}",
            "from": emergency_id,
            "to": controller_id,
            "labels": ["emergency_detected", "emergency_cleared"],
        })
    if include_pedestrian:
        channels.extend([
            {
                "id": f"{pedestrian_id}_to_{controller_id}",
                "from": pedestrian_id,
                "to": controller_id,
                "labels": ["pedestrian_request", "pedestrian_clear"],
            },
            {
                "id": f"{controller_id}_to_{pedestrian_id}",
                "from": controller_id,
                "to": pedestrian_id,
                "labels": ["crossing_grant", "crossing_hold"],
            },
        ])
    channels.extend(_extra_channels(params, {agent["id"] for agent in agents}, {channel["id"] for channel in channels}))

    resources = [{"id": primary_lock_id, "type": "Lock"}]
    if include_emergency:
        resources.append({"id": "emergency_override_lock", "type": "Lock"})
    if include_pedestrian:
        resources.append({"id": "pedestrian_phase_lock", "type": "Lock"})

    controller_resources = [resource["id"] for resource in resources]
    ir_data = {
        "agents": agents,
        "resources": resources,
        "channels": channels,
        "agent_resources": {controller_id: controller_resources},
        "state_tasks": _state_tasks(
            approach_ids,
            controller_id,
            include_emergency=include_emergency,
            emergency_id=emergency_id,
            include_pedestrian=include_pedestrian,
            pedestrian_id=pedestrian_id,
            approach_task=str(
                params.get("approach_task")
                or "Submit traffic or failure status for {agent_id}."
            ),
            controller_coordinate_task=str(
                params.get("controller_coordinate_task")
                or "Coordinate safe signal phases and enter all-red on failure."
            ),
            controller_command_task=str(
                params.get("controller_command_task")
                or "Publish verified signal commands to controlled approaches."
            ),
        ),
    }
    return ir_data, _render_protocol(
        agents=agents,
        channels=channels,
        resources=resources,
        approach_ids=approach_ids,
        controller_id=controller_id,
        include_emergency=include_emergency,
        emergency_id=emergency_id,
        include_pedestrian=include_pedestrian,
        pedestrian_id=pedestrian_id,
        channel_bound=channel_bound,
        variant_name=variant_name,
        status_label=status_label,
        stop_label=stop_label,
        primary_lock_id=primary_lock_id,
        phase_var_suffix=phase_var_suffix,
    )



def _extra_channels(params: dict, agent_ids: set[str], existing_channel_ids: set[str]) -> list[dict[str, Any]]:
    extras: list[dict[str, Any]] = []
    for index, raw in enumerate(params.get("extra_channels") or []):
        if not isinstance(raw, dict):
            continue
        sender = _safe_id(raw.get("from") or raw.get("sender") or "")
        receiver = _safe_id(raw.get("to") or raw.get("receiver") or "")
        if sender not in agent_ids or receiver not in agent_ids or sender == receiver:
            continue
        channel_id = _safe_id(raw.get("id") or f"{sender}_to_{receiver}_status")
        if channel_id in existing_channel_ids or channel_id in {ch["id"] for ch in extras}:
            continue
        labels = raw.get("labels") or ["status_exchange"]
        labels = [_safe_label(label) for label in labels if str(label).strip()]
        extras.append({
            "id": channel_id,
            "from": sender,
            "to": receiver,
            "labels": labels or ["status_exchange"],
        })
    return extras


def _safe_label(value: object) -> str:
    label = re.sub(r"[^a-z0-9_]", "_", str(value).lower().strip()).strip("_")
    return label or "status_exchange"

def _approach_ids(params: dict) -> list[str]:
    raw_ids = params.get("approach_ids")
    if raw_ids:
        approach_ids = [_safe_id(value) for value in raw_ids]
    else:
        count = int(params.get("approach_count") or 4)
        approach_ids = _default_approaches(count)
    if len(approach_ids) < 2 or len(approach_ids) > 8:
        raise ValueError("traffic_signal_coordination supports 2 to 8 approaches")
    if len(set(approach_ids)) != len(approach_ids):
        raise ValueError("traffic_signal_coordination requires distinct approaches")
    return approach_ids


def _default_approaches(count: int) -> list[str]:
    if count == 4:
        return ["north_approach", "east_approach", "south_approach", "west_approach"]
    return [f"approach_{index}" for index in range(1, count + 1)]


def _variant_name(approach_ids: list[str], include_emergency: bool, include_pedestrian: bool) -> str:
    prefix = "standard_four_way" if len(approach_ids) == 4 and approach_ids == _default_approaches(4) else "n_approach"
    suffixes = []
    if include_emergency:
        suffixes.append("emergency")
    if include_pedestrian:
        suffixes.append("pedestrian")
    if not suffixes:
        return prefix
    if prefix == "standard_four_way":
        return "four_way_" + "_".join(suffixes)
    return prefix + "_" + "_".join(suffixes)


def _state_tasks(
    approach_ids: list[str],
    controller_id: str,
    *,
    include_emergency: bool,
    emergency_id: str,
    include_pedestrian: bool,
    pedestrian_id: str,
    approach_task: str,
    controller_coordinate_task: str,
    controller_command_task: str,
) -> dict[str, str]:
    tasks = {
        **{
            f"{approach_id}_request": approach_task.format(agent_id=approach_id)
            for approach_id in approach_ids
        },
        f"{controller_id}_coordinate": controller_coordinate_task,
        f"{controller_id}_command": controller_command_task,
    }
    if include_emergency:
        tasks[f"{emergency_id}_detect"] = "Report emergency vehicle priority requests to the controller."
        tasks[f"{controller_id}_emergency"] = "Apply emergency override and return to all-red when needed."
    if include_pedestrian:
        tasks[f"{pedestrian_id}_request"] = "Request a pedestrian crossing phase."
        tasks[f"{controller_id}_pedestrian"] = "Coordinate pedestrian crossing only after vehicle signals are held red."
    return tasks


def _render_protocol(
    *,
    agents: list[dict[str, Any]],
    channels: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    approach_ids: list[str],
    controller_id: str,
    include_emergency: bool,
    emergency_id: str,
    include_pedestrian: bool,
    pedestrian_id: str,
    channel_bound: int,
    variant_name: str,
    status_label: str,
    stop_label: str,
    primary_lock_id: str,
    phase_var_suffix: str,
) -> str:
    constants = {agent["id"]: _agent_id_to_const(agent["id"]) for agent in agents}
    channel_vars = {channel["id"]: _sanitize_id(channel["id"]) for channel in channels}
    all_consts = "{" + ", ".join(constants.values()) + "}"
    lock_type_set = "{" + ", ".join(constants.values()) + ', "FREE"}'
    green_vars = {approach_id: _phase_var(approach_id, phase_var_suffix) for approach_id in approach_ids}

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        "CONSTANTS " + ", ".join(constants.values()),
        "",
        f"\\* traffic_signal_coordination variant: {variant_name}",
        "(* --algorithm Protocol {",
        "variables",
    ]
    for channel in channels:
        lines.append(
            f"  {channel_vars[channel['id']]} = <<>>; "
            f"\\* {channel['from']} -> {channel['to']}, labels: {channel['labels']}"
        )
    for resource in resources:
        lines.append(f'  {_sanitize_id(resource["id"])} = "FREE";')
    for green_var in green_vars.values():
        lines.append(f"  {green_var} = FALSE;")
    lines.extend([
        "",
        "macro send(ch, msg) {",
        "  ch := Append(ch, msg);",
        "}",
        "",
        "macro receive(ch, var) {",
        "  await Len(ch) > 0;",
        "  var := Head(ch);",
        "  ch := Tail(ch);",
        "}",
        "",
        "macro acquire_lock(lock) {",
        '  await lock = "FREE";',
        "  lock := self;",
        "}",
        "",
        "macro release_lock(lock) {",
        '  lock := "FREE";',
        "}",
        "",
    ])

    for approach_id in approach_ids:
        approach_var = _sanitize_id(approach_id)
        inbound_var = channel_vars[f"{approach_id}_to_{controller_id}"]
        outbound_var = channel_vars[f"{controller_id}_to_{approach_id}"]
        lines.extend([
            f"fair process ({approach_var}_proc \\in {{{constants[approach_id]}}})",
            'variables msg = "";',
            "{",
            f"  {approach_var}_request:",
            f'    send({inbound_var}, "{status_label}");',
            f"  {approach_var}_await_command:",
            f"    receive({outbound_var}, msg);",
            f"  {approach_var}_done:",
            "    skip;",
            "}",
            "",
        ])

    if include_emergency:
        emergency_var = _sanitize_id(emergency_id)
        emergency_channel = channel_vars[f"{emergency_id}_to_{controller_id}"]
        lines.extend([
            f"fair process ({emergency_var}_proc \\in {{{constants[emergency_id]}}})",
            "{",
            f"  {emergency_var}_detect:",
            f'    send({emergency_channel}, "emergency_detected");',
            f"  {emergency_var}_done:",
            "    skip;",
            "}",
            "",
        ])

    if include_pedestrian:
        pedestrian_var = _sanitize_id(pedestrian_id)
        pedestrian_to_controller = channel_vars[f"{pedestrian_id}_to_{controller_id}"]
        controller_to_pedestrian = channel_vars[f"{controller_id}_to_{pedestrian_id}"]
        lines.extend([
            f"fair process ({pedestrian_var}_proc \\in {{{constants[pedestrian_id]}}})",
            'variables msg = "";',
            "{",
            f"  {pedestrian_var}_request:",
            f'    send({pedestrian_to_controller}, "pedestrian_request");',
            f"  {pedestrian_var}_await_command:",
            f"    receive({controller_to_pedestrian}, msg);",
            f"  {pedestrian_var}_done:",
            "    skip;",
            "}",
            "",
        ])

    controller_var = _sanitize_id(controller_id)
    lines.extend([
        f"fair process ({controller_var}_proc \\in {{{constants[controller_id]}}})",
        'variables msg = "";',
        "{",
    ])
    receive_index = 1
    for approach_id in approach_ids:
        lines.extend([
            f"  {controller_var}_receive_{receive_index}:",
            f"    receive({channel_vars[f'{approach_id}_to_{controller_id}']}, msg);",
        ])
        receive_index += 1
    if include_emergency:
        lines.extend([
            f"  {controller_var}_receive_emergency:",
            f"    receive({channel_vars[f'{emergency_id}_to_{controller_id}']}, msg);",
        ])
    if include_pedestrian:
        lines.extend([
            f"  {controller_var}_receive_pedestrian:",
            f"    receive({channel_vars[f'{pedestrian_id}_to_{controller_id}']}, msg);",
        ])

    lines.extend([
        f"  {controller_var}_acquire_intersection:",
        f"    acquire_lock({_sanitize_id(primary_lock_id)});",
        f"  {controller_var}_safe_phase:",
    ])
    for index, approach_id in enumerate(approach_ids):
        lines.append(f"    {green_vars[approach_id]} := {'TRUE' if index == 0 else 'FALSE'};")
    lines.append(f"  {controller_var}_transition_all_red:")
    for approach_id in approach_ids:
        lines.append(f"    {green_vars[approach_id]} := FALSE;")
    lines.extend([
        f"  {controller_var}_release_intersection:",
        f"    release_lock({_sanitize_id(primary_lock_id)});",
    ])

    if include_emergency:
        lines.extend([
            f"  {controller_var}_acquire_emergency_override:",
            "    acquire_lock(emergency_override_lock);",
            f"  {controller_var}_emergency_all_red:",
        ])
        for approach_id in approach_ids:
            lines.append(f"    {green_vars[approach_id]} := FALSE;")
        lines.extend([
            f"  {controller_var}_emergency_priority:",
            f"    {green_vars[approach_ids[0]]} := TRUE; \\* bounded emergency priority phase",
            f"  {controller_var}_release_emergency_override:",
            "    release_lock(emergency_override_lock);",
        ])

    if include_pedestrian:
        lines.extend([
            f"  {controller_var}_acquire_pedestrian_phase:",
            "    acquire_lock(pedestrian_phase_lock);",
            f"  {controller_var}_pedestrian_all_red:",
        ])
        for approach_id in approach_ids:
            lines.append(f"    {green_vars[approach_id]} := FALSE;")
        lines.extend([
            f"  {controller_var}_pedestrian_command:",
            f'    send({channel_vars[f"{controller_id}_to_{pedestrian_id}"]}, "crossing_grant");',
            f"  {controller_var}_release_pedestrian_phase:",
            "    release_lock(pedestrian_phase_lock);",
        ])

    lines.append(f"  {controller_var}_failure_all_red:")
    for approach_id in approach_ids:
        lines.append(f"    {green_vars[approach_id]} := FALSE;")
    for index, approach_id in enumerate(approach_ids, start=1):
        lines.extend([
            f"  {controller_var}_command_{index}:",
            f'    send({channel_vars[f"{controller_id}_to_{approach_id}"]}, "{stop_label}");',
        ])
    lines.extend([
        f"  {controller_var}_done:",
        "    skip;",
        "}",
        "",
        "} *)",
        "",
        f'AllDone == \\A p \\in {all_consts}: pc[p] = "Done"',
        "",
        "TypeInvariant ==",
        f"  /\\ \\A p \\in {all_consts}: pc[p] \\in STRING",
    ])
    for resource in resources:
        lines.append(f"  /\\ {_sanitize_id(resource['id'])} \\in {lock_type_set}")
    for green_var in green_vars.values():
        lines.append(f"  /\\ {green_var} \\in BOOLEAN")
    for channel_var in channel_vars.values():
        lines.append(f"  /\\ {channel_var} \\in Seq(STRING)")

    conflict_terms = [
        f"~({green_vars[left]} /\\ {green_vars[right]})"
        for left, right in combinations(approach_ids, 2)
    ] or ["TRUE"]
    drained = " /\\ ".join(f"Len({channel_var}) = 0" for channel_var in channel_vars.values())
    bounded = " /\\ ".join(f"Len({channel_var}) <= {channel_bound}" for channel_var in channel_vars.values())
    lock_free = " /\\ ".join(f'{_sanitize_id(resource["id"])} = "FREE"' for resource in resources)
    lines.extend([
        "",
        "NoConflictingGreens ==",
        "  " + " /\\ ".join(conflict_terms),
        "",
        "AllRedOnCompletion ==",
        "  AllDone =>",
        "    " + " /\\ ".join(f"~{green_var}" for green_var in green_vars.values()),
        "",
        "NoOrphanLocks ==",
        f"  AllDone => ({lock_free})",
        "",
        "ChannelsDrained ==",
        f"  AllDone => ({drained})",
        "",
        "ChannelBound ==",
        f"  {bounded}",
        "",
        "====",
    ])
    return "\n".join(lines)


def _green_var(approach_id: str) -> str:
    return _phase_var(approach_id, "green")


def _phase_var(approach_id: str, suffix: str) -> str:
    parts = [part for part in _sanitize_id(approach_id).split("_") if part and part != "approach"]
    base = parts[0] if parts else "approach"
    if base.startswith("approach") and base[-1:].isdigit():
        base = base.replace("approach", "approach_")
    return _sanitize_id(base + "_" + suffix)


def _safe_id(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9_]", "_", str(value).lower().strip()).strip("_")
    if not normalized:
        return "agent"
    if normalized[0].isdigit():
        normalized = f"agent_{normalized}"
    return normalized
