import asyncio
import json
from datetime import datetime, timezone

from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness, CityOSHarnessConfig
from tracefix.runtime.cityos_synthesizer import (
    _agent_toml,
    _app_py,
    _dockerfile,
    _execution_agents,
    _requirements_txt,
)


def _config(tmp_path, bundle):
    return CityOSHarnessConfig(
        app_kind="agent",
        agent_id="WRITER",
        bundle_dir=bundle,
        runtime_mode="cityos_data",
        autorun=False,
        output_dir=tmp_path / "out",
        ready_dir=tmp_path / "ready",
        startup_cmd=[],
        handler_cmd=[],
        handler_timeout=60.0,
        verbose=True,
        task_id="",
    )


def test_cityos_harness_resolves_bundle_prompt_and_records_frames(tmp_path):
    bundle = tmp_path / "tracefix_bundle"
    workspace = bundle / "workspace"
    prompt = workspace / "prompts" / "runtime_b" / "WRITER.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("# writer\n", encoding="utf-8")
    frame = tmp_path / "occupancy.json"
    frame.write_text('{"occupied": true}\n', encoding="utf-8")
    (bundle / "plan.json").write_text(json.dumps({
        "version": "0.1",
        "application": {"name": "demo_cityos_app"},
    }), encoding="utf-8")
    (bundle / "agent.json").write_text(json.dumps({
        "name": "WRITER",
        "prompt_path": "prompts/runtime_b/WRITER.md",
    }), encoding="utf-8")

    harness = CityOSAgentHarness(_config(tmp_path, bundle))

    assert harness.task_id == "demo_cityos_app"
    assert harness.prompt_path() == prompt
    payload = harness.readiness_payload()
    assert payload["agent"] == "WRITER"
    assert payload["runtime_mode"] == "cityos_data"
    assert payload["handler_configured"] is False
    assert payload["prompt_path"] == str(prompt)

    ready_path = asyncio.run(harness.write_readiness())
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    assert ready["kind"] == "agent"
    assert ready["plan_version"] == "0.1"

    record = asyncio.run(harness.record_frame(
        "occupancy-events",
        frame,
        datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc),
    ))
    record_path = tmp_path / "out" / "frames"
    assert record["stream"] == "occupancy-events"
    assert record["input_exists"] is True
    assert record["input_size_bytes"] == frame.stat().st_size
    assert str(record_path) in record["record_path"]


def test_generated_cityos_app_delegates_to_shared_data_harness():
    app_source = _app_py("agent", "WRITER")
    toml = _agent_toml("demo-app", "WRITER")
    dockerfile = _dockerfile("demo-app", "demo_app")

    assert "CityOSAgentHarness.from_env" in app_source
    assert "tracefix.runtime.cli" not in app_source
    assert "tracefix.runtime.opencode_adapter" not in app_source
    assert "TRACEFIX_RUNTIME_MODE=cityos_data" in toml
    assert "TRACEFIX_HANDLER_CMD=python3 -m tracefix.runtime.web_data_agent" in toml
    assert "TRACEFIX_HANDLER_TIMEOUT=60" in toml
    assert "TRACEFIX_OUTPUT_DIR=/app/demo-app/tracefix_output" in toml
    assert "TRACEFIX_OPENCODE_BIN" not in toml
    assert "TRACEFIX_COORD_URL" not in toml
    assert "opencode-ai" not in dockerfile
    assert "nodejs npm" not in dockerfile
    assert _requirements_txt() == ""


def test_execution_agents_add_distinct_retriever_and_answer_roles():
    empty_roles = _execution_agents({"agents": []})
    assert [agent["name"] for agent in empty_roles] == ["TRACEFIX_RETRIEVER", "TRACEFIX_ANSWER"]

    single_roles = _execution_agents({"agents": [{"name": "OBSERVER"}]})
    assert [agent["name"] for agent in single_roles] == ["OBSERVER", "TRACEFIX_ANSWER"]

    answer_only_roles = _execution_agents({"agents": [{"name": "ANSWER"}]})
    assert [agent["name"] for agent in answer_only_roles] == ["TRACEFIX_RETRIEVER", "ANSWER"]
