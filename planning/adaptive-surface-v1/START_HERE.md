# Implement Project Control Adaptive Surface v1

You are the root controller. Current setup authorization covers package preparation, live revalidation and additive plan import only. Do not implement, claim, dispatch or prepare workspaces during setup. Preserve active PCE2 and unrelated work. Once execution is authorized, deliver the **complete new surface** and retain native lifecycle, integration and final acceptance. Follow `EXECUTION_HANDOFF.md` for configured Codex worker assignments and same-base workspace preparation. Obtain fresh explicit user permission for genuinely destructive admin effects described in the risk contract.

## Governing objective

Reduce the cost of turning an engineering intention into correct, verified progress. Project Control exists to provide useful context, continuity and safe routine mechanics—not to make agents administer the framework. Keep source/Todo/Skills/CUDA authorities intact and reuse their working internals. Rebuild the public frontend around the specified roles and operations.

Read `EXECUTION_HANDOFF.md`, `README.md`, `spec/00-purpose-and-decisions.md`, `spec/01-tools-and-profiles.md`, and `spec/07-bootstrap-and-release.md` first. Then read only the specs/outcomes relevant to the next work. `WORKPACKAGES.md` and `planning/outcomes.json` are the execution map; `contracts/requirements.json` is the user-requirement ledger. Resolve any later live-source drift without losing those requirements.

**Important correction:** the shared information surface has eight tools. `search` retains all discovery behavior and absorbs exact typed canonical lookup through its cheapest deterministic path; there is no public `find`. Native filesystem discovery remains `find`/`rg`/Git. overview is callable by all roles, never automatically injected. Observer-only adapters are `read` and `skill`; extended output is observer-only. Investigator is command-first with shared compact semantic tools. Mutator may independently plan and investigate. Keep local coder delegation implemented but hidden/temporarily inactive.

## Bootstrap using the installed surface

From the extracted package:

```bash
python3 scripts/bootstrap.py check
python3 scripts/bootstrap.py stage --repo /actual/project-control --project project-control
python3 scripts/bootstrap.py stage --repo /actual/skills --project skills
python3 scripts/bootstrap.py validate --repo /actual/project-control --project project-control
python3 scripts/bootstrap.py validate --repo /actual/skills --project skills
```

`/actual/...` are explicit placeholders; discover registered roots using the installed Project Control environment. Staging writes only the new planning package, rejects different existing content and follows no symlinks. Validation invokes the **same current registered backend as** `project-control plan validate` in the bound runtime Python and saves its receipt outside the repositories. Supply `--runtime-python /actual/bound/environment/bin/python` when this script is not already running in that environment. A nonzero/invalid result is not permission to bypass the kernel.

After inspecting both diffs and the current authority/frontier:

```bash
python3 scripts/bootstrap.py apply --repo /actual/project-control --project project-control --allow-additive-apply
python3 scripts/bootstrap.py apply --repo /actual/skills --project skills --allow-additive-apply
```

These are independent transactions. The helper refuses existing-task updates; it is not a general replanner. If bootstrap was partially performed already, inspect current tasks and use supported plan/maintenance semantics rather than resetting/reapplying the whole plan.

Reconcile overlapping existing PCE2 obligations using `planning/legacy-disposition.json`. No blind run retirement is included in the script. Preserve dirty work, successful outcomes, gate evidence, unrelated tasks and active grants. Keep NF1A paused and user scientific repositories unmodified.

After separate execution authorization, execute through the current coder tools until the replacement is live. Plan import itself authorizes no claims or dispatch. The known first calls are conceptually:

```text
next_task(repo_root=<registered Project Control root>, run_id="PC-AS1-RUN-1", task_id="PC-AS1-CONTRACT")
next_task(repo_root=<registered Skills root>, run_id="SK-AS1-RUN-1", task_id="SK-AS1-SEMANTICS")
```

Skills work consumes the contract producer's reviewed receipt before implementing the cross-authority boundary. Do not have two active writers on overlapping scopes merely because there are two agents. Use economical capable subagents for bounded engineering/review; the hidden local coder-delegation surface is not the required mechanism. Do not hard-code hosted model prices or convert this into a swarm/microtask epic.

## Definition of done

Every default profile has exactly its specified discovery/dispatch surface and mode restrictions; new information tools return navigable locators and reusable packets; investigation/skill jobs persist and drain independently of request/model lifetime; skill authority is exact direct text; impact/history/evidence semantics are correct and freshness-aware; mutator operations reuse canonical transactions; native skill guidance no longer routes coders through observer adapters; host-global observer-model eviction works; legacy state is preserved and scoped; the paired candidate is deployed and live journeys pass.

Required product acceptance tests live under each repository's `tests/as1/` and carry the case IDs in `contracts/acceptance-cases.json`. The included gate runner rejects missing or skipped cases. Package self-tests are not product proof. Publish current source/runtime/contract/gate receipts, not claims of completion.

Finish with the actual commits, registered tool/profile comparison, executed tests and cost/context results, live verification, remaining limitations, preserved old work, and rollback location. Do not claim success while required acceptance is skipped or the old frontend is still the ordinary installed surface.
