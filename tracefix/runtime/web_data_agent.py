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


def _request_json(url: str, *, timeout: int, max_bytes: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TraceFix-Generated-Agent/0.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - approved runtime URL
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"agent data response exceeded {max_bytes} bytes")
    value = json.loads(body.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return value


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
    cameras = []
    for index, raw_camera in enumerate(draft.get("cameras", []), start=1):
        if not isinstance(raw_camera, dict):
            continue
        cameras.append({
            "source_index": index,
            "peak_people": raw_camera.get("peakPeople"),
            "latest_people": raw_camera.get("lastPeople"),
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
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = draft.get("confidence", 0.0)
    limitations = result.get("limitations")
    if not isinstance(limitations, list):
        limitations = draft.get("limitations") or []
    return {
        **draft,
        "answer": answer,
        "text": answer,
        "chatAnswer": answer,
        "chat_answer": answer,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "limitations": [str(item) for item in limitations if str(item).strip()],
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
                    "data": _request_json(endpoint, timeout=timeout, max_bytes=max_bytes),
                }
            except Exception as exc:  # noqa: BLE001 - retain partial evidence
                errors.append({"url": endpoint, "error": f"{type(exc).__name__}: {exc}"})
        selected_packet["cameras"][str(camera_name)] = {"metadata": metadata, "inference": inference}
    return selected_packet, errors


def _retrieve_smartroom(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    source_url = str(request.get("source_url") or "").strip()
    question = str(request.get("question") or "").strip()
    timeout = int(request.get("timeout_seconds") or 30)
    max_bytes = int(request.get("max_bytes") or 50 * 1024 * 1024)
    base = _base_url(source_url)
    recordings_doc = _request_json(_api_url(base, "recordings"), timeout=timeout, max_bytes=max_bytes)
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
            recordings_doc = _request_json(_api_url(base, "recordings"), timeout=timeout, max_bytes=max_bytes)
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


def _retrieve_generic(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    raw = str(request.get("raw_data_json") or "").strip()
    if raw:
        data = json.loads(raw)
        source_url = "inline-json"
    else:
        source_url = str(request.get("source_url") or "").strip()
        data = _request_json(
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


def retrieve(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    source_mode = str(request.get("source_mode") or "auto").lower()
    source_url = str(request.get("source_url") or "").lower()
    provider = str(request.get("agent_provider") or "deterministic").strip().lower()
    model = str(request.get("agent_model") or "").strip()
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


def _camera_summary(camera_name: str, camera: dict[str, Any]) -> dict[str, Any]:
    peak: int | None = None
    latest: int | None = None
    activities: set[str] = set()
    activity_tracks: dict[str, set[str]] = {}
    pose_available = False
    inference = camera.get("inference") if isinstance(camera.get("inference"), dict) else {}
    endpoints: dict[str, str] = {}
    for model, wrapper in inference.items():
        if "pose" in str(model).lower():
            pose_available = True
        if isinstance(wrapper, dict) and wrapper.get("url"):
            endpoints[str(model)] = str(wrapper["url"])
        data = wrapper.get("data") if isinstance(wrapper, dict) else {}
        for node in _walk(data):
            if not isinstance(node, dict):
                continue
            timeline = node.get("timeline")
            if isinstance(timeline, list):
                counts = [int(item["count"]) for item in timeline if isinstance(item, dict) and isinstance(item.get("count"), (int, float))]
                if counts:
                    peak = max(peak or 0, max(counts))
                    latest = counts[-1]
            for key in ("actions", "poses", "labels"):
                values = node.get(key)
                if isinstance(values, list):
                    activities.update(str(item).strip().lower() for item in values if str(item).strip())
            track_actions = node.get("trackActions")
            if isinstance(track_actions, dict):
                for track_id, label in track_actions.items():
                    normalized = str(label).strip().lower()
                    if normalized:
                        activities.add(normalized)
                        activity_tracks.setdefault(normalized, set()).add(str(track_id))
            persons = node.get("persons")
            labels = node.get("labels")
            if isinstance(persons, list) and isinstance(labels, list):
                pose_available = pose_available or bool(persons)
                ids = {str(person.get("id", index + 1)) for index, person in enumerate(persons) if isinstance(person, dict)}
                for label in labels:
                    normalized = str(label).strip().lower()
                    if normalized:
                        activities.add(normalized)
                        activity_tracks.setdefault(normalized, set()).update(ids)
                if persons:
                    peak = max(peak or 0, len(persons))
    activity_counts = {label: len(ids) for label, ids in activity_tracks.items()}
    return {
        "camera": camera_name,
        "peakPeople": peak,
        "lastPeople": latest,
        "activities": sorted(activities),
        "activityCounts": dict(sorted(activity_counts.items())),
        "activityTrackIds": {label: sorted(ids) for label, ids in sorted(activity_tracks.items())},
        "endpoints": endpoints,
        "pose": {"available": pose_available},
    }


def _count_label(value: int) -> str:
    return f"{value} {'person' if value == 1 else 'people'}"


def _camera_label(value: str) -> str:
    return re.sub(r"(?<=\D)(?=\d)", " ", value.replace("_", " ")).strip()


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
            peaks = [camera["peakPeople"] for camera in camera_summaries if camera.get("peakPeople") is not None]
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
    known_labels = sorted({label for camera in cameras for label in camera["activities"]}, key=lambda label: question.lower().find(label) if label in question.lower() else 10_000)
    requested_labels = [label for label in known_labels if label in question.lower()]
    day = str(selected.get("day") or "")
    rec = str(selected.get("rec") or "")
    requested_date = str(selection.get("requestedDateLabel") or "").strip()
    prefix = f"For {requested_date} ({day} / {rec}), " if requested_date else f"For the selected recording ({day} / {rec}), "
    lower_question = question.lower()
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
    else:
        peaks = [camera["peakPeople"] for camera in cameras if camera["peakPeople"] is not None]
        latest = [camera["lastPeople"] for camera in cameras if camera["lastPeople"] is not None]
        if peaks:
            camera_parts = [
                f"{_camera_label(camera['camera'])} peaked at {_count_label(int(camera['peakPeople']))}"
                for camera in cameras if camera["peakPeople"] is not None
            ]
            text = prefix + ", and ".join(camera_parts) + "."
            text += f" Across the observed time window, the peak occupancy was {_count_label(max(peaks))} overall."
            if latest:
                text += f" The latest aggregate reading showed {_count_label(max(latest))}."
        else:
            text = prefix + "the generated agent found no usable occupancy counts."
        activity_labels = sorted({label for camera in cameras for label in camera["activities"]})
        if activity_labels:
            text += " Detected activities included " + ", ".join(activity_labels) + "."
    counts = {label: max([camera["activityCounts"].get(label, 0) for camera in cameras] or [0]) for label in requested_labels}
    return {
        "answer": text,
        "text": text,
        "chatAnswer": text,
        "chat_answer": text,
        "producer_agent": agent_id,
        "confidence": 0.9 if cameras else 0.0,
        "evidence_refs": [f"{evidence.get('producer_agent')}:{rec}:{camera['camera']}" for camera in cameras],
        "limitations": ["Counts are bounded by the available aggregate inference data."],
        "source_count": int(evidence.get("source_count") or 0),
        "evidence_used_count": len(cameras),
        "recording": {"day": day, "rec": rec},
        "cameras": cameras,
        "selection": selection,
        "requestedActivities": requested_labels,
        "requestedActivityCounts": counts,
        "requestedActivityCombination": combination,
        "errors": evidence.get("errors") or [],
    }


def synthesize(request: dict[str, Any], agent_id: str) -> dict[str, Any]:
    packets = [item for item in request.get("evidence_packets", []) if isinstance(item, dict)]
    if not packets:
        raise ValueError("answer synthesis requires at least one agent evidence packet")
    smartroom = next((item for item in packets if item.get("source_kind") == "smartroom-control"), None)
    if smartroom:
        draft = _smartroom_answer(smartroom, agent_id)
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
    elif mode in {"event", "complete"} and not transcript:
        preflight_errors.append("communication transcript is empty during active monitoring")
    current_event = request.get("current_event")
    if mode == "event" and not isinstance(current_event, dict):
        preflight_errors.append("current communication event is missing")
    verification = protocol_context.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        preflight_errors.append("packaged protocol is not marked verified")
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
    if mode in {"event", "complete"} and (not isinstance(transcript, list) or not transcript):
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
