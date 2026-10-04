# AS1 outcome-sized workpackages

Sixteen implementation/qualification outcomes, plus one aggregate epic per authority. Project Control has an exclusive integrator lane for CONTRACT → PACKETS → SURFACE → QUALIFY → RELEASE → epic, an isolated context lane for CONTEXT → TRACE → CONTROL, and an isolated observer-runtime lane for JOBS → SKILL. Both producer lanes integrate at SURFACE. Skills retains its exclusive SEMANTICS → RUNTIME → GPU → ROUTING → QUALIFY → RELEASE → epic sequence. All prerequisites and child closure gates remain intact. Cross-authority producer/consumer receipts remain explicit. See `EXECUTION_HANDOFF.md` for same-base preparation and bounded worker ownership.

## PC-AS1-CONTRACT — Freeze contracts and reconcile existing work

Make this design executable against current runtime reality without losing prior work.

Repository: `project-control`. Local prerequisites: none.

Read: `spec/00-purpose-and-decisions.md`.

Reuse/inspect: `src/project_control/profiles.py`, `src/project_control/models.py`, `src/project_control/workflow_tools.py`, `planning/pce2`, `docs/ARCHITECTURE.md`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `CON-01`, `CON-02`, `CON-03`. Implement behavioral tests in `tests/as1/test_pc_as1_contract.py`; gate `PC-AS1-CONTRACT-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-PACKETS — Build reusable source-bound information packets

Make information reusable through durable exact payloads, non-reused word aliases and freshness-aware hints.

Repository: `project-control`. Local prerequisites: `PC-AS1-CONTRACT`.

Read: `spec/02-packets-and-scouts.md`.

Reuse/inspect: `src/project_control/call_audit.py`, `src/project_control/normalize.py`, `src/project_control/models.py`, `src/project_control/context_fragments.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `PKT-01`, `PKT-02`, `PKT-03`, `PKT-04`, `PKT-05`. Implement behavioral tests in `tests/as1/test_pc_as1_packets.py`; gate `PC-AS1-PACKETS-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-CONTEXT — Consolidate orientation and deterministic context

Deliver overview/delta/frontier/read/search/evidence/history/machine through canonical services.

Repository: `project-control`. Local prerequisites: `PC-AS1-PACKETS`.

Read: `spec/01-tools-and-profiles.md`.

Reuse/inspect: `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/delta.py`, `src/project_control/services/source_context.py`, `src/project_control/services/inspect.py`, `src/project_control/services/architecture.py`, `src/project_control/services/coordination.py`, `src/project_control/services/evidence.py`, `src/project_control/services/history.py`, `src/project_control/services/machine_inspection.py`, `src/project_control/source_index.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `CTX-01`, `CTX-02`, `CTX-03`, `CTX-04`, `CTX-05`, `CTX-06`, `CTX-07`, `CTX-08`, `CTX-09`, `CTX-10`, `CTX-11`. Implement behavioral tests in `tests/as1/test_pc_as1_context.py`; gate `PC-AS1-CONTEXT-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-TRACE — Implement heterogeneous incremental dependency tracing

Make impact a directed, source-bound, cross-project tracer with honest provider-scoped completeness.

Repository: `project-control`. Local prerequisites: `PC-AS1-CONTEXT`.

Read: `spec/03-impact-history-evidence.md`.

Reuse/inspect: `src/project_control/graph.py`, `src/project_control/services/impact.py`, `src/project_control/registry.py`, `src/project_control/source_index.py`, `src/project_control/adapters/ctxpp.py`, `src/project_control/adapters/git.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `TRC-01`, `TRC-02`, `TRC-03`, `TRC-04`, `TRC-05`, `TRC-06`. Implement behavioral tests in `tests/as1/test_pc_as1_trace.py`; gate `PC-AS1-TRACE-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-JOBS — Run durable command-first investigation jobs

Serve bounded scout questions with persistent queue/log/hints, independent dispatch and restart-safe attempts.

Repository: `project-control`. Local prerequisites: `PC-AS1-PACKETS`.

Read: `spec/02-packets-and-scouts.md`.

Reuse/inspect: `src/project_control/services/local_investigate.py`, `src/project_control/services/readonly_exec.py`, `src/project_control/observer_analysis.py`, `src/project_control/app.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `JOB-01`, `JOB-02`, `JOB-03`, `JOB-04`, `JOB-05`, `JOB-06`, `JOB-07`. Implement behavioral tests in `tests/as1/test_pc_as1_jobs.py`; gate `PC-AS1-JOBS-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-SKILL — Deliver routed skill authority through one adapter

Use the shared local worker in skills-home mode to select sources; broker returns direct text plus tiny labeled synthesis.

Repository: `project-control`. Local prerequisites: `PC-AS1-JOBS`.

Read: `spec/04-skills-and-resources.md`.

Reuse/inspect: `src/project_control/skills.py`, `src/project_control/skill_context.py`, `src/project_control/observer_analysis.py`, `src/project_control/app.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `SKL-01`, `SKL-02`, `SKL-03`, `SKL-04`. Implement behavioral tests in `tests/as1/test_pc_as1_skill.py`; gate `PC-AS1-SKILL-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-CONTROL — Expose complete transactional mutator control

Give mutator independent planning, typed project registration and safe maintenance without raw admin ritual.

Repository: `project-control`. Local prerequisites: `PC-AS1-CONTEXT`, `PC-AS1-TRACE`.

Read: `spec/05-mutation-and-registration.md`.

Reuse/inspect: `src/project_control/mutation.py`, `src/project_control/mutation_tools.py`, `src/project_control/admin.py`, `src/project_control/workflow_core`, `src/project_control/maintenance_host.py`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `MUT-01`, `MUT-02`, `MUT-03`, `MUT-04`, `MUT-05`. Implement behavioral tests in `tests/as1/test_pc_as1_control.py`; gate `PC-AS1-CONTROL-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-SURFACE — Wire and document the new role-aware frontend

