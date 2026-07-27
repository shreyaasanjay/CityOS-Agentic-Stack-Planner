import json

from tracefix.protocol_templates import get_template
from tracefix.protocol_templates.template import Template
from tracefix.runtime.deterministic_template_engine import (
    DeterministicTemplateEngine,
    rank_templates_for_attributes,
)
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData


def _sample_extracted(**overrides):
    payload = {
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
        ],
        "number_of_agents": 3,
        "agent_roles": ["requester"],
        "communication_flow": ["request", "grant", "enter", "exit", "release"],
        "limitations": [
            "emergency_agents_have_priority",
            "ordinary_agents_must_not_starve",
        ],
        "number_of_resources": 1,
        "number_of_channels": None,
    }
    payload.update(overrides)
    return ExtractedCoordinationData.from_payload(payload)


def test_single_agent_request_bypasses_coordination_procedure_selection():
    extracted = _sample_extracted(
        coordination_patterns=[],
        number_of_agents=1,
        agent_roles=["occupancy_analyzer"],
        communication_flow=[],
        limitations=[],
        number_of_resources=0,
        number_of_channels=0,
    )
    engine = DeterministicTemplateEngine()

    assert engine.is_single_agent_request(extracted, task_route="single_agent") is True
    decision = engine.select_single_agent_procedure(
        extracted,
        task_route="single_agent",
    )

    assert decision.selected_procedure == "single_agent_generation"
    assert decision.reason_codes == ["single_agent_request"]
    assert decision.selected_template_id is None
    assert "full_generation_fallback" not in decision.reason_codes


def test_single_agent_workflow_pattern_labels_do_not_force_protocol_generation():
    extracted = _sample_extracted(
        coordination_patterns=["Request-Response", "Verification"],
        number_of_agents=1,
        agent_roles=["occupancy_context", "answer_synthesis"],
        communication_flow=[],
        limitations=[],
        number_of_resources=1,
        number_of_channels=0,
    )

    assert DeterministicTemplateEngine().is_single_agent_request(
        extracted,
        task_route="single_agent",
    ) is True


def test_single_agent_route_with_coordination_remains_in_multi_agent_pipeline():
    extracted = _sample_extracted(number_of_agents=1)

    assert DeterministicTemplateEngine().is_single_agent_request(
        extracted,
        task_route="single_agent",
    ) is False


def _priority_template(**overrides):
    data = {
        "template_id": "priority_shared_resource",
        "name_of_template": "Priority Shared Resource",
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
        ],
        "number_of_agents": 3,
        "agent_roles": ["requester", "scheduler", "emergency_agent"],
        "communication_flow": ["request", "grant", "enter", "exit", "release"],
        "limitations": [
            "emergency_agents_have_priority",
            "ordinary_agents_must_not_starve",
        ],
        "number_of_resources": 1,
        "number_of_channels": 3,
    }
    data.update(overrides)
    return Template.from_dict(data)


def _producer_consumer_template():
    return Template(
        template_id="producer_consumer",
        name_of_template="Producer-Consumer",
        coordination_patterns=["Producer-Consumer"],
        number_of_agents=2,
        agent_roles=["producer", "consumer"],
        communication_flow=["produce", "consume"],
        limitations=[],
        number_of_resources=0,
        number_of_channels=1,
    )


def _consensus_template():
    return Template(
        template_id="consensus",
        name_of_template="Consensus",
        coordination_patterns=["Consensus", "Majority Voting"],
        number_of_agents=3,
        agent_roles=["proposer", "voter"],
        communication_flow=["propose", "vote", "decide"],
        limitations=["majority_must_agree"],
        number_of_resources=0,
        number_of_channels=3,
    )


def test_rank_templates_puts_priority_shared_resource_first_and_serializes():
    extracted = _sample_extracted()
    templates = [_producer_consumer_template(), _consensus_template(), _priority_template()]

    rankings = rank_templates_for_attributes(extracted, templates)

    assert [ranking.template_id for ranking in rankings] == [
        "priority_shared_resource",
        "consensus",
        "producer_consumer",
    ]
    assert rankings[0].match_count == 5
    assert rankings[0].mismatch_count == 0
    assert rankings[0].unknown_count == 1
    assert rankings[0].compared_count == 5
    assert rankings[0].score == 1.0
    assert [comparison.attribute_name for comparison in rankings[0].comparisons] == [
        "coordination_patterns",
        "number_of_agents",
        "agent_roles",
        "communication_flow",
        "number_of_resources",
        "number_of_channels",
    ]
    assert "limitations" not in {comparison.attribute_name for comparison in rankings[0].comparisons}
    json.dumps([ranking.to_dict() for ranking in rankings])


