"""``tracefix design`` — drive an UNMODIFIED headless opencode through the
/tla-verify-pluscal skill to turn a natural-language requirement into a
verified workspace (spec/ + prompts/runtime_b/ + spec/cityos_module_plan.json).

Architecture: the design *knowledge* lives in the skill (SKILL.md + references)
and the *checks* live in the ``tla-verify-pluscal`` CLI — both harness-portable.
opencode is just the engine: this orchestrator injects the skill as the agent's
system prompt (plus a headless preamble), points ``--dir`` at the repo root (so
``.claude/skills/`` references and ``workspace/`` resolve), and judges the
outcome from the ARTIFACTS the run leaves behind, never from the transcript.

No opencode source modification — same principle as the runtime adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from tracefix.pipeline_timing import PipelineTimingReport
from tracefix.protocol_templates import get_template, list_pattern_ids
from tracefix.runtime.audit import audit_json_text, write_audit_json, write_audit_text
from tracefix.runtime.deterministic_template_engine import DeterministicTemplateEngine
from tracefix.runtime.llm_attribute_extractor import extract_coordination_attributes
from tracefix.runtime.procedure_execution import (
    ProcedureExecutionError,
    instantiate_exact_reuse,
    instantiate_parameterized_reuse,
)
from tracefix.runtime.procedure_prompt import (
    build_procedure_execution_context,
    build_procedure_execution_prompt,
)
from tracefix.runtime.single_agent_fastpath import (
    FastPathDecision,
    _extract_structured_task,
    assess_single_agent_fast_path,
    build_single_agent_generation_input,
    generate_single_agent_ir,
    render_verified_runtime_prompt,
)
from tracefix.runtime.template_promotion import promote_verified_workspace_template
from tracefix.runtime.taskspec_attribute_validation import (
    MAX_ATTRIBUTE_CORRECTION_ATTEMPTS,
    extract_with_taskspec_reevaluation,
)
from tracefix.textio import safe_read_json, safe_read_text
from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.driver import run_opencode_agent

#: Where the design-workflow skills live, relative to the repo root.
_SKILL = ".claude/skills/tla-verify-pluscal/SKILL.md"
_PROMPT_GEN_SKILL = ".claude/skills/tla-prompt-gen/SKILL.md"

#: A full design+verify+prompts run includes several TLC invocations and up to
#: 5 repair attempts — give it real time by default.
DEFAULT_TIMEOUT = 1800.0


def _print_audit_block(label: str, payload: object) -> None:
    print(f"[TRACEFIX {label} START]", flush=True)
    print(audit_json_text(payload), end="", flush=True)
    print(f"[TRACEFIX {label} END]", flush=True)


def _execution_artifacts(ws: Path) -> list[str]:
    spec = _spec_dir(ws)
    paths = [spec / "ir.json", spec / "Protocol.tla", spec / "Protocol.cfg", spec / "states.json"]
    artifacts: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        if path.name == "ir.json" and _looks_like_init_stub(safe_read_json(path, {})):
            continue
        artifacts.append(str(path.relative_to(ws)).replace("\\", "/"))
    return artifacts


def _execution_marker_name(mode: str) -> str:
    return {
        "exact_reuse": "EXACT REUSE",
        "parameterized_reuse": "PARAMETERIZED REUSE",
        "partial_recomposition": "PARTIAL RECOMPOSITION",
        "full_generation": "FULL GENERATION",
        "single_agent_generation": "SINGLE AGENT GENERATION",
    }[mode]


def _generate_and_verify_single_agent(
    ws: Path,
    decision: FastPathDecision,
    timing: PipelineTimingReport,
) -> list[str]:
    """Run the existing deterministic one-agent generation and verification path."""

    from tracefix.pipeline.pipeline.validator import normalize_ir

    diagnostics: list[str] = []
    fast_ir = normalize_ir(generate_single_agent_ir(decision))
    (_spec_dir(ws) / "ir.json").write_text(
        json.dumps(fast_ir, indent=2) + "\n",
        encoding="utf-8",
    )
    valid_fast_ir, fast_ir_errors, fast_ir_diagnostics = validate_design_ir(ws)
    diagnostics.extend(fast_ir_diagnostics)
    if not valid_fast_ir:
        raise ValueError("; ".join(fast_ir_errors))
    diagnostics.extend(_scaffold_valid_ir(ws))
    diagnostics.extend(_run_tlc_and_extract(ws, timing))
    fast_status, fast_errors, fast_diagnostics = classify_design_artifacts(ws)
    diagnostics.extend(fast_diagnostics)
    if fast_status != "ready":
        raise ValueError(
            "; ".join(fast_errors)
            or f"deterministic verification ended with status {fast_status}"
        )
    diagnostics.append("Single-agent generation completed deterministic verification")
    return diagnostics


def _write_deterministic_reuse_prompts(ws: Path, task: str) -> list[str]:
    """Render minimal verified-plan prompts without invoking OpenCode."""

    plan = safe_read_json(_spec_dir(ws) / "cityos_module_plan.json", {})
    verification = plan.get("verification")
    if not isinstance(verification, dict) or not verification.get("production_ready"):
        raise ValueError("deterministic reuse prompts require a production-ready CityOS plan")
    agents = plan.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("verified CityOS plan has no agents")
    prompt_dir = ws / "prompts" / "runtime_b"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for agent in agents:
        if not isinstance(agent, dict) or not agent.get("name"):
            continue
        agent_id = str(agent["name"])
        prompt = "\n".join([
            f"# {agent_id} Verified Template Runtime",
            "",
            "## Read-only task",
            task,
            "",
            "## Deterministic runtime contract",
            "- Execute only the verified Template protocol in spec/states.json.",
            "- Follow the verified CityOS module plan and declared coordination operations.",
            "- Do not redesign the protocol or alter canonical Template attributes.",
            "- Signal completion only after reaching a legal terminal state.",
            "",
        ])
        path = prompt_dir / f"{agent_id}.md"
        path.write_text(prompt, encoding="utf-8")
        paths.append(str(path))
    if not paths:
        raise ValueError("verified CityOS plan yielded no deterministic runtime prompts")
    return paths


def _subprocess_python() -> str:
    return os.environ.get("TRACEFIX_PYTHON_EXE", "").strip() or sys.executable


def repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents
                if (p / "pyproject.toml").exists())


def _attribute_extractor_model(model: str | None) -> str | None:
    configured = (os.getenv("TRACEFIX_LLM_ATTRIBUTE_EXTRACTOR_MODEL") or "").strip()
    if configured:
        return configured
    value = (model or "").strip()
    if value.startswith("openrouter/"):
        return value.removeprefix("openrouter/")
    if value.startswith("openai/"):
        return value.removeprefix("openai/")
    return value or None


def slugify(task: str) -> str:
    """A short filesystem-safe workspace name derived from the task text."""
    words = re.findall(r"[a-z0-9]+", task.lower())[:5]
    return "_".join(words) or "design"


_HEADLESS_PREAMBLE = """\
# Headless mode (no interactive user)

You are running non-interactively inside `tracefix design`. Follow the workflow
below with these adjustments:
- NEVER pause to ask the user anything. At the Phase 1.5 review gate, write
  `plan.md` into the workspace and proceed immediately (it is kept for
  after-the-fact review).
- TraceFix has already extracted canonical Template attributes and selected a
  deterministic procedure. Treat both as authoritative. Never rediscover,
  rename, replace, or contradict roles, patterns, flow, counts, limitations,
  or procedure. Derive only low-level IR/PlusCal/TLC/CityOS implementation
  details within those fixed boundaries and record those details in `plan.md`.
- The IR schema allows ONLY these topology fields: `agents`, `resources`, and
  `channels` (plus documented planner metadata such as `state_tasks`,
  `agent_resources`, and `tool_resource_map`). Do NOT emit top-level or nested
  `locks`, `counters`, `permissions`, `edges`, `messages`, or other ad hoc
  schema fields. Lock-like behavior is encoded as
  `{"id": "RESOURCE_ID", "type": "Lock"}` inside `resources`; counter-like
  behavior is encoded as `{"id": "POOL_ID", "type": "Counter", "config":
  {"initial": N}}` inside `resources`.
- Typed tools: most domain work runs on builtins (read/write/edit/bash). If a
  step needs a structured typed tool (a real external API, or custom typed
  logic), tag it in PlusCal `[tool: ...; impl: external|local]` and list it
  (name, owning agent, impl kind) under `## Assumptions` — extract-states turns
  the tags into a generated `tools.json` + impl stub the user binds later.
- "Never pause" means do NOT ask the user — it does NOT mean skip the checks.
  Still do the SKILL.md Phase 1.5 self-critique (verify every hazard, channel,
  ordering constraint, and failure path is represented against the requirement)
  and the MANDATORY Phase 2.5 fidelity check before verifying.
- Where the workflow says to invoke the `/tla-prompt-gen` skill (Phase 5), read
  `{prompt_gen_skill}` and follow it directly on this same workspace instead.
- The skill's reference files live under `.claude/skills/tla-verify-pluscal/references/`
  — read them when the workflow points to them.
- Work ONLY inside the workspace directory given in the task message; run the
  `tla-verify-pluscal` CLI via bash (it is on PATH).
- If verification still fails after 5 attempts, write an honest failure report
  and stop. Never create or edit `summary.json`; its `tlc_passed` value belongs
  exclusively to the deterministic TraceFix TLC execution gate.
- After TLC passes and `states.json` is extracted, export/read
  `spec/cityos_module_plan.json` before generating Runtime B prompts. Runtime
  prompts must be downstream of the verified module plan, never raw IR alone.

---

"""


def build_designer_prompt(root: Path) -> str:
    """The designer agent's system prompt: headless preamble + the skill itself."""
    skill_text = safe_read_text(root / _SKILL)
    # Strip the YAML frontmatter (invocation metadata, meaningless to opencode).
    if skill_text.startswith("---"):
        end = skill_text.find("\n---", 3)
        if end != -1:
            skill_text = skill_text[end + 4:]
    phase1 = skill_text.find("### Phase 1: Structured Analysis")
    phase2 = skill_text.find("### Phase 2: Write PlusCal Process Bodies", phase1)
    if phase1 != -1 and phase2 != -1:
        skill_text = (
            skill_text[:phase1]
            + "### Phase 1: Structured Analysis\n\n"
            + "Read the authoritative procedure execution context and canonical Template attributes "
            + "provided by TraceFix. Do not identify or rediscover agents, roles, coordination patterns, "
            + "communication flow, counts, limitations, or procedure from description.md. Validate the "
            + "existing IR against those fixed values. Derive only schema-valid low-level implementation "
            + "details for attributes explicitly marked unknown, without contradicting any canonical value.\n\n"
            + skill_text[phase2:]
        )
    # The reusable skill predates the canonical runtime ownership boundary.
    # Replace its verification bookkeeping section in the composed prompt so
    # OpenCode can run tools but cannot author the TLC verdict consumed later.
    phase3 = skill_text.find("### Phase 3: Verify & Repair")
    phase4 = skill_text.find("### Phase 4", phase3)
    if phase3 != -1 and phase4 != -1:
        skill_text = (
            skill_text[:phase3]
            + "### Phase 3: Verify & Repair\n\n"
            + "Run the verification tool and repair Protocol.tla up to five times. "
            + "Never create or edit summary.json or tlc_passed; TraceFix's deterministic "
            + "post-scaffold TLC gate owns that verdict. Record optional repair history "
            + "in repair_notes.md.\n\n"
            + skill_text[phase4:]
        )
    preamble = _HEADLESS_PREAMBLE.replace("{prompt_gen_skill}", _PROMPT_GEN_SKILL)
    return preamble + skill_text.lstrip("\n")


