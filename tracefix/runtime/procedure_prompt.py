"""Build bounded execution context for an already-selected procedure."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData
from tracefix.runtime.procedure_decision import DeterministicProcedureDecision, ProcedureName
from tracefix.runtime.coordination_patterns import COORDINATION_PATTERNS


PROCEDURE_EXECUTION_INSTRUCTIONS: dict[ProcedureName, tuple[str, ...]] = {
    "single_agent_generation": (
        "Use the deterministic single-agent generation path.",
        "Do not synthesize a multi-agent coordination protocol.",
        "Do not select, adapt, or generate a coordination template.",
    ),
    "exact_reuse": (
        "Use the selected template as-is.",
        "Do not alter protocol topology, coordination patterns, communication flow, or safety invariants.",
        "Do not redesign the protocol.",
        "Fill only explicit runtime or template parameters when required.",
        "Preserve every protected field and produce the artifacts required for validation and TLC.",
    ),
    "parameterized_reuse": (
        "Use the selected template and change only fields listed in parameterizable_fields.",
        "Preserve coordination structure, safety invariants, and every non-parameterizable field.",
        "Do not introduce agents, messages, channels, resources, or phases unless an allowed parameter requires it.",
        "Return artifacts in the exact format required by the downstream IR, PlusCal, and TLC pipeline.",
    ),
    "partial_recomposition": (
        "Reuse every validated matching component and change only fields listed in adaptable_fields or recomposable_fields.",
        "Fields listed in recomposable_fields are authorized by deterministic validator overlap counts.",
        "Preserve protected and matched structural properties and never modify fatal_mismatch_fields.",
        "Do not redesign unrelated sections; generate only missing or adaptable pieces.",
        "Maintain compatibility with the selected template and return all downstream validation artifacts.",
    ),
    "full_generation": (
        "Generate a new protocol from the original request and extracted attributes.",
        "Candidate-template rankings are diagnostic context only; do not claim template reuse.",
        "Produce every artifact required by the current OpenCode, IR, PlusCal, TLC, and CityOS pipeline.",
    ),
}


class ProcedureExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_procedure: ProcedureName
    selected_template_id: str | None
    original_request: str
    task_spec: dict[str, Any] = Field(default_factory=dict)
    extracted_attributes: dict[str, Any]
    template_metadata: dict[str, Any] = Field(default_factory=dict)
    matched_fields: list[str] = Field(default_factory=list)
    mismatched_fields: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    parameterizable_fields: list[str] = Field(default_factory=list)
    adaptable_fields: list[str] = Field(default_factory=list)
    recomposable_fields: list[str] = Field(default_factory=list)
    fatal_mismatch_fields: list[str] = Field(default_factory=list)
    protected_fields: list[str] = Field(default_factory=list)
    reason_codes: list[str]
    execution_instructions: list[str]
    canonical_field_semantics: dict[str, str] = Field(default_factory=dict)
    canonical_generated_template_schema: dict[str, Any] = Field(default_factory=dict)
    canonical_coordination_pattern_vocabulary: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_procedure_execution_context(
    *,
    query: str,
    extracted_data: ExtractedCoordinationData,
    decision: DeterministicProcedureDecision,
    template_metadata: dict[str, Any] | None,
    task_spec: dict[str, Any] | None = None,
) -> ProcedureExecutionContext:
    """Create bounded input for execution without reconsidering the route."""

    extracted_attributes = extracted_data.as_dict()
    Template.validate_canonical_keys(
        extracted_attributes,
        include_identity=False,
        include_reuse_policy=False,
    )
    canonical_generated_schema = {
        "template_id": "generated_<stable-id>",
        "name_of_template": "<descriptive canonical name>",
        **Template.empty_coordination_attributes(),
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }

    return ProcedureExecutionContext(
        selected_procedure=decision.selected_procedure,
        selected_template_id=decision.selected_template_id,
        original_request=query,
        task_spec=dict(task_spec or {}),
        extracted_attributes=extracted_attributes,
        template_metadata=dict(template_metadata or {}),
        matched_fields=list(decision.matched_fields),
        mismatched_fields=list(decision.mismatched_fields),
        unknown_fields=list(decision.unknown_fields),
        parameterizable_fields=list(decision.parameterizable_fields),
        adaptable_fields=list(decision.adaptable_fields),
        recomposable_fields=list(decision.recomposable_fields),
        fatal_mismatch_fields=list(decision.fatal_mismatch_fields),
        protected_fields=list(decision.protected_fields),
        reason_codes=list(decision.reason_codes),
        execution_instructions=list(PROCEDURE_EXECUTION_INSTRUCTIONS[decision.selected_procedure]),
        canonical_field_semantics=dict(Template.ATTRIBUTE_SEMANTICS),
        canonical_generated_template_schema=canonical_generated_schema,
        canonical_coordination_pattern_vocabulary=list(COORDINATION_PATTERNS),
    )


def build_procedure_execution_prompt(
    context: ProcedureExecutionContext,
    *,
    workspace_rel: str | None = None,
) -> str:
    """Render instructions for executing, never selecting, a fixed procedure."""

    workspace_instruction = (
        f"Write all generated artifacts only under `{workspace_rel}/`; replace its initialized IR stub.\n"
        if workspace_rel
        else ""
    )
    return (
        "The deterministic validation engine has selected the procedure below.\n"
        "You are not allowed to select, substitute, or recommend another procedure.\n"
        "Execute only the authoritative selected_procedure and obey every protected-field boundary.\n"
        "Do not return a route decision. Write the required workspace artifacts and continue the normal validation pipeline.\n\n"
        "READ-ONLY TASKSPEC CONTRACT:\n"
        "task_spec is unchanged authoritative TeLLMe input. Never modify, rewrite, correct, extend, or replace it. "
        "Partial recomposition may adapt only its authorized fields; full generation may derive only low-level implementation details. "
        "Never perform full generation unless selected_procedure is full_generation.\n\n"
        "CANONICAL TRACEFIX TEMPLATE CONTRACT:\n"
        "The following Template attributes have already been extracted and validated. They are authoritative.\n"
        "Do NOT infer them again, rename them, replace them, or emit alternate coordination metadata.\n"
        "Use extracted_attributes directly to implement coordination. coordination_patterns define mechanisms; "
        "agent_roles define participating entities; communication_flow defines interaction order; limitations "
        "define required guarantees; number_of_resources and number_of_channels define coordinated resources "
        "and logical channels.\n"
        "At the end of generation, write `spec/generated_template.json` using exactly "
        "canonical_generated_template_schema. Fully populate its canonical values from the verified implementation. "
        "Never include tlc_passed and never create or edit summary.json; only TraceFix's deterministic TLC gate owns that verdict. "
        "Do not write participants, roles, flow, communication, resources, channels, agent_count, channel_count, "
        "safety_features, or safety_properties as Template metadata aliases.\n\n"
        + workspace_instruction
        + "AUTHORITATIVE_PROCEDURE_EXECUTION_CONTEXT_JSON:\n"
        + json.dumps(context.to_dict(), indent=2, ensure_ascii=False)
    )


__all__ = [
    "PROCEDURE_EXECUTION_INSTRUCTIONS",
    "ProcedureExecutionContext",
    "build_procedure_execution_context",
    "build_procedure_execution_prompt",
]
