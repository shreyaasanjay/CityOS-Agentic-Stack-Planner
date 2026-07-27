# Canonical Template hardening report

## OpenCode prompt inventory

Inspected the composed designer system prompt (`build_designer_prompt`), its
headless preamble, the embedded `tla-verify-pluscal` skill, procedure execution
prompt, initial design/IR-repair/PlusCal-continuation/prompt-generation kickoff
prompts, and the shared pipeline system and runtime-prompt-generation prompts.

The composed system prompt now replaces the embedded skill's legacy Phase 1
rediscovery instructions and Phase 3 LLM-owned TLC bookkeeping. The headless
preamble no longer says to derive coordination structure. IR repair uses the
canonical extraction and procedure context as its authority. The procedure
prompt prohibits routing changes, aliases, `tlc_passed`, and `summary.json`
writes. Reading runtime-owned `summary.json` downstream remains allowed.

## Built-in Template audit

| Template | Field | Old | Audited value | Evidence/status |
|---|---|---|---|---|
| traffic_signal_coordination | communication_flow | `[]` | `request, grant, enter, exit, release, enqueue, dequeue, complete` | Deterministic merge of Request-Grant, Exclusive Resource Access, and Queue-Based Scheduling mappings; fixed for the declared pattern order. |

All other populated values were retained. They are covered by the built-in
golden metadata test and their protocol builders/tests. The following values
remain unknown because they vary by supported instantiation rather than being
missing fixed facts:

- `fan_in_decision.number_of_agents` and `number_of_channels`: source count and
  fan-in topology are parameterizable.
- `traffic_signal_coordination.number_of_agents` and `number_of_channels`:
  approach/detector/pedestrian participants and directed channels vary.
- `traffic_signal_coordination.agent_roles`: the role set varies with those
  optional participants and cannot be represented as one fixed canonical list.

## Deterministic flow mapping

- Exclusive Resource Access: request, grant, enter, exit, release
- Queue-Based Scheduling: enqueue, dequeue, complete
- Barrier Synchronization: arrive, wait, release
- Request-Grant: request, grant
- Producer-Consumer: produce, send, receive, consume
- Sequential Handoff: work, handoff, receive, continue
- Broadcast: broadcast, receive
- Consensus: propose, vote, decide

Multiple patterns are processed in canonical input order. Steps are appended in
mapping order and repeated steps are retained only at their first occurrence.
Unmapped patterns contribute no invented flow.

## Exact coordination-pattern vocabulary

Subscription; Checkpoint; Cross Validation; Cascade; Backup;
Split-and-Merge; Fair Judgement; Interruption Recovery; Confirmation Loop;
Frozen Resource; Priority Escort; Progressive Disclosure; Observer; Dynamic
Pairing; Token Passing; Opportunity Window; Leader-Follower; Isolate Agent;
Consensus; Task Prioritization; Verification; Checkpoint Recovery; Adaptive
Yield; Courtesy Yield; Role Switching; Replication; Request-Grant;
Producer-Consumer; Barrier Synchronization; Sequential Handoff; Broadcast;
Election; Heartbeat Monitoring; Request-Response; Reservation; Queue-Based
Scheduling; Exclusive Resource Access; Majority Voting; Retry with Timeout;
Publish-Subscribe.

Spelling, spacing, capitalization, punctuation, and hyphenation are exact.
Duplicates are rejected rather than deduplicated.

## Registry visibility

Promotion registers the published Template immediately in the current process.
Restart reconstruction reads canonical `template.json`. Other already-running
processes do not receive notifications; they must call the explicit registry
refresh function. This is the intentional cross-process limitation.