def design_kickoff(workspace_rel: str) -> str:
    return (
        f"Design, verify, and generate prompts for the coordination protocol of the "
        f"task described in `{workspace_rel}/description.md`. The workspace is "
        f"`{workspace_rel}/` (already initialized: spec/ir.json is a stub to replace). "
        "Before scaffolding PlusCal/TLC, replace the stub with a complete IR. "
        "The IR must contain only schema-allowed fields. Do not write `locks` "
        "or `counters`; encode them as `resources` entries with type `Lock` "
        "or `Counter`. "
        "If the IR keeps two or more agents, `channels` must be non-empty: add "
        "the minimal directed FIFO channels required by task handoffs, shared "
        "resource coordination, review/approval flow, data dependencies, or "
        "failure/retry paths. Each channel must include id, from, to, and a "
        "non-empty labels list, and endpoints must match agent ids. Do not add "
        "arbitrary complete-graph channels. If the task is truly independent "
        "and needs no communication, collapse it to a single-agent design or "
        "document why in plan.md before continuing. "
        f"Follow your instructions end to end — finish only when "
        f"`{workspace_rel}/prompts/runtime_b/` holds one prompt per agent."
    )


def ir_repair_kickoff(workspace_rel: str, errors: list[str]) -> str:
    error_text = "\n".join(f"- {error}" for error in errors) or "- unknown IR validation failure"
    return (
        f"Repair ONLY `{workspace_rel}/spec/ir.json`.\n\n"
        f"First read `{workspace_rel}/spec/procedure_execution_context.json` and "
        f"`{workspace_rel}/spec/extracted_coordination_attributes.json`; they are "
        "the authoritative source for the selected procedure and canonical Template attributes. "
        "Do not rediscover or change those values from description.md.\n\n"
        f"Current IR validation errors:\n{error_text}\n\n"
        "Rules:\n"
        "- Do not remove agents.\n"
        "- Do not remove resources.\n"
        "- The IR must contain only schema-allowed fields. Do not add `locks`, "
        "`counters`, `permissions`, `edges`, `messages`, or other ad hoc fields.\n"
        "- Lock-like behavior belongs in `resources` as {\"id\": \"...\", \"type\": \"Lock\"}; "
        "counter-like behavior belongs in `resources` as type `Counter` with config.initial.\n"
        "- Normalize agents to objects like {\"id\": \"DEVELOPER_A\"} if needed.\n"
        "- If two or more agents remain, channels must be non-empty.\n"
        "- Repair channel schema/topology only within the authoritative number_of_channels and communication_flow; "
        "derive a minimal topology only when the canonical count is explicitly unknown.\n"
        "- Do not add arbitrary complete-graph channels just to satisfy validation.\n"
        "- If agents are truly independent, collapse to a single-agent IR and explain why.\n"
        "- Each channel must include id, from, to, and labels.\n"
        "- labels must be a non-empty list of task-relevant message labels.\n"
        "- Every channel endpoint must reference an existing agent id.\n"
        "- Preserve JSON validity.\n"
        "- The IR must be scaffoldable by tla-verify-pluscal.\n"
        "- Write spec/ir_repair_notes.md listing each channel added and why.\n"
        "- Do not edit Protocol.tla, Protocol.cfg, states.json, prompts, or runtime files.\n\n"
        "After writing spec/ir.json, call validate_ir(). Stop after the IR validates."
    )


def pluscal_completion_kickoff(workspace_rel: str) -> str:
    return (
        f"Continue the TraceFix design workflow for `{workspace_rel}/`.\n\n"
        "TraceFix has already validated `spec/ir.json` and deterministically "
        "generated `spec/Protocol.tla` plus `spec/Protocol.cfg` from that IR. "
        "Do not redesign the IR unless verify reports a concrete IR problem.\n\n"
        "Required next steps:\n"
        "- Read `spec/Protocol.tla` and the PlusCal rules before editing.\n"
        "- Replace the scaffolded process-body placeholders with faithful "
        "PlusCal coordination logic for every agent.\n"
        "- Use brace-form choices only: `either { ... } or { ... };`. Never "
        "write `endeither`.\n"
        "- Run `tla-verify-pluscal verify` on the spec directory.\n"
        "- If TLC fails, repair `Protocol.tla` and verify again, up to the "
        "workflow's repair limit.\n"
        "- After TLC passes, extract states, export/read "
        "`spec/cityos_module_plan.json`, and only then follow the "
        "prompt-generation phase for this same workspace.\n"
        "- Runtime prompts must derive from the verified protocol, "
        "`states.json`, and `spec/cityos_module_plan.json`; do not generate "
        "prompts from raw IR alone.\n"
        "- Finish only when `prompts/runtime_b/` contains one prompt per agent."
    )


def prompt_generation_kickoff(workspace_rel: str) -> str:
    return (
        f"Generate Runtime B prompts for `{workspace_rel}/` only.\n\n"
        "TraceFix has already completed protocol verification. Required inputs "
        "are present and must be read before writing prompts:\n"
        "- `spec/ir.json`\n"
        "- `spec/Protocol.tla`\n"
        "- `spec/Protocol_translated.tla`\n"
        "- `spec/states.json`\n"
        "- `spec/summary.json`\n"
        "- `spec/cityos_module_plan.json`\n\n"
        f"Read `{_PROMPT_GEN_SKILL}` and follow it directly. Do not edit "
        "`spec/ir.json`, `spec/Protocol.tla`, `spec/Protocol.cfg`, "
        "`spec/Protocol_translated.tla`, `spec/states.json`, "
        "`spec/summary.json`, or `spec/cityos_module_plan.json`. "
        "Write one prompt per agent to `prompts/runtime_b/`. Prompts must "
        "derive from the verified protocol, `states.json`, and "
        "`spec/cityos_module_plan.json`, never from raw IR alone."
    )


@dataclass
class DesignResult:
    """Artifact-judged outcome of one design run."""
    success: bool
    workspace: str
    status: str                       # ready | ir_incomplete | pluscal_error | tlc_error | timeout | error
    tlc_passed: bool | None = None
    agents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    repairs: int | None = None
    duration: float = 0.0
    events: int = 0
    stderr_tail: list[str] = field(default_factory=list)
    ir_errors: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def inspect_workspace(ws: Path) -> dict:
    """Judge a design run purely from its artifacts (transcript-independent)."""
    spec = ws / "spec" if (ws / "spec").is_dir() else ws
    out: dict = {"ir": None, "states": (spec / "states.json").exists(),
                 "tlc_passed": None, "repairs": None, "prompts": [],
                 "protocol": (spec / "Protocol.tla").exists(),
                 "tlc_error": (spec / "tlc_error.md").exists()}
    ir_path = spec / "ir.json"
    if ir_path.exists():
        try:
            out["ir"] = safe_read_json(ir_path)
        except (json.JSONDecodeError, OSError):
            pass
    summary = spec / "summary.json"
    if summary.exists():
        try:
            s = safe_read_json(summary, {})
            out["tlc_passed"] = s.get("tlc_passed")
            out["repairs"] = s.get("total_repairs")
        except (json.JSONDecodeError, OSError):
            pass
    pdir = ws / "prompts" / "runtime_b"
    if pdir.is_dir():
        out["prompts"] = sorted(p.stem for p in pdir.glob("*.md"))
    return out


def _agent_ids(ir: dict | None) -> list[str]:
    if not isinstance(ir, dict):
        return []
    try:
        from tracefix.pipeline.pipeline.validator import normalize_ir

        ir = normalize_ir(ir)
    except Exception:
        pass
    agents = ir.get("agents", []) if isinstance(ir, dict) else []
    return [
        agent["id"]
        for agent in agents
        if isinstance(agent, dict) and isinstance(agent.get("id"), str)
    ]


def _spec_dir(ws: Path) -> Path:
    spec = ws / "spec"
    return spec if spec.is_dir() else ws


def _load_ir(ws: Path) -> dict | None:
    ir_path = _spec_dir(ws) / "ir.json"
    try:
        return safe_read_json(ir_path)
    except (json.JSONDecodeError, OSError):
        return None


def _channel_key(channel: dict) -> str:
    return str(channel.get("id") or f"{channel.get('from')}->{channel.get('to')}")


def _channel_diagnostics(before: dict | None, after: dict | None) -> list[str]:
    if not isinstance(after, dict):
        return ["IR repair diagnostics: spec/ir.json is unreadable after repair"]
    before_channels = before.get("channels", []) if isinstance(before, dict) else []
    after_channels = after.get("channels", [])
    if not isinstance(before_channels, list):
        before_channels = []
    if not isinstance(after_channels, list):
        return ["IR repair diagnostics: channels is not a list after repair"]

    before_keys = {
        _channel_key(channel)
        for channel in before_channels
        if isinstance(channel, dict)
    }
    added = [
        channel
        for channel in after_channels
        if isinstance(channel, dict) and _channel_key(channel) not in before_keys
    ]
    if not added:
        return ["IR repair diagnostics: no channels were added"]

    diagnostics = []
    for channel in added:
        labels = channel.get("labels", [])
        if isinstance(labels, list):
            label_text = ", ".join(str(label) for label in labels)
        else:
            label_text = str(labels)
        diagnostics.append(
            "IR repair added channel "
            f"{_channel_key(channel)}: {channel.get('from')} -> {channel.get('to')} "
            f"labels=[{label_text}]"
        )
    return diagnostics


def _ir_counts(ir: dict | None) -> dict[str, int]:
    if not isinstance(ir, dict):
        return {"agents": 0, "resources": 0, "channels": 0}
    return {
        "agents": len(ir.get("agents", [])) if isinstance(ir.get("agents"), list) else 0,
        "resources": len(ir.get("resources", [])) if isinstance(ir.get("resources"), list) else 0,
        "channels": len(ir.get("channels", [])) if isinstance(ir.get("channels"), list) else 0,
    }


def _looks_like_init_stub(ir: dict | None) -> bool:
    if not isinstance(ir, dict):
        return False
    counts = _ir_counts(ir)
    agents = _agent_ids(ir)
    return (
        counts["channels"] == 0
        and counts["resources"] == 0
        and agents in (["AGENT_A", "AGENT_B"], ["agent_a", "agent_b"])
    )


def _workspace_stage_diagnostics(ws: Path, label: str) -> list[str]:
    spec = _spec_dir(ws)
    desc_path = ws / "description.md"
    ir_path = spec / "ir.json"
    diagnostics = [f"{label}: workspace={ws}"]

    if desc_path.is_file():
        description = safe_read_text(desc_path, default="")
        preview = " ".join(description.split())[:500]
        diagnostics.append(
            f"{label}: description.md size={desc_path.stat().st_size} preview={preview!r}"
        )
    else:
        diagnostics.append(f"{label}: description.md missing at {desc_path}")

    if ir_path.is_file():
        ir = _load_ir(ws)
        counts = _ir_counts(ir)
        diagnostics.append(
            f"{label}: ir.json path={ir_path} size={ir_path.stat().st_size} "
            f"agents={counts['agents']} resources={counts['resources']} "
            f"channels={counts['channels']}"
        )
    else:
        diagnostics.append(f"{label}: ir.json missing at {ir_path}")

    return diagnostics


