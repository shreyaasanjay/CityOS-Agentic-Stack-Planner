import json
import pytest

from tellme_harness.schemas import TraceFixTaskSpec
from tracefix.pipeline.pipeline.pluscal_generator import generate_tlc_config
from tracefix.protocol_templates import (
    build_template,
    clear_generated_templates_for_tests,
    get_template,
    list_pattern_ids,
)
from tracefix.protocol_templates.template import Template
from tracefix.runtime.deterministic_template_engine import DeterministicTemplateEngine
from tracefix.runtime.llm_attribute_extractor import (
    ExtractedCoordinationAttributes,
    extract_coordination_attributes,
)
from tracefix.runtime.procedure_execution import instantiate_exact_reuse
from tracefix.runtime.procedure_prompt import build_procedure_execution_context
from tracefix.runtime.template_promotion import promote_verified_workspace_template
from tracefix.runtime.taskspec_attribute_validation import extract_with_taskspec_reevaluation


def _promotion_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    spec = workspace / "spec"
    spec.mkdir(parents=True)
    ir, protocol = build_template("sequential_handoff", {"agent_a_id": "upstream_agent", "agent_b_id": "downstream_agent"})
    extracted = ExtractedCoordinationAttributes.from_payload({
        "coordination_patterns": ["Sequential Handoff"], "number_of_agents": 2,
        "agent_roles": ["upstream_agent", "downstream_agent"], "communication_flow": [],
        "limitations": ["no_acknowledgement_required"], "number_of_resources": 2, "number_of_channels": 1,
    })
    for name, content in {
        "ir.json": json.dumps(ir), "Protocol.tla": protocol,
        "Protocol.cfg": generate_tlc_config(ir), "states.json": "{}\n",
    }.items():
        (spec / name).write_text(content, encoding="utf-8")
    metadata = {
        "template_id": "generated_promotion_contract", "name_of_template": "Promotion Contract",
        **extracted.as_dict(), "parameterizable_fields": [], "adaptable_fields": [],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }
    return workspace, spec, extracted, metadata


@pytest.mark.parametrize("verdict", [False, None])
def test_tlc_failure_or_missing_verdict_blocks_promotion(tmp_path, verdict):
    workspace, spec, extracted, metadata = _promotion_workspace(tmp_path)
    metadata["tlc_passed"] = True
    (spec / "generated_template.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="successful TLC verdict"):
        promote_verified_workspace_template(workspace, extracted=extracted, tlc_passed=verdict)


def test_missing_generated_template_is_explicit_contract_failure(tmp_path):
    workspace, _spec, extracted, _metadata = _promotion_workspace(tmp_path)
    with pytest.raises(ValueError, match="contract violation"):
        promote_verified_workspace_template(workspace, extracted=extracted, tlc_passed=True)


def test_generated_metadata_cannot_contain_or_control_tlc_verdict(tmp_path):
    workspace, spec, extracted, metadata = _promotion_workspace(tmp_path)
    metadata["tlc_passed"] = True
    (spec / "generated_template.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported alternate fields"):
        promote_verified_workspace_template(workspace, extracted=extracted, tlc_passed=True)


def test_metadata_ir_count_conflict_writes_report_and_blocks(tmp_path):
    workspace, spec, extracted, metadata = _promotion_workspace(tmp_path)
    extracted = ExtractedCoordinationAttributes.from_payload({**extracted.as_dict(), "number_of_agents": None, "communication_flow": []})
    metadata["number_of_agents"] = 3
    (spec / "generated_template.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts with generated IR"):
        promote_verified_workspace_template(workspace, extracted=extracted, tlc_passed=True)
    report = json.loads((spec / "generated_template_consistency.json").read_text(encoding="utf-8"))
    assert report["conflicts"][0]["field"] == "number_of_agents"


def test_injected_taskspec_to_exact_reuse_and_promotion_lifecycle(tmp_path, monkeypatch):
    registry = tmp_path / "lifecycle_registry"
    monkeypatch.setenv("TRACEFIX_GENERATED_TEMPLATE_DIR", str(registry))
    clear_generated_templates_for_tests()
    task_spec = TraceFixTaskSpec(
        task_id="task_lifecycle",
        query_id="query_lifecycle",
        user_query="One upstream agent hands work to one downstream agent.",
        route="multi_agent",
        constraints={"max_agents": 2},
    ).model_dump(mode="json")
    task_spec_before = json.dumps(task_spec, sort_keys=True)

    def injected_extractor(**kwargs):
        return extract_coordination_attributes(
            task_spec=kwargs["task_spec"],
            original_request=kwargs.get("original_request"),
            correction_feedback=kwargs.get("correction_feedback"),
            client=lambda _request: {
                "coordination_patterns": ["Sequential Handoff"],
                "number_of_agents": 2,
                "agent_roles": ["upstream_agent", "downstream_agent"],
                "communication_flow": [],
                "limitations": ["no_acknowledgement_required"],
                "number_of_resources": 2,
                "number_of_channels": 1,
            },
        )

    extraction = extract_with_taskspec_reevaluation(
        task_spec=task_spec,
        original_request="secondary request text",
        extractor=injected_extractor,
    )
    assert extraction.diagnostic.status == "valid"
    assert extraction.attributes is not None
    extracted = extraction.attributes

    templates = [get_template(template_id) for template_id in list_pattern_ids()]
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, templates)
    selected = get_template(rankings[0].template_id)
    validation = engine.validate(extracted, selected)
    options = engine.procedure_options(rankings, validation, templates)
    decision = engine.select_procedure(extracted, rankings, validation, options, templates)
    assert decision.selected_procedure == "exact_reuse"

    context = build_procedure_execution_context(
        query="secondary request text",
        task_spec=task_spec,
        extracted_data=extracted,
        decision=decision,
        template_metadata=selected.to_dict(),
    )
    workspace = tmp_path / "lifecycle_workspace"
    instantiate_exact_reuse(workspace, context)
    spec = workspace / "spec"
    (spec / "states.json").write_text("{}\n", encoding="utf-8")
    (spec / "summary.json").write_text(
        json.dumps({"tlc_passed": True}), encoding="utf-8"
    )
    generated = {
        **selected.to_dict(),
        "template_id": "generated_taskspec_lifecycle",
        "name_of_template": "TaskSpec Lifecycle",
    }
    (spec / "generated_template.json").write_text(
        json.dumps(generated), encoding="utf-8"
    )

    promoted, destination = promote_verified_workspace_template(
        workspace,
        extracted=extracted,
        tlc_passed=True,
    )
    assert promoted.get_template_id() == "generated_taskspec_lifecycle"
    assert destination.is_dir()
    assert json.dumps(task_spec, sort_keys=True) == task_spec_before
    clear_generated_templates_for_tests()


