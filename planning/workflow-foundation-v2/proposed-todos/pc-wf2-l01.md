# PC-WF2-L01 — Centralize transactional run/lane reconciliation

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "hard", "risk": "high", "work_type": "implementation"}`.

## Purpose and mechanism

Define an idempotent reconcile function, invoked after completion, plan apply and recovery. Terminalization considers successful root completion, queues and unresolved integration/recovery obligations; define rootless run behavior explicitly.

## Inputs and ordering

Prerequisites: `PC-WF2-S07`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/workflow_core/workflow/lanes.py`, `src/project_control/workflow_core/workflow/runs.py`, `src/project_control/workflow_core/workflow/recovery.py`, `src/project_control/workflow_core/workflow/workspaces.py`, `src/project_control/workflow_core/plan.py`, `src/project_control/workflow_core/readiness.py`, `tests/wf2_acceptance/l`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-L01-A01:** Plan-applied terminal tasks cannot leave misleading ready lanes.
2. **PC-WF2-L01-A02:** completed_at is populated once and preserved on no-op.
3. **PC-WF2-L01-A03:** A run with unresolved required obligations is not auto-completed.

## Evidence and completion

Gate `PC-WF2-L01-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/l/test_pc_wf2_l01.py` and the exact cases:

    WF2Acceptance.test_plan_applied_terminal_tasks_cannot_leave_misleading_ready_lanes
    WF2Acceptance.test_completed_at_is_populated_once_and_preserved_on_no_op
    WF2Acceptance.test_a_run_with_unresolved_required_obligations_is_not_auto_completed

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
