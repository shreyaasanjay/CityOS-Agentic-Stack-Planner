"""Protocol template registry and deterministic built-in instantiation.

Ranking and route selection remain in the deterministic template engine. This
registry exposes template metadata plus the builders needed after exact reuse
has already been selected.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any

from tracefix.protocol_templates import (
    attendance_verification,
    fan_in_decision,
    producer_consumer,
    sequential_handoff,
    traffic_signal_coordination,
    verifier_approver,
)
from tracefix.protocol_templates.template import Template

_TEMPLATE_MODULES = (
    fan_in_decision,
    sequential_handoff,
    verifier_approver,
    producer_consumer,
    attendance_verification,
    traffic_signal_coordination,
)

_BY_ID: dict[str, object] = {
    str(module.PATTERN_ID): module for module in _TEMPLATE_MODULES
}
_GENERATED_TEMPLATES: dict[str, Template] = {}
_GENERATED_ARTIFACT_DIRS: dict[str, Path] = {}
_PERSISTED_ROOT_LOADED: Path | None = None


@dataclass(frozen=True)
class RegistryLoadDiagnostics:
    loaded_templates: tuple[Template, ...]
    skipped_entries: tuple[dict[str, str], ...]

    def __iter__(self):
        return iter(self.loaded_templates)

    def __getitem__(self, index):
        return self.loaded_templates[index]

    def __len__(self) -> int:
        return len(self.loaded_templates)

_BUILTIN_TEMPLATE_ATTRIBUTES: dict[str, dict[str, Any]] = {
    "attendance_verification": {
        "coordination_patterns": ["Observer", "Verification"],
        "number_of_agents": 2,
        "agent_roles": ["observer", "verifier"],
        "communication_flow": ["observe", "submit_evidence", "verify", "report"],
        "limitations": ["insufficient_evidence_must_be_reported"],
        "number_of_resources": 2,
        "number_of_channels": 2,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "fan_in_decision": {
        "coordination_patterns": ["Split-and-Merge", "Majority Voting"],
        "number_of_agents": None,
        "agent_roles": ["evidence_agent", "decision_agent"],
        "communication_flow": ["submit_result", "wait_for_all_sources", "decide"],
        "limitations": ["decision_waits_for_every_source"],
        "number_of_resources": 0,
        "number_of_channels": None,
        "parameterizable_fields": ["number_of_agents", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "producer_consumer": {
        "coordination_patterns": ["Producer-Consumer"],
        "number_of_agents": 2,
        "agent_roles": ["producer", "consumer"],
        "communication_flow": ["produce", "send", "receive", "consume"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 1,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "sequential_handoff": {
        "coordination_patterns": ["Sequential Handoff"],
        "number_of_agents": 2,
        "agent_roles": ["upstream_agent", "downstream_agent"],
        "communication_flow": ["work", "handoff", "receive", "continue"],
        "limitations": ["no_acknowledgement_required"],
        "number_of_resources": 2,
        "number_of_channels": 1,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "traffic_signal_coordination": {
        "coordination_patterns": [
            "Request-Grant",
            "Exclusive Resource Access",
            "Task Prioritization",
            "Queue-Based Scheduling",
            "Reservation",
        ],
        "number_of_agents": None,
        "agent_roles": [],
        "communication_flow": [
            "request", "grant", "enter", "exit", "release",
            "enqueue", "dequeue", "complete",
        ],
        "limitations": [
            "only_one_resource_holder_at_a_time",
            "priority_requests_must_be_handled",
            "ordinary_requests_must_not_starve",
            "request_before_entering",
            "release_after_completion",
        ],
        "number_of_resources": 1,
        "number_of_channels": None,
        "parameterizable_fields": ["number_of_agents", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
    "verifier_approver": {
        "coordination_patterns": ["Verification", "Request-Grant"],
        "number_of_agents": 2,
        "agent_roles": ["worker", "verifier"],
        "communication_flow": ["submit", "verify", "approve_or_reject", "return_verdict"],
        "limitations": ["verifier_must_return_verdict"],
        "number_of_resources": 2,
        "number_of_channels": 2,
        "parameterizable_fields": ["number_of_agents", "number_of_resources", "number_of_channels"],
        "adaptable_fields": ["agent_roles", "communication_flow", "limitations"],
    },
}


def list_pattern_ids() -> list[str]:
    """Return registered built-in and in-memory generated template IDs."""

    load_persisted_templates()
    return list(_BY_ID) + list(_GENERATED_TEMPLATES)


def get_template_metadata(pattern_id: str) -> dict[str, Any]:
    """Return canonical Template metadata for a registered template.

    This is metadata only. It does not imply the template matched the request or
    is safe to reuse.
    """

    return get_template(pattern_id).to_dict()


def list_template_metadata() -> list[dict[str, Any]]:
    """Return metadata for all built-in templates."""

    return [get_template_metadata(pattern_id) for pattern_id in list_pattern_ids()]


def get_template(pattern_id: str) -> Template:
    """Return a data-only Template object for a built-in or generated template."""

    generated = _GENERATED_TEMPLATES.get(pattern_id)
    if generated is not None:
        return Template.from_dict(generated.to_dict())
    module = _BY_ID.get(pattern_id)
    if module is None:
        load_persisted_templates()
        generated = _GENERATED_TEMPLATES.get(pattern_id)
        if generated is not None:
            return Template.from_dict(generated.to_dict())
        raise KeyError(f"Unknown coordination pattern: {pattern_id!r}")
    attributes = deepcopy(_BUILTIN_TEMPLATE_ATTRIBUTES.get(pattern_id, {}))
    return Template(
        template_id=pattern_id,
        name_of_template=str(getattr(module, "DESCRIPTION", pattern_id)),
        coordination_patterns=list(attributes.get("coordination_patterns") or []),
        number_of_agents=attributes.get("number_of_agents"),
        agent_roles=list(attributes.get("agent_roles") or []),
        communication_flow=list(attributes.get("communication_flow") or []),
        limitations=list(attributes.get("limitations") or []),
        number_of_resources=attributes.get("number_of_resources"),
        number_of_channels=attributes.get("number_of_channels"),
        parameterizable_fields=list(attributes.get("parameterizable_fields") or []),
        adaptable_fields=list(attributes.get("adaptable_fields") or []),
        fatal_mismatch_fields=list(attributes.get("fatal_mismatch_fields") or ["coordination_patterns"]),
    )


def build_template(pattern_id: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Instantiate a built-in template through its deterministic builder."""

    module = _BY_ID.get(pattern_id)
    if module is None:
        load_persisted_templates()
        artifact_dir = _GENERATED_ARTIFACT_DIRS.get(pattern_id)
        if artifact_dir is None:
            raise KeyError(f"Unknown protocol template: {pattern_id!r}")
        ir_path = artifact_dir / "ir.json"
        tla_path = artifact_dir / "Protocol.tla"
        if not ir_path.is_file() or not tla_path.is_file():
            raise ValueError(
                f"Generated template artifacts are incomplete for {pattern_id!r}"
            )
        ir_data = json.loads(ir_path.read_text(encoding="utf-8"))
        if not isinstance(ir_data, dict):
            raise ValueError(f"Generated template IR must be an object: {pattern_id!r}")
        return deepcopy(ir_data), tla_path.read_text(encoding="utf-8")
    builder = getattr(module, "build_template", None)
    if not callable(builder):
        raise ValueError(f"Protocol template has no deterministic builder: {pattern_id!r}")
    return builder(dict(params))


