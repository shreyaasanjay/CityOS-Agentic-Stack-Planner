"""First-stage LLM extraction of coordination attributes.

The extractor only returns a validated attribute object. It does not select,
score, rank, recommend, route, or authorize protocol template reuse.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tracefix.protocol_templates.template import Template
from tracefix.runtime.coordination_patterns import (
    COORDINATION_PATTERNS,
    deterministic_communication_flow,
    normalize_coordination_patterns,
)


class AttributeExtractionError(RuntimeError):
    """Base error raised by the coordination attribute extractor."""


class AttributeExtractionResponseError(AttributeExtractionError):
    """Raised when a provider response is empty, invalid, or fails schema checks."""


class ExtractedCoordinationAttributes(BaseModel):
    """Strict validated first-stage coordination attributes."""

    model_config = ConfigDict(extra="forbid")

    coordination_patterns: list[str]
    number_of_agents: int | None
    agent_roles: list[str]
    communication_flow: list[str]
    limitations: list[str]
    number_of_resources: int | None
    number_of_channels: int | None

    @field_validator("coordination_patterns", mode="before")
    @classmethod
    def _validate_patterns(cls, value: Any) -> list[str]:
        return normalize_coordination_patterns(_coerce_string_list(value, "coordination_patterns"))

    @field_validator("agent_roles", "communication_flow", "limitations", mode="before")
    @classmethod
    def _validate_string_lists(cls, value: Any, info: Any) -> list[str]:
        return _dedupe_preserving_order(_coerce_string_list(value, str(info.field_name)))

    @field_validator("number_of_agents", "number_of_resources", "number_of_channels", mode="before")
    @classmethod
    def _validate_optional_count(cls, value: Any, info: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{info.field_name} must be an integer or null")
        if value < 0:
            raise ValueError(f"{info.field_name} must not be negative")
        return value

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ExtractedCoordinationAttributes":
        """Validate an LLM payload and deterministically complete known flow."""

        try:
            Template.validate_canonical_keys(
                payload, include_identity=False, include_reuse_policy=False
            )
            validated = cls.model_validate(payload)
            deterministic_flow = deterministic_communication_flow(validated.coordination_patterns)
            if validated.communication_flow not in ([], deterministic_flow):
                raise ValueError(
                    "communication_flow must be [] or exactly the deterministic pattern expansion"
                )
            validated = validated.model_copy(update={"communication_flow": deterministic_flow})
            return validated
        except (ValidationError, ValueError) as exc:
            raise AttributeExtractionResponseError(
                "coordination attribute schema validation failed: " + str(exc)
            ) from exc

    def as_dict(self) -> dict[str, Any]:
        """Return the stable artifact dictionary."""

        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Serialize the artifact as pretty UTF-8 JSON text."""

        return json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n"


# Backward-compatible name for callers/tests that have not been renamed yet.
ExtractedCoordinationData = ExtractedCoordinationAttributes


def build_attribute_extraction_prompt(
    query: str | None = None,
    *,
    task_spec: Mapping[str, Any] | object | None = None,
    original_request: str | None = None,
    correction_feedback: str | None = None,
) -> list[dict[str, str]]:
    """Build provider-agnostic chat messages for attribute extraction."""

    task_spec_payload = _task_spec_payload(task_spec)
    supporting_request = (original_request if original_request is not None else query) or ""
    if not task_spec_payload and not supporting_request.strip():
        raise AttributeExtractionError("TaskSpec or coordination attribute query must be provided")
    vocabulary = "\n".join(f"- {pattern}" for pattern in COORDINATION_PATTERNS)
    schema = Template.empty_coordination_attributes()
    semantics = "\n".join(
        f"- {field}: {Template.ATTRIBUTE_SEMANTICS[field]}"
        for field in Template.COORDINATION_ATTRIBUTE_FIELDS
    )
    system = (
        "You are deriving the seven canonical TraceFix coordination attributes from a TeLLMe TaskSpec. "
        "The TaskSpec is a read-only authoritative description of the task. Do not modify, rewrite, "
        "correct, extend, or invent fields for the TaskSpec. Use explicit TaskSpec information directly, "
        "use deterministic implications where clear, and infer only when reasonably supported. "
        "The canonical TraceFix Template class is the single source of truth. These attributes are NOT "
        "arbitrary JSON; they are instance-variable values consumed directly by the "
        "deterministic validator. The validator expects these exact names. Do NOT "
        "rename fields, invent fields, or omit fields. If information cannot be "
        "determined, return null or [] as appropriate. You are an attribute extractor "
        "only. Do not select templates. Do not recommend templates. Do not rank "
        "templates. Do not route templates. Do not return confidence. Return only JSON."
    )
    user = (
        "Extract only the canonical coordination attributes. The TaskSpec is primary; the original "
        "request is secondary context and must not override explicit TaskSpec information.\n\n"
        "Return exactly this JSON shape and no other keys:\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Canonical field meanings:\n"
        f"{semantics}\n\n"
        "Rules:\n"
        "- coordination_patterns must contain zero or more values chosen only from the exact list below. Copy each value exactly; do not alter spelling, spacing, capitalization, punctuation, or hyphens; do not return duplicates or invent aliases.\n"
        "- Unknown list values must be []. Unknown numeric values must be null.\n"
        "- Do not invent unsupported information.\n"
        "- Return communication_flow as []; TraceFix deterministically expands every mapped canonical pattern after validation.\n"
        "- agent_roles contains functional roles, not arbitrary personal names.\n"
        "- limitations contains explicit restrictions, guarantees, deadlines, failure rules, or forbidden behaviors.\n"
        "- number_of_channels means explicitly identifiable logical communication channels, not message count.\n"
        "- These fields are consumed directly to determine Exact Reuse, Parameterized Reuse, Partial Recomposition, or Full Generation.\n"
        "- The deterministic pattern library may complete communication_flow after extraction when a recognized pattern has a fixed sequence.\n"
        "- No markdown, prose, comments, routing decisions, template IDs, or template metadata.\n\n"
        "Controlled coordination pattern vocabulary:\n"
        f"{vocabulary}\n\n"
        "READ-ONLY_TELLME_TASKSPEC_JSON:\n"
        f"{json.dumps(task_spec_payload, indent=2, ensure_ascii=False) if task_spec_payload else '(not supplied)'}\n\n"
        "SECONDARY_ORIGINAL_REQUEST:\n"
        f"{supporting_request.strip() or '(not supplied)'}\n\n"
        "TARGETED_REEVALUATION_FEEDBACK:\n"
        f"{(correction_feedback or '').strip() or '(initial extraction; no correction feedback)'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def extract_coordination_attributes(
    query: str | None = None,
    *,
    task_spec: Mapping[str, Any] | object | None = None,
    original_request: str | None = None,
    correction_feedback: str | None = None,
    model: str | None = None,
    client: object | None = None,
) -> ExtractedCoordinationAttributes:
    """Extract coordination attributes using a mocked or configured LLM client."""

    task_spec_payload = _task_spec_payload(task_spec)
    supporting_request = (original_request if original_request is not None else query) or ""
    if not task_spec_payload:
        _validate_query(supporting_request)
    messages = build_attribute_extraction_prompt(
        query,
        task_spec=task_spec_payload,
        original_request=supporting_request,
        correction_feedback=correction_feedback,
    )
    resolved_model = _resolve_model(model)
    active_client = client or _build_default_client(resolved_model)
    try:
        raw = _call_client(active_client, query=supporting_request, model=resolved_model, messages=messages)
    except AttributeExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap provider/client details
        raise AttributeExtractionError(f"coordination attribute provider failure: {exc}") from exc
    payload = _coerce_json_object(raw)
    return ExtractedCoordinationAttributes.from_payload(payload)


