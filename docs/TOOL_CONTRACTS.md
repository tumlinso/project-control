# Tool contracts

Version 1 exposes exactly these eight tools and no resources, prompts, sampling,
elicitation, UI, arbitrary file access, shell, or write operations.

1. `project_overview` synthesizes the current program, work, blockers,
   architecture/validation/performance attention, material completion,
   filtered historical counts, cross-authority warnings, and judgment-oriented
   recommended focus.
2. `project_delta` accepts a cursor, todo revision, task, checkpoint, interface,
   commit, or time anchor. Todo resolves semantic anchors and coalesces its full
   interval before budgeting; Git paths are grouped by task scopes and stable
   path prefixes before presentation.
3. `project_frontier` reports semantic-lifecycle-eligible ready work, active
   claims, blockers, path-prefix/lock/interface/checkpoint contention, and safe
   parallel groups. Terminal and superseded work is excluded. Critical-path and
   local-worker suitability remain explicitly labeled heuristics.
4. `inspect` examines one bounded task, contract, source symbol, path, decision,
   dependency, or subsystem through registered roots and the transient project
   graph. Architectural concepts resolve across task names/objectives,
   interfaces, registered artifacts, paths, tests, and performance links before
   source fallback.
5. `evidence` resolves its subject first, then synthesizes relevant current
   support, contradiction, stale/historical evidence, unmeasured assumptions,
   freshness-sensitive confidence, and bounded provenance.
6. `plan_preview` returns objective-resolved planning context, validates/diffs
   an app-private proposal without applying it, adds a conservative prospective
   impact section, or packages a prospective Codex handoff.
7. `agent_status` reports only observable sessions, claims, children, results,
   and existing local-service state.
8. `performance_status` projects raw campaign state into active-watch, current,
   reference, historical, superseded, or noncomparable lifecycle; prioritizes
   current-compatible measurements and registered architecture evidence; and
   keeps historical campaign dumps to expanded detail. The only committed
   architecture-evidence parser in this pass recognizes the observed stable
   `CE-ARCH-92-SUMMARY/1` schema from an explicitly registered todo artifact.

Every tool is annotated `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`. Inputs use a registered
workspace ID. Results share a schema-versioned envelope with status, observed
todo revision, repository commits and dirty states, synthesized data, warnings,
and a reusable explicit cursor. The todo revision is nullable when authority is
unavailable; null is not revision zero. Every emitted cursor is legal input to
`project_delta`, including nullable revisions and optional working-tree
fingerprints.

For compatibility, cursors retain the original `fingerprints` map and also
emit the explicit `working_tree_fingerprints` name. Each repository identity
emits `working_tree_fingerprint`; this hashes filtered working-tree status only.

Evidence support is always relevant to the requested subject. Repository
identity is provenance for source evidence, not affirmative support for an
arbitrary claim. Provider failures degrade only tools and evidence kinds that
depend on that provider; unrelated healthy reads do not inherit their warnings.

Budgets are enforced on serialized UTF-8 output. Results use deterministic
ordering, stable identifiers, deduplication, truncation metadata, freshness,
confidence, caveats, and secret redaction. Raw tokens, command lines, database
paths, GPU UUIDs, service endpoints, environment variables, logs, profiler
exports, and worker transcripts are excluded from normal output.

Ranking precedes truncation. Truncated results report the byte budget, items
considered and returned, and historical items omitted. Historical invalid gates,
superseded tasks, claim pulses, and armed archival campaigns do not consume
normal current-attention budgets.

`plan_preview` is prospective only. It never calls plan apply, never stores a
proposal in a registered project, and never implies that validation changed
todo or Git state.

The MCP transport is stateless Streamable HTTP with JSON responses at `/mcp`.
The server publishes no MCP resources or prompts. Operational liveness,
readiness, and immutable release identity are available outside the MCP tool
surface at `/healthz`, `/readyz`, and `/version`.
