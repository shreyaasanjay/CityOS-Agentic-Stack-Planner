"""Deterministic template ranking and validation for coordination attributes.

This module does not call an LLM, generate IR, run OpenCode, synthesize CityOS
apps, or authorize protocol execution.  It compares a validated extractor
artifact against data-only Template objects.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData
from tracefix.runtime.procedure_decision import (
    DeterministicProcedureDecision,
    ProcedureName,
)

PRIMARY_ATTRIBUTES: tuple[str, ...] = (
    "coordination_patterns",
    "number_of_agents",
    "agent_roles",
    "communication_flow",
    "number_of_resources",
    "number_of_channels",
)

# Exact reuse requires positive pattern evidence and at least one additional
# structural match. This prevents one match plus many unknowns from authorizing
# an unchanged template.
MIN_REUSE_MATCHED_STRUCTURAL_FIELDS = 2
MIN_PARTIAL_PATTERN_OVERLAP = 2
MEANINGFUL_REUSE_FIELDS = frozenset({
    "coordination_patterns",
    "agent_roles",
    "communication_flow",
    "number_of_resources",
})

@dataclass(frozen=True)
class AttributeComparison:
    attribute_name: str
    extracted_value: object
    template_value: object
    result: bool | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class TemplateRanking:
    template_id: str
    name_of_template: str
    match_count: int
    mismatch_count: int
    unknown_count: int
    compared_count: int
    score: float
    comparisons: tuple[AttributeComparison, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "name_of_template": self.name_of_template,
            "match_count": self.match_count,
            "mismatch_count": self.mismatch_count,
            "unknown_count": self.unknown_count,
            "compared_count": self.compared_count,
            "score": self.score,
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
        }


@dataclass(frozen=True)
class TemplateValidationResult:
    template_id: str
    valid: bool
    matched_fields: tuple[str, ...]
    mismatched_fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]
    comparisons: tuple[AttributeComparison, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "valid": self.valid,
            "matched_fields": list(self.matched_fields),
            "mismatched_fields": list(self.mismatched_fields),
            "unknown_fields": list(self.unknown_fields),
            "comparisons": [comparison.to_dict() for comparison in self.comparisons],
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ProcedureOption:
    procedure: ProcedureName
    deterministically_available: bool
    supporting_reasons: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    candidate_template_id: str | None
    candidate_template_name: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "procedure": self.procedure,
            "deterministically_available": self.deterministically_available,
            "supporting_reasons": list(self.supporting_reasons),
            "blocking_reasons": list(self.blocking_reasons),
            "candidate_template_id": self.candidate_template_id,
            "candidate_template_name": self.candidate_template_name,
        }


@dataclass(frozen=True)
class ProcedureOptions:
    top_candidate_template_id: str | None
    top_candidate_template_name: str | None
    options: tuple[ProcedureOption, ...]
    ranking_summary: dict[str, object]
    validation_summary: dict[str, object]

    def option_for(self, procedure: ProcedureName) -> ProcedureOption | None:
        for option in self.options:
            if option.procedure == procedure:
                return option
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "top_candidate_template_id": self.top_candidate_template_id,
            "top_candidate_template_name": self.top_candidate_template_name,
            "options": [option.to_dict() for option in self.options],
            "ranking_summary": dict(self.ranking_summary),
            "validation_summary": dict(self.validation_summary),
        }


class DeterministicTemplateEngine:
    """Rank and validate templates using shared deterministic comparisons."""

    def is_single_agent_request(
        self,
        extracted: ExtractedCoordinationData,
        *,
        task_route: str | None = None,
    ) -> bool:
        """Return true only when no inter-agent coordination must be synthesized."""

        route = str(task_route or "").strip().lower()
        one_logical_agent = extracted.number_of_agents == 1 or (
            route == "single_agent" and extracted.number_of_agents in (None, 1)
        )
        no_inter_agent_communication = (
            not extracted.communication_flow
            and extracted.number_of_channels in (None, 0)
        )
        # Pattern labels can describe an internal one-agent workflow (for example,
        # request-response or verification) without implying inter-agent coordination.
        return one_logical_agent and no_inter_agent_communication

    def select_single_agent_procedure(
        self,
        extracted: ExtractedCoordinationData,
        *,
        task_route: str | None = None,
    ) -> DeterministicProcedureDecision:
        """Select the non-protocol generation route before template validation."""

        if not self.is_single_agent_request(extracted, task_route=task_route):
            raise ValueError("single_agent_generation requires a single-agent request")
        coordination_only_reason = [
            "Single-agent requests do not enter coordination-template procedure selection."
        ]
        return DeterministicProcedureDecision(
            selected_procedure="single_agent_generation",
            selected_template_id=None,
            selected_template_rank=None,
            reason_codes=["single_agent_request"],
            explanation="Single-agent request has no inter-agent coordination to synthesize.",
            evidence={
                "task_route": str(task_route or "").strip().lower() or None,
                "number_of_agents": extracted.number_of_agents,
                "coordination_pattern_count": len(extracted.coordination_patterns),
                "communication_step_count": len(extracted.communication_flow),
                "number_of_channels": extracted.number_of_channels,
            },
            matched_fields=[],
            mismatched_fields=[],
            unknown_fields=[],
            parameterizable_fields=[],
            adaptable_fields=[],
            recomposable_fields=[],
            fatal_mismatch_fields=[],
            protected_fields=[],
            available_procedures=["single_agent_generation"],
            rejected_procedures={
                "exact_reuse": coordination_only_reason,
                "parameterized_reuse": coordination_only_reason,
                "partial_recomposition": coordination_only_reason,
                "full_generation": coordination_only_reason,
            },
            evidence_sufficient=True,
        )

    def single_agent_procedure_options(self) -> ProcedureOptions:
        """Describe the exclusive non-coordination route without ranking templates."""

        reason = "The request has one logical agent and no inter-agent communication."
        return ProcedureOptions(
            top_candidate_template_id=None,
            top_candidate_template_name=None,
            options=(
                _option("single_agent_generation", True, (reason,), (), None, None),
            ),
            ranking_summary={
                "ranking_order": [],
                "template_count": 0,
                "skipped": True,
                "reason": "single_agent_request",
            },
            validation_summary={
                "valid": True,
                "skipped": True,
                "reason": "single_agent_request",
            },
        )

    def rank(
        self,
        extracted: ExtractedCoordinationData,
        templates: Sequence[Template],
    ) -> list[TemplateRanking]:
        rankings = [self._ranking_for(extracted, template) for template in templates]
        return sorted(
            rankings,
            key=lambda ranking: (
                -ranking.score,
                -ranking.match_count,
                ranking.mismatch_count,
                -ranking.compared_count,
                ranking.template_id,
            ),
        )

    def validate(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> TemplateValidationResult:
        comparisons = self.compare_for_validation(extracted, template)
        matched = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is True)
        mismatched = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is False)
        unknown = tuple(comparison.attribute_name for comparison in comparisons if comparison.result is None)
        structural = [comparison for comparison in comparisons if comparison.attribute_name in PRIMARY_ATTRIBUTES]
        structural_compared = sum(1 for comparison in structural if comparison.result is not None)
        valid = structural_compared > 0 and not mismatched
        reasons: list[str] = [comparison.reason for comparison in comparisons]
        if structural_compared == 0:
            reasons.append("Candidate invalid: zero comparable structural attributes.")
        if mismatched:
            reasons.append(
                "Candidate invalid: known attribute mismatches: "
                + ", ".join(mismatched)
                + "."
            )
        return TemplateValidationResult(
            template_id=template.get_template_id(),
            valid=valid,
            matched_fields=matched,
            mismatched_fields=mismatched,
            unknown_fields=unknown,
            comparisons=comparisons,
            reasons=tuple(reasons),
        )

    def procedure_options(
        self,
        rankings: Sequence[TemplateRanking],
        validation: TemplateValidationResult | None,
        templates: Sequence[Template],
    ) -> ProcedureOptions:
        top = rankings[0] if rankings else None
        template = _find_template(templates, top.template_id) if top else None
        options = _build_procedure_options(top, validation, template)
        return ProcedureOptions(
            top_candidate_template_id=top.template_id if top else None,
            top_candidate_template_name=top.name_of_template if top else None,
            options=options,
            ranking_summary={
                "ranking_order": [ranking.template_id for ranking in rankings],
                "top_score": top.score if top else None,
                "top_match_count": top.match_count if top else None,
                "top_mismatch_count": top.mismatch_count if top else None,
                "top_unknown_count": top.unknown_count if top else None,
                "template_count": len(rankings),
            },
            validation_summary=validation.to_dict() if validation else {
                "valid": False,
                "reason": "No candidate validation was available.",
            },
        )

    def validation_audit(
        self,
        extracted: ExtractedCoordinationData,
        rankings: Sequence[TemplateRanking],
        templates: Sequence[Template],
    ) -> tuple[dict[str, object], tuple[TemplateValidationResult, ...]]:
        """Serialize every ranked candidate using the same deterministic rules."""

        templates_by_id = {template.get_template_id(): template for template in templates}
        candidates: list[dict[str, object]] = []
        validations: list[TemplateValidationResult] = []
        for rank, ranking in enumerate(rankings, start=1):
            template = templates_by_id[ranking.template_id]
            validation = self.validate(extracted, template)
            validations.append(validation)
            options = _build_procedure_options(ranking, validation, template)
            options_by_name = {option.procedure: option for option in options}
            mismatched = set(validation.mismatched_fields)
            parameterizable = set(template.get_parameterizable_fields())
            adaptable = set(template.get_adaptable_fields())
            fatal = set(template.get_fatal_mismatch_fields())
            field_results = {
                comparison.attribute_name: {
                    "result": comparison.result,
                    "request_value": comparison.extracted_value,
                    "template_value": comparison.template_value,
                    "reason": comparison.reason,
                }
                for comparison in validation.comparisons
            }
            pattern_comparison = next(
                (
                    comparison
                    for comparison in validation.comparisons
                    if comparison.attribute_name == "coordination_patterns"
                ),
                None,
            )
            pattern_overlap_count = _coordination_pattern_overlap_count(validation)
            count_qualified_recomposition_fields = (
                ["coordination_patterns"]
                if "coordination_patterns" in mismatched
                and pattern_overlap_count >= MIN_PARTIAL_PATTERN_OVERLAP
                else []
            )
            rejection_reasons: list[str] = []
            for procedure in ("exact_reuse", "parameterized_reuse", "partial_recomposition"):
                option = options_by_name[procedure]
                if not option.deterministically_available:
                    rejection_reasons.extend(option.blocking_reasons)
            candidates.append({
                "rank": rank,
                "template_id": ranking.template_id,
                "template_name": ranking.name_of_template,
                "valid": validation.valid,
                "score": ranking.score,
                "match_count": ranking.match_count,
                "mismatch_count": ranking.mismatch_count,
                "unknown_count": ranking.unknown_count,
                "ranking_match_count": ranking.match_count,
                "ranking_mismatch_count": ranking.mismatch_count,
                "ranking_unknown_count": ranking.unknown_count,
                "validation_match_count": len(validation.matched_fields),
                "validation_mismatch_count": len(validation.mismatched_fields),
                "validation_unknown_count": len(validation.unknown_fields),
                "matched_fields": list(validation.matched_fields),
                "mismatched_fields": list(validation.mismatched_fields),
                "unknown_fields": list(validation.unknown_fields),
                "field_results": field_results,
                "parameterizable_mismatches": sorted(mismatched.intersection(parameterizable)),
                "adaptable_mismatches": sorted(mismatched.intersection(adaptable)),
                "fatal_mismatches": sorted(mismatched.intersection(fatal)),
                "coordination_pattern_match": bool(
                    pattern_comparison and pattern_comparison.result is True
                ),
                "coordination_pattern_overlap_count": pattern_overlap_count,
                "count_qualified_recomposition_fields": count_qualified_recomposition_fields,
                "meaningful_structural_match_count": len(
                    set(validation.matched_fields).intersection(MEANINGFUL_REUSE_FIELDS)
                ),
                "eligible_for_exact_reuse": options_by_name["exact_reuse"].deterministically_available,
                "eligible_for_parameterized_reuse": options_by_name[
                    "parameterized_reuse"
                ].deterministically_available,
                "eligible_for_partial_recomposition": options_by_name[
                    "partial_recomposition"
                ].deterministically_available,
                "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
            })
        return (
            {"candidate_count": len(candidates), "ranked_candidates": candidates},
            tuple(validations),
        )

    def select_procedure(
        self,
        extracted: ExtractedCoordinationData,
        rankings: Sequence[TemplateRanking],
        validation: TemplateValidationResult | None,
        procedure_options: ProcedureOptions,
        templates: Sequence[Template],
    ) -> DeterministicProcedureDecision:
        """Select exactly one procedure using a stable preference order."""

        del extracted  # Comparisons already contain normalized extractor values.
        top = rankings[0] if rankings else None
        template = _find_template(templates, top.template_id) if top else None
        options_by_name = {option.procedure: option for option in procedure_options.options}
        preference: tuple[ProcedureName, ...] = (
            "exact_reuse",
            "parameterized_reuse",
            "partial_recomposition",
            "full_generation",
        )
        selected = next(
            (
                name
                for name in preference
                if options_by_name.get(name)
                and options_by_name[name].deterministically_available
            ),
            None,
        )
        if selected is None:
            raise ValueError("deterministic procedure options contain no available procedure")

        matched = list(validation.matched_fields if validation else ())
        mismatched = list(validation.mismatched_fields if validation else ())
        unknown = list(validation.unknown_fields if validation else ())
        parameterizable = list(template.get_parameterizable_fields() if template else ())
        adaptable = list(template.get_adaptable_fields() if template else ())
        pattern_overlap_count = _coordination_pattern_overlap_count(validation)
        recomposable_fields = (
            ["coordination_patterns"]
            if selected == "partial_recomposition"
            and "coordination_patterns" in mismatched
            and pattern_overlap_count >= MIN_PARTIAL_PATTERN_OVERLAP
            else []
        )
        fatal = sorted(
            set(mismatched).intersection(
                template.get_fatal_mismatch_fields() if template else ()
            ) - set(recomposable_fields)
        )
        evidence_sufficient = _exact_reuse_evidence_sufficient(top, validation)
        selected_template_id = top.template_id if selected != "full_generation" and top else None
        available = [
            name
            for name in preference
            if options_by_name.get(name)
            and options_by_name[name].deterministically_available
        ]
        rejected = {
            name: list(options_by_name[name].blocking_reasons)
            for name in preference
            if name in options_by_name
            and not options_by_name[name].deterministically_available
        }
        allowed_changes = (
            set(parameterizable)
            if selected == "parameterized_reuse"
            else set(adaptable)
            if selected == "partial_recomposition"
            else set()
        ) | set(recomposable_fields)
        protected = sorted(
            ({*PRIMARY_ATTRIBUTES, "limitations", "safety_properties"} - allowed_changes)
            | set(matched)
            | (
                set(template.get_fatal_mismatch_fields() if template else ())
                - allowed_changes
            )
        )
        reason_codes = _procedure_reason_codes(
            selected,
            top=top,
            fatal_mismatches=fatal,
            evidence_sufficient=evidence_sufficient,
            mismatched=mismatched,
        )
        if recomposable_fields:
            reason_codes = (*reason_codes, "coordination_pattern_overlap_sufficient")
        meaningful_match_count = (
            len(set(matched).intersection(MEANINGFUL_REUSE_FIELDS))
            + int("coordination_patterns" in recomposable_fields)
        )
        return DeterministicProcedureDecision(
            selected_procedure=selected,
            selected_template_id=selected_template_id,
            selected_template_rank=1 if selected_template_id else None,
            reason_codes=reason_codes,
            explanation="; ".join(code.replace("_", " ") for code in reason_codes) + ".",
            evidence={
                "match_count": len(matched),
                "mismatch_count": len(mismatched),
                "unknown_count": len(unknown),
                "ranking_match_count": top.match_count if top else 0,
                "ranking_mismatch_count": top.mismatch_count if top else 0,
                "ranking_unknown_count": top.unknown_count if top else 0,
                "coordination_pattern_match": "coordination_patterns" in matched,
                "coordination_pattern_overlap_count": pattern_overlap_count,
                "meaningful_structural_match_count": meaningful_match_count,
                "parameterizable_mismatches": sorted(set(mismatched).intersection(parameterizable)),
                "adaptable_mismatches": sorted(set(mismatched).intersection(adaptable)),
                "fatal_mismatches": fatal,
            },
            matched_fields=matched,
            mismatched_fields=mismatched,
            unknown_fields=unknown,
            parameterizable_fields=parameterizable,
            adaptable_fields=adaptable,
            recomposable_fields=recomposable_fields,
            fatal_mismatch_fields=fatal,
            protected_fields=protected,
            available_procedures=available,
            rejected_procedures=rejected,
            evidence_sufficient=evidence_sufficient,
        )

    def compare(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> tuple[AttributeComparison, ...]:
        return (
            _compare_patterns(
                "coordination_patterns",
                extracted.coordination_patterns,
                template.get_coordination_patterns(),
            ),
            _compare_exact_int(
                "number_of_agents",
                extracted.number_of_agents,
                template.get_number_of_agents(),
            ),
            _compare_subset_list(
                "agent_roles",
                extracted.agent_roles,
                template.get_agent_roles(),
            ),
            _compare_ordered_list(
                "communication_flow",
                extracted.communication_flow,
                template.get_communication_flow(),
            ),
            _compare_exact_int(
                "number_of_resources",
                extracted.number_of_resources,
                template.get_number_of_resources(),
            ),
            _compare_exact_int(
                "number_of_channels",
                extracted.number_of_channels,
                template.get_number_of_channels(),
            ),
        )

    def compare_for_validation(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> tuple[AttributeComparison, ...]:
        return self.compare(extracted, template) + (
            _compare_subset_list(
                "limitations",
                extracted.limitations,
                template.get_limitations(),
            ),
        )

    def _ranking_for(
        self,
        extracted: ExtractedCoordinationData,
        template: Template,
    ) -> TemplateRanking:
        comparisons = self.compare(extracted, template)
        primary = [comparison for comparison in comparisons if comparison.attribute_name in PRIMARY_ATTRIBUTES]
        match_count = sum(1 for comparison in primary if comparison.result is True)
        mismatch_count = sum(1 for comparison in primary if comparison.result is False)
        unknown_count = sum(1 for comparison in primary if comparison.result is None)
        compared_count = match_count + mismatch_count
        score = match_count / compared_count if compared_count > 0 else 0.0
        return TemplateRanking(
            template_id=template.get_template_id(),
            name_of_template=template.get_name_of_template(),
            match_count=match_count,
            mismatch_count=mismatch_count,
            unknown_count=unknown_count,
            compared_count=compared_count,
            score=round(score, 4),
            comparisons=comparisons,
        )


def rank_templates_for_attributes(
    extracted: ExtractedCoordinationData,
    templates: Sequence[Template],
) -> list[TemplateRanking]:
    return DeterministicTemplateEngine().rank(extracted, templates)


def validate_template_for_attributes(
    extracted: ExtractedCoordinationData,
    template: Template,
) -> TemplateValidationResult:
    return DeterministicTemplateEngine().validate(extracted, template)


def derive_procedure_options(
    rankings: Sequence[TemplateRanking],
    validation: TemplateValidationResult | None,
    templates: Sequence[Template],
) -> ProcedureOptions:
    return DeterministicTemplateEngine().procedure_options(rankings, validation, templates)


def _compare_patterns(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_values = tuple(extracted)
    template = tuple(template_values)
    if not extracted_values or not template:
        return _unknown(attribute_name, extracted_values, template, "One side has no coordination patterns.")
    extracted_set = {_normalize_term(value) for value in extracted_values}
    template_set = {_normalize_term(value) for value in template}
    missing = sorted(extracted_set - template_set)
    if missing:
        return _mismatch(
            attribute_name,
            extracted_values,
            template,
            f"Missing template patterns: {missing}.",
        )
    return _match(
        attribute_name,
        extracted_values,
        template,
        "Every extracted coordination pattern is present.",
    )


def _compare_exact_int(
    attribute_name: str,
    extracted: int | None,
    template_value: int | None,
) -> AttributeComparison:
    if extracted is None or template_value is None:
        return _unknown(attribute_name, extracted, template_value, "One side has no numeric value.")
    if extracted == template_value:
        return _match(attribute_name, extracted, template_value, f"Exact numeric match: {extracted}.")
    return _mismatch(attribute_name, extracted, template_value, f"Numeric mismatch: {extracted} != {template_value}.")


def _compare_subset_list(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_norm = tuple(_normalize_term(value) for value in extracted if _normalize_term(value))
    template_norm = tuple(_normalize_term(value) for value in template_values if _normalize_term(value))
    if not extracted_norm or not template_norm:
        return _unknown(attribute_name, extracted_norm, template_norm, "One side has no comparable values.")
    template_set = set(template_norm)
    missing = [value for value in extracted_norm if value not in template_set]
    if missing:
        return _mismatch(attribute_name, extracted_norm, template_norm, f"Missing template values: {missing}.")
    return _match(attribute_name, extracted_norm, template_norm, "Every extracted value is present.")


def _compare_ordered_list(
    attribute_name: str,
    extracted: Sequence[str],
    template_values: Sequence[str],
) -> AttributeComparison:
    extracted_norm = tuple(_normalize_term(value) for value in extracted if _normalize_term(value))
    template_norm = tuple(_normalize_term(value) for value in template_values if _normalize_term(value))
    if not extracted_norm or not template_norm:
        return _unknown(attribute_name, extracted_norm, template_norm, "One side has no comparable ordered values.")
    if extracted_norm == template_norm:
        return _match(attribute_name, extracted_norm, template_norm, "Ordered values match exactly.")
    return _mismatch(attribute_name, extracted_norm, template_norm, "Ordered values differ.")


def _build_procedure_options(
    top: TemplateRanking | None,
    validation: TemplateValidationResult | None,
    template: Template | None,
) -> tuple[ProcedureOption, ...]:
    if top is None or template is None:
        no_candidate = "No ranked candidate template exists."
        return (
            _option("exact_reuse", False, (), (no_candidate,), None, None),
            _option("parameterized_reuse", False, (), (no_candidate,), None, None),
            _option("partial_recomposition", False, (), (no_candidate,), None, None),
            _option(
                "full_generation",
                True,
                ("No stored template is available for safe reuse.",),
                (),
                None,
                None,
            ),
        )

    candidate_id = top.template_id
    candidate_name = top.name_of_template
    mismatched = set(validation.mismatched_fields if validation else ())
    matched = set(validation.matched_fields if validation else ())
    fatal_fields = set(template.get_fatal_mismatch_fields())
    parameterizable = set(template.get_parameterizable_fields())
    adaptable = set(template.get_adaptable_fields())
    fatal_mismatches = sorted(mismatched.intersection(fatal_fields))
    compared = top.compared_count
    meaningful_matches = matched.intersection(MEANINGFUL_REUSE_FIELDS)
    pattern_match = "coordination_patterns" in matched
    pattern_overlap_count = _coordination_pattern_overlap_count(validation)
    count_qualified_pattern_recomposition = (
        "coordination_patterns" in mismatched
        and pattern_overlap_count >= MIN_PARTIAL_PATTERN_OVERLAP
    )
    partial_meaningful_match_count = len(meaningful_matches) + int(
        count_qualified_pattern_recomposition
    )
    partial_required_changes = mismatched - (
        {"coordination_patterns"} if count_qualified_pattern_recomposition else set()
    )
    partial_fatal_mismatches = set(fatal_mismatches) - (
        {"coordination_patterns"} if count_qualified_pattern_recomposition else set()
    )
    exact_evidence_sufficient = _exact_reuse_evidence_sufficient(top, validation)
    reuse_evidence_sufficient = (
        pattern_match
        and len(meaningful_matches) >= MIN_REUSE_MATCHED_STRUCTURAL_FIELDS
    )

    exact_available = bool(
        validation
        and validation.valid
        and top.mismatch_count == 0
        and exact_evidence_sufficient
    )
    exact_blockers: list[str] = []
    exact_support: list[str] = []
    if exact_available:
        exact_support.append("Top candidate validation passed with zero known structural mismatches.")
    else:
        if validation is None:
            exact_blockers.append("No validation result is available.")
        if compared == 0:
            exact_blockers.append("No comparable structural attributes were available.")
        if compared > 0 and not exact_evidence_sufficient:
            exact_blockers.append(
                "Exact reuse requires a coordination-pattern match and at least two meaningful "
                "matched structural fields."
            )
        if top.mismatch_count:
            exact_blockers.append(f"Known attribute mismatches exist: {sorted(mismatched)}.")
        if validation is not None and not validation.valid:
            exact_blockers.append("Candidate validation did not pass.")

    parameterized_available = (
        bool(top and validation)
        and not exact_available
        and compared > 0
        and bool(mismatched)
        and reuse_evidence_sufficient
        and not fatal_mismatches
        and mismatched.issubset(parameterizable)
    )
    parameterized_support: list[str] = []
    parameterized_blockers: list[str] = []
    if parameterized_available:
        parameterized_support.append(f"Mismatches are declared parameterizable: {sorted(mismatched)}.")
        parameterized_support.append("No fatal structural mismatch was reported.")
    else:
        if exact_available:
            parameterized_blockers.append("Exact reuse is already sufficient; no parameter change is required.")
        if not mismatched:
            parameterized_blockers.append("No parameter mismatch was detected.")
        if not reuse_evidence_sufficient:
            parameterized_blockers.append(
                "Parameterized reuse requires a coordination-pattern match and at least two "
                "meaningful matched structural fields."
            )
        if fatal_mismatches:
            parameterized_blockers.append(f"Fatal mismatches block parameterization: {fatal_mismatches}.")
        missing_parameter_metadata = sorted(mismatched - parameterizable)
        if missing_parameter_metadata:
            parameterized_blockers.append(
                f"Mismatches are not declared parameterizable: {missing_parameter_metadata}."
            )

    partial_available = (
        not exact_available
        and not parameterized_available
        and partial_meaningful_match_count >= MIN_REUSE_MATCHED_STRUCTURAL_FIELDS
        and not partial_fatal_mismatches
        and bool(mismatched)
        and partial_required_changes.issubset(adaptable)
    )
    partial_support: list[str] = []
    partial_blockers: list[str] = []
    if partial_available:
        partial_support.append(f"Reusable structural evidence exists: {sorted(meaningful_matches)}.")
        partial_support.append(
            f"Coordination-pattern overlap count is {pattern_overlap_count}; "
            f"validator unknown field count is "
            f"{len(validation.unknown_fields) if validation else 0}."
        )
        partial_support.append(f"Template declares bounded adaptable fields: {sorted(adaptable)}.")
    else:
        if exact_available or parameterized_available:
            partial_blockers.append("A safer reuse procedure is already available.")
        if partial_meaningful_match_count < MIN_REUSE_MATCHED_STRUCTURAL_FIELDS:
            partial_blockers.append("Fewer than two meaningful reusable structural fields matched.")
        if partial_fatal_mismatches:
            partial_blockers.append(
                f"Fatal mismatches block bounded adaptation: {sorted(partial_fatal_mismatches)}."
            )
        if not adaptable:
            partial_blockers.append("Template does not declare bounded adaptable fields.")
        missing_adaptation_metadata = sorted(partial_required_changes - adaptable)
        if missing_adaptation_metadata:
            partial_blockers.append(
                f"Mismatches are not declared adaptable: {missing_adaptation_metadata}."
            )

    reuse_available = exact_available or parameterized_available or partial_available
    full_support = (
        ("Available as fallback if the downstream LLM rejects the reusable options.",)
        if reuse_available
        else ("No safe reuse procedure is currently available.",)
    )
    full_blockers = (
        ("A reusable procedure is deterministically available and should be preferred when safe.",)
        if reuse_available
        else ()
    )

    return (
        _option("exact_reuse", exact_available, exact_support, exact_blockers, candidate_id, candidate_name),
        _option(
            "parameterized_reuse",
            parameterized_available,
            parameterized_support,
            parameterized_blockers,
            candidate_id,
            candidate_name,
        ),
        _option(
            "partial_recomposition",
            partial_available,
            partial_support,
            partial_blockers,
            candidate_id,
            candidate_name,
        ),
        _option("full_generation", True, full_support, full_blockers, None, None),
    )


def _option(
    procedure: ProcedureName,
    available: bool,
    supporting: Sequence[str],
    blocking: Sequence[str],
    candidate_id: str | None,
    candidate_name: str | None,
) -> ProcedureOption:
    return ProcedureOption(
        procedure=procedure,
        deterministically_available=available,
        supporting_reasons=tuple(supporting),
        blocking_reasons=tuple(blocking),
        candidate_template_id=candidate_id,
        candidate_template_name=candidate_name,
    )


def _find_template(templates: Sequence[Template], template_id: str) -> Template | None:
    for template in templates:
        if template.get_template_id() == template_id:
            return template
    return None


def _exact_reuse_evidence_sufficient(
    top: TemplateRanking | None,
    validation: TemplateValidationResult | None,
) -> bool:
    if top is None or validation is None:
        return False
    matched = set(validation.matched_fields)
    meaningful_matches = matched.intersection(MEANINGFUL_REUSE_FIELDS)
    return (
        "coordination_patterns" in matched
        and len(meaningful_matches) >= MIN_REUSE_MATCHED_STRUCTURAL_FIELDS
    )


def _procedure_reason_codes(
    procedure: ProcedureName,
    *,
    top: TemplateRanking | None,
    evidence_sufficient: bool,
    mismatched: Sequence[str],
    fatal_mismatches: Sequence[str],
) -> tuple[str, ...]:
    if procedure == "exact_reuse":
        return ("top_candidate_valid", "zero_known_mismatches", "exact_evidence_sufficient")
    if procedure == "parameterized_reuse":
        return ("exact_reuse_unavailable", "all_mismatches_parameterizable", "no_fatal_mismatch")
    if procedure == "partial_recomposition":
        return ("safer_reuse_unavailable", "bounded_adaptation_available", "no_fatal_mismatch")
    if top is None:
        return ("no_ranked_template", "full_generation_fallback")
    if fatal_mismatches:
        return ("fatal_template_mismatch", "full_generation_fallback")
    if not evidence_sufficient and not mismatched:
        return ("insufficient_reuse_evidence", "full_generation_fallback")
    return ("no_safe_reuse_procedure", "full_generation_fallback")


def _match(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, True, reason)


def _mismatch(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, False, reason)


def _unknown(attribute_name: str, extracted: object, template: object, reason: str) -> AttributeComparison:
    return AttributeComparison(attribute_name, extracted, template, None, reason)


def _normalize_term(value: Any) -> str:
    return "_".join(
        str(value)
        .strip()
        .lower()
        .replace("-", " ")
        .split()
    )


def _coordination_pattern_overlap_count(
    validation: TemplateValidationResult | None,
) -> int:
    if validation is None:
        return 0
    comparison = next(
        (
            item
            for item in validation.comparisons
            if item.attribute_name == "coordination_patterns"
        ),
        None,
    )
    if comparison is None:
        return 0
    extracted = {_normalize_term(value) for value in comparison.extracted_value or ()}
    template = {_normalize_term(value) for value in comparison.template_value or ()}
    return len(extracted.intersection(template))