Replace ordinary discovery, dispatch, descriptions, schemas, modes and routing with the consolidated API.

Repository: `project-control`. Local prerequisites: `PC-AS1-CONTEXT`, `PC-AS1-TRACE`, `PC-AS1-JOBS`, `PC-AS1-SKILL`, `PC-AS1-CONTROL`.

Read: `spec/01-tools-and-profiles.md`.

Reuse/inspect: `src/project_control/profiles.py`, `src/project_control/app.py`, `src/project_control/cli.py`, `docs`, `tests`, `packaging`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `API-01`, `API-02`, `API-03`, `API-04`. Implement behavioral tests in `tests/as1/test_pc_as1_surface.py`; gate `PC-AS1-SURFACE-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-QUALIFY — Qualify complete role journeys and economics

Prove correctness and useful-context efficiency across the whole paired candidate, not individual renamed tools.

Repository: `project-control`. Local prerequisites: `PC-AS1-SURFACE`.

Read: `spec/06-acceptance-and-economics.md`.

Reuse/inspect: `tests`, `packaging`, `docs`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `E2E-01`, `E2E-02`, `E2E-03`, `E2E-04`, `E2E-05`. Implement behavioral tests in `tests/as1/test_pc_as1_qualify.py`; gate `PC-AS1-QUALIFY-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## PC-AS1-RELEASE — Activate and verify the paired adaptive surface

Deploy the qualified bound runtime and prove live new profile surfaces with rollback and persisted jobs intact.

Repository: `project-control`. Local prerequisites: `PC-AS1-QUALIFY`.

Read: `spec/07-bootstrap-and-release.md`.