def build_template_from_metadata(metadata: dict[str, Any]) -> Template:
    """Validate generated template metadata and return a Template object.

    Generated IDs are deterministic from the structural metadata unless an
    explicit template_id is already provided by trusted harness code.
    """

    if not isinstance(metadata, dict):
        raise ValueError("generated template metadata must be a mapping")
    canonical = dict(metadata)
    canonical.setdefault("template_id", "")
    Template.validate_canonical_keys(
        canonical,
        include_identity=True,
        include_reuse_policy=True,
    )
    template_id = str(canonical.get("template_id") or "").strip() or _generated_template_id(canonical)
    return Template(
        template_id=template_id,
        name_of_template=str(canonical.get("name_of_template") or "").strip(),
        coordination_patterns=list(canonical.get("coordination_patterns") or []),
        number_of_agents=canonical.get("number_of_agents"),
        agent_roles=list(canonical.get("agent_roles") or []),
        communication_flow=list(canonical.get("communication_flow") or []),
        limitations=list(canonical.get("limitations") or []),
        number_of_resources=canonical.get("number_of_resources"),
        number_of_channels=canonical.get("number_of_channels"),
        parameterizable_fields=list(canonical.get("parameterizable_fields") or []),
        adaptable_fields=list(canonical.get("adaptable_fields") or []),
        fatal_mismatch_fields=list(canonical.get("fatal_mismatch_fields") or ["coordination_patterns"]),
    )


