# Tool contracts

Tool schema version 2 exposes exactly fourteen tools and no resources, prompts,
sampling, elicitation, UI, arbitrary file access, shell, or write operations.
The original eight version-1 names, accepted calls, defaults, and meanings are
frozen and remain valid:

1. `project_overview` synthesizes the current program, work, blockers,
   architecture/validation/performance attention, material completion,
   filtered historical counts, cross-authority warnings, and judgment-oriented
   recommended focus. Additive workflow output covers the active run/lane tree,
   typed blockers, rendezvous, workspace/patch/integration state, recovery
   attention, and context cursors.
2. `project_delta` accepts a cursor, todo revision, task, checkpoint, interface,
   commit, or time anchor. Todo resolves semantic anchors and coalesces its full
   interval before budgeting; Git paths are grouped by task scopes and stable
   path prefixes before presentation.
3. `project_frontier` reports semantic-lifecycle-eligible ready work, active
   claims, blockers, path-prefix/lock/interface/checkpoint contention, and safe
   parallel groups. Terminal and superseded work is excluded. Critical-path and
   local-worker suitability remain explicitly labeled heuristics. When workflow
   semantics are available, serial queues and safe groups come directly from
   todo's normalized read.
4. `inspect` examines one bounded task, contract, source symbol, path, decision,
   dependency, or subsystem through registered roots and the transient project
   graph. Architectural concepts resolve across task names/objectives,
   interfaces, registered artifacts, paths, tests, and performance links before
   source fallback. The existing `subsystem` kind also resolves run, lane,
   dispatch, message, rendezvous, workspace, patch, integration, and subordinate
   child identities without changing the public input schema.
5. `evidence` resolves its subject first, then synthesizes relevant current
   support, contradiction, stale/historical evidence, unmeasured assumptions,
   freshness-sensitive confidence, and bounded provenance.
6. `plan_preview` returns objective-resolved planning context, validates/diffs
   an app-private proposal without applying it, adds a conservative prospective
   impact section, or packages a prospective Codex handoff.
7. `agent_status` reports only observable sessions, claims, children, results,
   and existing local-service state. It separates authoritative first-class
   Codex lane dispatches, claim-only observations, and subordinate local-worker
   children; local children are never peer run participants.
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

The exact eight original tool names and their version-1 calls remain frozen;
their schemas receive additive optional fields only. If `todo semantic workflow` is unavailable,
responses carry `todo_workflow_semantic_unavailable`; project-control does not
query raw workflow tables or invoke recovery.

The six version-2 tools are:

9. `architecture_context` accepts a workspace, architectural question, optional
   repository/worktree, compact/standard/expanded detail, current/reference/all
   scope, inclusion categories, item bound, and continuation cursor. It returns
   several thematic seed clusters, attributed graph expansion, commitments,
   boundaries, decisions, interfaces/consumers, realization, tests/evidence,
   active coordination, assumptions, contradictions, risks, next inspections,
   retrieval basis, provenance, and observation preconditions. Serialized data
   budgets are 16/48/128 KiB.
10. `coordination_view` accepts optional run/lane/task filters, since revision,
   detail, resolved-message and historical-arrival flags, item bound, and
   continuation. It reports authoritative runs, lane hierarchy/roles/queues,
   observable dispatches, stable worktree identities, fragment manifests,
   messages/answers/references, decisions, interfaces, rendezvous/arrivals,
   workspaces, patches, integration/conflicts, recovery, safe parallel groups,
   and subordinate children separately. Expanded data is bounded to 96 KiB and
   reads never update receipts or cursors.
11. `source_context` accepts one registered repository, optional stable worktree
   ID, one to 32 structured path/symbol/subsystem/text targets, working-tree/HEAD
   or explicit-commit selector, intent, requested relations, detail, explicit
   byte budget, and continuation. It supports bounded line ranges, including
   ranges within files larger than 2 MiB, with pre/post source identity and one
   retry or `racy_source_read`. Maximum data is 128 KiB.
12. `history_trace` accepts a subject, at most one starting revision/time/task/
   checkpoint/interface/commit anchor, optional ending revision/commit, detail,
   event bound, and continuation. It coalesces administrative noise and labels
   supported causation separately from temporal adjacency or inference. Maximum
   data is 96 KiB.
13. `impact_preview` accepts a hypothesis, optional inert structured change set,
   optional target entities, detail, item bound, and proposal-envelope flag. It
   separates proven, possible, unknown, stale-context, integration, performance,
   and unaffected impacts. It never applies or stores a proposal. Maximum data
   is 96 KiB.
14. `program_context` accepts exactly one configured program ID or explicit list
   of up to 16 registered workspaces, plus question, detail, item bound, and
   continuation. Program membership is query grouping only and never implies
   dependency, ownership, or architectural authority. Each project retains its
   own observation time and skew; no global transaction is claimed. Maximum data
   is 160 KiB.

All fourteen tools carry `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`.

## Component authority and provenance

High-level responses expose an authority map whose members independently report
`available`, `unavailable`, `partial`, or `raced`; operation; revision;
authority fingerprint; project UUID; observation time; stable source identity;
bounded error code; and revision skew. A valid workflow observation is retained
when status/export fails or races. A valid Git observation remains useful when
todo is unavailable. Cross-project and cross-component reads are never labeled
atomic unless their actual authorities match.

Normal MCP output never includes database/state/worktree/model paths, service
endpoints, process command lines, environment values, raw logs, transcripts, or
secrets. Local-only `project-control doctor --json` may show resolved executable
and filesystem paths so provider selection can be diagnosed.

Every new high-level tool returns `ObservationPreconditions`. An optional inert
`ProposalEnvelope` has proposal version, intent, structured proposed change,
those preconditions, deterministic digest, creation time, and
`authority_to_apply=false`. No read-server path accepts it for application.

Continuation cursors are opaque, bounded, tied to the request and observation
identities, and do not create server-side authority. Ranking and section budgets
precede continuation; critical safety and provenance state is never silently
removed.

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
