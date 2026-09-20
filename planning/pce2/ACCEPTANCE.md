# Public-journey acceptance

The machine-readable authority for these requirements is [machine/acceptance.json](machine/acceptance.json). These are scenario groups, not additional Todo tasks. They have **not** been executed by this package.

Use actual public tool schemas and the real kernel/transport in disposable repositories. Reuse existing tests and fixtures, including `test_workflow_lane_resume.py`, rather than rebuilding a harness. Controlled process/clock fakes are appropriate for edge cases; demonstrate grant transfer across a real subprocess boundary. No paid model or GPU is required for the deterministic acceptance suite.

Record executed commands, source/runtime identities, results, actual protocol calls and whether any originator intervention represented a genuine decision. Byte/call targets must not suppress essential constraints or weaken authorization.

## J01 — Entry gives enough context in the right execution

**Owner:** `SK-PCE2-OPERATE`

In a prepared ordinary fixture, one next_task response supplies objective, the planner's mechanical direction/rationale or references, required constraints, acceptance, exact workspace/focus and relevant input availability. The agent can begin work without a compulsory inspect/sync call. Explicit run/task focus never falls back to older compat-v2; ambiguous focus returns a small choice without a wrong claim. Legacy calls remain supported.

Healthy-fixture target: `{"ordinary_entry_calls_before_work": 1}`.

## J02 — Advertised actions are actually permitted

**Owner:** `SK-PCE2-OPERATE`

Enumerate normal roles and scoped maintenance grants. Derive tool actions/schema examples from shared definitions; every advertised executable action passes authorization and schema validation at the same observed state. Deliberately test coordinator/integrator delegation. Do not solve mismatch by granting all role operations. Blocking errors retain a concise cause, needed decision and callable next step; ordinary ready work may have no next protocol call.

## J03 — All views explain real blockers

**Owner:** `PC-PCE2-SURFACE`

Satisfied prerequisites are not immediate blockers. Distinguish recovery attention, claims, quarantine, true dependencies, resource wait and missing authority. Frontier, inspection, maintenance and claim assessment agree at one authority observation. Targeted context works without a full-history export; multiple current runs and partial providers remain explicit.

## J04 — Context is bounded without losing a committed result

**Owner:** `SK-PCE2-OPERATE`

Wire existing known-manifest/cursor fields to useful deltas. Budget the full transport envelope including mandatory scope/constraints, not only the inner context. Large optional history becomes references. Oversized essential context returns needs_context with a retrievable receipt and no permission to proceed without it; a successful claim is not misreported as absent because rendering failed. Read-only observer expansion stays read-only.

## J05 — Delegate the intended work, not an arbitrary file

**Owner:** `SK-PCE2-COLLABORATE`

Explicit authorized source targets reach the delegate unchanged; omission uses declared task/sub-outcome context, never first-file guessing. Carry actual parent constraints, relevant references/interfaces and candidate acceptance. Read scope may equal the bounded parent read scope. A write scope may equal a parent scope only under enforced isolated-candidate or single-writer handoff; reject conflicting writes. A one-file task does not need a dummy path to become delegable.

## J06 — Executor type and launch status are honest

**Owner:** `PC-PCE2-RUNTIME`

Test configured and unavailable adapters. A local code child is not advertised as a tool-capable maintenance agent. An available tool-capable launcher receives an exact assignment and grant and acknowledges a real invocation. With no launcher, return launch_required and a complete handoff rather than delegated/running. Do not start a model to discover whether the adapter is configured.

## J07 — Delegation can fail, wait and be collected economically

**Owner:** `SK-PCE2-COLLABORATE`

Inject failure after child authorization and during launch/collection/cancellation. Repeated calls do not create new children or candidate mutations unnecessarily. Preserve uncertain live work. Running/candidate/failed/unavailable are intelligible; collection gives a bounded waiting hint or other useful work, not an immediate polling ritual. Correct tool annotations when collecting persists authoritative candidate state.

## J08 — Maintenance mandate is delegable, bounded and independent of the target claim

**Owner:** `SK-PCE2-MAINTAIN`

