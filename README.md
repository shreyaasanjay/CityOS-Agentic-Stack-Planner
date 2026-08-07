# TraceFix CityOS Agentic Stack Planner

TraceFix CityOS Agentic Stack Planner turns a natural-language question into a
formally verified multi-agent workflow, generates the agent packages needed to
perform the work, and presents a privacy-safe answer in TeLLMe.

The repository currently supports two data sources:

- **Smart Room** — recorded camera-inference data, selected by date and recording.
- **CARLA simulation** — recorded city-street traces containing vehicles and pedestrians.

TraceFix verifies the **coordination protocol** before the agents run. It does
not claim to prove the factual correctness of a model's answer; the generated
agents are responsible for retrieving and interpreting the selected source data.

## How it works

```text
Question in TeLLMe
  -> choose an exact Smart Room recording or CARLA trace
  -> TraceFix creates or reuses a verified protocol
  -> Agent Synthesizer generates or reuses agent handlers
  -> retrieval agent obtains source evidence
  -> answer agent produces a privacy-safe answer
  -> monitor checks the observed workflow
  -> TeLLMe displays the result
```

For a Smart Room workflow, the verified protocol normally contains a
`RETRIEVAL_AGENT`, an `ANSWER_AGENT`, a permitted `evidence_packet` handoff,
and a monitor. The exact topology is determined by the verified workspace.

## What is generated and what is prebuilt

TraceFix provides the prebuilt infrastructure: protocol verification, request
selection, app packaging, runtime orchestration, monitoring, and privacy-safe
response shaping.

Agent Synthesizer creates a package for every agent in the verified plan and a
separate monitor package. Each package includes a generated Python handler,
verified plan artifacts, and deployment scaffolding. Generated handler source
is cached by the verified plan, instructions, template version, agent identity,
and selected code-generation model; an unchanged workflow can reuse it instead
of generating code again.

For local demonstrations, the web-data harness runs the generated handlers
against the selected source. These packages are also structured as deployable
CityOS-style app artifacts; deploying them to a production runtime is a
separate operational step.

## Requirements

- Python 3.11 or later
- Java 17 and `lib/tla2tools.jar` for TLC model checking
- Node.js and npm for the TeLLMe Next.js frontend
- OpenCode CLI for LLM-assisted TraceFix design runs
- API keys only for the providers and data sources you use

The default code-generation model is `z-ai/glm-5.2` through OpenRouter, but the
model is configurable in the UI/request.

## First-time setup

From the repository root, create a virtual environment and install the Python
dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test,agentic,opencode]"
python -m pip install -r local-ui\requirements-ui.txt
```

Create a local environment file if you want the backend to load credentials
from the shell environment:

```powershell
Copy-Item .env.example .env
```

Keep `.env` local. It is ignored by Git; never put real credentials in source
code, generated handlers, screenshots, or `.env.example`.

Useful settings include:

```env
# LLM providers
OPENAI_API_KEY=
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=

# Optional CARLA trace server settings
SIMULATION_API_URL=
SIMULATION_API_KEY=
SIMULATION_FULL_API_KEY=
```

`SIMULATION_FULL_API_KEY` is preferred when present; `SIMULATION_API_KEY` is the
fallback. You may instead provide CARLA keys at request time through TeLLMe.

## Run TeLLMe (recommended)

The current TeLLMe experience is the Next.js frontend in
`tellme-potential-ui`, backed by the TraceFix runner.

In the first PowerShell window, start the backend:

```powershell
.\local-ui\start-runner.ps1 -Port 8788
```

In a second PowerShell window, start the frontend:

```powershell
Set-Location .\tellme-potential-ui
Copy-Item .env.example .env.local
npm ci
npm run dev
```

Open <http://127.0.0.1:3000>.

By default, the frontend calls `http://127.0.0.1:8788`. To use another backend,
set this value in `tellme-potential-ui/.env.local`:

```env
TELLME_BACKEND_URL=http://127.0.0.1:8788
```

See [tellme-potential-ui/README.md](tellme-potential-ui/README.md) for the
frontend-specific guide.

## Use the unified planner UI

The repository also includes a single-page TraceFix planner and Agent
Synthesizer UI. Start it with:

```powershell
.\local-ui\start-synth.ps1 -Port 8790 -Open
```

Open <http://127.0.0.1:8790>. To make it available on your local network:

```powershell
.\local-ui\start-synth.ps1 -Port 8790 -Lan
```

The scripts can also start the viewer, runner, and synthesizer together:

```powershell
.\local-ui\start-both.ps1 -Open
```

Stop local UI processes with:

