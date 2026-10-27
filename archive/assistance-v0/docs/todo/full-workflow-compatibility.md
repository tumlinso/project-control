---
name: todo-orchestrator
description: Transactional orchestration for substantial multi-step projects. Use when Codex should create or continue a persistent task graph, atomically claim safe work across parallel chats in one repository/worktree, coordinate checkpoints, barriers, interfaces, ownership scopes, named locks, scarce resources, gates, evidence, handoffs, or migrate legacy todos.md ledgers. A fresh chat should be able to invoke this skill and continue without the user restating project architecture.
---

> Deprecated maintenance reference. For ordinary substantial repository work,
> use the `project-control` Codex profile. Use `project-control admin recover`
> for owner recovery; the older recovery forest below remains compatibility-only.
> The historical `coding-workflow` name and commands documented below are not a
> second ordinary front door or live MCP registration.

# Todo Orchestrator

Within an explicitly authorized legacy maintenance boundary, use the v2 command
line as the coordination interface. SQLite is the live operational source of
truth; `.todo-orchestrator/state.snapshot.json` is durable recovery state;
`todos.md`, `todo-status.md`, and `todos/*.md` are generated human projections
and legacy migration inputs.

Do not use this skill for a narrow one-step request that clearly belongs to another specialized skill.

## Legacy maintenance startup: Continue

Only when the user explicitly requests Todo maintenance and says “Use
`$todo-orchestrator` and continue”:

1. Locate the repository root and read repository `AGENTS.md` if present.
2. Resolve this skill’s `scripts/todo.py` path. Do not assume it is inside the target repository.
3. Run:

   ```bash
   python <skill-dir>/scripts/todo.py bootstrap --repo-root <repo-root> --json
   python <skill-dir>/scripts/todo.py continue --repo-root <repo-root> --json
   ```

4. Use only the returned task capsule for orchestration context. Inspect the task’s relevant source, declared paths, interfaces, and any on-demand capsule sections; do not begin by rereading every Markdown ledger.
5. Proceed with the claimed task without asking the user to choose among safe ready work. Ask only when the graph explicitly requires a human decision or no safe action exists.

`continue` registers a distinct session, reconciles expired claims, computes readiness, atomically selects one task, acquires claim-time locks/resources, and returns claim/session credentials plus a compact context capsule. Preserve the claim token for subsequent commands. Tokens are secrets: do not commit or paste them into ledgers.

If bootstrap created an empty project with no graph, follow “Planning a New Project.” If legacy Markdown exists but has not been migrated, follow “Legacy Migration.”

## During Work

- Treat `task.objective`, `task.next_action`, `scope`, `prerequisites`, `checkpoints`, `gates`, `resources`, `interlocks`, and `active_siblings` in the capsule as binding coordination state.
- Edit only declared exclusive paths. Read-only paths may be inspected but not modified.
- Before editing an uncertain path, run `todo guard --paths ...`.
- Acquire each named shared lock before its critical section. Use `todo exec --lock <name> -- <argv...>` for a short wrapped critical section.
- Use `todo gate run` for validation, tests, benchmarks, and other evidence-bearing checks. Never benchmark on an unleased exclusive resource.
- Run `todo pulse` during long unwrapped work. Authenticated coordination commands also refresh the lease.
- Run `todo changes --since <delta_cursor>` after material pauses and before integration-sensitive work.
- If an interface or checkpoint is invalidated, stop consuming the stale contract and follow the returned recovery state.
- Never reset, overwrite, clean, or attribute shared-worktree changes merely because they are outside the current task. Run `todo audit` and reconcile ownership first.

Useful commands:

```bash
python <skill-dir>/scripts/todo.py context --repo-root <repo> --claim-token <token> --section dependencies --json
python <skill-dir>/scripts/todo.py changes --repo-root <repo> --claim-token <token> --since <revision> --json
python <skill-dir>/scripts/todo.py guard --repo-root <repo> --claim-token <token> --paths <path...> --json
python <skill-dir>/scripts/todo.py audit --repo-root <repo> --json
```

