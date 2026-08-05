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


def _tracked_camera(track_segments, *, max_persons=None, frames=120):
    """A camera whose action classifier produced the given per-track timelines."""
    tracks = [
        {
            "trackId": track_id,
            "timeline": [
                {"t": stamp, "action": action, "kept": True} for stamp, action in points
            ],
        }
        for track_id, points in track_segments.items()
    ]
    detections = {"framesAnalyzed": frames, "trackActions": {tid: "sit" for tid in track_segments}}
    if max_persons is not None:
        detections["maxPersons"] = max_persons
    return {"inference": {"action-ava": {"data": {"tracks": tracks, "detections": detections}}}}


def test_one_person_reacquired_many_times_is_not_many_people():
    # The tracker loses and reacquires a single seated person, minting a new ID each
    # time. The IDs never coexist, so this is one person, not four.
    camera = _tracked_camera({
        "1": [(0.0, "sit"), (0.3, "sit")],
        "2": [(0.6, "sit"), (0.9, "sit")],
        "3": [(1.2, "sit")],
        "4": [(1.5, "sit")],
    }, max_persons=1)

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["activityCountsUnion"]["sit"] == 4
    assert summary["activityCounts"]["sit"] == 1
    assert summary["activityCountMethods"]["sit"] == "concurrent"


def test_simultaneous_sitters_are_counted_separately():
    # Two IDs classified as sitting at the same instants are genuinely two people.
    camera = _tracked_camera({
        "1": [(0.0, "sit"), (0.3, "sit")],
        "2": [(0.0, "sit"), (0.3, "sit")],
    }, max_persons=2)

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["activityCounts"]["sit"] == 2
    assert summary["activityCountMethods"]["sit"] == "concurrent"


def test_activity_count_is_clamped_to_detected_occupancy():
    # No per-track timelines, so the count falls back to the track union and must
    # not exceed the number of people the detector ever saw at once.
    camera = {"inference": {"action-ava": {"data": {"detections": {
        "maxPersons": 2,
        "framesAnalyzed": 120,
        "trackActions": {str(index): "sit" for index in range(9)},
    }}}}}

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["activityCountsUnion"]["sit"] == 9
    assert summary["activityCounts"]["sit"] == 2
    assert summary["activityCountMethods"]["sit"] == "union-clamped"


def _confidence_camera(count, method, *, frames=120):
    return {
        "camera": "cam1", "peakPeople": 5, "lastPeople": 2, "framesAnalyzed": frames,
        "activityCounts": {"sit": count}, "activityCountMethods": {"sit": method},
    }


def test_confidence_reflects_evidence_quality():
    strong, strong_notes = web_data_agent._derive_confidence(
        [_confidence_camera(2, "concurrent"), _confidence_camera(2, "concurrent")], ["sit"], []
    )
    union, union_notes = web_data_agent._derive_confidence(
        [_confidence_camera(28, "union")], ["sit"], []
    )
    disagreeing, disagreeing_notes = web_data_agent._derive_confidence(
        [_confidence_camera(2, "concurrent"), _confidence_camera(9, "concurrent")], ["sit"], []
    )
    failed, failed_notes = web_data_agent._derive_confidence(
        [_confidence_camera(2, "concurrent")], ["sit"], ["upstream boom"]
    )

    assert strong > 0.9 and not strong_notes
    assert union < 0.5 and any("tracking segments" in note for note in union_notes)
    assert disagreeing < strong and any("disagreed" in note for note in disagreeing_notes)
    assert failed < strong and any("failed" in note for note in failed_notes)
    assert web_data_agent._derive_confidence([], ["sit"], []) == (
        0.0, ["No camera returned usable inference data."]
    )


def test_model_cannot_raise_confidence_above_derived(monkeypatch):
    draft = {
        "confidence": 0.4,
        "limitations": ["Derived caveat that must survive."],
        "cameras": [],
    }
    monkeypatch.setattr(
        web_data_agent,
        "_call_model_json",
        lambda **_kwargs: ({"answer": "Two people were sitting.", "confidence": 0.99,
                            "limitations": ["Model caveat."]}, "some-model"),
    )

    result = web_data_agent._model_answer(
        provider="openrouter", model="some-model", question="how many?", draft=draft
    )

    assert result["confidence"] == 0.4
    assert "Derived caveat that must survive." in result["limitations"]
    assert "Model caveat." in result["limitations"]


