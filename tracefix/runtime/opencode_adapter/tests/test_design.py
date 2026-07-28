"""Tests for `tracefix design` (headless opencode + skill) — no opencode spawned."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracefix.runtime.llm_attribute_extractor import (
    AttributeExtractionError,
    ExtractedCoordinationAttributes,
)
import tracefix.runtime.opencode_adapter.design as design_module
from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.design import (
    _channel_diagnostics,
    _guard_init_stub_ir,
    _ensure_plan_before_prompts,
    _normalize_legacy_endeither_syntax,
    _runtime_prompts_current,
    _run_tlc_and_extract,
    _scaffold_valid_ir,
    build_designer_prompt,
    classify_design_artifacts,
    design_kickoff,
    ir_repair_kickoff,
    judge,
    pluscal_completion_kickoff,
    prompt_generation_kickoff,
    repo_root,
    slugify,
    validate_design_ir,
)


# --- naming -----------------------------------------------------------------

def test_slugify_short_and_safe():
    assert slugify("Design a 2PC protocol with a coordinator!") == \
        "design_a_2pc_protocol_with"
    assert slugify("___") == "design"


# --- designer prompt (skill injection) ---------------------------------------

def test_designer_prompt_embeds_skill_without_frontmatter():
    prompt = build_designer_prompt(repo_root())
    assert "Headless mode" in prompt                      # preamble first
    assert "name: tla-verify-pluscal" not in prompt        # frontmatter stripped
    assert "Phase 1: Structured Analysis" in prompt        # the actual workflow
    assert ".claude/skills/tla-prompt-gen/SKILL.md" in prompt  # Phase-5 redirection
    assert "Do NOT emit top-level or nested" in prompt
    assert "`locks`, `counters`" in prompt
    assert "Derive the coordination structure yourself" not in prompt
    assert "create `summary.json`" not in prompt
    assert 'set `"tlc_passed": true`' not in prompt
    assert "canonical Template attributes" in prompt
    assert "deterministic procedure" in prompt


def test_designer_prompt_preserves_literal_ir_json_examples():
    prompt = build_designer_prompt(repo_root())
    assert '{"id": "RESOURCE_ID", "type": "Lock"}' in prompt
    assert '{"initial": N}' in prompt
    assert "{prompt_gen_skill}" not in prompt


def test_kickoff_names_the_workspace():
    k = design_kickoff("workspace/my_task")
    assert "workspace/my_task/description.md" in k
    assert "prompts/runtime_b/" in k
    assert "`channels` must be non-empty" in k
    assert "Do not write `locks`" in k
    assert "Do not add arbitrary complete-graph channels" in k


def test_ir_repair_kickoff_requires_channel_rationale():
    k = ir_repair_kickoff("workspace/my_task", ["channels must not be empty"])
    assert "channels must be non-empty" in k
    assert "spec/ir_repair_notes.md" in k
    assert "Do not add `locks`" in k
    assert "Do not add arbitrary complete-graph channels" in k


def test_pluscal_completion_kickoff_continues_after_scaffold():
    k = pluscal_completion_kickoff("workspace/my_task")
    assert "Protocol.tla" in k
    assert "Run `tla-verify-pluscal verify`" in k
    assert "prompts/runtime_b/" in k
    assert "Do not redesign the IR" in k
    assert "Never write `endeither`" in k
    assert "spec/cityos_module_plan.json" in k
    assert "do not generate prompts from raw IR alone" in k


def test_prompt_generation_kickoff_requires_verified_plan():
    k = prompt_generation_kickoff("workspace/my_task")
    assert "spec/cityos_module_plan.json" in k
    assert "Protocol_translated.tla" in k
    assert "prompts/runtime_b/" in k
    assert "never from raw IR alone" in k
    assert "Do not edit `spec/ir.json`" in k


def test_normalize_legacy_endeither_syntax():
    tla = "\n".join([
        "choice:",
        "  either",
        "    \\* ok",
        "    { assert msg = \"ok\"; goto done; }",
        "  or",
        "    { assert msg = \"fail\";",
        "      goto retry; }",
        "  endeither",
        "done:",
        "  skip;",
    ])

    fixed, diagnostics = _normalize_legacy_endeither_syntax(tla)
    assert "endeither" not in fixed
    assert "  either {" in fixed
    assert "  } or {" in fixed
    assert "  };" in fixed
    assert "assert msg = \"ok\"; goto done;" in fixed
    assert "goto retry;" in fixed
    assert diagnostics


def test_prompt_gate_removes_prompts_created_before_plan(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    pdir = ws / "prompts" / "runtime_b"
    spec.mkdir(parents=True)
    pdir.mkdir(parents=True)
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    (spec / "Protocol_translated.tla").write_text("---- MODULE Protocol ----\n====\n")
    (spec / "states.json").write_text("{}")
    (spec / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    prompt = pdir / "A.md"
    prompt.write_text("# stale")
    plan = spec / "cityos_module_plan.json"
    plan.write_text("{}")
    old = plan.stat().st_mtime - 10
    import os
    os.utime(prompt, (old, old))

    diagnostics = _ensure_plan_before_prompts(ws)

    assert any("removed 1 stale runtime prompt" in item for item in diagnostics)
    assert not prompt.exists()
    assert not _runtime_prompts_current(ws)


# --- design config (no MCP, headless-safe permissions) ------------------------

def test_design_config_shape():
    cfg = build_design_config("THE PROMPT", model="openai/gpt-5.4")
    assert "mcp" not in cfg                                # designer has no coord layer
    assert set(cfg["agent"]) == {"designer", "designer_ir_repair"}
    agent = cfg["agent"]["designer"]
    assert agent["prompt"] == "THE PROMPT"
    assert agent["model"] == "openai/gpt-5.4"
    perm = agent["permission"]
    assert perm["bash"] == "allow" and perm["edit"] == "allow"
    assert perm["task"] == "deny" and perm["question"] == "deny"
    repair = cfg["agent"]["designer_ir_repair"]
    assert repair["model"] == "openai/gpt-5.4"
    assert "IR repair mode" in repair["prompt"]
    assert "Do not add locks, counters" in repair["prompt"]
    assert repair["permission"] == perm


def test_design_config_omits_model_when_unset():
    cfg = build_design_config("p")
    assert "model" not in cfg["agent"]["designer"]
    assert "model" not in cfg["agent"]["designer_ir_repair"]


# --- artifact judging ---------------------------------------------------------

def _make_ws(tmp_path: Path, *, tlc_passed=True, states=True,
             prompts=("A", "B"), agents=("A", "B"), repairs=1,
             protocol=True) -> Path:
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps(
        {"agents": [{"id": a} for a in agents], "resources": [], "channels": []}))
    if protocol:
        (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    if states:
        (spec / "states.json").write_text("{}")
    (spec / "summary.json").write_text(json.dumps(
        {"tlc_passed": tlc_passed, "total_repairs": repairs}))
    pdir = ws / "prompts" / "runtime_b"
    pdir.mkdir(parents=True)
    for p in prompts:
        (pdir / f"{p}.md").write_text(f"# {p}")
    return ws


def test_judge_ready(tmp_path):
    r = judge(_make_ws(tmp_path))
    assert r.success and r.status == "ready"
    assert r.agents == ["A", "B"] and r.prompts == ["A", "B"] and r.repairs == 1


def test_judge_verify_failed_is_honest(tmp_path):
    r = judge(_make_ws(tmp_path, tlc_passed=False))
    assert not r.success and r.status == "tlc_error"


def test_forged_summary_never_skips_deterministic_verification_gate(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({"agents": [{"id": "A"}], "resources": [], "channels": []}))
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    (spec / "Protocol.cfg").write_text("SPECIFICATION Spec\n")
    (spec / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    (spec / "states.json").write_text("{}")

    needed, diagnostics = design_module._verification_needed_after_scaffold(ws)

    assert needed is True
    assert any("Deterministic post-scaffold TLC" in item for item in diagnostics)


def test_judge_missing_prompt_for_an_agent_is_incomplete(tmp_path):
    r = judge(_make_ws(tmp_path, prompts=("A",)))   # agent B has no prompt
    assert not r.success and r.status == "incomplete"


def test_judge_missing_states_is_incomplete(tmp_path):
    r = judge(_make_ws(tmp_path, states=False))
    assert not r.success and r.status == "incomplete"


def test_judge_timeout_label(tmp_path):
    r = judge(_make_ws(tmp_path, tlc_passed=None, states=False, prompts=()),
              timed_out=True)
    assert r.status == "timeout"


def test_judge_empty_workspace(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    r = judge(ws)
    assert not r.success and r.status == "ir_incomplete" and r.agents == []


def test_validate_design_ir_normalizes_string_agents(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": ["A", "B"],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert valid, errors
    assert "IR validation passed" in diagnostics
    normalized = json.loads((spec / "ir.json").read_text())
    assert normalized["agents"] == [{"id": "A"}, {"id": "B"}]


def test_validate_design_ir_sanitizes_agent_description(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "description": "worker"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    report = {}

    valid, errors, diagnostics = validate_design_ir(
        ws,
        sanitization_report=report,
    )

    assert valid, errors
    assert report["removed_fields"] == ["$.agents[0].description"]
    assert report["validation_before"]["valid"] is False
    assert report["validation_after"]["valid"] is True
    assert report["recovered"] is True
    assert any("removed $.agents[0].description" in item for item in diagnostics)
    sanitized = json.loads((spec / "ir.json").read_text())
    assert sanitized["agents"] == [{"id": "A"}, {"id": "B"}]


def test_validate_design_ir_canonicalizes_agent_role(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [
            {"id": "A", "role": "produce evidence"},
            {"id": "B", "role": "verify evidence"},
        ],
        "resources": [],
        "channels": [
            {"id": "a_to_b", "from": "A", "to": "B", "labels": ["submit"]},
        ],
    }))
    report = {}

    valid, errors, diagnostics = validate_design_ir(
        ws, sanitization_report=report
    )

    persisted = json.loads((spec / "ir.json").read_text())
    assert valid, errors
    assert all("role" not in agent for agent in persisted["agents"])
    assert report["removed_fields"] == ["$.agents[0].role", "$.agents[1].role"]
    assert any("passed after sanitization" in item for item in diagnostics)


def test_canonicalizer_does_not_hide_unknown_structural_field(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "agent_id": "wrong-contract-field"}],
        "resources": [],
        "channels": [],
    }))

    valid, errors, _ = validate_design_ir(ws)

    assert valid is False
    assert any("$.agents[0].agent_id" in error for error in errors)


def test_validate_design_ir_sanitizer_preserves_structural_failure(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    original = {
        "agents": [{"id": "A", "notes": "harmless"}, {"id": "B"}],
        "resources": [],
        "channels": [],
    }
    (spec / "ir.json").write_text(json.dumps(original))
    report = {}

    valid, errors, _ = validate_design_ir(ws, sanitization_report=report)

    assert not valid
    assert any("no communication channels" in error for error in errors)
    assert report["validation_after"]["valid"] is False
    assert report["recovered"] is False
    assert json.loads((spec / "ir.json").read_text()) == original


def test_validate_design_ir_sanitizer_never_removes_required_fields(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"description": "missing required id"}],
        "resources": [],
        "channels": [],
    }))
    report = {}

    valid, errors, _ = validate_design_ir(ws, sanitization_report=report)

    assert not valid
    assert any("'id' is a required property" in error for error in errors)
    assert report["removed_fields"] == ["$.agents[0].description"]
    assert report["validation_after"]["valid"] is False


def test_sanitized_ir_still_uses_normal_scaffold_gate(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "rationale": "sender"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{
            "id": "a_to_b",
            "from": "A",
            "to": "B",
            "labels": ["work ready"],
            "comments": "handoff",
        }],
    }))

    valid, errors, _ = validate_design_ir(ws)
    assert valid, errors
    diagnostics = _scaffold_valid_ir(ws)

    assert "Scaffold fallback wrote Protocol.tla and Protocol.cfg" in diagnostics
    sanitized = json.loads((spec / "ir.json").read_text())
    assert sanitized["channels"][0]["labels"] == ["work_ready"]
    assert "comments" not in sanitized["channels"][0]
    assert (spec / "Protocol.tla").is_file()
    assert (spec / "Protocol.cfg").is_file()


def test_timing_ignores_missing_optional_opencode_disposition(tmp_path):
    from tracefix.pipeline_timing import PipelineTimingReport

    report = PipelineTimingReport(tmp_path, run_kind="test")
    report.opencode_call("opencode_optional", None)

    assert report.api_calls == []
    assert report.stages[-1]["stage"] == "opencode_optional"
    assert report.stages[-1]["success"] is True
    assert report.stages[-1]["skipped"] is True


def test_simple_prompt_uses_single_agent_fast_path():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "How many people are in the room right now?"
    )

    assert decision.eligible is True
    assert decision.agent_id == "OCCUPANCY_ANALYZER"


def test_coordination_prompt_falls_back_to_opencode():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "Coordinate three agents to validate authorization and produce an audit report."
    )

    assert decision.eligible is False
    assert "coordination signal" in decision.reason


def test_multi_source_prompt_falls_back_to_opencode():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "Verify the room count against badge logs and meeting attendance."
    )

    assert decision.eligible is False
    assert "multi-source signal" in decision.reason


def test_fast_path_ir_passes_strict_validation():
    from tracefix.pipeline.pipeline.validator import validate_ir
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        generate_single_agent_ir,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")
    ir = generate_single_agent_ir(decision)
    result = validate_ir(ir)

    assert result.valid, result.errors
    assert ir["agents"] == [{"id": "SUMMARY_AGENT"}]
    assert ir["channels"] == []


def test_structured_tellme_occupancy_uses_fast_path():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    task = """TeLLMe structured smart-room application requirements.
Structured task specification:
{
  "user_query": "How many people are in the room right now?",
  "route": "single_agent",
  "required_modalities": ["video"],
  "candidate_harnesses": [
    "occupancy_context_harness",
    "answer_synthesis_harness"
  ],
  "evidence_plan": {
    "primary_evidence": ["occupancy_context_harness_packet"],
    "supporting_evidence": [],
    "conflicting_evidence_checks": []
  },
  "application_goal": {"goal_type": "occupancy_count"}
}
"""
    decision = assess_single_agent_fast_path(task)

    assert decision.eligible is True
    assert decision.structured_input is True
    assert decision.task_text == "How many people are in the room right now?"



def test_init_stub_guard_repairs_tellme_multi_agent_stub(tmp_path):
    from tracefix.pipeline.pipeline.validator import validate_ir

    ws = tmp_path / "ws"
    spec_dir = ws / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "ir.json").write_text(json.dumps({
        "agents": [{"id": "AGENT_A"}, {"id": "AGENT_B"}],
        "resources": [],
        "channels": [],
    }))
    structured = {
        "user_query": "Design a system for events captured by two smart-room cameras.",
        "route": "multi_agent",
        "candidate_harnesses": [
            "camera_event_harness",
            "answer_synthesis_harness",
        ],
    }
    task = "TeLLMe structured smart-room application requirements.\nStructured task specification:\n" + json.dumps(structured)
    diagnostics = []

    _guard_init_stub_ir(ws, task, diagnostics)

    repaired = json.loads((spec_dir / "ir.json").read_text())
    result = validate_ir(repaired)
    assert result.valid, result.errors
    assert repaired["agents"] == [
        {"id": "camera_event"},
        {"id": "answer_synthesizer"},
    ]
    assert repaired["resources"] == []
    assert repaired["channels"] == [{
        "id": "camera_event_to_answer_synthesizer",
        "from": "camera_event",
        "to": "answer_synthesizer",
        "labels": ["evidence_ready", "answer_ready"],
    }]
    assert any("Init stub guard" in item for item in diagnostics)

def test_fast_path_runtime_prompt_requires_verified_plan():
    import pytest
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        render_verified_runtime_prompt,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")

    with pytest.raises(ValueError, match="production-ready"):
        render_verified_runtime_prompt(
            decision,
            {"verification": {"production_ready": False}},
        )


def test_fast_path_runtime_prompt_derives_from_verified_plan():
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        render_verified_runtime_prompt,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")
    prompt = render_verified_runtime_prompt(decision, {
        "verification": {"production_ready": True},
        "agents": [{"name": "SUMMARY_AGENT"}],
        "protocol": {
            "allowed_transitions": [{
                "agent": "SUMMARY_AGENT",
                "from": "SUMMARY_AGENT_start",
                "to": "SUMMARY_AGENT_done",
            }],
        },
    })

    assert "Summarize this log file." in prompt
    assert "`SUMMARY_AGENT_start` -> `SUMMARY_AGENT_done`" in prompt
    assert "after PlusCal/TLC verification" in prompt


def test_validate_design_ir_normalizes_legacy_locks_and_counters(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": ["A", "B"],
        "resources": [],
        "locks": ["SHARED_FILE"],
        "counters": [{"id": "API_POOL", "initial": 2}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert valid, errors
    assert any("$.locks[0]" in item for item in diagnostics)
    assert any("$.counters[0]" in item for item in diagnostics)
    normalized = json.loads((spec / "ir.json").read_text())
    assert "locks" not in normalized
    assert "counters" not in normalized
    assert {"id": "SHARED_FILE", "type": "Lock"} in normalized["resources"]
    assert {"id": "API_POOL", "type": "Counter", "config": {"initial": 2}} in normalized["resources"]


def test_validate_design_ir_rejects_nested_locks_with_path(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "locks": ["SHARED_FILE"]}, {"id": "B"}],
        "resources": [{"id": "SHARED_FILE", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert not valid
    assert any("$.agents[0].locks" in error for error in errors)
    assert "IR validation failed" in diagnostics


def test_scaffold_valid_ir_writes_protocol_artifacts(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    diagnostics = _scaffold_valid_ir(ws)

    assert "Scaffold fallback wrote Protocol.tla and Protocol.cfg" in diagnostics
    assert (spec / "Protocol.tla").exists()
    assert (spec / "Protocol.cfg").exists()


def test_classify_design_artifacts_ir_incomplete_for_empty_channels(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [],
    }))

    status, errors, diagnostics = classify_design_artifacts(ws)
    assert status == "ir_incomplete"
    assert any("no communication channels" in error for error in errors)
    assert "TLC did not run" in diagnostics


def test_classify_design_artifacts_tlc_error(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----")
    (spec / "tlc_error.md").write_text("# TLC failed")

    status, errors, diagnostics = classify_design_artifacts(ws)
    assert status == "tlc_error"
    assert errors == []
    assert "TLC error artifact present" in diagnostics


def test_classify_design_artifacts_pluscal_error_includes_artifact_inventory(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    # No states.json, no tlc_error.md — the pluscal_error case.

    status, errors, diagnostics = classify_design_artifacts(ws)

    assert status == "pluscal_error"
    assert any("states.json is missing" in d for d in diagnostics)
    # The diagnostic should mention Protocol.cfg and tlc_output.log presence.
    inv_diag = next(d for d in diagnostics if "states.json is missing" in d)
    assert "Protocol.cfg=" in inv_diag
    assert "tlc_output.log=" in inv_diag


def test_run_tlc_and_extract_writes_tlc_error_when_cfg_missing(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    # Protocol.tla present but Protocol.cfg absent
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")

    diagnostics = _run_tlc_and_extract(ws)

    assert any("Protocol.cfg missing" in d for d in diagnostics)
    assert (spec / "tlc_error.md").exists()
    assert "Protocol.cfg missing" in (spec / "tlc_error.md").read_text()


def test_channel_diagnostics_reports_added_channels():
    before = {"channels": []}
    after = {
        "channels": [
            {
                "id": "a_to_b",
                "from": "A",
                "to": "B",
                "labels": ["handoff", "review"],
            }
        ]
    }

    diagnostics = _channel_diagnostics(before, after)
    assert diagnostics == [
        "IR repair added channel a_to_b: A -> B labels=[handoff, review]"
    ]


@pytest.mark.asyncio
async def test_run_design_exact_reuse_instantiates_artifacts_without_opencode(monkeypatch, capsys):
    def fake_extract(query, *, model=None, client=None):
        assert "three robots" in query.lower()
        assert model == "test-model"
        return ExtractedCoordinationAttributes(
            coordination_patterns=[
                "Request-Grant",
                "Exclusive Resource Access",
                "Task Prioritization",
                "Queue-Based Scheduling",
                "Reservation",
            ],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=1,
            number_of_channels=None,
        )

    monkeypatch.setattr(design_module, "extract_coordination_attributes", fake_extract)
    monkeypatch.setattr(design_module, "_verification_needed_after_scaffold", lambda _ws: (False, []))

    async def unexpected_opencode(*_args, **_kwargs):
        raise AssertionError("exact reuse must not invoke OpenCode")

    monkeypatch.setattr(design_module, "run_opencode_agent", unexpected_opencode)

    extractor_input = "Coordinate three robots through a shared corridor."
    result = await design_module.run_design(
        extractor_input,
        name="pytest_attr_extract_success",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    artifact = ws / "spec" / "extracted_coordination_attributes.json"
    timing = json.loads((ws / "output" / "pipeline_timing_report.json").read_text(encoding="utf-8"))

    decision = json.loads((ws / "spec" / "procedure_decision.json").read_text(encoding="utf-8"))
    validation_audit = json.loads(
        (ws / "spec" / "template_validation_results.json").read_text(encoding="utf-8")
    )
    execution_audit = json.loads(
        (ws / "spec" / "procedure_execution.json").read_text(encoding="utf-8")
    )
    terminal = capsys.readouterr().out
    assert decision["selected_procedure"] == "exact_reuse"
    assert decision["reason_codes"]
    assert decision["selected_template_id"] == validation_audit["ranked_candidates"][0]["template_id"]
    extractor_audit = json.loads((ws / "spec" / "extractor_input.txt").read_text(encoding="utf-8"))
    assert extractor_audit["secondary_original_request"] == extractor_input
    assert artifact.is_file()
    assert json.loads(artifact.read_text(encoding="utf-8"))["number_of_resources"] == 1
    assert timing["llm_attribute_extraction"]["template_mapping_status"] == "deterministic_exact_reuse_selected"
    assert (ws / "spec" / "template_rankings.json").exists()
    assert (ws / "spec" / "template_validation_result.json").exists()
    assert (ws / "spec" / "procedure_options.json").exists()
    assert (ws / "spec" / "procedure_decision.json").exists()
    assert (ws / "spec" / "procedure_execution_context.json").exists()
    assert validation_audit["candidate_count"] == len(validation_audit["ranked_candidates"])
    assert validation_audit["ranked_candidates"][0]["rank"] == 1
    assert "field_results" in validation_audit["ranked_candidates"][0]
    assert execution_audit["executor"] == "deterministic_builder"
    assert execution_audit["llm_expected"] is False
    assert execution_audit["success"] is True
    assert (ws / "spec" / "Protocol.tla").exists()
    assert (ws / "spec" / "Protocol.cfg").exists()
    assert (ws / "spec" / "ir.json").exists()
    assert not (ws / "spec" / "procedure_selection_prompt.txt").exists()
    for marker in (
        "[TRACEFIX EXTRACTOR INPUT START]",
        "[TRACEFIX EXTRACTOR OUTPUT START]",
        "[TRACEFIX VALIDATOR OUTPUT START]",
        "[TRACEFIX PROCEDURE SELECTED]",
        "[TRACEFIX PROCEDURE DECISION START]",
        "[TRACEFIX PROCEDURE EXECUTION START]",
        "[TRACEFIX PROCEDURE EXECUTION END]",
    ):
        assert marker in terminal
    assert extractor_input in terminal


@pytest.mark.asyncio
async def test_run_design_routes_extracted_single_agent_without_template_validation_or_opencode(
    monkeypatch,
    capsys,
):
    def fake_extract(_query, *, model=None, client=None):
        assert model == "test-model"
        return ExtractedCoordinationAttributes(
            coordination_patterns=[],
            number_of_agents=1,
            agent_roles=["occupancy_analyzer"],
            communication_flow=[],
            limitations=[],
            number_of_resources=0,
            number_of_channels=0,
        )

    generated = []

    def fake_single_agent_generation(ws, decision, _timing):
        generated.append(decision)
        (ws / "spec" / "ir.json").write_text(
            json.dumps({
                "agents": [{"id": decision.agent_id}],
                "resources": [],
                "channels": [],
                "state_tasks": {f"{decision.agent_id}_start": decision.task_text},
            }),
            encoding="utf-8",
        )
        return ["single-agent test generation completed"]

    async def unexpected_opencode(*_args, **_kwargs):
        raise AssertionError("single_agent_generation must not invoke OpenCode")

    monkeypatch.setattr(design_module, "extract_coordination_attributes", fake_extract)
    monkeypatch.setattr(
        design_module,
        "_generate_and_verify_single_agent",
        fake_single_agent_generation,
    )
    monkeypatch.setattr(design_module, "run_opencode_agent", unexpected_opencode)
    monkeypatch.setattr(design_module, "_verification_needed_after_scaffold", lambda _ws: (False, []))

    result = await design_module.run_design(
        "How many people are in the room?",
        task_spec={
            "route": "single_agent",
            "user_query": "How many people are in the room?",
        },
        name="pytest_single_agent_procedure",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    decision = json.loads(
        (ws / "spec" / "procedure_decision.json").read_text(encoding="utf-8")
    )
    rankings = json.loads(
        (ws / "spec" / "template_rankings.json").read_text(encoding="utf-8")
    )
    validation = json.loads(
        (ws / "spec" / "template_validation_results.json").read_text(encoding="utf-8")
    )
    terminal = capsys.readouterr().out

    assert generated
    assert decision["selected_procedure"] == "single_agent_generation"
    assert decision["reason_codes"] == ["single_agent_request"]
    assert "full_generation_fallback" not in decision["reason_codes"]
    assert rankings == []
    assert validation["skipped"] is True
    assert validation["reason"] == "single_agent_request"
    assert "[TRACEFIX PROCEDURE SELECTED] mode=single_agent_generation" in terminal
    assert "[TRACEFIX PROCEDURE SELECTED] mode=full_generation" not in terminal


@pytest.mark.asyncio
async def test_run_design_full_generation_decision_falls_back_to_opencode(monkeypatch):
    def fake_extract(query, *, model=None, client=None):
        assert "three robots" in query.lower()
        assert model == "test-model"
        return ExtractedCoordinationAttributes(
            coordination_patterns=["Subscription"],
            number_of_agents=3,
            agent_roles=["robot", "scheduler"],
            communication_flow=["request", "grant", "release"],
            limitations=["one robot in corridor"],
            number_of_resources=1,
            number_of_channels=3,
        )

    opencode_calls: list[tuple[str, str]] = []

    async def fake_run_opencode_agent(agent_name, _cfg, **kwargs):
        opencode_calls.append((agent_name, kwargs.get("kickoff", "")))
        return {
            "status": "success",
            "events": 0,
            "stderr_tail": [],
            "returncode": 0,
            "provider": None,
            "model": None,
        }

    monkeypatch.setattr(design_module, "extract_coordination_attributes", fake_extract)
    monkeypatch.setattr(design_module, "run_opencode_agent", fake_run_opencode_agent)

    result = await design_module.run_design(
        "Coordinate three robots through a shared corridor.",
        name="pytest_full_generation_fallback",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    timing = json.loads((ws / "output" / "pipeline_timing_report.json").read_text(encoding="utf-8"))

    assert any(agent_name == "designer" for agent_name, _kickoff in opencode_calls)
    assert timing["llm_attribute_extraction"]["template_mapping_status"] == "deterministic_full_generation_selected"
    initial_prompt = opencode_calls[0][1]
    assert "not allowed to select, substitute, or recommend another procedure" in initial_prompt
    assert '"selected_procedure": "full_generation"' in initial_prompt
    assert any(
        "Deterministic full_generation execution was run through OpenCode." in line
        for line in result.diagnostics
    )
    execution_audit = json.loads(
        (ws / "spec" / "procedure_execution.json").read_text(encoding="utf-8")
    )
    assert execution_audit["selected_procedure"] == "full_generation"
    assert execution_audit["llm_expected"] is True
    assert execution_audit["executor"] == "opencode"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "communication_flow", "number_of_agents"),
    [
        ("parameterized_reuse", ["work", "handoff", "receive", "continue"], 3),
        ("partial_recomposition", ["work", "handoff", "receive", "continue", "acknowledge"], 2),
    ],
)
async def test_run_design_reuse_modes_dispatch_fixed_execution_prompt(
    monkeypatch,
    mode,
    communication_flow,
    number_of_agents,
):
    def fake_extract(_query, *, model=None, client=None):
        return ExtractedCoordinationAttributes(
            coordination_patterns=["Sequential Handoff"],
            number_of_agents=number_of_agents,
            agent_roles=["upstream_agent", "downstream_agent"],
            communication_flow=communication_flow,
            limitations=["no_acknowledgement_required"],
            number_of_resources=2,
            number_of_channels=1,
        )

    opencode_calls = []

    async def fake_run_opencode_agent(agent_name, _cfg, **kwargs):
        opencode_calls.append((agent_name, kwargs.get("kickoff", "")))
        return {
            "status": "success",
            "events": 0,
            "stderr_tail": [],
            "returncode": 0,
            "provider": None,
            "model": None,
        }

    monkeypatch.setattr(design_module, "extract_coordination_attributes", fake_extract)
    monkeypatch.setattr(design_module, "run_opencode_agent", fake_run_opencode_agent)

    result = await design_module.run_design(
        "Coordinate multiple agents in a sequential handoff.",
        name=f"pytest_{mode}_dispatch",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    decision = json.loads((ws / "spec" / "procedure_decision.json").read_text(encoding="utf-8"))
    context = json.loads((ws / "spec" / "procedure_execution_context.json").read_text(encoding="utf-8"))
    prompt_path = ws / "spec" / "procedure_execution_prompt.txt"

    assert decision["selected_procedure"] == mode
    assert context["selected_procedure"] == mode
    if mode == "parameterized_reuse":
        assert not prompt_path.exists()
        assert not opencode_calls
        assert result.status == "parameterized_reuse_execution_failed"
    else:
        prompt = prompt_path.read_text(encoding="utf-8")
        assert opencode_calls
        assert opencode_calls[0][1] == prompt
        assert f'"selected_procedure": "{mode}"' in prompt
        assert "not allowed to select, substitute, or recommend another procedure" in prompt
    assert result.status != "procedure_decision_complete"
    execution_audit = json.loads(
        (ws / "spec" / "procedure_execution.json").read_text(encoding="utf-8")
    )
    assert execution_audit["selected_procedure"] == mode
    assert execution_audit["llm_expected"] is (mode != "parameterized_reuse")
    assert execution_audit["allowed_fields"] == (
        decision["parameterizable_fields"]
        if mode == "parameterized_reuse"
        else decision["adaptable_fields"]
    )


@pytest.mark.asyncio
async def test_run_design_reports_attribute_extraction_failure(monkeypatch):
    def fake_extract(query, *, model=None, client=None):
        raise AttributeExtractionError("provider unavailable")

    monkeypatch.setattr(design_module, "extract_coordination_attributes", fake_extract)

    result = await design_module.run_design(
        "Coordinate three robots through a shared corridor.",
        name="pytest_attr_extract_failure",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    timing = json.loads((ws / "output" / "pipeline_timing_report.json").read_text(encoding="utf-8"))

    assert result.status == "attribute_extraction_failed"
    assert not (ws / "spec" / "extracted_coordination_attributes.json").exists()
    assert timing["llm_attribute_extraction"]["template_mapping_status"] == "attribute_extraction_failed"


@pytest.mark.asyncio
async def test_persistent_taskspec_contradiction_stops_before_template_validator(monkeypatch):
    task_spec = {
        "task_id": "task_agent_cap",
        "query_id": "query_agent_cap",
        "user_query": "Coordinate at most two robots.",
        "route": "multi_agent",
        "constraints": {"max_agents": 2},
    }
    before = json.dumps(task_spec, sort_keys=True)
    calls = []

    def contradictory_extract(
        _query=None,
        *,
        task_spec=None,
        original_request=None,
        correction_feedback=None,
        model=None,
        client=None,
    ):
        calls.append(correction_feedback)
        return ExtractedCoordinationAttributes(
            coordination_patterns=["Exclusive Resource Access"],
            number_of_agents=3,
            agent_roles=["robot"],
            communication_flow=[],
            limitations=[],
            number_of_resources=1,
            number_of_channels=2,
        )

    class UnexpectedValidator:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("Template validator must not run after persistent contradiction")

    monkeypatch.setattr(design_module, "extract_coordination_attributes", contradictory_extract)
    monkeypatch.setattr(design_module, "DeterministicTemplateEngine", UnexpectedValidator)

    result = await design_module.run_design(
        "Secondary original wording.",
        task_spec=task_spec,
        name="pytest_taskspec_persistent_contradiction",
        model="openrouter/test-model",
        timeout=1,
    )

    ws = Path(result.workspace)
    report = json.loads(
        (ws / "spec" / "attribute_validation_report.json").read_text(encoding="utf-8")
    )
    assert result.status == "attribute_extraction_failed"
    assert len(calls) == 3
    assert calls[0] is None
    assert all("number_of_agents" in feedback for feedback in calls[1:])
    assert report["status"] == "failed"
    assert report["attempts"] == 3
    assert json.dumps(task_spec, sort_keys=True) == before
    assert not (ws / "spec" / "template_validation_results.json").exists()
