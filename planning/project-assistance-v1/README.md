# Project Assistance v1 — PA1

**A practical bootstrap for making Project Control a better engineering assistant, not a larger agent framework.**

Prepared 6 October 2026. This is an inert design and implementation package. Nothing here has been applied to Project Control, Skills, Todo, model services, or the engineering repositories.

## The outcome

Keep Project Control's existing MCP experience. Behind it, let a small local runtime prepare useful context, suspend work instead of occupying a model slot while waiting, and perform explicitly permitted scratch experiments. Give the user a thin conversational CLI for setting goals, directing attention, and turning automatic work off. Make discoveries useful to external agents through the information tools they already use.

The primary success measure is **better human and agent work per unit of attention and compute**. More notes, more agents, more thinking tokens, or more GPU utilization are not success measures.

## Start here

Read [IMPLEMENTER_START.md](IMPLEMENTER_START.md), [intent](docs/01-intent.md), [architecture](docs/03-architecture.md), and [fast development](docs/06-fast-development.md). Use the remaining documents on demand:

- [Research and reuse map](docs/02-research-and-reuse.md): actual source findings, corrections to earlier assumptions, and external research.
- [Knowledge, goals, and attention](docs/04-knowledge-and-attention.md): what to prepare, retain, surface, and ignore.
- [Scratch experiments and authority](docs/05-experiments-and-authority.md): useful execution without unsolicited repository edits.
- [Acceptance and rollout](docs/07-acceptance-and-rollout.md): staged delivery, gates, economics, and rollback.
- [Decisions and scope](docs/08-decisions-and-scope.md): recommended defaults, questions for the user, and explicit deferrals.
- [Outcome briefs](docs/09-outcomes.md): seven Project Control outcomes and two bounded Skills transition outcomes.
- [Worked journeys](docs/10-worked-journeys.md): concrete examples of the intended user and agent experience.

Machine-readable material is in `machine/`. The source ledger is [evidence/sources.json](evidence/sources.json). The small synthetic repository in `fixtures/repository/` supports repeatable evaluation; it is not copied from the user's projects and makes no real GPU-performance claims.

## The implementation shape

Use one existing central inference owner, the existing host resource interlock, and an evolved service-private broker. Add only the continuation/dependency, attention, and derived-knowledge records that current machinery lacks. Preserve Todo as the sole project workflow authority. Begin with a few behavior presets over one runtime; keep permissions independent of role names.

The first useful product is focused change-shadowing and source-backed prepared context. Scratch experiments and narrowly authorized delegation build on that. Do not make a universal autonomous coder, opaque KV-state migration, a vector database, or a new MCP task protocol prerequisites.

## Package and native validation

From this directory, run:

```sh
python scripts/check_package.py
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s fixtures/repository/tests -v
```

The first command checks package integrity, JSON, references, task graphs, and coverage links. It is **not** Todo's validator or a product acceptance test. On a trusted development environment already bound to the canonical Todo package, also run:

```sh
python scripts/check_package.py --native
```

That optional mode imports the installed `todo_orchestrator.plan.validate_plan`; it neither initializes nor applies a ledger. It fails clearly when the canonical package is absent. Do not add an ambient package or relax runtime identity checks to make it pass.

Native schema-3 plans are supplied directly. Do not lower them through the schema-2 preledger compiler, which would discard run/lane structure [S21, S24]. After adoption preflight, validate through the existing front door:

```sh
project-control plan validate --project project-control --file machine/project-control.todo-plan.json
project-control plan validate --project skills --file machine/skills.todo-plan.json
```

Application is an explicit implementing-root action through the corresponding existing `project-control plan apply` operation after reviewing the live diff and active work. No script in this package applies a plan. Select the new run explicitly when other ready runs exist.

Place the immutable bundle at `planning/project-assistance-v1/` in Project Control. Where native Skills scope or evidence publication requires local planning material, place the same bundle there as well; this does not install Project Control runtime source in Skills. Record any authorized package amendments with new hashes. Do not overwrite an existing package or dirty file silently.

Cross-authority ordering is in [machine/cross-authority.json](machine/cross-authority.json). Native plans deliberately contain only same-authority dependencies. The root must verify the listed producer handoffs before starting their consumers; a foreign task name is not a native dependency or receipt.

## Validation status

See [the deliverable validation record](validation/results.json) for checks actually performed.

This package was constructed from non-agentic source inspection and primary-source research. Local package checks and the included synthetic baseline can be run without GPUs. Native Todo validation, source implementation tests, live Qwen evaluations, service cutover, and actual CUDA eviction qualification are separate and are **not claimed complete by this package**.