def _validate_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip():
        raise AttributeExtractionError("coordination attribute query must be non-empty")


def _task_spec_payload(task_spec: Mapping[str, Any] | object | None) -> dict[str, Any]:
    if task_spec is None:
        return {}
    if isinstance(task_spec, Mapping):
        return dict(task_spec)
    model_dump = getattr(task_spec, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        if isinstance(payload, dict):
            return payload
    raise AttributeExtractionError("task_spec must be a mapping or Pydantic model")


def _resolve_model(explicit_model: str | None) -> str:
    return (
        (explicit_model or "").strip()
        or (os.getenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_MODEL") or "").strip()
        or (os.getenv("TELLME_MODEL") or "").strip()
        or (os.getenv("OPENAI_MODEL") or "").strip()
        or "gpt-4.1-mini"
    )


def _build_default_client(model: str) -> object:
    try:
        from tellme_harness.config import get_llm_config
        from tellme_harness.llm_client import OpenAICompatibleLLMClient

        config = get_llm_config()
        api_key = (
            (os.getenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY") or "").strip()
            or (config.api_key or "")
        )
        if not api_key:
            raise AttributeExtractionError(
                "coordination attribute extraction requires an API key. "
                "Set TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_API_KEY, OPENROUTER_API_KEY, "
                "TELLME_API_KEY, or OPENAI_API_KEY."
            )
        base_url = (
            (os.getenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_BASE_URL") or "").strip()
            or config.base_url
        )
        return OpenAICompatibleLLMClient(
            base_url=base_url,
            model=model or config.model,
            api_key=api_key,
            timeout_seconds=config.timeout_seconds,
        )
    except AttributeExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AttributeExtractionError(f"could not configure attribute extractor client: {exc}") from exc


def _call_client(
    client: object,
    *,
    query: str,
    model: str,
    messages: list[dict[str, str]],
) -> Any:
    if callable(client):
        return client({"model": model, "messages": messages})
    if hasattr(client, "extract"):
        return client.extract(query=query, model=model, messages=messages)
    if hasattr(client, "complete"):
        return client.complete(model=model, messages=messages)
    if hasattr(client, "complete_json"):
        return client.complete_json(_messages_to_prompt(messages))
    raise AttributeExtractionError("client must be callable or expose extract(), complete(), or complete_json()")


def _coerce_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, ExtractedCoordinationAttributes):
        return raw.as_dict()
    if raw is None or raw == "":
        raise AttributeExtractionResponseError("coordination attribute response was empty")
    if isinstance(raw, dict):
        if "choices" in raw:
            try:
                content = raw["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise AttributeExtractionResponseError(
                    "LLM response did not contain message content"
                ) from exc
            return _coerce_json_object(content)
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise AttributeExtractionResponseError("coordination attribute response was empty")
        try:
            payload = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
        except json.JSONDecodeError as exc:
            raise AttributeExtractionResponseError(f"invalid coordination attribute JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise AttributeExtractionResponseError("coordination attribute JSON must decode to an object")
        return payload
    raise AttributeExtractionResponseError(f"unsupported LLM response type: {type(raw).__name__}")


def _coerce_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        raise ValueError(f"{field_name} must be a list, not null")
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings")
        stripped = item.strip()
        if stripped:
            cleaned.append(stripped)
    return cleaned


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttributeExtractionResponseError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    return "\n\n".join(f"{message['role'].upper()}:\n{message['content']}" for message in messages)


if tuple(ExtractedCoordinationAttributes.model_fields) != Template.COORDINATION_ATTRIBUTE_FIELDS:
    raise RuntimeError("extractor schema diverged from canonical Template coordination fields")
