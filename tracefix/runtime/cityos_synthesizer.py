"""Synthesize Docker-buildable CityOS app packages from a TraceFix plan.

This is the production packaging stage after TraceFix verification:

    verified workspace -> spec/cityos_module_plan.json -> CityOS app folders

The generated apps are CityOS service shims that carry the verified TraceFix
bundle (plan, prompt, IR, states, Protocol.tla). They do not re-run TLC or
weaken the verified protocol boundary.
"""

from __future__ import annotations

import json
import ast
import hashlib
import re
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracefix.runtime.cityos_plan import export_cityos_module_plan
from tracefix.textio import safe_read_json, safe_read_text


SYNTHESIS_VERSION = "0.1"
HANDLER_TEMPLATE_MARKER = "# tracefix-handler-template: smartroom-v7"
GENERATED_MARKER = ".tracefix-synthesized"

def _handler_scaffold(kind: str) -> str:
    """Stable, versioned starting point reused for every generated CityOS handler."""
    result_fields = 'status, checks, violations' if 'monitor' in kind else 'answer, evidence, confidence'
    return f'''import json
import os
from urllib.request import urlopen


def main():
    request = json.load(open(os.environ["TRACEFIX_FRAME_PATH"], encoding="utf-8"))
    # LLM: implement the task-specific retrieval and reasoning here.
    result = {{"agent": "", "kind": "{kind}", "{result_fields.split(', ')[0]}": None}}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
'''


def _stable_cache_value(value: Any) -> Any:
    """Remove run-specific fields so equivalent verified tasks share generated code."""
    volatile = {"task_id", "query_id", "run_id", "workspace", "workspace_path", "generated_at", "created_at", "updated_at", "plan_path"}
    if isinstance(value, dict):
        return {str(key): _stable_cache_value(item) for key, item in value.items() if str(key).lower() not in volatile}
    if isinstance(value, list):
        return [_stable_cache_value(item) for item in value]
    if isinstance(value, str):
        normalized = re.sub(r"tellme_structured_smart_room_application_\d+(?:_\d+)?", "<workspace>", value, flags=re.IGNORECASE)
        return re.sub(r"(?:tellme|task)_[a-z0-9]+", "<run>", normalized, flags=re.IGNORECASE)
    return value


def _persistent_handler_cache_dir() -> Path:
    # This is repository-local generated code, never a credential store.
    return Path(__file__).resolve().parents[2] / ".tracefix_agent_cache"


def _cached_handler_source(*, workspace: Path, kind: str, identity: str, plan: dict[str, Any], instructions: str, model: str, api_key: str) -> str:
    key_input = json.dumps({
        "template": "cityos-handler-v7-window-timestamps",
        "kind": kind,
        "identity": identity,
        "plan": _stable_cache_value(plan),
        "instructions": _stable_cache_value(instructions),
        "model": model,
    }, sort_keys=True, default=str)
    digest = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
    cache_paths = [
        _persistent_handler_cache_dir() / f"{digest}.py",
        workspace / "output" / "cityos_handler_cache" / f"{digest}.py",
    ]
    for cache_path in cache_paths:
        if cache_path.is_file():
            try:
                source = _validate_generated_handler(safe_read_text(cache_path))
                # Backfill the workspace-local cache for transparent portability.
                _write_text(cache_paths[1], source)
                return source
            except (SyntaxError, ValueError):
                cache_path.unlink()
    source = _generate_handler_source(kind=kind, identity=identity, plan=plan, instructions=instructions, model=model, api_key=api_key)
    for cache_path in cache_paths:
        _write_text(cache_path, source)
    return source
