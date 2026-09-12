# Shared engine and cutover authority

Owner: `PC-WF2-C03`. The normative developmental record is
`shared_engine_cutover.v1.json`, validated by its independently versioned
schema. It is a contract, not a receipt and not an authorization to deploy.

Ctxpp is a standalone, read-only query provider: Project Control consumes its
stable query contract without refresh, mutation, SQLite-layout coupling, or a
reverse workflow dependency. Source identity identifies algorithm, version,
domain, and covered inputs; different fallback identities are never treated as
equivalent merely because their output resembles a fingerprint.

Host and worker facades are supported read surfaces, not workflow writers.
The donor `todo_orchestrator` maps to `project_control.workflow_core` only
after behavior-preserving parity. Cutover fences old writers before an affected
authority changes; rollback is restore-or-migrate-forward unless compatibility
is independently qualified.

Closure is deliberately directional: PC I50 precedes Skills X03, Skills I90
precedes PC X04, and PC I90 closes last. Therefore neither authority requires
the other final acceptance to reach its own prerequisite final state.
