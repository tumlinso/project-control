# Implementation bootstrap

## Prompt to the implementing agent

Implement **PCE2**, replacing the earlier PCE1 design rather than layering another program over it. The goal is efficient semantic guidance: a caller should state its engineering intent, receive useful context and execute authorized work without reconstructing infrastructure rules.

Read DESIGN.md once, then the current outcome in machine/outcomes.json and its exact evidence references. Reuse relevant existing PCE1 code and tests. Improve the familiar workflow surface; add specialized **delegable** maintenance, not a root-only bureaucracy or new orchestration framework. You own local API choices, test commands, decomposition and economical delegation within the six outcomes.

The shared ceiling is **3 million aggregate model tokens**, including all agents, review, retries, bootstrap/release and relevant PCE1 implementation already incurred. The 1.6–2.4-million range is an allowance, not a guarantee. Use existing telemetry, report gaps and preserve validation capacity. There are no arbitrary delegate-launch or test-pass quotas. Do not weaken acceptance, expand into a new platform, automatically escalate expensive models or restart the budget by renaming a run.

Keep Cellerator and GlassHelix read-only and NF1A paused. End with a qualified paired candidate and a paused readiness handoff. Do not activate deployment or repair live donor state without the existing required authorization.

## Package adoption

Place this package under `planning/pce2` in each of the registered Project Control and Skills source repositories, through the owner's normal allowed workspace process. These are source documents, not a new Todo authority. Keep source, active state directory and frozen runtime identities distinct.

Run the local integrity check:

```sh
python3 planning/pce2/scripts/check_package.py
```

Inspect current source/runtime and whether PCE1 was imported or partly implemented. Compatible work is reused. Unimported PCE1 needs no retirement. If old records require lifecycle changes, use only the supported authorized path, with exact effects; this package does not authorize a blanket prefix sweep. Do not copy databases or import the PC plan into Skills to avoid an unreadable PC target.

Using the existing verified Project Control launcher, validate each native plan against its actual target:

```sh
project-control plan validate --project project-control --file planning/pce2/machine/project-control.todo-plan.json
project-control plan validate --project skills --file planning/pce2/machine/skills.todo-plan.json
```

The launcher name above is the existing CLI entry point, not an instruction to select an ambient unverified interpreter. Run in each repository/package context as appropriate. Both target-specific diffs must be current and acceptable before their respective imports. A shared-validator pass elsewhere establishes only payload/schema validity. If a target cannot be opened, obtain the specific runtime/registration diagnosis; never bootstrap or restore another database as a workaround.

Once actual target validation succeeds and the owner has authorized implementing this package, import through the same canonical plan front door:

```sh
project-control plan apply --project project-control --file planning/pce2/machine/project-control.todo-plan.json
project-control plan apply --project skills --file planning/pce2/machine/skills.todo-plan.json
```

These commands change their respective Todo authorities. They are supplied for the implementing agent; they were not executed by this design review. Do not start both imports as though they form a cross-project transaction.

## Working from the epic

The native plans provide one convenient serial implementer lane per authority. Use explicit PCE2 task/run focus to avoid historical default runs. Aggregate epics are closure records, not coordinator seats, and no child depends on an aggregate.

Two repositories do not require two expensive architects. A single responsible coordinator may sequence the local outcomes and delegate suitable work to cheaper available agents. The lane topology is a bootstrap default, not a hard limit on supported disjoint work. The [cross-authority contracts](machine/cross-authority.json) are verified by producer commit/test evidence before the receiver accepts completion; they are not fabricated foreign local tasks.

Before marking an outcome successful, bind its actual conformance tests through the existing canonical gate mechanism and execute them. The bootstrap does not invent gate paths for tests that do not exist yet. `no_change_required` needs executed proof that the outcome is already satisfied. Use a consolidated test binding for an outcome rather than a new task for every test. Retain actual commands, results and runtime/source identity.

If the existing workflow blocks this infrastructure change, diagnose the exact boundary once and use an already-authorized host/bootstrap route. Do not assume this unimplemented revision is available, simulate a TTY, edit SQLite directly or build successive private runtime workarounds. Report a genuine unavailable capability instead of inventing one.

## Closure

All 24 scenario groups in machine/acceptance.json are requirements, not 24 extra tasks. Existing tests may satisfy several groups. Record public journey measurements and one bounded independent check of mutation/grant/liveness paths. Keep outcome completion, local aggregate completion, paired release qualification and donor readiness distinct.

Final report: implemented/reused changes; exact paired identity; actual scenario evidence and compatibility results; measured caller effort; all-agent usage with coverage limits; remaining blockers; and read-only NF1A readiness. If the authorized allowance cannot cover correctness and validation, checkpoint the useful work and report what remains—do not declare partial work qualified.
