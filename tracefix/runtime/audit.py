"""Small, deterministic writers for runtime audit artifacts."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def to_audit_value(value: object) -> Any:
    """Convert supported runtime values into JSON-safe deterministic data."""

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return to_audit_value(value.to_dict())
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return to_audit_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return to_audit_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_audit_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_audit_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [to_audit_value(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Enum):
        return to_audit_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported audit value type: {type(value).__name__}")


def audit_json_text(payload: object) -> str:
    return json.dumps(
        to_audit_value(payload),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def write_audit_json(path: Path, payload: object) -> None:
    _atomic_write(Path(path), audit_json_text(payload))


def write_audit_text(path: Path, text: str) -> None:
    _atomic_write(Path(path), str(text))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = ["audit_json_text", "to_audit_value", "write_audit_json", "write_audit_text"]