def test_shared_comparisons_return_true_false_and_none():
    engine = DeterministicTemplateEngine()
    comparisons = {
        comparison.attribute_name: comparison
        for comparison in engine.compare(_sample_extracted(), _producer_consumer_template())
    }

    assert comparisons["number_of_agents"].result is False
    assert comparisons["coordination_patterns"].result is False
    assert comparisons["number_of_channels"].result is None
    assert "limitations" not in comparisons


def test_unknown_values_do_not_lower_score():
    extracted = _sample_extracted(
        number_of_channels=None,
        coordination_patterns=[],
        agent_roles=[],
        communication_flow=[],
    )

    ranking = DeterministicTemplateEngine().rank(extracted, [_priority_template()])[0]

    assert ranking.match_count == 2
    assert ranking.mismatch_count == 0
    assert ranking.unknown_count == 4
    assert ranking.score == 1.0


def test_limitations_do_not_change_ranking_score():
    engine = DeterministicTemplateEngine()
    matching_limitations = _sample_extracted()
    mismatching_limitations = _sample_extracted(limitations=["something_else"])

    first = engine.rank(matching_limitations, [_priority_template()])[0]
    second = engine.rank(mismatching_limitations, [_priority_template()])[0]

    assert first.score == second.score
    assert first.match_count == second.match_count
    assert first.mismatch_count == second.mismatch_count


def test_validation_inspects_limitations_and_rejects_mismatch():
    engine = DeterministicTemplateEngine()

    valid_result = engine.validate(_sample_extracted(), _priority_template())
    invalid_result = engine.validate(
        _sample_extracted(limitations=["unsupported_limit"]),
        _priority_template(),
    )

    assert valid_result.valid is True
    assert "limitations" in valid_result.matched_fields
    assert invalid_result.valid is False
    assert "limitations" in invalid_result.mismatched_fields


def test_validation_rejects_zero_comparable_structural_fields():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(
        coordination_patterns=[],
        number_of_agents=None,
        agent_roles=[],
        communication_flow=[],
        limitations=[],
        number_of_resources=None,
        number_of_channels=None,
    )

    result = engine.validate(extracted, _priority_template())

    assert result.valid is False
    assert "zero comparable structural attributes" in " ".join(result.reasons)


def test_inputs_are_not_mutated():
    extracted = _sample_extracted()
    template = _priority_template()
    before_extracted = extracted.as_dict()
    before_template = template.to_dict()

    DeterministicTemplateEngine().rank(extracted, [template])
    DeterministicTemplateEngine().validate(extracted, template)

    assert extracted.as_dict() == before_extracted
    assert template.to_dict() == before_template


def test_procedure_options_perfect_match_enables_exact_reuse():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _priority_template()
    rankings = engine.rank(extracted, [template])
    validation = engine.validate(extracted, template)

    options = engine.procedure_options(rankings, validation, [template])
    by_name = {option.procedure: option for option in options.options}

    assert tuple(by_name) == (
        "exact_reuse",
        "parameterized_reuse",
        "partial_recomposition",
        "full_generation",
    )
    assert by_name["exact_reuse"].deterministically_available is True
    assert by_name["parameterized_reuse"].deterministically_available is False


def test_parameter_only_mismatch_requires_declared_parameter_field():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_agents=4, number_of_channels=3)
    allowed = _priority_template(parameterizable_fields=["number_of_agents"])
    blocked = _priority_template(template_id="blocked", parameterizable_fields=[])

    allowed_options = engine.procedure_options(
        engine.rank(extracted, [allowed]),
        engine.validate(extracted, allowed),
        [allowed],
    )
    blocked_options = engine.procedure_options(
        engine.rank(extracted, [blocked]),
        engine.validate(extracted, blocked),
        [blocked],
    )

    assert allowed_options.option_for("parameterized_reuse").deterministically_available is True
    assert blocked_options.option_for("parameterized_reuse").deterministically_available is False


