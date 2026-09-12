# Workflow Core authority boundary

`project_control.workflow_core` is the sole workflow transaction authority in
the candidate: it validates and applies workflow transactions, owns workflow
state transitions, and is the only candidate path to authoritative workflow
storage. This name avoids the existing read-oriented
`project_control.workflow` module.

The boundary does not absorb external authority. Git and worktree operations
remain Git effects; the host scheduler, operating system, and providers retain
host-resource authority; and compiler toolchains retain compiler-artifact
authority. Workflow Core may record an intent and an observed receipt for each
such effect, but it must not represent one as an SQLite-atomic transition.

This is a parity-move contract. It preserves the internal Todo storage layout
and baseline workflow lifecycle, including state paths, UUIDs, history,
revision/event sequencing, graph/readiness behavior, capability classes,
context fragments, workspaces, integration, child acceptance, and recovery.
Relocation is not authorization for a lifecycle or schema change.

The normative record is
[`workflow_core_authority_boundary.v1.json`](workflow_core_authority_boundary.v1.json).
Its source-ledger digest anchors the construction-time source evidence without
claiming that the sealed package is a live authority.
