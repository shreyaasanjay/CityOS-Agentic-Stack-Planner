import pytest
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tracefix.protocol_templates import (
    Template,
    build_template,
    build_template_from_metadata,
    clear_generated_templates_for_tests,
    get_template,
    get_template_metadata,
    load_persisted_templates,
    list_pattern_ids,
    persist_template,
    register_template,
)


def test_registry_metadata_is_exactly_the_canonical_template_schema():
    assert tuple(get_template_metadata("sequential_handoff")) == Template.CANONICAL_FIELDS


def test_template_is_data_only_and_normalizes_patterns():
    template = Template(
        template_id="corridor_access",
        name_of_template="Corridor Access",
        coordination_patterns=["Request-Grant", "Exclusive Resource Access"],
        number_of_agents=3,
        agent_roles=["robot", "scheduler"],
        communication_flow=["request", "grant", "release"],
        limitations=["does not perform matching"],
        number_of_resources=1,
        number_of_channels=3,
    )

    assert template.get_template_id() == "corridor_access"
    assert template.get_name_of_template() == "Corridor Access"
    assert template.get_coordination_patterns() == [
        "Request-Grant",
        "Exclusive Resource Access",
    ]
    assert template.get_number_of_agents() == 3
    assert template.get_agent_roles() == ["robot", "scheduler"]
    assert template.get_communication_flow() == ["request", "grant", "release"]
    assert template.get_limitations() == ["does not perform matching"]
    assert template.get_number_of_resources() == 1
    assert template.get_number_of_channels() == 3
    assert not hasattr(template, "get_coordination_keywords")
    assert template.to_dict() == {
        "template_id": "corridor_access",
        "name_of_template": "Corridor Access",
        "coordination_patterns": ["Request-Grant", "Exclusive Resource Access"],
        "number_of_agents": 3,
        "agent_roles": ["robot", "scheduler"],
        "communication_flow": ["request", "grant", "release"],
        "limitations": ["does not perform matching"],
        "number_of_resources": 1,
        "number_of_channels": 3,
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }


def test_template_round_trips_from_dict_and_defensive_copies():
    template = Template.from_dict({
        "template_id": "corridor_access",
        "name_of_template": "Corridor Access",
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 3,
        "agent_roles": ["robot"],
        "communication_flow": ["request"],
        "limitations": [],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    })

    roles = template.get_agent_roles()
    roles.append("mutated")

    assert template.get_agent_roles() == ["robot"]
    assert Template.from_dict(template.to_dict()).to_dict() == template.to_dict()


def test_template_rejects_bad_numeric_values_and_unknown_patterns():
    with pytest.raises(ValueError, match="number_of_agents must not be negative"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=-1,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
        )


def test_template_validates_reuse_metadata_fields():
    template = Template(
        template_id="corridor_access",
        name_of_template="Corridor Access",
        coordination_patterns=["Request-Grant"],
        number_of_agents=3,
        agent_roles=["robot", "scheduler"],
        communication_flow=["request", "grant", "release"],
        limitations=["one robot at a time"],
        number_of_resources=1,
        number_of_channels=3,
        parameterizable_fields=["number_of_agents"],
        adaptable_fields=["communication_flow"],
        fatal_mismatch_fields=["coordination_patterns"],
    )

    assert template.get_parameterizable_fields() == ["number_of_agents"]
    assert template.get_adaptable_fields() == ["communication_flow"]
    assert template.get_fatal_mismatch_fields() == ["coordination_patterns"]

    with pytest.raises(ValueError, match="identity field"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
            parameterizable_fields=["template_id"],
        )

    with pytest.raises(ValueError, match="unsupported template attribute"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=[],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
            adaptable_fields=["not_a_field"],
        )

    with pytest.raises(ValueError, match="unknown coordination pattern"):
        Template(
            template_id="bad",
            name_of_template="Bad",
            coordination_patterns=["not real"],
            number_of_agents=None,
            agent_roles=[],
            communication_flow=[],
            limitations=[],
            number_of_resources=None,
            number_of_channels=None,
        )


def test_builtin_template_registry_returns_data_objects_without_matching():
    ids = list_pattern_ids()

    assert "traffic_signal_coordination" in ids
    template = get_template("traffic_signal_coordination")
    assert isinstance(template, Template)
    assert template.get_template_id() == "traffic_signal_coordination"


