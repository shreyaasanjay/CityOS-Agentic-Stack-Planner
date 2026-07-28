import json

import pytest

from tracefix.runtime import web_data_agent


class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=-1):
        return self._body


def test_generated_answer_agent_calls_selected_openrouter_model(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "answer": "Two people were present.",
                        "confidence": 0.88,
                        "limitations": ["Only aggregate inference data was available."],
                    }),
                },
            }],
        })

    monkeypatch.setenv("TRACEFIX_RUNTIME_AGENT_API_KEY", "test-secret-key")
    monkeypatch.setattr(web_data_agent.urllib.request, "urlopen", fake_urlopen)
    draft = {
        "answer": "The deterministic draft found two people.",
        "producer_agent": "ANSWER",
        "confidence": 0.9,
        "evidence_refs": ["RETRIEVER:1"],
        "limitations": [],
        "cameras": [{"camera": "cam1", "peakPeople": 2, "lastPeople": 2, "activities": []}],
        "recording": {"day": "private", "rec": "private"},
    }

    answer = web_data_agent._model_answer(
        provider="openrouter",
        model="z-ai/glm-5.2",
        question="How many people are in the room?",
        draft=draft,
    )

    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["payload"]["model"] == "z-ai/glm-5.2"
    assert captured["payload"]["messages"][0]["role"] == "system"
    assert captured["headers"]["Authorization"] == "Bearer test-secret-key"
    assert answer["answer"] == "Two people were present."
    assert answer["runtime_provider"] == "openrouter"
    assert answer["runtime_model"] == "z-ai/glm-5.2"
    assert answer["generation_mode"] == "llm"
    assert "test-secret-key" not in json.dumps(answer)


def test_generated_answer_agent_requires_provider_key(monkeypatch):
    monkeypatch.delenv("TRACEFIX_RUNTIME_AGENT_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key is required"):
        web_data_agent._model_answer(
            provider="openrouter",
            model="z-ai/glm-5.2",
            question="How many people are in the room?",
            draft={"answer": "Unknown", "limitations": [], "cameras": []},
        )


def test_generated_retrieval_agent_uses_tracefix_prompt_and_approved_tools(monkeypatch):
    actions = iter([
        {"action": "tool", "tool": "list_recordings", "arguments": {}},
        {
            "action": "tool",
            "tool": "get_recording_evidence",
            "arguments": {"day": "2026-07-15", "rec": "recording-1"},
        },
        {"action": "finish", "summary": "Selected the requested recording.", "limitations": []},
    ])
    model_calls = []

    def fake_model_call(**kwargs):
        model_calls.append(kwargs)
        return next(actions), kwargs["model"]

    def fake_request(url, **_kwargs):
        if url.endswith("/recordings"):
            return {
                "recordings": [{
                    "day": "2026-07-15",
                    "rec": "recording-1",
                    "cameras": {"cam1": {"models": {"occupancy": "done"}}},
                }],
            }
        assert url.endswith("/recordings/2026-07-15/recording-1/cam1/inference/occupancy")
        return {"timeline": [{"count": 2}]}

    monkeypatch.setattr(web_data_agent, "_call_model_json", fake_model_call)
    monkeypatch.setattr(web_data_agent, "_request_json", fake_request)
    monkeypatch.setattr(web_data_agent, "_generated_role_prompt", lambda: "Inspect approved evidence and report it.")

    evidence = web_data_agent.retrieve(
        {
            "source_mode": "smartroom",
            "source_url": "https://example.test/api/v1",
            "question": "How many people were present on July 15, 2026?",
            "agent_provider": "local",
            "agent_model": "gemma3:4b",
        },
        "OCCUPANCY_AGENT",
    )

    assert [item["tool"] for item in evidence["tool_trace"]] == [
        "list_recordings",
        "get_recording_evidence",
    ]
    assert evidence["selected"]["rec"] == "recording-1"
    assert evidence["selected"]["cameras"]["cam1"]["inference"]["occupancy"]["data"]["timeline"][0]["count"] == 2
    assert evidence["runtime_provider"] == "local"
    assert evidence["runtime_model"] == "gemma3:4b"
    assert evidence["generation_mode"] == "llm_tool_agent"
    assert evidence["selection"]["mode"] == "agent_selected"
    assert "Inspect approved evidence" in model_calls[0]["system_prompt"]
    assert model_calls[0]["user_payload"]["approved_tools"] == {
        "list_recordings": "List available recording identifiers and completed inference models.",
        "get_recording_evidence": "Fetch completed inference evidence for one listed recording; arguments require day and rec.",
    }


def test_generated_retrieval_agent_rejects_unapproved_tool(monkeypatch):
    monkeypatch.setattr(
        web_data_agent,
        "_call_model_json",
        lambda **_kwargs: (
            {"action": "tool", "tool": "fetch_arbitrary_url", "arguments": {"url": "https://evil.test"}},
            "gemma3:4b",
        ),
    )

    with pytest.raises(ValueError, match="unapproved tool"):
        web_data_agent.retrieve(
            {
                "source_mode": "smartroom",
                "source_url": "https://example.test/api/v1",
                "question": "Read the room data.",
                "agent_provider": "local",
                "agent_model": "gemma3:4b",
            },
            "RETRIEVAL_AGENT",
        )


