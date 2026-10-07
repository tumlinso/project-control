# PA1 implementation and qualification handoff

This page adapts the original PA1 package to the current Project Control
architecture. The package's original `machine/acceptance.json` remains the
historical statement of A01–A40 intent. Current interpretation is in
`docs/pa1/current-acceptance-mapping.md`; current source and live-check
instructions are in `docs/pa1/source-qualification.md`.
Use current Todo authority and the current task sheet for status and permission.
The old package validation results and qualification handoffs are not current
runtime or task-state authority.

## Product goal and operating boundary

Finish PA1 so users can get grounded, useful answers about registered projects
and optional skill content, and can authorize bounded CPU or CUDA LAB work with
inspectable evidence and cleanup. Scope authorization permits only its
displayed work; it does not authorize edits to canonical source. Keep automatic
preparation off. Keep inference warm while eligible and yield resources only
through the established owner/interlock path.

This pass repairs implementation and performs the two minimal authorized
journeys recorded in the active task: one source-grounded answer and one
bounded CPU LAB odd-tail experiment. Stop before broad quality comparisons,
held-out campaigns, resource/latency profiling, real CUDA LAB, comprehensive
crash qualification, or running all A01–A40 cases. Leave final acceptance open
where its evidence is absent. Do not treat the permitted journeys as blanket
acceptance.

## Current architecture and authority

- Project Control and Todo executable code are in the same checkout and Python
  distribution. Import `todo_orchestrator` from the bundled package; do not
  restore the external `skills_dev`/Todo supplier or
  `coding-workflow-mcp` adapter. Preserve the old supplier only as provenance.
- Use `scripts/pc-dev` for setup, source execution, tests, and helper Python.
  Source identity is derived from current source files; process code changes
  require a restart after focused checks, not a wheel rebuild or hand-refreshed
  receiver manifest. Frozen releases keep strict package verification.
- Optional domain documentation is content, not executable identity. The
  preferred content root is `PROJECT_CONTROL_OBSERVER_SKILLS_ROOT`; the older
  `PROJECT_CONTROL_SKILLS_ROOT` name is content-only compatibility. Missing
  CUDA/C++/skill documents do not invalidate PC or bundled Todo startup, core
  workflow reads, or rescue mutation. A provider can report unavailable when
  the specific content it needs is absent.
- Discover the current profile's actual tools and schemas from its live MCP
  `tools/list`. Historical profile counts and schema hashes are not contracts.
  `/readyz` covers valid core application configuration and the bundled
  workflow engine; inference and domain-content availability are separate.
- The mutator's `maintain_execution` rescue path does not require the Todo
  Orchestrator skill. Diagnose, inspect, prepare, then execute only the opaque
  grant returned for the exact task/run. Native owner, principal, retained-work,
  cleanliness, and continuation guards remain authoritative.
- Use configured Codex subagents for bounded coding/research/review when the
  root assigns them. Project Control's local inference runtime is for observer
  assistance; it is not the coding worker. The root owns architecture,
  cross-workstream decisions, task recovery, integration, and acceptance.
- Preserve ledgers, SQLite, claims, queued work, snapshots, worktrees, dirty
  files, project identities, and historical receipts. Do not hand-edit Todo
  state or generated projections. Follow current native task/run procedures.

## Development and verification

Read root `AGENTS.md`, `docs/development.md`, `docs/deployment.md`, the current
bounded task sheet, and its prerequisite contracts. Do not load the full
program when a scoped task view is available.

For each confirmed defect, capture one focused reproduction, add or update its
regression assertion, run the impacted tests, and expand only across the
affected shared boundary. Source tests need no live service, model, GPU, or
Todo assignment. Use the default smoke and collection-only check at integration
as directed by the root; collection is not assertion evidence. Do not run the
expanded suite merely because it exists. Record exact selectors, outcomes,
source identity, and untested layers.

After batching executable changes and focused checks, the root restarts the
paired source processes before live use. Inference stays demand-driven; a
restart is not permission to load the model or use a GPU. For live checks,
preserve request, source, process, authorization, effect, and cleanup receipts
under a fresh private artifact directory outside the checkout. Stop and
reconcile if an outcome is uncertain; never replay an ambiguous effect.

## Acceptance and delivery

Use the current A01–A40 map to decide which old requirements retain their
meaning, which old implementation mechanics have been replaced, and which
cases remain deferred. Preserve `acceptance-results.json`, the original
machine contract, and all prior receipts as historical evidence. New results
must carry current source/runtime provenance and may update only the evidence
authorized by the active task. Never label old fixture results as live model,
GPU, crash, or product evidence.

At this boundary, report repairs, focused checks, the two minimal journey
outcomes, cleanup, remaining blockers, and deferred cases. Do not mark PA1
fully accepted or turn a partial/historical result into a pass. Keep automatic
assistance disabled unless a separate accepted decision explicitly changes it.
