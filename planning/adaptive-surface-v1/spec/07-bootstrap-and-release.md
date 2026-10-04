# Bootstrap, existing-work reconciliation, and paired rollout

## What is delivered versus applied

This package is staged design and executable bootstrap material. Preparation used only current read/preview interfaces. No live Todo tasks were claimed, changed or closed; no product code was deployed; no GPU job was started. The new API names are not available until implemented.

Two native schema-v3 plans target `project-control` and `skills`. They are independent authorities. `planning/cross-authority.json` describes producer receipts/consumers; it never places a foreign task ID into a local native dependency. Project Control declares an exclusive integrator lane (CONTRACT → PACKETS → SURFACE → QUALIFY → RELEASE → epic) and two isolated-merge producer lanes (CONTEXT → TRACE → CONTROL and JOBS → SKILL) targeting SURFACE. Producer tasks use the supported `parallel_safe` policy. Skills retains one exclusive lane in the existing SEMANTICS → RUNTIME → GPU → ROUTING → QUALIFY → RELEASE → epic sequence. All original dependencies and scopes remain intact. The aggregate epic does not gate its own children. Its closure is protected by required task-state gates over all local workpackages, not by dependencies back to its children: current Todo cycle validation includes parent links, so those reverse dependencies would be cyclic. Root prepares the exclusive integrator destination and both isolated producers from the same reviewed CONTRACT/PACKETS foundation commit before producer dispatch, and merges both producers at SURFACE before dependent qualification. Native workspace declarations support mode and integration target, not a future base SHA; record and verify that SHA with native preparation at execution time. See `EXECUTION_HANDOFF.md`.

## Current bootstrap interfaces

The observed installed CLI supports:

```text
project-control workspace list
project-control plan validate --project PROJECT --file PLAN
project-control plan apply --project PROJECT --file PLAN
project-control admin prepare-supersession --repo REPO --intent INTENT --recipient PRINCIPAL
```

The current observer `plan_preview(project, mode="validate", proposal=<native plan>)` can non-mutatingly validate/diff a plan during transition. Its presence today is not a reason to retain it in the new observer surface. Plan application belongs to a trusted local bootstrap/mutator environment. The current CLI creates a fresh proposal using the installed authority and applies through the canonical transaction; do not hand-edit state.snapshot.json or Todo Markdown.

The supplied bootstrap script performs package checks, collision-safe staging, native validation and optional additive plan import through those same installed Python services. It reconstructs the apply proposal from the **saved validation preconditions**, not from a new unreviewed snapshot. This closes a validate/apply race that would otherwise allow a newly existing AS1 task to be updated. Runtime receipts live outside the repository so writing a receipt cannot invalidate its own source fingerprint. Use `--runtime-python` to select the actual bound installed environment; do not edit PYTHONPATH to fake a binding. It deliberately does not retire old work or switch the running product automatically. Paths must be explicitly supplied by the local implementing agent from the actual registered checkouts; no guessed host roots are embedded.

## Sequence