```powershell
.\local-ui\stop-ui.ps1
```

## Asking a question

1. Choose **Smart Room** or **CARLA simulation** in TeLLMe.
2. Enter a specific question.
3. For Smart Room, include a date or select an offered recording before the
   workflow is generated. This avoids querying an ambiguous set of recordings.
4. For CARLA, select the trace to analyze when more than one is available.
5. Choose the TraceFix provider/model and provide the required key only at
   runtime.
6. Run the workflow.

Examples:

- Smart Room: “How many people were observed in the room on July 22, 2026?”
- Smart Room: “What activities were detected in the selected recording?”
- CARLA: “How many pedestrians appear in the selected simulation trace?”
- CARLA: “Which vehicle types are present in this trace?”

Smart Room answers are based on a single selected recording. Occupancy is
derived from concurrent anonymous tracks; it is not the sum of every track ever
seen across every frame or camera.

## Verification and synthesis

TraceFix creates a workspace for a new workflow and verifies its coordination
protocol with PlusCal/TLA+ and TLC. A successful workspace includes:

```text
workspace/<run_id>/
  spec/
    ir.json
    Protocol.tla
    Protocol.cfg
    Protocol_translated.tla
    states.json
    summary.json
    cityos_module_plan.json
  prompts/
  output/
```

The key handoff artifact is:

```text
spec/cityos_module_plan.json
```

Agent Synthesizer consumes that verified plan and creates one package per agent
plus one monitor package. By default, packages and the synthesis manifest are
written to:

```text
workspace/<run_id>/output/cityos_synthesis/
```

An explicit `appsDir` or CityOS apps directory can be supplied when synthesis
needs to write elsewhere.

Local web-data results are written under:

```text
.tracefix-ui/web-data-runs/
```

Those run folders contain request snapshots, handler records, answer artifacts,
and workflow receipts intended for debugging and demonstrations. Treat them as
local artifacts: do not publish them without checking their contents.

## CLI workflow

Generate a verified workspace from a task:

```powershell
tracefix design "Design a smart room application with independent agents for occupancy, lighting, badge access, and monitoring" --model <model-name> --verbose
```

Export or regenerate the verified synthesis plan:

```powershell
tracefix export-cityos-plan --workspace workspace/<generated_workspace>
```

Run the legacy local debug runner only when needed:

```powershell
tracefix run --local-dev --workspace workspace/<generated_workspace>
```

## Project structure

```text
tracefix/
  pipeline/              Protocol design, PlusCal/TLA+, TLC verification
  runtime/               CLI, synthesis, local web-data execution, monitors
  runner_ui/             Backend and unified local planner UI
  cityos_synth_ui/       Standalone synthesis UI

tellme-potential-ui/     Next.js TeLLMe frontend
tellme_harness/          TeLLMe planning and handoff logic
capability_service/      Capability metadata service
capability_bridge/       Capability-service integration
cityos_mock_data/        Local privacy-safe fixtures
benchmark/               Coordination benchmark tasks
docs/                    Architecture and handoff documentation
local-ui/                PowerShell launch and stop scripts
workspace/               Generated verification workspaces (ignored by Git)
```

For the complete implementation-level view, see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/CITYOS_MODULE_PLAN.md](docs/CITYOS_MODULE_PLAN.md).

## Troubleshooting

### TeLLMe cannot reach the backend

Confirm the runner is listening on port `8788` and that
`TELLME_BACKEND_URL` in `.env.local` matches it. Then restart the Next.js
frontend after changing `.env.local`.

### Verification cannot start or complete

Check that Java 17 and `lib/tla2tools.jar` are available. For LLM-assisted
runs, also verify the selected provider, model, API key, and network access.

### Synthesis is unavailable

The selected workspace must contain the verified artifacts below:

```text
spec/ir.json
spec/states.json
spec/cityos_module_plan.json
```

### No recording or trace can be selected

Confirm the source URL is reachable and that the required source key is valid.
For Smart Room questions, provide a date. For CARLA, set `SIMULATION_API_URL`
or provide the trace-server URL in TeLLMe.

### Where to find logs

```text
.tracefix-ui/logs/runner.out.log
.tracefix-ui/logs/runner.err.log
.tracefix-ui/logs/unified-ui.out.log
.tracefix-ui/logs/unified-ui.err.log
```

## Security notes

- Never commit `.env`, `.env.local`, API keys, or raw private captures.
- Rotate a key if it has been pasted into a chat, issue, pull request, or log.
- Restrict LAN mode to trusted networks; it exposes the local UI to devices on
  that network.
- Review generated code and run artifacts before deployment or publication.
