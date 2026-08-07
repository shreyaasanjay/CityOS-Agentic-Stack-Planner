"""Host-side runner for synthesized TraceFix apps against a web data source.

This keeps the CityOS synthesis path intact while letting the same generated
app bundles consume data from a normal HTTP server when CityOS is not the
runtime environment. The host runner only transports request/result envelopes:
generated retrieval agents access the source, a generated answer agent creates
the response, and generated monitors validate the response contract.
"""

from __future__ import annotations

import asyncio
import json
import sys
import mimetypes
import re
import shlex
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness, CityOSHarnessConfig
from tracefix.runtime.cityos_docker_harness import CityOSDockerApp, load_manifest, manifest_apps

_DEFAULT_SOURCE_URL = "http://172.16.60.239:3000/api/v1"
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


def _requested_time_from_context(question_context: Any | None) -> dict[str, Any] | None:
    requested_date = _requested_date_from_context(question_context)
    text = _question_text(question_context)
    if not requested_date or not text:
        return None
    iso_match = re.search(r"\b20\d{2}-\d{1,2}-\d{1,2}[T\s]+(\d{1,2}):(\d{2})(?::\d{2})?", text)
    meridiem_match = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\b", text, flags=re.IGNORECASE)
    clock_match = re.search(r"\bat\s+([01]?\d|2[0-3]):([0-5]\d)\b", text, flags=re.IGNORECASE)
    hour = minute = None
    if iso_match:
        hour, minute = int(iso_match.group(1)), int(iso_match.group(2))
    elif meridiem_match:
        hour = int(meridiem_match.group(1)) % 12
        minute = int(meridiem_match.group(2) or 0)
        if meridiem_match.group(3).lower() == "p":
            hour += 12
    elif clock_match:
        hour, minute = int(clock_match.group(1)), int(clock_match.group(2))
    if hour is None or minute is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return {
        **requested_date,
        "hour": hour,
        "minute": minute,
        "seconds": hour * 3600 + minute * 60,
        "timeLabel": f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}",
    }