def test_verified_template_persists_reloads_ranks_and_executes_exactly(tmp_path, monkeypatch):
    registry = tmp_path / "registry"
    monkeypatch.setenv("TRACEFIX_GENERATED_TEMPLATE_DIR", str(registry))
    clear_generated_templates_for_tests()
    workspace = tmp_path / "workspace"
    spec = workspace / "spec"
    spec.mkdir(parents=True)
    ir, protocol = build_template("sequential_handoff", {
        "agent_a_id": "upstream_agent",
        "agent_b_id": "downstream_agent",
    })
    (spec / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
    (spec / "Protocol.tla").write_text(protocol, encoding="utf-8")
    (spec / "Protocol.cfg").write_text(generate_tlc_config(ir), encoding="utf-8")
    (spec / "states.json").write_text("{}\n", encoding="utf-8")
    (spec / "summary.json").write_text(
        json.dumps({"tlc_passed": True}),
        encoding="utf-8",
    )
    extracted = ExtractedCoordinationAttributes.from_payload({
        "coordination_patterns": ["Sequential Handoff"],
        "number_of_agents": 2,
        "agent_roles": ["upstream_agent", "downstream_agent"],
        "communication_flow": ["work", "handoff", "receive", "continue"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 2,
        "number_of_channels": 1,
    })
    generated_metadata = {
        "template_id": "generated_sequential_handoff_verified",
        "name_of_template": "Generated Sequential Handoff Verified",
        **extracted.as_dict(),
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
        "fatal_mismatch_fields": ["coordination_patterns"],
    }
    (spec / "generated_template.json").write_text(json.dumps(generated_metadata), encoding="utf-8")

    promoted, destination = promote_verified_workspace_template(
        workspace,
        extracted=extracted,
        tlc_passed=True,
    )
    persisted = json.loads((destination / "template.json").read_text(encoding="utf-8"))
    assert set(persisted) == set(Template.CANONICAL_FIELDS)
    assert Template.from_dict(persisted).to_dict() == promoted.to_dict()

    clear_generated_templates_for_tests()
    ids = list_pattern_ids()
    assert promoted.get_template_id() in ids
    reloaded = get_template(promoted.get_template_id())
    assert reloaded.to_dict() == promoted.to_dict()

    templates = [get_template(template_id) for template_id in ids]
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, templates)
    assert rankings[0].template_id == promoted.get_template_id()
    validation = engine.validate(extracted, reloaded)
    options = engine.procedure_options(rankings, validation, templates)
    decision = engine.select_procedure(extracted, rankings, validation, options, templates)
    assert decision.selected_procedure == "exact_reuse"
    context = build_procedure_execution_context(
        query="repeat the verified sequential handoff",
        extracted_data=extracted,
        decision=decision,
        template_metadata=reloaded.to_dict(),
    )
    output_workspace = tmp_path / "exact_output"
    result = instantiate_exact_reuse(output_workspace, context)
    assert result.template_id == promoted.get_template_id()
    assert (output_workspace / "spec" / "ir.json").is_file()
    assert (output_workspace / "spec" / "Protocol.tla").is_file()

    clear_generated_templates_for_tests()
