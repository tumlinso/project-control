# Project Control repository guidance

## Product and ownership

Project Control is a deterministic development-context MCP server. Source retrieval does not require Todo, an inference service, or Project-Lab. Project Source and Project Evidence are independent shared libraries; Project-Lab owns scheduled experiments in its separate repository. Read [architecture](docs/architecture.md) and [retrieval recipes](docs/retrieval.md) for current behavior. Retired assistance code and contracts are historical material under `archive/assistance-v0`, outside installed packages and test discovery.

The eight shared retrieval tools are `overview`, `frontier`, `delta`, `search`, `context`, `trace`, `evidence`, and `machine`. Observer adds `read` and `inspect` (10 total). Codex adds `next_task`, `inspect_task`, `coordinate_task`, and `finish_task` (12 total). Mutator additionally exposes `plan`, `amend_project`, and `maintain_execution` (15 total). Use the live schemas. Profiles own presentation; substantive ranges, revisions, relationships, limits and budgets express retrieval intent. Never present lexical candidates as verified structural relationships or assessments as independent proof.

Todo retains workflow verification and acceptance authority. Routine agents use the four established workflow tools; do not expose engine credentials, leases or retry bookkeeping. Source-only projects remain supported.

## Preserve work and runtime state

Preserve unknown edits, project identities, Todo ledgers, claims, queued work, snapshots, worktrees and active tasks. Do not reset, clean, force-push, hand-edit SQLite authorities or generated projections. For an assigned workflow task, read its bounded task sheet and prerequisites before acting.

Use isolated worktrees with private config, state, caches, artifacts and candidate ports. Leave active services, launchers, credentials, installed tools and release pointers untouched unless the task authorizes a cutover. Historical-data readers operate on copies; inspection must not initialize missing resource authorities or resume experiments.

## Develop and verify

Use `scripts/pc-dev setup --foundation-repo /path/to/project-foundations` for setup; optionally supply the compatible pinned Todo wheel for workflow functionality. Follow [development](docs/development.md) and [deployment](docs/deployment.md) for commands and runtime boundaries.

Use a focused regression for a bug, then nearby shared-boundary checks as risk requires. Run `scripts/pc-dev test` for core changes. Collection verifies imports and discovery, not assertions. Broader suites include host-dependent cases; report the actual selection and limitations. Do not weaken assertions or introduce skips merely to get green results.

Default to deterministic planner/scheduler fixtures. Optional real-agent smoke is limited to `gpt-6-luna` at low effort, without escalation. Live scheduler effects, GPU execution, model quality, comprehensive benchmarking, crash campaigns and production release qualification require their own scope. Distinguish source checks, installed packages, MCP transport and physical execution evidence.

## Collaboration

Use configured named Codex subagents for bounded engineering work, never the retired local investigator. Give each worker coherent ownership, relevant context, authority boundaries and required evidence. Preserve concurrent changes. The root retains cross-workstream integration, consequential scope decisions and final acceptance; avoid repetitive review chains or optional refactoring after acceptance.
