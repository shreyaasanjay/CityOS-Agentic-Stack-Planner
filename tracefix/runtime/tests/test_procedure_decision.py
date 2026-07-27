import pytest
from pydantic import ValidationError

from tracefix.runtime.procedure_decision import DeterministicProcedureDecision


def _decision(**overrides):
    payload = {
        "selected_procedure": "exact_reuse",
        "selected_template_id": "shared_resource",
        "reason_codes": ["exact_evidence_sufficient"],
        "matched_fields": ["coordination_patterns", "communication_flow"],
        "mismatched_fields": [],
        "unknown_fields": [],
        "parameterizable_fields": ["number_of_agents"],
        "adaptable_fields": ["communication_flow"],
        "fatal_mismatch_fields": [],
        "protected_fields": ["coordination_patterns"],
        "available_procedures": ["exact_reuse", "full_generation"],
        "rejected_procedures": {},
        "evidence_sufficient": True,
    }
    payload.update(overrides)
    return DeterministicProcedureDecision(**payload)


def test_decision_serializes_auditable_fields():
    payload = _decision().to_dict()

    assert payload["selected_procedure"] == "exact_reuse"
    assert payload["selected_template_id"] == "shared_resource"
    assert payload["reason_codes"] == ["exact_evidence_sufficient"]
    assert payload["protected_fields"] == ["coordination_patterns"]


def test_selected_procedure_must_be_available():
    with pytest.raises(ValidationError, match="included in available_procedures"):
        _decision(available_procedures=["full_generation"])


def test_reuse_requires_template_and_full_generation_forbids_one():
    with pytest.raises(ValidationError, match="selected_template_id"):
        _decision(selected_template_id=None)
    with pytest.raises(ValidationError, match="must not claim"):
        _decision(
            selected_procedure="full_generation",
            selected_template_id="shared_resource",
            available_procedures=["full_generation"],
        )


def test_single_agent_generation_is_template_free_and_serializable():
    decision = _decision(
        selected_procedure="single_agent_generation",
        selected_template_id=None,
        selected_template_rank=None,
        reason_codes=["single_agent_request"],
        matched_fields=[],
        available_procedures=["single_agent_generation"],
    )

    assert decision.to_dict()["selected_procedure"] == "single_agent_generation"
    assert decision.reason_codes == ["single_agent_request"]


def test_parameterized_and_partial_changes_are_bounded_by_metadata():
    with pytest.raises(ValidationError, match="parameterizable"):
        _decision(
            selected_procedure="parameterized_reuse",
            mismatched_fields=["communication_flow"],
            available_procedures=["parameterized_reuse", "full_generation"],
        )
    with pytest.raises(ValidationError, match="adaptable"):
        _decision(
            selected_procedure="partial_recomposition",
            mismatched_fields=["number_of_agents"],
            available_procedures=["partial_recomposition", "full_generation"],
        )