def _opencode_provider_diagnostics(ws: Path, disposition: dict) -> list[str]:
    haystack = "\n".join(str(line) for line in disposition.get("stderr_tail", []))
    log_path = ws / ".design" / "data" / "opencode" / "log" / "opencode.log"
    if log_path.is_file():
        try:
            haystack += "\n" + safe_read_text(log_path, default="")[-12000:]
        except OSError:
            pass
    lowered = haystack.lower()
    auth_markers = [
        "missing authentication header",
        "unauthorized",
        "invalid api key",
        "authentication",
        "api key",
    ]
    model_markers = [
        "providermodelnotfounderror",
        "model not found",
        "unsupported model",
        "model is not supported",
        "no endpoints found that support tool use",
        "does not support tool use",
    ]
    if any(marker in lowered for marker in auth_markers):
        return [
            "OpenCode/provider authentication error detected; design model "
            "did not complete a usable IR update.",
            "Check the selected provider API key in the UI or environment "
            "before treating this as an IR topology failure.",
        ]
    if any(marker in lowered for marker in model_markers):
        return [
            "OpenCode/provider model error detected; design model did not "
            "complete a usable IR update.",
            "Check that the selected model is installed/available in the "
            "OpenCode provider catalog before treating this as an IR topology "
            "failure.",
        ]
    return []


def _normalize_legacy_endeither_syntax(tla_content: str) -> tuple[str, list[str]]:
    """Convert simple Pascal-style `either/or/endeither` to PlusCal brace form."""
    lines = tla_content.splitlines()
    out: list[str] = []
    diagnostics: list[str] = []
    legacy_depth = 0

    for line_num, line in enumerate(lines, start=1):
        indent = line[: len(line) - len(line.lstrip())]
        stripped = line.strip()

        if stripped == "either":
            out.append(f"{indent}either {{")
            legacy_depth += 1
            diagnostics.append(f"normalized line {line_num}: either -> either {{")
            continue
        if legacy_depth > 0 and stripped == "or":
            out.append(f"{indent}}} or {{")
            diagnostics.append(f"normalized line {line_num}: or -> }} or {{")
            continue
        if legacy_depth > 0 and stripped == "endeither":
            out.append(f"{indent}}};")
            legacy_depth -= 1
            diagnostics.append(f"normalized line {line_num}: endeither -> }};")
            continue

        if legacy_depth > 0 and stripped.startswith("{"):
            body = stripped[1:].lstrip()
            if body.endswith("}"):
                body = body[:-1].rstrip()
            if body:
                out.append(f"{indent}{body}")
            diagnostics.append(f"normalized line {line_num}: removed legacy branch opening brace")
            continue

        if legacy_depth > 0 and stripped.endswith("}"):
            body = stripped[:-1].rstrip()
            if body:
                out.append(f"{indent}{body}")
            diagnostics.append(f"normalized line {line_num}: removed legacy branch closing brace")
            continue

        out.append(line)

    if legacy_depth != 0:
        return tla_content, [
            "legacy either normalization skipped: unbalanced either/endeither blocks"
        ]

    normalized = "\n".join(out)
    if tla_content.endswith("\n"):
        normalized += "\n"
    if normalized == tla_content:
        return tla_content, []
    return normalized, diagnostics


def _verified_protocol_ready(ws: Path) -> bool:
    spec = _spec_dir(ws)
    try:
        summary = safe_read_json(spec / "summary.json", {})
    except (json.JSONDecodeError, OSError):
        summary = {}
    return (
        summary.get("tlc_passed") is True
        and (spec / "states.json").is_file()
        and (spec / "Protocol.tla").is_file()
        and (spec / "Protocol_translated.tla").is_file()
    )


def _ensure_cityos_plan(ws: Path) -> list[str]:
    if not _verified_protocol_ready(ws):
        return [
            "CityOS plan export skipped: verified protocol artifacts are not complete"
        ]

    plan_path = _spec_dir(ws) / "cityos_module_plan.json"
    if plan_path.is_file():
        return [f"CityOS module plan already present: {plan_path}"]

    from tracefix.runtime.cityos_plan import export_cityos_module_plan

    result = export_cityos_module_plan(ws)
    return [f"CityOS module plan exported: {result.plan_path}"]


def _runtime_prompt_files(ws: Path) -> list[Path]:
    pdir = ws / "prompts" / "runtime_b"
    if not pdir.is_dir():
        return []
    return sorted(p for p in pdir.glob("*.md") if p.is_file())


def _remove_runtime_prompts(ws: Path) -> list[str]:
    prompts = _runtime_prompt_files(ws)
    for prompt in prompts:
        prompt.unlink()
    if not prompts:
        return []
    return [
        f"Prompt gate: removed {len(prompts)} stale runtime prompt(s) "
        "generated before the verified CityOS module plan"
    ]


def _runtime_prompts_current(ws: Path) -> bool:
    plan_path = _spec_dir(ws) / "cityos_module_plan.json"
    if not plan_path.is_file():
        return False
    prompts = _runtime_prompt_files(ws)
    if not prompts:
        return False
    plan_mtime = plan_path.stat().st_mtime
    return all(prompt.stat().st_mtime >= plan_mtime for prompt in prompts)


def _ensure_plan_before_prompts(ws: Path) -> list[str]:
    diagnostics: list[str] = []
    plan_path = _spec_dir(ws) / "cityos_module_plan.json"
    if not _verified_protocol_ready(ws):
        return ["Prompt gate skipped: protocol verification is not complete"]

    if not plan_path.is_file():
        diagnostics.extend(_remove_runtime_prompts(ws))
        diagnostics.extend(_ensure_cityos_plan(ws))
        return diagnostics

    stale_prompts = [
        prompt for prompt in _runtime_prompt_files(ws)
        if prompt.stat().st_mtime < plan_path.stat().st_mtime
    ]
    if stale_prompts:
        diagnostics.extend(_remove_runtime_prompts(ws))
    else:
        diagnostics.append(
            "Prompt gate: runtime prompts are current with the verified "
            "CityOS module plan"
        )
    return diagnostics


