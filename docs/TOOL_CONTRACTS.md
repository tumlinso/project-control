# Tool contracts

Version 1 exposes exactly these eight tools and no resources, prompts, sampling,
elicitation, UI, arbitrary file access, shell, or write operations.

1. `project_overview` synthesizes current tasks, blockers, readiness, recent
   outcomes, architectural attention, and recommended focus.
2. `project_delta` compares an explicit caller cursor with current todo and Git
   identities and classifies material changes.
3. `project_frontier` reports ready work, active claims, blockers, contention,
   and dependency-based parallel groups. Critical-path additions are labeled as
   heuristic rather than todo authority.
4. `inspect` examines one bounded task, contract, source symbol, path, decision,
   dependency, or subsystem through registered roots only.
5. `evidence` synthesizes support, contradictions, caveats, confidence, and
   provenance for a bounded subject.
6. `plan_preview` returns planning context, validates/diffs an app-private
   proposal without applying it, or packages a prospective Codex handoff.
7. `agent_status` reports only observable sessions, claims, children, results,
   and existing local-service state.
8. `performance_status` summarizes existing CUDA, benchmark, capacity, and
   contamination evidence without starting or reserving anything.

Every tool is annotated `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`. Inputs use a registered
workspace ID. Results share a schema-versioned envelope with status, observed
todo revision, repository commits and dirty states, synthesized data, warnings,
and a reusable explicit cursor. The todo revision is nullable when authority is
unavailable; null is not revision zero. Every emitted cursor is legal input to
`project_delta`, including nullable revisions and optional working-tree
fingerprints.

Evidence support is always relevant to the requested subject. Repository
identity is provenance for source evidence, not affirmative support for an
arbitrary claim. Provider failures degrade only tools and evidence kinds that
depend on that provider; unrelated healthy reads do not inherit their warnings.

Budgets are enforced on serialized UTF-8 output. Results use deterministic
ordering, stable identifiers, deduplication, truncation metadata, freshness,
confidence, caveats, and secret redaction. Raw tokens, command lines, database
paths, GPU UUIDs, service endpoints, environment variables, logs, profiler
exports, and worker transcripts are excluded from normal output.

`plan_preview` is prospective only. It never calls plan apply, never stores a
proposal in a registered project, and never implies that validation changed
todo or Git state.

The MCP transport is stateless Streamable HTTP with JSON responses at `/mcp`.
The server publishes no MCP resources or prompts. Operational liveness,
readiness, and immutable release identity are available outside the MCP tool
surface at `/healthz`, `/readyz`, and `/version`.