## Checkpoints, Interfaces, and Barriers

A checkpoint is independent of task completion. Reach it only through:

```bash
python <skill-dir>/scripts/todo.py checkpoint reach <checkpoint-id> --repo-root <repo> --claim-token <token> --json
```

The command verifies required gates, records evidence, freezes configured interfaces, reevaluates barriers, and unblocks dependents. Revoke through the CLI; active dependents become `attention_required`.

Freeze or revise an owned interface only through `todo interface freeze|revise`. An explicit revision recalculates contract hashes, emits an event, and marks active consumers `attention_required`.

Barrier state is computed from structured requirements. Do not manually declare a fan-in complete. Use `todo barrier explain <id> --json` when blocked.

## Gates and Resources

Prefer argv arrays in plan gates. Gate commands can declare working directory, environment, timeout, expected exit code, input paths/interfaces, locks, and generic resource selectors.

The scheduler is resource-agnostic. `gpu:any` is one optional selector supported by the NVIDIA inventory provider; other classes may represent CPU benchmark slots, ports, datasets, build directories, or external services. A gate acquires resources immediately before execution, heartbeats while the child runs, sets provider environment such as `CUDA_VISIBLE_DEVICES`, captures evidence, and releases leases in finalization.

If all matching resources are busy, accept the structured unavailable result or use an explicitly bounded `resource acquire --wait <seconds>`. Never bypass allocation with an ad hoc benchmark command.

## Finish, Block, Release, or Hand Off

Use exactly one structured exit path:

```bash
python <skill-dir>/scripts/todo.py complete --repo-root <repo> --claim-token <token> --disposition implemented --json
python <skill-dir>/scripts/todo.py handoff --repo-root <repo> --claim-token <token> --note "<concise note>" --json
python <skill-dir>/scripts/todo.py block --repo-root <repo> --claim-token <token> --reason "<structured reason>" --json
python <skill-dir>/scripts/todo.py release --repo-root <repo> --claim-token <token> --json
```

`complete` refuses to close a task while required gates are missing, failed, or invalidated. Use the disposition allowed by the task policy. `evaluated_not_promoted` is a normal generic outcome for a correctly executed experiment that did not satisfy its promotion threshold.

`handoff` derives changed owned files, diffstat, checkpoint/gate evidence, interfaces, resource history, warnings, and revision. Add only a concise note; do not rewrite project architecture in prose.

## Orphan Recovery

Claims use a configurable lease (default two hours), separate from legacy 3/7-day review freshness. Expiry does not blindly reassign dirty work:

- unchanged owned scopes return safely to ready;
- changed scopes become orphaned/quarantined and `attention_required`;
- demonstrably live local resource processes are not reclaimed solely by time.

Use:

```bash
python <skill-dir>/scripts/todo.py recover inspect <task-id> --repo-root <repo> --json
python <skill-dir>/scripts/todo.py recover adopt <task-id> --repo-root <repo> --json
python <skill-dir>/scripts/todo.py recover release <task-id> --repo-root <repo> --json
```

Inspect before adopting or explicitly acknowledging dirty release. Never discard orphaned files.

## Live Token-Loss Recovery

Choose recovery by claim state and credential availability:

- Current token available: finish through ordinary `complete`, `handoff`,
  `block`, or `release`.
- Expired/orphaned claim: use `recover inspect`, then `recover release` or
  `recover adopt`; do not use a live emergency path.
- Still-live claim with a lost token:
  - an unchanged, verifiably `coding-workflow`-owned claim may use
    `recover live-inspect`, manual `recover live-approve`, and one-use
    `recover live-override` to create a replacement facade-owned claim;
  - an arbitrary owner-controlled claim may use `recover force-release-inspect`,
    manual `recover force-release-approve`, and `recover force-release` to
    retire the claim and return the task to `planned`.

Force-release approval is an owner capability, not an agent option. Its creation
requires interactive TTY stdin and stdout, displays repository/project identity,
revision, fingerprint, owner metadata, lease expiry, reason, and consequences,
then requires the exact task ID. It is short-lived, one-use, repository/project/
task/UID/revision/fingerprint-bound, and passed to consumption only through
`TODO_FORCE_RELEASE_APPROVAL`. Never put the token in argv, logs, ledgers, audit
payloads, or model context.