def validate_design_ir(
    ws: Path,
    *,
    sanitization_report: dict | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Normalize and validate spec/ir.json for design postflight."""
    from tracefix.pipeline.pipeline.validator import (
        canonicalize_ir_with_diagnostics,
        normalize_ir_with_diagnostics,
        validate_canonical_ir,
    )

    spec = _spec_dir(ws)
    ir_path = spec / "ir.json"
    diagnostics = ["IR validation started"]
    if not ir_path.exists():
        return False, ["missing spec/ir.json"], diagnostics

    original = safe_read_json(ir_path)
    if not isinstance(original, dict):
        return False, ["spec/ir.json must decode to a JSON object."], diagnostics

    normalized, normalize_diagnostics = normalize_ir_with_diagnostics(original)
    if normalized != original:
        ir_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
        diagnostics.append("IR normalized: agent/resource schemas canonicalized")
        diagnostics.extend(normalize_diagnostics)

    result_before = validate_canonical_ir(normalized)
    report = {
        "attempted": False,
        "changed": False,
        "removed_fields": [],
        "normalized_fields": [],
        "validation_before": {
            "valid": result_before.valid,
            "errors": list(result_before.errors),
        },
        "validation_after": {
            "valid": result_before.valid,
            "errors": list(result_before.errors),
        },
        "recovered": False,
        "prevented_likely_unnecessary_failure": False,
    }
    if result_before.valid:
        if sanitization_report is not None:
            sanitization_report.update(report)
        diagnostics.append("IR validation passed")
        return True, [], diagnostics

    diagnostics.append("IR validation failed before sanitization")
    candidate, canonicalization = canonicalize_ir_with_diagnostics(original)
    report.update({
        "attempted": canonicalization["attempted"],
        "changed": canonicalization["changed"],
        "removed_fields": canonicalization["removed_fields"],
        "normalized_fields": canonicalization["normalized_fields"],
    })
    diagnostics.append("IR sanitization attempted")
    diagnostics.extend(
        f"IR sanitization removed {path}"
        for path in report["removed_fields"]
    )
    diagnostics.extend(
        f"IR sanitization normalized {change}"
        for change in report["normalized_fields"]
    )
    if not report["changed"]:
        diagnostics.append("IR sanitization made no safe changes")

    result_after = validate_canonical_ir(candidate)
    report["validation_after"] = {
        "valid": result_after.valid,
        "errors": list(result_after.errors),
    }
    report["recovered"] = bool(report["changed"] and result_after.valid)
    report["prevented_likely_unnecessary_failure"] = report["recovered"]
    if sanitization_report is not None:
        sanitization_report.update(report)

    if result_after.valid:
        ir_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
        diagnostics.append("IR validation passed after sanitization")
        diagnostics.append("IR sanitizer recovered a valid IR; pipeline continued")
        return True, [], diagnostics

    diagnostics.append("IR validation failed after sanitization")
    diagnostics.append("IR validation failed")
    return False, list(result_after.errors), diagnostics


def _scaffold_coord_ir(ws: Path) -> list[str]:
    """Write Protocol.cfg from IR for a coord-template run.

    The template already wrote Protocol.tla — this only generates the TLC
    config so the existing Protocol.tla is not overwritten.
    """
    from tracefix.pipeline.pipeline.pluscal_generator import generate_tlc_config
    from tracefix.pipeline.pipeline.validator import normalize_ir, validate_ir

    spec = _spec_dir(ws)
    ir_path = spec / "ir.json"
    ir_data = normalize_ir(safe_read_json(ir_path, {}))
    result = validate_ir(ir_data)
    if not result.valid:
        errors = "; ".join(result.errors)
        raise ValueError(f"cannot generate TLC config for invalid IR: {errors}")
    (spec / "Protocol.cfg").write_text(generate_tlc_config(ir_data), encoding="utf-8")
    return ["Coord template: generated Protocol.cfg from IR (Protocol.tla preserved)"]


def _scaffold_valid_ir(ws: Path) -> list[str]:
    """Generate Protocol.tla/Protocol.cfg after postflight proves IR is valid."""
    from tracefix.pipeline.pipeline.pluscal_generator import (
        generate_pluscal_scaffold,
        generate_tlc_config,
    )
    from tracefix.pipeline.pipeline.validator import normalize_ir, validate_ir

    spec = _spec_dir(ws)
    ir_path = spec / "ir.json"
    ir_data = normalize_ir(safe_read_json(ir_path, {}))
    result = validate_ir(ir_data)
    if not result.valid:
        errors = "; ".join(result.errors)
        raise ValueError(f"cannot scaffold invalid IR: {errors}")

    (spec / "ir.json").write_text(json.dumps(ir_data, indent=2) + "\n", encoding="utf-8")
    (spec / "Protocol.tla").write_text(generate_pluscal_scaffold(ir_data), encoding="utf-8")
    (spec / "Protocol.cfg").write_text(generate_tlc_config(ir_data), encoding="utf-8")
    return [
        "Valid IR had no Protocol.tla; deterministic scaffold fallback ran",
        "Scaffold fallback wrote Protocol.tla and Protocol.cfg",
    ]


def _format_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in cmd])


def _write_tlc_stage_error(ws: Path, title: str, body: str) -> None:
    spec = _spec_dir(ws)
    spec.mkdir(parents=True, exist_ok=True)
    (spec / "tlc_error.md").write_text(
        f"# {title}\n\n{body.rstrip()}\n",
        encoding="utf-8",
    )


def _verification_needed_after_scaffold(ws: Path) -> tuple[bool, list[str]]:
    valid_ir, ir_errors, diagnostics = validate_design_ir(ws)
    spec = _spec_dir(ws)
    if not valid_ir:
        return False, [*diagnostics, *ir_errors]
    if not (spec / "Protocol.tla").is_file():
        return False, [*diagnostics, "Post-scaffold verification skipped: Protocol.tla missing"]
    if not (spec / "Protocol.cfg").is_file():
        return False, [*diagnostics, "Post-scaffold verification skipped: Protocol.cfg missing"]

    return True, [*diagnostics, "Deterministic post-scaffold TLC verification required"]


def _run_tlc_and_extract(
    ws: Path,
    timing: PipelineTimingReport | None = None,
) -> list[str]:
    """Python-level TLC verification + state extraction fallback.

    Called when Protocol.tla exists but TLC was never invoked (the opencode
    continuation pass didn't call `tla-verify-pluscal verify`). Mirrors
    cmd_verify + cmd_extract_states, writing the same artifact set:

    - Success: Protocol_translated.tla, tlc_output.log, states.json, summary.json
    - Failure: tlc_error.md, tlc_output.log (and summary.json with tlc_passed=false)
    """
    spec = _spec_dir(ws)
    # Discard status-shaped files that may have been produced by OpenCode.
    # This function recreates them only from the deterministic compiler/TLC
    # execution below, so stale or LLM-authored booleans cannot survive an
    # exception and be consumed as a verification verdict.
    for name in ("summary.json", "states.json", "Protocol_translated.tla", "tlc_output.log", "tlc_error.md"):
        (spec / name).unlink(missing_ok=True)
    from tracefix.pipeline.pipeline.pluscal_compiler import translate_pluscal
    from tracefix.pipeline.pipeline.tlc_runner import run_tlc
    from tracefix.pipeline.pipeline.trace_parser import parse_trace
    from tracefix.pipeline.pipeline.error_formatter import format_tlc_error
    from tracefix.pipeline.pipeline.pluscal_parser import parse_pluscal
    from tracefix.pipeline.pipeline.toolchain import resolve_java, resolve_jar

    tla_path = spec / "Protocol.tla"
    cfg_path = spec / "Protocol.cfg"
    ir_path = spec / "ir.json"
    diagnostics: list[str] = []

    diagnostics.append(
        "TLC fallback: Protocol.tla present but TLC never ran; invoking verification"
    )

    def _artifact_inventory() -> str:
        checks = [
            ("Protocol.tla", tla_path),
            ("Protocol.cfg", cfg_path),
            ("Protocol_translated.tla", spec / "Protocol_translated.tla"),
            ("tlc_output.log", spec / "tlc_output.log"),
            ("states.json", spec / "states.json"),
        ]
        return "; ".join(f"{n}={'yes' if p.exists() else 'no'}" for n, p in checks)

    if not tla_path.exists():
        diagnostics.append("TLC fallback aborted: Protocol.tla missing")
        (spec / "tlc_error.md").write_text(
            f"# PlusCal/TLC Stage Incomplete\n\nProtocol.tla missing.\n\n"
            f"**Artifacts**: {_artifact_inventory()}\n",
            encoding="utf-8",
        )
        return diagnostics

    if not cfg_path.exists():
        diagnostics.append("TLC fallback aborted: Protocol.cfg missing")
        (spec / "tlc_error.md").write_text(
            f"# PlusCal/TLC Stage Incomplete\n\nProtocol.cfg missing.\n\n"
            f"**Artifacts**: {_artifact_inventory()}\n",
            encoding="utf-8",
        )
        return diagnostics

    tla_content = safe_read_text(tla_path)
    cfg_content = safe_read_text(cfg_path)
    java = resolve_java()
    jar = resolve_jar()
    pcal_command = [java, "-cp", jar, "pcal.trans", "Protocol.tla"]
    tlc_command = [
        java,
        "-Xmx4g",
        "-cp",
        jar,
        "tlc2.TLC",
        "-config",
        "Protocol.cfg",
        "-workers",
        "auto",
        "Protocol.tla",
    ]

    # Step 1: PlusCal to TLA+ translation
    diagnostics.append("[TRACEFIX PLUSCAL START]")
    diagnostics.append(f"[TRACEFIX PLUSCAL COMMAND] {_format_command(pcal_command)}")
    print("[TRACEFIX PLUSCAL START]", flush=True)
    pcal_started_at = datetime.now(timezone.utc).isoformat()
    pcal_start = time.monotonic() * 1000.0
    pcal_result = translate_pluscal(tla_content, cfg_content, java_path=java, tla2tools_jar=jar)
    pcal_duration = (time.monotonic() * 1000.0 - pcal_start) / 1000.0

    if not pcal_result.success:
        repaired_content, repair_diagnostics = _normalize_legacy_endeither_syntax(tla_content)
        if repair_diagnostics and repaired_content != tla_content:
            diagnostics.append(
                "TLC fallback: PlusCal translation failed; applying one syntax-only "
                "repair for legacy either/or/endeither form"
            )
            diagnostics.extend(f"TLC fallback: {item}" for item in repair_diagnostics)
            tla_path.write_text(repaired_content, encoding="utf-8")
            tla_content = repaired_content
            pcal_result = translate_pluscal(
                tla_content,
                cfg_content,
                java_path=java,
                tla2tools_jar=jar,
            )
            if pcal_result.success:
                diagnostics.append(
                    "TLC fallback: PlusCal translation succeeded after syntax repair"
                )
    pcal_duration = (time.monotonic() * 1000.0 - pcal_start) / 1000.0

    if not pcal_result.success:
        print(
            f"[TRACEFIX PLUSCAL END] result=fail duration={pcal_duration:.2f}s",
            flush=True,
        )
        if timing is not None:
            timing.stage(
                "pluscal_translation",
                started_at=pcal_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=pcal_duration * 1000.0,
                success=False,
                error=pcal_result.error_message,
            )
        diagnostics.append(
            f"[TRACEFIX PLUSCAL END] result=fail duration={pcal_duration:.2f}s"
        )
        diagnostics.append(
            f"TLC fallback: PlusCal translation failed: {pcal_result.error_message[:120]}"
        )
        (spec / "tlc_error.md").write_text(
            f"# PlusCal Translation Error\n\n"
            f"## Command\n\n```\n{_format_command(pcal_command)}\n```\n\n"
            f"## Output\n\n```\n{pcal_result.error_message}\n```\n\n"
            f"**Artifacts**: {_artifact_inventory()}\n"
            f"**Expected**: {spec / 'states.json'}\n",
            encoding="utf-8",
        )
        return diagnostics

    if timing is not None:
        timing.stage(
            "pluscal_translation",
            started_at=pcal_started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=pcal_duration * 1000.0,
            success=True,
        )
    diagnostics.append(
        f"[TRACEFIX PLUSCAL END] result=pass duration={pcal_duration:.2f}s"
    )
    print(
        f"[TRACEFIX PLUSCAL END] result=pass duration={pcal_duration:.2f}s",
        flush=True,
    )
    (spec / "Protocol_translated.tla").write_text(pcal_result.translated_tla, encoding="utf-8")
    diagnostics.append(
        "TLC fallback: PlusCal translation succeeded; wrote Protocol_translated.tla"
    )

    # Step 2: Run TLC on translated spec
    diagnostics.append("[TRACEFIX TLC START]")
    diagnostics.append(f"[TRACEFIX TLC COMMAND] {_format_command(tlc_command)}")
    print("[TRACEFIX TLC START]", flush=True)
    tlc_started_at = datetime.now(timezone.utc).isoformat()
    tlc_start = time.monotonic() * 1000.0
    tlc_result = run_tlc(pcal_result.translated_tla, cfg_content, java_path=java, tla2tools_jar=jar)
    tlc_duration = (time.monotonic() * 1000.0 - tlc_start) / 1000.0
    (spec / "tlc_output.log").write_text(tlc_result.raw_output, encoding="utf-8")

    if not tlc_result.success:
        print(
            f"[TRACEFIX TLC END] result=fail duration={tlc_duration:.2f}s",
            flush=True,
        )
        if timing is not None:
            timing.stage(
                "tlc_verification",
                started_at=tlc_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=tlc_duration * 1000.0,
                success=False,
                error=tlc_result.violation_type,
            )
        diagnostics.append(
            f"[TRACEFIX TLC END] result=fail duration={tlc_duration:.2f}s"
        )
        trace = parse_trace(tlc_result.raw_output)
        error_md = format_tlc_error(tlc_result, trace)
        (spec / "tlc_error.md").write_text(
            f"## Command\n\n```\n{_format_command(tlc_command)}\n```\n\n"
            f"{error_md}",
            encoding="utf-8",
        )
        (spec / "summary.json").write_text(
            json.dumps({"tlc_passed": False, "total_repairs": 0}, indent=2) + "\n",
            encoding="utf-8",
        )
        diagnostics.append(
            f"TLC fallback: TLC failed ({tlc_result.violation_type}); "
            "wrote tlc_error.md and summary.json(tlc_passed=false)"
        )
        return diagnostics

    if timing is not None:
        timing.stage(
            "tlc_verification",
            started_at=tlc_started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=tlc_duration * 1000.0,
            success=True,
        )
    diagnostics.append(
        f"[TRACEFIX TLC END] result=pass duration={tlc_duration:.2f}s"
    )
    print(
        f"[TRACEFIX TLC END] result=pass duration={tlc_duration:.2f}s",
        flush=True,
    )
    diagnostics.append("TLC fallback: TLC passed; wrote tlc_output.log")

    # Step 3: Extract per-agent states
    diagnostics.append("TLC fallback: extracting states from translated spec")
    print("[TRACEFIX STATE EXTRACTION START]", flush=True)
    extract_started_at = datetime.now(timezone.utc).isoformat()
    extract_started_ms = time.monotonic() * 1000.0
    try:
        ir_data: dict = {}
        if ir_path.exists():
            ir_data = safe_read_json(ir_path, {})
        parse_result = parse_pluscal(pcal_result.translated_tla, ir_data)
    except Exception as exc:  # noqa: BLE001
        print(f"[TRACEFIX STATE EXTRACTION END] result=fail error={type(exc).__name__}", flush=True)
        if timing is not None:
            timing.stage(
                "state_extraction",
                started_at=extract_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - extract_started_ms,
                success=False,
                error=str(exc),
            )
        diagnostics.append(f"TLC fallback: state extraction failed: {exc}")
        (spec / "tlc_error.md").write_text(
            f"# State Extraction Failed\n\nTLC passed but state extraction raised:\n\n"
            f"```\n{exc}\n```\n\n"
            f"**Artifacts**: {_artifact_inventory()}\n",
            encoding="utf-8",
        )
        return diagnostics

    if parse_result.errors:
        diagnostics.append(
            f"TLC fallback: {len(parse_result.errors)} parse warning(s) during "
            "state extraction (continuing)"
        )

    out_data: dict = {
        "states": parse_result.states,
        "initial_states": parse_result.initial_states,
    }
    if parse_result.local_variables:
        out_data["local_variables"] = parse_result.local_variables
    (spec / "states.json").write_text(json.dumps(out_data, indent=2) + "\n", encoding="utf-8")
    (spec / "summary.json").write_text(
        json.dumps({"tlc_passed": True, "total_repairs": 0}, indent=2) + "\n",
        encoding="utf-8",
    )
    diagnostics.append(
        f"TLC fallback: extracted {len(parse_result.states)} states; "
        "wrote states.json and summary.json(tlc_passed=true)"
    )
    print(
        f"[TRACEFIX STATE EXTRACTION END] result=pass states={len(parse_result.states)}",
        flush=True,
    )
    if timing is not None:
        timing.stage(
            "state_extraction",
            started_at=extract_started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=time.monotonic() * 1000.0 - extract_started_ms,
            success=True,
        )
    diagnostics.extend(_ensure_cityos_plan(ws))
    return diagnostics


def classify_design_artifacts(
    ws: Path,
    *,
    timed_out: bool = False,
    sanitization_report: dict | None = None,
) -> tuple[str, list[str], list[str]]:
    """Classify the design stage from artifacts without trusting transcript text."""
    if timed_out:
        return "timeout", [], ["Design timed out"]

    valid_ir, ir_errors, diagnostics = validate_design_ir(
        ws,
        sanitization_report=sanitization_report,
    )
    spec = _spec_dir(ws)
    protocol_path = spec / "Protocol.tla"
    states_path = spec / "states.json"
    summary_path = spec / "summary.json"
    tlc_error_path = spec / "tlc_error.md"

    if not valid_ir:
        diagnostics.append("PlusCal scaffold skipped because IR is incomplete")
        diagnostics.append("TLC did not run")
        return "ir_incomplete", ir_errors, diagnostics

    if not protocol_path.exists():
        diagnostics.append("Protocol.tla missing after valid IR")
        return (
            "ir_incomplete",
            ["Design stopped before PlusCal scaffolding. TLC did not run."],
            diagnostics,
        )

    diagnostics.append("PlusCal scaffold artifact present")
    if summary_path.exists():
        try:
            summary = safe_read_json(summary_path, {})
        except (json.JSONDecodeError, OSError):
            summary = {}
        if summary.get("tlc_passed") is True and states_path.exists():
            diagnostics.append("TLC passed and states extracted")
            return "ready", [], diagnostics
        if summary.get("tlc_passed") is False:
            diagnostics.append("TLC ran and failed")
            return "tlc_error", [], diagnostics

    if tlc_error_path.exists():
        diagnostics.append("TLC error artifact present")
        return "tlc_error", [], diagnostics

    if not states_path.exists():
        inv = "; ".join(
            f"{n}={'yes' if (spec / f).exists() else 'no'}"
            for n, f in [
                ("Protocol.cfg", "Protocol.cfg"),
                ("Protocol_translated.tla", "Protocol_translated.tla"),
                ("tlc_output.log", "tlc_output.log"),
            ]
        )
        diagnostics.append(
            f"Protocol.tla exists but states.json is missing ({inv})"
        )
        return (
            "pluscal_error",
            ["Protocol.tla exists, but TLC/states extraction did not complete."],
            diagnostics,
        )

    return "incomplete", [], diagnostics


def judge(ws: Path, *, timed_out: bool = False) -> DesignResult:
    info = inspect_workspace(ws)
    agents = _agent_ids(info["ir"])
    have_all_prompts = bool(agents) and set(agents) <= set(info["prompts"])
    ready = bool(info["tlc_passed"]) and info["states"] and have_all_prompts
    if ready:
        status = "ready"
    elif timed_out:
        status = "timeout"
    elif info["tlc_passed"] is False:
        status = "tlc_error"
    elif not info["protocol"]:
        status = "ir_incomplete"
    elif info["tlc_error"]:
        status = "tlc_error"
    else:
        status = "incomplete"
    return DesignResult(
        success=ready, workspace=str(ws), status=status,
        tlc_passed=info["tlc_passed"], agents=agents,
        prompts=info["prompts"], repairs=info["repairs"],
    )


def _init_workspace(name: str, task: str) -> Path:
    """Create a fresh workspace/<name>_<timestamp>/ via the CLI's init.

    resolve_init_dir does the timestamping (every design run gets a new
    directory, never iterating on an older same-named workspace); passing the
    resolved path to cmd_init keeps the two in agreement on where files land."""
    from tracefix.cli.cli import cmd_init, resolve_init_dir
    out = resolve_init_dir(name)
    cmd_init(argparse.Namespace(dir=str(out), task=task, agents=None, with_tools=False))
    return out.resolve()


class DesignWatcher:
    """Polls the workspace's ARTIFACTS and narrates design progress to the event bus.

    Observability only (the runtime three-plane rule applies here too): the watcher
    renders what the artifacts prove — ir.json written, TLC verdict, states.json,
    prompts appearing — and never influences the design run.
    """

    def __init__(self, ws: Path, bus, poll: float = 1.0):
        self.ws = ws
        self.bus = bus
        self.poll = poll
        self._ir_snapshot: str | None = None
        self._summary_snapshot: str | None = None
        self._prompts_seen: set[str] = set()
        self._states_done = False

    async def _emit(self, event: str, data: dict) -> None:
        await self.bus.emit(event, data)

    async def _tick(self) -> None:
        spec = self.ws / "spec" if (self.ws / "spec").is_dir() else self.ws

        ir_path = spec / "ir.json"
        if ir_path.exists():
            try:
                raw = safe_read_text(ir_path, default="")
            except OSError:
                raw = None
            if raw and raw != self._ir_snapshot:
                try:
                    ir = json.loads(raw)
                except json.JSONDecodeError:
                    ir = None
                # the init stub has no resources/channels — only narrate a real IR
                if ir and ir.get("agents") and (ir.get("channels") or ir.get("resources")):
                    self._ir_snapshot = raw
                    await self._emit("design.phase",
                                     {"key": "ir", "state": "done", "label": "IR designed"})
                    await self._emit("design.ir", {"ir": ir})
                    await self._emit("design.phase",
                                     {"key": "pluscal", "state": "active",
                                      "label": "writing PlusCal bodies"})

        summary = spec / "summary.json"
        if summary.exists():
            try:
                raw = safe_read_text(summary, default="")
            except OSError:
                raw = None
            if raw and raw != self._summary_snapshot:
                self._summary_snapshot = raw
                try:
                    s = json.loads(raw)
                except json.JSONDecodeError:
                    s = {}
                await self._emit("design.phase",
                                 {"key": "verify", "state": "active", "label": "TLC verify"})
                if "tlc_passed" in s and (s["tlc_passed"] or s.get("total_repairs")):
                    await self._emit("design.verdict",
                                     {"tlc_passed": s.get("tlc_passed"),
                                      "repairs": s.get("total_repairs")})

        if not self._states_done and (spec / "states.json").exists():
            self._states_done = True
            await self._emit("design.phase",
                             {"key": "states", "state": "done", "label": "states extracted"})

        pdir = self.ws / "prompts" / "runtime_b"
        if pdir.is_dir():
            total = len((json.loads(self._ir_snapshot) or {}).get("agents", [])) \
                if self._ir_snapshot else 0
            for p in sorted(pdir.glob("*.md")):
                if p.stem not in self._prompts_seen:
                    self._prompts_seen.add(p.stem)
                    await self._emit("design.prompt",
                                     {"agent": p.stem, "count": len(self._prompts_seen),
                                      "total": total or "?"})

    async def run(self) -> None:
        await self._emit("design.phase",
                         {"key": "toolchain", "state": "active", "label": "starting designer"})
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self.poll)
        except asyncio.CancelledError:
            await self._tick()   # final sweep so late artifacts still render
            raise


async def run_design(
    task: str,
    *,
    task_spec: Mapping[str, Any] | None = None,
    task_spec_path: Path | str | None = None,
    name: str | None = None,
    model: str | None = None,
    opencode_cmd: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    on_event: Callable[[str, dict], None] | None = None,
    verbose: bool = False,
    live: bool = False,
    live_port: int = 8765,
    live_warmup: float = 3.0,
    live_hold: float = 0.0,
) -> DesignResult:
    """One full design run: requirement in, verified workspace out."""
    run_started_at = datetime.now(timezone.utc).isoformat()
    run_started_ms = time.monotonic() * 1000.0
    root = repo_root()

    # --- Early diagnostics: print task spec metadata before anything else --------
    task_spec_source = "embedded_task_projection"
    task_spec_original_bytes: bytes | None = None
    resolved_task_spec_path: Path | None = None
    if task_spec_path is not None:
        resolved_task_spec_path = Path(task_spec_path).resolve()
        task_spec_original_bytes = resolved_task_spec_path.read_bytes()
        loaded_task_spec = json.loads(task_spec_original_bytes.decode("utf-8-sig"))
        if not isinstance(loaded_task_spec, dict):
            raise ValueError("TeLLMe TaskSpec JSON must contain an object")
        task_spec_payload = loaded_task_spec
        task_spec_source = str(resolved_task_spec_path)
    elif task_spec is not None:
        task_spec_payload = json.loads(json.dumps(dict(task_spec), ensure_ascii=False))
        task_spec_source = "in_memory_taskspec"
    else:
        task_spec_payload = _extract_structured_task(task) or {}
    task_spec_snapshot = json.dumps(task_spec_payload, sort_keys=True, ensure_ascii=False)
    _diag_spec = task_spec_payload or _extract_structured_task(task)
    _diag_route = str((_diag_spec or {}).get("route") or "")
    _diag_query = str((_diag_spec or {}).get("user_query") or "")[:120]
    _diag_harnesses = (_diag_spec or {}).get("candidate_harnesses") or []
    _diag_is_placeholder = task.strip() == (
        "Loaded automatically from the current TeLLMe task spec."
    )
    print(
        f"[run_design] has_tellme_spec={_diag_spec is not None} "
        f"is_placeholder={_diag_is_placeholder} "
        f"route={_diag_route!r} "
        f"task_len={len(task)}",
        flush=True,
    )
    print(f"[run_design] user_query={_diag_query!r}", flush=True)
    print(f"[run_design] candidate_harnesses={_diag_harnesses}", flush=True)
    print(f"[run_design] task_preview={task[:100]!r}", flush=True)
    if _diag_is_placeholder:
        print(
            "[run_design] WARNING: task is the UI placeholder string. "
            "The TeLLMe spec was not passed through correctly — "
            "TASK_AGENT fallback is likely.",
            flush=True,
        )
    # ---------------------------------------------------------------------------

    name = name or slugify(task)
    init_started_at = datetime.now(timezone.utc).isoformat()
    init_started_ms = time.monotonic() * 1000.0
    ws = _init_workspace(name, task)
    ws_rel = str(ws.relative_to(root))
    queued_at = os.getenv("TRACEFIX_UI_QUEUED_AT")
    queue_wait_ms: float | None = None
    if queued_at:
        try:
            queued_time = datetime.fromisoformat(queued_at)
            started_time = datetime.fromisoformat(run_started_at)
            queue_wait_ms = max(0.0, (started_time - queued_time).total_seconds() * 1000.0)
        except ValueError:
            queued_at = None
    timing = PipelineTimingReport(
        ws,
        run_kind="tracefix_design",
        run_id=ws.name,
        started_at=run_started_at,
        started_ms=run_started_ms,
    )
    timing.stage(
        "workspace_initialization",
        started_at=init_started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=time.monotonic() * 1000.0 - init_started_ms,
        success=True,
        workspace=str(ws),
        queued_at=queued_at,
        queue_wait_ms=queue_wait_ms,
    )

    prompt = build_designer_prompt(root)
    cfg = build_design_config(prompt, model=model)

    # Optional live view: same SSE stack as the runtime, design-phase page.
    bus = server = watcher_task = None
    if live:
        from tracefix.runtime.monitoring.event_bus import EventBus
        from tracefix.runtime.monitoring.live_server import start_live_server
        from tracefix.runtime.opencode_adapter.design_view import render_design_html

        bus = EventBus()
        server = await start_live_server(
            {}, bus, port=live_port,
            html=render_design_html(title=f"design: {name}", model=model or ""))
        url = f"http://127.0.0.1:{live_port}"
        print(f"[design] live view: {url}", file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        if live_warmup > 0:
            await asyncio.sleep(live_warmup)
        watcher_task = asyncio.create_task(DesignWatcher(ws, bus).run())

        user_on_event = on_event

        def _tap(agent_id: str, ev: dict) -> None:
            if user_on_event is not None:
                user_on_event(agent_id, ev)
            if ev.get("type") == "tool_use":
                part = ev.get("part") or {}
                asyncio.get_running_loop().create_task(bus.emit("design.tool", {
                    "tool": part.get("tool"),
                    "status": (part.get("state") or {}).get("status"),
                }))
        on_event = _tap

    # Same process hygiene as the runtime adapter: per-run XDG data/state (keep the
    # user's opencode state clean; cache stays shared for the warm ripgrep), and the
    # venv's bin on PATH so `tla-verify-pluscal` resolves inside opencode's bash.
    inst = ws / ".design"
    for sub in ("data", "state"):
        (inst / sub).mkdir(parents=True, exist_ok=True)
    venv_bin = str(Path(_subprocess_python()).resolve().parent)
    env = {
        "XDG_DATA_HOME": str(inst / "data"),
        "XDG_STATE_HOME": str(inst / "state"),
        "PATH": venv_bin + os.pathsep + os.environ.get("PATH", ""),
    }

    if verbose:
        print(f"[design] workspace={ws_rel} model={model or '(opencode default)'} "
              f"timeout={timeout:.0f}s", file=sys.stderr)

    start = time.time()
    fast_path_started_at = datetime.now(timezone.utc).isoformat()
    fast_path_started_ms = time.monotonic() * 1000.0
    fast_path_decision = assess_single_agent_fast_path(task)
    fast_path_used = False
    fast_path_error: str | None = None
    fast_path_ir_duration_ms = 0.0
    fast_path_diagnostics = [
        f"Single-agent fast path considered: {fast_path_decision.reason}"
    ]
    if fast_path_decision.eligible and not task_spec_payload:
        try:
            fast_path_ir_started_ms = time.monotonic() * 1000.0
            fast_path_diagnostics.extend(
                _generate_and_verify_single_agent(ws, fast_path_decision, timing)
            )
            fast_path_ir_duration_ms = (
                time.monotonic() * 1000.0 - fast_path_ir_started_ms
            )
            fast_path_used = True
        except Exception as exc:  # noqa: BLE001 - fallback is the safety boundary
            fast_path_error = str(exc)
            fast_path_diagnostics.append(
                f"Single-agent fast path fell back to OpenCode: {exc}"
            )

    fast_path_duration_ms = time.monotonic() * 1000.0 - fast_path_started_ms
    fast_path_report = {
        "considered": fast_path_decision.considered,
        "used": fast_path_used,
        "reason": fast_path_decision.reason,
        "structured_input": fast_path_decision.structured_input,
        "agent_id": fast_path_decision.agent_id or None,
        "ir_generation_duration_ms": round(fast_path_ir_duration_ms, 2),
        "fallback_to_opencode": not fast_path_used,
        "error": fast_path_error,
    }
    timing.stage(
        "single_agent_fast_path",
        started_at=fast_path_started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=fast_path_duration_ms,
        success=fast_path_used or not fast_path_decision.eligible,
        error=fast_path_error,
        fast_path=fast_path_report,
        aggregate=True,
    )

    # --- Coordination attribute extraction ----------------------------------------
    # The LLM only extracts schema-bound attributes here. Ranking, validation,
    # and procedure selection below are deterministic; any later LLM call only
    # executes the selected mode.
    attribute_extraction_diagnostics: list[str] = []
    procedure_decision_complete = False
    procedure_decision_selected: str | None = None
    procedure_execution_prompt: str | None = None
    procedure_decision = None
    procedure_execution_audit: dict | None = None
    deterministic_reuse_instantiated = False
    single_agent_generation_used = False
    single_agent_generation_decision: FastPathDecision | None = None
    procedure_execution_failed = False
    procedure_execution_error: str | None = None
    template_ranking_failed = False
    attribute_extraction_failed = False
    attributes = None
    if not fast_path_used:
        attr_started_at = datetime.now(timezone.utc).isoformat()
        attr_started_ms = time.monotonic() * 1000.0
        attr_error: str | None = None
        try:
            extractor_input = json.dumps({
                "read_only_taskspec": task_spec_payload,
                "secondary_original_request": task,
            }, indent=2, ensure_ascii=False)
            extractor_input_path = _spec_dir(ws) / "extractor_input.txt"
            write_audit_text(extractor_input_path, extractor_input)
            print("[TRACEFIX EXTRACTOR INPUT START]", flush=True)
            print(extractor_input, flush=True)
            print("[TRACEFIX EXTRACTOR INPUT END]", flush=True)
            extraction_result = extract_with_taskspec_reevaluation(
                task_spec=task_spec_payload,
                original_request=task,
                extractor=extract_coordination_attributes,
                model=_attribute_extractor_model(model),
            )
            validation_report_path = _spec_dir(ws) / "attribute_validation_report.json"
            write_audit_json(validation_report_path, {
                **extraction_result.diagnostic.to_dict(),
                "attempts": extraction_result.attempts,
                "maximum_correction_attempts": MAX_ATTRIBUTE_CORRECTION_ATTEMPTS,
                "task_spec_source": task_spec_source,
                "task_spec_unchanged": json.dumps(task_spec_payload, sort_keys=True, ensure_ascii=False) == task_spec_snapshot,
                "artifact_reuse": "not_implemented_no_existing_safe_version_relationship",
            })
            if extraction_result.attributes is None:
                raise ValueError("TaskSpec attribute contradictions persisted after bounded reevaluation")
            attributes = extraction_result.attributes
            if resolved_task_spec_path is not None and resolved_task_spec_path.read_bytes() != task_spec_original_bytes:
                raise ValueError("TraceFix modified the read-only TeLLMe TaskSpec")
            attr_path = _spec_dir(ws) / "extracted_coordination_attributes.json"
            write_audit_json(attr_path, attributes)
            _print_audit_block("EXTRACTOR OUTPUT", attributes)
            attribute_extraction_diagnostics.append(
                f"Coordination attributes extracted: {attr_path.relative_to(ws)}"
            )
            try:
                engine = DeterministicTemplateEngine()
                task_route = str(task_spec_payload.get("route") or "").strip().lower()
                single_agent_request = engine.is_single_agent_request(
                    attributes,
                    task_route=task_route,
                )
                templates = (
                    []
                    if single_agent_request
                    else [get_template(pattern_id) for pattern_id in list_pattern_ids()]
                )
                rankings = [] if single_agent_request else engine.rank(attributes, templates)
                rankings_path = _spec_dir(ws) / "template_rankings.json"
                write_audit_json(rankings_path, rankings)
                if single_agent_request:
                    validation_audit = {
                        "candidates": [],
                        "skipped": True,
                        "reason": "single_agent_request",
                    }
                    candidate_validations = ()
                else:
                    validation_audit, candidate_validations = engine.validation_audit(
                        attributes,
                        rankings,
                        templates,
                    )
                validation_audit_path = _spec_dir(ws) / "template_validation_results.json"
                write_audit_json(validation_audit_path, validation_audit)
                _print_audit_block("VALIDATOR OUTPUT", validation_audit)
                top_template = (
                    next(template for template in templates if template.get_template_id() == rankings[0].template_id)
                    if rankings
                    else None
                )
                validation = candidate_validations[0] if top_template and candidate_validations else None
                validation_path = _spec_dir(ws) / "template_validation_result.json"
                write_audit_json(
                    validation_path,
                    validation if validation else {
                        "valid": False,
                        "skipped": single_agent_request,
                        "reason": (
                            "single_agent_request"
                            if single_agent_request
                            else "No templates available for validation."
                        ),
                    },
                )
                procedure_options = (
                    engine.single_agent_procedure_options()
                    if single_agent_request
                    else engine.procedure_options(rankings, validation, templates)
                )
                options_path = _spec_dir(ws) / "procedure_options.json"
                write_audit_json(options_path, procedure_options)
                decision = (
                    engine.select_single_agent_procedure(
                        attributes,
                        task_route=task_route,
                    )
                    if single_agent_request
                    else engine.select_procedure(
                        attributes,
                        rankings,
                        validation,
                        procedure_options,
                        templates,
                    )
                )
                procedure_decision = decision
                decision_path = _spec_dir(ws) / "procedure_decision.json"
                write_audit_json(decision_path, decision)
                selected_template_log = decision.selected_template_id or "none"
                print(
                    f"[TRACEFIX PROCEDURE SELECTED] mode={decision.selected_procedure} "
                    f"template={selected_template_log}",
                    flush=True,
                )
                _print_audit_block("PROCEDURE DECISION", decision)
                selected_metadata = (
                    get_template(decision.selected_template_id).to_dict()
                    if decision.selected_template_id
                    else {}
                )
                execution_context = build_procedure_execution_context(
                    query=task,
                    extracted_data=attributes,
                    decision=decision,
                    template_metadata=selected_metadata,
                    task_spec=task_spec_payload,
                )
                context_path = _spec_dir(ws) / "procedure_execution_context.json"
                write_audit_json(context_path, execution_context)
                procedure_decision_complete = True
                procedure_decision_selected = decision.selected_procedure
                if procedure_decision_selected in {
                    "single_agent_generation",
                    "exact_reuse",
                    "parameterized_reuse",
                }:
                    started_at = datetime.now(timezone.utc).isoformat()
                    procedure_execution_audit = {
                        "selected_procedure": decision.selected_procedure,
                        "selected_template_id": decision.selected_template_id,
                        "executor": (
                            "deterministic_single_agent_builder"
                            if procedure_decision_selected == "single_agent_generation"
                            else "deterministic_builder"
                        ),
                        "allowed_fields": (
                            list(decision.parameterizable_fields)
                            if procedure_decision_selected == "parameterized_reuse"
                            else []
                        ),
                        "protected_fields": list(decision.protected_fields),
                        "llm_expected": False,
                        "started_at": started_at,
                        "ended_at": None,
                        "success": False,
                        "artifacts_written": [],
                    }
                    print(
                        f"[TRACEFIX PROCEDURE EXECUTION START] mode={procedure_decision_selected} "
                        f"template={selected_template_log}",
                        flush=True,
                    )
                    print(f"[TRACEFIX {_execution_marker_name(procedure_decision_selected)} START] template={selected_template_log}", flush=True)
                    try:
                        if procedure_decision_selected == "single_agent_generation":
                            single_agent_generation_decision = build_single_agent_generation_input(
                                task,
                                task_spec_payload,
                            )
                            attribute_extraction_diagnostics.extend(
                                _generate_and_verify_single_agent(
                                    ws,
                                    single_agent_generation_decision,
                                    timing,
                                )
                            )
                            artifact_paths = [
                                ws / path for path in _execution_artifacts(ws)
                            ]
                        else:
                            reuse_result = (
                                instantiate_exact_reuse(ws, execution_context)
                                if procedure_decision_selected == "exact_reuse"
                                else instantiate_parameterized_reuse(ws, execution_context)
                            )
                            artifact_paths = list(reuse_result.artifact_paths)
                    except Exception as exc:
                        procedure_execution_audit["ended_at"] = datetime.now(timezone.utc).isoformat()
                        write_audit_json(
                            _spec_dir(ws) / "procedure_execution.json",
                            procedure_execution_audit,
                        )
                        print(f"[TRACEFIX {_execution_marker_name(procedure_decision_selected)} END] result=fail", flush=True)
                        print(
                            f"[TRACEFIX PROCEDURE EXECUTION END] mode={procedure_decision_selected} result=fail",
                            flush=True,
                        )
                        if procedure_decision_selected == "single_agent_generation":
                            raise ProcedureExecutionError(
                                f"single-agent generation failed: {exc}"
                            ) from exc
                        raise
                    procedure_execution_audit.update({
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "success": True,
                        "artifacts_written": [
                            str(path.relative_to(ws)).replace("\\", "/")
                            for path in artifact_paths
                        ],
                    })
                    write_audit_json(
                        _spec_dir(ws) / "procedure_execution.json",
                        procedure_execution_audit,
                    )
                    print(f"[TRACEFIX {_execution_marker_name(procedure_decision_selected)} END] result=pass", flush=True)
                    print(
                        f"[TRACEFIX PROCEDURE EXECUTION END] mode={procedure_decision_selected} result=pass",
                        flush=True,
                    )
                    if procedure_decision_selected == "single_agent_generation":
                        single_agent_generation_used = True
                    else:
                        deterministic_reuse_instantiated = True
                else:
                    procedure_execution_prompt = build_procedure_execution_prompt(
                        execution_context,
                        workspace_rel=ws_rel,
                    )
                    prompt_path = _spec_dir(ws) / "procedure_execution_prompt.txt"
                    write_audit_text(prompt_path, procedure_execution_prompt)
                attribute_extraction_diagnostics.extend([
                    f"Template rankings written: {rankings_path.relative_to(ws)}",
                    f"Template validation written: {validation_path.relative_to(ws)}",
                    f"Procedure options written: {options_path.relative_to(ws)}",
                    f"Procedure decision written: {decision_path.relative_to(ws)}",
                    f"Procedure execution context written: {context_path.relative_to(ws)}",
                    f"Deterministic procedure selected: {procedure_decision_selected}.",
                ])
            except ProcedureExecutionError as exc:
                procedure_execution_failed = True
                procedure_execution_error = str(exc)
                attr_error = str(exc)
                attribute_extraction_diagnostics.append(f"Fixed procedure execution failed: {attr_error}")
            except Exception as exc:  # noqa: BLE001 - ranking/validation failures should be explicit
                template_ranking_failed = True
                attr_error = str(exc)
                attribute_extraction_diagnostics.append(f"Template ranking or validation failed: {attr_error}")
        except Exception as exc:  # noqa: BLE001 - extraction failures should be explicit
            attr_error = str(exc)
            attribute_extraction_failed = True
            attribute_extraction_diagnostics.append(
                f"Coordination attribute extraction failed: {attr_error}"
            )
        attr_duration_ms = time.monotonic() * 1000.0 - attr_started_ms
        timing.stage(
            "llm_attribute_extraction",
            started_at=attr_started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=attr_duration_ms,
            success=attr_error is None,
            error=attr_error,
            extracted_coordination_attributes=(attributes.as_dict() if attributes is not None else {}),
            template_mapping_status=(
                f"deterministic_{procedure_decision_selected}_selected"
                if procedure_decision_complete
                else (
                    "procedure_execution_failed"
                    if procedure_execution_failed
                    else (
                        "template_ranking_failed"
                        if template_ranking_failed
                        else "attribute_extraction_failed"
                    )
                )
            ),
            aggregate=True,
        )
    # -------------------------------------------------------------------------------
    if (
        fast_path_used
        or single_agent_generation_used
        or deterministic_reuse_instantiated
        or procedure_execution_failed
        or template_ranking_failed
        or attribute_extraction_failed
    ):
        disposition = {
            "status": "incomplete",
            "events": 0,
            "stderr_tail": [],
            "returncode": 0,
            "provider": None,
            "model": None,
        }
    else:
        if not procedure_execution_prompt or not procedure_decision_selected or procedure_decision is None:
            raise RuntimeError("deterministic procedure selection produced no execution prompt")
        usage_stage = f"opencode_procedure_execution_{procedure_decision_selected}"
        selected_template_log = procedure_decision.selected_template_id or "none"
        allowed_fields = (
            list(procedure_decision.parameterizable_fields)
            if procedure_decision_selected == "parameterized_reuse"
            else list(dict.fromkeys([
                *procedure_decision.adaptable_fields,
                *procedure_decision.recomposable_fields,
            ]))
            if procedure_decision_selected == "partial_recomposition"
            else []
        )
        procedure_execution_audit = {
            "selected_procedure": procedure_decision_selected,
            "selected_template_id": procedure_decision.selected_template_id,
            "executor": "opencode",
            "allowed_fields": allowed_fields,
            "protected_fields": list(procedure_decision.protected_fields),
            "llm_expected": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "ended_at": None,
            "success": False,
            "artifacts_written": [],
        }
        execution_audit_path = _spec_dir(ws) / "procedure_execution.json"
        write_audit_json(execution_audit_path, procedure_execution_audit)
        marker_name = _execution_marker_name(procedure_decision_selected)
        print(
            f"[TRACEFIX PROCEDURE EXECUTION START] mode={procedure_decision_selected} "
            f"template={selected_template_log}",
            flush=True,
        )
        if procedure_decision_selected == "full_generation":
            print("[TRACEFIX FULL GENERATION START]", flush=True)
        else:
            print(f"[TRACEFIX {marker_name} START] template={selected_template_log}", flush=True)
        try:
            disposition = await run_opencode_agent(
                "designer", cfg,
                opencode_cmd=opencode_cmd or ["opencode"],
                output_dir=root,                      # --dir = repo root: skills + workspace/ resolve
                kickoff=procedure_execution_prompt,
                timeout=timeout,
                on_event=on_event,
                env_overrides=env,
                usage_tracker=timing.usage,
                usage_stage=usage_stage,
                procedure=procedure_decision_selected,
                template_id=procedure_decision.selected_template_id,
            )
        except Exception:
            procedure_execution_audit["ended_at"] = datetime.now(timezone.utc).isoformat()
            write_audit_json(execution_audit_path, procedure_execution_audit)
            print(f"[TRACEFIX {marker_name} END] result=fail", flush=True)
            print(
                f"[TRACEFIX PROCEDURE EXECUTION END] mode={procedure_decision_selected} result=fail",
                flush=True,
            )
            raise
        artifacts_written = _execution_artifacts(ws)
        execution_success = (
            disposition.get("status") not in {"timeout", "error", "cost_limit", "correction_failed"}
            and disposition.get("returncode") in {0, None}
            and bool(artifacts_written)
        )
        execution_result = "pass" if execution_success else "fail"
        procedure_execution_audit.update({
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "success": execution_success,
            "artifacts_written": artifacts_written,
        })
        write_audit_json(execution_audit_path, procedure_execution_audit)
        print(f"[TRACEFIX {marker_name} END] result={execution_result}", flush=True)
        print(
            f"[TRACEFIX PROCEDURE EXECUTION END] mode={procedure_decision_selected} "
            f"result={execution_result}",
            flush=True,
        )
        timing.opencode_call(usage_stage, disposition)

    timed_out = disposition["status"] == "timeout"
    run_diagnostics = [
        f"Workspace initialized: {ws_rel}",
        f"Model requested: {model or '(opencode default)'}",
        *fast_path_diagnostics,
        *attribute_extraction_diagnostics,
        (
                "OpenCode initial design skipped: single-agent deterministic generation verified"
                if (fast_path_used or single_agent_generation_used)
                else (
                    "OpenCode initial design skipped: deterministic reuse instantiated template artifacts"
                    if deterministic_reuse_instantiated
                    else (
                        "OpenCode initial design skipped: fixed procedure setup failed"
                        if (procedure_execution_failed or template_ranking_failed)
                        else (
                            "OpenCode initial design skipped: coordination attribute extraction failed"
                            if attribute_extraction_failed
                            else "OpenCode design attempt finished"
                        )
                    )
            )
        ),
    ]
    run_diagnostics.extend(_workspace_stage_diagnostics(ws, "after initial design"))
    if not (
        fast_path_used
        or single_agent_generation_used
        or deterministic_reuse_instantiated
        or procedure_execution_failed
        or template_ranking_failed
        or attribute_extraction_failed
    ):
        run_diagnostics.extend(_opencode_provider_diagnostics(ws, disposition))
    validation_started_at = datetime.now(timezone.utc).isoformat()
    validation_started_ms = time.monotonic() * 1000.0
    sanitization_report: dict = {}
    status, ir_errors, diagnostics = classify_design_artifacts(
        ws,
        timed_out=timed_out,
        sanitization_report=sanitization_report,
    )
    timing.stage(
        "ir_validation",
        started_at=validation_started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        duration_ms=time.monotonic() * 1000.0 - validation_started_ms,
        success=not ir_errors,
        error="; ".join(ir_errors) if ir_errors else None,
        ir_sanitization=sanitization_report,
    )
    diagnostics = [*run_diagnostics, *diagnostics]
    if procedure_execution_failed and not (fast_path_used or single_agent_generation_used):
        status = f"{procedure_decision_selected or 'procedure'}_execution_failed"
        ir_errors = [procedure_execution_error or "Fixed procedure execution failed."]
        diagnostics.append("Fixed procedure execution failed before valid artifacts were produced.")
    elif (
        procedure_decision_complete
        and not deterministic_reuse_instantiated
        and not single_agent_generation_used
        and not fast_path_used
    ):
        diagnostics.append(
            f"Deterministic {procedure_decision_selected} execution was run through OpenCode."
        )
    elif deterministic_reuse_instantiated and not fast_path_used:
        diagnostics.append(
            f"{procedure_decision_selected} instantiated IR, Protocol.tla, and Protocol.cfg without an LLM call; "
            "normal validation and TLC continue."
        )
    elif single_agent_generation_used:
        diagnostics.append(
            "single_agent_generation produced and verified one-agent artifacts without OpenCode."
        )
    elif template_ranking_failed and not fast_path_used:
        status = "template_ranking_failed"
        ir_errors = ["Template ranking, validation, or procedure option generation failed."]
        diagnostics.append("Template ranking failed: stopping before generation.")
    elif attribute_extraction_failed and not fast_path_used:
        status = "attribute_extraction_failed"
        ir_errors = [
            "Coordination attribute extraction failed before template mapping."
        ]
        diagnostics.append(
            "Coordination attribute extraction failed: stopping before template "
            "reuse, OpenCode fallback, IR generation, PlusCal, or TLC."
        )
    repair_disposition = None
    continuation_disposition = None
    prompt_disposition = None
    if (
        status == "ir_incomplete"
        and not timed_out
        and not deterministic_reuse_instantiated
        and procedure_decision_selected in {"partial_recomposition", "full_generation"}
        and any("before PlusCal scaffolding" in error for error in ir_errors)
    ):
        diagnostics.append("PlusCal scaffold fallback started")
        scaffold_started_at = datetime.now(timezone.utc).isoformat()
        scaffold_started_ms = time.monotonic() * 1000.0
        try:
            diagnostics.extend(_scaffold_valid_ir(ws))
        except Exception as exc:  # noqa: BLE001 - keep the design report actionable
            timing.stage(
                "protocol_scaffold",
                started_at=scaffold_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - scaffold_started_ms,
                success=False,
                error=str(exc),
            )
            diagnostics.append(f"PlusCal scaffold fallback failed: {exc}")
        else:
            timing.stage(
                "protocol_scaffold",
                started_at=scaffold_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - scaffold_started_ms,
                success=True,
            )
            diagnostics.append("PlusCal continuation pass started")
            continuation_disposition = await run_opencode_agent(
                "designer",
                cfg,
                opencode_cmd=opencode_cmd or ["opencode"],
                output_dir=root,
                kickoff=pluscal_completion_kickoff(ws_rel),
                timeout=timeout,
                on_event=on_event,
                env_overrides=env,
                usage_tracker=timing.usage,
                usage_stage="opencode_pluscal_continuation",
                procedure=procedure_decision_selected,
                template_id=(procedure_decision.selected_template_id if procedure_decision else None),
            )
            diagnostics.append("PlusCal continuation pass finished")
            status, ir_errors, continuation_diagnostics = classify_design_artifacts(
                ws,
                timed_out=continuation_disposition["status"] == "timeout",
            )
            diagnostics.extend(continuation_diagnostics)
            if (
                status == "pluscal_error"
                and continuation_disposition["status"] != "timeout"
            ):
                try:
                    diagnostics.extend(_run_tlc_and_extract(ws, timing))
                except Exception as exc:  # noqa: BLE001
                    diagnostics.append(f"TLC direct fallback exception: {exc}")
                status, ir_errors, tlc_fallback_diagnostics = classify_design_artifacts(ws)
                diagnostics.extend(tlc_fallback_diagnostics)

    if (
        status == "ir_incomplete"
        and not timed_out
        and not deterministic_reuse_instantiated
        and procedure_decision_selected in {"partial_recomposition", "full_generation"}
        and any("no communication channels" in error for error in ir_errors)
    ):
        diagnostics.append("IR repair pass started")
        ir_before_repair = _load_ir(ws)
        repair_disposition = await run_opencode_agent(
            "designer_ir_repair",
            cfg,
            opencode_cmd=opencode_cmd or ["opencode"],
            output_dir=root,
            kickoff=ir_repair_kickoff(ws_rel, ir_errors),
            timeout=min(timeout, 600.0),
            on_event=on_event,
            env_overrides=env,
            usage_tracker=timing.usage,
            usage_stage="opencode_ir_repair",
            procedure=procedure_decision_selected,
            template_id=(procedure_decision.selected_template_id if procedure_decision else None),
        )
        timing.opencode_call("opencode_ir_repair", repair_disposition)
        diagnostics.append("IR repair pass finished")
        diagnostics.extend(_channel_diagnostics(ir_before_repair, _load_ir(ws)))
        diagnostics.extend(_workspace_stage_diagnostics(ws, "after IR repair"))
        diagnostics.extend(_opencode_provider_diagnostics(ws, repair_disposition))
        status, ir_errors, repair_diagnostics = classify_design_artifacts(
            ws,
            timed_out=repair_disposition["status"] == "timeout",
        )
        diagnostics.extend(repair_diagnostics)
        # If repair produced a valid IR but Protocol.tla still missing, scaffold now.
        if (
            status == "ir_incomplete"
            and repair_disposition["status"] != "timeout"
            and any("before PlusCal scaffolding" in error for error in ir_errors)
        ):
            diagnostics.append("IR valid after repair; starting PlusCal scaffold")
            scaffold_started_at = datetime.now(timezone.utc).isoformat()
            scaffold_started_ms = time.monotonic() * 1000.0
            try:
                diagnostics.extend(_scaffold_valid_ir(ws))
            except Exception as exc:  # noqa: BLE001
                timing.stage(
                    "protocol_scaffold_after_repair",
                    started_at=scaffold_started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=time.monotonic() * 1000.0 - scaffold_started_ms,
                    success=False,
                    error=str(exc),
                )
                diagnostics.append(f"PlusCal scaffold after repair failed: {exc}")
            else:
                timing.stage(
                    "protocol_scaffold_after_repair",
                    started_at=scaffold_started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=time.monotonic() * 1000.0 - scaffold_started_ms,
                    success=True,
                )
                diagnostics.append("PlusCal continuation pass started (post-repair)")
                post_repair_continuation = await run_opencode_agent(
                    "designer",
                    cfg,
                    opencode_cmd=opencode_cmd or ["opencode"],
                    output_dir=root,
                    kickoff=pluscal_completion_kickoff(ws_rel),
                    timeout=timeout,
                    on_event=on_event,
                    env_overrides=env,
                    usage_tracker=timing.usage,
                    usage_stage="opencode_pluscal_continuation_post_repair",
                    procedure=procedure_decision_selected,
                    template_id=(procedure_decision.selected_template_id if procedure_decision else None),
                )
                timing.opencode_call(
                    "opencode_pluscal_continuation_post_repair",
                    post_repair_continuation,
                )
                diagnostics.append("PlusCal continuation pass finished (post-repair)")
                status, ir_errors, post_repair_diagnostics = classify_design_artifacts(
                    ws,
                    timed_out=post_repair_continuation["status"] == "timeout",
                )
                diagnostics.extend(post_repair_diagnostics)
                if (
                    status == "pluscal_error"
                    and post_repair_continuation["status"] != "timeout"
                ):
                    try:
                        diagnostics.extend(_run_tlc_and_extract(ws, timing))
                    except Exception as exc:  # noqa: BLE001
                        diagnostics.append(f"TLC direct fallback exception (post-repair): {exc}")
                    status, ir_errors, tlc_fallback_diagnostics = classify_design_artifacts(ws)
                    diagnostics.extend(tlc_fallback_diagnostics)
                if repair_disposition is not None:
                    repair_disposition["events"] = (
                        repair_disposition.get("events", 0)
                        + post_repair_continuation.get("events", 0)
                    )

    needs_verify, verify_gate_diagnostics = _verification_needed_after_scaffold(ws)
    diagnostics.extend(verify_gate_diagnostics)
    if needs_verify:
        diagnostics.append(
            "Post-scaffold verification gate started: IR and scaffold artifacts "
            "are present, so PlusCal/TLC must run"
        )
        try:
            diagnostics.extend(_run_tlc_and_extract(ws, timing))
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(f"Post-scaffold verification gate exception: {exc}")
            _write_tlc_stage_error(
                ws,
                "PlusCal/TLC Stage Exception",
                "TraceFix reached a valid IR plus Protocol.tla/Protocol.cfg, "
                "but the deterministic verification gate raised before it could "
                "complete.\n\n"
                f"Exception:\n\n```\n{exc}\n```",
            )
        status, ir_errors, gate_diagnostics = classify_design_artifacts(ws)
        diagnostics.extend(gate_diagnostics)

    if _verified_protocol_ready(ws) and not timed_out:
        diagnostics.append("Prompt gate started")
        diagnostics.extend(_ensure_plan_before_prompts(ws))
        if (
            fast_path_used
            or single_agent_generation_used
            or deterministic_reuse_instantiated
        ) and not _runtime_prompts_current(ws):
            deterministic_prompt_started_at = datetime.now(timezone.utc).isoformat()
            deterministic_prompt_started_ms = time.monotonic() * 1000.0
            try:
                plan_path = _spec_dir(ws) / "cityos_module_plan.json"
                plan = safe_read_json(plan_path, {})
                prompt_dir = ws / "prompts" / "runtime_b"
                prompt_dir.mkdir(parents=True, exist_ok=True)
                if fast_path_used or single_agent_generation_used:
                    prompt_decisions = [
                        single_agent_generation_decision
                        if single_agent_generation_used
                        else fast_path_decision
                    ]
                    prompt_paths = []
                    for prompt_decision in prompt_decisions:
                        prompt_text = render_verified_runtime_prompt(prompt_decision, plan)
                        prompt_path = prompt_dir / f"{prompt_decision.agent_id}.md"
                        prompt_path.write_text(prompt_text, encoding="utf-8")
                        prompt_paths.append(str(prompt_path))
                else:
                    prompt_paths = _write_deterministic_reuse_prompts(ws, task)
            except Exception as exc:  # noqa: BLE001
                timing.stage(
                    "deterministic_runtime_prompt",
                    started_at=deterministic_prompt_started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=(
                        time.monotonic() * 1000.0
                        - deterministic_prompt_started_ms
                    ),
                    success=False,
                    error=str(exc),
                )
                diagnostics.append(
                    f"Deterministic verified-plan prompt generation failed: {exc}"
                )
            else:
                timing.stage(
                    "deterministic_runtime_prompt",
                    started_at=deterministic_prompt_started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=(
                        time.monotonic() * 1000.0
                        - deterministic_prompt_started_ms
                    ),
                    success=True,
                    prompt_paths=prompt_paths,
                )
                diagnostics.append(
                    "Deterministic runtime prompt generated from verified "
                    f"CityOS plan: {', '.join(prompt_paths)}"
                )
        if (
            not _runtime_prompts_current(ws)
            and not deterministic_reuse_instantiated
            and procedure_decision_selected in {"partial_recomposition", "full_generation"}
        ):
            diagnostics.append("Prompt generation pass started")
            prompt_disposition = await run_opencode_agent(
                "designer",
                cfg,
                opencode_cmd=opencode_cmd or ["opencode"],
                output_dir=root,
                kickoff=prompt_generation_kickoff(ws_rel),
                timeout=min(timeout, 900.0),
                on_event=on_event,
                env_overrides=env,
                usage_tracker=timing.usage,
                usage_stage="opencode_runtime_prompt_generation",
                procedure=procedure_decision_selected,
                template_id=(procedure_decision.selected_template_id if procedure_decision else None),
            )
            timing.opencode_call("opencode_runtime_prompt_generation", prompt_disposition)
            timing.opencode_call("opencode_pluscal_continuation", continuation_disposition)
            diagnostics.append("Prompt generation pass finished")
            if prompt_disposition["status"] == "timeout":
                diagnostics.append("Prompt generation pass timed out")
            diagnostics.extend(_ensure_plan_before_prompts(ws))

    result = judge(ws, timed_out=timed_out)
    if result.success:
        result.status = "ready"
    elif status != "ready":
        result.status = status
    result.ir_errors = ir_errors
    result.diagnostics = diagnostics
    result.duration = time.time() - start
    result.events = disposition.get("events", 0) + (
        repair_disposition.get("events", 0) if repair_disposition else 0
    ) + (
        continuation_disposition.get("events", 0) if continuation_disposition else 0
    ) + (
        prompt_disposition.get("events", 0) if prompt_disposition else 0
    )
    result.stderr_tail = [
        *disposition.get("stderr_tail", []),
        *(continuation_disposition.get("stderr_tail", []) if continuation_disposition else []),
        *(repair_disposition.get("stderr_tail", []) if repair_disposition else []),
        *(prompt_disposition.get("stderr_tail", []) if prompt_disposition else []),
    ]
    if result.status == "incomplete" and disposition.get("returncode") not in (0, None):
        result.status = "error"
    if _looks_like_init_stub(_load_ir(ws)) and any(
        "provider authentication error detected" in item
        or "provider model error detected" in item
        for item in diagnostics
    ):
        result.status = "error"
        result.ir_errors = [
            "OpenCode/provider startup failed before TraceFix could replace "
            "the initial IR stub."
        ]

    if result.success:
        plan_started_at = datetime.now(timezone.utc).isoformat()
        plan_started_ms = time.monotonic() * 1000.0
        try:
            diagnostics.extend(_ensure_cityos_plan(ws))
        except Exception as e:  # noqa: BLE001 - design should report the export issue cleanly
            timing.stage(
                "cityos_plan_export",
                started_at=plan_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - plan_started_ms,
                success=False,
                error=str(e),
            )
            result.success = False
            result.status = "cityos_plan_failed"
            result.stderr_tail = [*result.stderr_tail, f"cityos plan export failed: {e}"]
        else:
            timing.stage(
                "cityos_plan_export",
                started_at=plan_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - plan_started_ms,
                success=True,
            )

    if (
        result.success
        and attributes is not None
        and procedure_decision_selected != "exact_reuse"
    ):
        promotion_started_at = datetime.now(timezone.utc).isoformat()
        promotion_started_ms = time.monotonic() * 1000.0
        try:
            promoted_template, promoted_dir = promote_verified_workspace_template(
                ws,
                extracted=attributes,
                tlc_passed=result.tlc_passed,
            )
        except Exception as exc:  # noqa: BLE001 - promotion is an explicit completion gate
            timing.stage(
                "generated_template_promotion",
                started_at=promotion_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - promotion_started_ms,
                success=False,
                error=str(exc),
            )
            result.success = False
            result.status = "template_promotion_failed"
            result.stderr_tail = [*result.stderr_tail, f"template promotion failed: {exc}"]
            diagnostics.append(f"Verified template promotion failed: {exc}")
        else:
            timing.stage(
                "generated_template_promotion",
                started_at=promotion_started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_ms=time.monotonic() * 1000.0 - promotion_started_ms,
                success=True,
                template_id=promoted_template.get_template_id(),
                registry_dir=str(promoted_dir),
            )
            diagnostics.append(
                "Verified canonical Template promoted and persisted: "
                f"{promoted_template.get_template_id()}"
            )

    if live and bus is not None:
        if watcher_task is not None:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        await bus.emit("design.done", {"status": result.status, "workspace": ws_rel})
        if live_hold > 0:
            await asyncio.sleep(live_hold)
        else:
            await asyncio.sleep(1.0)  # let the browser drain the final events
        if server is not None:
            from tracefix.runtime.monitoring.live_server import stop_live_server
            await stop_live_server(server)

    timing.finalize()
    return result