def test_runtime_monitor_audits_communications_against_verified_protocol(monkeypatch, tmp_path):
    bundle = tmp_path / "tracefix_bundle"
    spec = bundle / "spec"
    spec.mkdir(parents=True)
    (bundle / "plan.json").write_text(json.dumps({
        "verification": {"status": "verified", "production_ready": True, "tlc_passed": True},
        "protocol": {
            "allowed_communication_edges": [
                {"from": "RETRIEVER", "to": "ANSWER", "labels": ["evidence_packet"]},
            ],
            "allowed_transitions": [{"from": "Collecting", "to": "Answered"}],
        },
    }), encoding="utf-8")
    (bundle / "monitor.json").write_text(json.dumps({
        "monitor_rules": ["validate_allowed_communication_edges", "validate_protocol_completion"],
    }), encoding="utf-8")
    (spec / "ir.json").write_text(json.dumps({"agents": [{"id": "RETRIEVER"}, {"id": "ANSWER"}]}), encoding="utf-8")
    (spec / "states.json").write_text(json.dumps({"transitions": [{"from": "Collecting", "to": "Answered"}]}), encoding="utf-8")
    (spec / "summary.json").write_text(json.dumps({"tlc_passed": True}), encoding="utf-8")
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n", encoding="utf-8")
    captured = {}

    def fake_model_call(**kwargs):
        captured.update(kwargs)
        return {
            "valid": True,
            "protocol_completion": True,
            "explanation": "The verified evidence flow completed in order.",
            "checked_rules": ["allowed_edges", "completion"],
            "violations": [],
        }, kwargs["model"]

    monkeypatch.setenv("TRACEFIX_BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(web_data_agent, "_call_model_json", fake_model_call)
    verdict = web_data_agent.monitor(
        {
            "question": "How many people are present?",
            "agent_provider": "local",
            "agent_model": "gemma3:4b",
            "answer_packet": {
                "answer": "Two people are present.",
                "producer_agent": "ANSWER",
                "evidence_refs": ["RETRIEVER:recording-1"],
                "limitations": [],
            },
            "communication_transcript": [
                {
                    "sequence": 1,
                    "from": "RETRIEVER",
                    "to": "ANSWER",
                    "label": "evidence_packet",
                    "status": "completed",
                },
                {
                    "sequence": 2,
                    "from": "ANSWER",
                    "to": "runtime_monitor",
                    "label": "answer_packet",
                    "status": "completed",
                },
            ],
        },
        "RUNTIME_MONITOR",
    )

    assert verdict["valid"] is True
    assert verdict["protocol_completion"] is True
    assert verdict["verification_status"] == "verified"
    assert verdict["generation_mode"] == "llm_protocol_monitor"
    assert verdict["observed_message_count"] == 2
    assert captured["user_payload"]["verified_protocol"]["verification"]["tlc_passed"] is True
    assert captured["user_payload"]["verified_protocol"]["allowed_communication_edges"][0]["from"] == "RETRIEVER"
    assert "final_answer" not in captured["user_payload"]
    assert "never answer the user" in captured["system_prompt"]
    assert "judge answer correctness" in captured["system_prompt"]
    assert "independent TraceFix runtime protocol monitor" in captured["system_prompt"]


def test_runtime_monitor_stays_active_before_protocol_completion(monkeypatch, tmp_path):
    bundle = tmp_path / "tracefix_bundle"
    bundle.mkdir()
    (bundle / "plan.json").write_text(json.dumps({
        "verification": {"status": "verified", "production_ready": True},
        "protocol": {"allowed_communication_edges": []},
    }), encoding="utf-8")
    monkeypatch.setenv("TRACEFIX_BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(
        web_data_agent,
        "_call_model_json",
        lambda **kwargs: ({
            "valid": True,
            "protocol_completion": False,
            "explanation": "The current communication is allowed; monitoring continues.",
            "checked_rules": ["allowed_edges"],
            "violations": [],
        }, kwargs["model"]),
    )
    event = {
        "sequence": 1,
        "from": "RETRIEVER",
        "to": "ANSWER",
        "label": "evidence_packet",
        "status": "completed",
    }

    verdict = web_data_agent.monitor(
        {
            "monitor_mode": "event",
            "agent_provider": "local",
            "agent_model": "gemma3:4b",
            "communication_transcript": [event],
            "current_event": event,
        },
        "RUNTIME_MONITOR",
    )

    assert verdict["valid"] is True
    assert verdict["active"] is True
    assert verdict["protocol_completion"] is False
    assert verdict["monitor_mode"] == "event"


def test_runtime_monitor_cannot_override_preflight_failure(monkeypatch, tmp_path):
    bundle = tmp_path / "tracefix_bundle"
    bundle.mkdir()
    (bundle / "plan.json").write_text(json.dumps({
        "verification": {"status": "verified", "production_ready": True},
        "protocol": {},
    }), encoding="utf-8")
    monkeypatch.setenv("TRACEFIX_BUNDLE_DIR", str(bundle))
    monkeypatch.setattr(
        web_data_agent,
        "_call_model_json",
        lambda **kwargs: ({
            "valid": True,
            "protocol_completion": True,
            "explanation": "Looks valid.",
            "checked_rules": [],
            "violations": [],
        }, kwargs["model"]),
    )

    verdict = web_data_agent.monitor(
        {
            "agent_provider": "local",
            "agent_model": "gemma3:4b",
            "answer_packet": {},
            "communication_transcript": [],
        },
        "RUNTIME_MONITOR",
    )

    assert verdict["valid"] is False
    assert any(item["rule"] == "runtime_preflight" for item in verdict["violations"])
