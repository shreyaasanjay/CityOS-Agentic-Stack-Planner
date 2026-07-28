"""Strict model for deterministic procedure-selection results.

The decision represented here is produced by deterministic code.  It is never
parsed from, proposed by, or delegated to an LLM.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ProcedureName = Literal[
    "single_agent_generation",
    "exact_reuse",
    "parameterized_reuse",
    "partial_recomposition",
    "full_generation",
]

REUSE_PROCEDURES = frozenset({
    "exact_reuse",
    "parameterized_reuse",
    "partial_recomposition",
})

GENERATION_PROCEDURES = frozenset({
    "single_agent_generation",
    "full_generation",
})


class DeterministicProcedureDecision(BaseModel):
    """One authoritative, reproducible procedure decision."""

    model_config = ConfigDict(extra="forbid")

    selected_procedure: ProcedureName
    selected_template_id: str | None
    selected_template_rank: int | None = None
    reason_codes: list[str] = Field(min_length=1)
    explanation: str = ""
    evidence: dict[str, object] = Field(default_factory=dict)
    matched_fields: list[str] = Field(default_factory=list)
    mismatched_fields: list[str] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    parameterizable_fields: list[str] = Field(default_factory=list)
    adaptable_fields: list[str] = Field(default_factory=list)
    recomposable_fields: list[str] = Field(default_factory=list)
    fatal_mismatch_fields: list[str] = Field(default_factory=list)
    protected_fields: list[str] = Field(default_factory=list)
    available_procedures: list[ProcedureName] = Field(min_length=1)
    rejected_procedures: dict[str, list[str]] = Field(default_factory=dict)
    evidence_sufficient: bool

    @model_validator(mode="after")
    def _validate_internal_consistency(self) -> "DeterministicProcedureDecision":
        if self.selected_procedure not in self.available_procedures:
            raise ValueError("selected procedure must be included in available_procedures")
        if self.selected_procedure in REUSE_PROCEDURES and not self.selected_template_id:
            raise ValueError("reuse procedures require selected_template_id")
        if self.selected_procedure in GENERATION_PROCEDURES and self.selected_template_id is not None:
            raise ValueError("generation procedures must not claim a selected template")
        if self.selected_procedure in GENERATION_PROCEDURES and self.selected_template_rank is not None:
            raise ValueError("generation procedures must not claim a selected template rank")
        if self.selected_procedure in REUSE_PROCEDURES and self.fatal_mismatch_fields:
            raise ValueError("reuse procedures cannot contain fatal mismatches")
        mismatch_set = set(self.mismatched_fields)
        if self.selected_procedure == "parameterized_reuse" and not mismatch_set.issubset(
            self.parameterizable_fields
        ):
            raise ValueError("parameterized_reuse contains non-parameterizable mismatches")
        if self.selected_procedure == "partial_recomposition" and not mismatch_set.issubset(
            set(self.adaptable_fields).union(self.recomposable_fields)
        ):
            raise ValueError("partial_recomposition contains non-adaptable mismatches")
        return self

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


# Compatibility name for callers that only consumed the old result type.
ProcedureDecision = DeterministicProcedureDecision


__all__ = [
    "DeterministicProcedureDecision",
    "ProcedureDecision",
    "ProcedureName",
    "REUSE_PROCEDURES",
    "GENERATION_PROCEDURES",
]