def test_nonparameterizable_mismatch_blocks_parameterized_reuse():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_agents=4, number_of_channels=3)
    template = _priority_template(parameterizable_fields=["number_of_channels"])

    options = engine.procedure_options(
        engine.rank(extracted, [template]),
        engine.validate(extracted, template),
        [template],
    )

    assert options.option_for("parameterized_reuse").deterministically_available is False


def test_adaptable_nonfatal_mismatch_may_enable_partial_recomposition():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _priority_template(
        communication_flow=["request", "grant", "release"],
        adaptable_fields=["communication_flow"],
    )

    options = engine.procedure_options(
        engine.rank(extracted, [template]),
        engine.validate(extracted, template),
        [template],
    )

    assert options.option_for("partial_recomposition").deterministically_available is True


def test_fatal_pattern_mismatch_blocks_reuse_and_full_generation_available():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _producer_consumer_template()
    rankings = engine.rank(extracted, [template])
    validation = engine.validate(extracted, template)

    options = engine.procedure_options(rankings, validation, [template])

    assert options.option_for("exact_reuse").deterministically_available is False
    assert options.option_for("parameterized_reuse").deterministically_available is False
    assert options.option_for("partial_recomposition").deterministically_available is False
    assert options.option_for("full_generation").deterministically_available is True


def test_no_templates_emits_all_options_with_full_generation_available():
    options = DeterministicTemplateEngine().procedure_options([], None, [])

    assert [option.procedure for option in options.options] == [
        "exact_reuse",
        "parameterized_reuse",
        "partial_recomposition",
        "full_generation",
    ]
    assert options.option_for("full_generation").deterministically_available is True


def _select(engine, extracted, templates):
    rankings = engine.rank(extracted, templates)
    top = next(
        (template for template in templates if rankings and template.get_template_id() == rankings[0].template_id),
        None,
    )
    validation = engine.validate(extracted, top) if top else None
    options = engine.procedure_options(rankings, validation, templates)
    return engine.select_procedure(extracted, rankings, validation, options, templates)


def test_selector_chooses_exact_reuse_with_sufficient_structural_evidence():
    decision = _select(
        DeterministicTemplateEngine(),
        _sample_extracted(number_of_channels=3),
        [_priority_template()],
    )

    assert decision.selected_procedure == "exact_reuse"
    assert decision.selected_template_id == "priority_shared_resource"
    assert decision.evidence_sufficient is True


def test_exact_reuse_is_blocked_when_only_pattern_evidence_is_known():
    # Construct canonical validator input directly so this remains a test of
    # pattern-only evidence, independent of the LLM boundary's deterministic
    # communication-flow completion.
    extracted = ExtractedCoordinationData.model_validate(
        {
            "coordination_patterns": [
                "Request-Grant",
                "Exclusive Resource Access",
                "Task Prioritization",
            ],
            "number_of_agents": None,
            "agent_roles": [],
            "communication_flow": [],
            "limitations": [],
            "number_of_resources": None,
            "number_of_channels": None,
        }
    )
    decision = _select(DeterministicTemplateEngine(), extracted, [_priority_template()])

    assert decision.selected_procedure == "full_generation"
    assert decision.evidence_sufficient is False
    assert "insufficient_reuse_evidence" in decision.reason_codes


def test_selector_chooses_parameterized_reuse_before_partial_or_full():
    template = _priority_template(parameterizable_fields=["number_of_agents"])
    decision = _select(
        DeterministicTemplateEngine(),
        _sample_extracted(number_of_agents=4, number_of_channels=3),
        [template],
    )

    assert decision.selected_procedure == "parameterized_reuse"
    assert decision.mismatched_fields == ["number_of_agents"]


def test_selector_chooses_partial_recomposition_for_bounded_adaptation():
    template = _priority_template(
        communication_flow=["request", "grant", "release"],
        adaptable_fields=["communication_flow"],
    )
    decision = _select(
        DeterministicTemplateEngine(),
        _sample_extracted(number_of_channels=3),
        [template],
    )

    assert decision.selected_procedure == "partial_recomposition"
    assert "communication_flow" in decision.adaptable_fields