1. Read the root mandate, consistency resolutions and relevant workpackage, not the whole corpus by default. Re-observe registered roots, actual runtime bindings, dirty files, current frontiers and active claims in both authorities. Record what changed since the preparation baseline.
2. Run package checks. Stage the package under `planning/adaptive-surface-v1` in both actual checkouts. Existing differing files cause refusal, not overwrite. No symlink traversal or writes elsewhere.
3. Native-validate both staged plans. Examine every would-modify, scope or active-lane conflict. Additive new IDs are intended; a rerun must not reset a partially completed AS1 plan. The script refuses any `would_modify` by default.
4. Apply only the missing additive plan through the pinned current-backend bootstrap bridge when ready. Each apply has a local journal/receipt. If the second authority fails, report partial bootstrap and resume it; do not roll back or delete the first authority's valid work.
5. Reconcile PCE2 and other visible work with `planning/legacy-disposition.json`. Adopt verified implementations; supersede only affected unfinished obligations with preserved success/evidence and supported exact admin machinery. The old source run may have more members by execution time, so never blindly run the supplied informational supersession-intent example.
6. Stop after setup/import until execution is authorized; do not claim, dispatch or prepare workspaces during setup. After authorization, follow the foundation/destination/producer preparation order in `EXECUTION_HANDOFF.md`. Execute outcome tasks through the current coder profile until the new candidate is qualified. Use `next_task`, handle-scoped context, `coordinate_task` and `finish_task`; do not call the new tools before they exist. Bind/run the supplied behavioral gates. Publish compatible producer contracts/receipts as soon as useful rather than waiting for whole-repository final completion.
7. Keep authoritative cross-repository receipt imports explicit. A receiver checks producing project UUID/task/gate/source/contract identity; copying a JSON file alone is not successful work. Revalidate relevant source dependencies, not an unrelated whole-repo change. Do not mark foreign tasks complete in the local authority.
8. Qualify both candidate packages, role schemas/dispatch, persistent state, native skill routing, actual local inference and GPU preemption. Then build the paired release, update the Skills gitlink to the qualified standalone Project Control commit, and refresh the single installed registration/launcher through existing candidate tooling.
9. Probe the live **new** tool names for each intended profile and check forbidden names/modes. Save rollout, comparison and rollback receipts; preserve the previous launcher/environment. A stopped/new client should discover the new surface without duplicated old aliases.
10. Close AS1 only after current executed acceptance evidence covers both authorities and the deployed surface. Summarize remaining unrelated work honestly. NF1A stays paused.

## Existing-work reconciliation

Preparation observed Project Control at commit `d7af16b...`, Todo revision 809, with `PC-PCE2-RUNTIME`, `PC-PCE2-SURFACE`, `PC-PCE2-QUALIFY` and aggregate `PC-PCE2-0000` in `PC-PCE2-RUN-1`. Skills at `7516ebf...`, Todo revision 887, has `SK-PCE2-OPERATE`, `SK-PCE2-MAINTAIN`, `SK-PCE2-COLLABORATE`, aggregate `SK-PCE2-0000`, and historical/current-attention records requiring review. Both worktrees were dirty. These are observations, **not** authorization fingerprints for future mutation.

Map runtime work to AS1 packet/job/runtime/resource outcomes; frontend work to the consolidated surface and native instructions; PCE2 qualification to the new full public journeys; maintenance to the typed mutator/kernel bridge; collaboration's local coder delegation to preserved but temporarily inactive implementation. Do not retire unrelated blocked items simply to obtain an empty frontier. An old failed record later followed by successful work needs authoritative applicability review, not a blanket status rewrite.

Run retirement/supersession only after deriving the current complete affected set, checking live work, preserving dirty workspace handoffs and downstream ownership, and obtaining any required destructive-action permission. Source-preserving routine adoption within this task need not be turned into repeated user rituals. Preserve append-only history and old successful evidence.

## Rollout and rollback

Keep state migrations additive/versioned until rollback compatibility is proven. Packet/job/cache state is separate from Todo authority; switching launchers must not erase accepted questions. Rollback either reads the forward-compatible store or pauses the new dispatcher and leaves durable jobs intact for a supported upgrade. Never “roll back” a committed Todo change by restoring an old database over newer work.

Use isolated candidate environments bound to the correct installed Todo/Skills distribution. Do not route around a stale/ambiguous binding by editing import paths during a request. Startup remains cheap: no GPU reservation/model launch/full corpus scan just to expose tools. Update doctor, packaging, manifest pins, setup guidance, descriptions, schemas, profile tests and native skill instruction routes; a source-only implementation is not a complete release.

Old MCP compatibility, if required, is an explicit trusted compatibility mode with a removal plan. Normal model-facing discovery shows only the new surface. Preserve internal/CLI compatibility needed by current operator grants and inactive delegation state. `temporary_inactive` is a declared feature state, never unimplemented deletion.

## Stop conditions

Stop only the unsafe/unavailable branch, retain progress, and report the concrete unmet prerequisite. Examples: live conflicting source mutation, unapproved destructive action, missing registered root, broken canonical Todo binding, failed required real inference/preemption gate, or an invalid source/evidence identity. Do not substitute hand-edited database/projections, weakened gates, fabricated receipts, or silent scope cuts. Ordinary uncertainty is a task for command/search/investigate/review, not a reason to offload solvable implementation decisions to the user.
