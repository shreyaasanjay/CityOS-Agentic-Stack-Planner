"""Optional LLM client adapters for the V0.5 pluggable agent backend."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class LLMClient:
    def complete_json(self, prompt: str) -> Dict[str, Any]:
        raise NotImplementedError


class ResilientLLMClient(LLMClient):
    """Wraps a primary client, falling back to a secondary on failure.

    Records lightweight, secret-free operational metadata (model, last latency,
    fallback reason, call counts) so the UI/logs can report what actually ran.
    Exception messages from the primary are sanitized to a coarse category so an
    API key embedded in an error string can never leak into artifacts.
    """

    def __init__(self, primary: "LLMClient", fallback: "LLMClient", *, model: str = "") -> None:
        self.primary = primary
        self.fallback = fallback
        self.model = model
        self.last_latency_ms: Optional[float] = None
        self.last_used: str = "none"
        self.fallback_reason: Optional[str] = None
        self.primary_calls = 0
        self.fallback_calls = 0

    def complete_json(self, prompt: str) -> Dict[str, Any]:
        import time as _time

        start = _time.monotonic()
        try:
            result = self.primary.complete_json(prompt)
            self.primary_calls += 1
            self.last_used = "primary"
            self.fallback_reason = None
            return result
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, never propagate key
            self.fallback_reason = _sanitize_error(exc)
            self.fallback_calls += 1
            self.last_used = "fallback"
            return self.fallback.complete_json(prompt)
        finally:
            self.last_latency_ms = round((_time.monotonic() - start) * 1000.0, 2)

    def operational_metadata(self) -> Dict[str, Any]:
        metadata = {
            "model": self.model,
            "last_used": self.last_used,
            "last_latency_ms": self.last_latency_ms,
            "fallback_reason": self.fallback_reason,
            "primary_calls": self.primary_calls,
            "fallback_calls": self.fallback_calls,
        }
        request_metadata = getattr(self.primary, "last_request_metadata", None)
        if isinstance(request_metadata, dict):
            metadata["last_request"] = dict(request_metadata)
        return metadata


def _sanitize_error(exc: Exception) -> str:
    """Map an exception to a coarse, secret-free category string."""
    name = type(exc).__name__
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_error_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            return "timeout"
        return "network_error"
    if isinstance(exc, OSError):
        return "network_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_response"
    return f"error_{name}"


_SENSITIVE_PROVIDER_FIELDS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "secret",
    "token",
)


def _redact_provider_error_value(value: Any, *, api_key: str | None) -> Any:
    """Remove credential-like values from a provider-owned error payload."""

    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if any(fragment in normalized_key for fragment in _SENSITIVE_PROVIDER_FIELDS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _redact_provider_error_value(child, api_key=api_key)
        return sanitized
    if isinstance(value, list):
        return [_redact_provider_error_value(item, api_key=api_key) for item in value]
    if isinstance(value, str):
        return _redact_provider_error_text(value, api_key=api_key)
    return value


def _redact_provider_error_text(text: str, *, api_key: str | None) -> str:
    sanitized = text
    if api_key:
        sanitized = sanitized.replace(api_key, "[REDACTED]")
    sanitized = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", sanitized)
    sanitized = re.sub(r"\bsk-[A-Za-z0-9._-]+", "[REDACTED]", sanitized)
    sanitized = re.sub(
        r"(?i)(api[_-]?key|authorization|cookie|token|secret)(\s*[:=]\s*)([^\r\n,;]+)",
        r"\1\2[REDACTED]",
        sanitized,
    )
    return sanitized


def _sanitized_provider_error_body(body: str, *, api_key: str | None) -> str:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return _redact_provider_error_text(body, api_key=api_key)
    sanitized = _redact_provider_error_value(parsed, api_key=api_key)
    return json.dumps(sanitized, indent=2, ensure_ascii=False)


def _provider_name(base_url: str) -> str:
    normalized = base_url.lower()
    if "openrouter.ai" in normalized:
        return "OpenRouter"
    if "api.openai.com" in normalized:
        return "OpenAI"
    return "OpenAI-compatible provider"


def _print_provider_http_error(
    error: urllib.error.HTTPError,
    *,
    provider: str,
    endpoint: str,
    model: str,
    api_key: str | None,
) -> None:
    """Print a secret-safe provider response while preserving the original error."""

    try:
        body = error.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - diagnostics must never mask the HTTPError
        body = "(provider response body unavailable)"
    response = _sanitized_provider_error_body(body, api_key=api_key)

    # This distinguishes invalid keys, limits/credits, model permissions,
    # provider restrictions, malformed requests, and other provider failures.
    print("=" * 50, file=sys.stderr)
    print("TRACEFIX PROVIDER ERROR", file=sys.stderr)
    print(f"Provider: {provider}", file=sys.stderr)
    print(f"HTTP Status: {error.code}", file=sys.stderr)
    print(f"Endpoint: {endpoint}", file=sys.stderr)
    print(f"Model: {model}", file=sys.stderr)
    print("\nResponse:", file=sys.stderr)
    print(response, file=sys.stderr)
    print("=" * 50, file=sys.stderr, flush=True)


class FakeLLMClient(LLMClient):
    """Deterministic fake client for tests and demos."""

    def __init__(self, scripted_actions: Optional[List[Dict[str, Any]]] = None) -> None:
        self.scripted_actions = list(scripted_actions or [])

    def complete_json(self, prompt: str) -> Dict[str, Any]:
        if self.scripted_actions:
            return dict(self.scripted_actions.pop(0))

        state = _extract_state_json(prompt)
        if "policy_envelope" in state:
            return _build_fake_decomposition_proposal(state)

        validated_context = state.get("validated_context")
        if isinstance(validated_context, dict) and validated_context:
            context_type = validated_context.get("context_type")
            value = validated_context.get("value", {})
            space_id = validated_context.get("space_id", state.get("space_id"))
            timestamp = validated_context.get("timestamp")
            if context_type == "occupancy":
                answer = "There appear to be {count} people in {space_id}.".format(
                    count=value.get("count"),
                    space_id=space_id,
                )
            elif context_type == "motion":
                observed_at = value.get("observed_at", timestamp)
                if value.get("motion_detected"):
                    answer = "Motion was detected in {space_id} at {observed_at}.".format(
                        space_id=space_id,
                        observed_at=observed_at,
                    )
                else:
                    answer = "No motion was detected in {space_id} at {observed_at}.".format(
                        space_id=space_id,
                        observed_at=observed_at,
                    )
            elif context_type == "audio":
                answer = (
                    "The audio context around {observed_at} indicates a noise level of {noise_level_db} dB in {space_id}."
                ).format(
                    observed_at=value.get("observed_at", timestamp),
                    noise_level_db=value.get("noise_level_db"),
                    space_id=space_id,
                )
            elif context_type == "room_state":
                answer = (
                    "The latest room state for {space_id} is {summary_state} with occupancy {occupancy_state} "
                    "and motion {motion_state}."
                ).format(
                    space_id=space_id,
                    summary_state=value.get("summary_state"),
                    occupancy_state=value.get("occupancy_state"),
                    motion_state=value.get("motion_state"),
                )
            else:
                answer = "Structured context is available for {space_id}.".format(space_id=space_id)

            return {
                "action_type": "final_answer",
                "tool_name": None,
                "arguments": {},
                "answer": answer,
                "confidence": validated_context.get("confidence"),
                "evidence_refs": list(validated_context.get("evidence_refs", [])),
                "caveats": ["Based on CityOS structured context."],
                "escalation_reason": None,
            }

        allowed_tools = state.get("allowed_tools", [])
        tool_name = allowed_tools[0] if allowed_tools else "cityos_context_lookup"
        arguments: Dict[str, Any] = {
            "space_id": state.get("space_id"),
            "timestamp": state.get("timestamp"),
        }
        if tool_name == "cityos_context_lookup":
            arguments["query"] = state.get("user_query")
        return {
            "action_type": "tool_request",
            "tool_name": tool_name,
            "arguments": arguments,
            "answer": None,
            "confidence": None,
            "evidence_refs": [],
            "caveats": [],
            "escalation_reason": None,
        }


class OpenAICompatibleLLMClient(LLMClient):
    """Small dependency-free client for OpenAI-compatible JSON chat endpoints."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.last_request_metadata: Dict[str, Any] = {}

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleLLMClient":
        if os.getenv("TELLME_ENABLE_REAL_LLM", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError(
                "Real LLM mode is disabled. Set TELLME_ENABLE_REAL_LLM=1 to enable the optional client."
            )
        base_url = os.getenv("TELLME_LLM_BASE_URL")
        model = os.getenv("TELLME_LLM_MODEL")
        if not base_url or not model:
            raise RuntimeError(
                "Real LLM mode requires TELLME_LLM_BASE_URL and TELLME_LLM_MODEL environment variables."
            )
        return cls(
            base_url=base_url,
            model=model,
            api_key=os.getenv("TELLME_LLM_API_KEY"),
            timeout_seconds=int(os.getenv("TELLME_LLM_TIMEOUT_SECONDS", "120")),
        )

    @classmethod
    def from_config(cls) -> "OpenAICompatibleLLMClient":
        """Build a client from ``.env`` / environment via :mod:`tellme_harness.config`.

        Raises ``RuntimeError`` when no ``OPENAI_API_KEY`` is configured so callers
        can fall back to the deterministic/fake path.
        """
        from .config import get_llm_config

        config = get_llm_config()
        if not config.has_key:
            raise RuntimeError(
                "Real LLM mode requires OPENAI_API_KEY (set it in .env or the environment)."
            )
        return cls(
            base_url=config.base_url,
            model=config.model,
            api_key=config.api_key,
            timeout_seconds=config.timeout_seconds,
        )

    @classmethod
    def for_openai(
        cls,
        api_key: str,
        model: str = "gpt-4.1-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
    ) -> "OpenAICompatibleLLMClient":
        """Build a client for the OpenAI HTTP API with an explicit (in-memory) API key.

        The key is held only on the instance; it is never persisted by this client.
        Callers must avoid placing it into logs, prompts, or written artifacts.
        """
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key must be a non-empty string.")
        return cls(base_url=base_url, model=model, api_key=api_key, timeout_seconds=timeout_seconds)

    def complete_json(self, prompt: str) -> Dict[str, Any]:
        request_started_at = datetime.now(timezone.utc).isoformat()
        request_started_ms = time.monotonic() * 1000.0
        first_response_at: str | None = None
        first_response_ms: float | None = None
        request_error: str | None = None
        endpoint = self.base_url + "/chat/completions"
        provider_name = _provider_name(self.base_url)
        self.last_request_metadata = {
            "request_start": request_started_at,
            "first_response_time": None,
            "request_end": None,
            "total_duration_ms": None,
            "time_to_first_response_ms": None,
            "model": self.model,
            "provider": self.base_url,
            "provider_name": provider_name,
            "endpoint": endpoint,
            "failed": False,
            "error": None,
            "http_status": None,
            "retry_count": 0,
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON matching the requested schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        request = urllib.request.Request(
            url=endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": "Bearer " + self.api_key} if self.api_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                first_response_at = datetime.now(timezone.utc).isoformat()
                first_response_ms = time.monotonic() * 1000.0
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            request_error = _sanitize_error(exc)
            self.last_request_metadata["http_status"] = exc.code
            _print_provider_http_error(
                exc,
                provider=provider_name,
                endpoint=endpoint,
                model=self.model,
                api_key=self.api_key,
            )
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            request_error = _sanitize_error(exc)
            raise
        finally:
            request_finished_ms = time.monotonic() * 1000.0
            self.last_request_metadata.update({
                "request_start": request_started_at,
                "first_response_time": first_response_at,
                "request_end": datetime.now(timezone.utc).isoformat(),
                "total_duration_ms": round(request_finished_ms - request_started_ms, 2),
                "time_to_first_response_ms": (
                    round(first_response_ms - request_started_ms, 2)
                    if first_response_ms is not None
                    else None
                ),
                "model": self.model,
                "provider": self.base_url,
                "provider_name": provider_name,
                "endpoint": endpoint,
                "failed": request_error is not None,
                "error": request_error,
                "retry_count": 0,
            })

        try:
            parsed = json.loads(body)
            content = parsed["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
            if not isinstance(content, str):
                raise ValueError("message content was not a string")
            action = json.loads(content)
        except Exception as exc:
            self.last_request_metadata["failed"] = True
            self.last_request_metadata["error"] = "invalid_response"
            raise RuntimeError("Real LLM response was not valid JSON AgentAction content.") from exc

        if not isinstance(action, dict):
            raise RuntimeError("Real LLM response did not produce a JSON object.")
        return action


def _extract_state_json(prompt: str) -> Dict[str, Any]:
    marker = "STATE_JSON:\n"
    if marker not in prompt:
        return {}
    state_text = prompt.split(marker, 1)[1].strip()
    try:
        payload = json.loads(state_text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_fake_decomposition_proposal(state: Dict[str, Any]) -> Dict[str, Any]:
    query = str(state.get("original_query", "")).strip()
    normalized = query.lower()
    policy = state.get("policy_envelope", {})
    allowed_harnesses = list(policy.get("allowed_harnesses", []))
    analysis = policy.get("analysis", {})
    room_context = policy.get("room_capability_context") or {}
    relevant_sensors = room_context.get("relevant_sensors") or []
    coverage_gaps = list(policy.get("coverage_gaps", []))
    available_apis = set(policy.get("allowed_context_apis", []))

    scenario_sensor_map = {
        "occupancy": ["camera_door_01", "camera_room_01", "microphone_array_01"],
        "entry_impact": ["camera_door_01", "microphone_array_01", "camera_room_01"],
        "speech": ["microphone_array_01", "camera_room_01"],
        "blind_spot": ["camera_room_01", "microphone_array_01"],
        "privacy": ["camera_door_01", "camera_room_01", "microphone_array_01"],
        "injury": ["microphone_array_01", "camera_room_01"],
        "default": [sensor.get("sensor_id") for sensor in relevant_sensors if sensor.get("available")],
    }
    scenario_api_map = {
        "occupancy": ["get_occupancy_context", "get_camera_occupancy_context"],
        "entry_impact": ["get_entry_event_context", "get_impact_sound_context", "get_posture_candidate_context"],
        "speech": ["get_speech_activity_context", "get_camera_occupancy_context"],
        "blind_spot": ["get_camera_coverage_metadata", "get_acoustic_event_context"],
        "privacy": ["get_camera_coverage_metadata"],
        "injury": ["get_impact_sound_context", "get_posture_candidate_context"],
        "default": list(available_apis),
    }

    if "show me the raw video" in normalized or "who was speaking" in normalized:
        harnesses = ["answer_synthesis_harness"]
        category = "unsupported"
        goal = "Reject or downgrade privacy-restricted identity and raw-access requests."
        answer_type = "insufficient_evidence"
        allowed_claims = ["insufficient_evidence"]
        scenario_key = "privacy"
        uncertainty = [
            "Raw video export is restricted.",
            "Speaker identity is unavailable from the microphone summaries.",
        ]
        privacy_notes = [
            "Reject raw video, speaker identity, and unrestricted transcription requests.",
            "Offer only privacy-preserving metadata or derived context when possible.",
        ]
    elif "under the table" in normalized:
        harnesses = [name for name in ["video_context_harness", "audio_context_harness"] if name in allowed_harnesses]
        category = "unsupported"
        goal = "Report the camera blind spot and avoid overclaiming from audio-only evidence."
        answer_type = "insufficient_evidence"
        allowed_claims = ["insufficient_evidence"]
        scenario_key = "blind_spot"
        uncertainty = [
            "The requested region is outside confirmed camera coverage.",
            "Audio cannot resolve the missing visual evidence.",
        ]
        privacy_notes = [
            "Use only derived context packets and camera coverage metadata.",
        ]
    elif "injured" in normalized or "injury" in normalized:
        harnesses = [name for name in ["fall_detection_harness", "audio_context_harness", "video_context_harness"] if name in allowed_harnesses]
        category = "safety_event_assessment"
        goal = "Assess a safety-event candidate without diagnosing injury."
        answer_type = "correlation_answer"
        allowed_claims = ["safety_event_candidate", "timestamped_event_summary", "cross_modal_agreement"]
        scenario_key = "injury"
        uncertainty = [
            "Impact sound and occluded posture evidence are candidate-level only.",
            "No medical or injury diagnosis is permitted.",
        ]
        privacy_notes = [
            "Do not turn impact or posture candidates into a confirmed injury claim.",
        ]
    elif "speaking" in normalized or "speech" in normalized:
        harnesses = [name for name in ["audio_context_harness", "video_context_harness"] if name in allowed_harnesses]
        category = "event_detection"
        goal = "Determine whether speech activity occurred without exposing words or identity."
        answer_type = "direct_answer"
        allowed_claims = ["timestamped_event_summary", "presence_state"]
        scenario_key = "speech"
        uncertainty = [
            "Speech activity does not reveal transcript content.",
            "Camera presence is supporting evidence only and cannot prove speech by itself.",
        ]
        privacy_notes = [
            "No transcript or speaker identity is available.",
        ]
    elif ("entered" in normalized and ("later fall" in normalized or "later fell" in normalized or "fall" in normalized)) or "impact sound" in normalized or ("enter" in normalized and "impact" in normalized):
        harnesses = [
            name
            for name in ["entry_event_harness", "audio_context_harness", "fall_detection_harness", "temporal_consistency_harness"]
            if name in allowed_harnesses
        ]
        category = "temporal_correlation"
        goal = "Correlate doorway entry with a later impact-sound candidate without claiming a fall."
        answer_type = "correlation_answer"
        allowed_claims = ["event_correlation", "bounded_temporal_sequence", "timestamped_event_summary"]
        scenario_key = "entry_impact"
        uncertainty = [
            "Temporal correlation does not establish cause.",
            "Impact sound does not confirm a fall or injury.",
        ]
        privacy_notes = [
            "Use identity-free event summaries only.",
        ]
    elif "how many" in normalized:
        harnesses = [name for name in ["occupancy_context_harness", "video_context_harness", "audio_context_harness"] if name in allowed_harnesses]
        category = "occupancy_count"
        goal = "Return a bounded occupancy count from camera-derived context, with audio only as supporting presence evidence."
        answer_type = "direct_answer"
        allowed_claims = ["bounded_count"]
        scenario_key = "occupancy"
        uncertainty = [
            "The current run is dry-run only; no measured runtime retrieval occurs.",
            "Occupancy is anonymous and bounded by camera coverage.",
        ]
        privacy_notes = [
            "No identity or raw media access is permitted.",
        ]
    elif "fall alert" in normalized or "false positive" in normalized:
        harnesses = [name for name in allowed_harnesses if name != "answer_synthesis_harness"]
        category = "safety_event_assessment"
        goal = "Assess whether the structured evidence supports the safety event."
        answer_type = "correlation_answer"
        allowed_claims = ["cross_modal_agreement", "timestamped_event_summary"]
        scenario_key = "default"
        uncertainty = [
            "This is a dry-run safety assessment only.",
        ]
        privacy_notes = [
            "No raw sensor access.",
        ]
    else:
        harnesses = [name for name in allowed_harnesses if name != "answer_synthesis_harness"][:1]
        category = "event_detection"
        goal = "Return the smallest policy-compliant answer."
        answer_type = "direct_answer"
        allowed_claims = ["timestamped_event_summary"]
        scenario_key = "default"
        uncertainty = [
            "Only the selected derived-context APIs may be used.",
        ]
        privacy_notes = [
            "Use CityOS structured context only.",
        ]

    if not harnesses:
        harnesses = ["answer_synthesis_harness"]

    # Deferred imports keep this module importable without the rest of the package
    # during lightweight tests, and avoid an import cycle at module load.
    from .evidence_card import build_validated_card_requirements
    from .schemas import AnswerPacketRequirements

    forbidden_claims = [
        "raw_sensor_access",
        "face_identity",
        "personal_identity",
        "medical_diagnosis",
        "unsupported_behavioral_inference",
    ]
    card_requirements, _ = build_validated_card_requirements(
        task_category=category,
        normalized_query=normalized,
        answer_requirements=AnswerPacketRequirements(
            answer_type=answer_type,
            required_fields=["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"],
            allowed_claims=allowed_claims,
            forbidden_claims=forbidden_claims,
            fallback_answer_type="insufficient_evidence",
        ),
        candidate_harnesses=harnesses,
        proposed=None,
    )

    return {
        "original_query": query,
        "normalized_query": normalized,
        "task_category": category,
        "inferred_user_goal": goal,
        "application_goal": {
            "goal_type": category,
            "user_intent": analysis.get("intent", "general_lookup"),
            "success_condition": "Return a privacy-bounded answer with confidence and evidence references.",
            "failure_condition": "Return insufficient_evidence when the bounded evidence is not enough.",
            "non_goals": [
                "identify person",
                "face recognition",
                "medical diagnosis",
                "infer cause or intent",
            ],
        },
        "proposed_harnesses": [
            {
                "name": harness_name,
                "role": "FakeLLM proposal for {name}".format(name=harness_name),
                "priority": "required" if index == 0 else "supporting",
                "expected_packet": "{name}_packet".format(name=harness_name),
                "rationale": "Selected from the deterministic envelope.",
            }
            for index, harness_name in enumerate(harnesses)
        ],
        "evidence_plan": {
            "primary_evidence": ["{name}_packet".format(name=harnesses[0])],
            "supporting_evidence": ["{name}_packet".format(name=name) for name in harnesses[1:]],
            "minimum_sufficient_evidence": ["{name}_packet".format(name=harnesses[0])],
            "conflicting_evidence_checks": ["cross_modal_consistency_packet"] if "cross_modal_consistency_harness" in harnesses else [],
        },
        "answer_packet_requirements": {
            "answer_type": answer_type,
            "required_fields": ["answer", "confidence", "evidence_refs", "caveats", "privacy_scope", "limitations"],
            "allowed_claims": allowed_claims,
            "forbidden_claims": [
                "raw_sensor_access",
                "face_identity",
                "personal_identity",
                "medical_diagnosis",
                "unsupported_behavioral_inference",
            ],
            "must_include_confidence": True,
            "must_include_evidence_refs": True,
            "must_include_limitations": True,
            "fallback_answer_type": "insufficient_evidence",
        },
        "evidence_card_requirements": card_requirements.model_dump(),
        "uncertainty_analysis": uncertainty + coverage_gaps,
        "escalation_conditions": [
            "insufficient_evidence",
            "conflicting_structured_context",
            "privacy_policy_denied",
        ],
        "privacy_risk_notes": privacy_notes,
        "room_context_summary": "Available derived context is centered on doorway and room cameras plus a ceiling microphone array. Blind spots, raw-access restrictions, and identity limits must be preserved.",
        "referenced_sensors": [sensor_id for sensor_id in scenario_sensor_map[scenario_key] if sensor_id],
        "referenced_context_apis": [api_name for api_name in scenario_api_map[scenario_key] if api_name in available_apis],
        "reasoning_summary": "FakeLLM semantic proposal for deterministic demo mode using only discovered sensors and APIs.",
    }
