"""``tracefix`` — plan and verify TraceFix workspaces.

TraceFix's production output is a verified intermediate CityOS module plan.
CityOS Synthesizer consumes that blueprint later and produces deployable
CityOS modules. The legacy ``run`` command remains for local debugging only:

    tracefix run --workspace workspace/my_task

Defaults to the **opencode** harness (tracefix's per-agent OpenCode harness).
Switch with ``--harness {opencode,monitoring}``. Common flags (--model,
--live, --verbose) are forwarded; any harness-specific flags after them are
passed straight through to that harness's ``run`` command, e.g.::

    tracefix run --workspace ws --opencode-bin 'bun run /path/to/opencode'
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import sys
from pathlib import Path

from tracefix.runtime.workspace_layout import spec_path

# harness name → CLI module exposing main(argv)
# (The Claude-SDK harness exists in the tree but is intentionally not surfaced here;
# the default is opencode, with the built-in monitoring loop for benchmark-style runs.)
_HARNESS_MODULES = {
    "opencode": "tracefix.runtime.opencode_adapter.cli",
    "monitoring": "tracefix.runtime.monitoring.cli",
}


def _derive_task(workspace: str, task: str | None) -> str:
    """Task id/label — explicit wins, else the workspace folder name."""
    if task:
        return task
    return Path(workspace).resolve().name or "custom"


def _has_prompts(ws: Path) -> bool:
    # orchestrators look for prompts/runtime_b/ then prompts/ (flat fallback)
    for sub in ("prompts/runtime_b", "prompts"):
        d = ws / sub
        if d.is_dir() and any(d.glob("*.md")):
            return True
    return False


def _preflight(workspace: str) -> list[str]:
    """Human-readable blockers (empty list = ready to run)."""
    ws = Path(workspace)
    if not ws.exists():
        return [f"workspace not found: {workspace}"]
    problems = []
    if not spec_path(ws, "ir.json").exists():
        problems.append("missing ir.json (expected spec/ir.json, or the workspace root)")
    if not _has_prompts(ws):
        problems.append("no per-agent prompts found (expected prompts/runtime_b/<agent>.md)")
    return problems


def _opencode_bin_from(extra: list[str], default: str = "opencode") -> str:
    """The opencode command a run will use, honoring a passthrough ``--opencode-bin``."""
    for i, tok in enumerate(extra):
        if tok == "--opencode-bin" and i + 1 < len(extra):
            return extra[i + 1]
        if tok.startswith("--opencode-bin="):
            return tok.split("=", 1)[1]
    return default


def _opencode_blockers(opencode_cmd: str, *, needs_mcp: bool) -> list[str]:
    """Human-readable blockers for the opencode path (empty list = ready).

    Catches the two onboarding footguns: the ``opencode`` CLI not being installed
    (it is an external binary, not a pip dependency), and — for ``tracefix run`` —
    the ``mcp`` package being absent (each agent spawns the ``tracefix-coord`` stdio
    MCP server, which imports it).
    """
    import importlib.util
    import shlex

    problems: list[str] = []
    parts = shlex.split(opencode_cmd)
    exe = parts[0] if parts else "opencode"
    if shutil.which(exe) is None:
        problems.append(
            f"opencode CLI not found on PATH: {exe!r}\n"
            "    The opencode harness runs each agent as an opencode process. Install it:\n"
            "        curl -fsSL https://opencode.ai/install | bash      (or: npm i -g opencode-ai)\n"
            "    Already built one (e.g. the TraceFix TUI)? Point at it:  --opencode-bin '/path/to/opencode'"
        )
    if needs_mcp and importlib.util.find_spec("mcp") is None:
        problems.append(
            "the 'mcp' package is missing (each opencode agent spawns the tracefix-coord\n"
            "    stdio MCP server, which imports it). Install the run extras:\n"
            "        pip install -e \".[opencode]\""
        )
    return problems


def cmd_run(args: argparse.Namespace, extra: list[str]) -> int:
    problems = _preflight(args.workspace)
    if problems:
        print("Cannot run this workspace yet:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nProduce a runnable workspace with the /tla-verify-pluscal skill "
            "(it designs + verifies the protocol and generates the prompts), "
            "then re-run this command.",
            file=sys.stderr,
        )
        return 2

    if args.harness == "opencode":
        blockers = _opencode_blockers(_opencode_bin_from(extra), needs_mcp=True)
        if blockers:
            print("Cannot run the opencode harness yet:", file=sys.stderr)
            for b in blockers:
                print(f"  - {b}", file=sys.stderr)
            return 2

    ws = Path(args.workspace)
    if not spec_path(ws, "states.json").exists():
        print(
            "note: no states.json — signal_done() will be ungated (per-agent FSM "
            "checks off). Run `tla-verify-pluscal extract-states` for full enforcement.",
            file=sys.stderr,
        )

    task = _derive_task(args.workspace, args.task)
    argv = ["run", "--task", task, "--workspace", args.workspace]
    if args.model:
        argv += ["--model", args.model]
    if args.live:
        argv += ["--live"]
    if args.verbose:
        argv += ["--verbose"]
    argv += extra  # harness-specific passthrough (e.g. --opencode-bin, --builtins)

    mod = importlib.import_module(_HARNESS_MODULES[args.harness])
    if args.verbose:
        print(f"[tracefix] harness={args.harness} task={task} "
              f"workspace={args.workspace}", file=sys.stderr)
    try:
        rc = mod.main(argv)
    except SystemExit as e:  # sdk/opencode mains sys.exit(code)
        rc = e.code
    if rc is None:
        return 0
    return rc if isinstance(rc, int) else 1


def cmd_export_cityos_plan(args: argparse.Namespace) -> int:
    """Export a verified TraceFix workspace as a CityOS module plan."""
    from tracefix.runtime.cityos_plan import export_cityos_module_plan

    try:
        result = export_cityos_module_plan(
            Path(args.workspace),
            Path(args.out) if args.out else None,
        )
    except Exception as e:  # noqa: BLE001 - CLI should fail cleanly
        print(f"Cannot export CityOS module plan: {e}", file=sys.stderr)
        return 2

    print(f"CityOS module plan written to: {result.plan_path}")
    print(f"Agents: {', '.join(result.agents) if result.agents else '(none detected)'}")
    print("Next stage: CityOS Synthesizer consumes this plan and builds one CityOS app per agent plus one monitor app.")
    return 0


def cmd_design(args: argparse.Namespace) -> int:
    import asyncio
    import shlex

    from tracefix.runtime.env_setup import load_repo_env
    from tracefix.runtime.opencode_adapter.design import run_design

    load_repo_env()  # the spawned opencode inherits the API keys from .env

    blockers = _opencode_blockers(args.opencode_bin or "opencode", needs_mcp=False)
    if blockers:
        print("Cannot run `tracefix design` yet:", file=sys.stderr)
        for b in blockers:
            print(f"  - {b}", file=sys.stderr)
        return 2

    opencode_cmd = shlex.split(args.opencode_bin) if args.opencode_bin else ["opencode"]
    result = asyncio.run(run_design(
        args.task, name=args.name, model=args.model,
        task_spec_path=args.task_spec,
        opencode_cmd=opencode_cmd, timeout=args.timeout, verbose=args.verbose,
        live=args.live, live_port=args.live_port, live_hold=args.live_hold))

    print(f"\n=== tracefix design: {result.status.upper()} "
          f"({result.duration:.0f}s, {result.events} events) ===")
    print(f"workspace: {result.workspace}")
    if result.agents:
        print(f"agents:    {', '.join(result.agents)}")
    if result.tlc_passed is not None:
        rep = f" after {result.repairs} repair(s)" if result.repairs else ""
        print(f"TLC:       {'PASS' if result.tlc_passed else 'FAIL'}{rep}")
    if result.prompts:
        print(f"prompts:   {', '.join(result.prompts)}")
    if result.success:
        print(f"cityos plan: {Path(result.workspace) / 'spec' / 'cityos_module_plan.json'}")
        print("\nReady for CityOS synthesis:")
        print(f"  tracefix export-cityos-plan --workspace {result.workspace}")
        print("\nLegacy local debug runner:")
        print(f"  tracefix run --local-dev --workspace {result.workspace}")
    else:
        if result.status == "ir_incomplete":
            if result.ir_errors and any("PlusCal scaffolding" in error for error in result.ir_errors):
                print("\nIR complete, but the design stopped before PlusCal scaffolding/TLC completed.")
            else:
                print("\nIR incomplete: multi-agent topology has no communication channels. PlusCal scaffolding and TLC did not run.")
            if result.ir_errors:
                print("IR validation errors:")
                for error in result.ir_errors:
                    print(f"  - {error}")
            print("\nFix spec/ir.json or re-run `tracefix design` with a clearer task.")
        elif result.status == "pluscal_error":
            print("\nPlusCal stage incomplete: Protocol.tla exists, but TLC/states extraction did not complete.")
        elif result.status == "tlc_error":
            print("\nTLC verification failed. Inspect spec/tlc_error.md and spec/history/.")
        else:
            print("\nNot runnable yet. Re-run `tracefix design`, or finish manually with the /tla-verify-pluscal skill.")
        if result.diagnostics:
            print("Design diagnostics:")
            for line in result.diagnostics:
                print(f"  - {line}")
        if result.stderr_tail and args.verbose:
            print("--- opencode stderr tail ---")
            for line in result.stderr_tail:
                print(f"  {line}")
    return 0 if result.success else 1


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="tracefix",
        description="Design and verify TraceFix workspaces, then emit a CityOS "
                    "module plan for the CityOS Synthesizer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    design = sub.add_parser(
        "design",
        help="Design + verify a protocol from a natural-language requirement "
             "(headless opencode + the tla-verify-pluscal skill)",
        description="Turn a requirement into a verified workspace and CityOS "
                    "module plan: "
                    "IR design → PlusCal → TLC (with repair) → states.json → "
                    "per-agent prompts → spec/cityos_module_plan.json. No "
                    "protocol is hand-written.",
    )
    design.add_argument("task", help="The MAS requirement, in natural language")
    design.add_argument("--name", default=None,
                        help="Workspace name (default: derived from the task)")
    design.add_argument("--task-spec", default=None,
                        help="Read-only path to the official TeLLMe tracefix_task_spec.json")
    design.add_argument("--model", default=None,
                        help="opencode model, provider/modelID (e.g. openai/gpt-5.4)")
    design.add_argument("--timeout", type=float, default=1800.0,
                        help="Wall-clock cap in seconds (default: 1800)")
    design.add_argument("--opencode-bin", default="opencode",
                        help="opencode binary/command (shlex-split)")
    design.add_argument("--live", action="store_true",
                        help="Real-time design view in the browser (phases, IR "
                             "topology, TLC verdict, activity feed)")
    design.add_argument("--live-port", type=int, default=8765)
    design.add_argument("--live-hold", type=float, default=0.0,
                        help="Keep the live view up N seconds after the run")
    design.add_argument("--verbose", action="store_true")

    run = sub.add_parser(
        "run",
        help="Legacy/local-dev only: run a verified workspace with a local harness",
        description="Legacy local debugging path. CityOS production execution "
                    "uses the exported module plan, CityOS Synthesizer, and "
                    "CityOS Runtime OS. Unknown flags are passed through to the "
                    "selected harness.",
    )
    run.add_argument("--workspace", required=True,
                     help="Path to a verified workspace (spec/ir.json + prompts/)")
    run.add_argument("--harness", choices=list(_HARNESS_MODULES), default="opencode",
                     help="Agent harness (default: opencode)")
    run.add_argument("--task", default=None,
                     help="Task id/label (default: the workspace folder name)")
    run.add_argument("--model", default=None,
                     help="Model override (the harness default if omitted)")
    run.add_argument("--live", action="store_true",
                     help="Real-time D3/SSE visualization in the browser")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--local-dev", "--legacy-runner", action="store_true",
                     dest="local_dev",
                     help="Acknowledge that this is the legacy local debug runner, "
                          "not the CityOS production execution path")

    export_plan = sub.add_parser(
        "export-cityos-plan",
        help="Export a verified workspace as a CityOS module plan",
        description="Export an already-generated TraceFix workspace as a verified "
                    "intermediate module plan. Docker packaging belongs to the "
                    "later CityOS Synthesizer stage.",
    )
    export_plan.add_argument("--workspace", required=True,
                             help="Path to a generated, verified TraceFix workspace")
    export_plan.add_argument("--out", default=None,
                             help="Optional output path. Defaults to spec/cityos_module_plan.json")

    args, extra = parser.parse_known_args(argv)
    if args.command == "design":
        sys.exit(cmd_design(args))
    if args.command == "run":
        if not getattr(args, "local_dev", False):
            print(
                "warning: `tracefix run` is legacy/local-dev only. "
                "CityOS production uses `tracefix export-cityos-plan` and the CityOS Synthesizer.",
                file=sys.stderr,
            )
        sys.exit(cmd_run(args, extra))
    if args.command == "export-cityos-plan":
        sys.exit(cmd_export_cityos_plan(args))


if __name__ == "__main__":
    main()
