"""Best-effort, secret-free timing reports for TraceFix pipeline runs."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracefix.runtime.usage_tracker import UsageTracker


_WRITE_LOCK = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0


class PipelineTimingReport:
    """Collect stage timing without affecting pipeline control flow."""

    def __init__(
        self,
        run_dir: Path,
        *,
        run_kind: str,
        run_id: str = "",
        started_at: str | None = None,
        started_ms: float | None = None,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.output_dir = self.run_dir / "output"
        self.json_path = self.output_dir / "pipeline_timing_report.json"
        self.md_path = self.output_dir / "pipeline_timing_report.md"
        self.run_kind = run_kind
        self.run_id = run_id
        self.started_at = started_at or utc_now()
        self._started_ms = started_ms if started_ms is not None else monotonic_ms()
        self.stages: list[dict[str, Any]] = []
        self.api_calls: list[dict[str, Any]] = []
        self.usage = UsageTracker(self.run_dir, run_id=self.run_id or self.run_dir.name)
        self.usage.write()

    def stage(
        self,
        name: str,
        *,
        started_at: str,
        finished_at: str,
        duration_ms: float,
        success: bool,
        error: str | None = None,
        **metadata: Any,
    ) -> None:
        entry: dict[str, Any] = {
            "stage": name,
            "queued_at": metadata.pop("queued_at", None),
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": round(max(0.0, duration_ms), 2),
            "success": bool(success),
            "error": _safe_error(error),
        }
        entry.update({key: value for key, value in metadata.items() if value is not None})
        self.stages.append(entry)
        print(
            "[TRACEFIX TIMING] "
            + json.dumps(
                {
                    "stage": name,
                    "duration_ms": entry["duration_ms"],
                    "success": entry["success"],
                    "model": entry.get("model"),
                    "provider": entry.get("provider"),
                    "retry_count": entry.get("retry_count"),
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        self.write()

    def opencode_call(
        self,
        stage: str,
        disposition: dict[str, Any] | None,
    ) -> None:
        if not isinstance(disposition, dict):
            now = utc_now()
            self.stage(
                stage,
                started_at=now,
                finished_at=now,
                duration_ms=0.0,
                success=True,
                skipped=True,
                skip_reason="optional OpenCode stage was not invoked",
            )
            return
        call = {
            "stage": stage,
            "provider": disposition.get("provider"),
            "model": disposition.get("model"),
            "request_start": disposition.get("started_at"),
            "first_response_time": disposition.get("first_event_at"),
            "request_end": disposition.get("finished_at"),
            "total_duration_ms": disposition.get("duration_ms"),
            "time_to_first_event_ms": disposition.get("time_to_first_event_ms"),
            "retry_count": disposition.get("retry_count", 0),
            "rate_limit_events": disposition.get("rate_limit_events", 0),
            "failed": disposition.get("status") not in {"completed", "incomplete"},
            "status": disposition.get("status"),
            "observation_scope": "opencode_process",
            "usage_available": bool(disposition.get("usage_available")),
            "usage": disposition.get("usage") or {},
        }
        self._record_disposition_usage(stage, disposition)
        self.api_calls.append(call)
        self.stage(
            stage,
            started_at=str(disposition.get("started_at") or utc_now()),
            finished_at=str(disposition.get("finished_at") or utc_now()),
            duration_ms=float(disposition.get("duration_ms") or 0.0),
            success=not call["failed"],
            error=_stderr_error(disposition),
            provider=call["provider"],
            model=call["model"],
            retry_count=call["retry_count"],
            rate_limit_events=call["rate_limit_events"],
            first_response_time=call["first_response_time"],
            time_to_first_event_ms=call["time_to_first_event_ms"],
        )

    def write(self, *, complete: bool = False) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            payload = self._payload(complete=complete)
            text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            markdown = _to_markdown(payload)
            with _WRITE_LOCK:
                _atomic_write(self.json_path, text)
                _atomic_write(self.md_path, markdown)
        except OSError:
            # Diagnostics must never become a pipeline failure.
            return

    def finalize(self) -> None:
        self.write(complete=True)
        summary = self.usage.summary()
        cost = summary.get("total_estimated_cost_usd")
        cost_text = "unavailable" if cost is None else f"${cost:.6f}"
        print(
            "[TRACEFIX LLM TOTAL] "
            f"tokens={summary.get('total_tokens', 0)} cost={cost_text} "
            f"highest_cost_stage={summary.get('highest_cost_stage') or 'none'} "
            f"usage_unavailable={summary.get('usage_unavailable_count', 0)} "
            f"missing_pricing={summary.get('missing_pricing_count', 0)}",
            flush=True,
        )

    def _record_disposition_usage(
        self,
        stage: str,
        disposition: dict[str, Any],
    ) -> None:
        call_id = str(disposition.get("call_id") or stage)
        steps = disposition.get("usage_steps")
        if isinstance(steps, list) and steps:
            for index, step in enumerate(steps, 1):
                if not isinstance(step, dict):
                    continue
                self.usage.record(
                    stage=stage,
                    agent=str(disposition.get("agent_id") or ""),
                    provider=str(disposition.get("provider") or ""),
                    model=str(disposition.get("model") or ""),
                    started_at=str(disposition.get("started_at") or utc_now()),
                    ended_at=str(disposition.get("finished_at") or utc_now()),
                    duration_ms=float(disposition.get("duration_ms") or 0.0),
                    prompt_tokens=int(step.get("prompt_tokens") or 0),
                    completion_tokens=int(step.get("completion_tokens") or 0),
                    total_tokens=int(step.get("total_tokens") or 0),
                    cached_tokens=int(step.get("cached_tokens") or 0),
                    reasoning_tokens=int(step.get("reasoning_tokens") or 0),
                    exact_cost_usd=step.get("cost_usd"),
                    record_id=f"{call_id}:{index}",
                )
            return
        self.usage.record_unavailable(
            stage=stage,
            agent=str(disposition.get("agent_id") or ""),
            provider=str(disposition.get("provider") or ""),
            model=str(disposition.get("model") or ""),
            started_at=str(disposition.get("started_at") or utc_now()),
            ended_at=str(disposition.get("finished_at") or utc_now()),
            duration_ms=float(disposition.get("duration_ms") or 0.0),
            record_id=f"{call_id}:unavailable",
        )

    def _payload(self, *, complete: bool) -> dict[str, Any]:
        duration_ms = round(monotonic_ms() - self._started_ms, 2)
        measured_stages = [
            stage for stage in self.stages if not stage.get("aggregate")
        ]
        slowest = max(
            measured_stages,
            key=lambda item: item.get("duration_ms", 0),
            default=None,
        )
        retries = sum(int(call.get("retry_count") or 0) for call in self.api_calls)
        rate_limits = sum(int(call.get("rate_limit_events") or 0) for call in self.api_calls)
        suspected, confidence, recommendation = _diagnosis(slowest, self.api_calls, rate_limits)
        repair = _load_repair_progress(self.run_dir)
        ir_sanitization = _latest_ir_sanitization(self.stages)
        fast_path = _latest_fast_path(self.stages)
        llm_attribute_extraction = _latest_coordination_attributes(self.stages)
        usage = self.usage.summary()
        if repair.get("stop_reason") and repair.get("stop_reason") != "tlc_passed":
            recommendation = str(repair.get("recommendation") or recommendation)
        return {
            "schema_version": "0.1",
            "run_kind": self.run_kind,
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "started_at": self.started_at,
            "finished_at": utc_now() if complete else None,
            "complete": complete,
            "total_wall_clock_ms": duration_ms,
            "stages": self.stages,
            "api_calls": self.api_calls,
            "usage": usage,
            "usage_records": list(self.usage.records),
            "total_prompt_tokens": usage["total_prompt_tokens"],
            "total_completion_tokens": usage["total_completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "total_estimated_cost_usd": usage["total_estimated_cost_usd"],
            "cost_by_stage": usage["cost_by_stage"],
            "cost_by_model": usage["cost_by_model"],
            "repair": repair,
            "ir_sanitization": ir_sanitization,
            "single_agent_fast_path": fast_path,
            "llm_attribute_extraction": llm_attribute_extraction,
            "summary": {
                "slowest_stage": slowest.get("stage") if slowest else None,
                "slowest_stage_duration_ms": slowest.get("duration_ms") if slowest else None,
                "api_call_count": len(self.api_calls),
                "retry_count": retries,
                "rate_limit_events": rate_limits,
                "total_repair_attempts": repair.get("total_attempts", 0),
                "repair_stop_reason": repair.get("stop_reason"),
                "suspected_bottleneck": suspected,
                "confidence": confidence,
                "recommended_next_fix": recommendation,
            },
            "limitations": [
                "OpenCode timing observes the subprocess and its first JSON event, not provider first-token telemetry.",
                "A long time-to-first-event can indicate OpenCode startup, provider queueing, or model latency.",
            ],
        }


def append_stage(
    run_dir: Path,
    name: str,
    *,
    started_at: str,
    finished_at: str,
    duration_ms: float,
    success: bool,
    error: str | None = None,
    **metadata: Any,
) -> None:
    """Append a stage to an existing report, or create a small report."""
    report = PipelineTimingReport(run_dir, run_kind="cross_stage")
    if report.json_path.exists():
        try:
            existing = json.loads(report.json_path.read_text(encoding="utf-8"))
            report.started_at = str(existing.get("started_at") or report.started_at)
            report.stages = list(existing.get("stages") or [])
            report.api_calls = list(existing.get("api_calls") or [])
            existing_duration = float(existing.get("total_wall_clock_ms") or 0.0)
            report._started_ms = monotonic_ms() - existing_duration
        except (OSError, ValueError, TypeError):
            pass
    report.stage(
        name,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        success=success,
        error=error,
        **metadata,
    )
    report.finalize()


def _safe_error(error: str | None) -> str | None:
    if not error:
        return None
    value = str(error).replace("\r", " ").replace("\n", " ")
    for name in ("OPENAI_API_KEY", "TELLME_API_KEY", "OPENROUTER_API_KEY"):
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[:1000]


def _stderr_error(disposition: dict[str, Any]) -> str | None:
    if disposition.get("status") in {"completed", "incomplete"}:
        return None
    tail = disposition.get("stderr_tail") or []
    return " | ".join(str(line) for line in tail[-3:]) or str(disposition.get("status"))


def _diagnosis(
    slowest: dict[str, Any] | None,
    api_calls: list[dict[str, Any]],
    rate_limits: int,
) -> tuple[str, str, str]:
    if rate_limits:
        return (
            "external_api_rate_limit_or_retry",
            "high",
            "Inspect provider limits and retry policy; consider fail-fast development settings.",
        )
    if slowest and str(slowest.get("stage", "")).startswith("opencode_"):
        first_event = float(slowest.get("time_to_first_event_ms") or 0)
        if first_event > 30_000:
            return (
                "opencode_startup_or_external_model_queue",
                "medium",
                "Compare another provider/model and inspect OpenCode provider logs for request-level latency.",
            )
        return (
            "opencode_model_or_tool_loop",
            "high",
            "Reduce repeated model repair passes or cache successful intermediate artifacts after correctness review.",
        )
    if slowest and slowest.get("stage") in {"pluscal_translation", "tlc_verification"}:
        return (
            "formal_verification",
            "high",
            "Inspect TLC state-space size and use bounded development constants without skipping TLC.",
        )
    if api_calls:
        return (
            "external_model_or_orchestration",
            "medium",
            "Compare API wait and deterministic stage durations in this report.",
        )
    return (
        "internal_or_unknown",
        "low",
        "Run a model-backed design trial to collect API and formal verification timings.",
    )


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _load_repair_progress(run_dir: Path) -> dict[str, Any]:
    candidates = (
        run_dir / "spec" / "repair_progress.json",
        run_dir / "repair_progress.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        attempts = payload.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
        return {
            "total_attempts": len(attempts),
            "attempts": attempts,
            "repair_time_seconds": payload.get("repair_time_seconds", 0.0),
            "stop_reason": payload.get("stop_reason"),
            "recommendation": payload.get("recommendation"),
            "config": payload.get("config") or {},
        }
    return {
        "total_attempts": 0,
        "attempts": [],
        "repair_time_seconds": 0.0,
        "stop_reason": None,
        "recommendation": None,
        "config": {},
    }


def _latest_ir_sanitization(stages: list[dict[str, Any]]) -> dict[str, Any]:
    for stage in reversed(stages):
        report = stage.get("ir_sanitization")
        if isinstance(report, dict):
            return report
    return {
        "attempted": False,
        "changed": False,
        "removed_fields": [],
        "normalized_fields": [],
        "validation_before": None,
        "validation_after": None,
        "recovered": False,
        "prevented_likely_unnecessary_failure": False,
    }


def _latest_fast_path(stages: list[dict[str, Any]]) -> dict[str, Any]:
    for stage in reversed(stages):
        report = stage.get("fast_path")
        if isinstance(report, dict):
            return report
    return {
        "considered": False,
        "used": False,
        "reason": None,
        "structured_input": False,
        "agent_id": None,
        "ir_generation_duration_ms": 0.0,
        "fallback_to_opencode": True,
        "error": None,
    }


def _latest_coordination_attributes(stages: list[dict[str, Any]]) -> dict[str, Any]:
    for stage in reversed(stages):
        if stage.get("stage") != "llm_attribute_extraction":
            continue
        return {
            "attempted": True,
            "success": bool(stage.get("success")),
            "attributes": stage.get("extracted_coordination_attributes") or {},
            "template_mapping_status": stage.get("template_mapping_status"),
            "error": stage.get("error"),
        }
    return {
        "attempted": False,
        "success": False,
        "attributes": {},
        "template_mapping_status": "not_attempted",
        "error": None,
    }


def _to_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    sanitization = payload.get("ir_sanitization") or {}
    fast_path = payload.get("single_agent_fast_path") or {}
    coord_attrs = payload.get("llm_attribute_extraction") or {}
    attrs = coord_attrs.get("attributes") or {}
    usage = payload.get("usage") or {}
    total_cost = usage.get("total_estimated_cost_usd")
    cost_text = "unavailable" if total_cost is None else f"${total_cost:.6f}"
    lines = [
        "# TraceFix Pipeline Timing",
        "",
        f"- Total: {payload.get('total_wall_clock_ms', 0):.2f} ms",
        f"- Slowest stage: {summary.get('slowest_stage') or 'unavailable'}",
        f"- Suspected bottleneck: {summary.get('suspected_bottleneck')}",
        f"- Confidence: {summary.get('confidence')}",
        f"- Repair attempts: {summary.get('total_repair_attempts', 0)}",
        f"- Repair stop reason: {summary.get('repair_stop_reason') or 'none'}",
        f"- LLM prompt tokens: {usage.get('total_prompt_tokens', 0)}",
        f"- LLM completion tokens: {usage.get('total_completion_tokens', 0)}",
        f"- LLM total tokens: {usage.get('total_tokens', 0)}",
        f"- LLM estimated cost: {cost_text}",
        f"- Highest-cost LLM stage: {usage.get('highest_cost_stage') or 'none'}",
        f"- Calls without usage: {usage.get('usage_unavailable_count', 0)}",
        f"- Calls missing pricing: {usage.get('missing_pricing_count', 0)}",
        f"- IR sanitization attempted: {'yes' if sanitization.get('attempted') else 'no'}",
        f"- IR sanitizer recovered pipeline: {'yes' if sanitization.get('recovered') else 'no'}",
        f"- Single-agent fast path considered: {'yes' if fast_path.get('considered') else 'no'}",
        f"- Single-agent fast path used: {'yes' if fast_path.get('used') else 'no'}",
        f"- Fast-path reason: {fast_path.get('reason') or 'unavailable'}",
        f"- Fast-path fallback to OpenCode: {'yes' if fast_path.get('fallback_to_opencode') else 'no'}",
        f"- Fast-path IR duration: {float(fast_path.get('ir_generation_duration_ms') or 0):.2f} ms",
        f"- Coordination attribute extraction attempted: {'yes' if coord_attrs.get('attempted') else 'no'}",
        f"- Coordination attribute extraction success: {'yes' if coord_attrs.get('success') else 'no'}",
        f"- Template mapping status: {coord_attrs.get('template_mapping_status') or 'not_attempted'}",
        "- Extracted coordination patterns: "
        + (", ".join(attrs.get("coordination_patterns") or []) or "none"),
        f"- Extracted agent count: {attrs.get('number_of_agents')}",
        f"- Extracted resource count: {attrs.get('number_of_resources')}",
        f"- Extracted channel count: {attrs.get('number_of_channels')}",
        "- Extracted agent roles: "
        + (", ".join(attrs.get("agent_roles") or []) or "none"),
        "- IR fields removed: "
        + (", ".join(sanitization.get("removed_fields") or []) or "none"),
        "- IR fields normalized: "
        + (", ".join(sanitization.get("normalized_fields") or []) or "none"),
        "",
        "| Stage | Duration (ms) | Success |",
        "| --- | ---: | :---: |",
    ]
    for stage in payload.get("stages") or []:
        lines.append(
            f"| {stage.get('stage')} | {stage.get('duration_ms', 0):.2f} | "
            f"{'yes' if stage.get('success') else 'no'} |"
        )
    lines.extend(["", f"Recommended next fix: {summary.get('recommended_next_fix')}", ""])
    return "\n".join(lines)
