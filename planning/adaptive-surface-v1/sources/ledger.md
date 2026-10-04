# Evidence ledger

These are live connected-source observations made while preparing the package, not implemented AS1 capabilities. Source authority remains the actual named repository/working-tree content. Commit/freshness baseline: `observations.json`.

**E01.** `project-control` — `project_frontier` result. Todo revision 809; current PCE2 runtime/surface/qualify lane; no active dispatch shown in that frontier.

**E02.** `skills` — `project_overview` result. Todo revision 887; PCE2 operate/maintain/collaborate; other attention records retained. Both current worktrees dirty.

**E03.** `project-control` — `src/project_control/profiles.py`, lines 25–115. Current observer/codex/mutator policy lists. Investigator currently observer-only; rich read set contains redundant/public execution tools.

**E04.** `project-control` — `src/project_control/cli.py`, lines 113–127. Current native CLI plan compile/validate/apply; no separate CLI plan diff subcommand.

**E05.** `project-control` — `src/project_control/cli.py`, lines 291–315. plan validate uses validate_native_plan; plan apply creates current ProposalEnvelope then applies canonical mutation.

**E06.** `project-control` — `src/project_control/mutation.py`, lines 141–196. Native validation/diff and selective replan have current authority/binding checks.

**E07.** `project-control` — `docs/MAINTENANCE.md`, lines 1–33. Current exact prepared recovery/supersession, dirty preservation, principal-bound grants and live continuation.

**E08.** `project-control` — `src/project_control/services/impact.py`, lines 19–126. One-hop graph neighbors; explicit targets labeled proven; lexical seeds and current workflow enrichment.

**E09.** `project-control` — `src/project_control/graph.py`, lines 40–155. Graph rebuilt from snapshot; kind:id node keys; forward/reverse typed edges over Todo/interface/ownership records.

**E10.** `project-control` — `src/project_control/services/history.py`, lines 99–152. Commit bounds currently compare SHA strings; material events and causal policy retained.

**E11.** `project-control` — `src/project_control/services/evidence.py`, lines 198–233. Current confidence label is support/contradiction heuristic; evidence applicability needs stronger presentation.

**E12.** `project-control` — `src/project_control/call_audit.py`, lines 15–88. Bounded call/message metadata audit; not a retained exact information-packet store.

**E13.** `skills` — `cuda/SKILL.md`, lines 1–73. CUDA controller is front door, host-global physical interlocks; skill guidance currently tells native agents to use observer skill_context/skill_read.

**E14.** `skills` — `local-coding-worker/references/resource-policy.md`, lines 1–51. Existing cooperative drain/eviction, scoped service slots, capacity cap, third-acquire rejection, TTL and quiescence.

**E15.** `skills` — `local-coding-worker/references/integration-contract.md`, lines 1–46. Existing subordinate child authority and packet-before-admission lifecycle; preserve dormant coding delegation.

**E16.** `skills` — `todo-orchestrator/references/project-plan-v2.md`, lines 1–49. Native declarative tasks, dependencies, scopes, resources, gates, interfaces and invariants.

**E17.** `skills` — `todo-orchestrator/schemas/project-plan-v3.schema.json`, lines 1–59. Native schema-v3 run/lanes plus v2-compatible task entities.

**E18.** `skills` — `todo-orchestrator/schemas/project-plan-v2.schema.json`, lines 1–117. Task/gate/dependency/scope grammar used for generated native plans.

**E19.** `skills` — `planning/pce2/machine/skills.todo-plan.json`, lines 1–176. Existing paired PCE2 plan style, serial lane, independent aggregate epic and source preservation requirements.

**E20.** `project-control` — `docs/ARCHITECTURE.md`, lines 1–86. Canonical Todo authority, independent clone/release then Skills gitlink, startup-bound profile isolation.

**E21.** `project-control` — `AGENTS.md`. Read failed as unavailable or escaping registered repository; not treated as an empty instruction file. Local implementer must resolve actual native guidance.

**E22.** `project-control` — `src/project_control/services/planning.py`, lines 148–292. Current read-only plan_preview accepts native plans, validates/diffs and checks mutation guard; target frontend will remove it from observer.

## Primary technical references

These informed implementation design, not a requirement to install a new dependency. All are primary project documentation checked 4 October 2026.

**X01. SCIP Code Intelligence Protocol.** Language-neutral symbol/reference/index interchange can be imported; it is not a complete behavioral dependency oracle.

https://github.com/scip-code/scip

**X02. Tree-sitter introduction.** Incremental concrete syntax trees support syntax extraction, not automatic full name/type resolution.

https://tree-sitter.github.io/tree-sitter/

**X03. Rust Compiler Development Guide: incremental compilation in detail.** Dependency/input fingerprints and changed output tracking motivate precise invalidation rather than file-only assumptions.

https://rustc-dev-guide.rust-lang.org/queries/incremental-compilation-in-detail.html

**X04. Git rev-list documentation.** Commit history is traversed through ancestry and revision-set/range semantics, not lexical SHA ordering.

https://git-scm.com/docs/git-rev-list

**X05. SQLite write-ahead logging.** Private durable queue storage must respect transaction/checkpoint and deployment-filesystem constraints.

https://www.sqlite.org/wal.html

## Prior observed implementation anchors

The same conversation also inspected `services/local_investigate.py`, `services/readonly_exec.py`, `services/machine_inspection.py`, `observer_analysis.py`, `workflow_tools.py`, `mutation_tools.py`, `admin.py`, and Todo skill maintenance/transaction references. They are implementation starting points, but bootstrap must reread their current contents. Do not infer an unchanged runtime from an old description.

## Bootstrap validation findings

**E24–E25:** Current `skills:todo-orchestrator/todo_orchestrator/graph.py:13–42` and `plan.py:34–250` include parent links in graph cycle validation. The initially prepared aggregate-backdependency draft was therefore corrected to use required task-state closure gates. The first complete Skills preview returned `internal_error / bounded_read_failed`; a minimal v3 control passed. After inspecting the native validator and correcting the cycle, both complete final plans passed, with exact payload digests recorded under `validation/`. The failed draft was never applied.

The final previews observed Project Control Todo revision 809 and Skills revision 887 unchanged. These receipts validate the plans as prepared; they are not future application authorization.

**E23:** `project-control:.todo-orchestrator/project.json:1–18` supplied project UUID `76dc6bf0-1223-4dda-bf8b-c306fb7721a7`. `src/project_control/registry.py:37–63` confirms registered authority-repository resolution; the bootstrap bridge verifies the supplied local root against that registry rather than assuming a host path.
