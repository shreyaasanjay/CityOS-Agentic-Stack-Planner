"""Executable web-data handler packaged with synthesized TraceFix CityOS apps.

The host harness supplies request envelopes only.  Retrieval, evidence shaping,
and answer creation happen in this agent process so TeLLMe can display a
TraceFix-produced answer packet instead of synthesizing one itself.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _request_json(url: str, *, timeout: int, max_bytes: int, api_key: str = "") -> Any:
    headers = {"User-Agent": "TraceFix-Generated-Agent/0.1", "Accept": "application/json"}
    if api_key:
        # The trace server accepts ?api_key= too, but prefers the header:
        # query params end up in server logs and browser history.
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - approved runtime URL
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError(f"agent data response exceeded {max_bytes} bytes")
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        if api_key:
            detail = detail.replace(api_key, "[redacted]")
        raise RuntimeError(f"agent data request failed with HTTP {exc.code}: {detail[:500]}") from exc
    return json.loads(body.decode("utf-8-sig"))


def _request_json_object(url: str, *, timeout: int, max_bytes: int, api_key: str = "") -> dict[str, Any]:
    value = _request_json(url, timeout=timeout, max_bytes=max_bytes, api_key=api_key)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return value


def _request_text(url: str, *, timeout: int, max_bytes: int) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TraceFix-Generated-Agent/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - approved runtime URL
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError(f"agent data response exceeded {max_bytes} bytes")
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"agent data request failed with HTTP {exc.code}: {detail[:500]}") from exc
    return body.decode("utf-8-sig", "replace")


def _frame_seconds_from_timestamps_csv(csv_text: str) -> list[float]:
    """Per-frame real times (seconds from the first frame) from a clip's timestamps CSV.

    The CSV is `frame,hw_timestamp_ms,sync_ms` — hw_timestamp_ms is the hardware
    clock, the only timebase that is comparable across cameras (see the mirror
    API docs' "Timebases" section).
    """
    lines = [line.strip() for line in csv_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    header = [column.strip().lower() for column in lines[0].split(",")]
    try:
        hw_index = header.index("hw_timestamp_ms")
    except ValueError:
        return []
    stamps: list[float] = []
    for line in lines[1:]:
        columns = line.split(",")
        if len(columns) <= hw_index:
            continue
        try:
            stamps.append(float(columns[hw_index]))
        except ValueError:
            continue
    if len(stamps) < 2:
        return []
    first = stamps[0]
    return [round((stamp - first) / 1000.0, 3) for stamp in stamps]


def _json_from_model_text(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("runtime agent model returned a non-object JSON value")
    return parsed


def _answer_context(draft: dict[str, Any]) -> dict[str, Any]:
    """Return grounded facts without URLs, raw captures, or tracking identifiers."""
    simulation = draft.get("simulation")
    if isinstance(simulation, dict):
        return {
            "grounded_draft": draft.get("answer"),
            "simulation": simulation,
            "actor_positions": draft.get("actorPositions"),
            "limitations": draft.get("limitations") or [],
        }
    cameras = []
    for index, raw_camera in enumerate(draft.get("cameras", []), start=1):
        if not isinstance(raw_camera, dict):
            continue
        cameras.append({
            "source_index": index,
            "peak_people": raw_camera.get("peakPeople"),
            "latest_people": raw_camera.get("lastPeople"),
            "occupancy_depth_verified": bool(raw_camera.get("occupancyVerified")),
            "objects_seen": raw_camera.get("objects") or {},
            "sound_events": raw_camera.get("sounds") or {},
            "activities": raw_camera.get("activities") or [],
            "activity_counts": raw_camera.get("activityCounts") or {},
            "pose_available": bool((raw_camera.get("pose") or {}).get("available"))
            if isinstance(raw_camera.get("pose"), dict) else False,
        })
    return {
        "grounded_draft": draft.get("answer"),
        "recording_available": draft.get("recording") is not None,
        "cameras": cameras,
        "requested_activities": draft.get("requestedActivities") or [],
        "requested_activity_counts": draft.get("requestedActivityCounts") or {},
        "requested_activity_combination": draft.get("requestedActivityCombination"),
        "limitations": draft.get("limitations") or [],
    }


def _provider_endpoint(provider: str) -> str:
    override = os.environ.get("TRACEFIX_RUNTIME_AGENT_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1/chat/completions"
    if provider == "openai":
        return "https://api.openai.com/v1/chat/completions"
    if provider == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    if provider == "local":
        return "http://127.0.0.1:11434/v1/chat/completions"
    raise ValueError(f"unsupported CityOS runtime agent provider: {provider}")


def _generated_role_prompt() -> str:
    bundle_dir = Path(os.environ.get("TRACEFIX_BUNDLE_DIR", "tracefix_bundle")).expanduser()
    direct = bundle_dir / "prompt.md"
    if direct.is_file():
        return direct.read_text(encoding="utf-8", errors="replace")[:12_000]
    return ""


def _optional_bundle_json(bundle_dir: Path, relative_path: str) -> dict[str, Any]:
    path = bundle_dir / relative_path
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _monitor_protocol_context() -> dict[str, Any]:
    """Load the canonical verified artifacts packaged with the monitor app."""
    bundle_dir = Path(os.environ.get("TRACEFIX_BUNDLE_DIR", "tracefix_bundle")).expanduser()
    plan = _optional_bundle_json(bundle_dir, "plan.json")
    protocol = plan.get("protocol") if isinstance(plan.get("protocol"), dict) else {}
    verification = plan.get("verification") if isinstance(plan.get("verification"), dict) else {}
    runtime_monitor = _optional_bundle_json(bundle_dir, "monitor.json")
    ir = _optional_bundle_json(bundle_dir, "spec/ir.json")
    states = _optional_bundle_json(bundle_dir, "spec/states.json")
    summary = _optional_bundle_json(bundle_dir, "spec/summary.json")
    tla_path = bundle_dir / "spec" / "Protocol.tla"
    tla_excerpt = (
        tla_path.read_text(encoding="utf-8", errors="replace")[:16_000]
        if tla_path.is_file()
        else ""
    )
    return {
        "verification": {
            "status": verification.get("status"),
            "production_ready": verification.get("production_ready"),
            "tlc_passed": verification.get("tlc_passed", summary.get("tlc_passed")),
        },
        "monitor_rules": runtime_monitor.get("monitor_rules") or [],
        "allowed_communication_edges": protocol.get("allowed_communication_edges") or [],
        "allowed_transitions": protocol.get("allowed_transitions") or [],
        "topology": protocol.get("topology") or plan.get("topology") or {},
        "ir": ir,
        "states": states,
        "tlc_summary": summary,
        "tla_protocol_excerpt": tla_excerpt,
    }


def _call_model_json(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any],
    max_tokens: int = 600,
) -> tuple[dict[str, Any], str]:
    """Call the configured runtime model and require one structured JSON object."""
    api_key = os.environ.get("TRACEFIX_RUNTIME_AGENT_API_KEY", "").strip()
    if provider != "local" and not api_key:
        raise ValueError(f"{provider} API key is required for the generated CityOS agent")
    if not model:
        raise ValueError("a model is required for the generated CityOS agent")
    if provider == "openrouter" and model.startswith("openrouter/"):
        model = model.removeprefix("openrouter/")
    elif provider == "openai" and model.startswith("openai/"):
        model = model.removeprefix("openai/")
    endpoint = _provider_endpoint(provider)
    user_prompt = json.dumps(user_payload, separators=(",", ":"), default=str)
    if provider == "anthropic":
        payload = {
            "model": model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310 - configured provider endpoint
            response_payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read(64 * 1024).decode("utf-8", "replace").strip()
        if api_key:
            body = body.replace(api_key, "[redacted]")
        raise RuntimeError(f"{provider} runtime model request failed with HTTP {exc.code}: {body[:2000]}") from exc
    if provider == "anthropic":
        blocks = response_payload.get("content") if isinstance(response_payload, dict) else None
        content = next((str(item.get("text") or "") for item in blocks or [] if isinstance(item, dict)), "")
    else:
        choices = response_payload.get("choices") if isinstance(response_payload, dict) else None
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = str(message.get("content") or "")
    return _json_from_model_text(content), model


def _model_answer(*, provider: str, model: str, question: str, draft: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "You are the generated TraceFix CityOS answer agent. Answer the user's question only from "
        "the supplied structured facts. Do not invent observations. Do not reveal camera identifiers, "
        "URLs, paths, raw captures, timestamps, or tracking identifiers. Return only a JSON object with "
        "answer (string), confidence (number from 0 to 1), and limitations (array of strings)."
    )
    role_prompt = _generated_role_prompt()
    if role_prompt:
        system_prompt += "\n\nTraceFix-generated role instructions:\n" + role_prompt
    result, model = _call_model_json(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_payload={"question": question, "verified_facts": _answer_context(draft)},
    )
    answer = str(result.get("answer") or "").strip()
    if not answer:
        raise ValueError("runtime agent model response omitted answer")
    derived = draft.get("confidence")
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = derived if isinstance(derived, (int, float)) else 0.0
    if isinstance(derived, (int, float)):
        # The model rewrites the sentence, not the evidence behind it. It may lower
        # confidence but never raise it above what the underlying data supports.
        confidence = min(float(confidence), float(derived))
    limitations = result.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    # Keep the derived caveats: the model does not get to drop them by omission.
    merged = [*(draft.get("limitations") or []), *limitations]
    return {
        **draft,
        "answer": answer,
        "text": answer,
        "chatAnswer": answer,
        "chat_answer": answer,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "limitations": list(dict.fromkeys(str(item) for item in merged if str(item).strip())),
        "runtime_provider": provider,
        "runtime_model": model,
        "generation_mode": "llm",
    }


def _base_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    path = parsed.path.rstrip("/")
    marker = "/recordings"
    if marker in path:
        path = path.split(marker, 1)[0]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _api_url(base: str, *parts: str) -> str:
    encoded = "/".join(urllib.parse.quote(str(part).strip("/"), safe="") for part in parts)
    return f"{base.rstrip('/')}/{encoded}"


def _requested_date(question: str) -> dict[str, Any] | None:
    month_names = "|".join(_MONTHS)
    match = re.search(
        rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b",
        question.lower(),
    )
    if not match:
        return None
    month_name, day_text, year_text = match.groups()
    day = int(day_text)
    year = int(year_text) if year_text else None
    label = f"{month_name.title()} {day}" + (f", {year}" if year else "")
    return {"month": _MONTHS[month_name], "day": day, "year": year, "label": label}


def _recording_date(recording: dict[str, Any]) -> dict[str, int] | None:
    for value in (recording.get("day"), recording.get("rec")):
        match = re.search(r"(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)", str(value or ""))
        if match:
            return {"year": int(match.group(1)), "month": int(match.group(2)), "day": int(match.group(3))}
    return None


def _date_label(value: dict[str, int]) -> str:
    month = next((name.title() for name, number in _MONTHS.items() if number == value["month"]), str(value["month"]))
    return f"{month} {value['day']}, {value['year']}"


def _recording_option(recording: dict[str, Any]) -> dict[str, Any]:
    day = str(recording.get("day") or "")
    rec = str(recording.get("rec") or "")
    return {
        "recordingId": "/".join(part for part in (day, rec) if part),
        "day": day,
        "rec": rec,
        "label": " / ".join(part for part in (day, rec) if part) or "recording",
    }


def _select_recording(
    recordings: list[dict[str, Any]],
    question: str,
    recording_override: Any | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    requested = _requested_date(question)
    dated = [
        recording
        for recording in recordings
        if requested
        and (date := _recording_date(recording)) is not None
        and date["month"] == requested["month"]
        and date["day"] == requested["day"]
        and requested.get("year") in {None, date["year"]}
    ]
    if recording_override is not None:
        candidates = dated or recordings
        selected_override: dict[str, Any] | None = None
        if isinstance(recording_override, dict):
            identifier = str(recording_override.get("recordingId") or "").strip()
            parts = [part for part in identifier.split("/") if part]
            day = str(recording_override.get("day") or (parts[-2] if len(parts) >= 2 else "")).strip()
            rec = str(recording_override.get("rec") or (parts[-1] if len(parts) >= 2 else "")).strip()
            selected_override = next(
                (
                    recording for recording in recordings
                    if (not day or str(recording.get("day") or "") == day)
                    and (not rec or str(recording.get("rec") or "") == rec)
                ),
                None,
            )
        else:
            text = str(recording_override or "").strip()
            take_match = re.fullmatch(r"(?:take\s*)?(\d+)", text, flags=re.IGNORECASE)
            if take_match:
                take_index = int(take_match.group(1)) - 1
                if 0 <= take_index < len(candidates):
                    selected_override = candidates[take_index]
                else:
                    return None, {
                        "mode": "needs_clarification",
                        "requestedDate": requested,
                        "requestedDateLabel": requested.get("label") if requested else None,
                        "needsClarification": True,
                        "clarificationPrompt": f"Take {take_index + 1} is not available. Choose one of the listed takes.",
                        "candidates": [_recording_option(item) for item in candidates],
                        "reason": "invalid recording selection",
                    }
            else:
                lowered = text.casefold()
                selected_override = next(
                    (
                        recording for recording in candidates
                        if lowered in {
                            str(recording.get("rec") or "").casefold(),
                            _recording_option(recording)["recordingId"].casefold(),
                            _recording_option(recording)["label"].casefold(),
                        }
                    ),
                    None,
                )
        if selected_override is not None:
            option = _recording_option(selected_override)
            selected_date = _recording_date(selected_override)
            return selected_override, {
                "mode": "recording_override",
                "requestedDate": selected_date,
                "requestedDateLabel": _date_label(selected_date) if selected_date else None,
                "recordingOverride": {"day": option["day"], "rec": option["rec"]},
                "reason": f"selected requested recording {option['rec']}",
            }
        return None, {
            "mode": "recording_override",
            "requestedDate": requested,
            "requestedDateLabel": requested.get("label") if requested else None,
            "needsClarification": True,
            "clarificationPrompt": "I could not find that exact recording. Choose one of the available recordings.",
            "candidates": [_recording_option(item) for item in candidates],
            "reason": "recording override did not match exactly",
        }
    if requested:
        if dated and re.search(r"\b(total|cumulative|all takes|all recordings)\b", question, re.IGNORECASE):
            return None, {
                "mode": "requested_date_total",
                "requestedDate": requested,
                "requestedDateLabel": requested["label"],
                "aggregateRecordings": dated,
                "reason": f"aggregate occupancy requested across {len(dated)} recordings for {requested['label']}",
            }
        if dated:
            count = len(dated)
            return None, {
                "mode": "needs_clarification",
                "requestedDate": requested,
                "requestedDateLabel": requested["label"],
                "needsClarification": True,
                "clarificationPrompt": f"I found {count} smartroom {'recording' if count == 1 else 'recordings'} for {requested['label']}. Which take should I use?",
                "candidates": [_recording_option(item) for item in dated],
                "reason": f"recording selection required for requested date {requested['label']}",
            }
        available = sorted({_date_label(date) for item in recordings if (date := _recording_date(item))}, reverse=True)
        return None, {
            "mode": "requested_date",
            "requestedDate": requested,
            "requestedDateLabel": requested["label"],
            "availableDates": available,
            "reason": f"no recording matched requested date {requested['label']}",
        }
    if len(recordings) > 1:
        return None, {
            "mode": "needs_clarification",
            "needsClarification": True,
            "clarificationPrompt": "I found multiple smartroom recordings. Which date/take should I use?",
            "candidates": [_recording_option(item) for item in recordings],
            "reason": "the question did not identify a specific recording",
        }
    selected = recordings[0] if recordings else None
    return selected, {
        "mode": "latest",
        "reason": "selected newest recording because the question did not include a specific date",
    }


def _recording_catalog(recordings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose only the identifiers and completed inference tools the agent can select."""
    catalog = []
    for recording in recordings:
        cameras = recording.get("cameras") if isinstance(recording.get("cameras"), dict) else {}
        catalog.append({
            "day": str(recording.get("day") or ""),
            "rec": str(recording.get("rec") or ""),
            "cameras": [
                {
                    "name": str(camera_name),
                    "completed_models": [
                        str(model)
                        for model, status in (
                            metadata.get("models", {}).items()
                            if isinstance(metadata, dict) and isinstance(metadata.get("models"), dict)
                            else []
                        )
                        if str(status).lower() == "done"
                    ],
                }
                for camera_name, metadata in cameras.items()
            ],
        })
    return catalog


def _fetch_recording_evidence(
    *,
    base: str,
    selected: dict[str, Any],
    timeout: int,
    max_bytes: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    day = str(selected.get("day") or "")
    rec = str(selected.get("rec") or "")
    selected_packet: dict[str, Any] = {"day": day, "rec": rec, "cameras": {}}
    errors: list[dict[str, str]] = []
    cameras = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
    for camera_name, raw_metadata in cameras.items():
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        inference: dict[str, Any] = {}
        models = metadata.get("models") if isinstance(metadata.get("models"), dict) else {}
        for inference_model, status in models.items():
            if str(status).lower() != "done":
                continue
            endpoint = _api_url(base, "recordings", day, rec, str(camera_name), "inference", str(inference_model))
            try:
                inference[str(inference_model)] = {
                    "url": endpoint,
                    "data": _request_json_object(endpoint, timeout=timeout, max_bytes=max_bytes),
                }
            except Exception as exc:  # noqa: BLE001 - retain partial evidence
                errors.append({"url": endpoint, "error": f"{type(exc).__name__}: {exc}"})
        # Sidecar `t` values are container time and the container stretch differs
        # per camera; the timestamps CSV is the documented way to recover real
        # recording-relative seconds so cameras can be aligned.
        frame_seconds: list[float] = []
        if inference:
            timestamps_endpoint = _api_url(base, "recordings", day, rec, str(camera_name), "timestamps")
            try:
                frame_seconds = _frame_seconds_from_timestamps_csv(
                    _request_text(timestamps_endpoint, timeout=timeout, max_bytes=max_bytes)
                )
            except Exception as exc:  # noqa: BLE001 - container time remains a usable fallback
                errors.append({"url": timestamps_endpoint, "error": f"{type(exc).__name__}: {exc}"})
        selected_packet["cameras"][str(camera_name)] = {
            "metadata": metadata,
            "inference": inference,
            "frameSeconds": frame_seconds,
        }
    return selected_packet, errors


def _retrieve_smartroom(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    source_url = str(request.get("source_url") or "").strip()
    question = str(request.get("question") or "").strip()
    timeout = int(request.get("timeout_seconds") or 30)
    max_bytes = int(request.get("max_bytes") or 50 * 1024 * 1024)
    base = _base_url(source_url)
    recordings_doc = _request_json_object(_api_url(base, "recordings"), timeout=timeout, max_bytes=max_bytes)
    recordings = [item for item in recordings_doc.get("recordings", []) if isinstance(item, dict)]
    selected, selection = _select_recording(
        recordings,
        question,
        request.get("recording_override"),
    )
    errors: list[dict[str, str]] = []
    selected_packet: dict[str, Any] | None = None
    aggregate_packets: list[dict[str, Any]] = []
    if selected:
        selected_packet, errors = _fetch_recording_evidence(
            base=base,
            selected=selected,
            timeout=timeout,
            max_bytes=max_bytes,
        )
    for aggregate_recording in selection.get("aggregateRecordings") or []:
        if not isinstance(aggregate_recording, dict):
            continue
        packet, packet_errors = _fetch_recording_evidence(
            base=base,
            selected=aggregate_recording,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        aggregate_packets.append(packet)
        errors.extend(packet_errors)
    selection = {key: value for key, value in selection.items() if key != "aggregateRecordings"}
    return {
        "kind": "tracefix.agent.evidence.v1",
        "producer_agent": agent_id,
        "source_kind": "smartroom-control",
        "source_url": base,
        "question": question,
        "fetched_at": _now(),
        "source_count": len(recordings),
        "recordings": recordings,
        "selection": selection,
        "selected": selected_packet,
        "aggregate_selected": aggregate_packets,
        "errors": errors,
    }


def _retrieval_system_prompt(agent_id: str, tools: list[str]) -> str:
    tool_names = ", ".join(tools)
    prompt = (
        f"You are the generated TraceFix CityOS retrieval agent {agent_id}. "
        "You receive a goal and execute approved data tools to collect grounded evidence. "
        "Return exactly one JSON object per turn. To call a tool return "
        '{"action":"tool","tool":"TOOL_NAME","arguments":{...}}. '
        "When the evidence is sufficient return "
        '{"action":"finish","summary":"grounded summary","limitations":[]}. '
        f"The only approved tools are: {tool_names}. Never invent a tool result, URL, or observation. "
        "Do not answer from prior knowledge; inspect tool observations first."
    )
    role_prompt = _generated_role_prompt()
    if role_prompt:
        prompt += "\n\nTraceFix-generated role instructions:\n" + role_prompt
    return prompt


def _agent_action(
    *,
    provider: str,
    model: str,
    agent_id: str,
    question: str,
    tools: dict[str, str],
    transcript: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    return _call_model_json(
        provider=provider,
        model=model,
        system_prompt=_retrieval_system_prompt(agent_id, list(tools)),
        user_payload={
            "goal": question,
            "approved_tools": tools,
            "previous_steps": transcript,
            "instruction": "Choose one approved tool or finish. Output only the action JSON object.",
        },
        max_tokens=500,
    )


def _retrieve_smartroom_agent(request: dict[str, Any], agent_id: str, provider: str, model: str) -> dict[str, Any]:
    # Recording choice is a deterministic product/UI contract. Resolve
    # clarifications, explicit take selections, and cumulative-date requests
    # before allowing the model to choose retrieval tools.
    if request.get("recording_override") is not None:
        return _retrieve_smartroom(request, agent_id)
    source_url = str(request.get("source_url") or "").strip()
    question = str(request.get("question") or "").strip()
    timeout = int(request.get("timeout_seconds") or 30)
    max_bytes = int(request.get("max_bytes") or 50 * 1024 * 1024)
    base = _base_url(source_url)
    tools = {
        "list_recordings": "List available recording identifiers and completed inference models.",
        "get_recording_evidence": "Fetch completed inference evidence for one listed recording; arguments require day and rec.",
    }
    transcript: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    recordings: list[dict[str, Any]] | None = None
    selected_packet: dict[str, Any] | None = None
    errors: list[dict[str, str]] = []
    summary = ""
    limitations: list[str] = []
    normalized_model = model
    for step in range(1, 9):
        action, normalized_model = _agent_action(
            provider=provider,
            model=model,
            agent_id=agent_id,
            question=question,
            tools=tools,
            transcript=transcript,
        )
        action_name = str(action.get("action") or "").strip().lower()
        if action_name == "finish":
            if not tool_trace:
                raise ValueError("generated retrieval agent attempted to finish before using a data tool")
            summary = str(action.get("summary") or "").strip()
            raw_limitations = action.get("limitations")
            if isinstance(raw_limitations, list):
                limitations = [str(item) for item in raw_limitations if str(item).strip()]
            break
        if action_name != "tool":
            raise ValueError(f"generated retrieval agent returned unsupported action: {action_name or '(empty)'}")
        tool = str(action.get("tool") or "").strip()
        arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        if tool not in tools:
            raise ValueError(f"generated retrieval agent requested an unapproved tool: {tool or '(empty)'}")
        if tool == "list_recordings":
            recordings_doc = _request_json_object(_api_url(base, "recordings"), timeout=timeout, max_bytes=max_bytes)
            recordings = [item for item in recordings_doc.get("recordings", []) if isinstance(item, dict)]
            observation: dict[str, Any] = {"recordings": _recording_catalog(recordings)}
        else:
            if recordings is None:
                raise ValueError("get_recording_evidence requires list_recordings first")
            day = str(arguments.get("day") or "")
            rec = str(arguments.get("rec") or "")
            selected = next(
                (item for item in recordings if str(item.get("day") or "") == day and str(item.get("rec") or "") == rec),
                None,
            )
            if selected is None:
                raise ValueError("generated retrieval agent selected a recording that was not returned by list_recordings")
            selected_packet, fetch_errors = _fetch_recording_evidence(
                base=base,
                selected=selected,
                timeout=timeout,
                max_bytes=max_bytes,
            )
            errors.extend(fetch_errors)
            observation = {
                "selected": {"day": day, "rec": rec},
                "camera_count": len(selected_packet["cameras"]),
                "inference_models": {
                    camera: list(packet.get("inference", {}))
                    for camera, packet in selected_packet["cameras"].items()
                    if isinstance(packet, dict)
                },
                "errors": len(fetch_errors),
            }
        trace_item = {"step": step, "tool": tool, "arguments": arguments, "status": "completed"}
        tool_trace.append(trace_item)
        transcript.append({"action": trace_item, "observation": observation})
    else:
        raise ValueError("generated retrieval agent exceeded the 8-step tool limit")
    safe_recordings = recordings or []
    _, selection = _select_recording(safe_recordings, question)
    if selected_packet is not None:
        selection = {
            **selection,
            "mode": "agent_selected",
            "reason": summary or "generated LLM agent selected and inspected this recording",
        }
    return {
        "kind": "tracefix.agent.evidence.v1",
        "producer_agent": agent_id,
        "source_kind": "smartroom-control",
        "source_url": base,
        "question": question,
        "fetched_at": _now(),
        "source_count": len(safe_recordings),
        "recordings": safe_recordings,
        "selection": selection,
        "selected": selected_packet,
        "errors": errors,
        "agent_summary": summary,
        "agent_limitations": limitations,
        "runtime_provider": provider,
        "runtime_model": normalized_model,
        "generation_mode": "llm_tool_agent",
        "tool_trace": tool_trace,
    }


def _source_api_key(request: dict[str, Any]) -> str:
    return str(
        request.get("source_api_key")
        or os.environ.get("TRACEFIX_SOURCE_API_KEY", "")
    ).strip()


def _carla_base_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    path = parsed.path.rstrip("/")
    # The shared link usually points at the human pages, not the API root.
    for page in ("/ui", "/endpoints", "/docs", "/traces"):
        if path.endswith(page):
            path = path[: -len(page)]
            break
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _select_carla_trace(traces: list[dict[str, Any]], question: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Pick one trace; the trace server's capture timestamps are experimental and never used."""
    lowered = question.lower()
    for trace in traces:
        trace_id = str(trace.get("trace_id") or "")
        if trace_id and trace_id.lower() in lowered:
            return trace, {"mode": "question_match", "reason": f"the question named trace {trace_id}"}
    # A couple of traces carry a synthetic_date fabricated expressly so TraceFix
    # can anchor date-scoped questions; capture epochs stay ignored.
    requested = _requested_date(question)
    if requested:
        for trace in traces:
            match = re.match(r"(\d{4})-(\d{2})-(\d{2})$", str(trace.get("synthetic_date") or ""))
            if match and int(match.group(2)) == requested["month"] and int(match.group(3)) == requested["day"] \
                    and requested.get("year") in (None, int(match.group(1))):
                return trace, {
                    "mode": "synthetic_date_match",
                    "reason": f"the question's date matches this trace's synthetic date {trace.get('synthetic_date')}",
                }
    scenario_match = re.search(r"\bscenario\s*(\d+)\b", lowered)
    if scenario_match:
        wanted = int(scenario_match.group(1))
        for trace in traces:
            if trace.get("scenario") == wanted:
                return trace, {
                    "mode": "question_match",
                    "reason": f"the question asked for scenario {wanted}",
                }
    first_scenario = next(
        (trace for trace in traces if str(trace.get("trace_id") or "").startswith("scenario_")),
        None,
    )
    if first_scenario is not None:
        return first_scenario, {
            "mode": "default_scenario",
            "reason": "selected the first scenario trace because the question did not name one",
        }
    if traces:
        return traces[0], {"mode": "first_trace", "reason": "no scenario traces exist; selected the first trace"}
    return None, {"mode": "empty", "reason": "the trace server returned no traces"}


def _carla_trace_catalog(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "trace_id": str(trace.get("trace_id") or ""),
            "scenario": trace.get("scenario"),
            "map": trace.get("map"),
            "actor_count": trace.get("actor_count"),
            "junction": trace.get("junction"),
            "has_ground_truth": trace.get("has_ground_truth"),
            "synthetic_date": trace.get("synthetic_date"),
        }
        for trace in traces
    ]


def _carla_positions_records(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Flat {tick, actor_id, x, y, z} records from a positions-capture trace.

    demo_simple_<epoch> traces have no log/trajectories; their /full view carries
    the positions.json object instead — the record list plus static extras such
    as drop_point and camera.
    """
    raw = doc.get("positions")
    extras: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    if isinstance(raw, list):
        records = [item for item in raw if isinstance(item, dict) and item.get("actor_id")]
    elif isinstance(raw, dict):
        for key, value in raw.items():
            if not records and isinstance(value, list) and any(
                isinstance(item, dict) and item.get("actor_id") for item in value
            ):
                records = [item for item in value if isinstance(item, dict) and item.get("actor_id")]
            else:
                extras[key] = value
    return records, extras


def _carla_trace_summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Reduce one trace to grounded facts: entities, claims, tick aggregates, presence series."""
    log = [entry for entry in doc.get("log") or [] if isinstance(entry, dict)]
    trajectories = [item for item in doc.get("trajectories") or [] if isinstance(item, dict)]
    ground_truth = doc.get("ground_truth") if isinstance(doc.get("ground_truth"), dict) else {}
    position_records, position_extras = _carla_positions_records(doc)
    # The deployment view strips ground_truth, trajectories, and positions
    # entirely; when they are absent the entity/actor counts are unknown, not zero.
    has_full_view = (
        isinstance(doc.get("trajectories"), list)
        or isinstance(doc.get("ground_truth"), dict)
        or bool(position_records)
    )

    entities = [
        {
            "id": str(entity.get("local_id") or ""),
            "type": str(entity.get("type") or ""),
            "name": str(entity.get("name") or ""),
        }
        for entity in ground_truth.get("entities") or []
        if isinstance(entity, dict)
    ]
    entity_type_counts: dict[str, int] = {}
    for entity in entities:
        if entity["type"]:
            entity_type_counts[entity["type"]] = entity_type_counts.get(entity["type"], 0) + 1

    # Old-schema ground truth carries claims[]; the v2 schema (schema_version
    # present) expresses the same facts as events[] with different field names.
    claims = []
    for claim in [*(ground_truth.get("claims") or []), *(ground_truth.get("events") or [])]:
        if not isinstance(claim, dict):
            continue
        statement = str(
            claim.get("natural_language") or claim.get("description") or claim.get("text") or ""
        ).strip()
        if not statement:
            continue
        claims.append({
            "claim_type": str(claim.get("claim_type") or claim.get("event_type") or claim.get("type") or ""),
            "statement": statement,
            "confidence": claim.get("confidence"),
            "polarity": claim.get("polarity"),
        })

    tick_seconds: dict[int, float] = {}
    crosswalk_occupied_ticks = 0
    peak_crosswalk_fraction = 0.0
    collisions_total = 0
    has_collision_data = False
    for entry in log:
        tick = entry.get("tick")
        elapsed = entry.get("sim_elapsed_s")
        if isinstance(tick, int) and isinstance(elapsed, (int, float)):
            tick_seconds[tick] = float(elapsed)
        if entry.get("crosswalk_occupied") is True:
            crosswalk_occupied_ticks += 1
        fraction = entry.get("crosswalk_pedestrian_fraction")
        if isinstance(fraction, (int, float)):
            peak_crosswalk_fraction = max(peak_crosswalk_fraction, float(fraction))
        total = entry.get("collision_count_total")
        if isinstance(total, (int, float)):
            has_collision_data = True
            collisions_total = max(collisions_total, int(total))

    # People/vehicles in the scene over simulation time: one sample per tick,
    # counting the actors whose trajectory spans that tick.
    role_of_id: dict[str, str] = {entity["id"]: entity["type"] for entity in entities}
    presence: dict[float, int] = {}
    walker_presence: dict[float, int] = {}
    actor_roles: dict[str, int] = {}
    for trajectory in trajectories:
        meta = trajectory.get("meta") if isinstance(trajectory.get("meta"), dict) else {}
        role = str(meta.get("role") or role_of_id.get(str(meta.get("id") or ""), "") or "actor")
        actor_roles[role] = actor_roles.get(role, 0) + 1
        first_tick, last_tick = trajectory.get("first_tick"), trajectory.get("last_tick")
        if not isinstance(first_tick, int) or not isinstance(last_tick, int):
            continue
        for tick in range(first_tick, last_tick + 1):
            stamp = round(tick_seconds.get(tick, float(tick)), 2)
            presence[stamp] = presence.get(stamp, 0) + 1
            if role == "walker":
                walker_presence[stamp] = walker_presence.get(stamp, 0) + 1

    # Positions-capture traces (demo_simple_<epoch>) have no log/trajectories:
    # per-tick data is the flat positions record list. Ticks advance at 0.1s in
    # every generation seen so far, but that rate is empirical, not a contract.
    positions_actors: dict[str, dict[str, Any]] = {}
    tick_count = len(log)
    tick_rate_assumed = False
    for record in position_records:
        actor = str(record.get("actor_id") or "")
        tick = record.get("tick")
        if not actor or not isinstance(tick, int):
            continue
        coords = [record.get(axis) for axis in ("x", "y", "z")]
        point = (
            [round(float(value), 2) for value in coords]
            if all(isinstance(value, (int, float)) for value in coords)
            else None
        )
        entry = positions_actors.setdefault(
            actor, {"samples": 0, "first_tick": tick, "last_tick": tick, "start": point, "end": point}
        )
        entry["samples"] += 1
        if tick <= entry["first_tick"]:
            entry["first_tick"] = tick
            entry["start"] = point or entry["start"]
        if tick >= entry["last_tick"]:
            entry["last_tick"] = tick
            entry["end"] = point or entry["end"]
        stamp = round(tick * 0.1, 2)
        presence[stamp] = presence.get(stamp, 0) + 1
        if re.match(r"(pedestrian|walker|person)", actor):
            walker_presence[stamp] = walker_presence.get(stamp, 0) + 1
    if positions_actors and not log:
        last_tick = max(entry["last_tick"] for entry in positions_actors.values())
        tick_count = last_tick + 1
        tick_seconds[last_tick] = last_tick * 0.1
        tick_rate_assumed = True
        for actor in positions_actors:
            kind = re.sub(r"_?\d+$", "", actor) or actor
            entity_type_counts[kind] = entity_type_counts.get(kind, 0) + 1

    # The synthetic calendar date (tx_1785317034_0 / tx_1785319164_0 only) is
    # fabricated by request so date-scoped questions have an anchor; it is not
    # real ground truth. It appears per log tick and in the trace metadata.
    synthetic_date = str(
        doc.get("synthetic_date")
        or next((entry.get("synthetic_date") for entry in log if entry.get("synthetic_date")), "")
        or ""
    ) or None

    duration_s = max(tick_seconds.values(), default=0.0)
    return {
        "trace_id": str(doc.get("trace_id") or ""),
        "map": next((str(entry.get("space")) for entry in log if entry.get("space")), None),
        "scenario": next((entry.get("scenario") for entry in log if entry.get("scenario") is not None), None),
        "tick_count": tick_count,
        "duration_s": round(duration_s, 2),
        "has_ground_truth": bool(ground_truth),
        "data_scope": "full" if has_full_view else "deployment",
        "synthetic_date": synthetic_date,
        "entities": entities,
        "entity_type_counts": dict(sorted(entity_type_counts.items())) if has_full_view else None,
        "actor_role_counts": dict(sorted(actor_roles.items())) if has_full_view else None,
        "positions_actors": dict(sorted(positions_actors.items())) or None,
        "position_extras": position_extras or None,
        "tick_rate_assumed": tick_rate_assumed,
        "claims": claims,
        "crosswalk": {
            "ticks_occupied": crosswalk_occupied_ticks,
            "occupied_fraction": round(crosswalk_occupied_ticks / len(log), 3) if log else None,
            "peak_pedestrian_fraction": round(peak_crosswalk_fraction, 4),
        },
        "collisions_total": collisions_total if has_collision_data else None,
        "peak_actors_present": max(presence.values(), default=0) if has_full_view else None,
        "peak_walkers_present": max(walker_presence.values(), default=0) if has_full_view else None,
        "occupancySamples": _downsample_series(presence),
        "walkerOccupancySamples": _downsample_series(walker_presence),
    }


def _retrieve_carla(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    source_url = str(request.get("source_url") or "").strip()
    question = str(request.get("question") or "").strip()
    timeout = int(request.get("timeout_seconds") or 30)
    max_bytes = int(request.get("max_bytes") or 50 * 1024 * 1024)
    api_key = _source_api_key(request)
    base = _carla_base_url(source_url)
    traces_doc = _request_json(_api_url(base, "traces"), timeout=timeout, max_bytes=max_bytes, api_key=api_key)
    traces = [item for item in traces_doc if isinstance(item, dict)] if isinstance(traces_doc, list) else []
    override = request.get("recording_override")
    override_id = ""
    if isinstance(override, dict):
        override_id = str(override.get("recordingId") or override.get("id") or "").strip()
    elif override is not None:
        override_id = str(override).strip()
    selected = None
    if override_id:
        selected = next(
            (item for item in traces if str(item.get("trace_id") or "").casefold() == override_id.casefold()),
            None,
        )
    if selected is not None:
        selection: dict[str, Any] = {
            "mode": "recording_override",
            "reason": f"selected the requested trace {selected.get('trace_id')}",
        }
    else:
        selected, selection = _select_carla_trace(traces, question)
        if override_id:
            selection = {
                **selection,
                "reason": f"trace {override_id} was not found; {selection.get('reason')}",
            }
    errors: list[dict[str, str]] = []
    summary: dict[str, Any] | None = None
    full_view_denied = False
    if selected is not None:
        trace_id = str(selected.get("trace_id") or "")
        try:
            doc = _request_json_object(
                _api_url(base, "traces", trace_id, "full"),
                timeout=timeout, max_bytes=max_bytes, api_key=api_key,
            )
        except Exception as exc:  # noqa: BLE001 - the deployment view is a legitimate fallback
            message = str(exc)
            if "HTTP 403" in message or "HTTP 401" in message:
                # Deployment-scope keys cannot read /full; expected, not a data failure.
                full_view_denied = True
            else:
                errors.append({"url": _api_url(base, "traces", trace_id, "full"), "error": f"{type(exc).__name__}: {exc}"})
            doc = _request_json_object(
                _api_url(base, "traces", trace_id, "deployment"),
                timeout=timeout, max_bytes=max_bytes, api_key=api_key,
            )
        summary = _carla_trace_summary(doc)
        if summary.get("synthetic_date") is None and selected.get("synthetic_date"):
            summary["synthetic_date"] = str(selected["synthetic_date"])
    # When the question names an actor from a positions-capture trace, pull its
    # position history through the documented per-actor filter endpoint.
    actor_positions: dict[str, Any] | None = None
    if summary is not None and isinstance(summary.get("positions_actors"), dict):
        lowered_question = question.lower()
        matched_actor = next(
            (
                actor for actor in summary["positions_actors"]
                if actor.lower() in lowered_question
                or (re.sub(r"_?\d+$", "", actor).replace("_", " ") or actor).lower() in lowered_question
            ),
            None,
        )
        if matched_actor:
            positions_url = _api_url(base, "traces", str(selected.get("trace_id") or ""), "positions", matched_actor)
            try:
                history = _request_json(positions_url, timeout=timeout, max_bytes=max_bytes, api_key=api_key)
                if isinstance(history, list):
                    records = [item for item in history if isinstance(item, dict)]
                    stride = max(1, len(records) // 50)
                    actor_positions = {
                        "actor_id": matched_actor,
                        "record_count": len(records),
                        "records": records[::stride][:50],
                    }
            except Exception as exc:  # noqa: BLE001 - the summary already covers the actor coarsely
                errors.append({"url": positions_url, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "kind": "tracefix.agent.evidence.v1",
        "producer_agent": agent_id,
        "source_kind": "carla-trace-server",
        "source_url": base,
        "question": question,
        "fetched_at": _now(),
        "source_count": len(traces),
        "traces": _carla_trace_catalog(traces),
        "selection": selection,
        "selected": summary,
        "actor_positions": actor_positions,
        "full_view_denied": full_view_denied,
        "errors": errors,
    }


def _carla_answer(evidence: dict[str, Any], agent_id: str) -> dict[str, Any]:
    question = str(evidence.get("question") or "")
    selection = evidence.get("selection") if isinstance(evidence.get("selection"), dict) else {}
    summary = evidence.get("selected") if isinstance(evidence.get("selected"), dict) else None
    errors = evidence.get("errors") or []
    if summary is None:
        text = "The CARLA trace server returned no traces to analyze."
        return {
            "answer": text, "text": text, "chatAnswer": text, "chat_answer": text,
            "producer_agent": agent_id,
            "confidence": 0.0,
            "evidence_refs": [f"{evidence.get('producer_agent')}:trace-index"],
            "limitations": ["No simulation trace was available."],
            "source_count": int(evidence.get("source_count") or 0),
            "evidence_used_count": 0,
            "recording": None,
            "cameras": [],
            "selection": selection,
            "errors": errors,
        }
    trace_id = summary.get("trace_id") or "unknown"
    scenario = summary.get("scenario")
    map_name = str(summary.get("map") or "").rsplit("/", 1)[-1]
    lowered = question.lower()
    role_counts = summary.get("actor_role_counts") or {}
    type_counts = summary.get("entity_type_counts") or {}
    positions_actors = summary.get("positions_actors") if isinstance(summary.get("positions_actors"), dict) else {}
    walkers = int(
        type_counts.get("walker") or role_counts.get("walker") or type_counts.get("pedestrian") or 0
    )
    vehicles = int(type_counts.get("vehicle") or role_counts.get("vehicle") or 0)
    crosswalk = summary.get("crosswalk") or {}
    deployment_only = str(summary.get("data_scope") or "") == "deployment"
    occupied_fraction = crosswalk.get("occupied_fraction")
    synthetic_date = summary.get("synthetic_date")
    actor_positions = evidence.get("actor_positions") if isinstance(evidence.get("actor_positions"), dict) else None

    prefix = f"For simulation trace {trace_id}"
    if scenario is not None:
        prefix += f" (scenario {scenario}"
        prefix += f", {map_name})" if map_name else ")"
    elif map_name:
        prefix += f" ({map_name})"
    if synthetic_date:
        prefix += f" (synthetic date {synthetic_date})"
    prefix += ", "

    parts: list[str] = []
    if actor_positions and positions_actors.get(str(actor_positions.get("actor_id"))):
        actor_id = str(actor_positions.get("actor_id"))
        info = positions_actors[actor_id]
        start, end = info.get("start"), info.get("end")
        span = f"tick {info.get('first_tick')} to {info.get('last_tick')}"
        sentence = f"{actor_id} was tracked from {span} ({info.get('samples')} position records)"
        if isinstance(start, list) and isinstance(end, list) and len(start) == 3 and len(end) == 3:
            displacement = round(sum((e - s) ** 2 for s, e in zip(start, end)) ** 0.5, 2)
            sentence += (
                f"; it started at ({start[0]}, {start[1]}, {start[2]}) and ended at "
                f"({end[0]}, {end[1]}, {end[2]}), a net displacement of {displacement} units"
            )
        parts.append(sentence)
    if re.search(r"\b(crosswalk|cross\w*|across|road|street)\b", lowered) and occupied_fraction is not None:
        parts.append(
            f"the crosswalk was occupied for {round(float(occupied_fraction) * 100)}% "
            f"of the {summary.get('duration_s')}s simulation"
        )
    if re.search(r"\b(collisions?|crash(es)?|accidents?)\b", lowered):
        if summary.get("collisions_total") is None:
            parts.append("collision data is not included in the deployment view of this trace")
        else:
            parts.append(f"{summary.get('collisions_total', 0)} collision(s) were recorded")
    if re.search(r"\b(pedestrians?|people|persons?|walkers?)\b", lowered):
        if deployment_only:
            if isinstance(occupied_fraction, (int, float)) and occupied_fraction > 0:
                parts.append(
                    f"pedestrian activity was detected on the crosswalk for {round(float(occupied_fraction) * 100)}% "
                    f"of the {summary.get('duration_s')}s simulation, but an exact people count is not available "
                    "because this API key only has deployment-scope access"
                )
            else:
                parts.append(
                    "a people count is not available: this API key only has deployment-scope access, "
                    "which excludes the simulator's entity data"
                )
        else:
            parts.append(f"{_count_label(walkers)} (pedestrians) appeared in the scene")
    if re.search(r"\b(cars?|vehicles?|traffic)\b", lowered):
        if deployment_only:
            parts.append(
                "a vehicle count is not available: this API key only has deployment-scope access, "
                "which excludes the simulator's entity data"
            )
        else:
            parts.append(f"{vehicles} vehicle(s) appeared in the scene")
    if not parts:
        if deployment_only:
            if summary.get("tick_count"):
                occupied_text = (
                    f" and the crosswalk was occupied for {round(float(occupied_fraction) * 100)}% of it"
                    if isinstance(occupied_fraction, (int, float)) else ""
                )
                parts.append(
                    f"the simulation ran for {summary.get('duration_s')}s over {summary.get('tick_count')} ticks"
                    f"{occupied_text}; entity-level details require full trace access, which this API key does not have"
                )
            else:
                parts.append(
                    "the deployment view of this trace is empty; its contents require full trace access, "
                    "which this API key does not have"
                )
        else:
            described = ", ".join(f"{count} {kind}(s)" for kind, count in sorted(type_counts.items())) or "no catalogued entities"
            parts.append(f"the simulation ran for {summary.get('duration_s')}s and contained {described}")
    text = prefix + "; ".join(parts) + "."

    limitations = [
        "Trace timestamps are experimental and were not used; times are simulation-relative seconds.",
    ]
    if deployment_only:
        limitations.insert(
            0,
            "The trace server API key only has deployment scope: ground-truth entities, trajectories, "
            "and counts were not accessible, so missing values are unknown rather than zero.",
        )
    else:
        limitations.insert(0, "Counts come from the CARLA simulator's ground truth for the selected trace.")
        if not summary.get("has_ground_truth") and not positions_actors:
            limitations.append("This trace has no ground-truth annotations; only the deployment log was available.")
    if synthetic_date:
        limitations.append(
            f"The calendar date {synthetic_date} is synthetic — fabricated so date-scoped "
            "questions have an anchor; it is not real ground truth."
        )
    if summary.get("tick_rate_assumed"):
        limitations.append(
            "Durations assume the empirically observed 10 ticks/second rate; the API does not guarantee it."
        )
    if errors:
        limitations.append(f"{len(errors)} upstream request(s) failed, so the evidence may be incomplete.")
    confidence = 0.9 if summary.get("has_ground_truth") or positions_actors else 0.6
    if errors:
        confidence = round(confidence * 0.85, 2)
    return {
        "answer": text, "text": text, "chatAnswer": text, "chat_answer": text,
        "producer_agent": agent_id,
        "confidence": confidence,
        "evidence_refs": [f"{evidence.get('producer_agent')}:{trace_id}"],
        "limitations": limitations,
        "source_count": int(evidence.get("source_count") or 0),
        "evidence_used_count": 1,
        "recording": {"traceId": trace_id},
        "cameras": [],
        "selection": selection,
        "simulation": {key: value for key, value in summary.items() if key not in {"occupancySamples", "walkerOccupancySamples", "entities"}},
        "actorPositions": actor_positions,
        "occupancyTimeline": summary.get("occupancySamples") or [],
        "errors": errors,
    }


def _retrieve_generic(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    raw = str(request.get("raw_data_json") or "").strip()
    if raw:
        data = json.loads(raw)
        source_url = "inline-json"
    else:
        source_url = str(request.get("source_url") or "").strip()
        data = _request_json_object(
            source_url,
            timeout=int(request.get("timeout_seconds") or 30),
            max_bytes=int(request.get("max_bytes") or 50 * 1024 * 1024),
        )
    return {
        "kind": "tracefix.agent.evidence.v1",
        "producer_agent": agent_id,
        "source_kind": "json" if raw else "http",
        "source_url": source_url,
        "question": str(request.get("question") or ""),
        "fetched_at": _now(),
        "source_count": 1,
        "data": data,
        "errors": [],
    }


def _retrieve_generic_agent(request: dict[str, Any], agent_id: str, provider: str, model: str) -> dict[str, Any]:
    question = str(request.get("question") or "").strip()
    tools = {"read_source_json": "Read the single configured JSON data source."}
    transcript: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    evidence: dict[str, Any] | None = None
    summary = ""
    limitations: list[str] = []
    normalized_model = model
    for step in range(1, 7):
        action, normalized_model = _agent_action(
            provider=provider,
            model=model,
            agent_id=agent_id,
            question=question,
            tools=tools,
            transcript=transcript,
        )
        action_name = str(action.get("action") or "").strip().lower()
        if action_name == "finish":
            if evidence is None:
                raise ValueError("generated retrieval agent attempted to finish before reading its data source")
            summary = str(action.get("summary") or "").strip()
            raw_limitations = action.get("limitations")
            if isinstance(raw_limitations, list):
                limitations = [str(item) for item in raw_limitations if str(item).strip()]
            break
        tool = str(action.get("tool") or "").strip()
        if action_name != "tool" or tool not in tools:
            raise ValueError(f"generated retrieval agent requested an unapproved action or tool: {tool or action_name or '(empty)'}")
        evidence = _retrieve_generic(request, agent_id)
        trace_item = {"step": step, "tool": tool, "arguments": {}, "status": "completed"}
        tool_trace.append(trace_item)
        transcript.append({
            "action": trace_item,
            "observation": {
                "source_kind": evidence["source_kind"],
                "data": evidence["data"],
            },
        })
    else:
        raise ValueError("generated retrieval agent exceeded the 6-step tool limit")
    return {
        **(evidence or {}),
        "agent_summary": summary,
        "agent_limitations": limitations,
        "runtime_provider": provider,
        "runtime_model": normalized_model,
        "generation_mode": "llm_tool_agent",
        "tool_trace": tool_trace,
    }


_CARLA_MODES = {"carla", "simulation", "carla-trace-server", "carla_trace_server"}


def retrieve(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    source_mode = str(request.get("source_mode") or "auto").lower()
    source_url = str(request.get("source_url") or "").lower()
    provider = str(request.get("agent_provider") or "deterministic").strip().lower()
    model = str(request.get("agent_model") or "").strip()
    if source_mode in _CARLA_MODES:
        # Trace selection is deterministic (first scenario trace unless the
        # question names one); the LLM still writes the final answer wording.
        return _retrieve_carla(request, agent_id)
    if source_mode == "smartroom" or (source_mode == "auto" and "/api/v1" in source_url):
        if provider != "deterministic":
            return _retrieve_smartroom_agent(request, agent_id, provider, model)
        return _retrieve_smartroom(request, agent_id)
    if provider != "deterministic":
        return _retrieve_generic_agent(request, agent_id, provider, model)
    return _retrieve_generic(request, agent_id)


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _activity_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _concurrent_counts_from_tracks(tracks: list[Any]) -> dict[str, int]:
    """Most tracks simultaneously classified with each action at one sampled instant.

    Counting distinct track IDs over a whole recording conflates one person the
    tracker re-acquired N times with N people, so co-occurrence at a shared
    timestamp is used instead: two IDs only count as two people if the classifier
    saw them doing the thing at the same moment.
    """
    per_label: dict[str, dict[float, set[str]]] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("trackId") or "").strip()
        timeline = track.get("timeline")
        if not track_id or not isinstance(timeline, list):
            continue
        for point in timeline:
            if not isinstance(point, dict) or point.get("kept") is False:
                continue
            label = _activity_key(point.get("action"))
            stamp = point.get("t")
            if not label or not isinstance(stamp, (int, float)):
                continue
            per_label.setdefault(label, {}).setdefault(round(float(stamp), 3), set()).add(track_id)
    return {
        label: max((len(ids) for ids in stamps.values()), default=0)
        for label, stamps in per_label.items()
    }


def _downsample_series(samples: dict[float, int], max_points: int = 180) -> list[dict[str, float]]:
    """Reduce a dense count series to a plottable number of points.

    Buckets take the median, not the maximum. The detector's per-frame count
    flickers by one or two people constantly, and carrying the maximum through
    turns that noise into a spike on the chart. A bucket spans well under a
    second, so anything a viewer would call a change still survives; only
    single-frame jitter is dropped.
    """
    if not samples:
        return []
    stamps = sorted(samples)
    span = stamps[-1] - stamps[0]
    if len(stamps) <= max_points or span <= 0:
        return [{"t": round(stamp, 2), "count": int(samples[stamp])} for stamp in stamps]
    width = span / max_points
    buckets: dict[int, list[int]] = {}
    for stamp in stamps:
        index = min(max_points - 1, int((stamp - stamps[0]) / width))
        buckets.setdefault(index, []).append(int(samples[stamp]))
    return [
        {"t": round(stamps[0] + index * width, 2), "count": int(median(sorted(values)))}
        for index, values in sorted(buckets.items())
    ]


def _segment_counts_from_tracks(tracks: list[Any]) -> dict[str, int]:
    """Peak overlap of per-track action segments, for data with no sample timeline."""
    intervals: dict[str, list[tuple[float, float]]] = {}
    for track in tracks:
        if not isinstance(track, dict):
            continue
        for segment in track.get("segments") or []:
            if not isinstance(segment, dict):
                continue
            label = _activity_key(segment.get("action"))
            start, end = segment.get("start"), segment.get("end")
            if not label or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            # Give degenerate (zero-length) segments a sliver of duration, otherwise
            # the close-before-open ordering below cancels them out to nothing.
            intervals.setdefault(label, []).append((float(start), max(float(end), float(start) + 1e-6)))
    counts: dict[str, int] = {}
    for label, spans in intervals.items():
        events: list[tuple[float, int]] = []
        for start, end in spans:
            events.append((start, 1))
            events.append((end, -1))
        # Close before open at equal timestamps so abutting segments are not an overlap.
        events.sort(key=lambda item: (item[0], item[1]))
        current = peak = 0
        for _, delta in events:
            current += delta
            peak = max(peak, current)
        counts[label] = peak
    return counts


def _geo_occupancy_series(
    camera: dict[str, Any],
    duration_sec: float = 0.0,
    remap: Any = None,
) -> dict[float, int] | None:
    """Depth-verified occupancy series from the geo model's room-frame centroids.

    Per-frame detector counts include anything person-shaped in the image —
    people on a TV screen, someone visible through a window, reflections — so
    they overstate how many people are physically in the room. The geo pipeline
    only emits a centroid when a detection has a consistent depth inside the
    calibrated room, which makes concurrent geo tracks the authoritative
    occupancy signal whenever they are present.
    """
    step = 0.2
    tracks_by_bucket: dict[float, set[str]] = {}
    inference = camera.get("inference") if isinstance(camera.get("inference"), dict) else {}
    for model, wrapper in inference.items():
        if "geo" not in str(model).lower():
            continue
        data = wrapper.get("data") if isinstance(wrapper, dict) else {}
        centroids = data.get("centroids") if isinstance(data, dict) else None
        persons = centroids.get("persons") if isinstance(centroids, dict) else None
        if not isinstance(persons, dict):
            continue
        for track_id, samples in persons.items():
            if not isinstance(samples, list) or not samples:
                continue
            # A hip-based centroid means the pipeline saw a full body and anchored
            # it to the floor. Shoulder-based centroids are its fallback for
            # partially visible figures — which is what someone behind the window
            # or on the TV screen looks like — so a track that is mostly
            # shoulder-based is not someone standing in the room.
            hip_samples = sum(
                1 for sample in samples
                if isinstance(sample, dict) and str(sample.get("src") or "").startswith("depth-hip")
            )
            if hip_samples * 2 < len(samples):
                continue
            for sample in samples:
                stamp = sample.get("t") if isinstance(sample, dict) else None
                if isinstance(stamp, (int, float)):
                    # Geo `t` is sidecar container time like every inference JSON;
                    # remap to real recording seconds when the caller has a mapper.
                    real = float(remap(float(stamp))) if remap else float(stamp)
                    bucket = round(round(real / step) * step, 2)
                    tracks_by_bucket.setdefault(bucket, set()).add(str(track_id))
    if not tracks_by_bucket:
        return None
    # Zero-fill the whole recording so an empty room reads as 0 rather than the
    # series silently stopping at the last sighting.
    end = max(max(tracks_by_bucket), float(duration_sec or 0.0))
    series: dict[float, int] = {}
    index = 0
    stamp = 0.0
    while stamp <= end + 1e-9:
        series[round(stamp, 2)] = len(tracks_by_bucket.get(round(stamp, 2), ()))
        index += 1
        stamp = index * step
    return series


def _camera_summary(camera_name: str, camera: dict[str, Any]) -> dict[str, Any]:
    latest: int | None = None
    peak: int | None = None
    frames_analyzed = 0
    duration_sec = 0.0
    activities: set[str] = set()
    activity_tracks: dict[str, set[str]] = {}
    concurrent_counts: dict[str, int] = {}
    segment_counts: dict[str, int] = {}
    occupancy_samples: dict[float, int] = {}
    pose_available = False
    inference = camera.get("inference") if isinstance(camera.get("inference"), dict) else {}
    endpoints: dict[str, str] = {}
    for model, wrapper in inference.items():
        if "pose" in str(model).lower():
            pose_available = True
        if isinstance(wrapper, dict) and wrapper.get("url"):
            endpoints[str(model)] = str(wrapper["url"])
        data = wrapper.get("data") if isinstance(wrapper, dict) else {}
        tracks = data.get("tracks") if isinstance(data, dict) else None
        if isinstance(tracks, list):
            for label, count in _concurrent_counts_from_tracks(tracks).items():
                concurrent_counts[label] = max(concurrent_counts.get(label, 0), count)
                activities.add(label)
            for label, count in _segment_counts_from_tracks(tracks).items():
                segment_counts[label] = max(segment_counts.get(label, 0), count)
                activities.add(label)
        for node in _walk(data):
            if not isinstance(node, dict):
                continue
            reported_peak = node.get("maxPersons")
            if isinstance(reported_peak, (int, float)):
                peak = max(peak or 0, int(reported_peak))
            timeline = node.get("timeline")
            counts = [
                int(item["count"]) for item in timeline
                if isinstance(item, dict) and isinstance(item.get("count"), (int, float))
            ] if isinstance(timeline, list) else []
            if counts:
                latest = counts[-1]
                peak = max(peak or 0, max(counts))
                # Retained at full resolution for the occupancy chart; the answer
                # itself still reports a single number.
                for item in timeline:
                    if not isinstance(item, dict):
                        continue
                    stamp, count = item.get("t"), item.get("count")
                    if isinstance(stamp, (int, float)) and isinstance(count, (int, float)):
                        rounded = round(float(stamp), 2)
                        occupancy_samples[rounded] = max(occupancy_samples.get(rounded, 0), int(count))
            analyzed = node.get("framesAnalyzed")
            # Only count frames from nodes that actually detect people. Auxiliary
            # models (depth/geometry) sample far more densely and would otherwise
            # overstate how much of the recording the counting models examined.
            if isinstance(analyzed, (int, float)) and (counts or reported_peak is not None):
                frames_analyzed = max(frames_analyzed, int(analyzed))
            reported_duration = node.get("durationSec")
            if isinstance(reported_duration, (int, float)):
                duration_sec = max(duration_sec, float(reported_duration))
            for key in ("actions", "poses", "labels"):
                values = node.get(key)
                if isinstance(values, list):
                    activities.update(str(item).strip().lower() for item in values if str(item).strip())
            track_actions = node.get("trackActions")
            if isinstance(track_actions, dict):
                for track_id, label in track_actions.items():
                    normalized = _activity_key(label)
                    if normalized:
                        activities.add(normalized)
                        activity_tracks.setdefault(normalized, set()).add(str(track_id))
            persons = node.get("persons")
            labels = node.get("labels")
            if isinstance(persons, list) and isinstance(labels, list):
                pose_available = pose_available or bool(persons)
                ids = {str(person.get("id", index + 1)) for index, person in enumerate(persons) if isinstance(person, dict)}
                for label in labels:
                    normalized = _activity_key(label)
                    if normalized:
                        activities.add(normalized)
                        activity_tracks.setdefault(normalized, set()).update(ids)

    metadata = camera.get("metadata") if isinstance(camera.get("metadata"), dict) else {}
    if isinstance(metadata.get("durationSec"), (int, float)):
        duration_sec = max(duration_sec, float(metadata["durationSec"]))
    # Sidecar `t` is container time, and live-segment containers are encoded
    # blind-CFR with only the frames that actually arrived — so the stretch
    # differs per camera. The timestamps CSV carries each frame's hardware-clock
    # time; remap onto it so every camera reports real recording seconds.
    frame_seconds = [
        float(item) for item in camera.get("frameSeconds") or []
        if isinstance(item, (int, float))
    ]
    remap = None
    if len(frame_seconds) >= 2 and duration_sec > 0:
        container_step = duration_sec / len(frame_seconds)

        def remap(stamp: float, _step: float = container_step, _frames: list[float] = frame_seconds) -> float:
            index = int(round(float(stamp) / _step)) if _step > 0 else 0
            return _frames[max(0, min(len(_frames) - 1, index))]

        if occupancy_samples:
            remapped: dict[float, int] = {}
            for stamp, count in occupancy_samples.items():
                real = round(remap(stamp), 2)
                remapped[real] = max(remapped.get(real, 0), count)
            occupancy_samples = remapped
        duration_sec = frame_seconds[-1]
    geo_series = _geo_occupancy_series(camera, duration_sec, remap=remap)
    if geo_series:
        # Depth-verified counts override the detectors' pixel counts: YOLO also
        # counts people on the TV screen and through the window, which is how a
        # two-person room gets reported as six.
        peak = max(geo_series.values())
        latest = geo_series[max(geo_series)]
        occupancy_samples = geo_series
    elif occupancy_samples:
        # Keep the reported single number consistent with the plotted series
        # instead of whichever model's timeline happened to be walked last.
        latest = occupancy_samples[max(occupancy_samples)]

    # The recordings listing carries per-clip `objects` (COCO class → sampled-frame
    # stats) and `sound` (AudioSet label → window stats) indexes. An empty objects
    # map means "not analysed for objects", never "nothing was in the room".
    objects_index: dict[str, dict[str, Any]] = {}
    raw_objects = metadata.get("objects")
    if isinstance(raw_objects, dict):
        for name, stats in raw_objects.items():
            if not isinstance(stats, dict):
                continue
            entry = {
                key: stats.get(key)
                for key in ("frames", "maxPerFrame", "avgPerFrame", "peakConf")
                if isinstance(stats.get(key), (int, float))
            }
            if isinstance(stats.get("frames"), (int, float)) and frames_analyzed:
                entry["visibleFraction"] = round(float(stats["frames"]) / frames_analyzed, 3)
            objects_index[str(name)] = entry
    sounds_index: dict[str, dict[str, Any]] = {}
    raw_sound = metadata.get("sound")
    if isinstance(raw_sound, dict):
        for label, stats in raw_sound.items():
            if isinstance(stats, dict):
                sounds_index[str(label)] = {
                    key: stats.get(key)
                    for key in ("windows", "peakProb")
                    if isinstance(stats.get(key), (int, float))
                }

    union_counts = {label: len(ids) for label, ids in activity_tracks.items()}
    activity_counts: dict[str, int] = {}
    count_methods: dict[str, str] = {}
    for label in set(union_counts) | set(concurrent_counts) | set(segment_counts):
        if concurrent_counts.get(label):
            value, method = concurrent_counts[label], "concurrent"
        elif segment_counts.get(label):
            value, method = segment_counts[label], "segments"
        else:
            value, method = union_counts.get(label, 0), "union"
        # However the label was counted, it cannot involve more people than the
        # person detector ever saw in the room at once.
        if peak is not None and value > peak:
            value, method = peak, method + "-clamped"
        activity_counts[label] = value
        count_methods[label] = method

    return {
        "camera": camera_name,
        "peakPeople": peak,
        "lastPeople": latest,
        "occupancyVerified": bool(geo_series),
        "objects": objects_index,
        "sounds": sounds_index,
        "framesAnalyzed": frames_analyzed,
        "durationSec": round(duration_sec, 3) if duration_sec else None,
        "activities": sorted(activities),
        "activityCounts": dict(sorted(activity_counts.items())),
        "activityCountMethods": dict(sorted(count_methods.items())),
        "activityCountsUnion": dict(sorted(union_counts.items())),
        "occupancySamples": [
            {"t": stamp, "count": occupancy_samples[stamp]} for stamp in sorted(occupancy_samples)
        ],
        "activityTrackIds": {label: sorted(ids) for label, ids in sorted(activity_tracks.items())},
        "endpoints": endpoints,
        "pose": {"available": pose_available},
    }


def _count_label(value: int) -> str:
    return f"{value} {'person' if value == 1 else 'people'}"


def _merge_occupancy_samples(cameras: list[dict[str, Any]]) -> list[dict[str, float]]:
    """One occupancy series for the recording, plottable as a line.

    Cameras cover different spans and see different parts of the room, so the
    highest count at each instant wins: a camera that cannot see a corner should
    not drag the room total down below what another camera plainly observed.
    """
    merged: dict[float, int] = {}
    for camera in cameras:
        for point in camera.get("occupancySamples") or []:
            stamp, count = point.get("t"), point.get("count")
            if isinstance(stamp, (int, float)) and isinstance(count, (int, float)):
                merged[float(stamp)] = max(merged.get(float(stamp), 0), int(count))
    # The chart ends where observation ends. A wall camera's video can run past
    # the last person-tracking coverage, but padding that stretch with zeros
    # would assert an empty room nobody actually observed.
    return _downsample_series(merged)


def _camera_label(value: str) -> str:
    return re.sub(r"(?<=\D)(?=\d)", " ", value.replace("_", " ")).strip()


_COUNT_METHOD_FACTORS = {
    "concurrent": 1.0,
    "segments": 0.85,
    "concurrent-clamped": 0.5,
    "union": 0.45,
    "segments-clamped": 0.45,
    "union-clamped": 0.4,
}


def _derive_confidence(
    cameras: list[dict[str, Any]],
    requested_labels: list[str],
    errors: list[Any],
) -> tuple[float, list[str]]:
    """Score the evidence instead of asserting a constant.

    Four independent things undermine a count: how it was derived, whether cameras
    watching the same room agree, how much of the recording was analyzed, and
    whether any upstream request failed. Each contributes a factor, and every
    deduction produces a note that becomes a stated limitation.
    """
    if not cameras:
        return 0.0, ["No camera returned usable inference data."]

    notes: list[str] = []

    method_factor = 1.0
    for label in requested_labels:
        for camera in cameras:
            method = (camera.get("activityCountMethods") or {}).get(label)
            if not method:
                continue
            method_factor = min(method_factor, _COUNT_METHOD_FACTORS.get(method, 0.5))
            if method.endswith("-clamped"):
                notes.append(
                    f"The {label} count exceeded observed occupancy and was capped at the peak "
                    "number of people the detector saw at once."
                )
            elif method == "union":
                notes.append(
                    f"The {label} count comes from distinct tracking segments, which can exceed "
                    "the number of distinct people."
                )

    if requested_labels:
        values = [
            int((camera.get("activityCounts") or {}).get(label, 0))
            for label in requested_labels
            for camera in cameras
            if label in (camera.get("activityCounts") or {})
        ]
    else:
        values = [int(camera["lastPeople"]) for camera in cameras if camera.get("lastPeople") is not None]

    agreement_factor = 1.0
    if len(values) > 1 and max(values) > 0:
        spread = max(values) - min(values)
        agreement_factor = max(0.5, 1.0 - spread / max(values))
        if spread:
            notes.append(
                f"Cameras covering the same recording disagreed (values {min(values)} to {max(values)})."
            )

    frames = max((int(camera.get("framesAnalyzed") or 0) for camera in cameras), default=0)
    if frames >= 100:
        coverage_factor = 1.0
    elif frames >= 30:
        coverage_factor = 0.85
        notes.append(f"Only {frames} frames of this recording were analyzed.")
    else:
        coverage_factor = 0.6
        notes.append(
            f"Sparse coverage: {frames} analyzed frames." if frames
            else "The inference data reported no analyzed frame count."
        )

    error_factor = 1.0
    if errors:
        error_factor = 0.7
        notes.append(f"{len(errors)} upstream request(s) failed, so the evidence may be incomplete.")

    score = 0.95 * method_factor * agreement_factor * coverage_factor * error_factor
    return round(max(0.0, min(1.0, score)), 2), list(dict.fromkeys(notes))


def _smartroom_answer(evidence: dict[str, Any], agent_id: str) -> dict[str, Any]:
    question = str(evidence.get("question") or "")
    selection = evidence.get("selection") if isinstance(evidence.get("selection"), dict) else {}
    selected = evidence.get("selected") if isinstance(evidence.get("selected"), dict) else None
    evidence_ref = f"{evidence.get('producer_agent')}:recording-index"
    aggregate_selected = [
        item for item in evidence.get("aggregate_selected") or [] if isinstance(item, dict)
    ]
    if selection.get("mode") == "requested_date_total" and aggregate_selected:
        recording_peaks: list[int] = []
        aggregate_cameras: list[dict[str, Any]] = []
        for packet in aggregate_selected:
            cameras_raw = packet.get("cameras") if isinstance(packet.get("cameras"), dict) else {}
            camera_summaries = [
                _camera_summary(name, value if isinstance(value, dict) else {})
                for name, value in cameras_raw.items()
            ]
            verified_summaries = [camera for camera in camera_summaries if camera.get("occupancyVerified")]
            counting_summaries = verified_summaries or camera_summaries
            peaks = [camera["peakPeople"] for camera in counting_summaries if camera.get("peakPeople") is not None]
            recording_peaks.append(max(peaks, default=0))
            aggregate_cameras.extend(camera_summaries)
        total_peak = sum(recording_peaks)
        requested = str(selection.get("requestedDateLabel") or "the requested date")
        text = (
            f"For {requested}, the summed per-take peak occupancy was "
            f"{_count_label(total_peak)} across {len(aggregate_selected)} recordings."
        )
        return {
            "answer": text,
            "text": text,
            "chatAnswer": text,
            "chat_answer": text,
            "producer_agent": agent_id,
            "confidence": 1.0,
            "evidence_refs": [evidence_ref],
            "limitations": ["Per-take peaks are summed; this is not a unique-person count."],
            "source_count": int(evidence.get("source_count") or 0),
            "evidence_used_count": len(aggregate_selected),
            "recording": None,
            "recordingsAggregated": len(aggregate_selected),
            "aggregatePeakPeople": total_peak,
            "cameras": aggregate_cameras,
            "selection": selection,
            "errors": evidence.get("errors") or [],
        }
    if selected is None:
        if selection.get("needsClarification"):
            prompt = str(selection.get("clarificationPrompt") or "Choose a recording to continue.")
            candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
            return {
                "answer": prompt,
                "text": prompt,
                "chatAnswer": prompt,
                "chat_answer": prompt,
                "producer_agent": agent_id,
                "confidence": 1.0,
                "evidence_refs": [evidence_ref],
                "limitations": ["A recording must be selected before evidence retrieval can continue."],
                "source_count": int(evidence.get("source_count") or 0),
                "evidence_used_count": 1,
                "recording": None,
                "cameras": [],
                "selection": selection,
                "needsClarification": True,
                "clarificationPrompt": prompt,
                "clarificationCandidates": candidates,
                "errors": evidence.get("errors") or [],
            }
        requested = str(selection.get("requestedDateLabel") or "the requested date")
        available = [str(item) for item in selection.get("availableDates", [])]
        text = f"No smartroom recording matched {requested}."
        if available:
            text += " Available recording dates include " + ", ".join(available[:8]) + "."
        return {
            "answer": text,
            "text": text,
            "chatAnswer": text,
            "chat_answer": text,
            "producer_agent": agent_id,
            "confidence": 1.0,
            "evidence_refs": [evidence_ref],
            "limitations": ["No recording was available for the requested date."],
            "source_count": int(evidence.get("source_count") or 0),
            "evidence_used_count": 1,
            "recording": None,
            "cameras": [],
            "selection": selection,
            "errors": evidence.get("errors") or [],
        }
    cameras_raw = selected.get("cameras") if isinstance(selected.get("cameras"), dict) else {}
    cameras = [_camera_summary(name, value if isinstance(value, dict) else {}) for name, value in cameras_raw.items()]
    # Depth-verified cameras are the authoritative occupancy signal; plain
    # webcams fall back to raw detector counts, which also count people on the
    # TV screen or through the window. Never let those outvote a verified count.
    verified_cameras = [camera for camera in cameras if camera.get("occupancyVerified")]
    counting_cameras = verified_cameras or cameras
    known_labels = sorted({label for camera in cameras for label in camera["activities"]}, key=lambda label: question.lower().find(label) if label in question.lower() else 10_000)
    requested_labels = [label for label in known_labels if label in question.lower()]
    day = str(selected.get("day") or "")
    rec = str(selected.get("rec") or "")
    requested_date = str(selection.get("requestedDateLabel") or "").strip()
    prefix = f"For {requested_date} ({day} / {rec}), " if requested_date else f"For the selected recording ({day} / {rec}), "
    lower_question = question.lower()
    occupancy_timeline = _merge_occupancy_samples(counting_cameras)
    media_notes: list[str] = []
    # Object classes and sound labels the listing indexed for this recording;
    # 'person' stays with the occupancy path, which is depth-verified.
    known_objects = sorted({
        str(name) for camera in cameras for name in (camera.get("objects") or {})
        if str(name).lower() != "person"
    })
    requested_objects = [
        name for name in known_objects
        if re.search(rf"\b{re.escape(name.lower())}(?:s|es)?\b", lower_question)
    ]
    _SOUND_ALIASES = {
        "speech": ("talk", "talking", "speak", "speaking", "conversation", "voice", "said", "say"),
        "music": ("music", "song"),
        "door": ("door",),
        "typing": ("typing", "typed"),
    }
    known_sounds = sorted({str(label) for camera in cameras for label in (camera.get("sounds") or {})})
    requested_sounds = []
    for label in known_sounds:
        words = {label.lower(), *_SOUND_ALIASES.get(label.lower(), ())}
        if any(re.search(rf"\b{re.escape(word)}\b", lower_question) for word in words):
            requested_sounds.append(label)
    generic_sound_query = bool(re.search(r"\b(sounds?|hear|heard|audio|noises?|loud)\b", lower_question))
    combined = len(requested_labels) > 1 and " both " in f" {lower_question} "
    combination: dict[str, Any] | None = None
    if combined:
        track_sets = []
        for label in requested_labels:
            ids = {track_id for camera in cameras for track_id in camera["activityTrackIds"].get(label, [])}
            track_sets.append(ids)
        exact_ids = set.intersection(*track_sets) if track_sets and all(track_sets) else set()
        count = len(exact_ids)
        combination = {
            "labels": requested_labels,
            "exact": bool(track_sets) and all(bool(ids) for ids in track_sets),
            "count": count,
            "byCamera": [
                {
                    "camera": camera["camera"],
                    "trackIds": sorted(set.intersection(*[
                        set(camera["activityTrackIds"].get(label, []))
                        for label in requested_labels
                    ])) if all(camera["activityTrackIds"].get(label) for label in requested_labels) else [],
                    "exact": all(bool(camera["activityTrackIds"].get(label)) for label in requested_labels),
                }
                for camera in cameras
            ],
        }
        text = prefix + f"{_count_label(count)} were both " + " and ".join(requested_labels) + "."
    elif requested_labels:
        parts = []
        for label in requested_labels:
            count = max([camera["activityCounts"].get(label, 0) for camera in cameras] or [0])
            parts.append(f"{label}: {_count_label(count)}")
        text = prefix + ", ".join(parts) + "."
    elif requested_objects or requested_sounds or generic_sound_query:
        parts = []
        for name in requested_objects:
            best: dict[str, Any] = {}
            for camera in cameras:
                stats = (camera.get("objects") or {}).get(name)
                if isinstance(stats, dict):
                    for key in ("maxPerFrame", "visibleFraction", "peakConf"):
                        value = stats.get(key)
                        if isinstance(value, (int, float)):
                            best[key] = max(best.get(key, 0), value)
            detail: list[str] = []
            if best.get("maxPerFrame"):
                detail.append(f"up to {int(best['maxPerFrame'])} at once")
            if best.get("visibleFraction"):
                detail.append(f"visible in about {round(float(best['visibleFraction']) * 100)}% of sampled frames")
            if best.get("peakConf"):
                detail.append(f"peak confidence {round(float(best['peakConf']), 2)}")
            parts.append(f"a {name} was detected ({', '.join(detail)})" if detail else f"a {name} was detected")
        if requested_sounds:
            for label in requested_sounds:
                best_windows = 0
                best_prob = 0.0
                for camera in cameras:
                    stats = (camera.get("sounds") or {}).get(label)
                    if isinstance(stats, dict):
                        best_windows = max(best_windows, int(stats.get("windows") or 0))
                        best_prob = max(best_prob, float(stats.get("peakProb") or 0.0))
                parts.append(
                    f"{label} was heard in {best_windows} analysis window(s)"
                    + (f" (peak probability {round(best_prob, 2)})" if best_prob else "")
                )
        elif generic_sound_query:
            ranked = sorted(
                ((label, stats) for camera in cameras for label, stats in (camera.get("sounds") or {}).items()),
                key=lambda item: int((item[1] or {}).get("windows") or 0),
                reverse=True,
            )
            seen_labels: list[str] = []
            for label, stats in ranked:
                if label not in seen_labels:
                    seen_labels.append(label)
                if len(seen_labels) >= 3:
                    break
            if seen_labels:
                parts.append("the most frequent sound events were " + ", ".join(seen_labels))
            else:
                parts.append("no confident sound events were recorded")
        if requested_sounds or generic_sound_query:
            media_notes.append(
                "Sound events are room-level: one microphone covers the room, so they cannot "
                "say who made a sound or where."
            )
        if requested_objects:
            media_notes.append(
                "Object detections are sampled at 5 Hz and dropped below the detector's "
                "confidence threshold; counts are per-frame, not unique objects."
            )
        text = prefix + "; ".join(parts) + "."
    else:
        latest = [camera["lastPeople"] for camera in counting_cameras if camera["lastPeople"] is not None]
        peaks = [camera["peakPeople"] for camera in counting_cameras if camera.get("peakPeople") is not None]
        if latest and peaks:
            text = prefix + (
                f"occupancy peaked at {_count_label(max(peaks))} and the recording ended with "
                f"{_count_label(max(latest))} in the room."
            )
        elif latest:
            text = prefix + f"the latest available room-level reading showed {_count_label(max(latest))}."
        else:
            text = prefix + "the generated agent found no usable occupancy count in this recording."
        activity_labels = sorted({label for camera in cameras for label in camera["activities"]})
        if activity_labels:
            text += " Detected activities included " + ", ".join(activity_labels) + "."
    counts = {label: max([camera["activityCounts"].get(label, 0) for camera in cameras] or [0]) for label in requested_labels}
    packet_errors = evidence.get("errors") or []
    confidence, confidence_notes = _derive_confidence(counting_cameras, requested_labels, packet_errors)
    excluded_notes: list[str] = []
    if verified_cameras and len(verified_cameras) < len(cameras):
        skipped = ", ".join(camera["camera"] for camera in cameras if not camera.get("occupancyVerified"))
        excluded_notes.append(
            f"Occupancy comes from the depth-verified camera(s) only; raw detector counts from {skipped} "
            "were not used because they can include people on screens or seen through windows."
        )
    return {
        "answer": text,
        "text": text,
        "chatAnswer": text,
        "chat_answer": text,
        "producer_agent": agent_id,
        "confidence": confidence,
        "evidence_refs": [f"{evidence.get('producer_agent')}:{rec}:{camera['camera']}" for camera in cameras],
        "limitations": [
            "Counts are bounded by the available aggregate inference data.",
            *media_notes,
            *excluded_notes,
            *confidence_notes,
        ],
        "source_count": int(evidence.get("source_count") or 0),
        "evidence_used_count": len(cameras),
        "recording": {"day": day, "rec": rec},
        "cameras": cameras,
        "selection": selection,
        "requestedActivities": requested_labels,
        "requestedActivityCounts": counts,
        "occupancyTimeline": occupancy_timeline,
        "requestedActivityCombination": combination,
        "errors": evidence.get("errors") or [],
    }


def synthesize(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    packets = [item for item in request.get("evidence_packets", []) if isinstance(item, dict)]
    if not packets:
        raise ValueError("answer synthesis requires at least one agent evidence packet")
    smartroom = next((item for item in packets if item.get("source_kind") == "smartroom-control"), None)
    carla = next((item for item in packets if item.get("source_kind") == "carla-trace-server"), None)
    if smartroom:
        draft = _smartroom_answer(smartroom, agent_id)
    elif carla:
        draft = _carla_answer(carla, agent_id)
    else:
        evidence_refs = [f"{item.get('producer_agent')}:json" for item in packets]
        text = "The generated agent retrieved the approved structured data, but no domain-specific answer rule matched the request."
        draft = {
            "answer": text,
            "text": text,
            "chatAnswer": text,
            "chat_answer": text,
            "producer_agent": agent_id,
            "confidence": 0.0,
            "evidence_refs": evidence_refs,
            "limitations": ["No domain-specific answer rule matched the structured payload."],
            "source_count": sum(int(item.get("source_count") or 0) for item in packets),
            "evidence_used_count": len(packets),
            "recording": None,
            "cameras": [],
        }
    provider = str(request.get("agent_provider") or "deterministic").strip().lower()
    model = str(request.get("agent_model") or "").strip()
    if provider == "deterministic":
        return {
            **draft,
            "runtime_provider": "deterministic",
            "runtime_model": None,
            "generation_mode": "deterministic",
        }
    return _model_answer(
        provider=provider,
        model=model,
        question=str(request.get("question") or ""),
        draft=draft,
    )


def _monitor_system_prompt(agent_id: str) -> str:
    return (
        f"You are {agent_id}, the independent TraceFix runtime protocol monitor. "
        "TraceFix deployed you before the generated agents to supervise their communication until "
        "the answer message is delivered. You monitor protocol behavior only: never answer the user, "
        "judge answer correctness, revise answer content, or assess whether the factual conclusion is good. "
        "Treat the supplied verified protocol artifacts as authoritative. Inspect the observed "
        "communication transcript in sequence and decide whether it conforms to the verified "
        "agent topology, allowed communication edges, message labels, state transitions, resource "
        "constraints, and completion conditions. Evaluate the current event before the receiving agent "
        "is allowed to continue. Confirm that no failed or unapproved communication or tool action was "
        "accepted. Do not invent missing events or infer compliance when the observed protocol history "
        "is insufficient. Return only one JSON object with: valid (boolean), "
        "protocol_completion (boolean), explanation (string), checked_rules (array of strings), and "
        "violations (array of objects containing rule, event_sequence, and message). A violation of "
        "the verified protocol must make valid false."
    )


def _protocol_monitor(
    *,
    request: dict[str, Any],
    agent_id: str,
    provider: str,
    model: str,
    preflight_errors: list[str],
) -> dict[str, Any]:
    protocol_context = _monitor_protocol_context()
    mode = str(request.get("monitor_mode") or "complete").strip().lower()
    transcript = request.get("communication_transcript")
    if not isinstance(transcript, list):
        preflight_errors.append("communication transcript is missing")
        transcript = []
    elif mode in {"event", "complete"} and not transcript and request.get("single_agent_execution") is not True:
        preflight_errors.append("communication transcript is empty during active monitoring")
    current_event = request.get("current_event")
    if mode == "event" and not isinstance(current_event, dict):
        preflight_errors.append("current communication event is missing")
    verification = protocol_context.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        preflight_errors.append("packaged protocol is not marked verified")
    if request.get("single_agent_execution") is True:
        complete = mode == "complete"
        return {
            "kind": "tracefix.agent.monitor.v1", "producer_agent": agent_id,
            "valid": not preflight_errors, "monitor_mode": mode,
            "active": not complete and not preflight_errors,
            "protocol_completion": complete and not preflight_errors,
            "errors": preflight_errors, "violations": [{"rule": "runtime_preflight", "event_sequence": None, "message": item} for item in preflight_errors],
            "checked_rules": ["validate_agent_state_transitions", "validate_protocol_completion"],
            "explanation": "Verified single-agent execution completed without inter-agent communication.",
            "observed_message_count": len(transcript), "verification_status": verification.get("status"),
            "runtime_provider": provider, "runtime_model": model,
            "generation_mode": "verified_single_agent_monitor", "checked_at": _now(),
        }
    try:
        result, normalized_model = _call_model_json(
            provider=provider,
            model=model,
            system_prompt=_monitor_system_prompt(agent_id),
            user_payload={
                "monitor_lifecycle": mode,
                "verified_protocol": protocol_context,
                "observed_communications": transcript,
                "current_event": current_event if isinstance(current_event, dict) else None,
                "deterministic_preflight_errors": preflight_errors,
                "instruction": "Authorize or reject this protocol checkpoint. Do not evaluate answer correctness.",
            },
            max_tokens=900,
        )
        generation_mode = "llm_protocol_monitor"
    except Exception:
        # A malformed model response must not turn an otherwise valid verified
        # protocol checkpoint into an unavailable smart-room answer.
        result = {
            "valid": not preflight_errors,
            "protocol_completion": mode == "complete" and not preflight_errors,
            "explanation": "Deterministic fallback validated the packaged protocol checkpoint.",
            "checked_rules": ["deterministic_protocol_fallback"],
            "violations": [],
        }
        normalized_model = model
        generation_mode = "deterministic_protocol_fallback"
    raw_violations = result.get("violations")
    violations = [item for item in raw_violations if isinstance(item, dict)] if isinstance(raw_violations, list) else []
    for error in preflight_errors:
        violations.append({"rule": "runtime_preflight", "event_sequence": None, "message": error})
    checked_rules = result.get("checked_rules")
    if not isinstance(checked_rules, list):
        checked_rules = []
    model_valid = result.get("valid") is True
    protocol_completion = result.get("protocol_completion") is True
    completion_required = mode == "complete"
    reported_completion = protocol_completion if completion_required else False
    return {
        "kind": "tracefix.agent.monitor.v1",
        "producer_agent": agent_id,
        "valid": model_valid and (protocol_completion or not completion_required) and not violations,
        "monitor_mode": mode,
        "active": mode != "complete" and model_valid and not violations,
        "protocol_completion": reported_completion,
        "errors": [str(item.get("message") or "protocol violation") for item in violations],
        "violations": violations,
        "checked_rules": [str(item) for item in checked_rules],
        "explanation": str(result.get("explanation") or "").strip(),
        "observed_message_count": len(transcript),
        "verification_status": verification.get("status") if isinstance(verification, dict) else None,
        "runtime_provider": provider,
        "runtime_model": normalized_model,
        "generation_mode": generation_mode,
        "checked_at": _now(),
    }


def monitor(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    errors: list[str] = []
    mode = str(request.get("monitor_mode") or "complete").strip().lower()
    transcript = request.get("communication_transcript")
    if mode in {"event", "complete"} and (not isinstance(transcript, list) or not transcript) and request.get("single_agent_execution") is not True:
        errors.append("active protocol monitor requires observed communications")
    if mode == "event" and not isinstance(request.get("current_event"), dict):
        errors.append("active protocol monitor requires the current communication event")
    provider = str(request.get("agent_provider") or "deterministic").strip().lower()
    model = str(request.get("agent_model") or "").strip()
    if provider != "deterministic":
        return _protocol_monitor(
            request=request,
            agent_id=agent_id,
            provider=provider,
            model=model,
            preflight_errors=errors,
        )
    return {
        "kind": "tracefix.agent.monitor.v1",
        "producer_agent": agent_id,
        "valid": not errors,
        "errors": errors,
        "monitor_mode": mode,
        "active": mode != "complete" and not errors,
        "protocol_completion": mode == "complete" and not errors,
        "generation_mode": "deterministic_contract_monitor",
        "checked_at": _now(),
    }


def execute(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    phase = str(request.get("phase") or "").strip().lower()
    if phase == "retrieve":
        return {"ok": True, "phase": phase, "agent_id": agent_id, "evidence_packet": retrieve(request, agent_id)}
    if phase == "synthesize":
        return {"ok": True, "phase": phase, "agent_id": agent_id, "answer_packet": synthesize(request, agent_id)}
    if phase == "single_agent":
        evidence = retrieve(request, agent_id)
        answer = synthesize({**request, "evidence_packets": [evidence]}, agent_id)
        return {"ok": True, "phase": phase, "agent_id": agent_id, "evidence_packet": evidence, "answer_packet": answer}
    if phase in {"monitor", "monitor_start", "monitor_event", "monitor_complete"}:
        verdict = monitor(request, agent_id)
        return {"ok": verdict["valid"], "phase": phase, "agent_id": agent_id, "monitor": verdict}
    raise ValueError(f"unsupported generated-agent phase: {phase or '(empty)'}")


def main() -> int:
    request_path = Path(os.environ.get("TRACEFIX_FRAME_PATH", "")).expanduser()
    agent_id = os.environ.get("TRACEFIX_AGENT_ID", "generated_agent").strip() or "generated_agent"
    try:
        request = _read_json(request_path)
        result = execute(request, agent_id)
    except Exception as exc:  # noqa: BLE001 - structured process failure for the host harness
        result = {"ok": False, "agent_id": agent_id, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