def _recording_start_time(recording: dict[str, Any]) -> datetime | None:
    rec = str(recording.get("rec") or "")
    match = re.search(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", rec)
    if match:
        try:
            return datetime(*(int(value) for value in match.groups()))
        except ValueError:
            return None
    mtime = recording.get("mtime")
    try:
        value = float(mtime)
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return None


def _recording_time_label(recording: dict[str, Any]) -> str:
    start = _recording_start_time(recording)
    return f"{start.hour % 12 or 12}:{start.minute:02d} {'AM' if start.hour < 12 else 'PM'}" if start else ""


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

def _recording_option(recording: dict[str, Any]) -> dict[str, Any]:
    cameras = recording.get("cameras") if isinstance(recording.get("cameras"), dict) else {}
    camera_names = [str(name) for name in cameras.keys()]
    durations: list[float] = []
    models: set[str] = set()
    nodes: set[str] = set()
    for raw_camera in cameras.values():
        camera = raw_camera if isinstance(raw_camera, dict) else {}
        if camera.get("node"):
            nodes.add(str(camera.get("node")))
        try:
            duration = float(camera.get("durationSec") or 0)
        except (TypeError, ValueError):
            duration = 0
        if duration > 0:
            durations.append(duration)
        camera_models = camera.get("models") if isinstance(camera.get("models"), dict) else {}
        for model, status in camera_models.items():
            if str(status or "").strip().lower() == "done":
                models.add(str(model))
    day = str(recording.get("day") or "")
    rec = str(recording.get("rec") or "")
    recording_date = _recording_date(recording)
    date_label = (
        _date_label(recording_date["month"], recording_date["day"], recording_date["year"])
        if recording_date
        else ""
    )
    details = []
    if date_label:
        details.append(date_label)
    if camera_names:
        details.append(", ".join(camera_names))
    if durations:
        details.append(f"{max(durations):.0f}s")
    return {
        "recordingId": "/".join(part for part in [day, rec] if part),
        "day": day,
        "rec": rec,
        "label": " / ".join(part for part in [day, rec] if part) or rec or day or "recording",
        "detail": " - ".join(details),
        "dateLabel": date_label,
        "timeLabel": _recording_time_label(recording),
        "cameras": camera_names,
        "cameraCount": len(camera_names),
        "durationSec": max(durations) if durations else None,
        "models": sorted(models),
        "nodes": sorted(nodes),
        "mtime": recording.get("mtime"),
    }


def _recording_options(recordings: list[dict[str, Any]], limit: int = 100) -> list[dict[str, Any]]:
    return [_recording_option(recording) for recording in recordings[:limit]]


def _normalize_recording_override(value: Any | None) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        identifier = str(value.get("recordingId") or value.get("id") or "").strip()
        day = str(value.get("day") or "").strip()
        rec = str(value.get("rec") or value.get("recording") or "").strip()
        if identifier and (not day or not rec):
            identifier_parts = [part.strip() for part in identifier.split("/") if part.strip()]
            if len(identifier_parts) >= 2:
                day, rec = identifier_parts[-2:]
        return {"day": day, "rec": rec} if day or rec else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return _normalize_recording_override(parsed)
    parts = [part.strip() for part in re.split(r"\s*/\s*|\s*,\s*", text) if part.strip()]
    if len(parts) >= 2:
        return {"day": parts[0], "rec": parts[1]}
    if text.startswith("rec_"):
        return {"day": "", "rec": text}
    if text.startswith("day_"):
        return {"day": text, "rec": ""}
    return {"day": "", "rec": text}


def _resolve_recording_selection(
    recordings: list[dict[str, Any]],
    question_context: Any | None,
    recording_override: Any | None,
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve a typed take number/name to the stable day/recording pair."""
    if recording_override is None:
        return None, None
    if isinstance(recording_override, dict):
        return _normalize_recording_override(recording_override), None
    text = str(recording_override or "").strip()
    if not text:
        return None, None
    requested = _requested_date_from_context(question_context)
    candidates = recordings
    if requested:
        dated = [recording for recording in recordings if _recording_matches_requested_date(recording, requested)]
        if dated:
            candidates = dated
    take_match = re.fullmatch(r"(?:take\s*)?(\d+)", text, flags=re.IGNORECASE)
    if take_match:
        take_index = int(take_match.group(1)) - 1
        if 0 <= take_index < len(candidates):
            return _normalize_recording_override(_recording_option(candidates[take_index])), None
        return None, f"Take {take_index + 1} is not available."
    lowered = text.casefold()
    for recording in candidates:
        option = _recording_option(recording)
        values = (option.get("recordingId"), option.get("rec"), option.get("label"))
        if any(str(value or "").casefold() == lowered for value in values):
            return _normalize_recording_override(option), None
    return _normalize_recording_override(text), None


def _is_cumulative_occupancy_question(question_context: Any | None) -> bool:
    text = _question_text(question_context).casefold()
    return bool(re.search(r"\b(total|cumulative|across all (?:takes|recordings)|all takes|all recordings)\b", text)) and bool(
        re.search(r"\b(people|person|occupancy|occupied|room)\b", text)
    )


def _recording_matches_override(recording: dict[str, Any], override: dict[str, str]) -> bool:
    day = str(recording.get("day") or "")
    rec = str(recording.get("rec") or "")
    override_day = str(override.get("day") or "")
    override_rec = str(override.get("rec") or "")
    if override_day and day != override_day:
        return False
    if override_rec and rec != override_rec:
        return False
    return bool(override_day or override_rec)

def _select_smartroom_recording(
    recordings: list[Any],
    question_context: Any | None,
    recording_override: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    typed_recordings = [item for item in recordings if isinstance(item, dict)]
    override, selection_error = _resolve_recording_selection(typed_recordings, question_context, recording_override)
    if selection_error:
        requested = _requested_date_from_context(question_context)
        candidates = typed_recordings
        if requested:
            dated = [recording for recording in typed_recordings if _recording_matches_requested_date(recording, requested)]
            if dated:
                candidates = dated
        return None, {
            "mode": "needs_clarification",
            "requestedDate": requested,
            "requestedDateLabel": requested.get("label") if requested else None,
            "needsClarification": True,
            "clarificationPrompt": f"{selection_error} Choose one of the listed takes.",
            "candidates": _recording_options(candidates),
            "reason": "invalid recording selection",
        }
    if override:
        matches = [recording for recording in typed_recordings if _recording_matches_override(recording, override)]
        if len(matches) == 1:
            selected = matches[0]
            option = _recording_option(selected)
            return selected, {
                "mode": "recording_override",
                "requestedDate": _recording_date(selected),
                "requestedDateLabel": option.get("dateLabel"),
                "recordingOverride": override,
                "reason": f"selected requested recording {selected.get('rec')}",
            }
        return None, {
            "mode": "recording_override",
            "requestedDate": None,
            "requestedDateLabel": None,
            "recordingOverride": override,
            "needsClarification": True,
            "clarificationPrompt": "I could not find that exact recording. Choose one of the available recordings.",
            "candidates": _recording_options(typed_recordings),
            "reason": "recording override did not match exactly",
        }
    requested = _requested_date_from_context(question_context)
    if requested:
        matches = [recording for recording in typed_recordings if _recording_matches_requested_date(recording, requested)]
        requested_time = _requested_time_from_context(question_context)
        if matches and requested_time:
            requested_start = datetime(
                int(requested_time["year"] or datetime.now().year),
                int(requested_time["month"]),
                int(requested_time["day"]),
                int(requested_time["hour"]),
                int(requested_time["minute"]),
            )
            timed = [(recording, _recording_start_time(recording)) for recording in matches]
            timed = [(recording, start) for recording, start in timed if start is not None]
            if timed:
                containing: list[tuple[dict[str, Any], datetime]] = []
                for recording, start in timed:
                    durations = [
                        float(camera.get("durationSec") or 0)
                        for camera in (recording.get("cameras") or {}).values()
                        if isinstance(camera, dict)
                    ]
                    duration = max(durations, default=0)
                    if duration > 0 and start <= requested_start <= start + timedelta(seconds=duration):
                        containing.append((recording, start))
                pool = containing or timed
                selected, selected_start = min(pool, key=lambda item: abs((item[1] - requested_start).total_seconds()))
                option = _recording_option(selected)
                return selected, {
                    "mode": "requested_timestamp",
                    "requestedDate": requested,
                    "requestedDateLabel": requested["label"],
                    "requestedTimeLabel": requested_time["timeLabel"],
                    "recordingOverride": _normalize_recording_override(option),
                    "reason": f"selected recording closest to requested time {requested_time['timeLabel']}",
                }
        if matches and _is_cumulative_occupancy_question(question_context):
            return None, {
                "mode": "requested_date_total",
                "requestedDate": requested,
                "requestedDateLabel": requested["label"],
                "aggregateRecordings": matches,
                "reason": f"aggregate occupancy requested across {len(matches)} recordings for {requested['label']}",
            }
        if matches:
            count = len(matches)
            noun = "recording" if count == 1 else "recordings"
            return None, {
                "mode": "needs_clarification",
                "requestedDate": requested,
                "requestedDateLabel": requested["label"],
                "needsClarification": True,
                "clarificationPrompt": f"I found {count} smartroom {noun} for {requested['label']}. Which take should I use?",
                "candidates": _recording_options(matches),
                "reason": f"recording selection required for requested date {requested['label']}",
            }
        available_dates = _available_recording_date_labels(typed_recordings)
        return None, {
            "mode": "requested_date",
            "requestedDate": requested,
            "requestedDateLabel": requested["label"],
            "availableDates": available_dates,
            "reason": f"no recording matched requested date {requested['label']}",
        }
    if len(typed_recordings) > 1:
        return None, {
            "mode": "needs_clarification",
            "requestedDate": None,
            "requestedDateLabel": None,
            "needsClarification": True,
            "clarificationPrompt": "I found multiple smartroom recordings. Which date/take should I use?",
            "candidates": _recording_options(typed_recordings),
            "reason": "the question did not identify a specific recording",
        }
    selected = typed_recordings[0] if typed_recordings else None
    return selected, {
        "mode": "latest",
        "requestedDate": None,
        "requestedDateLabel": None,
        "reason": "selected newest recording because it was the only available recording",
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


def _fetch_smartroom_recording_payload(
    selected: dict[str, Any], *, base_url: str, output_root: Path, timeout_seconds: int, max_bytes: int, errors: list[dict[str, str]],
) -> dict[str, Any]:
    day = str(selected.get("day") or "")
    rec = str(selected.get("rec") or "")
    selected_payload: dict[str, Any] = {"day": day, "rec": rec, "mtime": selected.get("mtime"), "cameras": {}}
    cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
    for camera, raw_camera_info in cameras.items():
        camera_name = str(camera)
        camera_info = raw_camera_info if isinstance(raw_camera_info, dict) else {}
        camera_payload: dict[str, Any] = {"metadata": camera_info, "inference": {}, "frame": None}
        models = camera_info.get("models") if isinstance(camera_info.get("models"), dict) else {}
        for model in _models_to_fetch(models):
            if str(models.get(model) or "").lower() != "done":
                continue
            inference_url = _api_url(base_url, "recordings", day, rec, camera_name, "inference", model)
            try:
                inference, _response = _read_json_url(inference_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
                camera_payload["inference"][model] = {"url": inference_url, "data": inference}
            except Exception as exc:  # noqa: BLE001
                errors.append({"url": inference_url, "error": f"{type(exc).__name__}: {exc}"})
        try:
            camera_payload["frame"] = _download_smartroom_frame(
                base_url=base_url, day=day, rec=rec, camera=camera_name, camera_info=camera_info,
                frames_dir=output_root / "source_data" / "frames", timeout_seconds=timeout_seconds, max_bytes=max_bytes,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": _api_url(base_url, "recordings", day, rec, camera_name, "frame"), "error": f"{type(exc).__name__}: {exc}"})
        selected_payload["cameras"][camera_name] = camera_payload
    return selected_payload


def fetch_smartroom_payload(
    source_url: str,
    *,
    output_root: Path,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
    recording_override: Any | None = None,
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
    selected, selection = _select_smartroom_recording(recordings, question, recording_override)
    snapshot: dict[str, Any] = {
        "kind": "smartroom-control.snapshot.v1",
        "sourceApi": base_url,
        "fetchedAt": _utc_now().isoformat(),
        "question": _question_text(question_context),
        "selection": selection,
        "recordingCount": len(recordings),
        "recordings": recordings,
        "selected": None,
        "errors": errors,
    }

    aggregate_records = selection.get("aggregateRecordings") if isinstance(selection.get("aggregateRecordings"), list) else []
    records_to_fetch = [record for record in aggregate_records if isinstance(record, dict)]
    if not records_to_fetch and selected is not None:
        records_to_fetch = [selected]
    fetched_records = [
        _fetch_smartroom_recording_payload(
            record, base_url=base_url, output_root=output_root, timeout_seconds=timeout_seconds, max_bytes=max_bytes, errors=errors,
        )
        for record in records_to_fetch
    ]
    if selection.get("mode") == "requested_date_total":
        snapshot["aggregateSelected"] = fetched_records
    elif fetched_records:
        snapshot["selected"] = fetched_records[0]

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
            "question": _question_text(question_context),
            "errors": len(errors),
            "needsClarification": bool(selection.get("needsClarification")),
            "clarificationPrompt": selection.get("clarificationPrompt"),
            "clarificationCandidates": selection.get("candidates") or [],
            "recordingOverride": selection.get("recordingOverride"),
        },
    }


def _extract_detection_summary(model: str, inference: dict[str, Any]) -> dict[str, Any]:
    detections = inference.get("detections") if isinstance(inference.get("detections"), dict) else {}
    timeline = detections.get("timeline") if isinstance(detections.get("timeline"), list) else []
    latest_count: int | None = None
    for point in reversed(timeline):
        if not isinstance(point, dict):
            continue
        try:
            latest_count = int(point.get("count") or 0)
            break
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
        "peakPeople": None,
        "lastPeople": latest_count,
        "samples": len(timeline),
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
    aggregate_selected = snapshot.get("aggregateSelected") if isinstance(snapshot.get("aggregateSelected"), list) else []
    if selection.get("mode") == "requested_date_total":
        take_peaks: list[int] = []
        for recording in aggregate_selected:
            if not isinstance(recording, dict):
                continue
            camera_peaks: list[int] = []
            for camera in (recording.get("cameras") or {}).values():
                if not isinstance(camera, dict):
                    continue
                for model, wrapper in (camera.get("inference") or {}).items():
                    data = wrapper.get("data") if isinstance(wrapper, dict) and isinstance(wrapper.get("data"), dict) else {}
                    peak = _extract_detection_summary(str(model), data).get("peakPeople")
                    if peak is not None:
                        camera_peaks.append(int(peak))
            if camera_peaks:
                take_peaks.append(max(camera_peaks))
        total = sum(take_peaks)
        label = requested_label or "the requested date"
        text = (
            f"Across {len(take_peaks)} recorded takes on {label}, the summed per-take peak occupancy was {total} people. "
            "This is a recording aggregate, not a count of unique people across takes."
        )
        return {
            "question": question, "text": text, "chatAnswer": text, "chat_answer": text,
            "recording": None, "recordingsAggregated": len(take_peaks), "aggregatePeakPeople": total,
            "cameras": [], "selection": selection, "errors": snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else [],
        }
    if selected is None:
        candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
        if selection.get("needsClarification"):
            prompt = str(selection.get("clarificationPrompt") or "Which smartroom recording should I use?")
            option_text = "; ".join(
                str(item.get("label") or item.get("rec") or item.get("day") or "recording")
                for item in candidates[:6]
                if isinstance(item, dict)
            )
            text = prompt + (f" Options: {option_text}." if option_text else "")
            return {
                "question": question,
                "text": text,
                "chatAnswer": prompt,
                "chat_answer": prompt,
                "needsClarification": True,
                "clarificationPrompt": prompt,
                "clarificationCandidates": candidates,
                "recording": None,
                "cameras": [],
                "selection": selection,
                "errors": snapshot.get("errors") if isinstance(snapshot.get("errors"), list) else [],
            }
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
    latest_parts: list[str] = []
    activity_parts: list[str] = []
    latest_values: list[int] = []
    for camera in cameras:
        name = _format_smartroom_camera_label(camera.get("camera") or "camera")
        latest = camera.get("lastPeople")
        if latest is not None:
            try:
                latest_int = int(latest)
            except (TypeError, ValueError):
                latest_int = None
            if latest_int is not None:
                latest_values.append(latest_int)
                latest_parts.append(f"{name} most recently showed {_person_count_label(latest_int)}")
        activities = camera.get("activities") or camera.get("actions") or []
        if isinstance(activities, list) and activities:
            activity_parts.append(f"{name}: {', '.join(str(item) for item in activities[:8])}")
    if not latest_parts:
        return "I found the selected recording, but it did not include a usable latest occupancy reading."

    recording = " / ".join(part for part in [day, rec] if part)
    if requested_label:
        prefix = f"For {requested_label} ({recording}), " if recording else f"For {requested_label}, "
    else:
        prefix = f"For the latest recording ({recording}), " if recording else "For the latest recording, "
    latest_value = max(latest_values)
    summary = prefix + f"the latest available room-level reading showed {_person_count_label(latest_value)}."
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

_CARLA_SOURCE_MODES = {"carla", "simulation", "carla-trace-server", "carla_trace_server"}


def _carla_trace_candidates(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for trace in traces:
        trace_id = str(trace.get("trace_id") or "")
        if not trace_id:
            continue
        bits = []
        if trace.get("scenario") is not None:
            bits.append(f"scenario {trace.get('scenario')}")
        if trace.get("actor_count") is not None:
            bits.append(f"{trace.get('actor_count')} actors")
        map_name = str(trace.get("map") or "").rsplit("/", 1)[-1]
        if map_name:
            bits.append(map_name)
        if trace.get("has_video"):
            bits.append("video")
        candidates.append({
            "recordingId": trace_id,
            "label": trace_id,
            "detail": ", ".join(bits),
        })
    return candidates


def _carla_override_id(recording_override: Any | None) -> str:
    if isinstance(recording_override, dict):
        return str(recording_override.get("recordingId") or recording_override.get("id") or "").strip()
    if recording_override is None:
        return ""
    return str(recording_override).strip()


def fetch_carla_payload(
    source_url: str,
    *,
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    api_key: str = "",
    question_context: Any | None = None,
    recording_override: Any | None = None,
) -> dict[str, Any]:
    """Preflight for the CARLA trace server: reachability plus trace choice.

    Mirrors the smartroom preflight contract: when neither the question nor an
    override names a trace, return a clarification listing the trace ids so the
    UI can render its selection dropdown. Trace timestamps are experimental
    upstream and are deliberately ignored here.
    """
    from tracefix.runtime import web_data_agent as _agent

    base = _agent._carla_base_url(source_url)
    url = f"{base}/traces"
    if api_key:
        url += ("&" if "?" in url else "?") + urlencode({"api_key": api_key})
    traces: list[dict[str, Any]] = []
    body = b"[]"
    error = None
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TraceFix-WebData/0.1"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - configured source
            body = response.read(max_bytes)
        parsed_body = json.loads(body.decode("utf-8-sig"))
        traces = [item for item in parsed_body if isinstance(item, dict)] if isinstance(parsed_body, list) else []
    except Exception as exc:  # noqa: BLE001 - the agent run reports retrieval failures itself
        message = f"{type(exc).__name__}: {exc}"
        error = message.replace(api_key, "[redacted]") if api_key else message

    question = _question_text(question_context)
    candidates = _carla_trace_candidates(traces)
    override_id = _carla_override_id(recording_override)
    answer: dict[str, Any] | None = None
    if error is None:
        if override_id:
            match = next(
                (item for item in traces if str(item.get("trace_id") or "").casefold() == override_id.casefold()),
                None,
            )
            if match is None:
                answer = {
                    "needsClarification": True,
                    "clarificationPrompt": "I could not find that simulation trace. Choose one of the available traces.",
                    "clarificationCandidates": candidates,
                }
        else:
            _, selection = _agent._select_carla_trace(traces, question)
            if selection.get("mode") != "question_match" and len(traces) > 1:
                answer = {
                    "needsClarification": True,
                    "clarificationPrompt": "Which simulation trace should I analyze? Each trace is one recorded CARLA run.",
                    "clarificationCandidates": candidates,
                }
    return {
        "url": base,
        "sourceKind": "carla-trace-server",
        "status": 200 if error is None else None,
        "contentType": "application/json",
        "body": body if isinstance(body, bytes) else b"[]",
        "fetchedAt": _utc_now().isoformat(),
        "answer": answer,
        "traceCount": len(traces),
        "error": error,
    }


def fetch_source_payload(
    source_url: str,
    *,
    output_root: Path,
    source_mode: str = "auto",
    timeout_seconds: int = 30,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    question_context: Any | None = None,
    raw_data_json: str | None = None,
    recording_override: Any | None = None,
    source_api_key: str = "",
) -> dict[str, Any]:
    mode = str(source_mode or "auto").strip().lower()
    raw_text = str(raw_data_json or "").strip()
    if raw_text or mode in {"raw-json", "raw_json", "pasted-json", "pasted_json", "json"}:
        return fetch_raw_json_payload(
            raw_text,
            max_bytes=max_bytes,
            question_context=question_context,
        )
    if mode in _CARLA_SOURCE_MODES:
        return fetch_carla_payload(
            source_url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            api_key=source_api_key,
            question_context=question_context,
            recording_override=recording_override,
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
            recording_override=recording_override,
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



def _write_agent_request(
    *,
    output_root: Path,
    source_url: str,
    source_mode: str,
    question_context: Any | None,
    raw_data_json: str | None,
    recording_override: Any | None,
) -> tuple[Path, dict[str, Any]]:
    """The harness transports instructions only; generated agents own retrieval and answers."""
    source_dir = output_root / "source_data"
    source_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "sourceUrl": source_url,
        "sourceMode": source_mode,
        "question": _question_text(question_context),
        "rawDataJson": raw_data_json or "",
        "recordingOverride": _normalize_recording_override(recording_override),
        "requestedAt": _utc_now().isoformat(),
    }
    path = source_dir / f"{_utc_now().strftime('%Y%m%d-%H%M%S-%f')}_agent_request.json"
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path, {"url": source_url, "sourceKind": "agent-owned", "payloadPath": str(path), "metadataPath": None, "sizeBytes": path.stat().st_size}


def _answer_from_generated_runs(runs: list[dict[str, Any]]) -> Any:
    for run in reversed(runs):
        if (run.get("app") or {}).get("kind") == "monitor":
            continue
        for record in reversed(run.get("handlerRecords") or []):
            try:
                output = json.loads(Path(record).read_text(encoding="utf-8")).get("handler", {}).get("output", "")
                value = json.loads(output.strip().splitlines()[-1])
                return value.get("answer", value) if isinstance(value, dict) else value
            except (OSError, ValueError, json.JSONDecodeError, IndexError):
                continue
    return None

def _resolve_app_path(app: CityOSDockerApp, manifest_path: Path) -> Path:
    path = app.path.expanduser()
    return path.resolve() if path.is_absolute() else (manifest_path.parent / path).resolve()


def _handler_command(command: str | list[str] | None) -> list[str]:
    """Resolve the executable handler packaged with every synthesized app."""
    if command is None or command == "" or command == []:
        return [sys.executable, str(Path(__file__).with_name("web_data_agent.py"))]
    if isinstance(command, list):
        return [str(item) for item in command if str(item).strip()]
    raw = str(command).strip()
    return shlex.split(raw) if raw else []


def _write_agent_phase_request(output_root: Path, app: CityOSDockerApp, phase: str, payload: dict[str, Any]) -> Path:
    request_dir = output_root / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / f"{phase}_{_safe_name(app.name)}.json"
    request_path.write_text(json.dumps({"phase": phase, **payload}, indent=2) + "\n", encoding="utf-8")
    return request_path


def _read_handler_output(handler_record: Path) -> dict[str, Any]:
    record = json.loads(handler_record.read_text(encoding="utf-8"))
    handler = record.get("handler") if isinstance(record, dict) else None
    if not isinstance(handler, dict) or handler.get("status") != "completed":
        raise RuntimeError(f"generated app handler failed: {handler or record}")
    raw_output = str(handler.get("output") or "").strip()
    if not raw_output:
        raise RuntimeError("generated app handler returned no structured output")
    try:
        output = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("generated app handler returned invalid JSON") from exc
    if not isinstance(output, dict) or output.get("ok") is not True:
        raise RuntimeError(f"generated app reported failure: {output}")
    return output


async def _run_app_phase(
    *,
    app: CityOSDockerApp,
    manifest_path: Path,
    request_path: Path,
    phase: str,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
    handler_environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    app_path = _resolve_app_path(app, manifest_path)
    bundle_dir = app_path / "tracefix_bundle"
    output_dir = output_root / "apps" / app.name / phase
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
            handler_cmd=_handler_command(handler_command) or [sys.executable, str(app_path / "generated_handler.py")],
            handler_timeout=float(handler_timeout_seconds),
            verbose=False,
            task_id="",
            handler_env=dict(handler_environment or {}),
        )
        harness = CityOSAgentHarness(config)
        existing_records = set()
        if frames_dir.exists():
            existing_records = {path.resolve() for path in frames_dir.glob("*.json")}
        ready_path = await harness.write_readiness()
        await harness.receive_frame(f"web_data_{phase}", request_path, _utc_now())
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
        if not handler_records:
            raise RuntimeError("generated app did not produce a handler result")
        agent_output = _read_handler_output(Path(handler_records[-1]))
        return {
            "app": {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app_path),
            },
            "status": "completed",
            "phase": phase,
            "outputDir": str(output_dir),
            "readyPath": str(ready_path),
            "framesDir": str(frames_dir),
            "frameRecords": frame_records,
            "handlerRecords": handler_records,
            "handlerConfigured": bool(config.handler_cmd),
            "agentOutput": agent_output,
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
            "phase": phase,
            "outputDir": str(output_dir),
            "framesDir": str(frames_dir),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _is_answer_agent(app: CityOSDockerApp) -> bool:
    label = f"{app.name} {app.agent or ''}".lower()
    return "answer" in label or "synth" in label


def _retrieval_communication_events(
    retrieval_runs: list[dict[str, Any]],
    evidence_packets: list[dict[str, Any]],
    answer_agent: CityOSDockerApp,
) -> list[dict[str, Any]]:
    """Describe evidence messages before they are delivered to the answer agent."""
    events: list[dict[str, Any]] = []
    answer_name = str(answer_agent.agent or answer_agent.name)
    for sequence, (run, evidence) in enumerate(zip(retrieval_runs, evidence_packets), start=1):
        app = run.get("app") if isinstance(run.get("app"), dict) else {}
        events.append({
            "sequence": sequence,
            "from": str(app.get("agent") or app.get("name") or evidence.get("producer_agent") or "unknown"),
            "to": answer_name,
            "label": "evidence_packet",
            "phase": "retrieve",
            "status": str(run.get("status") or "unknown"),
            "message": {
                "kind": evidence.get("kind"),
                "producer_agent": evidence.get("producer_agent"),
                "source_kind": evidence.get("source_kind"),
                "source_count": evidence.get("source_count"),
                "selected": bool(evidence.get("selected")),
                "error_count": len(evidence.get("errors") or []),
                "generation_mode": evidence.get("generation_mode"),
                "tool_trace": evidence.get("tool_trace") or [],
            },
        })
    return events


def _answer_communication_event(
    *,
    sequence: int,
    synthesis_run: dict[str, Any],
    answer: dict[str, Any],
    answer_agent: CityOSDockerApp,
) -> dict[str, Any]:
    """Describe answer delivery metadata without exposing or judging its content."""
    answer_name = str(answer_agent.agent or answer_agent.name)
    return {
        "sequence": sequence,
        "from": answer_name,
        "to": "tellme",
        "label": "answer_packet",
        "phase": "synthesize",
        "status": str(synthesis_run.get("status") or "unknown"),
        "message": {
            "producer_agent": answer.get("producer_agent"),
            "kind": "answer_packet",
            "generation_mode": answer.get("generation_mode"),
        },
    }


async def _run_monitor_checkpoint(
    *,
    monitor_apps: list[CityOSDockerApp],
    manifest_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
    agent_provider: str,
    agent_model: str,
    agent_api_key: str,
    mode: str,
    transcript: list[dict[str, Any]],
    current_event: dict[str, Any] | None = None,
    single_agent_execution: bool = False,
    state_transition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run active monitor apps at one protocol lifecycle checkpoint."""
    checkpoint_name = f"monitor_{mode}"
    if mode == "event" and isinstance(current_event, dict):
        checkpoint_name += f"_{int(current_event.get('sequence') or len(transcript))}"
    requests = [
        _write_agent_phase_request(
            output_root,
            app,
            checkpoint_name,
            {
                "phase": f"monitor_{mode}",
                "monitor_mode": mode,
                "communication_transcript": transcript,
                "current_event": current_event,
                "single_agent_execution": single_agent_execution,
                "state_transition": state_transition,
                "agent_provider": agent_provider,
                "agent_model": agent_model,
            },
        )
        for app in monitor_apps
    ]
    return await asyncio.gather(*[
        _run_app_phase(
            app=app,
            manifest_path=manifest_path,
            request_path=request_path,
            phase=checkpoint_name,
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
            handler_environment={"TRACEFIX_RUNTIME_AGENT_API_KEY": agent_api_key} if agent_api_key else {},
        )
        for app, request_path in zip(monitor_apps, requests)
    ])


async def _run_agent_pipeline(
    *,
    apps: list[CityOSDockerApp],
    manifest_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
    source_url: str,
    source_mode: str,
    timeout_seconds: int,
    max_bytes: int,
    question: str,
    raw_data_json: str,
    recording_override: Any | None,
    agent_provider: str,
    agent_model: str,
    agent_api_key: str,
    source_api_key: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    agent_environment = {
        name: value
        for name, value in {
            "TRACEFIX_RUNTIME_AGENT_API_KEY": agent_api_key,
            "TRACEFIX_SOURCE_API_KEY": source_api_key,
        }.items()
        if value
    }
    agent_apps = [app for app in apps if app.kind != "monitor"]
    monitor_apps = [app for app in apps if app.kind == "monitor"]
    if not monitor_apps:
        raise ValueError("Synthesis manifest must contain at least one runtime monitor app")
    single_app: CityOSDockerApp | None = None
    for candidate in agent_apps:
        try:
            plan_path = _resolve_app_path(candidate, manifest_path) / "tracefix_bundle" / "plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            protocol = plan.get("protocol") if isinstance(plan.get("protocol"), dict) else {}
            topology = protocol.get("topology") if isinstance(protocol.get("topology"), dict) else {}
            planned = topology.get("agents") if isinstance(topology.get("agents"), list) else []
            ids = {str(item.get("id") or item.get("name") or "") for item in planned if isinstance(item, dict)}
            if len(ids) == 1 and not protocol.get("allowed_communication_edges") and str(candidate.agent or candidate.name) in ids:
                single_app = candidate
                break
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    if single_app is not None:
        runs: list[dict[str, Any]] = []
        start = await _run_monitor_checkpoint(monitor_apps=monitor_apps, manifest_path=manifest_path, output_root=output_root, handler_command=handler_command, handler_timeout_seconds=handler_timeout_seconds, agent_provider=agent_provider, agent_model=agent_model, agent_api_key=agent_api_key, mode="start", transcript=[], single_agent_execution=True)
        runs.extend(start)
        request_path = _write_agent_phase_request(output_root, single_app, "single_agent", {"source_url": source_url, "source_mode": source_mode, "timeout_seconds": timeout_seconds, "max_bytes": max_bytes, "question": question, "raw_data_json": raw_data_json, "recording_override": recording_override, "agent_provider": agent_provider, "agent_model": agent_model})
        single_run = await _run_app_phase(app=single_app, manifest_path=manifest_path, request_path=request_path, phase="single_agent", output_root=output_root, handler_command=handler_command, handler_timeout_seconds=handler_timeout_seconds, handler_environment=agent_environment)
        runs.append(single_run)
        evidence = single_run.get("agentOutput", {}).get("evidence_packet")
        answer = single_run.get("agentOutput", {}).get("answer_packet")
        if single_run.get("status") != "completed" or not isinstance(answer, dict):
            return runs, [evidence] if isinstance(evidence, dict) else [], None
        name = str(single_app.agent or single_app.name)
        transition = {"agent": name, "from": f"{name}_start", "to": f"{name}_done", "status": "completed"}
        complete = await _run_monitor_checkpoint(monitor_apps=monitor_apps, manifest_path=manifest_path, output_root=output_root, handler_command=handler_command, handler_timeout_seconds=handler_timeout_seconds, agent_provider=agent_provider, agent_model=agent_model, agent_api_key=agent_api_key, mode="complete", transcript=[], single_agent_execution=True, state_transition=transition)
        runs.extend(complete)
        if any(run.get("status") != "completed" for run in start + complete):
            return runs, [evidence] if isinstance(evidence, dict) else [], None
        return runs, [evidence] if isinstance(evidence, dict) else [], answer
    if len({app.name for app in agent_apps}) < 2:
        raise ValueError(
            "Synthesis manifest must contain distinct retrieval and answer agent apps; "
            "regenerate the CityOS apps with the current TraceFix synthesizer"
        )
    if any(monitor.name in {app.name for app in agent_apps} for monitor in monitor_apps):
        raise ValueError("Runtime monitor app must be distinct from retrieval and answer agent apps")
    synthesizer = next((app for app in agent_apps if _is_answer_agent(app)), agent_apps[-1])
    retrievers = [app for app in agent_apps if app is not synthesizer]
    common_request = {
        "source_url": source_url,
        "source_mode": source_mode,
        "timeout_seconds": timeout_seconds,
        "max_bytes": max_bytes,
        "question": question,
        "raw_data_json": raw_data_json,
        "recording_override": recording_override,
        "agent_provider": agent_provider,
        "agent_model": agent_model,
    }
    runs: list[dict[str, Any]] = []
    communication_transcript: list[dict[str, Any]] = []
    monitor_start_runs = await _run_monitor_checkpoint(
        monitor_apps=monitor_apps,
        manifest_path=manifest_path,
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
        agent_provider=agent_provider,
        agent_model=agent_model,
        agent_api_key=agent_api_key,
        mode="start",
        transcript=communication_transcript,
    )
    runs.extend(monitor_start_runs)
    if any(run.get("status") != "completed" for run in monitor_start_runs):
        return runs, [], None
    retrieval_requests = [
        _write_agent_phase_request(output_root, app, "retrieve", common_request)
        for app in retrievers
    ]
    retrieval_runs = await asyncio.gather(*[
        _run_app_phase(
            app=app,
            manifest_path=manifest_path,
            request_path=request_path,
            phase="retrieve",
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
            handler_environment=agent_environment,
        )
        for app, request_path in zip(retrievers, retrieval_requests)
    ])
    runs.extend(retrieval_runs)
    evidence_packets = [
        run.get("agentOutput", {}).get("evidence_packet")
        for run in retrieval_runs
        if run.get("status") == "completed"
        and isinstance(run.get("agentOutput", {}).get("evidence_packet"), dict)
    ]
    if len(evidence_packets) != len(retrievers):
        return runs, evidence_packets, None

    for event in _retrieval_communication_events(retrieval_runs, evidence_packets, synthesizer):
        communication_transcript.append(event)
        monitor_event_runs = await _run_monitor_checkpoint(
            monitor_apps=monitor_apps,
            manifest_path=manifest_path,
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
            agent_provider=agent_provider,
            agent_model=agent_model,
            agent_api_key=agent_api_key,
            mode="event",
            transcript=communication_transcript,
            current_event=event,
        )
        runs.extend(monitor_event_runs)
        if any(run.get("status") != "completed" for run in monitor_event_runs):
            return runs, evidence_packets, None

    synthesis_request = _write_agent_phase_request(
        output_root,
        synthesizer,
        "synthesize",
        {
            "question": question,
            "evidence_packets": evidence_packets,
            "agent_provider": agent_provider,
            "agent_model": agent_model,
        },
    )
    synthesis_run = await _run_app_phase(
        app=synthesizer,
        manifest_path=manifest_path,
        request_path=synthesis_request,
        phase="synthesize",
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
        handler_environment=agent_environment,
    )
    runs.append(synthesis_run)
    answer = synthesis_run.get("agentOutput", {}).get("answer_packet")
    if synthesis_run.get("status") != "completed" or not isinstance(answer, dict):
        return runs, evidence_packets, None

    answer_event = _answer_communication_event(
        sequence=len(communication_transcript) + 1,
        synthesis_run=synthesis_run,
        answer=answer,
        answer_agent=synthesizer,
    )
    communication_transcript.append(answer_event)
    answer_event_runs = await _run_monitor_checkpoint(
        monitor_apps=monitor_apps,
        manifest_path=manifest_path,
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
        agent_provider=agent_provider,
        agent_model=agent_model,
        agent_api_key=agent_api_key,
        mode="event",
        transcript=communication_transcript,
        current_event=answer_event,
    )
    runs.extend(answer_event_runs)
    if any(run.get("status") != "completed" for run in answer_event_runs):
        return runs, evidence_packets, None
    monitor_complete_runs = await _run_monitor_checkpoint(
        monitor_apps=monitor_apps,
        manifest_path=manifest_path,
        output_root=output_root,
        handler_command=handler_command,
        handler_timeout_seconds=handler_timeout_seconds,
        agent_provider=agent_provider,
        agent_model=agent_model,
        agent_api_key=agent_api_key,
        mode="complete",
        transcript=communication_transcript,
    )
    runs.extend(monitor_complete_runs)
    if any(run.get("status") != "completed" for run in monitor_complete_runs):
        return runs, evidence_packets, None
    return runs, evidence_packets, answer


async def _run_apps(
    *,
    apps: list[CityOSDockerApp],
    manifest_path: Path,
    payload_path: Path,
    output_root: Path,
    handler_command: str | list[str] | None,
    handler_timeout_seconds: int,
) -> list[dict[str, Any]]:
    """Run each generated app once against the agent-owned request envelope."""
    return await asyncio.gather(*[
        _run_app_phase(
            app=app,
            manifest_path=manifest_path,
            request_path=payload_path,
            phase="web_data",
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
        )
        for app in apps
    ])

def _agent_payload_metadata(output_root: Path, evidence_packets: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_path = output_root / "agent-evidence.json"
    body = json.dumps({"evidence_packets": evidence_packets}, indent=2) + "\n"
    evidence_path.write_text(body, encoding="utf-8")
    primary = evidence_packets[0] if evidence_packets else {}
    selected = primary.get("selected") if isinstance(primary.get("selected"), dict) else None
    selection = primary.get("selection") if isinstance(primary.get("selection"), dict) else {}
    snapshot_summary = {
        "recordingCount": primary.get("source_count", 0),
        "selectedDay": selected.get("day") if selected else None,
        "selectedRecording": selected.get("rec") if selected else None,
        "cameras": list((selected.get("cameras") or {}).keys()) if selected else [],
        "selectionMode": selection.get("mode"),
        "selectionReason": selection.get("reason"),
        "requestedDate": selection.get("requestedDate"),
        "requestedDateLabel": selection.get("requestedDateLabel"),
        "question": primary.get("question"),
        "recordingOverride": selection.get("recordingOverride"),
        "needsClarification": bool(selection.get("needsClarification")),
        "clarificationPrompt": selection.get("clarificationPrompt"),
        "clarificationCandidates": selection.get("candidates") or [],
        "errors": len(primary.get("errors") or []),
    }
    return {
        "url": primary.get("source_url"),
        "status": 200 if evidence_packets else None,
        "reason": "agent_retrieval_completed" if evidence_packets else "agent_retrieval_failed",
        "contentType": "application/json",
        "sourceKind": primary.get("source_kind") or "unknown",
        "snapshotSummary": snapshot_summary,
        "payloadPath": str(evidence_path),
        "sizeBytes": len(body.encode("utf-8")),
        "fetchedAt": primary.get("fetched_at"),
        "writtenAt": _utc_now().isoformat(),
        "producerAgent": primary.get("producer_agent"),
    }


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
    recording_override: Any | None = None,
    agent_provider: str = "local",
    agent_model: str = "gemma3:4b",
    agent_api_key: str = "",
    source_api_key: str = "",
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
        recording_override=recording_override,
        source_api_key=source_api_key,
    )
    source_answer = payload.get("answer")
    needs_recording_choice = (
        isinstance(source_answer, dict)
        and source_answer.get("needsClarification") is True
    )
    # The preflight uses the server's recording index only. Once an exact
    # recording is selected, hand an instruction envelope to the generated
    # agent so it owns retrieval and analysis of the chosen data.
    if needs_recording_choice:
        payload_metadata = write_web_payload(payload, output_root)
        payload_path = Path(str(payload_metadata["payloadPath"]))
    else:
        payload_path, payload_metadata = _write_agent_request(
            output_root=output_root,
            source_url=source_url,
            source_mode=source_mode,
            question_context=question_context,
            raw_data_json=raw_data_json,
            recording_override=recording_override,
        )
    # A selection is returned directly rather than allowing a generated app to
    # overwrite it with an answer from incomplete context.
    if needs_recording_choice:
        runs = []
        answer = source_answer
    else:
        runs, evidence_packets, answer = asyncio.run(_run_agent_pipeline(
            apps=apps,
            manifest_path=manifest_path,
            output_root=output_root,
            handler_command=handler_command,
            handler_timeout_seconds=handler_timeout_seconds,
            source_url=source_url,
            source_mode=source_mode,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            question=_question_text(question_context),
            raw_data_json=raw_data_json or "",
            recording_override=recording_override,
            agent_provider=agent_provider,
            agent_model=agent_model,
            agent_api_key=agent_api_key,
            source_api_key=source_api_key,
        ))
    evidence_packets = [
        packet
        for run in runs
        for packet in [run.get("agentOutput", {}).get("evidence_packet")]
        if isinstance(packet, dict)
    ]
    answer_data = answer if isinstance(answer, dict) else {}
    answer_path = _write_answer_artifacts(output_root, runs, answer_data or None)
    finished_at = _utc_now().isoformat()
    monitor_runs = [run for run in runs if str(run.get("phase") or "").startswith("monitor_")]
    completed_monitor_runs = [run for run in monitor_runs if run.get("phase") == "monitor_complete"]
    monitors_valid = all(
        run.get("status") == "completed"
        and run.get("agentOutput", {}).get("monitor", {}).get("valid") is True
        for run in monitor_runs
    )
    result = {
        "ok": needs_recording_choice or (
            bool(runs) and all(run.get("status") == "completed" for run in runs)
        ),
        "sourceUrl": payload.get("url") or source_url,
        "sourceKind": payload.get("sourceKind") or "http",
        "sourceMode": source_mode,
        "question": _question_text(question_context),
        "recordingOverride": (
            (payload_metadata.get("snapshotSummary") or {}).get("recordingOverride")
            if isinstance(payload_metadata.get("snapshotSummary"), dict)
            else _normalize_recording_override(recording_override)
        ),
        "manifestPath": str(manifest_path),
        "outputRoot": str(output_root),
        "startedAt": started_at,
        "finishedAt": finished_at,
        "payload": payload_metadata,
        "answer": answer,
        "answerPath": answer_path,
        "runs": runs,
        "agentPipeline": {
            "retrievalProducers": [packet.get("producer_agent") for packet in evidence_packets],
            "answerProducer": answer_data.get("producer_agent"),
            "monitorCount": len(completed_monitor_runs),
            "monitorCheckpointCount": len(monitor_runs),
            "provider": answer_data.get("runtime_provider"),
            "model": answer_data.get("runtime_model"),
        },
    }
    result_path = output_root / "web-data-run.json"
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["resultPath"] = str(result_path)
    return result
