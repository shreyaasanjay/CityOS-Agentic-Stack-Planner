"""Repo-local bridge from TeLLMe planning artifacts to TraceFix design."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tellme_harness import TellMeHarness
from tellme_harness.config import DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_TIMEOUT_SECONDS, get_llm_config
from tellme_harness.tracefix_projection import build_tracefix_task_projection
from tracefix.pipeline_timing import PipelineTimingReport
from tracefix.runtime.context_manager import answer_uploaded_context_query


class TellMeBridge:
    """Run TeLLMe and persist the current cross-stage handoff."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runs_root = self.root / ".runs" / "tellme"
        self.current_path = self.runs_root / "current.json"

    def process_query(
        self,
        *,
        query: str,
        space_id: str | None = None,
        timestamp: str | None = None,
        backend_mode: str = "deterministic",
        llm_model: str = "",
        request_api_key: str | None = None,
        llm_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("A natural-language smart-room request is required.")
        if backend_mode not in {"deterministic", "fake_llm", "llm"}:
            raise ValueError("TeLLMe backend mode must be deterministic, fake_llm, or llm.")

        uploaded_answer = answer_uploaded_context_query(query, space_id=space_id, timestamp=timestamp)
        if uploaded_answer is not None:
            state = {
                "run_id": uploaded_answer["query_id"],
                "tellme": uploaded_answer,
                "tracefix": {},
                "cityos": {},
            }
            self._write_json(self.current_path, state)
            return uploaded_answer

        config = get_llm_config()
        effective_api_key = (request_api_key or "").strip() or config.api_key
        selected_model = llm_model.strip() or config.model or DEFAULT_OPENAI_MODEL
        selected_timeout_seconds = self._resolve_timeout_seconds(llm_timeout_seconds, config.timeout_seconds)
        if backend_mode == "llm" and not effective_api_key:
            raise ValueError(
                "LLM mode selected, but no API key was found. "
                "Set OPENAI_API_KEY or TELLME_API_KEY."
            )

        stage_started_at = datetime.now(timezone.utc).isoformat()
        stage_started_ms = time.monotonic() * 1000.0
        harness = TellMeHarness(
            runs_root=self.runs_root,
            agent_backend_mode=backend_mode,
            llm_api_key=effective_api_key if backend_mode == "llm" else None,
            llm_model=selected_model,
            llm_timeout_seconds=selected_timeout_seconds,
            execution_mode="planning_only",
        )
        answer = harness.handle_query(query, space_id=space_id, timestamp=timestamp)
        stage_finished_at = datetime.now(timezone.utc).isoformat()
        stage_duration_ms = time.monotonic() * 1000.0 - stage_started_ms
        answer_payload = answer.model_dump()
        llm_metadata = ((answer_payload.get("raw_outputs") or {}).get("llm_operational") or {})
        decomposition = llm_metadata.get("decomposition") if isinstance(llm_metadata, dict) else {}
        llm_warnings: list[str] = []
        if backend_mode == "llm" and isinstance(decomposition, dict) and decomposition.get("last_used") == "fallback":
            reason = decomposition.get("fallback_reason") or "unknown_llm_error"
            if self._is_hard_llm_failure(reason):
                raise RuntimeError(
                    f"TeLLMe LLM request failed ({reason}). "
                    "Check the API key and model, or use Deterministic mode."
                )
            llm_warnings.append(
                f"TeLLMe LLM request fell back to deterministic planning ({reason}) after waiting up to "
                f"{selected_timeout_seconds}s. TraceFix can continue with the validated fallback task spec."
            )
        run_dir = Path(str(answer.raw_outputs.get("run_dir") or "")).resolve()
        timing = PipelineTimingReport(
            run_dir,
            run_kind="tellme",
            run_id=str(answer.query_id),
            started_at=stage_started_at,
            started_ms=stage_started_ms,
        )
        timing.stage(
            "tellme_decomposition",
            started_at=stage_started_at,
            finished_at=stage_finished_at,
            duration_ms=stage_duration_ms,
            success=True,
            model=selected_model if backend_mode == "llm" else None,
            provider="openai_compatible" if backend_mode == "llm" else "deterministic",
            retry_count=0,
        )
        request_metadata = decomposition.get("last_request") if isinstance(decomposition, dict) else None
        if isinstance(request_metadata, dict):
            timing.api_calls.append(
                {
                    "stage": "tellme_decomposition",
                    **request_metadata,
                    "observation_scope": "http_request",
                }
            )
        timing.finalize()
        task_spec = answer_payload.get("tracefix_task_spec") or {}
        validation_policy = task_spec.get("validation_policy") if isinstance(task_spec, dict) else {}
        warnings = list(validation_policy.get("warnings") or []) if isinstance(validation_policy, dict) else []
        warnings.extend(llm_warnings)

        data = {
            "query_id": answer.query_id,
            "query": query,
            "mode": backend_mode,
            "model": selected_model,
            "api_key_detected": bool(effective_api_key),
            "llm_timeout_seconds": selected_timeout_seconds if backend_mode == "llm" else None,
            "status": answer.status,
            "route_decision": answer_payload.get("route_decision") or {},
            "privacy_guardrail": self._privacy_guardrail(answer_payload),
            "intent_decomposition": self._read_json(run_dir / "intent_decomposition.json"),
            "execution_brief": self._read_json(run_dir / "smartspace_execution_brief.json"),
            "tracefix_task_spec": task_spec,
            "tracefix_bundle": self._read_json(run_dir / "tracefix_bundle" / "bundle.json"),
            "answer_packet": answer_payload,
            "run_dir": str(run_dir),
            "warnings": warnings,
        }
        state = {
            "run_id": answer.query_id,
            "tellme": data,
            "tracefix": {},
            "cityos": {},
        }
        self._write_json(self.current_path, state)
        return data

    @staticmethod
    def _resolve_timeout_seconds(requested: int | None, default: int | None) -> int:
        try:
            value = int(requested if requested is not None else default or DEFAULT_OPENAI_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            value = DEFAULT_OPENAI_TIMEOUT_SECONDS
        return max(10, min(value, 600))

    @staticmethod
    def _is_hard_llm_failure(reason: str) -> bool:
        return reason.startswith(("http_error_401", "http_error_403", "http_error_404"))

    @staticmethod
    def llm_config() -> dict[str, Any]:
        config = get_llm_config()
        openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        tellme_key = (os.getenv("TELLME_API_KEY") or "").strip()
        api_key_detected = bool(openai_key or tellme_key)
        return {
            "mode": "deterministic",
            "model": config.model or DEFAULT_OPENAI_MODEL,
            "api_key_detected": api_key_detected,
            "timeout_seconds": config.timeout_seconds,
            "openai_api_key_detected": bool(openai_key),
            "tellme_api_key_detected": bool(tellme_key),
            "supported_modes": ["deterministic", "llm"],
            "key_environment_variables": ["TELLME_API_KEY", "OPENAI_API_KEY"],
            "model_environment_variable": "TELLME_MODEL",
        }

    def current(self) -> dict[str, Any] | None:
        payload = self._read_json(self.current_path)
        return payload if payload else None

    def tracefix_task_text(self) -> str:
        current = self.current()
        if not current:
            raise ValueError("No TeLLMe run is available. Process a request in the TeLLMe tab first.")
        tellme = current.get("tellme") or {}
        task_spec = tellme.get("tracefix_task_spec")
        if not isinstance(task_spec, dict) or not task_spec:
            raise ValueError("The current TeLLMe run did not produce a TraceFix-compatible task spec.")
        if tellme.get("status") not in {"needs_tracefix", "answered"}:
            raise ValueError(
                f"The current TeLLMe run is not eligible for TraceFix design: {tellme.get('status') or 'unknown'}."
            )

        run_dir = Path(str(tellme.get("run_dir") or ""))
        execution_brief = tellme.get("execution_brief")
        execution_brief = execution_brief if isinstance(execution_brief, dict) else {}
        compact_spec = build_tracefix_task_projection(
            task_spec,
            execution_brief=execution_brief,
            discovery_snapshot=self._read_json(run_dir / "cityos_capability_snapshot.json"),
            discovery_provenance=self._read_json(run_dir / "discovery_provenance.json"),
        )

        return "\n".join(
            [
                "TeLLMe structured smart-room application requirements.",
                "Treat this as compile-time input to TraceFix planning and verification.",
                "Generate a complete multi-agent topology with explicit communication channels.",
                "Do not execute production agents or bypass PlusCal/TLC verification.",
                "",
                "Structured task specification:",
                json.dumps(compact_spec, indent=2, ensure_ascii=False),
            ]
        )

    def tracefix_task_spec_path(self) -> Path:
        """Return the official, immutable TeLLMe TaskSpec artifact path."""

        current = self.current() or {}
        tellme = current.get("tellme") if isinstance(current.get("tellme"), dict) else {}
        run_dir = Path(str(tellme.get("run_dir") or ""))
        path = (run_dir / "tracefix_task_spec.json").resolve()
        if not path.is_file():
            raise ValueError("The current TeLLMe run has no persisted tracefix_task_spec.json artifact.")
        return path

    def record_tracefix_run(self, run_id: str) -> dict[str, Any]:
        state = self.current()
        if not state:
            raise ValueError("No TeLLMe run is available.")
        state["tracefix"] = {"run_id": run_id}
        self._write_json(self.current_path, state)
        return state

    def record_tracefix_workspace(self, run_id: str, workspace: str) -> None:
        state = self.current()
        if not state:
            return
        tracefix = state.setdefault("tracefix", {})
        if tracefix.get("run_id") != run_id:
            return
        tracefix["workspace"] = workspace
        self._write_json(self.current_path, state)

    def record_cityos_result(self, result: dict[str, Any]) -> None:
        state = self.current()
        if not state:
            return
        state["cityos"] = result
        self._write_json(self.current_path, state)


    def record_web_data_answer(self, result: dict[str, Any]) -> None:
        state = self.current()
        if not state:
            return
        tellme = state.get("tellme") if isinstance(state.get("tellme"), dict) else None
        if tellme is None:
            return
        answer = result.get("answer") if isinstance(result.get("answer"), dict) else None
        if answer is not None:
            tellme["answer_summary"] = answer.get("text") or ""
            chat_answer = answer.get("chatAnswer") or answer.get("chat_answer") or ""
            if chat_answer:
                tellme["chat_answer"] = chat_answer
            tellme["web_data_answer"] = answer
        if result.get("answerPath"):
            tellme["web_data_answer_path"] = str(result.get("answerPath"))
        if result.get("payload"):
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            tellme["web_data_snapshot_summary"] = payload.get("snapshotSummary")
            tellme["web_data_payload_path"] = payload.get("payloadPath")
        tellme["web_data_source_url"] = result.get("sourceUrl")
        tellme["web_data_source_kind"] = result.get("sourceKind")
        state["tellme"] = tellme
        self._write_json(self.current_path, state)
    def artifact_paths(self, data: dict[str, Any] | None = None) -> list[str]:
        current_data = data or ((self.current() or {}).get("tellme") or {})
        run_dir_raw = str(current_data.get("run_dir") or "")
        if not run_dir_raw:
            return []
        run_dir = Path(run_dir_raw)
        candidates = (
            run_dir / "route_decision.json",
            run_dir / "intent_decomposition.json",
            run_dir / "smartspace_execution_brief.json",
            run_dir / "tracefix_task_spec.json",
            run_dir / "answer_packet.json",
            run_dir / "tracefix_bundle" / "bundle.json",
        )
        return [str(path) for path in candidates if path.exists()]

    @staticmethod
    def _privacy_guardrail(answer: dict[str, Any]) -> dict[str, Any]:
        route = answer.get("route_decision") or {}
        task_spec = answer.get("tracefix_task_spec") or {}
        policy = task_spec.get("privacy_policy") if isinstance(task_spec, dict) else {}
        blocked = route.get("route") == "not_allowed"
        return {
            "status": "blocked" if blocked else "passed",
            "privacy_scope": answer.get("privacy_scope") or "cityos_structured_context_only",
            "raw_sensor_access_allowed": bool(policy.get("raw_sensor_access_allowed", False))
            if isinstance(policy, dict)
            else False,
            "identity_inference_allowed": bool(policy.get("identity_inference_allowed", False))
            if isinstance(policy, dict)
            else False,
            "caveats": answer.get("caveats") or [],
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        try:
            temporary.replace(path)
        except PermissionError:
            # Some Windows ACL/share-mode combinations deny delete-style replace.
            path.write_text(body, encoding="utf-8")
            try:
                temporary.unlink()
            except OSError:
                pass
