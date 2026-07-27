"""Read-only TaskSpec consistency checks and bounded attribute reevaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationAttributes

MAX_ATTRIBUTE_CORRECTION_ATTEMPTS = 2


@dataclass(frozen=True)
class TaskSpecAttributeDiagnostic:
    status: str
    contradictions: tuple[dict[str, Any], ...]
    not_checkable: tuple[dict[str, str], ...]
    checked_fields: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "contradictions": list(self.contradictions),
            "not_checkable": list(self.not_checkable),
            "checked_fields": list(self.checked_fields),
        }


@dataclass(frozen=True)
class TaskSpecExtractionResult:
    attributes: ExtractedCoordinationAttributes | None
    diagnostic: TaskSpecAttributeDiagnostic
    attempts: int


def validate_attributes_against_taskspec(
    task_spec: Mapping[str, Any],
    attributes: ExtractedCoordinationAttributes,
) -> TaskSpecAttributeDiagnostic:
    """Check only explicit structured relationships in the real TraceFixTaskSpec."""

    checked: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    constraints = task_spec.get("constraints")
    constraints = constraints if isinstance(constraints, Mapping) else {}
    maximum = constraints.get("max_agents")
    actual = attributes.number_of_agents
    agent_count_checkable = (
        isinstance(maximum, int) and not isinstance(maximum, bool) and actual is not None
    )
    if agent_count_checkable:
        item = {
            "field": "number_of_agents",
            "relationship": "not_greater_than_taskspec_constraints.max_agents",
            "task_spec_value": maximum,
            "extracted_value": actual,
        }
        checked.append(item)
        if actual > maximum:
            contradictions.append({**item, "reason": "extracted agent count exceeds the explicit TaskSpec maximum"})
    else:
        checked.append({
            "field": "number_of_agents",
            "relationship": "not_greater_than_taskspec_constraints.max_agents",
            "result": "not_checkable",
        })

    not_checkable = [
        {
            "field": field,
            "reason": "TraceFixTaskSpec has no explicit structured field for this canonical attribute",
        }
        for field in Template.COORDINATION_ATTRIBUTE_FIELDS
        if field != "number_of_agents"
    ]
    if not agent_count_checkable:
        not_checkable.insert(0, {
            "field": "number_of_agents",
            "reason": "TaskSpec max_agents or extracted number_of_agents is unknown",
        })
    return TaskSpecAttributeDiagnostic(
        status="needs_reevaluation" if contradictions else "valid",
        contradictions=tuple(contradictions),
        not_checkable=tuple(not_checkable),
        checked_fields=tuple(checked),
    )


def extract_with_taskspec_reevaluation(
    *,
    task_spec: Mapping[str, Any],
    original_request: str,
    extractor: Callable[..., ExtractedCoordinationAttributes],
    model: str | None = None,
    max_correction_attempts: int = MAX_ATTRIBUTE_CORRECTION_ATTEMPTS,
) -> TaskSpecExtractionResult:
    """Run initial extraction plus at most two targeted corrections."""

    feedback: str | None = None
    last_attributes: ExtractedCoordinationAttributes | None = None
    last_diagnostic: TaskSpecAttributeDiagnostic | None = None
    for attempt in range(1, max_correction_attempts + 2):
        try:
            last_attributes = extractor(
                task_spec=task_spec,
                original_request=original_request,
                correction_feedback=feedback,
                model=model,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            # Backward-compatible dependency injection for existing public
            # extractor fakes/clients that still expose the old query-only API.
            last_attributes = extractor(original_request, model=model)
        last_diagnostic = validate_attributes_against_taskspec(task_spec, last_attributes)
        if not last_diagnostic.contradictions:
            return TaskSpecExtractionResult(last_attributes, last_diagnostic, attempt)
        feedback = _targeted_feedback(last_attributes, last_diagnostic)
    assert last_diagnostic is not None
    failed = TaskSpecAttributeDiagnostic(
        status="failed",
        contradictions=last_diagnostic.contradictions,
        not_checkable=last_diagnostic.not_checkable,
        checked_fields=last_diagnostic.checked_fields,
    )
    return TaskSpecExtractionResult(None, failed, max_correction_attempts + 1)


def _targeted_feedback(
    attributes: ExtractedCoordinationAttributes,
    diagnostic: TaskSpecAttributeDiagnostic,
) -> str:
    return (
        "The previous canonical attribute object contradicted explicit structured TaskSpec data. "
        f"Contradictions: {list(diagnostic.contradictions)!r}. "
        f"Previous attributes: {attributes.as_dict()!r}. Reevaluate only the conflicting attributes. "
        "Return the complete seven-field canonical object again. Do not modify the TaskSpec."
    )


__all__ = [
    "MAX_ATTRIBUTE_CORRECTION_ATTEMPTS",
    "TaskSpecAttributeDiagnostic",
    "TaskSpecExtractionResult",
    "extract_with_taskspec_reevaluation",
    "validate_attributes_against_taskspec",
]
