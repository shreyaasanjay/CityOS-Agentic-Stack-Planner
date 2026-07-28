"""Data-only protocol template attribute object."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar
import re
from tracefix.runtime.coordination_patterns import normalize_coordination_patterns


class Template:
    """Store template attributes for a future mapper.

    This class intentionally has no matching, ranking, scoring, selection, or
    confidence behavior.
    """

    IDENTITY_FIELDS: ClassVar[tuple[str, ...]] = (
        "template_id",
        "name_of_template",
    )
    COORDINATION_ATTRIBUTE_FIELDS: ClassVar[tuple[str, ...]] = (
        "coordination_patterns",
        "number_of_agents",
        "agent_roles",
        "communication_flow",
        "limitations",
        "number_of_resources",
        "number_of_channels",
    )
    REUSE_POLICY_FIELDS: ClassVar[tuple[str, ...]] = (
        "parameterizable_fields",
        "adaptable_fields",
        "fatal_mismatch_fields",
    )
    CANONICAL_FIELDS: ClassVar[tuple[str, ...]] = (
        *IDENTITY_FIELDS,
        *COORDINATION_ATTRIBUTE_FIELDS,
        *REUSE_POLICY_FIELDS,
    )
    ATTRIBUTE_SEMANTICS: ClassVar[dict[str, str]] = {
        "coordination_patterns": "The coordination mechanisms required by the protocol.",
        "number_of_agents": "The number of participating coordinating agents.",
        "agent_roles": "The functional roles performed by participating agents.",
        "communication_flow": "The ordered logical communication sequence between agents.",
        "limitations": "The constraints and guarantees that must hold during protocol execution.",
        "number_of_resources": "The number of coordinated shared resources.",
        "number_of_channels": "The number of logical communication channels between agents.",
        "parameterizable_fields": "Canonical attributes that may change through parameterized reuse.",
        "adaptable_fields": "Canonical attributes that may change through partial recomposition.",
        "fatal_mismatch_fields": "Canonical attributes whose mismatch blocks ordinary reuse.",
    }
    TEMPLATE_ID_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    WINDOWS_RESERVED_IDS: ClassVar[frozenset[str]] = frozenset(
        {"con", "prn", "aux", "nul"}
        | {f"com{i}" for i in range(1, 10)}
        | {f"lpt{i}" for i in range(1, 10)}
    )

    @classmethod
    def validate_template_id(cls, value: object) -> str:
        """Return a safe registry slug or reject filesystem-capable input."""

        if not isinstance(value, str) or value != value.strip() or not value:
            raise ValueError("template_id must be a non-empty canonical slug")
        if not cls.TEMPLATE_ID_PATTERN.fullmatch(value):
            raise ValueError("template_id may contain only lowercase letters, digits, underscores, and hyphens")
        if value in cls.WINDOWS_RESERVED_IDS:
            raise ValueError(f"template_id is reserved by the filesystem: {value}")
        return value

    @classmethod
    def empty_coordination_attributes(cls) -> dict[str, object]:
        """Return the canonical extractor shape owned by Template."""

        return {
            "coordination_patterns": [],
            "number_of_agents": None,
            "agent_roles": [],
            "communication_flow": [],
            "limitations": [],
            "number_of_resources": None,
            "number_of_channels": None,
        }

    @classmethod
    def validate_canonical_keys(
        cls,
        data: Mapping[str, object],
        *,
        include_identity: bool,
        include_reuse_policy: bool,
    ) -> None:
        """Reject missing or alternate coordination-schema field names."""

        expected = set(cls.COORDINATION_ATTRIBUTE_FIELDS)
        if include_identity:
            expected.update(cls.IDENTITY_FIELDS)
        if include_reuse_policy:
            expected.update(cls.REUSE_POLICY_FIELDS)
        actual = set(data)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing canonical fields: {missing}")
            if extra:
                details.append(f"unsupported alternate fields: {extra}")
            raise ValueError("; ".join(details))

    def __init__(
        self,
        template_id: str,
        name_of_template: str,
        coordination_patterns: list[str] | tuple[str, ...],
        number_of_agents: int | None,
        agent_roles: list[str] | tuple[str, ...],
        communication_flow: list[str] | tuple[str, ...],
        limitations: list[str] | tuple[str, ...],
        number_of_resources: int | None,
        number_of_channels: int | None,
        parameterizable_fields: list[str] | tuple[str, ...] | None = None,
        adaptable_fields: list[str] | tuple[str, ...] | None = None,
        fatal_mismatch_fields: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._template_id = self.validate_template_id(template_id)
        self._name_of_template = _clean_required_string(name_of_template, "name_of_template")
        self._coordination_patterns = tuple(normalize_coordination_patterns(coordination_patterns))
        self._number_of_agents = _normalize_optional_count(number_of_agents, "number_of_agents")
        self._agent_roles = tuple(_normalize_string_sequence(agent_roles, "agent_roles"))
        self._communication_flow = tuple(_normalize_string_sequence(communication_flow, "communication_flow"))
        self._limitations = tuple(_normalize_string_sequence(limitations, "limitations"))
        self._number_of_resources = _normalize_optional_count(number_of_resources, "number_of_resources")
        self._number_of_channels = _normalize_optional_count(number_of_channels, "number_of_channels")
        self._parameterizable_fields = tuple(
            _normalize_field_names(parameterizable_fields or (), "parameterizable_fields")
        )
        self._adaptable_fields = tuple(_normalize_field_names(adaptable_fields or (), "adaptable_fields"))
        self._fatal_mismatch_fields = tuple(
            _normalize_field_names(
                fatal_mismatch_fields if fatal_mismatch_fields is not None else ("coordination_patterns",),
                "fatal_mismatch_fields",
            )
        )

    def get_template_id(self) -> str:
        return self._template_id

    def get_name_of_template(self) -> str:
        return self._name_of_template

    def get_coordination_patterns(self) -> list[str]:
        return list(self._coordination_patterns)

    def get_number_of_agents(self) -> int | None:
        return self._number_of_agents

    def get_agent_roles(self) -> list[str]:
        return list(self._agent_roles)

    def get_communication_flow(self) -> list[str]:
        return list(self._communication_flow)

    def get_limitations(self) -> list[str]:
        return list(self._limitations)

    def get_number_of_resources(self) -> int | None:
        return self._number_of_resources

    def get_number_of_channels(self) -> int | None:
        return self._number_of_channels

    def get_parameterizable_fields(self) -> list[str]:
        return list(self._parameterizable_fields)

    def get_adaptable_fields(self) -> list[str]:
        return list(self._adaptable_fields)

    def get_fatal_mismatch_fields(self) -> list[str]:
        return list(self._fatal_mismatch_fields)

    def to_dict(self) -> dict[str, object]:
        """Return the stable serialized template attribute schema."""

        return {
            "template_id": self._template_id,
            "name_of_template": self._name_of_template,
            "coordination_patterns": list(self._coordination_patterns),
            "number_of_agents": self._number_of_agents,
            "agent_roles": list(self._agent_roles),
            "communication_flow": list(self._communication_flow),
            "limitations": list(self._limitations),
            "number_of_resources": self._number_of_resources,
            "number_of_channels": self._number_of_channels,
            "parameterizable_fields": list(self._parameterizable_fields),
            "adaptable_fields": list(self._adaptable_fields),
            "fatal_mismatch_fields": list(self._fatal_mismatch_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Template":
        """Construct and validate a Template from serialized attributes."""

        if not isinstance(data, Mapping):
            raise ValueError("template data must be a mapping")
        # Reuse-policy fields are optional constructor metadata.  Normalize
        # their omissions to canonical defaults before validating the schema;
        # alternate or extra field names remain invalid.
        canonical = dict(data)
        canonical.setdefault("parameterizable_fields", [])
        canonical.setdefault("adaptable_fields", [])
        canonical.setdefault("fatal_mismatch_fields", ["coordination_patterns"])
        cls.validate_canonical_keys(
            canonical,
            include_identity=True,
            include_reuse_policy=True,
        )
        return cls(
            template_id=_required(canonical, "template_id"),
            name_of_template=_required(canonical, "name_of_template"),
            coordination_patterns=_sequence(canonical.get("coordination_patterns"), "coordination_patterns"),
            number_of_agents=_optional_int(canonical.get("number_of_agents"), "number_of_agents"),
            agent_roles=_sequence(canonical.get("agent_roles"), "agent_roles"),
            communication_flow=_sequence(canonical.get("communication_flow"), "communication_flow"),
            limitations=_sequence(canonical.get("limitations"), "limitations"),
            number_of_resources=_optional_int(canonical.get("number_of_resources"), "number_of_resources"),
            number_of_channels=_optional_int(canonical.get("number_of_channels"), "number_of_channels"),
            parameterizable_fields=_sequence(canonical.get("parameterizable_fields"), "parameterizable_fields"),
            adaptable_fields=_sequence(canonical.get("adaptable_fields"), "adaptable_fields"),
            fatal_mismatch_fields=_sequence(canonical.get("fatal_mismatch_fields"), "fatal_mismatch_fields")
            if canonical.get("fatal_mismatch_fields") is not None
            else None,
        )


def _required(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} is required")
    return value


def _sequence(value: object, field_name: str) -> list[str] | tuple[str, ...]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a list or tuple")
    return list(value)


def _optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or None")
    return value


def _clean_required_string(value: str, field_name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field_name} is required")
    return cleaned


def _normalize_string_sequence(value: list[str] | tuple[str, ...], field_name: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a list or tuple")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings")
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _normalize_optional_count(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer or None")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    return value


def _normalize_machine_terms(value: list[str] | tuple[str, ...], field_name: str) -> list[str]:
    terms = _normalize_string_sequence(value, field_name)
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = "_".join(
            term.strip()
            .lower()
            .replace("-", " ")
            .split()
        )
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


_ALLOWED_TEMPLATE_ATTRIBUTE_FIELDS = set(Template.COORDINATION_ATTRIBUTE_FIELDS)

_IDENTITY_FIELDS = {"template_id", "name_of_template", "pattern_id"}


def _normalize_field_names(value: list[str] | tuple[str, ...], field_name: str) -> list[str]:
    fields = _normalize_machine_terms(value, field_name)
    for field in fields:
        if field in _IDENTITY_FIELDS:
            raise ValueError(f"{field_name} cannot contain identity field: {field}")
        if field not in _ALLOWED_TEMPLATE_ATTRIBUTE_FIELDS:
            raise ValueError(f"{field_name} contains unsupported template attribute field: {field}")
    return fields
