# AS1 transactional control service

`project_control.as1_control.ControlService` is an in-process service port for the
SURFACE adapter. Construct it once with trusted `ContextHost(profile, principal,
projects)` and the registered `ProjectControlConfig`. Wire objects cannot choose
roles, claims, principals, repository roots, executable trust or host callbacks.
The matching `InformationService` supplies overview, inspection, search, evidence,
history and impact without an observer preparing a proposal first. Its host must
match exactly. Missing ports return explicit unavailable/partial results.

## Ports

* `plan(project, action, ...)`: `context` returns current native observation
  preconditions and semantic declarations; `validate`/`diff` take `native_plan`;
  `apply` takes a native plan or reviewed `ProposalEnvelope`; `amend` takes the
  existing native selective-replan request. `supersede` takes a small `intent`
  to prepare an exact principal-bound mandate or `authorization_id` to execute
  it. `retire` takes a small native retirement intent to derive the exact affected
  request, then `prepared_request` to apply through the canonical retirement
  transaction. Supply exactly one applicable payload. No paths or raw admin
  commands are exposed.
* `amend_project(request)`: frozen `pc-project-amendment/1` fields are `format`,
  `project`, `action`, `intent`, `expected_revision`, `operation_id`, `payload`
  and optional `mode` (`preview`/`apply`). Actions cover identity, relation,
  generation, host-registered provider options, removal, skill use and
  source-backed orientation. The canonical semantic kernel owns payload
  validation, affected-set review, durable operation receipt, transaction,
  invalidation, revision and projection repair reporting.
* `project_context(project)`: reads declarations and per-field orientation
  freshness. The broker verifies configured project/repository IDs and actual
  registered bytes when the kernel's read binding lacks the configured alias.
  This changes only the returned freshness view.
* `maintain_execution(request)`: the mutator's direct rescue path; the Todo
  workflow engine is bundled with Project Control, so this does not depend on
  Todo Orchestrator skill discovery or a separate source checkout. Diagnose
  with `{project, action: "diagnose", task_id}`; review blockers, then prepare
  the same task with `{project, action: "prepare", task_id, run_id?}`. Provide
  `run_id` when necessary to select the exact active run. Execute only the
  returned principal-bound grant with
  `{project, action: "execute", authorization_id}`. The server derives the
  repository and recipient principal. Native grants preserve dirty work and
  successful proof, refuse live owners, and replay canonical receipts with
  current continuation assessment. Project/principal access checks remain
  active; unsafe, ambiguous, or corrupt authorities may require separate owner
  repair.
* `publish_context(project, request)`: a coder/codex host may supply a startup
  `coder_claim_provider(principal, project)`; the native kernel authenticates the
  returned canonical claim and task source scope. Wire objects contain only
  `kind`, optional `task_id` and `payload`; a model-supplied token is rejected.
  Before opening a writable service, the facade authenticates that claim against
  the canonical authority and resolves every anchor through the host registry.
  Each anchor must resolve to the canonical authority repository and remain
  within the claimed task's paths, including its resolved filesystem path.
  A second registered repository in the same project grants no publication
  scope, even when its relative path matches the claimed task. A trusted,
  publication-scoped constructor callback repeats repository and current claim
  scope checks during native source verification. The kernel repeats claim and
  task-scope validation in the publication transaction.

Observer/coder/codex cannot invoke broad planning, declarations or maintenance.
Only mutator hosts may do so. The legacy host-issued maintenance operator route
remains owned by its existing adapter.

## Effects and authority

| Effect | Policy and enforcement |
| --- | --- |
| Read/validate/diff | No semantic mutation; registered roots only |
| Add/update source-backed fact | Mutator; native current revision/source checks; no repeated human ritual |
| Remove declaration | Exact kind/ID/version; historical records and related-source invalidations retained |
| Replan/supersede/retire | Native affected-set and ownership guards; preserved work; successful terminal proof frozen |
| Stopped repair | Fresh native inspection; exact principal-bound grant; no reset/clean or time-expiring live processes |
| Delete retained work/history, override active owner, extend trust | Unsupported by this facade; no `approved`, role, repository prose or caller claim can authorize these effects |

Destructive capabilities require a separate trusted host mechanism bound to real,
fresh, explicit user authority and exact effects. This service does not advertise
interaction-policy prose as cryptographic confirmation. No destructive primitive
is available here, even with a caller-authored approval flag.

## Freshness and deployment limits

Semantic preview stores exact affected records, actual source hashes and provider
trust in the native operation ledger. Apply checks these in the canonical
transaction. Unrelated semantic revisions/source edits may survive a reviewed,
declaration-only operation; changed affected rows/sources refuse. A retry returns
the known receipt and current readiness. No-ops/replays do not increment semantic
revision or rewrite projections. A committed projection failure remains visible.
These semantics are provided by migration 12; an older installed kernel or
migration-11 authority returns unavailable/partial without an implicit upgrade.

`verify_source(locator)` is a trusted broker port: exact configured IDs, canonical
Todo UUID/revision, registered relative path and direct SHA256. It denies private
paths and all requested symlinks. Reads use the existing descriptor-based,
no-follow, race-checked `InformationService._working_bytes` reader.
Canonical UUID locators may also use exact native local repository aliases
(the native registered repository ID, or its root path/name fallback). These
resolve only to the trusted canonical authority root; arbitrary PC repository
aliases retain their registered roots and do not gain publication scope.
When the paired kernel advertises the startup-only
`project_source_verifier` constructor keyword, the service passes its trusted
broker directly. Kernels without that capability retain
`source_prerequisite_unavailable` for cross-project anchors. Neither serialized
requests nor their context can install or replace the verifier. Cross-project typed
relations require exact UUID resolution among host registrations. No declaration
grants another root or merges project authorities.

Identical nonempty-plan no-op handling depends on the paired canonical plan
kernel. The facade reports its actual diff/transaction result and does not
invent idempotence. Tests require a real unchanged revision, task/gate versions
and canonical rows before claiming MUT-02 qualification; older installed
kernels do not provide this guarantee.

## Evidence

`tests/as1/test_pc_as1_control.py` runs candidate source kernels in child processes
against disposable Git/Todo authorities. Children explicitly select the actual
source identity; deployed release identity is untouched. Tests exercise canonical
planning, semantic transactions, review conflicts, orientation freshness,
root/provider denial, dirty handoff preservation, stale/live-owner refusal,
principal-bound grant replay and current continuation. Native maintenance,
supersession and plan regression suites must also pass; root owns the bound gate
and lifecycle acceptance.
