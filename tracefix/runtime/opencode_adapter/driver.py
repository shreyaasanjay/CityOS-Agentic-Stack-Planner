"""Drive ONE tracefix agent as an OpenCode subprocess and collect its outcome.

Spawns ``opencode run <kickoff> --agent <key> --format json --dir <output>`` with
``OPENCODE_CONFIG_CONTENT`` carrying the per-agent config (scoped tracefix-coord
MCP server + restricted task-denied agent + prompt + model). Streams the JSONL
event stream (schema from opencode ``cli/cmd/run.ts``: each line is
``{type, timestamp, sessionID, part}``; a ``tool_use`` event carries
``part.tool`` / ``part.state.status`` / ``part.state.output``) and reconstructs
the agent's disposition (the dispatcher lives in the separate tracefix-coord
process, so we read its results off the tool stream + the central /monitoring).

This module does NOT import ``mcp`` — only the spawned tracefix-coord child does.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tracefix.runtime.opencode_adapter.config_gen import agent_key, to_env
from tracefix.runtime.usage_tracker import CostLimitExceeded, UsageTracker

#: Kickoff message; the ordered protocol lives in the agent's system prompt.
KICKOFF = ("Begin your task now. Follow your protocol steps in the exact order "
           "given in your instructions, using your coordination tools.")

#: StreamReader buffer ceiling for the opencode subprocess. asyncio defaults to
#: 64 KiB per line; a single JSONL event can blow past that (an assistant
#: message embedding full PlusCal, or a large tool result), and the default
#: makes readline() raise LimitOverrunError and crash the whole run. 64 MiB is a
#: ceiling, not an allocation — only the actual line size is held in memory.
_STREAM_LIMIT = 64 * 1024 * 1024


def _spawnable_command(command: list[str]) -> list[str]:
    """Return a command form that ``create_subprocess_exec`` can launch.

    On Windows, npm places both an extensionless shim and a ``.cmd`` shim on
    PATH. ``where opencode`` finds both, but ``CreateProcess`` cannot execute the
    extensionless script directly. Prefer the Windows command shim when the user
    passes a bare command such as ``opencode``.
    """
    if not command or os.name != "nt":
        return command

    exe = command[0]
    exe_path = Path(exe)
    if exe_path.suffix.lower() in {".exe", ".cmd", ".bat", ".com"}:
        return command

    if exe_path.parent != Path("."):
        for suffix in (".cmd", ".exe", ".bat", ".com"):
            candidate = exe_path.with_suffix(suffix)
            if candidate.exists():
                return [str(candidate), *command[1:]]

    for name in (f"{exe}.cmd", f"{exe}.exe", f"{exe}.bat", f"{exe}.com", exe):
        resolved = shutil.which(name)
        if resolved:
            return [resolved, *command[1:]]
    return command


def _try_json(value) -> dict | None:
    if not isinstance(value, str):
        return value if isinstance(value, dict) else None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _agent_model(config: dict, key: str) -> str | None:
    agent = (config.get("agent") or {}).get(key) or {}
    model = agent.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


class AgentRunState:
    """Accumulates disposition signals from an agent's OpenCode JSONL event stream.

    Pure + synchronous so it is unit-testable without spawning OpenCode.
    """

    def __init__(self) -> None:
        self.events: int = 0
        self.tool_calls: list[dict] = []
        self.signaled_done: bool = False
        self.premature_done: bool = False
        self.correction_limit: bool = False
        self.out_of_order: int = 0
        self.usage_steps: list[dict] = []
        self._last_tokens = {
            "input": 0,
            "output": 0,
            "cached": 0,
            "reasoning": 0,
        }

    def feed(self, ev: dict) -> dict | None:
        self.events += 1
        if ev.get("type") == "step_finish":
            part = ev.get("part") or {}
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            current = {
                "input": _token_value(tokens, "input", "prompt", "prompt_tokens"),
                "output": _token_value(tokens, "output", "completion", "completion_tokens"),
                "cached": (
                    _token_value(tokens, "cached", "cached_tokens")
                    or _token_value(cache, "read", "write")
                ),
                "reasoning": _token_value(tokens, "reasoning", "reasoning_tokens"),
            }
            delta = {
                key: value - self._last_tokens[key]
                if value >= self._last_tokens[key]
                else value
                for key, value in current.items()
            }
            self._last_tokens = current
            step = {
                "prompt_tokens": max(0, delta["input"]),
                "completion_tokens": max(0, delta["output"]),
                "total_tokens": max(0, delta["input"]) + max(0, delta["output"]),
                "cached_tokens": max(0, delta["cached"]),
                "reasoning_tokens": max(0, delta["reasoning"]),
                "cost_usd": _optional_cost(part.get("cost")),
            }
            self.usage_steps.append(step)
            return step
        if ev.get("type") != "tool_use":
            return None
        part = ev.get("part") or {}
        name = part.get("tool") or ""
        st = part.get("state") or {}
        status = st.get("status")
        payload = st.get("output") if status == "completed" else st.get("error")
        result = _try_json(payload)

        tc = {"tool": name, "status": status,
              "result_status": result.get("status") if isinstance(result, dict) else None}
        self.tool_calls.append(tc)

        if not isinstance(result, dict):
            return None
        err = result.get("error")
        # signal_done is namespaced by opencode as <server>_signal_done; also
        # accept any tool whose result status is "done" (the coord contract).
        if name.endswith("signal_done") or result.get("status") == "done":
            self.signaled_done = True
            if result.get("warning"):
                self.premature_done = True
        if err == "correction_limit":
            self.correction_limit = True
        if err == "out_of_order":
            self.out_of_order += 1
        return None


def _token_value(data: dict, *names: str) -> int:
    for name in names:
        value = data.get(name)
        if isinstance(value, (int, float)):
            return max(0, int(value))
    return 0


def _optional_cost(value) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def classify(state: AgentRunState, returncode: int | None, timed_out: bool) -> str:
    """Map the collected signals + process exit to a final agent status."""
    if timed_out:
        return "timeout"
    if state.correction_limit:
        return "correction_failed"
    if state.signaled_done:
        return "premature_done" if state.premature_done else "completed"
    if returncode not in (0, None):
        return "error"
    return "incomplete"


async def _read_lines(stream, on_line: Callable[[str], None]) -> None:
    if stream is None:
        return
    while True:
        try:
            raw = await stream.readline()
        except (asyncio.LimitOverrunError, ValueError):
            # A single JSONL event exceeded even the raised StreamReader buffer
            # (`_STREAM_LIMIT`). Don't let one oversized line kill the whole run:
            # the separator is already buffered, so drain bytes (read() ignores
            # the limit) until we pass the newline, then carry on. That one event
            # is lost to the live feed, but the design's artifacts on disk — the
            # real source of truth — are judged afterward regardless.
            while True:
                chunk = await stream.read(_STREAM_LIMIT)
                if not chunk or b"\n" in chunk:
                    break
            if not chunk:
                break
            continue
        if not raw:
            break
        on_line(raw.decode("utf-8", "replace").rstrip("\r\n"))


async def _terminate(proc, grace: float = 3.0) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


async def run_opencode_agent(
    agent_id: str,
    config: dict,
    *,
    opencode_cmd: list[str],
    output_dir: str | Path,
    kickoff: str = KICKOFF,
    timeout: float = 600.0,
    on_event: Callable[[str, dict], None] | None = None,
    env_overrides: dict | None = None,
    usage_tracker: UsageTracker | None = None,
    usage_stage: str = "opencode",
    procedure: str | None = None,
    template_id: str | None = None,
) -> dict:
    """Run one agent via OpenCode; return its disposition.

    Args:
        agent_id: tracefix agent id.
        config: the per-agent OpenCode config (from ``config_gen.build_agent_config``).
        opencode_cmd: base command for the opencode binary (e.g. ``["opencode"]``).
        output_dir: the agent's working directory (``--dir``), where files land.
        kickoff: the message that starts the run (protocol is in the system prompt).
        timeout: wall-clock cap; on expiry the process is SIGTERM'd then SIGKILL'd.
        on_event: optional ``(agent_id, event_dict)`` callback for live visualization.
        procedure: deterministic procedure associated with this call, for audit logging.
        template_id: selected template associated with this call, if any.
    """
    if procedure in {"exact_reuse", "parameterized_reuse"}:
        raise RuntimeError(f"OpenCode execution is forbidden for {procedure}.")

    key = agent_key(agent_id)
    env = {**os.environ, **to_env(config), **(env_overrides or {})}
    cmd = [*_spawnable_command(opencode_cmd), "run", kickoff, "--agent", key,
           "--format", "json", "--dir", str(output_dir)]
    model = _agent_model(config, key)
    if model:
        cmd.extend(["--model", model])

    started_at = datetime.now(timezone.utc).isoformat()
    started_ms = time.monotonic() * 1000.0
    call_id = uuid.uuid4().hex
    if usage_tracker is not None:
        usage_tracker.ensure_can_call(usage_stage)
    print(
        f"[TRACEFIX LLM START] agent={agent_id} procedure={procedure or 'none'} "
        f"template={template_id or 'none'} model={model or '(opencode default)'} "
        f"started_at={started_at}",
        file=sys.stderr,
        flush=True,
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, env=env, limit=_STREAM_LIMIT,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    state = AgentRunState()
    stderr_tail: list[str] = []
    first_event_at: str | None = None
    first_event_ms: float | None = None
    retry_count = 0
    rate_limit_events = 0
    budget_exceeded = False
    step_started_at = started_at
    step_started_ms = started_ms

    def _on_stdout(line: str) -> None:
        nonlocal first_event_at, first_event_ms, budget_exceeded
        nonlocal step_started_at, step_started_ms
        if not line:
            return
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(ev, dict):
            return
        if first_event_at is None:
            first_event_at = datetime.now(timezone.utc).isoformat()
            first_event_ms = time.monotonic() * 1000.0
            print(
                f"[TRACEFIX LLM FIRST EVENT] agent={agent_id} "
                f"time_to_first_event_ms={first_event_ms - started_ms:.2f}",
                file=sys.stderr,
                flush=True,
            )
        if ev.get("type") == "step_start":
            step_started_at = datetime.now(timezone.utc).isoformat()
            step_started_ms = time.monotonic() * 1000.0
        usage = state.feed(ev)
        if usage is not None:
            ended_at = datetime.now(timezone.utc).isoformat()
            usage_line = {
                "record_id": f"{call_id}:{len(state.usage_steps)}",
                "stage": usage_stage,
                "agent": agent_id,
                "provider": model.split("/", 1)[0] if model and "/" in model else "",
                "model": model or "",
                "started_at": step_started_at,
                "ended_at": ended_at,
                "duration_ms": round(time.monotonic() * 1000.0 - step_started_ms, 2),
                **usage,
            }
            if usage_tracker is not None:
                usage_tracker.record(
                    stage=usage_stage,
                    agent=agent_id,
                    provider=usage_line["provider"],
                    model=usage_line["model"],
                    started_at=usage_line["started_at"],
                    ended_at=usage_line["ended_at"],
                    duration_ms=usage_line["duration_ms"],
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    total_tokens=usage["total_tokens"],
                    cached_tokens=usage["cached_tokens"],
                    reasoning_tokens=usage["reasoning_tokens"],
                    exact_cost_usd=usage["cost_usd"],
                    record_id=usage_line["record_id"],
                )
                try:
                    usage_tracker.ensure_can_call(usage_stage)
                except CostLimitExceeded as exc:
                    budget_exceeded = True
                    usage_line["budget_stop_reason"] = str(exc)
                    if proc.returncode is None:
                        proc.terminate()
            print(
                "[TRACEFIX LLM USAGE] "
                + json.dumps(usage_line, separators=(",", ":")),
                file=sys.stderr,
                flush=True,
            )
        if on_event is not None:
            try:
                on_event(agent_id, ev)
            except Exception:
                pass

    def _on_stderr(line: str) -> None:
        nonlocal retry_count, rate_limit_events
        if line:
            lowered = line.lower()
            if "retry" in lowered or "backoff" in lowered:
                retry_count += 1
            if "rate limit" in lowered or "rate_limit" in lowered or " 429" in lowered:
                rate_limit_events += 1
            stderr_tail.append(line)
            del stderr_tail[:-40]

    async def _pump() -> None:
        await asyncio.gather(
            _read_lines(proc.stdout, _on_stdout),
            _read_lines(proc.stderr, _on_stderr),
        )
        await proc.wait()

    timed_out = False
    try:
        await asyncio.wait_for(_pump(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        await _terminate(proc)
    except asyncio.CancelledError:
        # orchestrator is shutting down — don't leave an orphan opencode process
        await _terminate(proc)
        raise

    finished_at = datetime.now(timezone.utc).isoformat()
    finished_ms = time.monotonic() * 1000.0
    print(
        f"[TRACEFIX LLM END] agent={agent_id} duration_ms={finished_ms - started_ms:.2f} "
        f"retries={retry_count} rate_limit_events={rate_limit_events}",
        file=sys.stderr,
        flush=True,
    )
    provider = model.split("/", 1)[0] if model and "/" in model else None
    if usage_tracker is not None and not state.usage_steps:
        usage_tracker.record_unavailable(
            stage=usage_stage,
            agent=agent_id,
            provider=provider or "",
            model=model or "",
            started_at=started_at,
            ended_at=finished_at,
            duration_ms=finished_ms - started_ms,
            record_id=f"{call_id}:unavailable",
        )
    usage_totals = {
        "prompt_tokens": sum(step["prompt_tokens"] for step in state.usage_steps),
        "completion_tokens": sum(step["completion_tokens"] for step in state.usage_steps),
        "total_tokens": sum(step["total_tokens"] for step in state.usage_steps),
        "cached_tokens": sum(step["cached_tokens"] for step in state.usage_steps),
        "reasoning_tokens": sum(step["reasoning_tokens"] for step in state.usage_steps),
        "cost_usd": round(sum(
            float(step["cost_usd"] or 0.0) for step in state.usage_steps
            if step["cost_usd"] is not None
        ), 8),
    }
    status = "cost_limit" if budget_exceeded else classify(state, proc.returncode, timed_out)
    return {
        "call_id": call_id,
        "agent_id": agent_id,
        "provider": provider,
        "model": model,
        "started_at": started_at,
        "first_event_at": first_event_at,
        "finished_at": finished_at,
        "duration_ms": round(finished_ms - started_ms, 2),
        "time_to_first_event_ms": (
            round(first_event_ms - started_ms, 2) if first_event_ms is not None else None
        ),
        "retry_count": retry_count,
        "rate_limit_events": rate_limit_events,
        "status": status,
        "returncode": proc.returncode,
        "signaled_done": state.signaled_done,
        "premature_done": state.premature_done,
        "correction_limit": state.correction_limit,
        "out_of_order": state.out_of_order,
        "tool_calls": state.tool_calls,
        "events": state.events,
        "stderr_tail": stderr_tail[-10:],
        "usage_available": bool(state.usage_steps),
        "usage_steps": state.usage_steps,
        "usage": usage_totals,
        "budget_exceeded": budget_exceeded,
    }
