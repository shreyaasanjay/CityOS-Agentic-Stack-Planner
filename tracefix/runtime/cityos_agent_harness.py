"""Runtime harness used by generated TraceFix CityOS apps.

TraceFix verification runs outside CityOS. Generated CityOS apps use this module
only for the data-plane side: announce readiness, record incoming CityOS frames,
and optionally invoke a configured app/data handler for each frame.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _optional_path_env(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


def _cmd_env(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return shlex.split(raw) if raw else []


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value) or "item"


@dataclass(frozen=True)
class CityOSHarnessConfig:
    app_kind: str
    agent_id: str
    bundle_dir: Path
    runtime_mode: str
    autorun: bool
    output_dir: Path | None
    ready_dir: Path
    startup_cmd: list[str]
    handler_cmd: list[str]
    handler_timeout: float
    verbose: bool
    task_id: str
    handler_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, default_kind: str, default_agent_id: str) -> "CityOSHarnessConfig":
        bundle_dir = Path(os.environ.get("TRACEFIX_BUNDLE_DIR", "tracefix_bundle")).expanduser()
        return cls(
            app_kind=os.environ.get("TRACEFIX_APP_KIND", default_kind).strip() or default_kind,
            agent_id=os.environ.get("TRACEFIX_AGENT_ID", default_agent_id).strip() or default_agent_id,
            bundle_dir=bundle_dir,
            runtime_mode=os.environ.get("TRACEFIX_RUNTIME_MODE", "cityos_data").strip() or "cityos_data",
            autorun=_bool_env("TRACEFIX_AUTORUN"),
            output_dir=_optional_path_env("TRACEFIX_OUTPUT_DIR"),
            ready_dir=Path(os.environ.get("TRACEFIX_READY_DIR", "/run/cityos")).expanduser(),
            startup_cmd=_cmd_env("TRACEFIX_STARTUP_CMD"),
            handler_cmd=_cmd_env("TRACEFIX_HANDLER_CMD"),
            handler_timeout=_float_env("TRACEFIX_HANDLER_TIMEOUT", 60.0),
            verbose=_bool_env("TRACEFIX_VERBOSE"),
            task_id=os.environ.get("TRACEFIX_TASK_ID", "").strip(),
            handler_env={},
        )


class CityOSAgentHarness:
    """SDK-free data-plane harness used by generated CityOS service shims."""

    def __init__(self, config: CityOSHarnessConfig):
        self.config = config
        self.plan = _read_json(config.bundle_dir / "plan.json", {})
        self.agent = _read_json(config.bundle_dir / "agent.json", {})
        self.monitor = _read_json(config.bundle_dir / "monitor.json", {})
        self._startup_task: asyncio.Task | None = None

    @classmethod
    def from_env(cls, *, default_kind: str, default_agent_id: str) -> "CityOSAgentHarness":
        return cls(CityOSHarnessConfig.from_env(
            default_kind=default_kind,
            default_agent_id=default_agent_id,
        ))

    @property
    def workspace(self) -> Path:
        return self.config.bundle_dir / "workspace"

    @property
    def run_dir(self) -> Path:
        return self.config.output_dir or (self.config.bundle_dir / "run")

    @property
    def task_id(self) -> str:
        if self.config.task_id:
            return self.config.task_id
        application = self.plan.get("application") if isinstance(self.plan, dict) else None
        if isinstance(application, dict):
            name = str(application.get("name") or "").strip()
            if name:
                return name
        return self.workspace.name or "tracefix-cityos"

    def prompt_path(self) -> Path | None:
        direct = self.config.bundle_dir / "prompt.md"
        if direct.exists():
            return direct
        prompt_path = self.agent.get("prompt_path") if isinstance(self.agent, dict) else None
        if isinstance(prompt_path, str) and prompt_path.strip():
            candidate = self.workspace / prompt_path
            if candidate.exists():
                return candidate
        for rel in (
            Path("prompts") / "runtime_b" / f"{self.config.agent_id}.md",
            Path("prompts") / f"{self.config.agent_id}.md",
        ):
            candidate = self.workspace / rel
            if candidate.exists():
                return candidate
        return None

    def readiness_payload(self) -> dict[str, Any]:
        prompt = self.prompt_path()
        return {
            "kind": self.config.app_kind,
            "agent": self.config.agent_id,
            "task_id": self.task_id,
            "runtime_mode": self.config.runtime_mode,
            "bundle": str(self.config.bundle_dir),
            "workspace": str(self.workspace),
            "output_dir": str(self.run_dir),
            "plan_version": self.plan.get("version") if isinstance(self.plan, dict) else None,
            "autorun": self.config.autorun,
            "startup_configured": bool(self.config.startup_cmd),
            "handler_configured": bool(self.config.handler_cmd),
            "prompt_path": str(prompt) if prompt else None,
            "started_at": datetime.utcnow().isoformat() + "Z",
        }

    async def on_started(self) -> None:
        logging.info("TraceFix CityOS %s app started: %s", self.config.app_kind, self.config.agent_id)
        logging.info("TraceFix bundle: %s", self.config.bundle_dir)
        await self.write_readiness()
        if self.config.autorun and self.config.startup_cmd:
            self._startup_task = asyncio.create_task(self.run_startup_program())
        elif self.config.autorun:
            logging.info("TRACEFIX_AUTORUN set; no TRACEFIX_STARTUP_CMD configured, waiting for CityOS frames")

    async def write_readiness(self) -> Path:
        ready_dir = self.config.ready_dir
        try:
            ready_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            ready_dir = self.run_dir
            ready_dir.mkdir(parents=True, exist_ok=True)
        path = ready_dir / f"tracefix_{self.config.app_kind}_{_safe_name(self.config.agent_id)}_ready.json"
        path.write_text(json.dumps(self.readiness_payload(), indent=2) + "\n", encoding="utf-8")
        logging.info("TraceFix CityOS readiness written: %s", path)
        return path

    async def receive_frame(self, stream_name: str, input_path: Path, timestamp: datetime) -> None:
        logging.info(
            "TraceFix CityOS %s app %s received frame stream=%s path=%s timestamp=%s",
            self.config.app_kind,
            self.config.agent_id,
            stream_name,
            input_path,
            timestamp,
        )
        frame_record = await self.record_frame(stream_name, input_path, timestamp)
        if self.config.handler_cmd:
            result = await self.run_frame_handler(frame_record)
            await self.write_handler_result(frame_record, result)

    async def record_frame(self, stream_name: str, input_path: Path, timestamp: datetime) -> dict[str, Any]:
        frames_dir = self.run_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        input_path = Path(input_path)
        exists = input_path.exists()
        record = {
            "kind": self.config.app_kind,
            "agent": self.config.agent_id,
            "task_id": self.task_id,
            "stream": stream_name,
            "input_path": str(input_path),
            "input_exists": exists,
            "input_size_bytes": input_path.stat().st_size if exists and input_path.is_file() else None,
            "timestamp": timestamp.isoformat(),
            "recorded_at": datetime.utcnow().isoformat() + "Z",
            "bundle": str(self.config.bundle_dir),
            "workspace": str(self.workspace),
        }
        name = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S-%f')}_{_safe_name(stream_name)}.json"
        record_path = frames_dir / name
        record["record_path"] = str(record_path)
        record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return record

    async def run_startup_program(self) -> dict[str, Any]:
        result = await self._run_command(
            self.config.startup_cmd,
            extra_env={"TRACEFIX_EVENT": "startup"},
            timeout=None,
        )
        await self.write_run_result("startup", result)
        return result

    async def run_frame_handler(self, frame_record: dict[str, Any]) -> dict[str, Any]:
        return await self._run_command(
            self.config.handler_cmd,
            extra_env={
                "TRACEFIX_EVENT": "frame",
                "TRACEFIX_FRAME_STREAM": str(frame_record["stream"]),
                "TRACEFIX_FRAME_PATH": str(frame_record["input_path"]),
                "TRACEFIX_FRAME_TIMESTAMP": str(frame_record["timestamp"]),
                "TRACEFIX_FRAME_RECORD": str(frame_record["record_path"]),
                **self.config.handler_env,
            },
            timeout=self.config.handler_timeout,
        )

    async def _run_command(
        self,
        command: list[str],
        *,
        extra_env: dict[str, str],
        timeout: float | None,
    ) -> dict[str, Any]:
        if not command:
            return {"status": "skipped", "reason": "no command configured"}
        env = os.environ.copy()
        env.update({
            "TRACEFIX_APP_KIND": self.config.app_kind,
            "TRACEFIX_AGENT_ID": self.config.agent_id,
            "TRACEFIX_BUNDLE_DIR": str(self.config.bundle_dir),
            "TRACEFIX_WORKSPACE_DIR": str(self.workspace),
            "TRACEFIX_OUTPUT_DIR": str(self.run_dir),
            "TRACEFIX_TASK_ID": self.task_id,
        })
        env.update(extra_env)
        cwd = self.workspace if self.workspace.exists() else self.config.bundle_dir
        logging.info("Starting CityOS data handler: %s", " ".join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd),
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", "replace") if stdout else ""
            return {
                "status": "completed" if process.returncode == 0 else "failed",
                "returncode": process.returncode,
                "command": command,
                "output": output,
                "finished_at": datetime.utcnow().isoformat() + "Z",
            }
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {
                "status": "timeout",
                "returncode": process.returncode,
                "command": command,
                "timeout_seconds": timeout,
                "finished_at": datetime.utcnow().isoformat() + "Z",
            }

    async def write_handler_result(self, frame_record: dict[str, Any], result: dict[str, Any]) -> Path:
        record_path = Path(str(frame_record["record_path"]))
        path = record_path.with_name(record_path.stem + "_handler.json")
        payload = {"frame": frame_record, "handler": result}
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        return path

    async def write_run_result(self, name: str, payload: dict[str, Any]) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / f"last_{self.config.app_kind}_{_safe_name(self.config.agent_id)}_{_safe_name(name)}.json"
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        return path
