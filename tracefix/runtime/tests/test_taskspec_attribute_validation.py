import json

from tellme_harness.schemas import TraceFixTaskSpec
from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import (
    ExtractedCoordinationAttributes,
    build_attribute_extraction_prompt,
    extract_coordination_attributes,
)
from tracefix.runtime.taskspec_attribute_validation import (
    MAX_ATTRIBUTE_CORRECTION_ATTEMPTS,
    extract_with_taskspec_reevaluation,
    validate_attributes_against_taskspec,
)


def _task_spec(**overrides):
    values = {
        "task_id": "task_real_schema",
        "query_id": "tellme_real_schema",
        "user_query": "Two robots coordinate through one shared corridor.",
        "route": "multi_agent",
        "constraints": {"max_agents": 2, "privacy_scope": "cityos_structured_context_only"},
        "candidate_harnesses": ["video_context_harness", "answer_synthesis_harness"],
    }
    values.update(overrides)
    return TraceFixTaskSpec(**values).model_dump(mode="json")


def _attributes(number_of_agents=2):
    return ExtractedCoordinationAttributes.from_payload({
        "coordination_patterns": ["Exclusive Resource Access"],
        "number_of_agents": number_of_agents,
        "agent_roles": ["robot"],
        "communication_flow": [],
        "limitations": ["one_robot_at_a_time"],
        "number_of_resources": 1,
        "number_of_channels": 2,
    })


def test_taskspec_is_primary_and_original_request_is_secondary_without_mutation():
    task_spec = _task_spec()
    before = json.dumps(task_spec, sort_keys=True)
    messages = build_attribute_extraction_prompt(
        task_spec=task_spec,
        original_request="secondary wording",
    )
    rendered = "\n".join(message["content"] for message in messages)
    assert "READ-ONLY_TELLME_TASKSPEC_JSON" in rendered
    assert task_spec["user_query"] in rendered
    assert "SECONDARY_ORIGINAL_REQUEST" in rendered
    assert "secondary wording" in rendered
    assert "TaskSpec is primary" in rendered
    assert json.dumps(task_spec, sort_keys=True) == before


def test_taskspec_file_is_byte_for_byte_unchanged_after_extraction(tmp_path):
    path = tmp_path / "tracefix_task_spec.json"
    path.write_text(json.dumps(_task_spec(), indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    task_spec = json.loads(path.read_text(encoding="utf-8"))
    result = extract_coordination_attributes(
        task_spec=task_spec,
        original_request="secondary",
        client=lambda request: {
            "coordination_patterns": ["Exclusive Resource Access"],
            "number_of_agents": 2,
            "agent_roles": ["robot"],
            "communication_flow": [],
            "limitations": [],
            "number_of_resources": 1,
            "number_of_channels": 2,
        },
    )
    assert set(result.as_dict()) == set(Template.COORDINATION_ATTRIBUTE_FIELDS)
    assert result.number_of_agents == 2
    assert path.read_bytes() == before


def test_structured_maximum_agent_count_is_checked_deterministically():
    valid = validate_attributes_against_taskspec(_task_spec(), _attributes(2))
    contradiction = validate_attributes_against_taskspec(_task_spec(), _attributes(3))
    assert valid.status == "valid"
    assert not valid.contradictions
    assert contradiction.status == "needs_reevaluation"
    assert contradiction.contradictions[0]["field"] == "number_of_agents"


def test_real_taskspec_has_no_structured_roles_resources_or_channels_to_cross_check():
    diagnostic = validate_attributes_against_taskspec(_task_spec(), _attributes())
    fields = {item["field"] for item in diagnostic.not_checkable}
    assert {"agent_roles", "number_of_resources", "number_of_channels"}.issubset(fields)
    assert "candidate_harnesses" not in {item.get("relationship") for item in diagnostic.checked_fields}


def test_omitted_maximum_is_not_a_contradiction():
    diagnostic = validate_attributes_against_taskspec(
        _task_spec(constraints={}), _attributes(9)
    )
    assert diagnostic.status == "valid"
    assert not diagnostic.contradictions
    assert diagnostic.checked_fields[0]["result"] == "not_checkable"
    assert diagnostic.not_checkable[0]["field"] == "number_of_agents"


def test_real_contradiction_triggers_targeted_complete_reevaluation():
    calls = []
    def fake_extractor(**kwargs):
        calls.append(kwargs)
        return _attributes(3 if len(calls) == 1 else 2)
    result = extract_with_taskspec_reevaluation(
        task_spec=_task_spec(), original_request="secondary", extractor=fake_extractor
    )
    assert result.attempts == 2
    assert result.attributes is not None
    assert set(result.attributes.as_dict()) == set(Template.COORDINATION_ATTRIBUTE_FIELDS)
    assert "complete seven-field canonical object" in calls[1]["correction_feedback"]
    assert calls[1]["task_spec"] == calls[0]["task_spec"]


def test_persistent_contradiction_stops_after_fixed_maximum():
    calls = []
    def fake_extractor(**kwargs):
        calls.append(kwargs)
        return _attributes(3)
    result = extract_with_taskspec_reevaluation(
        task_spec=_task_spec(), original_request="secondary", extractor=fake_extractor
    )
    assert result.attributes is None
    assert result.diagnostic.status == "failed"
    assert result.attempts == MAX_ATTRIBUTE_CORRECTION_ATTEMPTS + 1
    assert len(calls) == 3