def test_selector_chooses_full_generation_for_fatal_pattern_mismatch():
    decision = _select(
        DeterministicTemplateEngine(),
        _sample_extracted(number_of_channels=3),
        [_producer_consumer_template()],
    )

    assert decision.selected_procedure == "full_generation"
    assert decision.selected_template_id is None
    assert "coordination_patterns" in decision.fatal_mismatch_fields


def test_selector_chooses_full_generation_when_no_template_exists():
    decision = _select(DeterministicTemplateEngine(), _sample_extracted(), [])

    assert decision.selected_procedure == "full_generation"
    assert decision.selected_template_id is None
    assert decision.reason_codes == ["no_ranked_template", "full_generation_fallback"]


def test_selector_is_repeatable_and_auditable():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    template = _priority_template()

    first = _select(engine, extracted, [template]).to_dict()
    second = _select(engine, extracted, [template]).to_dict()

    assert first == second
    assert first["reason_codes"]
    assert first["matched_fields"]
    assert "available_procedures" in first


def test_validation_audit_preserves_ranking_order_and_field_results():
    engine = DeterministicTemplateEngine()
    extracted = _sample_extracted(number_of_channels=3)
    templates = [_producer_consumer_template(), _priority_template(), _consensus_template()]
    rankings = engine.rank(extracted, templates)

    payload, validations = engine.validation_audit(extracted, rankings, templates)

    candidates = payload["ranked_candidates"]
    assert [candidate["template_id"] for candidate in candidates] == [
        ranking.template_id for ranking in rankings
    ]
    assert [candidate["rank"] for candidate in candidates] == [1, 2, 3]
    assert candidates[0]["template_id"] == validations[0].template_id
    assert candidates[0]["field_results"]["coordination_patterns"]["result"] is True
    assert "request_value" in candidates[0]["field_results"]["number_of_agents"]
    json.dumps(payload)


def test_medication_robot_overlap_counts_route_to_partial_recomposition():
    extracted = ExtractedCoordinationData.from_payload({
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Priority Escort",
            "Queue-Based Scheduling",
        ],
        "number_of_agents": 3,
        "agent_roles": ["medication-delivery robot"],
        "communication_flow": [],
        "limitations": [
            "Only one robot may occupy the corridor at a time",
            "Emergency deliveries have priority",
            "Ordinary deliveries must not starve",
            "Do not execute production agents or bypass PlusCal/TLC verification",
        ],
        "number_of_resources": 1,
        "number_of_channels": 3,
    })
    template = get_template("traffic_signal_coordination")
    engine = DeterministicTemplateEngine()
    rankings = engine.rank(extracted, [template])
    validation_audit, _ = engine.validation_audit(extracted, rankings, [template])

    decision = _select(engine, extracted, [template])
    candidate = validation_audit["ranked_candidates"][0]

    assert decision.selected_procedure == "partial_recomposition"
    assert decision.selected_template_id == "traffic_signal_coordination"
    assert decision.fatal_mismatch_fields == []
    assert decision.mismatched_fields == ["coordination_patterns", "limitations"]
    assert decision.unknown_fields == [
        "number_of_agents",
        "agent_roles",
        "number_of_channels",
    ]
    assert decision.recomposable_fields == ["coordination_patterns"]
    assert "coordination_patterns" not in decision.protected_fields
    assert decision.evidence["coordination_pattern_match"] is False
    assert decision.evidence["coordination_pattern_overlap_count"] == 3
    assert decision.evidence["meaningful_structural_match_count"] == 3
    assert decision.evidence["unknown_count"] == 3
    assert "coordination_pattern_overlap_sufficient" in decision.reason_codes
    assert candidate["match_count"] == 2
    assert candidate["mismatch_count"] == 1
    assert candidate["unknown_count"] == 3
    assert candidate["validation_match_count"] == 2
    assert candidate["validation_mismatch_count"] == 2
    assert candidate["validation_unknown_count"] == 3
    assert candidate["coordination_pattern_overlap_count"] == 3
    assert candidate["count_qualified_recomposition_fields"] == ["coordination_patterns"]
    assert candidate["eligible_for_partial_recomposition"] is True