Reuse/inspect: `packaging`, `scripts`, `docs`, `src/project_control/runtime_identity.py`, `planning/adaptive-surface-v1`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `REL-01`, `REL-02`, `REL-03`. Implement behavioral tests in `tests/as1/test_pc_as1_release.py`; gate `PC-AS1-RELEASE-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-SEMANTICS — Extend canonical semantic and mutation contracts

Supply durable project declarations/skill usage/orientation and typed amendments through the existing Todo authority.

Repository: `skills`. Local prerequisites: none.

Read: `spec/05-mutation-and-registration.md`.

Reuse/inspect: `todo-orchestrator`, `integrations/coding-workflow-mcp`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `SEM-01`, `SEM-02`, `SEM-03`. Implement behavioral tests in `tests/as1/test_sk_as1_semantics.py`; gate `SK-AS1-SEMANTICS-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-RUNTIME — Adapt the existing local service for read-only scout modes

Reuse the model supervisor in investigator/skill modes without tying persistent jobs to GPU or child coding authority.

Repository: `skills`. Local prerequisites: `SK-AS1-SEMANTICS`.

Read: `spec/02-packets-and-scouts.md`.

Reuse/inspect: `local-coding-worker`, `integrations/coding-workflow-mcp`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `RUN-01`, `RUN-02`, `RUN-03`. Implement behavioral tests in `tests/as1/test_sk_as1_runtime.py`; gate `SK-AS1-RUNTIME-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-GPU — Make warm observer models reclaimable by foreground work

Complete observer/skill residency participation in the existing host-global CUDA eviction/interlock protocol.

Repository: `skills`. Local prerequisites: `SK-AS1-RUNTIME`.

Read: `spec/04-skills-and-resources.md`.

Reuse/inspect: `cuda/scripts`, `cuda/tests`, `local-coding-worker`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `GPU-01`, `GPU-02`, `GPU-03`, `GPU-04`. Implement behavioral tests in `tests/as1/test_sk_as1_gpu.py`; gate `SK-AS1-GPU-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-ROUTING — Make native skills role-efficient and preserve corpus routing

Update instruction routing for native agents and the observer adapter without rewriting technical skill content.

Repository: `skills`. Local prerequisites: `SK-AS1-SEMANTICS`, `SK-AS1-GPU`.

Read: `spec/04-skills-and-resources.md`.

Reuse/inspect: `cuda`, `local-coding-worker`, `todo-orchestrator`, `cpp-context-compiler`, `integrations`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `NAT-01`, `NAT-02`, `NAT-03`, `NAT-04`. Implement behavioral tests in `tests/as1/test_sk_as1_routing.py`; gate `SK-AS1-ROUTING-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-QUALIFY — Qualify kernel, worker, CUDA and native-skill interoperation

Produce a paired Skills qualification receipt usable by Project Control without merging authorities.

Repository: `skills`. Local prerequisites: `SK-AS1-ROUTING`.

Read: `spec/06-acceptance-and-economics.md`.

Reuse/inspect: `tests`, `todo-orchestrator/tests`, `cuda/tests`, `local-coding-worker/tests`, `integrations`, `planning/adaptive-surface-v1`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `SQA-01`, `SQA-02`, `SQA-03`. Implement behavioral tests in `tests/as1/test_sk_as1_qualify.py`; gate `SK-AS1-QUALIFY-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.

## SK-AS1-RELEASE — Pin the qualified standalone release in Skills

Finish paired packaging by pinning the qualified standalone Project Control commit and publishing a bound release receipt.

Repository: `skills`. Local prerequisites: `SK-AS1-QUALIFY`.

Read: `spec/07-bootstrap-and-release.md`.

Reuse/inspect: `project-control`, `.gitmodules`, `integrations`, `planning/adaptive-surface-v1`. Some module paths are historical implementation anchors and must be rechecked; do not create a competing subsystem simply because a file was renamed.

Acceptance: `PIN-01`, `PIN-02`. Implement behavioral tests in `tests/as1/test_sk_as1_release.py`; gate `SK-AS1-RELEASE-ACCEPT`.

Current executed behavioral evidence for every owned case, producer/consumer compatibility, and actual native gate pass. No stubs or silently skipped gates.
