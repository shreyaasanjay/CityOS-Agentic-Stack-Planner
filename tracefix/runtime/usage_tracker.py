"""Workspace-scoped LLM token, cost, and budget tracking."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CostLimitExceeded(RuntimeError):
    """Raised before a model call when its workspace budget is exhausted."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _pricing_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "model_pricing.json"


def load_pricing(path: Path | None = None) -> dict[str, Any]:
    source = path or _pricing_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"models": {}}
    return payload if isinstance(payload, dict) else {"models": {}}


def resolve_pricing(model: str, pricing: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    model_key = str(model or "").strip().lower()
    models = (pricing or load_pricing()).get("models") or {}
    if not isinstance(models, dict):
        return model_key, None
    for canonical, entry in models.items():
        if not isinstance(entry, dict):
            continue
        aliases = [str(value).lower() for value in entry.get("aliases") or []]
        if model_key == str(canonical).lower() or model_key in aliases:
            return str(canonical), entry
    return model_key, None


def estimate_cost(
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    pricing: dict[str, Any] | None = None,
) -> tuple[float | None, str, str | None]:
    canonical, entry = resolve_pricing(model, pricing)
    if entry is None:
        return None, "missing", "missing_model_pricing"
    input_rate = _optional_float(entry.get("input_cost_per_1m_tokens"))
    output_rate = _optional_float(entry.get("output_cost_per_1m_tokens"))
    if input_rate is None or output_rate is None:
        return None, str(entry.get("pricing_source") or "missing"), "missing_model_pricing"

    cached_rate = _optional_float(entry.get("cached_input_cost_per_1m_tokens"))
    reasoning_rate = _optional_float(entry.get("reasoning_cost_per_1m_tokens"))
    prompt = _nonnegative_int(prompt_tokens)
    completion = _nonnegative_int(completion_tokens)
    cached = min(prompt, _nonnegative_int(cached_tokens))
    reasoning = min(completion, _nonnegative_int(reasoning_tokens))
    regular_input = prompt - cached
    regular_output = completion - reasoning if reasoning_rate is not None else completion
    cost = regular_input * input_rate
    cost += cached * (cached_rate if cached_rate is not None else input_rate)
    cost += regular_output * output_rate
    if reasoning_rate is not None:
        cost += reasoning * reasoning_rate
    return (
        round(cost / 1_000_000, 8),
        str(entry.get("pricing_source") or canonical),
        None,
    )


class UsageTracker:
    """Persist per-call usage and aggregate workspace reports."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        run_id: str = "",
        pricing_path: Path | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.run_id = run_id or self.workspace.name
        self.output_dir = self.workspace / "output"
        self.json_path = self.output_dir / "llm_usage_report.json"
        self.md_path = self.output_dir / "llm_usage_report.md"
        self.pricing = load_pricing(pricing_path)
        self.records: list[dict[str, Any]] = []
        self._record_ids: set[str] = set()
        self._lock = threading.RLock()
        self.warn_limit = _optional_float(os.getenv("TRACEFIX_WARN_COST_USD"))
        self.cost_limit = _optional_float(os.getenv("TRACEFIX_COST_LIMIT_USD"))
        self._warned = False
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        records = payload.get("usage_records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("run_id") != self.run_id:
                continue
            self.records.append(record)
            record_id = str(record.get("record_id") or "")
            if record_id:
                self._record_ids.add(record_id)

    def ensure_can_call(self, stage: str) -> None:
        summary = self.summary()
        cost = summary.get("total_estimated_cost_usd")
        if self.cost_limit is not None and cost is not None and cost >= self.cost_limit:
            raise CostLimitExceeded(
                f"TRACEFIX_COST_LIMIT_USD reached before {stage}: "
                f"${cost:.6f} >= ${self.cost_limit:.6f}"
            )

    def record(
        self,
        *,
        stage: str,
        agent: str = "",
        provider: str = "",
        model: str = "",
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float = 0.0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        exact_cost_usd: float | None = None,
        pricing_source: str = "",
        usage_available: bool = True,
        fallback_reason: str | None = None,
        record_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if record_id and record_id in self._record_ids:
                return next(
                    record for record in self.records
                    if record.get("record_id") == record_id
                )
            prompt = _nonnegative_int(prompt_tokens)
            completion = _nonnegative_int(completion_tokens)
            total = _nonnegative_int(total_tokens) if total_tokens is not None else prompt + completion
            cached = _nonnegative_int(cached_tokens)
            reasoning = _nonnegative_int(reasoning_tokens)
            warning = None
            if exact_cost_usd is not None:
                cost = round(max(0.0, float(exact_cost_usd)), 8)
                source = pricing_source or "provider_reported"
            elif usage_available:
                cost, source, warning = estimate_cost(
                    model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    cached_tokens=cached,
                    reasoning_tokens=reasoning,
                    pricing=self.pricing,
                )
            else:
                cost = None
                source = pricing_source or "usage_unavailable"
                warning = fallback_reason or "usage_unavailable"

            record = {
                "record_id": record_id or f"{self.run_id}:{len(self.records) + 1}",
                "run_id": self.run_id,
                "workspace": str(self.workspace),
                "stage": stage,
                "agent": agent,
                "provider": provider,
                "model": model,
                "started_at": started_at or utc_now(),
                "ended_at": ended_at or utc_now(),
                "duration_ms": round(max(0.0, float(duration_ms or 0.0)), 2),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "cached_tokens": cached,
                "reasoning_tokens": reasoning,
                "estimated_cost_usd": cost,
                "pricing_source": source,
                "usage_available": bool(usage_available),
                "fallback_reason": fallback_reason,
                "warning": warning,
            }
            self.records.append(record)
            self._record_ids.add(record["record_id"])
            self.write()
            self._emit_budget_warning()
            return record

    def record_unavailable(
        self,
        *,
        stage: str,
        agent: str = "",
        provider: str = "",
        model: str = "",
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: float = 0.0,
        fallback_reason: str = "provider_or_opencode_did_not_report_usage",
        record_id: str = "",
    ) -> dict[str, Any]:
        return self.record(
            stage=stage,
            agent=agent,
            provider=provider,
            model=model,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            usage_available=False,
            fallback_reason=fallback_reason,
            record_id=record_id,
        )

    def _emit_budget_warning(self) -> None:
        summary = self.summary()
        cost = summary.get("total_estimated_cost_usd")
        if (
            not self._warned
            and self.warn_limit is not None
            and cost is not None
            and cost >= self.warn_limit
        ):
            self._warned = True
            print(
                f"[TRACEFIX COST WARNING] estimated=${cost:.6f} "
                f"threshold=${self.warn_limit:.6f}",
                flush=True,
            )

    def summary(self) -> dict[str, Any]:
        prompt = sum(_nonnegative_int(record.get("prompt_tokens")) for record in self.records)
        completion = sum(_nonnegative_int(record.get("completion_tokens")) for record in self.records)
        cached = sum(_nonnegative_int(record.get("cached_tokens")) for record in self.records)
        reasoning = sum(_nonnegative_int(record.get("reasoning_tokens")) for record in self.records)
        unavailable = [record for record in self.records if not record.get("usage_available")]
        missing_pricing = [
            record for record in self.records
            if record.get("usage_available")
            and record.get("total_tokens", 0)
            and record.get("estimated_cost_usd") is None
        ]
        known_cost = round(sum(
            float(record.get("estimated_cost_usd") or 0.0)
            for record in self.records
            if record.get("estimated_cost_usd") is not None
        ), 8)
        total_cost = None if missing_pricing else known_cost
        by_stage = self._group_cost("stage")
        by_model = self._group_cost("model")
        highest = max(
            by_stage.items(),
            key=lambda item: float(item[1].get("known_cost_usd") or 0.0),
            default=(None, {}),
        )
        warnings = sorted({
            str(record.get("warning"))
            for record in self.records
            if record.get("warning")
        })
        return {
            "total_prompt_tokens": prompt,
            "total_completion_tokens": completion,
            "total_tokens": prompt + completion,
            "total_cached_tokens": cached,
            "total_reasoning_tokens": reasoning,
            "total_estimated_cost_usd": total_cost,
            "known_cost_usd": known_cost,
            "cost_by_stage": by_stage,
            "cost_by_model": by_model,
            "usage_record_count": len(self.records),
            "usage_unavailable_count": len(unavailable),
            "missing_pricing_count": len(missing_pricing),
            "highest_cost_stage": highest[0],
            "warnings": warnings,
            "warn_cost_usd": self.warn_limit,
            "cost_limit_usd": self.cost_limit,
        }

    def _group_cost(self, key: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for record in self.records:
            name = str(record.get(key) or "unknown")
            group = groups.setdefault(name, {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "known_cost_usd": 0.0,
                "estimated_cost_usd": 0.0,
                "pricing_complete": True,
            })
            group["prompt_tokens"] += _nonnegative_int(record.get("prompt_tokens"))
            group["completion_tokens"] += _nonnegative_int(record.get("completion_tokens"))
            group["total_tokens"] += _nonnegative_int(record.get("total_tokens"))
            cost = record.get("estimated_cost_usd")
            if cost is None and record.get("usage_available") and record.get("total_tokens"):
                group["pricing_complete"] = False
                group["estimated_cost_usd"] = None
            elif cost is not None:
                group["known_cost_usd"] = round(group["known_cost_usd"] + float(cost), 8)
                if group["estimated_cost_usd"] is not None:
                    group["estimated_cost_usd"] = group["known_cost_usd"]
        return groups

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "run_id": self.run_id,
            "workspace": str(self.workspace),
            "generated_at": utc_now(),
            "summary": self.summary(),
            "usage_records": list(self.records),
        }

    def write(self) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            payload = self.payload()
            _atomic_write(
                self.json_path,
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            )
            _atomic_write(self.md_path, _to_markdown(payload))
        except OSError:
            return


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _to_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    cost = summary.get("total_estimated_cost_usd")
    cost_text = "unavailable" if cost is None else f"${cost:.6f}"
    lines = [
        "# TraceFix LLM Usage",
        "",
        f"- Prompt tokens: {summary.get('total_prompt_tokens', 0)}",
        f"- Completion tokens: {summary.get('total_completion_tokens', 0)}",
        f"- Total tokens: {summary.get('total_tokens', 0)}",
        f"- Estimated cost: {cost_text}",
        f"- Highest-cost stage: {summary.get('highest_cost_stage') or 'unavailable'}",
        f"- Usage-unavailable calls: {summary.get('usage_unavailable_count', 0)}",
        f"- Missing-pricing calls: {summary.get('missing_pricing_count', 0)}",
        "",
        "| Stage | Agent | Provider | Model | Input | Output | Cost | Usage |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | :---: |",
    ]
    for record in payload.get("usage_records") or []:
        record_cost = record.get("estimated_cost_usd")
        record_cost_text = "n/a" if record_cost is None else f"${record_cost:.6f}"
        lines.append(
            f"| {record.get('stage')} | {record.get('agent') or '-'} | "
            f"{record.get('provider') or '-'} | {record.get('model') or '-'} | "
            f"{record.get('prompt_tokens', 0)} | {record.get('completion_tokens', 0)} | "
            f"{record_cost_text} | {'yes' if record.get('usage_available') else 'no'} |"
        )
    warnings = summary.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)
