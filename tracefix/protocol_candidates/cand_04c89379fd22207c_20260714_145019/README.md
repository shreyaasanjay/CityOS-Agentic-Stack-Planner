# Pattern Candidate

This candidate is **not an active protocol template**.
It must be reviewed and promoted manually before use.

The files in this folder were captured from a verified TraceFix run:

- `candidate_metadata.json` — provenance, counts, promotion status
- `normalized_topology.json` — agent/channel/resource structure (names anonymized)
- `source_ir.json` — the original IR from the verified run
- `Protocol.tla` — the PlusCal/TLA+ that passed TLC
- `Protocol.cfg` — TLC configuration (if captured)
- `pipeline_timing_report.json` — timing diagnostics (if captured)

## Promotion checklist

- [ ] Review normalized topology for correctness
- [ ] Verify no task-specific logic is encoded in Protocol.tla
- [ ] Write a new module in `tracefix/protocol_templates/` based on this candidate
- [ ] Add tests in `tracefix/runtime/opencode_adapter/tests/`
- [ ] Update `tracefix/protocol_templates/__init__.py` registry
- [ ] Delete or archive this candidate folder
