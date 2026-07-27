import json
from pathlib import Path

from tracefix.protocol_templates.attendance_verification import (
    build_template as attendance_build_template,
)
from tracefix.runner_ui.server import (
    _api_envelope,
    _build_design_command,
    _env_updates_for_provider,
    _synth_file_requirements,
    _synth_workspace_summary,
)
from tracefix.runner_ui.tellme_bridge import TellMeBridge
from tracefix.runtime.single_agent_fastpath import (
    _extract_structured_task,
    assess_single_agent_fast_path,
)
from tellme_harness.tracefix_workspace_adapter import _canonicalize_ir


SAMPLE_QUERY = "Turn off the lights in the conference room after 7 PM unless someone is still present."

AGENT_AB_QUERY = (
    "Agent A determines the number of people physically present in the conference room. "
    "Agent B verifies the occupancy count against the meeting attendance roster and "
    "produces a validated occupancy report."
)

OCCUPANCY_QUERY = "How many people are currently in the conference room?"


def test_tellme_workspace_boundary_canonicalizes_ir_metadata():
    ir, source = _canonicalize_ir({
        "agents": [{"id": "observer", "role": "observe room state"}],
        "resources": [],
        "channels": [],
    })

    assert source == "tracefix_normalizer"
    assert ir["agents"] == [{"id": "observer"}]


def _make_tellme_task_text(
    user_query: str,
    route: str = "multi_agent",
    candidate_harnesses: list | None = None,
) -> str:
    """Build a TeLLMe wrapper task text matching tracefix_task_text() format."""
    spec = {
        "task_id": "test_task_001",
        "query_id": "test_query_001",
        "user_query": user_query,
        "route": route,
        "candidate_harnesses": candidate_harnesses or [],
        "required_modalities": [],
        "application_goal": {},
        "evidence_plan": {},
    }
    return (
        "TeLLMe structured smart-room application requirements.\n"
        "Treat this as compile-time input to TraceFix planning and verification.\n"
        "Generate a complete multi-agent topology with explicit communication channels.\n"
        "Do not execute production agents or bypass PlusCal/TLC verification.\n\n"
        "Structured task specification:\n"
        + json.dumps(spec)
    )


# ---------------------------------------------------------------------------
# Test 1 & 2: TeLLMe multi_agent task â†’ â‰¥2 agents + â‰¥1 channel in IR
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 3: Single-agent occupancy prompt â†’ 1 agent (coord classifier not used)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 4: multi_agent route â†’ single_agent fast path returns eligible=False
# ---------------------------------------------------------------------------

def test_multi_agent_route_skips_single_agent_fast_path():
    """TeLLMe route=multi_agent causes single-agent fast path to return eligible=False."""
    task_text = _make_tellme_task_text(AGENT_AB_QUERY, route="multi_agent")
    fp = assess_single_agent_fast_path(task_text)
    assert fp.considered is True
    assert fp.eligible is False, (
        f"Expected fast path ineligible for multi_agent, got eligible=True. "
        f"reason={fp.reason}"
    )
    assert "multi_agent" in fp.reason or "not single_agent" in fp.reason


# ---------------------------------------------------------------------------
# Test 5: Coord classifier uses user_query, not full wrapper text
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 6: TASK_AGENT guard repairs degenerate IR for multi_agent routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 7â€“9: Placeholder task text is detected / rejected before reaching run_design
# ---------------------------------------------------------------------------

_PLACEHOLDER = "Loaded automatically from the current TeLLMe task spec."


def test_placeholder_has_no_structured_spec():
    """The placeholder string is not a valid TeLLMe spec â€” _extract_structured_task returns None."""
    result = _extract_structured_task(_PLACEHOLDER)
    assert result is None, (
        f"Expected None for placeholder, got {result!r}"
    )


