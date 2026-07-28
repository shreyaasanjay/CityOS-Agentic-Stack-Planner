"""Execute deterministically selected procedures without reopening selection."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tracefix.pipeline.pipeline.pluscal_generator import generate_tlc_config
from tracefix.pipeline.pipeline.validator import normalize_ir, validate_ir
from tracefix.protocol_templates import build_template
from tracefix.runtime.procedure_prompt import ProcedureExecutionContext


class ProcedureExecutionError(RuntimeError):
    """The fixed procedure could not produce valid downstream artifacts."""


@dataclass(frozen=True)
class ExactReuseInstantiationResult:
    template_id: str
    parameters: dict[str, Any]
    artifact_paths: tuple[Path, ...]


ParameterizedReuseInstantiationResult = ExactReuseInstantiationResult


def instantiate_exact_reuse(
    workspace: Path,
    context: ProcedureExecutionContext,
) -> ExactReuseInstantiationResult:
    """Instantiate a built-in exact-reuse template with no model call."""

    if context.selected_procedure != "exact_reuse":
        raise ProcedureExecutionError("exact instantiation requires selected_procedure=exact_reuse")
    template_id = context.selected_template_id
    if not template_id:
        raise ProcedureExecutionError("exact reuse decision has no selected_template_id")

    parameters = _default_template_parameters(template_id, context.extracted_attributes)
    try:
        raw_ir, protocol_tla = build_template(template_id, parameters)
        ir_data = normalize_ir(raw_ir)
        validation = validate_ir(ir_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProcedureExecutionError(
            f"selected template is not deterministically executable; "
            f"instantiation failed for {template_id}: {exc}"
        ) from exc
    if not validation.valid:
        raise ProcedureExecutionError(
            f"deterministic instantiation produced invalid IR for {template_id}: "
            + "; ".join(validation.errors)
        )

    spec_dir = Path(workspace) / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    ir_path = spec_dir / "ir.json"
    tla_path = spec_dir / "Protocol.tla"
    cfg_path = spec_dir / "Protocol.cfg"
    ir_path.write_text(json.dumps(ir_data, indent=2) + "\n", encoding="utf-8")
    tla_path.write_text(protocol_tla.rstrip() + "\n", encoding="utf-8")
    cfg_path.write_text(generate_tlc_config(ir_data).rstrip() + "\n", encoding="utf-8")
    return ExactReuseInstantiationResult(
        template_id=template_id,
        parameters=parameters,
        artifact_paths=(ir_path, tla_path, cfg_path),
    )


def instantiate_parameterized_reuse(
    workspace: Path,
    context: ProcedureExecutionContext,
) -> ParameterizedReuseInstantiationResult:
    """Instantiate an allowed builder parameterization without an LLM call."""

    if context.selected_procedure != "parameterized_reuse":
        raise ProcedureExecutionError(
            "parameterized instantiation requires selected_procedure=parameterized_reuse"
        )
    template_id = context.selected_template_id
    if not template_id:
        raise ProcedureExecutionError("parameterized reuse decision has no selected_template_id")
    parameters = _parameterized_template_parameters(template_id, context.extracted_attributes)
    try:
        raw_ir, protocol_tla = build_template(template_id, parameters)
        ir_data = normalize_ir(raw_ir)
        validation = validate_ir(ir_data)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProcedureExecutionError(
            f"selected template cannot apply the deterministic parameterization: {template_id}: {exc}"
        ) from exc
    if not validation.valid:
        raise ProcedureExecutionError(
            f"deterministic parameterization produced invalid IR for {template_id}: "
            + "; ".join(validation.errors)
        )
    _verify_parameterized_counts(ir_data, context)
    spec_dir = Path(workspace) / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    ir_path = spec_dir / "ir.json"
    tla_path = spec_dir / "Protocol.tla"
    cfg_path = spec_dir / "Protocol.cfg"
    ir_path.write_text(json.dumps(ir_data, indent=2) + "\n", encoding="utf-8")
    tla_path.write_text(protocol_tla.rstrip() + "\n", encoding="utf-8")
    cfg_path.write_text(generate_tlc_config(ir_data).rstrip() + "\n", encoding="utf-8")
    return ParameterizedReuseInstantiationResult(
        template_id=template_id,
        parameters=parameters,
        artifact_paths=(ir_path, tla_path, cfg_path),
    )


def _parameterized_template_parameters(template_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
    parameters = _default_template_parameters(template_id, attributes)
    count = attributes.get("number_of_agents")
    roles = list(attributes.get("agent_roles") or [])
    if template_id == "fan_in_decision" and isinstance(count, int) and count >= 2:
        parameters["evidence_sources"] = [
            {"id": roles[index] if index < len(roles) else f"evidence_source_{index + 1}"}
            for index in range(count - 1)
        ]
        parameters["decision_agent_id"] = roles[-1] if len(roles) >= count else "decision_agent"
    elif template_id == "traffic_signal_coordination" and isinstance(count, int) and count >= 2:
        parameters["approach_count"] = count - 1
    return parameters


def _verify_parameterized_counts(ir_data: dict[str, Any], context: ProcedureExecutionContext) -> None:
    mapping = {
        "number_of_agents": "agents",
        "number_of_resources": "resources",
        "number_of_channels": "channels",
    }
    for field, ir_key in mapping.items():
        if field not in context.mismatched_fields:
            continue
        expected = context.extracted_attributes.get(field)
        if expected is not None and len(ir_data.get(ir_key) or []) != expected:
            raise ProcedureExecutionError(
                f"selected template has no deterministic parameterization satisfying {field}={expected}"
            )


def _default_template_parameters(
    template_id: str,
    extracted_attributes: dict[str, Any],
) -> dict[str, Any]:
    """Return stable identity parameters; structural values remain template-owned."""

    del extracted_attributes
    defaults: dict[str, dict[str, Any]] = {
        "attendance_verification": {
            "observer_id": "observer",
            "verifier_id": "verifier",
        },
        "fan_in_decision": {
            "evidence_sources": [
                {"id": "evidence_source_1"},
                {"id": "evidence_source_2"},
                {"id": "evidence_source_3"},
            ],
            "decision_agent_id": "decision_agent",
        },
        "producer_consumer": {
            "producer_id": "producer",
            "consumer_id": "consumer",
        },
        "sequential_handoff": {
            "agent_a_id": "upstream_agent",
            "agent_b_id": "downstream_agent",
        },
        "traffic_signal_coordination": {},
        "verifier_approver": {
            "worker_id": "worker",
            "verifier_id": "verifier",
        },
    }
    # Persisted generated templates are artifact-backed and need no builder
    # parameters; build_template() resolves their verified IR/PlusCal bundle.
    return dict(defaults.get(template_id, {}))


__all__ = [
    "ExactReuseInstantiationResult",
    "ProcedureExecutionError",
    "instantiate_exact_reuse",
    "instantiate_parameterized_reuse",
    "ParameterizedReuseInstantiationResult",
]
