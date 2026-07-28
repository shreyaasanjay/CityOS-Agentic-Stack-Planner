"""Public compatibility package for deterministic template mapping."""
from __future__ import annotations

from tracefix.runtime.deterministic_template_engine import (
    AttributeComparison,
    DeterministicTemplateEngine,
    ProcedureOption,
    ProcedureOptions,
    TemplateRanking,
    TemplateValidationResult,
    derive_procedure_options,
    rank_templates_for_attributes,
    validate_template_for_attributes,
)

TemplateRank = TemplateRanking
TemplateRankingResult = list[TemplateRanking]

__all__ = [
    "AttributeComparison",
    "DeterministicTemplateEngine",
    "ProcedureOption",
    "ProcedureOptions",
    "TemplateRank",
    "TemplateRanking",
    "TemplateRankingResult",
    "TemplateValidationResult",
    "derive_procedure_options",
    "rank_templates_for_attributes",
    "validate_template_for_attributes",
]