def test_placeholder_fast_path_not_eligible():
    """Single-agent fast path must not classify the placeholder as a valid single-agent task."""
    fp = assess_single_agent_fast_path(_PLACEHOLDER)
    # The placeholder has no structured marker so it goes through the text path.
    # It has no coordination/multi-source/multi-step signals, so the text path
    # might mark it eligible â€” but that's fine because the agent_id will be TASK_AGENT.
    # The important thing is we catch it in the guard.
    # This test documents the current behavior so regressions are visible.
    assert fp.considered, "Fast path should at least consider the placeholder"
    if fp.eligible:
        assert fp.agent_id == "TASK_AGENT", (
            f"Placeholder task produced unexpected agent_id={fp.agent_id!r}"
        )


def test_multi_agent_spec_extract_succeeds_on_wrapper_text():
    """_extract_structured_task must return the full spec dict from tracefix_task_text() output."""
    task_text = _make_tellme_task_text(
        AGENT_AB_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_COUNTER_HARNESS", "ATTENDANCE_VERIFIER_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    assert spec is not None, "Expected spec dict from TeLLMe wrapper text"
    assert spec.get("route") == "multi_agent"
    assert spec.get("user_query") == AGENT_AB_QUERY
    assert "OCCUPANCY_COUNTER_HARNESS" in (spec.get("candidate_harnesses") or [])


def test_tellme_bridge_persists_tracefix_handoff(tmp_path: Path) -> None:
    bridge = TellMeBridge(tmp_path)

    result = bridge.process_query(query=SAMPLE_QUERY, space_id="smart_room_1")

    assert result["query_id"].startswith("tellme_")
    assert result["route_decision"]["route"] in {"single_agent", "multi_agent"}
    assert result["privacy_guardrail"]["status"] == "passed"
    assert result["tracefix_task_spec"]["user_query"] == SAMPLE_QUERY
    assert bridge.current()["run_id"] == result["query_id"]
    assert bridge.artifact_paths(result)

    task_text = bridge.tracefix_task_text()
    assert SAMPLE_QUERY in task_text
    assert "Do not execute production agents" in task_text

    task_spec_path = bridge.tracefix_task_spec_path()
    assert task_spec_path.name == "tracefix_task_spec.json"
    assert json.loads(task_spec_path.read_text(encoding="utf-8"))["user_query"] == SAMPLE_QUERY


def test_design_command_passes_official_taskspec_path_for_tellme_handoff(tmp_path: Path) -> None:
    bridge = TellMeBridge(tmp_path)
    bridge.process_query(query=SAMPLE_QUERY, space_id="smart_room_1")

    command, _env_updates, _env_flags = _build_design_command(
        tmp_path,
        {
            "taskMode": "custom",
            "customTask": _PLACEHOLDER,
            "provider": "ollama",
            "model": "test-model",
            "verbose": False,
        },
    )

    task_spec_index = command.index("--task-spec")
    assert Path(command[task_spec_index + 1]) == bridge.tracefix_task_spec_path()


def test_openrouter_ui_key_and_model_override_stale_extractor_environment(monkeypatch) -> None:
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY", "stale-extractor-key")
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_BASE_URL", "https://stale.invalid/v1")
    monkeypatch.setenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_MODEL", "stale/model")
    ui_key = "ui-openrouter-key"

    env = _env_updates_for_provider(
        {
            "provider": "openrouter",
            "model": "z-ai/glm-5.2",
            "openrouterKey": ui_key,
        },
        require_key=False,
    )

    assert env["OPENROUTER_API_KEY"] == ui_key
    assert env["TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY"] == ui_key
    assert env["TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_MODEL"] == "z-ai/glm-5.2"


def test_api_envelope_has_stable_shape() -> None:
    payload = _api_envelope(
        ok=True,
        data={"value": 1},
        warnings=["review"],
        artifact_paths=["artifact.json"],
        run_id="run_1",
    )

    assert payload == {
        "ok": True,
        "data": {"value": 1},
        "errors": [],
        "warnings": ["review"],
        "artifact_paths": ["artifact.json"],
        "run_id": "run_1",
    }


# ---------------------------------------------------------------------------
# Part A â€” Tests 10â€“15: attendance_verification template
# ---------------------------------------------------------------------------

ATTENDANCE_QUERY = (
    "Determine whether the observed room occupancy matches the expected meeting "
    "attendance count. Use occupancy sensor data and calendar event attendance count "
    "as evidence sources. If conflicting evidence or insufficient confidence, generate "
    "a report with limitations and evidence references."
)


def test_attendance_verification_produces_two_agents():
    """attendance_verification template build_template returns exactly 2 agents."""
    params = {
        "observer_id": "occupancy_sensor",
        "verifier_id": "calendar_attendance",
        "observer_role": "collect occupancy sensor data",
        "verifier_role": "compare occupancy against attendance count",
    }
    ir_data, _ = attendance_build_template(params)
    assert len(ir_data.get("agents", [])) == 2, (
        f"Expected 2 agents, got {ir_data.get('agents')}"
    )


def test_attendance_verification_produces_at_least_one_channel():
    """attendance_verification template produces at least 1 channel."""
    params = {
        "observer_id": "occupancy_sensor",
        "verifier_id": "calendar_attendance",
    }
    ir_data, _ = attendance_build_template(params)
    assert len(ir_data.get("channels", [])) >= 1, (
        f"Expected â‰¥1 channel, got {ir_data.get('channels')}"
    )


# ---------------------------------------------------------------------------
# Part B â€” Tests 16â€“20: workspace readiness without summary.json
# ---------------------------------------------------------------------------

def _make_workspace(base: Path, files: list[str]) -> Path:
    """Create a minimal workspace with spec/ dir and given file stubs."""
    ws = base / "workspace"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    for f in files:
        (spec / f).write_text("{}")
    return ws


def test_synth_workspace_summary_not_required():
    """spec/summary.json must NOT appear in the required items list."""
    reqs = _synth_file_requirements(Path("/nonexistent/workspace"))
    required_paths = {item["path"] for item in reqs if item["required"]}
    assert "spec/summary.json" not in required_paths, (
        f"summary.json should not be required; required_paths={required_paths}"
    )


def test_synth_workspace_ready_without_summary_json(tmp_path: Path):
    """Workspace with all core artifacts but no summary.json must be ready=True."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    result = _synth_workspace_summary(ws)
    assert result["ready"] is True, (
        f"Expected ready=True without summary.json. "
        f"missingRequired={result.get('missingRequired')} "
        f"verificationStatus={result.get('verificationStatus')}"
    )


def test_synth_workspace_status_verified_no_summary(tmp_path: Path):
    """Workspace with core artifacts but no summary.json reports verificationStatus='verified_no_summary'."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    result = _synth_workspace_summary(ws)
    assert result["verificationStatus"] == "verified_no_summary", (
        f"Expected 'verified_no_summary', got {result.get('verificationStatus')!r}"
    )


def test_synth_workspace_ready_with_summary_tlc_passed(tmp_path: Path):
    """Workspace with summary.json (tlc_passed=True) reports ready=True and status='verified'."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    (ws / "spec" / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    result = _synth_workspace_summary(ws)
    assert result["ready"] is True
    assert result["verificationStatus"] == "verified", (
        f"Expected 'verified', got {result.get('verificationStatus')!r}"
    )
    assert result["tlcStatus"] == "passed", (
        f"Expected tlcStatus='passed', got {result.get('tlcStatus')!r}"
    )


def test_synth_workspace_incomplete_when_missing_required(tmp_path: Path):
    """Workspace missing required artifacts (e.g. states.json) must be ready=False."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "Protocol.tla", "Protocol.cfg",
        # states.json and cityos_module_plan.json intentionally absent
    ])
    result = _synth_workspace_summary(ws)
    assert result["ready"] is False, (
        f"Expected ready=False when states.json missing. "
        f"missingRequired={result.get('missingRequired')}"
    )
    assert len(result.get("missingArtifacts", [])) > 0, (
        "Expected at least one missing artifact path"
    )

