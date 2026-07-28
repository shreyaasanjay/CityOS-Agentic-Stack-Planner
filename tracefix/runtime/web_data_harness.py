"""Host-side runner for synthesized TraceFix apps against a web data source.

This keeps the CityOS synthesis path intact while letting the same generated
app bundles consume data from a normal HTTP server when CityOS is not the
runtime environment. When the source is the smartroom-control LAN API, the
runner collects a structured snapshot from the API rather than just fetching
one raw URL.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shlex
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness, CityOSHarnessConfig
from tracefix.runtime.cityos_docker_harness import CityOSDockerApp, load_manifest, manifest_apps

_DEFAULT_SOURCE_URL = "https://smartroom-mirror.vercel.app/api/v1"
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_SMARTROOM_MODELS = ("action-hmdb", "action", "yolo26l", "yolo26n-pose")
_ACTIVITY_LABEL_KEYS = {
    "action",
    "actions",
    "activity",
    "activities",
    "class",
    "classes",
    "class_name",
    "label",
    "labels",
    "name",
    "pose",
    "poses",
    "state",
    "states",
    "verb",
    "verbs",
}
_IGNORED_ACTIVITY_LABELS = {
    "done",
    "ok",
    "true",
    "false",
    "none",
    "null",
    "unknown",
    "completed",
    "active",
    "inactive",
}


def _models_to_fetch(models: dict[str, Any]) -> list[str]:
    done = [
        str(model)
        for model, status in models.items()
        if str(status or "").strip().lower() == "done" and str(model).strip()
    ]
    ordered: list[str] = []
    for model in [*_SMARTROOM_MODELS, *done]:
        if model in done and model not in ordered:
            ordered.append(model)
    return ordered


def _activity_label(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    lowered = text.lower()
    if not text or lowered in _IGNORED_ACTIVITY_LABELS:
        return None
    if len(text) > 80 or text.startswith(("http://", "https://")):
        return None
    if not any(ch.isalpha() for ch in text):
        return None
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _labels_from_leaf(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 5:
        return set()
    if isinstance(value, str):
        label = _activity_label(value)
        return {label} if label else set()
    if isinstance(value, (int, float, bool)) or value is None:
        return set()
    if isinstance(value, list):
        labels: set[str] = set()
        for item in value[:200]:
            labels.update(_labels_from_leaf(item, depth=depth + 1))
        return labels
    if isinstance(value, dict):
        labels: set[str] = set()
        for key in _ACTIVITY_LABEL_KEYS:
            if key in value:
                labels.update(_labels_from_leaf(value[key], depth=depth + 1))
        return labels
    return set()


def _activity_labels_from_value(value: Any, *, parent_key: str = "", depth: int = 0) -> set[str]:
    if depth > 6:
        return set()
    labels: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in _ACTIVITY_LABEL_KEYS or any(token in key_lower for token in ("action", "activity", "label", "pose")):
                labels.update(_labels_from_leaf(child))
            if parent_key in {"trackactions", "track_actions"}:
                labels.update(_labels_from_leaf(child))
            labels.update(_activity_labels_from_value(child, parent_key=key_lower, depth=depth + 1))
    elif isinstance(value, list):
        for item in value[:200]:
            labels.update(_activity_labels_from_value(item, parent_key=parent_key, depth=depth + 1))
    return labels


def _count_named_collections(value: Any, names: tuple[str, ...], *, depth: int = 0) -> int:
    if depth > 6:
        return 0
    total = 0
    if isinstance(value, dict):
        for key, child in value.items():
            key_lower = str(key).lower()
            if any(name in key_lower for name in names):
                if isinstance(child, (list, dict)):
                    total += len(child)
            total += _count_named_collections(child, names, depth=depth + 1)
    elif isinstance(value, list):
        for item in value[:200]:
            total += _count_named_collections(item, names, depth=depth + 1)
    return total


def _add_activity_count(counts: dict[str, int], label: Any, amount: int = 1) -> None:
    normalized = _activity_label(label)
    if not normalized:
        return
    counts[normalized] = counts.get(normalized, 0) + max(int(amount or 0), 0)


def _count_pose_people(value: Any, *, depth: int = 0) -> int:
    if depth > 6:
        return 0
    if isinstance(value, dict):
        best = 0
        for key, child in value.items():
            key_lower = str(key).lower()
            if key_lower in {"person", "persons", "people", "tracks"} or "person" in key_lower:
                if isinstance(child, list):
                    best = max(best, len(child))
                elif isinstance(child, dict):
                    best = max(best, len(child))
            best = max(best, _count_pose_people(child, depth=depth + 1))
        return best
    if isinstance(value, list):
        best = 0
        for item in value[:200]:
            best = max(best, _count_pose_people(item, depth=depth + 1))
        return best
    return 0

def _normalize_track_id(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _track_id_from_mapping(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("id", "track", "track_id", "trackId", "trackID", "person_id", "personId", "personID"):
        if key in value:
            track_id = _normalize_track_id(value.get(key))
            if track_id:
                return track_id
    return ""


def _collect_person_track_ids(value: Any, *, depth: int = 0) -> set[str]:
    if depth > 6:
        return set()
    ids: set[str] = set()
    if isinstance(value, dict):
        direct_id = _track_id_from_mapping(value)
        if direct_id:
            ids.add(direct_id)
        for key, child in value.items():
            key_lower = str(key).lower()
            if key_lower in {"person", "persons", "people", "tracks"} or "person" in key_lower:
                if isinstance(child, list):
                    for item in child[:500]:
                        item_id = _track_id_from_mapping(item)
                        if item_id:
                            ids.add(item_id)
                        elif not isinstance(item, (dict, list)):
                            item_id = _normalize_track_id(item)
                            if item_id:
                                ids.add(item_id)
                elif isinstance(child, dict):
                    for child_key, item in child.items():
                        item_id = _track_id_from_mapping(item) or _normalize_track_id(child_key)
                        if item_id:
                            ids.add(item_id)
            ids.update(_collect_person_track_ids(child, depth=depth + 1))
    elif isinstance(value, list):
        for item in value[:500]:
            ids.update(_collect_person_track_ids(item, depth=depth + 1))
    return ids


def _add_activity_track_id(track_ids: dict[str, set[str]], label: Any, track_id: Any) -> None:
    normalized = _activity_label(label)
    normalized_id = _normalize_track_id(track_id)
    if not normalized or not normalized_id:
        return
    track_ids.setdefault(normalized, set()).add(normalized_id)

def _extract_pose_summary(model: str, inference: dict[str, Any], labels: set[str]) -> dict[str, Any] | None:
    is_pose_model = "pose" in str(model).lower()
    keypoint_sets = _count_named_collections(inference, ("keypoint", "keypoints"))
    segment_sets = _count_named_collections(inference, ("segment", "segments"))
    centroid_sets = _count_named_collections(inference, ("centroid", "centroids"))
    if not is_pose_model and not keypoint_sets and not segment_sets and not centroid_sets:
        return None
    return {
        "model": str(model),
        "labels": sorted(labels),
        "keypointSets": keypoint_sets,
        "segmentSets": segment_sets,
        "centroidSets": centroid_sets,
        "hasKeypoints": keypoint_sets > 0,
    }


_SMARTROOM_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "juen": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _question_text(question_context: Any | None) -> str:
    if question_context is None:
        return ""
    if isinstance(question_context, dict):
        parts: list[str] = []
        for key in ("query", "question", "user_query", "task", "taskText", "task_text"):
            value = question_context.get(key)
            if value:
                parts.append(str(value))
        return " ".join(parts).strip()
    return str(question_context).strip()


def _date_label(month: int, day: int, year: int | None = None) -> str:
    month_name = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ][month - 1]
    return f"{month_name} {day}, {year}" if year else f"{month_name} {day}"


def _requested_date_from_context(question_context: Any | None) -> dict[str, Any] | None:
    text = _question_text(question_context)
    if not text:
        return None
    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        day = int(iso_match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return {"year": year, "month": month, "day": day, "label": _date_label(month, day, year)}

    numeric_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b", text)
    if numeric_match:
        month = int(numeric_match.group(1))
        day = int(numeric_match.group(2))
        year = int(numeric_match.group(3)) if numeric_match.group(3) else None
        if 1 <= month <= 12 and 1 <= day <= 31:
            return {"year": year, "month": month, "day": day, "label": _date_label(month, day, year)}

    month_pattern = "|".join(sorted(_SMARTROOM_MONTHS, key=len, reverse=True))
    named_match = re.search(
        rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        text,
        flags=re.IGNORECASE,
    )
    if named_match:
        month = _SMARTROOM_MONTHS[named_match.group(1).lower().rstrip(".")]
        day = int(named_match.group(2))
        year = int(named_match.group(3)) if named_match.group(3) else None
        if 1 <= day <= 31:
            return {"year": year, "month": month, "day": day, "label": _date_label(month, day, year)}
    return None


def _recording_date(recording: dict[str, Any]) -> dict[str, int] | None:
    candidates = [str(recording.get("day") or ""), str(recording.get("rec") or "")]
    for candidate in candidates:
        iso_match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", candidate)
        if iso_match:
            return {
                "year": int(iso_match.group(1)),
                "month": int(iso_match.group(2)),
                "day": int(iso_match.group(3)),
            }
        compact_match = re.search(r"(20\d{2})(\d{2})(\d{2})", candidate)
        if compact_match:
            return {
                "year": int(compact_match.group(1)),
                "month": int(compact_match.group(2)),
                "day": int(compact_match.group(3)),
            }
    return None


def _recording_matches_requested_date(recording: dict[str, Any], requested: dict[str, Any]) -> bool:
    recording_date = _recording_date(recording)
    if recording_date is None:
        return False
    if recording_date["month"] != requested["month"] or recording_date["day"] != requested["day"]:
        return False
    return requested.get("year") in {None, recording_date["year"]}


def _available_recording_date_labels(recordings: list[Any]) -> list[str]:
    labels: list[str] = []
    for recording in recordings:
        if not isinstance(recording, dict):
            continue
        recording_date = _recording_date(recording)
        if not recording_date:
            continue
        label = _date_label(recording_date["month"], recording_date["day"], recording_date["year"])
        if label not in labels:
            labels.append(label)
    return labels

def _select_smartroom_recording(
    recordings: list[Any],
    question_context: Any | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    typed_recordings = [item for item in recordings if isinstance(item, dict)]
    requested = _requested_date_from_context(question_context)
    if requested:
        for recording in typed_recordings:
            if _recording_matches_requested_date(recording, requested):
                return recording, {
                    "mode": "requested_date",
                    "requestedDate": requested,
                    "requestedDateLabel": requested["label"],
                    "reason": f"matched requested date {requested['label']}",
                }
        available_dates = _available_recording_date_labels(typed_recordings)
        return None, {
            "mode": "requested_date",
            "requestedDate": requested,
            "requestedDateLabel": requested["label"],
            "availableDates": available_dates,
            "reason": f"no recording matched requested date {requested['label']}",
        }
    selected = typed_recordings[0] if typed_recordings else None
    return selected, {
        "mode": "latest",
        "requestedDate": None,
        "requestedDateLabel": None,
        "reason": "selected newest recording because the question did not include a specific date",
    }

def default_web_data_url() -> str:
    return _DEFAULT_SOURCE_URL


def _tracefix_repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def default_web_data_output_root(manifest_path: Path, repo_root: Path | None = None) -> Path:
    root = (repo_root or _tracefix_repo_root()).expanduser().resolve()
    stamp = _utc_now().strftime("%Y%m%d-%H%M%S-%f")
    manifest_name = _safe_name(Path(manifest_path).stem)
    return root / ".tracefix-ui" / "web-data-runs" / f"{manifest_name}-{stamp}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "item"


def _payload_extension(content_type: str, body: bytes) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type in {"application/json", "application/ld+json"}:
        return ".json"
    if media_type.startswith("text/"):
        return ".txt"
    guessed = mimetypes.guess_extension(media_type) if media_type else None
    if guessed:
        return guessed
    stripped = body.lstrip()[:1]
    if stripped in {b"{", b"["}:
        return ".json"
    return ".bin"


def _read_url(
    url: str,
    *,
    timeout_seconds: int,
    max_bytes: int,
    accept: str = "application/json, text/plain, */*",
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={
        "User-Agent": "TraceFix-WebDataHarness/0.2",
        "Accept": accept,
    })
    fetched_at = _utc_now().isoformat()
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local user-provided data source
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"Web data response exceeded {max_bytes} bytes: {url}")
        return {
            "url": url,
            "status": getattr(response, "status", None),
            "reason": getattr(response, "reason", ""),
            "contentType": response.headers.get("content-type", ""),
            "headers": dict(response.headers.items()),
            "body": body,
            "fetchedAt": fetched_at,
        }


def _read_json_url(url: str, *, timeout_seconds: int, max_bytes: int) -> tuple[dict[str, Any], dict[str, Any]]:
    response = _read_url(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        accept="application/json",
    )
    try:
        data = json.loads(response["body"].decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Expected JSON from {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return data, response


def fetch_web_payload(
    source_url: str,
    *,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    url = str(source_url or "").strip()
    if not url:
        raise ValueError("Web data URL is required.")
    payload = _read_url(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
    payload["sourceKind"] = "http"
    return payload



def _looks_like_smartroom_snapshot(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = str(value.get("kind") or "").strip().lower()
    if kind == "smartroom-control.snapshot.v1":
        return True
    if isinstance(value.get("selected"), dict) and (
        "recordingCount" in value or isinstance(value.get("recordings"), list)
    ):
        return True
    return False


def _snapshot_summary_from_smartroom_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    selection = snapshot.get("selection") if isinstance(snapshot.get("selection"), dict) else {}
    selected = snapshot.get("selected") if isinstance(snapshot.get("selected"), dict) else None
    recordings = snapshot.get("recordings") if isinstance(snapshot.get("recordings"), list) else []
    errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else []
    cameras = []
    if selected is not None and isinstance(selected.get("cameras"), dict):
        cameras = list(selected.get("cameras", {}).keys())
    return {
        "recordingCount": snapshot.get("recordingCount", len(recordings)),
        "selectedDay": selected.get("day") if isinstance(selected, dict) else None,
        "selectedRecording": selected.get("rec") if isinstance(selected, dict) else None,
        "cameras": cameras,
        "selectionMode": selection.get("mode"),
        "selectionReason": selection.get("reason"),
        "requestedDate": selection.get("requestedDate"),
        "requestedDateLabel": selection.get("requestedDateLabel"),
        "question": snapshot.get("question"),
        "errors": len(errors),
    }




def _generic_count_targets(question: str) -> list[str]:
    text = question.lower()
    targets: list[str] = []
    if any(term in text for term in ("pedestrian", "pedestrians", "walker", "walkers", "people", "person")):
        targets.extend(["pedestrian", "walker", "person"])
    if any(term in text for term in ("vehicle", "vehicles", "car", "cars", "automobile", "automobiles")):
        targets.extend(["vehicle", "car"])
    if any(term in text for term in ("emergency vehicle", "emergency vehicles", "ambulance", "firetruck", "police")):
        targets.extend(["emergency vehicle", "ambulance", "firetruck", "police"])
    deduped: list[str] = []
    for target in targets:
        if target not in deduped:
            deduped.append(target)
    return deduped


def _generic_entity_matches(entity: dict[str, Any], targets: list[str]) -> bool:
    values = [entity.get("type"), entity.get("name"), entity.get("class"), entity.get("label"), entity.get("category")]
    text = " ".join(str(value).lower() for value in values if value is not None)
    if not text:
        return False
    if "pedestrian" in targets or "walker" in targets or "person" in targets:
        if "pedestrian" in text or "walker" in text or re.search(r"\bperson\b", text):
            return True
    if "vehicle" in targets or "car" in targets:
        if any(term in text for term in ("vehicle", "car", "truck", "bus", "motorcycle", "bike", "bicycle")):
            return True
    if "emergency vehicle" in targets:
        if any(term in text for term in ("ambulance", "firetruck", "fire truck", "police", "emergency")):
            return True
    return any(target in text for target in targets)


def _claim_subject_id(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _generic_presence_claim_matches(claim: dict[str, Any], targets: list[str], entity_ids: set[str]) -> bool:
    polarity = str(claim.get("polarity") or "positive").lower()
    modality = str(claim.get("modality") or "asserted").lower()
    if polarity not in {"positive", "true", "asserted"} or modality in {"negated", "hypothetical"}:
        return False
    claim_type = str(claim.get("claim_type") or claim.get("type") or "").lower()
    predicate = str(claim.get("predicate") or "").lower()
    natural = str(claim.get("natural_language") or claim.get("text") or "").lower()
    subject = _claim_subject_id(claim.get("subject"))
    if claim_type == "object_presence" or predicate == "present":
        if subject and subject in entity_ids:
            return True
        if "present" in natural and any(target in natural for target in targets):
            return True
    return False


def _count_generic_targets(data: Any, question: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    targets = _generic_count_targets(question)
    if not targets:
        return None
    entities = data.get("entities") if isinstance(data.get("entities"), list) else []
    matched_entities = [entity for entity in entities if isinstance(entity, dict) and _generic_entity_matches(entity, targets)]
    entity_ids = {
        str(entity.get("local_id") or entity.get("id") or entity.get("track_id") or entity.get("trackId") or "").strip()
        for entity in matched_entities
    }
    entity_ids.discard("")
    claims = data.get("claims") if isinstance(data.get("claims"), list) else []
    presence_subjects = {
        _claim_subject_id(claim.get("subject"))
        for claim in claims
        if isinstance(claim, dict) and _generic_presence_claim_matches(claim, targets, entity_ids)
    }
    presence_subjects.discard("")
    count = len(presence_subjects) if presence_subjects else len(matched_entities)
    if count <= 0:
        return None
    label = "pedestrians" if any(target in targets for target in ("pedestrian", "walker", "person")) else targets[0] + "s"
    source = str(data.get("source") or "uploaded JSON")
    scene_id = str(data.get("scene_id") or data.get("scenario_id") or data.get("id") or "").strip()
    if scene_id:
        text = f"There are {count} {label} in scene {scene_id}."
    else:
        text = f"There are {count} {label} in the uploaded data."
    method = "positive object-presence claims" if presence_subjects else "matching entities"
    return {
        "question": question or "How many matching objects are present?",
        "text": text,
        "chatAnswer": text,
        "chat_answer": text,
        "answer": text,
        "sourceKind": "raw-json",
        "source": source,
        "sceneId": scene_id or None,
        "count": count,
        "target": label,
        "method": method,
        "entities": [
            {
                "id": entity.get("local_id") or entity.get("id") or entity.get("track_id") or entity.get("trackId"),
                "type": entity.get("type"),
                "name": entity.get("name"),
            }
            for entity in matched_entities
        ],
        "claimSubjects": sorted(presence_subjects),
    }


def _build_generic_raw_json_answer(data: Any, question: str) -> dict[str, Any] | None:
    count_answer = _count_generic_targets(data, question)
    if count_answer is not None:
        return count_answer
    return None

def _build_generic_bundle_answer(
    generic_entries: list[dict[str, Any]],
    *,
    file_count: int,
    question: str,
) -> dict[str, Any] | None:
    if not generic_entries:
        return None
    counts = [entry.get("answer", {}).get("count") for entry in generic_entries if isinstance(entry.get("answer"), dict)]
    numeric_counts = [int(count) for count in counts if isinstance(count, int) or (isinstance(count, str) and count.isdigit())]
    target = "items"
    for entry in generic_entries:
        answer = entry.get("answer") if isinstance(entry.get("answer"), dict) else {}
        if answer.get("target"):
            target = str(answer.get("target"))
            break
    per_file = []
    for entry in generic_entries:
        name = str(entry.get("name") or "JSON file")
        answer = entry.get("answer") if isinstance(entry.get("answer"), dict) else {}
        if answer.get("count") is not None:
            per_file.append(f"{name}: {answer.get('count')} {answer.get('target') or target}")
        elif answer.get("text"):
            per_file.append(f"{name}: {answer.get('text')}")
    if numeric_counts:
        total = sum(numeric_counts)
        text = f"Across {len(generic_entries)} relevant JSON file(s) out of {file_count} selected file(s), I found {total} {target} total."
        if per_file:
            text += " Per file: " + "; ".join(per_file) + "."
    else:
        text = f"I found relevant data in {len(generic_entries)} JSON file(s) out of {file_count} selected file(s)."
        if per_file:
            text += " " + " ".join(per_file)
    return {
        "question": question or "What does the uploaded data show?",
        "text": text,
        "chatAnswer": text,
        "chat_answer": text,
        "answer": text,
        "sourceKind": "raw-json-bundle",
        "count": sum(numeric_counts) if numeric_counts else None,
        "target": target,
        "files": [
            {
                "name": entry.get("name"),
                "answer": entry.get("answer"),
            }
            for entry in generic_entries
        ],
    }
def _looks_like_raw_json_bundle(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    kind = str(value.get("kind") or "").strip().lower()
    return kind in {"tracefix.raw-json-bundle.v1", "tracefix.raw_json_bundle.v1"} and isinstance(value.get("files"), list)


def _raw_json_bundle_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    files = bundle.get("files") if isinstance(bundle.get("files"), list) else []
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if isinstance(item, dict) and "data" in item:
            data = item.get("data")
            name = str(item.get("name") or f"file_{index + 1}.json")
            size = item.get("size")
        else:
            data = item
            name = f"file_{index + 1}.json"
            size = None
        entries.append({"name": name, "size": size, "data": data})
    return entries


def _snapshot_summary_from_raw_json_bundle(
    entries: list[dict[str, Any]],
    smartroom_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    smartroom_names = {str(item.get("name") or "") for item in smartroom_entries}
    files = []
    for entry in entries:
        name = str(entry.get("name") or "")
        data = entry.get("data")
        summary = _snapshot_summary_from_smartroom_snapshot(data) if isinstance(data, dict) and name in smartroom_names else None
        files.append({
            "name": name,
            "size": entry.get("size"),
            "sourceKind": "smartroom-control" if name in smartroom_names else "json",
            "snapshotSummary": summary,
        })
    return {
        "fileCount": len(entries),
        "smartroomSnapshotCount": len(smartroom_entries),
        "files": files,
    }


def _build_smartroom_bundle_answer(
    smartroom_entries: list[dict[str, Any]],
    *,
    file_count: int,
    question: str,
) -> dict[str, Any] | None:
    if not smartroom_entries:
        return None
    answer_parts = []
    cameras: list[dict[str, Any]] = []
    recordings: list[dict[str, Any]] = []
    errors: list[Any] = []
    for entry in smartroom_entries:
        name = str(entry.get("name") or "JSON file")
        answer = entry.get("answer") if isinstance(entry.get("answer"), dict) else {}
        snapshot = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        text = str(answer.get("chatAnswer") or answer.get("chat_answer") or answer.get("text") or "").strip()
        if text:
            answer_parts.append(f"{name}: {text}")
        selected = snapshot.get("selected") if isinstance(snapshot.get("selected"), dict) else None
        recordings.append({
            "file": name,
            "recording": answer.get("recording"),
            "selectedDay": selected.get("day") if isinstance(selected, dict) else None,
            "selectedRecording": selected.get("rec") if isinstance(selected, dict) else None,
        })
        for camera in answer.get("cameras") or []:
            if isinstance(camera, dict):
                enriched = dict(camera)
                enriched["sourceFile"] = name
                cameras.append(enriched)
        for error in answer.get("errors") or []:
            errors.append({"file": name, "error": error})
    if answer_parts:
        text = f"I loaded {len(smartroom_entries)} smartroom JSON file(s) out of {file_count} selected file(s). " + " ".join(answer_parts)
    else:
        text = f"I loaded {len(smartroom_entries)} smartroom JSON file(s) out of {file_count} selected file(s), but none included enough data to summarize."
    return {
        "question": question or "What does the uploaded smartroom data show?",
        "text": text,
        "chatAnswer": text,
        "chat_answer": text,
        "recording": None,
        "recordings": recordings,
        "cameras": cameras,
        "selection": {"bundle": True, "fileCount": file_count, "smartroomSnapshotCount": len(smartroom_entries)},
        "errors": errors,
    }

def fetch_raw_json_payload(
    raw_data_json: str,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
) -> dict[str, Any]:
    raw = str(raw_data_json or "").strip()
    if not raw:
        raise ValueError("Raw data JSON is empty.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Raw data must be valid JSON: {exc}") from exc

    source_kind = "raw-json"
    answer = parsed.get("answer") if isinstance(parsed, dict) and isinstance(parsed.get("answer"), dict) else None
    snapshot_summary = None
    body_value: Any = parsed
    question = _question_text(question_context)

    if isinstance(parsed, dict) and _looks_like_raw_json_bundle(parsed):
        entries = _raw_json_bundle_entries(parsed)
        processed_files: list[dict[str, Any]] = []
        smartroom_entries: list[dict[str, Any]] = []
        generic_entries: list[dict[str, Any]] = []
        for entry in entries:
            processed_entry = dict(entry)
            data = processed_entry.get("data")
            if isinstance(data, dict) and _looks_like_smartroom_snapshot(data):
                snapshot = dict(data)
                if question and not snapshot.get("question"):
                    snapshot["question"] = question
                entry_answer = snapshot.get("answer") if isinstance(snapshot.get("answer"), dict) else None
                if entry_answer is None:
                    entry_answer = build_smartroom_answer(snapshot)
                    snapshot["answer"] = entry_answer
                processed_entry["data"] = snapshot
                processed_entry["answer"] = entry_answer
                smartroom_entries.append(processed_entry)
            elif isinstance(data, dict):
                entry_answer = _build_generic_raw_json_answer(data, question)
                if entry_answer is not None:
                    processed_entry["answer"] = entry_answer
                    generic_entries.append(processed_entry)
            processed_files.append(processed_entry)
        body_value = dict(parsed)
        body_value["kind"] = "tracefix.raw-json-bundle.v1"
        body_value["fileCount"] = len(processed_files)
        body_value["files"] = processed_files
        if answer is None:
            answer = _build_smartroom_bundle_answer(
                smartroom_entries,
                file_count=len(processed_files),
                question=question,
            )
        if answer is None:
            answer = _build_generic_bundle_answer(
                generic_entries,
                file_count=len(processed_files),
                question=question,
            )
        source_kind = "smartroom-control-bundle" if smartroom_entries else "raw-json-bundle"
        snapshot_summary = _snapshot_summary_from_raw_json_bundle(processed_files, smartroom_entries)

    elif isinstance(parsed, dict) and _looks_like_smartroom_snapshot(parsed):
        snapshot = dict(parsed)
        if question and not snapshot.get("question"):
            snapshot["question"] = question
        answer = snapshot.get("answer") if isinstance(snapshot.get("answer"), dict) else None
        if answer is None:
            answer = build_smartroom_answer(snapshot)
            snapshot["answer"] = answer
        source_kind = "smartroom-control"
        snapshot_summary = _snapshot_summary_from_smartroom_snapshot(snapshot)
        body_value = snapshot

    elif isinstance(parsed, dict):
        answer = answer or _build_generic_raw_json_answer(parsed, question)
        if answer is not None:
            snapshot_summary = {
                "sourceKind": "raw-json",
                "answerKind": "generic-count" if answer.get("count") is not None else "generic",
                "sceneId": answer.get("sceneId"),
                "target": answer.get("target"),
                "count": answer.get("count"),
            }

    body = json.dumps(body_value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError(f"Raw data JSON exceeded {max_bytes} bytes.")
    fetched_at = _utc_now().isoformat()
    payload_url = "raw-json://bundle" if "bundle" in source_kind else "raw-json://uploaded"
    return {
        "url": payload_url,
        "status": 200,
        "reason": "OK",
        "contentType": "application/json",
        "headers": {},
        "body": body,
        "fetchedAt": fetched_at,
        "sourceKind": source_kind,
        "answer": answer,
        "snapshotSummary": snapshot_summary,
    }

def _looks_like_smartroom_url(source_url: str) -> bool:
    parsed = urlparse(str(source_url or ""))
    if "/api/v1" in parsed.path:
        return True
    host = (parsed.hostname or "").lower()
    if host in {"smartroom-mirror.vercel.app", "feruzgay.local"}:
        return True
    if host.startswith("smartroom-") or "smartroom" in host:
        return True
    try:
        return parsed.port == 4000
    except ValueError:
        return False


def _smartroom_base_url(source_url: str) -> str:
    url = str(source_url or "").strip() or _DEFAULT_SOURCE_URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid smartroom API URL: {source_url}")
    path = parsed.path.rstrip("/")
    marker = "/api/v1"
    if marker in path:
        path = path[:path.index(marker) + len(marker)]
    else:
        path = marker
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _api_url(base_url: str, *parts: str) -> str:
    quoted = "/".join(quote(str(part).strip("/"), safe="") for part in parts)
    return f"{base_url.rstrip('/')}/{quoted}"


def _frame_time(camera_info: dict[str, Any]) -> float:
    try:
        duration = float(camera_info.get("durationSec") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 1:
        return 0.0
    return min(5.0, max(0.0, duration / 2.0))


def _download_smartroom_frame(
    *,
    base_url: str,
    day: str,
    rec: str,
    camera: str,
    camera_info: dict[str, Any],
    frames_dir: Path,
    timeout_seconds: int,
    max_bytes: int,
) -> dict[str, Any]:
    t = _frame_time(camera_info)
    params = urlencode({"t": f"{t:.3f}", "w": 320, "q": 50, "video": "raw"})
    url = _api_url(base_url, "recordings", day, rec, camera, "frame") + f"?{params}"
    response = _read_url(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        accept="image/jpeg,*/*",
    )
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_name = _safe_name(f"{day}_{rec}_{camera}_t{t:.1f}") + ".jpg"
    frame_path = frames_dir / frame_name
    frame_path.write_bytes(response["body"])
    return {
        "url": url,
        "localPath": str(frame_path),
        "contentType": response.get("contentType") or "image/jpeg",
        "sizeBytes": len(response["body"]),
        "t": t,
        "width": 320,
        "quality": 50,
        "fetchedAt": response.get("fetchedAt"),
    }


def fetch_smartroom_payload(
    source_url: str,
    *,
    output_root: Path,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
) -> dict[str, Any]:
    base_url = _smartroom_base_url(source_url)
    errors: list[dict[str, str]] = []
    recordings_doc, recordings_response = _read_json_url(
        _api_url(base_url, "recordings"),
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    recordings = recordings_doc.get("recordings") if isinstance(recordings_doc.get("recordings"), list) else []
    question = _question_text(question_context)
    selected, selection = _select_smartroom_recording(recordings, question)
    snapshot: dict[str, Any] = {
        "kind": "smartroom-control.snapshot.v1",
        "sourceApi": base_url,
        "fetchedAt": _utc_now().isoformat(),
        "question": question,
        "selection": selection,
        "recordingCount": len(recordings),
        "recordings": recordings,
        "selected": None,
        "errors": errors,
    }

    if selected is not None:
        day = str(selected.get("day") or "")
        rec = str(selected.get("rec") or "")
        selected_payload: dict[str, Any] = {
            "day": day,
            "rec": rec,
            "mtime": selected.get("mtime"),
            "cameras": {},
        }
        cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
        for camera, raw_camera_info in cameras.items():
            camera_name = str(camera)
            camera_info = raw_camera_info if isinstance(raw_camera_info, dict) else {}
            camera_payload: dict[str, Any] = {
                "metadata": camera_info,
                "inference": {},
                "frame": None,
            }
            models = camera_info.get("models") if isinstance(camera_info.get("models"), dict) else {}
            for model in _models_to_fetch(models):
                if str(models.get(model) or "").lower() != "done":
                    continue
                inference_url = _api_url(base_url, "recordings", day, rec, camera_name, "inference", model)
                try:
                    inference, _response = _read_json_url(
                        inference_url,
                        timeout_seconds=timeout_seconds,
                        max_bytes=max_bytes,
                    )
                    camera_payload["inference"][model] = {
                        "url": inference_url,
                        "data": inference,
                    }
                except Exception as exc:  # noqa: BLE001 - keep partial snapshot useful
                    errors.append({
                        "url": inference_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            try:
                camera_payload["frame"] = _download_smartroom_frame(
                    base_url=base_url,
                    day=day,
                    rec=rec,
                    camera=camera_name,
                    camera_info=camera_info,
                    frames_dir=output_root / "source_data" / "frames",
                    timeout_seconds=timeout_seconds,
                    max_bytes=max_bytes,
                )
            except Exception as exc:  # noqa: BLE001 - frames are helpful but not required
                frame_url = _api_url(base_url, "recordings", day, rec, camera_name, "frame")
                errors.append({
                    "url": frame_url,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            selected_payload["cameras"][camera_name] = camera_payload
        snapshot["selected"] = selected_payload

    answer = build_smartroom_answer(snapshot)
    snapshot["answer"] = answer
    body = json.dumps(snapshot, indent=2, default=str).encode("utf-8")
    if len(body) > max_bytes:
        raise ValueError(f"Smartroom snapshot exceeded {max_bytes} bytes.")
    return {
        "url": base_url,
        "status": recordings_response.get("status"),
        "reason": recordings_response.get("reason"),
        "contentType": "application/json",
        "headers": recordings_response.get("headers") or {},
        "body": body,
        "fetchedAt": snapshot["fetchedAt"],
        "sourceKind": "smartroom-control",
        "answer": answer,
        "snapshotSummary": {
            "recordingCount": len(recordings),
            "selectedDay": selected.get("day") if isinstance(selected, dict) else None,
            "selectedRecording": selected.get("rec") if isinstance(selected, dict) else None,
            "cameras": list((snapshot.get("selected") or {}).get("cameras", {}).keys()) if snapshot.get("selected") else [],
            "selectionMode": selection.get("mode"),
            "selectionReason": selection.get("reason"),
            "requestedDate": selection.get("requestedDate"),
            "requestedDateLabel": selection.get("requestedDateLabel"),
            "question": question,
            "errors": len(errors),
        },
    }


def _extract_detection_summary(model: str, inference: dict[str, Any]) -> dict[str, Any]:
    detections = inference.get("detections") if isinstance(inference.get("detections"), dict) else {}
    timeline = detections.get("timeline") if isinstance(detections.get("timeline"), list) else []
    counts = []
    for point in timeline:
        if not isinstance(point, dict):
            continue
        try:
            counts.append(int(point.get("count") or 0))
        except (TypeError, ValueError):
            continue
    actions = detections.get("actions") if isinstance(detections.get("actions"), list) else []
    track_actions = detections.get("trackActions") if isinstance(detections.get("trackActions"), dict) else {}
    activity_labels = set(_activity_labels_from_value(inference))
    activity_labels.update(str(item) for item in actions if str(item).strip())
    activity_labels.update(str(value) for value in track_actions.values() if str(value).strip())
    activity_counts: dict[str, int] = {}
    activity_track_ids: dict[str, set[str]] = {}
    for track_id, value in track_actions.items():
        _add_activity_count(activity_counts, value, 1)
        _add_activity_track_id(activity_track_ids, value, track_id)
    for value in actions:
        normalized = _activity_label(value)
        if normalized and normalized not in activity_counts:
            _add_activity_count(activity_counts, normalized, 1)
    pose_summary = _extract_pose_summary(model, inference, activity_labels)
    pose_people = _count_pose_people(inference)
    pose_track_ids = _collect_person_track_ids(inference)
    if pose_summary and pose_people:
        for label in pose_summary.get("labels") or []:
            activity_counts[label] = max(activity_counts.get(label, 0), pose_people)
            for track_id in pose_track_ids:
                _add_activity_track_id(activity_track_ids, label, track_id)
    return {
        "status": detections.get("status"),
        "durationSec": detections.get("durationSec"),
        "peakPeople": max(counts) if counts else None,
        "lastPeople": counts[-1] if counts else None,
        "samples": len(counts),
        "tracks": detections.get("tracks"),
        "actions": [str(item) for item in actions if str(item).strip()],
        "trackActions": {str(key): str(value) for key, value in track_actions.items()},
        "activityLabels": sorted(label for label in activity_labels if _activity_label(label)),
        "activityCounts": dict(sorted(activity_counts.items())),
        "activityTrackIds": {label: sorted(ids) for label, ids in sorted(activity_track_ids.items())},
        "pose": pose_summary,
        "jumps": detections.get("jumps"),
    }

def _activity_query_labels(question: str, cameras: list[dict[str, Any]]) -> list[str]:
    text = " " + " ".join(str(question or "").lower().replace("_", " ").replace("-", " ").split()) + " "
    labels: set[str] = {
        "standing up",
        "talking",
        "walking",
        "sitting",
        "sit",
        "turn",
        "typing",
        "type on keyboard",
        "clapping",
        "falling down",
    }
    for camera in cameras:
        for label in camera.get("activities") or []:
            normalized = _activity_label(label)
            if normalized:
                labels.add(normalized)
        for label in (camera.get("activityCounts") or {}).keys():
            normalized = _activity_label(label)
            if normalized:
                labels.add(normalized)
    requested: list[str] = []
    for label in sorted(labels, key=len, reverse=True):
        label_text = " ".join(label.lower().split())
        variants = {label_text}
        if label_text.endswith("ing"):
            variants.add(label_text[:-3])
        if label_text == "type on keyboard":
            variants.update({"typing", "type"})
        if any(f" {variant} " in text for variant in variants if variant):
            canonical = label
            if label_text == "typing":
                canonical = "type on keyboard"
            if canonical not in requested:
                requested.append(canonical)
    return requested


def _aggregate_activity_counts(cameras: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for camera in cameras:
        for label, count in (camera.get("activityCounts") or {}).items():
            normalized = _activity_label(label)
            if not normalized:
                continue
            try:
                value = int(count)
            except (TypeError, ValueError):
                value = 0
            totals[normalized] = totals.get(normalized, 0) + max(value, 0)
    return dict(sorted(totals.items()))


def _activity_label_phrase(labels: list[str]) -> str:
    if not labels:
        return "the requested activities"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _question_requests_combined_activities(question: str, requested_activities: list[str]) -> bool:
    if len(requested_activities) < 2:
        return False
    text = " " + " ".join(str(question or "").lower().replace("_", " ").replace("-", " ").split()) + " "
    combined_phrases = (
        " both ",
        " same time ",
        " at the same time ",
        " simultaneously ",
        " together ",
        " at once ",
    )
    return any(phrase in text for phrase in combined_phrases)


def _requested_activity_combination(
    *,
    question: str,
    requested_activities: list[str],
    cameras: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _question_requests_combined_activities(question, requested_activities):
        return None
    labels = requested_activities
    camera_results: list[dict[str, Any]] = []
    exact_total = 0
    for camera in cameras:
        counts = camera.get("activityCounts") if isinstance(camera.get("activityCounts"), dict) else {}
        track_ids = camera.get("activityTrackIds") if isinstance(camera.get("activityTrackIds"), dict) else {}
        if not any(counts.get(label) or track_ids.get(label) for label in labels):
            continue
        label_sets: list[set[str]] = []
        missing: list[str] = []
        for label in labels:
            ids = {_normalize_track_id(value) for value in (track_ids.get(label) or []) if _normalize_track_id(value)}
            if not ids:
                missing.append(label)
            label_sets.append(ids)
        if missing:
            camera_results.append({
                "camera": camera.get("camera"),
                "exact": False,
                "count": None,
                "trackIds": [],
                "missingTrackIdsFor": missing,
            })
            continue
        intersection = set.intersection(*label_sets) if label_sets else set()
        exact_total += len(intersection)
        camera_results.append({
            "camera": camera.get("camera"),
            "exact": True,
            "count": len(intersection),
            "trackIds": sorted(intersection),
            "missingTrackIdsFor": [],
        })
    exact = bool(camera_results) and all(item.get("exact") for item in camera_results)
    return {
        "labels": labels,
        "exact": exact,
        "count": exact_total if exact else None,
        "knownCount": exact_total,
        "byCamera": camera_results,
        "method": "track_id_intersection" if exact else "track_id_intersection_unavailable",
    }


def _build_requested_activity_answer(
    *,
    requested_activities: list[str],
    activity_counts: dict[str, int],
    cameras: list[dict[str, Any]],
    requested_label: str,
    day: str,
    rec: str,
    activity_combination: dict[str, Any] | None = None,
) -> str:
    if not requested_activities:
        return ""
    date_label = requested_label or "the selected recording"
    recording = " / ".join(part for part in [day, rec] if part)
    prefix = f"For {date_label} ({recording}), " if recording else f"For {date_label}, "
    if activity_combination:
        labels = [str(label) for label in activity_combination.get("labels") or requested_activities]
        labels_phrase = _activity_label_phrase(labels)
        if activity_combination.get("exact"):
            count = activity_combination.get("count") or 0
            camera_parts = []
            for item in activity_combination.get("byCamera") or []:
                if item.get("exact"):
                    camera_parts.append(
                        f"{_format_smartroom_camera_label(item.get('camera'))}: {_person_count_label(item.get('count') or 0)}"
                    )
            suffix = f" Camera breakdown: {'; '.join(camera_parts)}." if camera_parts else ""
            note = " This uses matching track/person IDs across the activity and pose endpoints."
            return prefix + f"{_person_count_label(count)} were both {labels_phrase}." + suffix + note
        pieces = [f"{label}: {_person_count_label(activity_counts.get(label, 0))}" for label in labels]
        note = " The API data did not include matching track/person IDs for every requested label, so I cannot confirm the exact overlap."
        return prefix + "I found " + ", ".join(pieces) + f", but not the exact number of people who were both {labels_phrase}." + note

    pieces = []
    for label in requested_activities:
        pieces.append(f"{label}: {_person_count_label(activity_counts.get(label, 0))}")
    camera_parts = []
    for camera in cameras:
        counts = camera.get("activityCounts") or {}
        details = [f"{label} {counts.get(label, 0)}" for label in requested_activities if counts.get(label, 0)]
        if details:
            camera_parts.append(f"{_format_smartroom_camera_label(camera.get('camera'))}: {', '.join(details)}")
    suffix = f" Camera breakdown: {'; '.join(camera_parts)}." if camera_parts else ""
    note = " Counts are per detected activity label/track; ask for 'both' to compute overlap when matching track IDs are available."
    return prefix + ", ".join(pieces) + "." + suffix + note

def build_smartroom_answer(snapshot: dict[str, Any]) -> dict[str, Any]:
    question = _question_text(snapshot.get("question")) or "What does the smartroom API data show?"
    selection = snapshot.get("selection") if isinstance(snapshot.get("selection"), dict) else {}
    requested_label = str(selection.get("requestedDateLabel") or "").strip()
    selected = snapshot.get("selected") if isinstance(snapshot.get("selected"), dict) else None
    if selected is None:
        available_dates = selection.get("availableDates") if isinstance(selection.get("availableDates"), list) else []
        text = (
            f"No smartroom recording matched {requested_label}."
            if requested_label
            else "No smartroom recordings were available from the API."
        )
        if requested_label and available_dates:
            text += " Available recording dates include " + ", ".join(str(item) for item in available_dates[:8]) + "."
        return {
            "question": question,
            "text": text,
            "chatAnswer": text,
            "chat_answer": text,
            "recording": None,
            "cameras": [],
            "selection": selection,
            "errors": snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else [],
        }

    day = str(selected.get("day") or "unknown day")
    rec = str(selected.get("rec") or "unknown recording")
    camera_answers: list[dict[str, Any]] = []
    if requested_label:
        line_parts = [f"Recording {rec} from {day} matched requested date {requested_label}."]
    else:
        line_parts = [f"Latest recording {rec} from {day}."]
    cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
    for camera_name, raw_camera in cameras.items():
        camera = raw_camera if isinstance(raw_camera, dict) else {}
        metadata = camera.get("metadata") if isinstance(camera.get("metadata"), dict) else {}
        inference = camera.get("inference") if isinstance(camera.get("inference"), dict) else {}
        model_summaries: dict[str, Any] = {}
        endpoints: dict[str, str] = {}
        action_labels: set[str] = set()
        activity_labels: set[str] = set()
        activity_events: list[dict[str, str]] = []
        activity_counts: dict[str, int] = {}
        activity_track_ids: dict[str, set[str]] = {}
        pose_summaries: list[dict[str, Any]] = []
        peak_people: int | None = None
        last_people: int | None = None
        track_count: int | None = None
        for model, wrapper in inference.items():
            model_name = str(model)
            data = wrapper.get("data") if isinstance(wrapper, dict) and isinstance(wrapper.get("data"), dict) else {}
            if isinstance(wrapper, dict) and wrapper.get("url"):
                endpoints[model_name] = str(wrapper.get("url"))
            summary = _extract_detection_summary(model_name, data)
            model_summaries[model_name] = summary
            if summary["peakPeople"] is not None:
                peak_people = max(peak_people or 0, int(summary["peakPeople"]))
            if summary["lastPeople"] is not None:
                last_people = int(summary["lastPeople"])
            if summary["tracks"] is not None:
                try:
                    track_count = max(track_count or 0, int(summary["tracks"]))
                except (TypeError, ValueError):
                    pass
            model_action_labels = set(summary["actions"])
            model_action_labels.update(value for value in summary["trackActions"].values() if value)
            model_activity_labels = set(summary.get("activityLabels") or [])
            action_labels.update(model_action_labels)
            activity_labels.update(model_activity_labels)
            activity_labels.update(model_action_labels)
            for label in sorted(model_activity_labels | model_action_labels):
                activity_events.append({"model": model_name, "label": label})
            for label, count in (summary.get("activityCounts") or {}).items():
                normalized = _activity_label(label)
                if not normalized:
                    continue
                try:
                    count_int = int(count)
                except (TypeError, ValueError):
                    count_int = 0
                activity_counts[normalized] = max(activity_counts.get(normalized, 0), count_int)
            for label, ids in (summary.get("activityTrackIds") or {}).items():
                normalized = _activity_label(label)
                if not normalized:
                    continue
                for track_id in ids:
                    _add_activity_track_id(activity_track_ids, normalized, track_id)
            if summary.get("pose"):
                pose_summaries.append(summary["pose"])
        frame = camera.get("frame") if isinstance(camera.get("frame"), dict) else None
        camera_answer = {
            "camera": str(camera_name),
            "node": metadata.get("node"),
            "durationSec": metadata.get("durationSec"),
            "peakPeople": peak_people,
            "lastPeople": last_people,
            "trackCount": track_count,
            "actions": sorted(action_labels),
            "activities": sorted(activity_labels),
            "activityEvents": activity_events,
            "activityCounts": dict(sorted(activity_counts.items())),
            "activityTrackIds": {label: sorted(ids) for label, ids in sorted(activity_track_ids.items())},
            "pose": {"models": pose_summaries, "available": bool(pose_summaries)},
            "endpoints": endpoints,
            "models": model_summaries,
            "framePath": frame.get("localPath") if frame else None,
        }
        camera_answers.append(camera_answer)
        facts = [str(camera_name)]
        if peak_people is not None:
            facts.append(f"peak occupancy {peak_people}")
        if last_people is not None:
            facts.append(f"last observed occupancy {last_people}")
        if activity_labels:
            facts.append("activities " + ", ".join(sorted(activity_labels)))
        elif action_labels:
            facts.append("actions " + ", ".join(sorted(action_labels)))
        if track_count is not None:
            facts.append(f"tracks {track_count}")
        if frame and frame.get("localPath"):
            facts.append(f"sample frame {frame['localPath']}")
        line_parts.append("; ".join(facts) + ".")

    if not camera_answers:
        line_parts.append("No cameras were listed for the selected recording.")
    errors = snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else []
    if errors:
        line_parts.append(f"Partial data: {len(errors)} API request(s) failed; see snapshot errors.")

    activity_counts = _aggregate_activity_counts(camera_answers)
    requested_activities = _activity_query_labels(question, camera_answers)
    requested_activity_combination = _requested_activity_combination(
        question=question,
        requested_activities=requested_activities,
        cameras=camera_answers,
    )
    requested_activity_answer = _build_requested_activity_answer(
        requested_activities=requested_activities,
        activity_counts=activity_counts,
        cameras=camera_answers,
        requested_label=requested_label,
        day=day,
        rec=rec,
        activity_combination=requested_activity_combination,
    )
    if requested_activity_answer:
        line_parts.append(requested_activity_answer)
    chat_answer = requested_activity_answer or _build_smartroom_chat_answer(
        day=day,
        rec=rec,
        cameras=camera_answers,
        errors=errors,
        requested_label=requested_label,
    )
    return {
        "question": question,
        "text": " ".join(line_parts),
        "chatAnswer": chat_answer,
        "chat_answer": chat_answer,
        "recording": {"day": day, "rec": rec},
        "selection": selection,
        "activityCounts": activity_counts,
        "requestedActivities": requested_activities,
        "requestedActivityCounts": {label: activity_counts.get(label, 0) for label in requested_activities},
        "requestedActivityCombination": requested_activity_combination,
        "requestedActivityAnswer": requested_activity_answer,
        "cameras": camera_answers,
        "errors": errors,
    }

def _build_smartroom_chat_answer(
    *,
    day: str | None,
    rec: str | None,
    cameras: list[dict[str, Any]],
    errors: list[Any],
    requested_label: str = "",
) -> str:
    if not cameras:
        return "I could not find any camera results in the latest smartroom recording yet."
    camera_parts: list[str] = []
    latest_parts: list[str] = []
    activity_parts: list[str] = []
    peak_values: list[int] = []
    for camera in cameras:
        name = _format_smartroom_camera_label(camera.get("camera") or "camera")
        peak = camera.get("peakPeople")
        latest = camera.get("lastPeople")
        if peak is not None:
            try:
                peak_int = int(peak)
            except (TypeError, ValueError):
                peak_int = None
            if peak_int is not None:
                peak_values.append(peak_int)
                camera_parts.append(f"{name} peaked at {_person_count_label(peak_int)}")
        if latest is not None:
            latest_parts.append(f"{name} most recently showed {_person_count_label(latest)}")
        activities = camera.get("activities") or camera.get("actions") or []
        if isinstance(activities, list) and activities:
            activity_parts.append(f"{name}: {', '.join(str(item) for item in activities[:8])}")
    if not camera_parts:
        return "I found the latest smartroom recording, but it did not include enough occupancy data to summarize yet."

    recording = " / ".join(part for part in [day, rec] if part)
    if requested_label:
        prefix = f"For {requested_label} ({recording}), " if recording else f"For {requested_label}, "
    else:
        prefix = f"For the latest recording ({recording}), " if recording else "For the latest recording, "
    overall_peak = max(peak_values) if peak_values else None
    summary = prefix + ", and ".join(camera_parts) + "."
    if overall_peak is not None:
        summary += f" Across the observed time window, the peak occupancy was {_person_count_label(overall_peak)} overall."
    if latest_parts:
        summary += " At the latest observed moment, " + ", and ".join(latest_parts) + "."
    if activity_parts:
        summary += " Detected activities/poses included " + "; ".join(activity_parts) + "."
    if errors:
        summary += f" This answer is based on partial data because {len(errors)} API request(s) failed."
    return summary


def _person_count_label(value: Any) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return f"{value} people"
    noun = "person" if count == 1 else "people"
    return f"{count} {noun}"


def _format_smartroom_camera_label(value: Any) -> str:
    name = str(value or "camera")
    lowered = name.lower()
    suffix = name[3:]
    if lowered.startswith("cam") and suffix.isdigit():
        return f"cam {suffix}"
    return name


def _answer_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    payload_answer = payload.get("answer") if isinstance(payload.get("answer"), dict) else None
    if payload_answer is not None:
        return payload_answer
    if payload.get("sourceKind") != "smartroom-control":
        return None
    body = payload.get("body") or b""
    if not isinstance(body, bytes):
        return None
    try:
        snapshot = json.loads(body.decode("utf-8-sig"))
    except json.JSONDecodeError:
        return None
    if not isinstance(snapshot, dict):
        return None
    answer = snapshot.get("answer") if isinstance(snapshot.get("answer"), dict) else None
    return answer or build_smartroom_answer(snapshot)


def _write_answer_artifacts(output_root: Path, runs: list[dict[str, Any]], answer: dict[str, Any] | None) -> str | None:
    if not answer:
        return None
    answer_path = output_root / "smartroom-answer.json"
    answer_path.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        if run.get("status") != "completed" or not run.get("outputDir"):
            continue
        app_answer_path = Path(str(run["outputDir"])) / "smartroom-answer.json"
        app_answer_path.write_text(json.dumps({
            "app": run.get("app"),
            "answer": answer,
        }, indent=2) + "\n", encoding="utf-8")
        run["answerPath"] = str(app_answer_path)
    return str(answer_path)

def fetch_source_payload(
    source_url: str,
    *,
    output_root: Path,
    source_mode: str = "auto",
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
    raw_data_json: str | None = None,
) -> dict[str, Any]:
    mode = str(source_mode or "auto").strip().lower()
    raw_text = str(raw_data_json or "").strip()
    if raw_text or mode in {"raw-json", "raw_json", "pasted-json", "pasted_json", "json"}:
        return fetch_raw_json_payload(
            raw_text,
            max_bytes=max_bytes,
            question_context=question_context,
        )
    if mode in {"smartroom", "smartroom-control", "smartroom_control"} or (
        mode == "auto" and _looks_like_smartroom_url(source_url)
    ):
        return fetch_smartroom_payload(
            source_url,
            output_root=output_root,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            question_context=question_context,
        )
    return fetch_web_payload(
        source_url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )


def write_web_payload(payload: dict[str, Any], output_root: Path) -> dict[str, Any]:
    source_dir = output_root / "source_data"
    source_dir.mkdir(parents=True, exist_ok=True)
    body = payload.get("body") or b""
    if not isinstance(body, bytes):
        raise TypeError("Fetched web payload body must be bytes.")
    stamp = _utc_now().strftime("%Y%m%d-%H%M%S-%f")
    extension = _payload_extension(str(payload.get("contentType") or ""), body)
    payload_path = source_dir / f"{stamp}_web_payload{extension}"
    metadata_path = source_dir / f"{stamp}_web_payload_metadata.json"
    payload_path.write_bytes(body)
    metadata = {
        "url": payload.get("url"),
        "status": payload.get("status"),
        "reason": payload.get("reason"),
        "contentType": payload.get("contentType"),
        "headers": payload.get("headers") or {},
        "sourceKind": payload.get("sourceKind") or "http",
        "snapshotSummary": payload.get("snapshotSummary"),
        "answer": payload.get("answer"),
        "payloadPath": str(payload_path),
        "sizeBytes": len(body),
        "fetchedAt": payload.get("fetchedAt"),
        "writtenAt": _utc_now().isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata["metadataPath"] = str(metadata_path)
    return metadata


def _resolve_app_path(app: CityOSDockerApp, manifest_path: Path) -> Path:
    path = app.path.expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _handler_command(command: str | list[str] | None) -> list[str]:
    if command is None:
        return []
    if isinstance(command, list):
        return [str(item) for item in command if str(item).strip()]
    raw = str(command).strip()
    return shlex.split(raw) if raw else []


async def _run_app_against_payload(
    *,
    app: CityOSDockerApp,
    manifest_path: Path,
    payload_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
) -> dict[str, Any]:
    app_path = _resolve_app_path(app, manifest_path)
    bundle_dir = app_path / "tracefix_bundle"
    output_dir = output_root / "apps" / app.name
    frames_dir = output_dir / "frames"
    try:
        if not bundle_dir.is_dir():
            raise FileNotFoundError(f"Generated app bundle does not exist: {bundle_dir}")
        config = CityOSHarnessConfig(
            app_kind=app.kind or "app",
            agent_id=app.agent or app.name,
            bundle_dir=bundle_dir,
            runtime_mode="web_data",
            autorun=False,
            output_dir=output_dir,
            ready_dir=output_root / "ready",
            startup_cmd=[],
            handler_cmd=_handler_command(handler_command),
            handler_timeout=float(handler_timeout_seconds),
            verbose=False,
            task_id="",
        )
        harness = CityOSAgentHarness(config)
        existing_records = set()
        if frames_dir.exists():
            existing_records = {path.resolve() for path in frames_dir.glob("*.json")}
        ready_path = await harness.write_readiness()
        await harness.receive_frame("web_data", payload_path, _utc_now())
        frame_records = [
            str(path)
            for path in sorted(frames_dir.glob("*.json"))
            if path.resolve() not in existing_records and not path.name.endswith("_handler.json")
        ]
        handler_records = [
            str(path)
            for path in sorted(frames_dir.glob("*_handler.json"))
            if path.resolve() not in existing_records
        ]
        return {
            "app": {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app_path),
            },
            "status": "completed",
            "outputDir": str(output_dir),
            "readyPath": str(ready_path),
            "framesDir": str(frames_dir),
            "frameRecords": frame_records,
            "handlerRecords": handler_records,
            "handlerConfigured": bool(config.handler_cmd),
        }
    except Exception as exc:  # noqa: BLE001 - result JSON should report each app failure
        return {
            "app": {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app_path),
            },
            "status": "failed",
            "outputDir": str(output_dir),
            "framesDir": str(frames_dir),
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _run_apps(
    *,
    apps: list[CityOSDockerApp],
    manifest_path: Path,
    payload_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
) -> list[dict[str, Any]]:
    return await asyncio.gather(*[
        _run_app_against_payload(
            app=app,
            manifest_path=manifest_path,
            payload_path=payload_path,
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
        )
        for app in apps
    ])


def run_web_data_apps(
    *,
    manifest_path: Path,
    source_url: str = _DEFAULT_SOURCE_URL,
    output_root: Path | None = None,
    source_mode: str = "auto",
    timeout_seconds: int = 30,
    handler_command: str | list[str] | None = None,
    handler_timeout_seconds: int = 60,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
    raw_data_json: str | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Synthesis manifest does not exist: {manifest_path}")
    manifest = load_manifest(manifest_path)
    apps = manifest_apps(manifest)
    if not apps:
        raise ValueError(f"No apps found in synthesis manifest: {manifest_path}")
    if output_root is None:
        output_root = default_web_data_output_root(manifest_path)
    else:
        output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    started_at = _utc_now().isoformat()
    payload = fetch_source_payload(
        source_url,
        output_root=output_root,
        source_mode=source_mode,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        question_context=question_context,
        raw_data_json=raw_data_json,
    )
    payload_metadata = write_web_payload(payload, output_root)
    payload_path = Path(str(payload_metadata["payloadPath"]))
    runs = asyncio.run(_run_apps(
        apps=apps,
        manifest_path=manifest_path,
        payload_path=payload_path,
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
    ))
    answer = _answer_from_payload(payload)
    answer_path = _write_answer_artifacts(output_root, runs, answer)
    finished_at = _utc_now().isoformat()
    result = {
        "ok": bool(runs) and all(run.get("status") == "completed" for run in runs),
        "sourceUrl": payload.get("url") or source_url,
        "sourceKind": payload.get("sourceKind") or "http",
        "sourceMode": source_mode,
        "question": _question_text(question_context),
        "manifestPath": str(manifest_path),
        "outputRoot": str(output_root),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "payload": payload_metadata,
        "answer": answer,
        "answerPath": answer_path,
        "runs": runs,
    }
    result_path = output_root / "web-data-run.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["resultPath"] = str(result_path)
    return result