def test_builtin_template_metadata_golden_contract():
    expected = {
        "attendance_verification": (2, 2, 2),
        "fan_in_decision": (None, 0, None),
        "producer_consumer": (2, 1, 1),
        "sequential_handoff": (2, 2, 1),
        "traffic_signal_coordination": (None, 1, None),
        "verifier_approver": (2, 2, 2),
    }
    for template_id, counts in expected.items():
        template = get_template(template_id)
        assert (
            template.get_number_of_agents(), template.get_number_of_resources(),
            template.get_number_of_channels(),
        ) == counts
        assert template.get_coordination_patterns()
        assert template.get_limitations()
        assert template.get_fatal_mismatch_fields() == ["coordination_patterns"]
    assert get_template("traffic_signal_coordination").get_communication_flow() == [
        "request", "grant", "enter", "exit", "release", "enqueue", "dequeue", "complete",
    ]
    assert not hasattr(template, "score")
    assert not hasattr(template, "match")


def test_generated_template_registration_is_idempotent_for_identical_content():
    clear_generated_templates_for_tests()
    template = Template(
        template_id="generated_corridor_access",
        name_of_template="Generated Corridor Access",
        coordination_patterns=["Request-Grant"],
        number_of_agents=2,
        agent_roles=["requester", "scheduler"],
        communication_flow=["request", "grant"],
        limitations=[],
        number_of_resources=1,
        number_of_channels=1,
    )

    register_template(template)
    assert "generated_corridor_access" in list_pattern_ids()
    assert get_template("generated_corridor_access").to_dict() == template.to_dict()
    register_template(template)


