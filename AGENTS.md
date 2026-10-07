# Project Control repository guidance

## Protect work and state

- Preserve uncommitted edits, Todo ledgers, project UUIDs, claims, queued work,
  snapshots, worktrees, and active tasks. Do not reset, clean, force-push, or
  hand-edit SQLite state or generated Markdown projections without explicit
  authorization.
- Read the current task sheet and its prerequisite contracts for an assigned
  workflow task. Retrieve only the context needed for that task; do not load an
  entire program when a bounded task view is available.
- Registration does not initialize Todo state. Use supported workflow and
  reconciliation operations for semantic workflow changes.

## Develop and test

- Use `scripts/pc-dev` for checkout setup, source execution, and tests. Run
  `scripts/pc-dev setup` for a new checkout or dependency changes. Source edits
  need the relevant process restarted; they do not need a wheel build or manual
  receiver-manifest refresh.
- Source tests must be runnable without a live Project Control service,
  inference/model process, GPU, or assigned workflow task unless the particular
  test explicitly qualifies that layer. Prefer a focused pytest selector for a
  code change; use the default smoke for core source changes. Collection-only
  checks diagnose imports and discovery but do not execute assertions.
- For a bug, reproduce it with a focused test or minimal fixture, add a
  regression assertion, run the impacted tests promptly, then expand to nearby
  shared-boundary tests based on risk. Do not require a broad rebuild or suite
  before every small source change.
- `scripts/pc-dev test tests` runs the expanded collected suite, which can
  include legacy tests requiring host facilities; it is not guaranteed to be a
  hermetic CPU suite. A large test count, collection success, or smoke pass is
  not comprehensive product acceptance. State the selection and result, then
  list untested live MCP, inference/model, GPU/CUDA LAB, recovery, or
  packaged-release layers where relevant. Do not run GPU/model or broad service
  qualification merely because it exists.
- Do not turn a failure into a new skip/xfail or weaken an assertion just to
  obtain a green result. A test with a real environment prerequisite may use
  an explicit skip/xfail when the reason and scope are reported.
- Follow [development](docs/development.md) and
  [deployment](docs/deployment.md) for durable commands and verification
  boundaries. For current assistance qualification, follow
  [the source qualification guide](docs/pa1/source-qualification.md). Keep
  operational configuration and data paths explicit.

## Delegation and ownership

- The controller may delegate bounded research, coding, tests, and review to
  configured Codex subagents using only the named profiles `scout`,
  `researcher`, `implementer`, `reviewer`, or `parallel-head`. Do not use the
  Project Control observer/local service as a coding worker.
- State each worker's owned files, task boundary, and return evidence. Ordinary
  workers do not recursively delegate. A parallel head is for a substantial,
  independent workstream only; it does not own cross-workstream decisions or
  final acceptance.
- Keep architecture, scope changes, integration conflicts, recovery, and final
  acceptance in the root controller. Do not silently promote a worker or use a
  more expensive role without an observable escalation trigger.
- Treat shared-workspace changes from other agents as concurrent work: inspect
  and preserve them, and adjust rather than reverting unrelated edits.

## Runtime boundaries

- Project Control and bundled Todo executable code are verified independently
  of optional domain documentation. `PROJECT_CONTROL_OBSERVER_SKILLS_ROOT` is
  the preferred content root; `PROJECT_CONTROL_SKILLS_ROOT` is a content-only
  compatibility alias.
- Preserve strict packaged-code checks for frozen candidates. Source-mode
  changes should be developed and tested directly from this checkout using the
  launcher.
- Inference is demand-driven. Service readiness, actual MCP behavior, model
  quality, GPU/CUDA LAB behavior, recovery, and frozen-candidate qualification
  are distinct evidence layers. Verify only the layers relevant to the task and
  report their boundaries plainly.
