"""Promote verified workspaces using canonical Template reconstruction metadata."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tracefix.protocol_templates import persist_template
from tracefix.protocol_templates.template import Template
from tracefix.runtime.llm_attribute_extractor import ExtractedCoordinationData


def promote_verified_workspace_template(
    workspace: Path,
    *,
    extracted: ExtractedCoordinationData,
    tlc_passed: bool | None,
) -> tuple[Template, Path]:
    """Create, register, and persist a Template backed by verified artifacts."""

    ws = Path(workspace).resolve()
    spec = ws / "spec"
    if tlc_passed is not True:
        raise ValueError("generated templates require a successful TLC verdict")

    metadata_path = spec / "generated_template.json"
    if not metadata_path.is_file():
        raise ValueError("OpenCode contract violation: spec/generated_template.json is required")
    template = Template.from_dict(_read_object(metadata_path))
    _validate_against_extraction(template, extracted)
    _write_ir_consistency_report(spec, template, _read_object(spec / "ir.json"))

    # Always rewrite the workspace artifact through Template serialization so
    # persisted reconstruction metadata cannot retain aliases or extra keys.
    metadata_path.write_text(
        json.dumps(template.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    artifacts = {
        name: spec / name
        for name in ("ir.json", "Protocol.tla", "Protocol.cfg", "states.json")
    }
    cityos_plan = spec / "cityos_module_plan.json"
    if cityos_plan.is_file():
        artifacts["cityos_module_plan.json"] = cityos_plan
    destination = persist_template(template, artifact_paths=artifacts)
    return template, destination


def _validate_against_extraction(template: Template, extracted: ExtractedCoordinationData) -> None:
    attributes = extracted.as_dict()
    Template.validate_canonical_keys(
        attributes,
        include_identity=False,
        include_reuse_policy=False,
    )
    metadata = template.to_dict()
    for field in Template.COORDINATION_ATTRIBUTE_FIELDS:
        expected = attributes[field]
        if expected not in (None, []) and metadata[field] != expected:
            raise ValueError(f"generated metadata conflicts with authoritative extracted field: {field}")
    if metadata["coordination_patterns"] != attributes["coordination_patterns"]:
        raise ValueError("generated metadata conflicts with authoritative extracted field: coordination_patterns")


def _write_ir_consistency_report(spec: Path, template: Template, ir: dict[str, Any]) -> None:
    metadata = template.to_dict()
    checked: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for field, ir_key in (
        ("number_of_agents", "agents"),
        ("number_of_resources", "resources"),
        ("number_of_channels", "channels"),
    ):
        values = ir.get(ir_key)
        if not isinstance(values, list):
            raise ValueError(f"generated IR field must be a list: {ir_key}")
        item = {"field": field, "metadata": metadata[field], "ir": len(values)}
        checked.append(item)
        if metadata[field] != len(values):
            conflicts.append(item)
    report = {
        "checked": checked,
        "conflicts": conflicts,
        "not_cross_checkable": [
            {"field": field, "reason": "IR schema has no deterministic structured representation"}
            for field in ("agent_roles", "communication_flow", "coordination_patterns", "limitations")
        ],
    }
    (spec / "generated_template_consistency.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if conflicts:
        raise ValueError("generated template metadata conflicts with generated IR")


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required generated-template artifact is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"generated-template artifact must be an object: {path}")
    return payload


__all__ = ["promote_verified_workspace_template"]