Force release is deliberately narrower than arbitrary takeover. It refuses
active or acceptance-pending child work, active gate execution,
queued/running/preempted background or CUDA work, and a locally live attached
command process. Changed owned scope is separately gated: the owner must pass
`--acknowledge-dirty` to inspect and approval, which binds the current material
scope fingerprint and dirty-path summary into the permission and audit. Generated
todo projections are excluded because approval refreshes them itself; a later
material file change makes approval stale. Safe claim-owned lock and ordinary resource leases are
released in the same authority transaction; the old claim becomes explicitly
`force_released`, its token stops authenticating, the task returns to `planned`,
the revision advances, projections refresh, all repository files remain intact,
and the reason plus prior claim and scope fingerprints are recorded in audit
history. Project Control cannot mint this approval; after owner release its
Codex profile simply calls ordinary `next_task` again.

## Planning a New Project

For substantial new work, read `references/planning-workflow.md` and create a v2 JSON plan. Use a scaffold when useful:

```bash
python <skill-dir>/scripts/todo.py plan scaffold fanout --output <draft.json> --json
python <skill-dir>/scripts/todo.py plan validate --file <draft.json> --repo-root <repo> --json
python <skill-dir>/scripts/todo.py plan diff --file <draft.json> --repo-root <repo> --json
python <skill-dir>/scripts/todo.py plan apply --file <draft.json> --repo-root <repo> --json
```

The plan should declaratively capture hierarchy, typed prerequisites, checkpoints, barriers, decisions, ownership scopes, interfaces, locks, resource requests, gates, dispositions, and relevant invariants. Do not ask the user to hand-author JSON; Codex creates it from the project request and repository evidence.

Validate and show the semantic diff before applying a substantial update. Plan application is one transaction.

## Legacy Migration

Existing `todos.md`, `todo-status.md`, and `todos/*.md` remain supported. Bootstrap first, then dry-run before apply:

```bash
python <skill-dir>/scripts/todo.py migrate markdown --repo-root <repo> --dry-run --json
python <skill-dir>/scripts/todo.py migrate markdown --repo-root <repo> --apply --json
```

Legacy owner labels are history, not session credentials. Legacy claimed entries become orphaned/attention-required migration records. Preserve unknown user-authored sections. After migration, never use Markdown as authority for claims, readiness, locks, resources, dependencies, or barriers.

Legacy scripts continue to work only before v2 bootstrap. Once `.todo-orchestrator/project.json` exists, compatibility wrappers direct mutations to the unified CLI so two state engines cannot diverge.

Legacy review defaults remain: planned, in-progress, and stale streams use 3 days; blocked streams use 7 days. These review windows are unrelated to claim/resource leases.

## Hard Rules

- Never manually declare a claim or directly edit runtime SQLite state.
- Never treat Markdown pickup status as authoritative in v2.
- Never edit a path owned by another active claim.
- Never assume missing ownership means parallel-safe; the default is conservative.
- Never cross an unopened barrier or ignore a checkpoint/interface invalidation.
- Never modify a shared integration file without its named lock.
- Never use an exclusive resource without a lease.
- Never mark done without valid required gates.
- Never auto-clean project state. Cleanup remains explicit-only.
- Never rely on branch separation for coordination; v2 is designed for one branch and one shared worktree.

## Reference Map

- `references/v2-architecture.md`: state stores, transactions, recovery, and safety model
- `references/project-plan-v2.md`: plan entities and examples
- `references/cli-reference.md`: commands, JSON envelope, and stable exit codes
- `references/planning-workflow.md`: evidence-first decomposition into the v2 graph
- `references/status-and-cleanup.md`: computed state, leases, and explicit cleanup
- `references/todo-format.md`: generated Markdown and legacy compatibility
- `schemas/project-plan-v2.schema.json`: machine-readable plan schema