def _generated_handler_prompt(*, kind: str, identity: str, plan: dict[str, Any], instructions: str) -> str:
    return f"""Write complete Python source for a CityOS {kind} handler named {identity!r}. It must be a distinct implementation based on the verified plan and instructions. It runs as `python generated_handler.py` with TRACEFIX_FRAME_PATH pointing to an agent-request JSON file. That request supplies sourceUrl, sourceMode, question, rawDataJson, and recordingOverride. The smart-room contract is mandatory: recordingOverride is an object, normally {{"recordingId":"day_15_2026-07-21/rec_20260721_164500","day":"day_15_2026-07-21","rec":"rec_20260721_164500"}}; never compare that whole object to a string. Prefer its day+rec values, and treat recordingId as the same two values joined with '/'. The /api/v1/recordings response is not guaranteed to be a flat list: walk nested dicts/lists and preserve enclosing day keys. A usable recording record has a day and rec (or nested day/rec fields), and may contain cameras, metadata, frames, results, occupancy, count, people, or analysis. Build candidates only from real day+rec pairs and emit recordingId as day + '/' + rec; never emit blank IDs. When an override is present, fetch and analyze that exact pair rather than asking again. Inspect the actual JSON response before choosing field paths; do not assume a top-level recordings/data list. Use only standard-library modules. Real API schema observed from the configured mirror: GET sourceUrl + "/recordings" returns one JSON object with a top-level `recordings` array (not a bare list). Each array item is one session with string `day` and `rec`, plus `cameras`, which is a dictionary keyed by camera ID. Each camera contains `models` (model name to status) and `urls.inference`, a URL template ending `/inference/{{model}}`. Choose a camera/model whose status is `done`, replace `{{model}}` with the model name, and GET that inference URL. Inference JSON contains the selected day, rec, camera, model, and model-specific structured results. For action-ava, `detections.tracks` and the number of `persons.persons` keys are cumulative track-ID totals across the whole recording; they are not occupancy and must never be phrased as people simultaneously in the room. To answer occupancy, use `persons.persons[trackId].windows`. Each window is an object shaped like `{{ "t": 33.133, "kept": true, "action": "walk", ... }}`; it has a point timestamp `t`, not `start`/`end` fields. For each numeric `t`, count distinct anonymous track IDs whose window has `kept == true` at that exact timestamp, then take the maximum concurrent count. Do not return zero when valid windows with numeric `t` exist; treat `kept` missing as usable rather than discarding the window. State the result as “up to N people were observed at once during the recording,” not “N people were in the room.” Do not sum people across cameras: use one selected camera only. Include the selected camera, model, and that the result is peak concurrent anonymous tracks in the caveat. Handle non-JSON, HTTP errors, and empty model outputs explicitly in the answer caveats rather than silently replacing them with an empty object. The handler itself must fetch the supplied web-server source when sourceMode is not raw-json (use urllib.request), interpret/process the returned data, and produce the answer. First resolve one exact recording: query the sourceUrl recordings collection (normally sourceUrl + "/recordings" when sourceUrl ends in "/api/v1"), match the requested calendar date and any supplied recordingOverride. If no exact recording can be chosen, return {{"agent": ..., "kind": ..., "answer": {{"needsClarification": true, "clarificationPrompt": "...", "clarificationCandidates": [{{"recordingId": "day/rec", "label": "...", "dateLabel": "...", "timeLabel": "..."}}]}}}} rather than guessing. Never return an ambiguous-recordings message without at least one candidate containing recordingId, label, dateLabel, and timeLabel. Do not rely on any TraceFix smart-room retrieval or answer helper. Read the request file, print one JSON object, exit 0. Do not run subprocesses or use eval/exec. Include main() and an __main__ entry point. An agent returns agent, kind, answer, evidence, confidence. A monitor independently inspects the request and prior result context if supplied, returning agent, kind, status, checks, violations. Start from this required reusable scaffold; preserve its environment/JSON contract and replace the TODO with task-specific code:\n```python\n{_handler_scaffold(kind)}\n```\nReturn the completed Python source only, no Markdown.

Verified plan:\n{json.dumps(plan, indent=2)[:18000]}

Assigned instructions:\n{instructions[:18000]}"""


def _validate_generated_handler(source: str) -> str:
    source = source.strip()
    if source.startswith("```"):
        source = re.sub(r"^```(?:python)?\s*|\s*```$", "", source, flags=re.IGNORECASE).strip()
    tree = ast.parse(source)
    banned = {"subprocess", "socket", "requests", "pathlib", "shutil"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(alias.name.split(".")[0] in banned for alias in node.names):
            raise ValueError("generated handler imports a disallowed module")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "compile"}:
            raise ValueError("generated handler uses a disallowed dynamic execution primitive")
    if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
        raise ValueError("generated handler did not define main()")
    if not source.startswith(HANDLER_TEMPLATE_MARKER):
        source = HANDLER_TEMPLATE_MARKER + "\n" + source
    return source + "\n"


