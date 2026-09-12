# Workflow Foundation v2 — project-control

**Inert manual bootstrap · WF2-20260912-v1 · native schema 3**

This package contains **69 task records in 16 first-class lanes**, including a claimable coordinator and a terminal aggregate epic. It is paired with the other authority's package. No plan has been applied, no production code or user configuration has been changed, and no worker has been launched by this delivery.

The native apply file is **`machine/project-control-workflow-foundation-v2.todo-plan.json`**. `machine/proposed_todos.json` is the richer authoring catalog. CSVs, profiles, acceptance matrices and handoffs are consistency-checked views, not alternative task authorities. Do not use the current generic pre-ledger compiler; it emits schema 2.

## Read first

Read `01_SCOPE_AND_DECISIONS.md`, `09_PARALLELISM_AND_INTEGRATION.md`, `10_VALIDATION_AND_MIGRATION.md`, and `11_MANUAL_BOOTSTRAP.md`. The numbered documents hold the design; `proposed-todos/` and `handoff/` hold task-specific instructions. `index.html` is the offline navigation entry.

## Qualification status

Offline package structure, self-tests and manifests are recorded in `evidence/package_validation.json`. Representative live schema-3 probes passed after correcting the epic-parent cycle. **The full exact delivered native plan still requires fresh on-host native validation/diff before import.** The wrapper enforces that; no passing implementation receipt is shipped. See `evidence/native_plan_validation.json`.

## Key invariants

Absorb Todo as one strongly bounded internal core, not a rewritten duplicate. Establish parity before semantic changes. Keep ctxpp standalone. Preserve existing state locations/UUIDs. Model-independent work profiles remain sidecars until the new schema is qualified. Keep the old live release frozen until verified cutover. Researchers use bounded evidence, not the root's entire transcript.

## First local check

```sh
python3 -B scripts/validate_package.py --peer-package /actual/peer/planning/workflow-foundation-v2
python3 -B scripts/test_package.py
```

These commands check the package, not the live authority. Follow the manual procedure for preview, deliberate import and verification; then stop. A later explicit launch of `handoff/START_CONTROLLER.md` authorizes execution from Project Control.
