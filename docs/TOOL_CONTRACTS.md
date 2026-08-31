# Tool contracts

Project Control exposes three exact profile-specific tool sets. No profile
publishes resources, prompts, sampling, elicitation, UI, arbitrary file access,
or a generic shell.

The **observer** profile exposes exactly 15 tools over loopback Streamable HTTP:
the fourteen rich reads described below plus `terminal_capture`. It registers no
workflow mutation tool. The **codex** profile exposes exactly 20 tools over
stdio: the same fourteen rich reads, excluding `terminal_capture`, plus the six
canonical workflow tools `next_task`, `inspect_task`, `coordinate_task`,
`delegate_task`, `collect_delegation`, and `finish_task`.
The **mutator** profile exposes exactly 21 tools over local stdio: the Codex
20-tool surface plus `apply_plan`. It does not expose `terminal_capture`.

Both registration and invocation are allowlisted. A name hidden from a profile
cannot be invoked directly. Trusted startup configuration selects the profile;
MCP `clientInfo`, user-agent, model claims, annotations, and model-supplied
arguments do not.

Tool schema version 3 remains the observer compatibility authority: its
fourteen rich-read names, accepted calls, defaults, meanings, and input schemas
are frozen, and `terminal_capture` is the additive fifteenth tool. The original
eight version-1 calls remain valid:

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
   child identities. Schema v2 exposes those as explicit additive kinds and adds
   optional line range, worktree, source-selector, continuation, and larger
   explicit-budget inputs while preserving every v1 call.
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

The fourteen v2 query tools are annotated `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, and `openWorldHint=false`.
Inputs use a registered
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

All fourteen v2 tools carry `readOnlyHint=true`, `destructiveHint=false`,
`idempotentHint=true`, and `openWorldHint=false`.

## Additive version-3 terminal capability

15. `terminal_capture` accepts `project` and exactly one of `executable` or
   `session`. A launch may also provide a registered `repository`, literal
   `argv`, repository-relative `cwd`, active-session `label`, `wait_ms`, bounded
   `rows`/`cols`, and `kill_after_capture` (default `true`). Recapture accepts an
   opaque session ID or active unique label; optional rows and columns together
   resize the existing PTY. It never reruns the executable.

`executable` and `cwd` resolve beneath the registered root with symlink and deny
pattern enforcement. No host path, command string, caller environment, URL, or
shell is accepted. Bubblewrap provides a read-only repository, read-only runtime
libraries, isolated HOME/tmp, no network, no inherited secrets, and fail-closed
availability. The child sees a real PTY with `TERM=xterm-256color`; `pyte`
incrementally interprets the VT stream, including split UTF-8 and escape
sequences, cursor/erase/scroll behavior, and the alternate screen.

The result uses the normal envelope and terminal data fields: `operation`,
rendered `screen`, `rows`, `cols`, opaque `session_id`, optional `label`,
`active`, lifecycle `state`, `returncode` when known, wait/capture/elapsed
timing, `stream_limited`, and `screen_truncated`. It never returns stdout/stderr, a raw transcript,
escape stream, command line, environment, PID, or absolute path. The rendered
screen passes through normal secret and private-path redaction.

With `kill_after_capture=true`, Project Control terminates the entire owned
process group, escalates after a short grace interval, closes the PTY, reaps the
child, and removes registry state. With `false`, a live session remains bonded
to the in-memory registry and its emulator state continues across MCP requests.
Natural exit drains the final PTY bytes and retires the label. Bonded sessions
do not survive service restart; shutdown terminates and reaps them.

Bounds are: 30 seconds wait, 5-200 rows, 20-400 columns, 64 arguments and 8 KiB
aggregate argument data, 1 MiB per capture interval, 4 MiB per session, a
512 KiB serialized screen with an explicit truncation flag, four
active sessions per workspace, and eight per service. Same-session captures
serialize; independent sessions may proceed concurrently. Retained sessions
expire after 30 minutes without capture or four hours total; expiry terminates
and reaps the owned group and releases its label. There is no stdin,
keystroke, arbitrary signal, transcript, SSH, environment, or generic process
manager API.

Because launch, retention, and termination change app-private runtime state,
`terminal_capture` honestly carries `readOnlyHint=false`,
`destructiveHint=false`, `idempotentHint=false`, and `openWorldHint=false`.
This does not grant project, Git, todo, workflow, worker, or performance mutation
authority and does not weaken the fourteen query tools.

## Codex workflow protocol

The Codex profile preserves the exact existing names, accepted input schemas,
bounded responses, annotations, statuses, and opaque capability handling of the
canonical Todo `WorkflowProtocol`:

1. `next_task` atomically resumes or claims the current first-class run lane.
2. `inspect_task` returns bounded, scope-aware current-task context.
3. `coordinate_task` performs role- and scope-validated typed coordination.
4. `delegate_task` optionally starts one bounded subordinate local child.
5. `collect_delegation` nonblockingly collects only the returned opaque handle.
6. `finish_task` completes, hands off, blocks, or releases the parent task.

The adapter contains no scheduler, claim, recovery, capability, transaction, or
completion business logic. It calls the in-process canonical protocol, never an
MCP client or MCP subprocess. Normal instructions are cheap-first: begin with
`next_task`, use `inspect_task` and `coordinate_task`, and escalate to rich reads
only when bounded task context cannot answer a source, architecture, history,
impact, performance, or cross-project question.

## Mutator plan application

`apply_plan(project, proposal)` is registered only in the explicitly selected
mutator profile. `proposal` is an inert `ProposalEnvelope` whose
`proposed_change` is a native Todo plan; it accepts no file path, command,
environment, raw SQLite operation, force flag, or stale-recovery option. The
serialized proposal is bounded to 256 KiB.

The handler verifies the proposal digest, rebuilds current Project Control
observation preconditions, rejects any relevant mismatch, validates and diffs
through the verified in-process Todo runtime, and applies at most once through
Todo's own canonical transaction. Empty diffs return a no-op. Todo authority
UUID continuity, coherent revision advance, and expected task presence are
checked after application. Failures are bounded and never trigger automatic
retry.

`apply_plan` is annotated `readOnlyHint=false`, `destructiveHint=false`,
`idempotentHint=false`, and `openWorldHint=false`. Todo native plan application
is an additive/upsert transaction and does not remove omitted tasks, so v1 does
not label the tool destructive; it remains non-idempotent because a successful
apply advances Todo revision and records an event. The local `project-control
plan apply --file ...` command constructs a fresh inert proposal and calls the
same service. `plan_preview` and `project-control plan validate` never mutate.

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
`authority_to_apply=false`. Read profiles do not accept it for application;
only the explicit mutator's `apply_plan` consumes a fresh valid envelope.

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

The observer transport is stateless Streamable HTTP with JSON responses at
`/mcp`; the Codex and mutator transports are stdio. No server publishes MCP resources or
prompts. Operational liveness,
readiness, and immutable release identity are available outside the MCP tool
surface at `/healthz`, `/readyz`, and `/version`.