def _completion_text(payload: Any) -> str:
    """Normalize OpenRouter/OpenAI-compatible message content shapes."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts).strip()
    return ""

def _generate_handler_source(*, kind: str, identity: str, plan: dict[str, Any], instructions: str, model: str, api_key: str, repair_attempt: int = 0) -> str:
    if not api_key:
        raise ValueError("CityOS code generation needs the OpenRouter API key from the TraceFix settings.")
    body = json.dumps({"model": model or "z-ai/glm-5.2", "messages": [{"role": "user", "content": _generated_handler_prompt(kind=kind, identity=identity, plan=plan, instructions=instructions)}], "temperature": 0.1, "max_tokens": 24000, "reasoning": {"effort": "medium"}}).encode("utf-8")
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "HTTP-Referer": "https://tracefix.local", "X-Title": "TraceFix CityOS synthesis"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenRouter could not generate CityOS handler code ({exc.code}): {exc.read().decode('utf-8', 'replace')[:500]}") from exc
    content = _completion_text(payload)
    if not content:
        choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        message = choice.get("message") if isinstance(choice, dict) else {}
        if finish_reason == "length" and repair_attempt < 2:
            return _generate_handler_source(
                kind=kind,
                identity=identity,
                plan=plan,
                instructions=(instructions + "\n\nReturn concise, complete Python in under 350 lines. Do not explain your reasoning; output code only."),
                model=model,
                api_key=api_key,
                repair_attempt=repair_attempt + 1,
            )
        raise RuntimeError(
            "OpenRouter returned no CityOS handler source code "
            f"(finish_reason={finish_reason!r}, message_keys={sorted(message) if isinstance(message, dict) else []})."
        )
    try:
        return _validate_generated_handler(content)
    except (SyntaxError, ValueError) as exc:
        if repair_attempt >= 2:
            raise RuntimeError(f"OpenRouter generated invalid CityOS handler code after {repair_attempt + 1} attempts: {exc}") from exc
        repair_instructions = (
            f"{instructions}\n\nThe previous generated Python source was invalid: {exc}. "
            "Return a complete corrected Python file only. Ensure every string and triple-quoted string is closed. "
            f"Previous source:\n```python\n{content[:24000]}\n```"
        )
        return _generate_handler_source(
            kind=kind,
            identity=identity,
            plan=plan,
            instructions=repair_instructions,
            model=model,
            api_key=api_key,
            repair_attempt=repair_attempt + 1,
        )


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
    "TRACEFIX_HANDLER_CMD=python3 /app/{app_name}/generated_handler.py",
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
    "TRACEFIX_HANDLER_CMD=python3 /app/{app_name}/generated_handler.py",
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
    codegen_model: str,
    codegen_api_key: str,
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
    instructions = safe_read_text(app_dir / "tracefix_bundle" / "prompt.md") if (app_dir / "tracefix_bundle" / "prompt.md").exists() else json.dumps(agent, indent=2)
    _write_text(app_dir / "generated_handler.py", _cached_handler_source(workspace=workspace, kind="agent", identity=agent_name, plan=plan, instructions=instructions, model=codegen_model, api_key=codegen_api_key))
    return CityOSAppPackage(name=app_name, path=app_dir, kind="agent", agent=agent_name)


def _write_monitor_app(
    *,
    apps_dir: Path,
    app_name: str,
    workspace: Path,
    plan: dict[str, Any],
    overwrite: bool,
    codegen_model: str,
    codegen_api_key: str,
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
    _write_text(app_dir / "generated_handler.py", _cached_handler_source(workspace=workspace, kind="runtime monitor", identity="monitor", plan=plan, instructions=json.dumps(plan.get("runtime_monitor", {}), indent=2), model=codegen_model, api_key=codegen_api_key))
    return CityOSAppPackage(name=app_name, path=app_dir, kind="monitor")


def synthesize_cityos_apps(
    workspace: Path,
    *,
    apps_dir: Path,
    package_name: str | None = None,
    overwrite: bool = False,
    codegen_model: str = "z-ai/glm-5.2",
    codegen_api_key: str = "",
) -> CityOSSynthesisResult:
    workspace = Path(workspace).expanduser().resolve()
    apps_dir = Path(apps_dir).expanduser().resolve()
    if not workspace.exists():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")

    plan_path, plan = _load_or_export_plan(workspace)
    package = _slug(package_name or f"tracefix-{workspace.name}")
    apps: list[CityOSAppPackage] = []
    for agent in plan.get("agents", []):
        if not isinstance(agent, dict):
            continue
        agent_name = str(agent.get("name") or "agent")
        app_name = _slug(f"{package}-{agent_name}")
        apps.append(_write_agent_app(
            apps_dir=apps_dir,
            app_name=app_name,
            workspace=workspace,
            plan=plan,
            agent=agent,
            overwrite=overwrite,
            codegen_model=codegen_model,
            codegen_api_key=codegen_api_key,
        ))

    monitor_name = _slug(f"{package}-monitor")
    apps.append(_write_monitor_app(
        apps_dir=apps_dir,
        app_name=monitor_name,
        workspace=workspace,
        plan=plan,
        overwrite=overwrite,
        codegen_model=codegen_model,
        codegen_api_key=codegen_api_key,
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
