import json

import pytest

from tracefix.runtime.procedure_execution import (
    ProcedureExecutionError,
    instantiate_exact_reuse,
    instantiate_parameterized_reuse,
)
from tracefix.runtime.opencode_adapter.driver import run_opencode_agent
from tracefix.runtime.procedure_prompt import ProcedureExecutionContext


def _context(template_id="sequential_handoff"):
    return ProcedureExecutionContext(
        selected_procedure="exact_reuse",
        selected_template_id=template_id,
        original_request="agent A hands work to agent B",
        extracted_attributes={},
        template_metadata={"pattern_id": template_id},
        matched_fields=["coordination_patterns", "communication_flow"],
        mismatched_fields=[],
        unknown_fields=[],
        parameterizable_fields=[],
        adaptable_fields=[],
        fatal_mismatch_fields=[],
        protected_fields=["coordination_patterns", "communication_flow"],
        reason_codes=["exact_evidence_sufficient"],
        execution_instructions=["Use the selected template as-is."],
    )


def test_exact_reuse_writes_valid_protocol_artifacts_without_llm(tmp_path):
    result = instantiate_exact_reuse(tmp_path, _context())

    assert result.template_id == "sequential_handoff"
    assert all(path.exists() for path in result.artifact_paths)
    ir = json.loads((tmp_path / "spec" / "ir.json").read_text(encoding="utf-8"))
    assert [agent["id"] for agent in ir["agents"]] == ["upstream_agent", "downstream_agent"]
    assert "--algorithm" in (tmp_path / "spec" / "Protocol.tla").read_text(encoding="utf-8")


def test_exact_reuse_fails_explicitly_for_nonexecutable_template(tmp_path):
    with pytest.raises(ProcedureExecutionError, match="not deterministically executable"):
        instantiate_exact_reuse(tmp_path, _context("generated_example"))


def test_parameterized_fan_in_reuse_is_deterministic(tmp_path):
    context = _context("fan_in_decision").model_copy(update={
        "selected_procedure": "parameterized_reuse",
        "extracted_attributes": {
            "coordination_patterns": ["Split-and-Merge", "Majority Voting"],
            "number_of_agents": 4,
            "agent_roles": ["source_a", "source_b", "source_c", "decision_agent"],
            "communication_flow": ["submit_result", "wait_for_all_sources", "decide"],
            "limitations": ["decision_waits_for_every_source"],
            "number_of_resources": 0,
            "number_of_channels": 3,
        },
        "mismatched_fields": ["number_of_agents"],
        "parameterizable_fields": ["number_of_agents", "number_of_channels"],
    })
    result = instantiate_parameterized_reuse(tmp_path, context)
    ir = json.loads((tmp_path / "spec" / "ir.json").read_text(encoding="utf-8"))
    assert result.template_id == "fan_in_decision"
    assert len(ir["agents"]) == 4
    assert len(ir["channels"]) == 3


@pytest.mark.asyncio
async def test_designer_execution_is_forbidden_for_exact_reuse(tmp_path):
    with pytest.raises(RuntimeError, match="OpenCode execution is forbidden for exact_reuse"):
        await run_opencode_agent(
            "designer",
            {},
            opencode_cmd=["opencode"],
            output_dir=tmp_path,
            usage_stage="opencode_procedure_execution_exact_reuse",
            procedure="exact_reuse",
            template_id="sequential_handoff",
        )


@pytest.mark.asyncio
async def test_opencode_is_forbidden_for_parameterized_reuse(tmp_path):
    with pytest.raises(RuntimeError, match="OpenCode execution is forbidden for parameterized_reuse"):
        await run_opencode_agent(
            "designer", {}, opencode_cmd=["opencode"], output_dir=tmp_path,
            usage_stage="opencode_procedure_execution_parameterized_reuse",
            procedure="parameterized_reuse", template_id="fan_in_decision",
        )