def register_template(template: Template, *, artifact_dir: Path | None = None) -> None:
    """Register a generated Template in the active in-memory registry."""

    template_id = Template.validate_template_id(template.get_template_id())
    _ensure_collision_allowed(template)
    _GENERATED_TEMPLATES[template_id] = Template.from_dict(template.to_dict())
    if artifact_dir is not None:
        _GENERATED_ARTIFACT_DIRS[template_id] = Path(artifact_dir).resolve()


def generated_template_registry_root() -> Path:
    configured = (os.getenv("TRACEFIX_GENERATED_TEMPLATE_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    repo = next(
        parent for parent in Path(__file__).resolve().parents
        if (parent / "pyproject.toml").exists()
    )
    return repo / "tracefix" / "generated_templates"


def persist_template(
    template: Template,
    *,
    artifact_paths: dict[str, Path],
    registry_root: Path | None = None,
) -> Path:
    """Persist canonical reconstruction metadata plus verified artifacts."""

    root = Path(registry_root or generated_template_registry_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)
    template_id = Template.validate_template_id(template.get_template_id())
    destination = (root / template_id).resolve()
    if destination.parent != root:
        raise ValueError("template destination escapes the registry root")
    _validate_artifact_names(artifact_paths)
    with _registry_lock(root):
        _ensure_collision_allowed(template)
        if destination.exists():
            existing = Template.from_dict(json.loads((destination / "template.json").read_text(encoding="utf-8")))
            if existing.to_dict() != template.to_dict():
                raise ValueError(f"persisted template ID has conflicting metadata: {template_id}")
            _verify_existing_artifacts(destination, artifact_paths)
            _GENERATED_TEMPLATES[template_id] = existing
            _GENERATED_ARTIFACT_DIRS[template_id] = destination
            return destination
        temporary = Path(tempfile.mkdtemp(prefix=f".{template_id}.", dir=root))
        try:
            (temporary / "template.json").write_text(
                json.dumps(template.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            for canonical_name, source in artifact_paths.items():
                source_path = Path(source).resolve()
                if not source_path.is_file():
                    raise ValueError(f"verified template artifact is missing: {source_path}")
                shutil.copy2(source_path, temporary / canonical_name)
            reconstructed = Template.from_dict(json.loads((temporary / "template.json").read_text(encoding="utf-8")))
            if reconstructed.to_dict() != template.to_dict():
                raise ValueError("persisted template failed canonical reconstruction")
            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        _GENERATED_TEMPLATES[template_id] = reconstructed
        _GENERATED_ARTIFACT_DIRS[template_id] = destination
        return destination


def load_persisted_templates(registry_root: Path | None = None, *, force: bool = False) -> RegistryLoadDiagnostics:
    """Reconstruct persisted Templates directly from canonical metadata."""

    global _PERSISTED_ROOT_LOADED
    root = Path(registry_root or generated_template_registry_root()).resolve()
    if registry_root is None and not force and _PERSISTED_ROOT_LOADED == root:
        return RegistryLoadDiagnostics(tuple(_GENERATED_TEMPLATES.values()), ())
    if not root.exists():
        if registry_root is None:
            _PERSISTED_ROOT_LOADED = root
        return RegistryLoadDiagnostics((), ())
    loaded: list[Template] = []
    skipped: list[dict[str, str]] = []
    for metadata_path in sorted(root.glob("*/template.json")):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            template = Template.from_dict(payload)
            if template.get_template_id() != metadata_path.parent.name:
                raise ValueError("directory name does not match template_id")
            _ensure_collision_allowed(template)
            _GENERATED_TEMPLATES[template.get_template_id()] = template
            _GENERATED_ARTIFACT_DIRS[template.get_template_id()] = metadata_path.parent.resolve()
            loaded.append(template)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            skipped.append({"entry": str(metadata_path.parent), "reason": str(exc)})
    if registry_root is None:
        _PERSISTED_ROOT_LOADED = root
    return RegistryLoadDiagnostics(tuple(loaded), tuple(skipped))


def refresh_persisted_templates(registry_root: Path | None = None) -> RegistryLoadDiagnostics:
    return load_persisted_templates(registry_root, force=True)


def _ensure_collision_allowed(template: Template) -> None:
    template_id = Template.validate_template_id(template.get_template_id())
    if template_id in _BY_ID:
        raise ValueError(f"generated template conflicts with built-in ID: {template_id}")
    existing = _GENERATED_TEMPLATES.get(template_id)
    if existing is not None and existing.to_dict() != template.to_dict():
        raise ValueError(f"generated template ID has conflicting metadata: {template_id}")


def _validate_artifact_names(artifact_paths: dict[str, Path]) -> None:
    for name in artifact_paths:
        if not name or Path(name).name != name or name in {".", "..", "template.json"}:
            raise ValueError(f"unsafe template artifact name: {name!r}")


def _verify_existing_artifacts(destination: Path, artifact_paths: dict[str, Path]) -> None:
    for name, source in artifact_paths.items():
        existing = destination / name
        source_path = Path(source).resolve()
        if not existing.is_file() or not source_path.is_file() or existing.read_bytes() != source_path.read_bytes():
            raise ValueError(f"persisted template ID has conflicting artifact: {name}")


@contextmanager
def _registry_lock(root: Path):
    lock = root / ".registry.lock"
    deadline = time.monotonic() + 10.0
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for template registry lock: {lock}")
            time.sleep(0.01)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def clear_generated_templates_for_tests() -> None:
    """Clear generated templates. Intended for tests only."""

    _GENERATED_TEMPLATES.clear()
    _GENERATED_ARTIFACT_DIRS.clear()
    global _PERSISTED_ROOT_LOADED
    _PERSISTED_ROOT_LOADED = None


def _generated_template_id(metadata: dict[str, Any]) -> str:
    stable_payload = {
        "name_of_template": metadata.get("name_of_template"),
        "coordination_patterns": metadata.get("coordination_patterns") or [],
        "number_of_agents": metadata.get("number_of_agents"),
        "agent_roles": metadata.get("agent_roles") or [],
        "communication_flow": metadata.get("communication_flow") or [],
        "limitations": metadata.get("limitations") or [],
        "number_of_resources": metadata.get("number_of_resources"),
        "number_of_channels": metadata.get("number_of_channels"),
    }
    digest = hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return f"generated_{digest}"


__all__ = [
    "Template",
    "build_template",
    "build_template_from_metadata",
    "clear_generated_templates_for_tests",
    "get_template",
    "get_template_metadata",
    "list_pattern_ids",
    "list_template_metadata",
    "load_persisted_templates",
    "refresh_persisted_templates",
    "RegistryLoadDiagnostics",
    "persist_template",
    "generated_template_registry_root",
    "register_template",
]