In separate disposable processes, bind a cheaper operator principal to a trusted scoped mandate for a named execution. It can inspect and execute approved recovery across the permitted stages without repeated originator approval, even when the target claim is expired. Check out-of-scope target, wrong principal, expired/revoked grant, replayed proposal, unauthorized redelegation and run/source-generation changes. Observer and ungranted agents cannot mutate. A maintenance job cannot grant itself broader authority.

Healthy-fixture target: `{"originator_interventions_in_authorized_no_conflict_job_max": 2}`.

## J09 — Routine preparation and recovery reach a usable postcondition

**Owner:** `SK-PCE2-MAINTAIN`

Given a configured existing authority and a safe predeclared workspace intent, ordinary entry prepares routine scaffolding using existing canonical workspace operations. Clean stale ownership is reconciled under an in-scope maintenance mandate; actual next_task then succeeds with correct source/workspace. No TTY simulation, row editing, entire-plan replay or fingerprint assembly by the model. Include already-current no-op and previously broken writable/readonly coordinator fixtures.

Healthy-fixture target: `{"clean_resume_including_following_claim_calls_max": 2, "routine_maintenance_calls": 1}`.

## J10 — Dirty work is preserved and adoption is a real choice

**Owner:** `SK-PCE2-MAINTAIN`

Preserve tracked and untracked dirty source, evidence and workspace identity. Do not auto-commit/reset/clean. If exact adoption is not already authorized, return a clear adoption_required decision; after approval of exact retained content, safely bind a new writer and invalidate affected qualification. Changed content invalidates that approval. Git failure is unknown, not clean; projection-only dirt does not require source commits. Metadata-only retirement may retain stopped dirty work without marking it accepted.

## J11 — Liveness and host cleanup use proof, not expired timestamps alone

**Owner:** `SK-PCE2-MAINTAIN`

Test live processes with expired heartbeats, PID reuse, denied probes, surviving workload children, unaccepted results, active peer-project work and local-model reservations. Release only provably stopped/fenced exact ownership through the existing host/sidecar lifecycle, with repeatable reacquisition. Inject failures around admission, activation and marker/sidecar cleanup. Keep unknown work protected and expose owner-specific remaining actions, not a new scheduler.

## J12 — Supersession covers exactly the intended generation

**Owner:** `SK-PCE2-MAINTAIN`

Account for every unfinished source-run member before cancelling that run; partial task selection cannot cancel the remainder. Preserve completed outcomes, source, evidence and foreign/shared memberships. Check external consumers, queued artifacts, capabilities, aggregate state and current interface/message applicability. Bind the explicit successor and preserve unrelated programs. The model supplies intent/target, not a raw membership manifest.

Healthy-fixture target: `{"new_consequential_choice_preview_and_apply_calls_max": 2}`.

## J13 — Invalidation and retirement are not history erasure

**Owner:** `SK-PCE2-MAINTAIN`

Invalidating current applicability stops only the selected current consumption and records reason/provenance without falsifying prior successful results. Retirement without a successor remains different from supersession. No destructive cleanup or numerical-success bypass is included in a maintenance grant.

## J14 — Fresh validation and same-effect refresh do not become a workaround loop

**Owner:** `SK-PCE2-MAINTAIN`

Recheck rows plus membership/absence/conflict facts under the canonical transaction. Concurrent conflicting transitions cannot both succeed. Own routine recovery and unrelated heartbeat/source changes may be re-evaluated internally only when the same authorized semantic effect remains safe; reviewed membership/impact or a bound source-adoption change returns a new decision. Never bypass stale checks or indefinitely retry. Keep the existing conservative compare-and-set where it is adequate.

## J15 — Lost responses and post-commit failures have one effect

**Owner:** `SK-PCE2-MAINTAIN`

Replay/status after a lost response returns the original canonical result, including when the old task capability is no longer live. A committed ledger change with failed derived projection refresh reports committed_projection_pending; retry only that refresh. Git/host stages expose their own committed/pending outcomes and are not claimed globally atomic. Preview does not initialize/migrate/restore authorities.

## J16 — A small amendment stays small

**Owner:** `SK-PCE2-MAINTAIN`

Change one scope, gate binding or declared execution dependency/queue field after historical claims. Preserve omitted fields, unrelated tasks and successful evidence. Identical amendment is a no-op. Resolve or refuse actual live write conflicts; historical claim existence alone is not a refusal. A correction cannot silently weaken acceptance or remap external consumers without semantic authorization.

