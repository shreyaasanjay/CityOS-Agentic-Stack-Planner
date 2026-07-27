# TraceFix TaskSpec Integration Report

## Discovered active path

The official TeLLMe object is `tellme_harness.schemas.TraceFixTaskSpec`. TeLLMe persists it as JSON at `<TeLLMe run_dir>/tracefix_task_spec.json` through `TeLLMeHarness` and `LoggingStore.write_json`. The runner state points at that run directory. `TellMeBridge.tracefix_task_spec_path()` now resolves the official artifact, and the runner passes it to `tracefix.runtime.cli design --task-spec`.

Before this change, the active runner called `TellMeBridge.tracefix_task_text()`, which embedded a compact projection in prose, and `run_design()` passed that string to `extract_coordination_attributes(query)`. The extractor therefore received an intermediary string, not the official TaskSpec object. The active path now loads the official JSON object and supplies it as the extractor's primary input; the task text remains secondary context for backward compatibility.

## Real schema and seven-field derivation

The real `TraceFixTaskSpec` fields are `task_id`, `query_id`, `user_query`, `space_id`, `intent`, `tracefix_reason`, `required_capabilities`, `input_artifacts`, `constraints`, `success_criteria`, `output_contract_name`, `route`, `time_windows`, `required_modalities`, `candidate_harnesses`, `application_goal`, `evidence_plan`, `answer_packet_requirements`, `evidence_card_contract`, `output_contract`, `privacy_policy`, `validation_policy`, `escalation_conditions`, `forbidden_claims`, `allowed_claims`, `reasoning_summary`, `executable`, `reason`, `target_tracefix_path`, `status`, and `caveats`. `constraints` contains `raw_media_allowed`, `allowed_outputs`, `require_evidence_refs`, `max_agents`, `privacy_scope`, and `identity_inference_allowed`.

| Canonical attribute | TaskSpec evidence | Classification | Deterministic cross-check |
|---|---|---|---|
| `coordination_patterns` | `user_query`, `intent`, `tracefix_reason`, capabilities, goal/evidence/validation requirements | Explicit only when a canonical name is present; otherwise bounded inference or `[]` | Not checkable: the schema has no structured canonical-pattern field |
| `number_of_agents` | task description plus `constraints.max_agents` | Count can be explicit/inferred; `null` when unsupported | If both exist, extracted count must not exceed structured `max_agents` |
| `agent_roles` | role wording in task/goal/capability descriptions | Explicit/inferred or `[]` | Not checkable: no structured participants/roles collection exists |
| `communication_flow` | Never accepted from model as an alternative flow | Deterministically expanded from accepted canonical patterns | Not checkable against TaskSpec; validated by the canonical pattern mapping |
| `limitations` | constraints, success criteria, privacy/validation policy, escalation conditions, forbidden claims, caveats | Explicit/inferred or `[]` | Not checkable as exact equality: the real fields have heterogeneous semantics |
| `number_of_resources` | task, input artifacts, goal/evidence descriptions | Explicit/inferred or `null` | Not checkable: no structured coordinated-resource declaration exists |
| `number_of_channels` | task/goal/evidence communication description | Explicit/inferred or `null` | Not checkable: no structured channel declaration exists |

`candidate_harnesses` is not treated as `agent_roles`: it names TeLLMe harness candidates, not coordination participants. Missing TaskSpec information is reported as `not_checkable`, never as a contradiction.

## Extractor prompt before cleanup (complete)

System message:

```text
You are an attribute extractor only. Do not select templates. Do not recommend templates. Do not rank templates. Do not return confidence. Do not decide routing. Return only valid JSON with exactly the required fields.
```

User message template:

```text
Extract coordination attributes from the user query.

Return exactly this JSON shape and no other keys:
{
  "coordination_patterns": [],
  "number_of_agents": null,
  "agent_roles": [],
  "communication_flow": [],
  "limitations": [],
  "number_of_resources": null,
  "number_of_channels": null
}

Rules:
- coordination_patterns may contain multiple values, but only from the controlled vocabulary below.
- Unknown list values must be []. Unknown numeric values must be null.
- Do not invent unsupported information.
- communication_flow describes ordered messages or interaction steps between agents.
- agent_roles contains functional roles, not arbitrary personal names.
- limitations contains explicit restrictions, guarantees, deadlines, failure rules, or forbidden behaviors.
- number_of_channels means explicitly identifiable logical communication channels, not message count.
- No markdown, prose, comments, routing decisions, template IDs, or template metadata.

Controlled coordination pattern vocabulary:
<exact COORDINATION_PATTERNS entries, one per line>

User query:
<raw query>
```

## Extractor prompt after cleanup (complete)

System message:

```text
You are deriving the seven canonical TraceFix coordination attributes from a TeLLMe TaskSpec. The TaskSpec is a read-only authoritative description of the task. Do not modify, rewrite, correct, extend, or invent fields for the TaskSpec. Use explicit TaskSpec information directly, use deterministic implications where clear, and infer only when reasonably supported. The canonical TraceFix Template class is the single source of truth. These attributes are NOT arbitrary JSON; they are instance-variable values consumed directly by the deterministic validator. The validator expects these exact names. Do NOT rename fields, invent fields, or omit fields. If information cannot be determined, return null or [] as appropriate. You are an attribute extractor only. Do not select templates. Do not recommend templates. Do not rank templates. Do not route templates. Do not return confidence. Return only JSON.
```

User message template:

```text
Extract only the canonical coordination attributes. The TaskSpec is primary; the original request is secondary context and must not override explicit TaskSpec information.

Return exactly this JSON shape and no other keys:
<Template.empty_coordination_attributes() serialized as JSON>

Canonical field meanings:
<each Template.COORDINATION_ATTRIBUTE_FIELDS entry and Template.ATTRIBUTE_SEMANTICS value>

Rules:
- coordination_patterns must contain zero or more values chosen only from the exact list below. Copy each value exactly; do not alter spelling, spacing, capitalization, punctuation, or hyphens; do not return duplicates or invent aliases.
- Unknown list values must be []. Unknown numeric values must be null.
- Do not invent unsupported information.
- Return communication_flow as []; TraceFix deterministically expands every mapped canonical pattern after validation.
- agent_roles contains functional roles, not arbitrary personal names.
- limitations contains explicit restrictions, guarantees, deadlines, failure rules, or forbidden behaviors.
- number_of_channels means explicitly identifiable logical communication channels, not message count.
- These fields are consumed directly to determine Exact Reuse, Parameterized Reuse, Partial Recomposition, or Full Generation.
- The deterministic pattern library may complete communication_flow after extraction when a recognized pattern has a fixed sequence.
- No markdown, prose, comments, routing decisions, template IDs, or template metadata.

Controlled coordination pattern vocabulary:
<exact COORDINATION_PATTERNS entries, one per line>

READ-ONLY_TELLME_TASKSPEC_JSON:
<unchanged official TaskSpec JSON, or “(not supplied)” for legacy callers>

SECONDARY_ORIGINAL_REQUEST:
<original request, or “(not supplied)”>

TARGETED_REEVALUATION_FEEDBACK:
<structured contradiction feedback, or “(initial extraction; no correction feedback)”>
```

The angle-bracketed portions are runtime substitutions from the named canonical sources; they are not additional instructions. This is the complete static prompt text.

## Consistency, correction, and artifacts

`taskspec_attribute_validation.py` returns a structured diagnostic with `status`, `contradictions`, `not_checkable`, and `checked_fields`. The only structured relationship the real schema supports is `number_of_agents <= constraints.max_agents`; omission is not a contradiction. Initial extraction is followed by at most `MAX_ATTRIBUTE_CORRECTION_ATTEMPTS = 2` targeted corrections, for three total attempts. Every attempt reuses the same extractor and passes through the existing strict seven-key, type, vocabulary, duplicate, and deterministic-flow validation. Persistent contradiction stops before `DeterministicTemplateEngine` construction.

TraceFix writes `spec/extracted_coordination_attributes.json` as the canonical seven-field object and `spec/attribute_validation_report.json` as separate diagnostic/provenance metadata. TaskSpec is never written. File-backed TaskSpec bytes and in-memory JSON snapshots are checked for immutability.

No artifact reuse was implemented. The current repository has no safe existing relationship covering TaskSpec identity plus compatible extractor/schema/pattern versions. The report records `not_implemented_no_existing_safe_version_relationship`; no cache subsystem was invented.

## OpenCode audit

The five orchestration call sites are: initial selected-procedure execution, PlusCal continuation after scaffold, IR repair, PlusCal continuation after repair, and runtime-prompt generation. All call-site conditions now restrict reuse follow-up calls to `partial_recomposition` or `full_generation`. Exact and parameterized reuse build artifacts through deterministic template builders. In addition, `run_opencode_agent()` rejects `exact_reuse` and `parameterized_reuse` at the driver boundary, protecting against future call-site regressions.

The parameterized builder applies only template-declared deterministic parameters. Fan-in derives evidence-source count from the requested total; traffic-signal coordination derives approach count. If a selected builder cannot satisfy a requested parameterized count, execution fails explicitly and does not fall through to OpenCode.

Partial/full prompts contain the unchanged TaskSpec, accepted seven attributes, selected procedure/template evidence and boundaries, canonical schema, and exact pattern vocabulary. They forbid TaskSpec rewriting, procedure reconsideration, unauthorized attribute changes, invented patterns, and LLM-owned TLC verdicts.

## Protected files

Hashes recorded immediately before integration and again after implementation are identical:

| File | Before SHA-256 | After SHA-256 |
|---|---|---|
| `tracefix/runtime/deterministic_template_engine.py` | `6875B1C3C62D01779E1C123962F5279268AFAE6EF98D97F1B370440D9F737B0F` | `6875B1C3C62D01779E1C123962F5279268AFAE6EF98D97F1B370440D9F737B0F` |
| `tracefix/runtime/procedure_decision.py` | `2372843E228CCCD0BF92A6B85D13B792C8AB498073E40A9154D26E94281A3660` | `2372843E228CCCD0BF92A6B85D13B792C8AB498073E40A9154D26E94281A3660` |

