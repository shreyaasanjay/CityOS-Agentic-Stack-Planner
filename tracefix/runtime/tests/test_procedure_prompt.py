from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData
from tracefix.runtime.procedure_decision import DeterministicProcedureDecision
from tracefix.runtime.procedure_prompt import (
    build_procedure_execution_context,
    build_procedure_execution_prompt,
)
from tracefix.runtime.opencode_adapter.design import build_designer_prompt, repo_root


def _context(procedure="parameterized_reuse"):
    extracted = ExtractedCoordinationData.from_payload({
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 3,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
    })
    decision = DeterministicProcedureDecision(
        selected_procedure=procedure,
        selected_template_id=None if procedure == "full_generation" else "shared_resource",
        reason_codes=["deterministic_test"],
        matched_fields=["coordination_patterns", "communication_flow"],
        mismatched_fields=["number_of_agents"] if procedure == "parameterized_reuse" else [],
        unknown_fields=[],
        parameterizable_fields=["number_of_agents"],
        adaptable_fields=["communication_flow"],
        fatal_mismatch_fields=[],
        protected_fields=["coordination_patterns", "communication_flow"],
        available_procedures=[procedure, "full_generation"] if procedure != "full_generation" else ["full_generation"],
        rejected_procedures={},
        evidence_sufficient=False,
    )
    return build_procedure_execution_context(
        query="three agents share a resource",
        extracted_data=extracted,
        decision=decision,
        template_metadata={"pattern_id": "shared_resource"},
        task_spec={
            "task_id": "task_prompt_fixture",
            "user_query": "three agents share a resource",
            "constraints": {"max_agents": 4},
        },
    )


def test_execution_context_contains_fixed_mode_and_boundaries():
    context = _context()

    assert context.selected_procedure == "parameterized_reuse"
    assert context.parameterizable_fields == ["number_of_agents"]
    assert context.protected_fields == ["coordination_patterns", "communication_flow"]
    assert any("only fields" in item for item in context.execution_instructions)


def test_execution_prompt_forbids_route_reconsideration():
    prompt = build_procedure_execution_prompt(_context("partial_recomposition"))

    assert "not allowed to select, substitute, or recommend another procedure" in prompt
    assert '"selected_procedure": "partial_recomposition"' in prompt
    assert "Do not return a route decision" in prompt
    assert "CANONICAL TRACEFIX TEMPLATE CONTRACT" in prompt
    assert "spec/generated_template.json" in prompt
    assert '"coordination_patterns"' in prompt
    assert '"communication_flow"' in prompt
    assert "Never include tlc_passed" in prompt
    assert "only TraceFix's deterministic TLC gate owns that verdict" in prompt


def test_execution_context_uses_only_canonical_template_metadata():
    context = _context("partial_recomposition")

    assert set(context.extracted_attributes) == {
        "coordination_patterns",
        "number_of_agents",
        "agent_roles",
        "communication_flow",
        "limitations",
        "number_of_resources",
        "number_of_channels",
    }
    assert set(context.canonical_generated_template_schema) == {
        "template_id",
        "name_of_template",
        *context.extracted_attributes,
        "parameterizable_fields",
        "adaptable_fields",
        "fatal_mismatch_fields",
    }


def test_complete_system_and_user_prompt_has_one_authoritative_contract():
    complete = build_designer_prompt(repo_root()) + "\n\n" + build_procedure_execution_prompt(
        _context("partial_recomposition")
    )
    assert "Derive the coordination structure yourself" not in complete
    assert "create `summary.json`" not in complete
    assert 'set `"tlc_passed": true`' not in complete
    assert "canonical Template attributes" in complete
    assert '"selected_procedure": "partial_recomposition"' in complete
    assert "not allowed to select, substitute, or recommend another procedure" in complete


def test_partial_prompt_includes_unchanged_taskspec_and_accepted_attributes():
    context = _context("partial_recomposition")
    prompt = build_procedure_execution_prompt(context)

    assert context.task_spec == {
        "task_id": "task_prompt_fixture",
        "user_query": "three agents share a resource",
        "constraints": {"max_agents": 4},
    }
    assert '"task_spec": {' in prompt
    assert '"task_id": "task_prompt_fixture"' in prompt
    assert '"extracted_attributes": {' in prompt
    assert '"selected_procedure": "partial_recomposition"' in prompt
    assert "Never modify, rewrite, correct, extend, or replace it" in prompt
    assert "Never perform full generation unless selected_procedure is full_generation" in prompt


def test_full_generation_prompt_includes_taskspec_schema_and_pattern_vocabulary():
    prompt = build_procedure_execution_prompt(_context("full_generation"))

    assert '"selected_procedure": "full_generation"' in prompt
    assert '"task_id": "task_prompt_fixture"' in prompt
    assert '"canonical_generated_template_schema": {' in prompt
    assert '"canonical_coordination_pattern_vocabulary": [' in prompt
    assert '"Request-Grant"' in prompt
    assert "only TraceFix's deterministic TLC gate owns that verdict" in prompt
