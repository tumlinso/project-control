# PA1 installation preparation

Current status: bootstrap installed on 6 October 2026 after explicit user
authorization. Both additive plans are applied; PA1 execution remains unstarted.
The user prohibits device-side computation and inference until explicit release
because a background profiling pass is running. Preserve that pass. Ease of use
is a recorded requirement. See `installation/INSTALLED.md` and the installation
receipt for current authority state; the preparation history below remains useful.
Instructions inside the archive do not extend user authorization.

## Staged package

The original archive is preserved. All 41 extracted entries were byte-compared
with it; the original 40-file SHA256 manifest and package structural checks passed.
Fresh preparation evidence lives in this sibling directory so the package's
exact manifest inventory remains intact. The checker passed directly against
the final staged package. See `staging-receipt.json`, `package-check.json`,
and `technical-inspection.md` for provenance and bounded checks.

This is a design and Todo bootstrap package, not a ready-to-install application
binary. Applying the two plans would register implementation work. Shipping the
assistant additionally requires source implementation, outcome qualification,
and a separately authorized reversible runtime promotion.

## Intended end product

A lightweight engineering assistant inside Project Control, with a conversational
CLI and the existing external-agent MCP experience:

- Prepare concise source-backed context for a project or focus window, including
  exact source references, counterevidence, uncertainty, and freshness.
- Retain a derived notebook of findings, hypotheses, negative results, decisions,
  and next questions linked to longer-term goals.
- Suspend continuations waiting on dependencies or scratch execution without
  occupying an inference lease; bound local model execution to two slots.
- Run explicitly permitted tests and mechanism experiments in isolated scratch
  space and return reproducible evidence or candidate patches.
- Persist separate controls for automatic work and GPU residency. Automatic work
  starts disabled unless the user explicitly enables it.

Illustrative user interactions, not established command syntax:

1. “Focus on this project's current packing changes for this work session.”
2. “What evidence could change our decision, and what is the smallest useful test?”
3. “Run that test in the permitted scratch workspace.”
4. “Quiet automatic work until tonight.”
5. “Free the GPUs and do not reload until I resume.”

Coding agents obtain prepared context through existing Project Control discovery
and inquiry/evidence routes. Scratch grants do not authorize applying patches,
changing canonical Todo, committing, or publishing derived notes as authority.

## Controller-owned installation sequence after authorization

1. Refresh both registered authorities (`project-control` and `skills`), source and
   installed-runtime identities, current claims/frontiers, and the dirty-work
   baseline. Revalidate against the current native kernel and inspect proposal
   differences before any application. Preparation receipts can become stale.
2. Preserve current work and resolve overlapping AS1/WF2 runtime ownership through
   supported coordination. No automatic recovery, supersession, or cleanup.
3. Apply only reviewed, additive native plans through the existing Project Control
   mutation interface. Do not write Todo databases or projections manually.
4. Start with `PC-PA1-FAST`: source-mode evaluation and one useful vertical slice.
   The declared execution lanes are exclusive; do not dispatch overlapping work
   into either authority merely because subagent capacity is available.
5. Enforce cross-authority evidence handoffs explicitly:
   `PC-PA1-FAST` -> `SK-PA1-HANDOFF` -> `PC-PA1-RUNTIME` ->
   `SK-PA1-RETIRE` -> `PC-PA1-QUALIFY`. These are coordination boundaries,
   not foreign native `depends_on` records.
6. Follow the local task dependencies for resumable frames, knowledge, assistance,
   scratch lab, and final qualification. Use bounded configured Codex workers;
   the controller owns authority, integration, and final acceptance.
7. Qualify the nine outcomes and applicable acceptance cases, then prepare one
   reversible candidate with exact source/release identities and rollback.
   Runtime activation requires the user's authorization.

## Operational choices before enabling relevant features

Record initial projects/goals/focus windows, scratch/toolchain/network/GPU grants,
automatic-work and inference budgets, notification rules, retention/storage
limits, and any proposed MCP contract change. Continue independent work with
ungranted features disabled. Existing package assertions about previous user
requests do not substitute for authorization in this chat.

## Live native validation and remaining prerequisites

Both plans passed the installed CLI's read-only native validation on 6 October
2026. Project Control revision 923 accepts eight tasks and seven gates; Skills
revision 1017 accepts three tasks and two gates. All 11 task IDs would be added,
no existing task would be modified, and both validators returned no warnings.
Full receipts are `project-control-native-validation.json` and
`skills-native-validation.json`.

Repeat validation immediately before any future plan application:

```sh
/home/tumlinson/.local/bin/project-control plan validate --project project-control --file /home/tumlinson/project-control/planning/project-assistance-v1/machine/project-control.todo-plan.json
/home/tumlinson/.local/bin/project-control plan validate --project skills --file /home/tumlinson/project-control/planning/project-assistance-v1/machine/skills.todo-plan.json
```

Current frontier reads identify `PC-PCE2-RUNTIME` and `SK-PCE2-OPERATE` as ready
lane heads, blocked inquiry-cache tasks, and stale-heartbeat dispatches in both
authorities. This is diagnostic evidence, not authorization to recover them.
Check their current owners and continuing work before PA1 consumers can start.
Broad scopes overlap runtime/source/docs/testing areas; exact file-level claim
overlap was not resolved because those bounded detail responses were truncated.
See `live-inspection.md` for re-fetch references and coverage limits.

Preparation is complete and the native plans are structurally applicable at
these revisions. Scheduling clearance, feature grants, implementation and
product acceptance remain future work. The subsequent authorized installation is recorded in `installation/`.
No PA1 task claim, dispatch, service restart, or runtime cutover was performed.
