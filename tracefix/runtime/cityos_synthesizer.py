"""Synthesize Docker-buildable CityOS app packages from a TraceFix plan.

This is the production packaging stage after TraceFix verification:

    verified workspace -> spec/cityos_module_plan.json -> CityOS app folders

The generated apps are CityOS service shims that carry the verified TraceFix
bundle (plan, prompt, IR, states, Protocol.tla). They do not re-run TLC or
weaken the verified protocol boundary.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracefix.runtime.cityos_plan import export_cityos_module_plan
from tracefix.textio import safe_read_json, safe_read_text


SYNTHESIS_VERSION = "0.1"
GENERATED_MARKER = ".tracefix-synthesized"


@dataclass(frozen=True)
class CityOSAppPackage:
    name: str
    path: Path
    kind: str
    agent: str | None = None


@dataclass(frozen=True)
class CityOSSynthesisResult:
    workspace: Path
    plan_path: Path
    apps_dir: Path
    manifest_path: Path
    apps: list[CityOSAppPackage]


def _slug(value: str, *, fallback: str = "tracefix") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:72].strip("-") or fallback


def _module_name(app_name: str) -> str:
    return _slug(app_name).replace("-", "_")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=2) + "\n")


def _spec_dir(workspace: Path) -> Path:
    spec = workspace / "spec"
    return spec if spec.is_dir() else workspace


def _plan_path(workspace: Path) -> Path:
    return _spec_dir(workspace) / "cityos_module_plan.json"


def _load_or_export_plan(workspace: Path) -> tuple[Path, dict[str, Any]]:
    plan_path = _plan_path(workspace)
    if not plan_path.exists():
        export_cityos_module_plan(workspace)
    plan = safe_read_json(plan_path, {})
    if not isinstance(plan, dict):
        raise ValueError(f"invalid CityOS module plan: {plan_path}")
    verification = plan.get("verification", {})
    if verification.get("production_ready") is not True:
        # Plan may be stale — written before TLC passed (e.g. during an intermediate
        # attempt). Re-export from current workspace artifacts if summary.json shows
        # tlc_passed: True, which means verification has since completed.
        spec = _spec_dir(workspace)
        summary = safe_read_json(spec / "summary.json", {})
        tlc_passed = isinstance(summary, dict) and summary.get("tlc_passed") is True
        if tlc_passed:
            export_cityos_module_plan(workspace)
            plan = safe_read_json(plan_path, {})
            if not isinstance(plan, dict):
                raise ValueError(f"invalid CityOS module plan after re-export: {plan_path}")
            verification = plan.get("verification", {})
        if verification.get("production_ready") is not True:
            status = verification.get("status", "unknown")
            missing = [
                name for name in ("ir.json", "states.json", "Protocol.tla", "Protocol.cfg")
                if not (spec / name).exists()
            ]
            raise ValueError(
                "cannot synthesize CityOS apps before successful TraceFix "
                f"verification; current status is {status!r}. "
                f"workspace: {workspace}; "
                f"cityos_module_plan.json exists: {plan_path.exists()}; "
                f"tlc_passed (summary.json): {tlc_passed}; "
                f"missing spec files: {missing or 'none'}"
            )
    return plan_path, plan


def _copy_bundle_artifacts(workspace: Path, app_dir: Path, plan: dict[str, Any]) -> None:
    bundle = app_dir / "tracefix_bundle"
    _write_json(bundle / "plan.json", plan)
    spec = _spec_dir(workspace)
    for name in (
        "ir.json",
        "states.json",
        "summary.json",
        "Protocol.tla",
        "Protocol.cfg",
        "Protocol_translated.tla",
        "cityos_module_plan.json",
    ):
        source = spec / name
        if source.exists():
            _write_text(bundle / "spec" / name, safe_read_text(source))
            _write_text(bundle / "workspace" / "spec" / name, safe_read_text(source))

    for root_name in ("description.md", "tools.json", "metadata.json"):
        source = workspace / root_name
        if source.exists():
            _write_text(bundle / "workspace" / root_name, safe_read_text(source))

    prompt_root = workspace / "prompts"
    if prompt_root.exists():
        for prompt in prompt_root.rglob("*.md"):
            rel = prompt.relative_to(workspace)
            _write_text(bundle / "workspace" / rel, safe_read_text(prompt))


def _copy_tracefix_runtime(app_dir: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    target = app_dir / "tracefix"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def _prepare_app_dir(app_dir: Path, *, overwrite: bool) -> None:
    if app_dir.exists():
        marker = app_dir / GENERATED_MARKER
        if not marker.exists():
            raise FileExistsError(
                f"refusing to overwrite non-TraceFix CityOS app directory: {app_dir}"
            )
        if not overwrite:
            raise FileExistsError(
                f"CityOS app directory already exists: {app_dir}; pass overwrite=True"
            )
    app_dir.mkdir(parents=True, exist_ok=True)
    _write_text(app_dir / GENERATED_MARKER, datetime.now(timezone.utc).isoformat() + "\n")


def _requirements_txt() -> str:
    # CityOS app containers run data handlers only. TraceFix verification and LLM
    # execution happen outside CityOS before synthesis.
    return ""

def _dockerfile(app_name: str, module: str) -> str:
    return f"""FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/{app_name}