## J17 — Useful source can be delivered before final qualification

**Owner:** `SK-PCE2-COLLABORATE`

A producer publishes an immutable source delivery while unfinished, and a sibling uses an authorized pinned import. Track producer/base/content, consumer and unqualified state automatically. Qualified-completion dependencies remain unsatisfied. New producer source creates a new delivery. Interrupted import is resumable without a handwritten parallel provenance ledger.

Healthy-fixture target: `{"consumer_import_calls_max": 1, "producer_publish_calls_max": 1}`.

## J18 — Validation and integration have unsurprising effects

**Owner:** `SK-PCE2-COLLABORATE`

Normal validation is explicit about whether it can change source; provide a gates-only path and an explicit integration path. Preserve a declared sealed batch's semantics, while serial dependency delivery does not wait for unrelated producers. Compatibility behavior of old combined calls is explicit and tested. Mode/input requirements are visible before execution, not found by trying incompatible helpers.

## J19 — Evidence is reused correctly, not repeatedly paid for

**Owner:** `SK-PCE2-OPERATE`

On unchanged valid acceptance inputs, validate followed by complete (including integration finalization) does not repeat the same required gate merely due to protocol sequence. Change each relevant source, command, toolchain/environment/dependency or gate policy input and require revalidation. Gates lacking a sound reusable input contract, time-sensitive observations and GPU admission/quiescence are rechecked. No passed-flag-only shortcut; parent acceptance of child results remains distinct.

## J20 — One runtime route and actionable missing-authority diagnostics

**Owner:** `PC-PCE2-RUNTIME`

Use one pinned paired runtime for workflow, preview, read and maintenance. Deliberate package/interpreter/root disagreement produces consistent diagnosis and a supported launch/config action. No hot import rebinding, blind database replacement, arbitrary restore or automatic model install. Missing target authority prevents import and is not reported as an empty project.

## J21 — A capable agent follows the public surface, not source workarounds

**Owner:** `PC-PCE2-QUALIFY`

Run representative entry→work→validate→finish, delegate→collect, and grant→maintain→resume transcripts against real kernel plus transport in disposable repositories. Call only published schemas and returned references, not private helpers to complete a journey. Measure calls, supplied fields, root interventions, visible bytes and gate executions. Include a bounded independent review of authorization/liveness/transaction changes. Tests need no paid model or GPU except an explicitly authorized optional production smoke.

## J22 — Adaptive planning and schemas do not add a new bureaucracy

**Owner:** `PC-PCE2-SURFACE`

Planner packets preserve mechanical direction, rationale, durable outcomes, constraints and acceptance without prescribing every command. Normal reads need no extra durable task. Optional branches become execution work only when selected. Deliberate invalid/parent-cyclic plan input reports a specific validator error. Existing tool names and supported old calls are covered; new operation payload shapes are visible and match dispatch.

## J23 — Qualified paired release with honest cost and paused donors

**Owner:** `PC-PCE2-QUALIFY`

Record exact commits/runtime, real scenario evidence, usage across all agents and metering gaps. Retain prior relevant PCE1 expenditure in the shared cap. Do not call untested/incomplete work qualified to meet the cap. Cellerator/GlassHelix remain read-only regression witnesses and NF1A paused; report unknown readiness where authority is unavailable. Deployment activation needs the existing host-owner authorization.

## J24 — Old PCE1 work is reused, not duplicated or falsely retired

**Owner:** `PC-PCE2-QUALIFY`

Identify whether PCE1 is unimported, imported, partially implemented or present in another candidate. Reuse compatible changes/tests. Import PCE2 only into each intended authority after fresh validation; do not overwrite IDs, blanket-retire old work, or import the PC plan into Skills as a workaround. Where old planning records need supersession, expose the exact supported authorized transition separately from this read-only design delivery.

## Retained PCE1 acceptance

Every PCE1 scenario is mapped in [machine/pce1-mapping.json](machine/pce1-mapping.json). Root-only execution and universal two-call ceremony are deliberately replaced; preservation, correctness, replay, liveness, identity and paused-donor guarantees remain.