## Per-file justification and verification

| File | Why / exact issue fixed | Architectural preservation | Verification |
|---|---|---|---|
| `tracefix/runner_ui/tellme_bridge.py` | Resolves the official persisted TaskSpec rather than only producing a prose projection | Read-only path lookup; no producer/schema/validator change | TeLLMe bridge and design-command tests |
| `tracefix/runner_ui/server.py` | Passes `--task-spec` on the active TeLLMe handoff | Existing task text remains secondary; no TaskSpec write | design-command handoff test |
| `tracefix/runtime/cli.py` | Accepts and forwards the official TaskSpec path | Optional/backward-compatible argument | compilation and runtime tests |
| `tracefix/runtime/llm_attribute_extractor.py` | Makes TaskSpec primary, keeps request secondary, and provides targeted correction feedback | Uses `Template` field/schema sources; retains strict JSON, vocabulary, duplicate, type, and flow checks | extractor and TaskSpec tests |
| `tracefix/runtime/taskspec_attribute_validation.py` | Adds real-schema consistency diagnostics and bounded same-extractor reevaluation | Separate from protected validator/selector; never mutates TaskSpec or attributes in place | focused consistency/retry tests and pre-validator-stop test |
| `tracefix/runtime/procedure_execution.py` | Removes parameterized reuse’s OpenCode dependency | Uses existing deterministic template builders and validates emitted IR | deterministic execution tests |
| `tracefix/runtime/procedure_prompt.py` | Supplies unchanged TaskSpec and canonical boundaries to partial/full execution | Canonical attributes remain authoritative; procedure fixed; TLC runtime-owned | partial/full prompt tests |
| `tracefix/runtime/opencode_adapter/design.py` | Loads TaskSpec, writes separate artifacts, stops on contradiction, dispatches deterministic reuse, and fixes explicit reuse-failure status precedence | Existing deterministic engine and selector called unchanged; TaskSpec byte/snapshot checks; OpenCode only on generative routes | design routing, immutability, contradiction, and invocation tests |
| `tracefix/runtime/opencode_adapter/driver.py` | Adds a final prohibition against accidental exact/parameterized OpenCode calls | Does not change procedure selection | driver rejection tests |
| `tracefix/runtime/tests/test_taskspec_attribute_validation.py` | Covers real-schema input, primary/secondary context, immutability, exact keys, checkability, retries, and limits | Uses `TraceFixTaskSpec`, not an invented schema | focused test module |
| `tracefix/runtime/tests/test_procedure_execution.py` | Covers deterministic parameterized instantiation and driver prohibition | Exercises existing builders and canonical IR | focused test module |
| `tracefix/runtime/tests/test_procedure_prompt.py` | Covers unchanged TaskSpec and accepted attributes in partial/full prompts | Confirms fixed procedure, schema, vocabulary, and TLC ownership | focused test module |
| `tracefix/runtime/opencode_adapter/tests/test_design.py` | Covers all four routes, invocation behavior, persistent contradiction before validator, and separate audit artifacts | Uses mocks only at external model/OpenCode boundaries | adapter suite |
| `tracefix/runtime/tests/test_tellme_integration.py` | Covers official artifact discovery and active runner CLI handoff | No TeLLMe schema or producer modification | TeLLMe integration tests |
| `tracefix/runtime/tests/test_template_promotion.py` | Adds injected real-TaskSpec lifecycle through extraction, checking, deterministic validation/selection, exact artifacts, simulated successful TLC verdict, and promotion | Promotion still requires explicit runtime-owned `tlc_passed is True` | promotion suite |
| `docs/TASKSPEC_INTEGRATION_REPORT.md` | Records discovery, prompts, mapping, checks, limits, call sites, hashes, and test provenance | Documentation only | manual audit and diff check |

## Integration status and limitation

Automated lifecycle coverage uses the real `TraceFixTaskSpec` Pydantic schema with injected extractor/OpenCode boundaries. The successful TLC verdict in the lifecycle test is simulated; the deterministic promotion gate itself is exercised. A live credentialed TaskSpec-to-model run is reported separately only if usable credentials are present. The main remaining semantic limitation is the real TaskSpec’s lack of structured participant, role, resource, channel, and canonical-pattern fields. Those relationships cannot be deterministically cross-checked without changing TaskSpec, so they are deliberately reported as `not_checkable`.

No supported extractor/provider credential was present during verification, so no live model call was attempted. The final focused integration set passed `87 passed`. The full `tracefix` suite reported `1084 passed, 73 skipped, 2 xfailed, 11 failed`; the 11 failures are the repository baseline categories (three Windows newline assumptions, six tests requiring the absent optional `mcp` package, and two enforcement exception-type expectations). Python compilation and `git diff --check` passed; the latter emitted only the repository's existing LF-to-CRLF checkout warnings.