WORKDIR /app

RUN apt-get update \\
    && apt-get install -y --no-install-recommends ca-certificates \\
    && rm -rf /var/lib/apt/lists/*
COPY apps/sdk/python/requirements.txt ./sdk/python/
COPY apps/{app_name}/requirements.txt ./{app_name}/
RUN pip install --no-cache-dir \\
    -r sdk/python/requirements.txt \\
    -r {app_name}/requirements.txt

COPY apps/{app_name}/ {app_name}/
COPY apps/sdk/python/ {app_name}/sdk/python/
COPY apps/sdk/cityos.proto {app_name}/sdk/

RUN mkdir -p {app_name}/sdk/python/_autogen/grpc
RUN python -m grpc_tools.protoc \\
    -I {app_name}/sdk/ \\
    --python_out={app_name}/sdk/python/_autogen/grpc \\
    --grpc_python_out={app_name}/sdk/python/_autogen/grpc \\
    cityos.proto

CMD ["python3", "{app_name}/{module}.py"]
"""


def _agent_toml(app_name: str, agent_name: str) -> str:
    return f"""TarFile = "cityos-{app_name}.tar"
ImageName = "cityos-{app_name}"
ApiLevel = 2
Trusted = true
Persistent = true
StandbyPolicy = "none"

InputStreams = []

ExtraEnv = [
    "TRACEFIX_APP_KIND=agent",
    "TRACEFIX_AGENT_ID={agent_name}",
    "TRACEFIX_BUNDLE_DIR=/app/{app_name}/tracefix_bundle",
    "TRACEFIX_RUNTIME_MODE=cityos_data",
    "TRACEFIX_AUTORUN=0",
    "TRACEFIX_OUTPUT_DIR=/app/{app_name}/tracefix_output",
    "TRACEFIX_READY_DIR=/run/cityos",
    "TRACEFIX_STARTUP_CMD=",
    "TRACEFIX_HANDLER_CMD=python3 -m tracefix.runtime.web_data_agent",
    "TRACEFIX_HANDLER_TIMEOUT=60",
    "TRACEFIX_VERBOSE=0",
]

[OutputStreams.tracefix-events]
AllowedReaders = []
"""


def _monitor_toml(app_name: str) -> str:
    return f"""TarFile = "cityos-{app_name}.tar"
ImageName = "cityos-{app_name}"
ApiLevel = 2
Trusted = true
Persistent = true
StandbyPolicy = "none"

InputStreams = []

ExtraEnv = [
    "TRACEFIX_APP_KIND=monitor",
    "TRACEFIX_BUNDLE_DIR=/app/{app_name}/tracefix_bundle",
    "TRACEFIX_RUNTIME_MODE=cityos_data",
    "TRACEFIX_AUTORUN=0",
    "TRACEFIX_OUTPUT_DIR=/app/{app_name}/tracefix_output",
    "TRACEFIX_READY_DIR=/run/cityos",
    "TRACEFIX_STARTUP_CMD=",
    "TRACEFIX_HANDLER_CMD=python3 -m tracefix.runtime.web_data_agent",
    "TRACEFIX_HANDLER_TIMEOUT=60",
    "TRACEFIX_VERBOSE=0",
]

[OutputStreams.tracefix-monitor]
AllowedReaders = []
"""


def _app_py(app_kind: str, display_name: str) -> str:
    class_name = "TraceFixAgentApp" if app_kind == "agent" else "TraceFixMonitorApp"
    return f'''from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from sdk.python.cityos import CityosServicer
from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness


class {class_name}(CityosServicer):
    def __init__(self):
        super().__init__()
        self.harness = CityOSAgentHarness.from_env(
            default_kind="{app_kind}",
            default_agent_id="{display_name}",
        )

    async def on_started(self) -> None:
        await self.harness.on_started()

    async def receive_frame(self, stream_name: str, input_path: Path, timestamp: datetime) -> None:
        await self.harness.receive_frame(stream_name, input_path, timestamp)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run({class_name}().start())
'''

def _write_agent_app(
    *,
    apps_dir: Path,
    app_name: str,
    workspace: Path,
    plan: dict[str, Any],
    agent: dict[str, Any],
    overwrite: bool,
) -> CityOSAppPackage:
    app_dir = apps_dir / app_name
    _prepare_app_dir(app_dir, overwrite=overwrite)
    module = _module_name(app_name)
    agent_name = str(agent.get("name") or app_name)
    _write_text(app_dir / "Dockerfile", _dockerfile(app_name, module))
    _write_text(app_dir / "requirements.txt", _requirements_txt())
    _write_text(app_dir / "cityos-app.toml", _agent_toml(app_name, agent_name))
    _write_text(app_dir / f"{module}.py", _app_py("agent", agent_name))
    _copy_tracefix_runtime(app_dir)
    _copy_bundle_artifacts(workspace, app_dir, plan)
    _write_json(app_dir / "tracefix_bundle" / "agent.json", agent)
    prompt_path = agent.get("prompt_path")
    if isinstance(prompt_path, str) and prompt_path:
        prompt_source = workspace / prompt_path
        if prompt_source.exists():
            _write_text(app_dir / "tracefix_bundle" / "prompt.md", safe_read_text(prompt_source))
    return CityOSAppPackage(name=app_name, path=app_dir, kind="agent", agent=agent_name)


def _write_monitor_app(
    *,
    apps_dir: Path,
    app_name: str,
    workspace: Path,
    plan: dict[str, Any],
    overwrite: bool,
) -> CityOSAppPackage:
    app_dir = apps_dir / app_name
    _prepare_app_dir(app_dir, overwrite=overwrite)
    module = _module_name(app_name)
    _write_text(app_dir / "Dockerfile", _dockerfile(app_name, module))
    _write_text(app_dir / "requirements.txt", _requirements_txt())
    _write_text(app_dir / "cityos-app.toml", _monitor_toml(app_name))
    _write_text(app_dir / f"{module}.py", _app_py("monitor", "monitor"))
    _copy_tracefix_runtime(app_dir)
    _copy_bundle_artifacts(workspace, app_dir, plan)
    _write_json(app_dir / "tracefix_bundle" / "monitor.json", plan.get("runtime_monitor", {}))
    return CityOSAppPackage(name=app_name, path=app_dir, kind="monitor")


def _execution_agents(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return plan agents plus any roles required by the data-answer runtime.

    TraceFix protocols may legitimately contain only one logical agent, but the
    CityOS execution boundary keeps retrieval and answer generation in separate
    processes.  The monitor is packaged separately below.
    """
    agents = [dict(agent) for agent in plan.get("agents", []) if isinstance(agent, dict)]
    labels = [str(agent.get("name") or "").lower() for agent in agents]

    has_answer = any("answer" in label or "synth" in label for label in labels)
    if not has_answer:
        agents.append({
            "name": "TRACEFIX_ANSWER",
            "runtime_role": "answer",
            "generated_by": "cityos_execution_role_enforcement",
        })

    answer_index = next(
        index
        for index, agent in enumerate(agents)
        if "answer" in str(agent.get("name") or "").lower()
        or "synth" in str(agent.get("name") or "").lower()
    )
    if not any(index != answer_index for index in range(len(agents))):
        agents.insert(0, {
            "name": "TRACEFIX_RETRIEVER",
            "runtime_role": "retriever",
            "generated_by": "cityos_execution_role_enforcement",
        })
    return agents


def synthesize_cityos_apps(
    workspace: Path,
    *,
    apps_dir: Path,
    package_name: str | None = None,
    overwrite: bool = False,
) -> CityOSSynthesisResult:
    workspace = Path(workspace).expanduser().resolve()
    apps_dir = Path(apps_dir).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")

    plan_path, plan = _load_or_export_plan(workspace)
    package = _slug(package_name or f"tracefix-{workspace.name}")
    apps: list[CityOSAppPackage] = []
    for agent in _execution_agents(plan):
        agent_name = str(agent.get("name") or "agent")
        app_name = _slug(f"{package}-{agent_name}")
        apps.append(_write_agent_app(
            apps_dir=apps_dir,
            app_name=app_name,
            workspace=workspace,
            plan=plan,
            agent=agent,
            overwrite=overwrite,
        ))

    monitor_name = _slug(f"{package}-monitor")
    apps.append(_write_monitor_app(
        apps_dir=apps_dir,
        app_name=monitor_name,
        workspace=workspace,
        plan=plan,
        overwrite=overwrite,
    ))

    manifest = {
        "artifact_type": "tracefix_cityos_synthesis_manifest",
        "version": SYNTHESIS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace),
        "plan_path": str(plan_path),
        "apps_dir": str(apps_dir),
        "apps": [
            {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app.path),
                "build_command": f"just build app={app.name}",
            }
            for app in apps
        ],
    }
    manifest_path = apps_dir / f"{package}-synthesis.json"
    _write_json(manifest_path, manifest)
    return CityOSSynthesisResult(
        workspace=workspace,
        plan_path=plan_path,
        apps_dir=apps_dir,
        manifest_path=manifest_path,
        apps=apps,
    )