def test_opencode_metadata_constructs_valid_template_before_registration():
    clear_generated_templates_for_tests()
    metadata = {
        "name_of_template": "OpenCode Corridor Template",
        "coordination_patterns": ["Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["requester", "scheduler"],
        "communication_flow": ["request", "grant"],
        "limitations": ["exclusive_access"],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents"],
        "adaptable_fields": ["communication_flow"],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }

    template = build_template_from_metadata(metadata)
    register_template(template)

    assert template.get_template_id().startswith("generated_")
    assert get_template(template.get_template_id()).get_name_of_template() == "OpenCode Corridor Template"


def test_template_owns_canonical_coordination_schema():
    assert Template.COORDINATION_ATTRIBUTE_FIELDS == tuple(
        Template.empty_coordination_attributes()
    )
    assert Template.CANONICAL_FIELDS == tuple(Template(
        template_id="canonical_test",
        name_of_template="Canonical Test",
        coordination_patterns=[],
        number_of_agents=None,
        agent_roles=[],
        communication_flow=[],
        limitations=[],
        number_of_resources=None,
        number_of_channels=None,
    ).to_dict())


@pytest.mark.parametrize("alias", [
    "agent_count",
    "participants",
    "roles",
    "channels",
    "resources_used",
    "channel_count",
    "flow",
    "communication",
    "safety_features",
    "safety_properties",
])
def test_canonical_template_metadata_rejects_alternate_field_names(alias):
    payload = {
        "template_id": "generated_alias_test",
        "name_of_template": "Alias Test",
        **Template.empty_coordination_attributes(),
        "parameterizable_fields": [],
        "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
        alias: [],
    }

    with pytest.raises(ValueError, match="unsupported alternate fields"):
        build_template_from_metadata(payload)


def test_persisted_metadata_reconstructs_identical_template_with_artifacts(tmp_path):
    clear_generated_templates_for_tests()
    ir, protocol = build_template("sequential_handoff", {
        "agent_a_id": "robot_a",
        "agent_b_id": "robot_b",
    })
    source = tmp_path / "source"
    source.mkdir()
    (source / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
    (source / "Protocol.tla").write_text(protocol, encoding="utf-8")
    template = build_template_from_metadata({
        "template_id": "generated_corridor_verified",
        "name_of_template": "Generated Corridor Verified",
        "coordination_patterns": ["Sequential Handoff"],
        "number_of_agents": 2,
        "agent_roles": ["upstream_agent", "downstream_agent"],
        "communication_flow": ["work", "handoff", "receive", "continue"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 2,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
        "fatal_mismatch_fields": ["coordination_patterns"],
    })
    registry = tmp_path / "registry"

    destination = persist_template(
        template,
        artifact_paths={
            "ir.json": source / "ir.json",
            "Protocol.tla": source / "Protocol.tla",
        },
        registry_root=registry,
    )
    clear_generated_templates_for_tests()
    loaded = load_persisted_templates(registry)

    assert loaded[0].to_dict() == template.to_dict()
    assert json.loads((destination / "template.json").read_text(encoding="utf-8")) == template.to_dict()
    loaded_ir, loaded_protocol = build_template(template.get_template_id(), {})
    assert loaded_ir == ir
    assert loaded_protocol == protocol


@pytest.mark.parametrize("template_id", ["../escape", "..\\escape", "C-drive", "has/slash", "has space", "CON", "con", ""])
def test_template_ids_reject_unsafe_filesystem_values(template_id):
    with pytest.raises(ValueError):
        Template(
            template_id=template_id, name_of_template="Unsafe", coordination_patterns=[],
            number_of_agents=None, agent_roles=[], communication_flow=[], limitations=[],
            number_of_resources=None, number_of_channels=None,
        )


def test_persistence_rejects_builtin_id_collision(tmp_path):
    template = Template(
        template_id="sequential_handoff", name_of_template="Shadow", coordination_patterns=[],
        number_of_agents=None, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=None, number_of_channels=None,
    )
    with pytest.raises(ValueError, match="built-in"):
        persist_template(template, artifact_paths={}, registry_root=tmp_path)


def test_corrupt_registry_entry_is_isolated(tmp_path):
    clear_generated_templates_for_tests()
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "template.json").write_text("{bad", encoding="utf-8")
    good = Template(
        template_id="generated_good", name_of_template="Good", coordination_patterns=[],
        number_of_agents=0, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=0, number_of_channels=0,
    )
    persist_template(good, artifact_paths={}, registry_root=tmp_path)
    clear_generated_templates_for_tests()
    diagnostic = load_persisted_templates(tmp_path)
    assert [item.get_template_id() for item in diagnostic] == ["generated_good"]
    assert diagnostic.skipped_entries[0]["entry"].endswith("bad")


def test_atomic_persistence_cleans_temporary_directory_on_copy_failure(tmp_path):
    clear_generated_templates_for_tests()
    template = Template(
        template_id="generated_atomic", name_of_template="Atomic", coordination_patterns=[],
        number_of_agents=0, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=0, number_of_channels=0,
    )
    with pytest.raises(ValueError, match="artifact is missing"):
        persist_template(template, artifact_paths={"ir.json": tmp_path / "missing"}, registry_root=tmp_path)
    assert not (tmp_path / "generated_atomic").exists()
    assert not list(tmp_path.glob(".generated_atomic.*"))


def test_atomic_persistence_cleans_temporary_directory_on_metadata_failure(tmp_path, monkeypatch):
    clear_generated_templates_for_tests()
    template = Template(
        template_id="generated_metadata_failure", name_of_template="Metadata Failure", coordination_patterns=[],
        number_of_agents=0, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=0, number_of_channels=0,
    )
    original = Path.write_text
    def fail_template_write(path, *args, **kwargs):
        if path.name == "template.json":
            raise OSError("injected metadata failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_template_write)
    with pytest.raises(OSError, match="injected metadata failure"):
        persist_template(template, artifact_paths={}, registry_root=tmp_path)
    assert not (tmp_path / "generated_metadata_failure").exists()
    assert not list(tmp_path.glob(".generated_metadata_failure.*"))


def test_generated_id_collision_rejects_conflicting_content(tmp_path):
    clear_generated_templates_for_tests()
    first = Template(
        template_id="generated_collision", name_of_template="First", coordination_patterns=[],
        number_of_agents=0, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=0, number_of_channels=0,
    )
    second = Template.from_dict({**first.to_dict(), "name_of_template": "Second"})
    persist_template(first, artifact_paths={}, registry_root=tmp_path)
    with pytest.raises(ValueError, match="conflicting metadata"):
        persist_template(second, artifact_paths={}, registry_root=tmp_path)


def test_concurrent_identical_persistence_is_idempotent(tmp_path):
    clear_generated_templates_for_tests()
    template = Template(
        template_id="generated_concurrent", name_of_template="Concurrent", coordination_patterns=[],
        number_of_agents=0, agent_roles=[], communication_flow=[], limitations=[],
        number_of_resources=0, number_of_channels=0,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: persist_template(template, artifact_paths={}, registry_root=tmp_path), range(2)))
    assert results[0] == results[1] == tmp_path / "generated_concurrent"
    assert (results[0] / "template.json").is_file()