def test_model_may_lower_confidence(monkeypatch):
    draft = {"confidence": 0.8, "limitations": [], "cameras": []}
    monkeypatch.setattr(
        web_data_agent,
        "_call_model_json",
        lambda **_kwargs: ({"answer": "Unclear.", "confidence": 0.2, "limitations": []}, "some-model"),
    )

    result = web_data_agent._model_answer(
        provider="openrouter", model="some-model", question="how many?", draft=draft
    )

    assert result["confidence"] == 0.2


def test_occupancy_series_is_kept_for_charting():
    camera = {"inference": {"yolo": {"data": {"detections": {
        "maxPersons": 3,
        "framesAnalyzed": 4,
        "timeline": [
            {"t": 0.0, "count": 1}, {"t": 0.2, "count": 3},
            {"t": 0.4, "count": 2}, {"t": 0.6, "count": 2},
        ],
    }}}}}

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["occupancySamples"] == [
        {"t": 0.0, "count": 1}, {"t": 0.2, "count": 3},
        {"t": 0.4, "count": 2}, {"t": 0.6, "count": 2},
    ]
    # The answer still reports one number; the series exists only for the chart.
    assert summary["lastPeople"] == 2
    assert summary["peakPeople"] == 3


def test_geo_centroids_override_detector_counts():
    # YOLO also counts people on the TV screen and through the window; the geo
    # model only emits centroids for depth-verified bodies in the room, so its
    # track count must win when it is available.
    camera = {
        "metadata": {"durationSec": 3.0},
        "inference": {
            "yolo26m": {"data": {"detections": {
                "maxPersons": 6,
                "framesAnalyzed": 15,
                "timeline": [{"t": round(0.2 * i, 1), "count": 4} for i in range(15)],
            }}},
            "geo": {"data": {"centroids": {"persons": {
                "1": [{"t": 0.2, "src": "depth-hip"}, {"t": 0.4, "src": "depth-hip"},
                      {"t": 0.6, "src": "depth-hip"}, {"t": 0.8, "src": "depth-hip"}],
                "2": [{"t": 0.6, "src": "depth-hip"}, {"t": 0.8, "src": "depth-hip-hold"},
                      {"t": 1.0, "src": "depth-hip"}],
                # Mostly shoulder-based: a partially visible figure behind the
                # window, not someone in the room. Must not be counted.
                "3": [{"t": 2.6, "src": "depth-shoulder"}, {"t": 2.8, "src": "depth-shoulder"},
                      {"t": 3.0, "src": "depth-hip"}],
            }}}},
        },
    }

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["peakPeople"] == 2
    assert summary["lastPeople"] == 0
    samples = {point["t"]: point["count"] for point in summary["occupancySamples"]}
    assert samples[0.0] == 0
    assert samples[0.6] == 2
    assert samples[2.8] == 0
    # Zero-filled through the recording's duration, not stopped at the last sighting.
    assert samples[3.0] == 0


def test_detector_last_count_follows_merged_series():
    # Without geo data the reported single number must match the plotted series,
    # not whichever model's timeline was walked last.
    camera = {"inference": {
        "yolo26m": {"data": {"detections": {
            "framesAnalyzed": 3,
            "timeline": [{"t": 0.0, "count": 2}, {"t": 0.2, "count": 2}],
        }}},
        "yolo26n-pose": {"data": {"detections": {
            "framesAnalyzed": 3,
            "timeline": [{"t": 0.0, "count": 1}, {"t": 0.2, "count": 1}],
        }}},
    }}

    summary = web_data_agent._camera_summary("cam1", camera)

    assert summary["occupancySamples"][-1] == {"t": 0.2, "count": 2}
    assert summary["lastPeople"] == 2


