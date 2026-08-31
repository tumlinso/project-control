# Project Control mutation ingestion v1

Project Control is a control plane over Todo Orchestrator. Todo Orchestrator is
the sole Todo validation, dependency, lifecycle, transaction, SQLite, event,
and projection authority. Project Control never writes Todo SQLite, copies plan
semantics, or invokes another MCP server to perform mutation.

## Input and authority boundary

The mutation service consumes a native Todo Orchestrator plan. The pre-ledger
compiler is only a deterministic frontend producing that canonical plan.

A versioned package contains `preledger.json` with format
`project-control-preledger`, schema version 1, and a required `tasks` reference.
It may additionally reference `interfaces`, `dependency_index`, `summary`, and
`manifest` files. The conventional names are `proposed_todos.json`,
`interface_catalog.json`, `dependency_edges.csv`, `plan_summary.json`, and
`MANIFEST.sha256`; only the task source is mandatory. If `preledger.json` is
absent, a directory containing `proposed_todos.json` is accepted as the current
compatibility package.

`proposed_todos.json` is semantic task authority. Summary and CSV files never
replace it. A manifest, when present, is validated before compilation. Without
a manifest, the compiler digests the semantic inputs deterministically.

V1 compiles and applies exactly one explicitly selected repository authority.
Dependencies between selected tasks become native Todo task dependencies.
Dependencies crossing to an excluded repository are returned separately and
are never fabricated as local tasks or silently discarded. V1 does not provide
cross-project atomic transactions.

## Lowering contract

Task `id`, `title`, and `purpose` lower to native task ID, title, and objective.
Identity fields are required; other rich fields are optional. Explicit
`write_scope` lowers to exclusive paths, and explicit existing/source paths
lower to read paths. Repository membership never implies broad ownership.
Unsupported rich metadata is preserved compactly in native task notes,
including the package digest, original ID, workstream, motivations,
implementation mechanism, invariants, forbidden shortcuts, validation,
performance evidence, completion condition, and experimental/negative-result
status.

An interface is imported only when its ID, owner task, state, version, contract
paths, and content hash meet the native schema. Incomplete records are reported
as unsupported; values are never guessed. Canonical JSON serialization makes
the same package, target label, and compiler version byte-for-byte stable.

## Proposal and mutation contract

`ProposalEnvelope` remains inert and `authority_to_apply` remains false. A
proposal grants no authority. The trusted, startup-selected `mutator` profile
grants authority to consume a supported native plan only after Project Control
re-observes the project, verifies the proposal digest, and compares all relevant
observation preconditions using the existing proposal machinery.

Staleness fails closed. Project Control does not guess, rebase, regenerate, or
retry. It delegates validation, diff, and one apply attempt to the verified Todo
runtime. An empty diff returns a deterministic no-op without apply. A bounded
receipt records proposal and plan digests, authority UUID, before/after
revisions, expected and applied additions/updates, warnings, and current
preconditions; Todo events remain the durable mutation history.

The observer stays permanently project-read-only and its tool contract is
unchanged. The Codex profile retains its existing six workflow tools and rich
reads. Broad plan mutation exists only in the distinct stdio `mutator` profile;
client metadata, user agents, annotations, model claims, and tool arguments
cannot select it. Ordinary claimed implementation work continues to use the
six-tool workflow protocol. Plan mutation is for bootstrap and ledger/control
changes.