def test_occupancy_series_merges_cameras_by_highest_count():
    cameras = [
        {"occupancySamples": [{"t": 0.0, "count": 1}, {"t": 1.0, "count": 1}]},
        {"occupancySamples": [{"t": 0.0, "count": 2}, {"t": 2.0, "count": 3}]},
    ]

    merged = web_data_agent._merge_occupancy_samples(cameras)

    assert merged == [
        {"t": 0.0, "count": 2}, {"t": 1.0, "count": 1}, {"t": 2.0, "count": 3},
    ]


def test_occupancy_series_does_not_extend_beyond_observations():
    # A wall camera's video runs to 3s but person tracking only covered the
    # first second; the chart must stop there, not fabricate an empty room.
    cameras = [
        {"durationSec": 1.0, "occupancySamples": [{"t": 0.0, "count": 2}, {"t": 1.0, "count": 1}]},
        {"durationSec": 3.0, "occupancySamples": []},
    ]

    merged = web_data_agent._merge_occupancy_samples(cameras)

    assert merged[-1] == {"t": 1.0, "count": 1}


def test_downsampling_drops_single_frame_jitter():
    # The detector's count flickers for one frame; that is noise, not someone who
    # entered and left the room between two frames, so it must not reach the chart.
    samples = {float(index): 1 for index in range(400)}
    samples[137.0] = 9

    reduced = web_data_agent._downsample_series(samples, max_points=10)

    assert len(reduced) <= 10
    assert max(point["count"] for point in reduced) == 1


def test_downsampling_keeps_a_sustained_change():
    # A change lasting a meaningful slice of the recording survives reduction.
    samples = {float(index): 1 for index in range(400)}
    for index in range(200, 260):
        samples[float(index)] = 4

    reduced = web_data_agent._downsample_series(samples, max_points=10)

    assert max(point["count"] for point in reduced) == 4


def test_downsampling_leaves_short_series_untouched():
    samples = {0.0: 1, 1.0: 2, 2.0: 1}

    assert web_data_agent._downsample_series(samples, max_points=180) == [
        {"t": 0.0, "count": 1}, {"t": 1.0, "count": 2}, {"t": 2.0, "count": 1},
    ]


def _carla_traces():
    return [
        {"trace_id": "tx_1785118651_0", "scenario": None, "map": "Carla/Maps/Town10HD", "actor_count": 1, "junction": 468, "has_ground_truth": False, "timestamp": 1785118651},
        {"trace_id": "scenario_4_1782927647", "scenario": 4, "map": "Carla/Maps/Town10HD", "actor_count": 5, "junction": None, "has_ground_truth": True, "timestamp": 1782927647},
        {"trace_id": "scenario_7_1782900000", "scenario": 7, "map": "Carla/Maps/Town10HD", "actor_count": 2, "junction": None, "has_ground_truth": True, "timestamp": 1782900000},
    ]


def _carla_full_doc(trace_id="scenario_4_1782927647"):
    return {
        "trace_id": trace_id,
        "ground_truth": {
            "entities": [
                {"local_id": "300", "type": "walker", "name": "walker.pedestrian.0022"},
                {"local_id": "301", "type": "walker", "name": "walker.pedestrian.0011"},
                {"local_id": "500", "type": "intersection", "name": "junction_468"},
            ],
            "claims": [
                {"claim_type": "object_presence", "natural_language": "Pedestrian 300 is present in the scene.", "confidence": 1.0, "polarity": "positive"},
            ],
        },
        "log": [
            {"tick": 0, "sim_elapsed_s": 0.0, "scenario": 4, "space": "Carla/Maps/Town10HD", "crosswalk_occupied": True, "crosswalk_pedestrian_fraction": 0.03, "collision_count_total": 0},
            {"tick": 1, "sim_elapsed_s": 0.1, "scenario": 4, "space": "Carla/Maps/Town10HD", "crosswalk_occupied": False, "crosswalk_pedestrian_fraction": 0.01, "collision_count_total": 1},
        ],
        "trajectories": [
            {"meta": {"id": 300, "role": "walker"}, "first_tick": 0, "last_tick": 1},
            {"meta": {"id": 301, "role": "walker"}, "first_tick": 1, "last_tick": 1},
        ],
    }


def _patch_carla_server(monkeypatch, requested):
    def fake_request(url, *, timeout, max_bytes, api_key=""):
        requested.append({"url": url, "api_key": api_key})
        if url.endswith("/traces"):
            return _carla_traces()
        if url.endswith("/full"):
            return _carla_full_doc()
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(web_data_agent, "_request_json", fake_request)


def test_carla_retrieval_defaults_to_first_scenario_trace(monkeypatch):
    requested = []
    _patch_carla_server(monkeypatch, requested)

    evidence = web_data_agent.retrieve(
        {"source_url": "http://sim.example:8420/ui", "source_mode": "carla", "question": "What happened?", "source_api_key": "sim-key"},
        "carla_agent",
    )

    assert evidence["source_kind"] == "carla-trace-server"
    assert evidence["selection"]["mode"] == "default_scenario"
    assert evidence["selected"]["trace_id"] == "scenario_4_1782927647"
    assert evidence["source_count"] == 3
    # The /ui page suffix is stripped and every request carries the source key.
    assert all(item["api_key"] == "sim-key" for item in requested)
    assert requested[0]["url"] == "http://sim.example:8420/traces"


def test_carla_retrieval_matches_scenario_named_in_question(monkeypatch):
    requested = []
    _patch_carla_server(monkeypatch, requested)

    evidence = web_data_agent.retrieve(
        {"source_url": "http://sim.example:8420", "source_mode": "carla", "question": "How busy was scenario 7?"},
        "carla_agent",
    )

    assert evidence["selection"]["mode"] == "question_match"


def test_carla_answer_counts_pedestrians_and_keeps_timeline(monkeypatch):
    requested = []
    _patch_carla_server(monkeypatch, requested)
    evidence = web_data_agent.retrieve(
        {"source_url": "http://sim.example:8420/ui", "source_mode": "carla", "question": "How many pedestrians appear?"},
        "carla_agent",
    )

    answer = web_data_agent.synthesize(
        {"evidence_packets": [evidence], "question": "How many pedestrians appear?"},
        "carla_agent",
    )

    assert "2 people (pedestrians)" in answer["answer"]
    assert answer["confidence"] == 0.9
    assert answer["occupancyTimeline"], "the actor-presence series must survive for charting"
    assert any("simulation-relative" in item for item in answer["limitations"])
    assert answer["simulation"]["collisions_total"] == 1
    assert answer["generation_mode"] == "deterministic"


def test_carla_source_key_is_appended_as_query_parameter(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return _Response([])

    monkeypatch.setattr(web_data_agent.urllib.request, "urlopen", fake_urlopen)

    web_data_agent._request_json("http://sim.example:8420/traces", timeout=5, max_bytes=1024, api_key="secret-key")

    assert captured["url"] == "http://sim.example:8420/traces?api_key=secret-key"


def test_carla_retrieval_honors_trace_override(monkeypatch):
    requested = []
    _patch_carla_server(monkeypatch, requested)

    evidence = web_data_agent.retrieve(
        {
            "source_url": "http://sim.example:8420/ui",
            "source_mode": "carla",
            "question": "What happened?",
            "recording_override": {"recordingId": "scenario_4_1782927647", "day": "", "rec": ""},
        },
        "carla_agent",
    )

    assert evidence["selection"]["mode"] == "recording_override"
    assert evidence["selected"]["trace_id"] == "scenario_4_1782927647"


def test_carla_retrieval_falls_back_when_override_is_unknown(monkeypatch):
    requested = []
    _patch_carla_server(monkeypatch, requested)

    evidence = web_data_agent.retrieve(
        {
            "source_url": "http://sim.example:8420",
            "source_mode": "carla",
            "question": "What happened?",
            "recording_override": "no_such_trace",
        },
        "carla_agent",
    )

    assert evidence["selection"]["mode"] == "default_scenario"
    assert "no_such_trace" in evidence["selection"]["reason"]
