# Project Control Unification V1

PCU-V1 is a non-destructive unification campaign. Its deterministic architectural payload is [campaign.json](campaign.json), SHA-256 `4eb7aaba42e3b3818cf9dfb3d64abe140a8c7c3449bb099727838b3308258dda`. The same payload and digest are committed in both repositories.

The frozen boundary is: Project Control is the only model-facing product; Todo Orchestrator remains the sole transactional and SQLite authority; observer and Codex profiles are separately registered and allowlisted; Project Control binds to the canonical Todo kernel in-process; the compatibility name is a forwarding alias only; cutover is candidate-first and atomic; Project Control history later enters Skills only as a pinned submodule; downstream migration is explicit and outside PCU-V1; and all history advances through ordinary commits and events.

Bootstrap authority is limited to planning, contracts, sanitized evidence, rollback inventory, additive schema-v3 plan application, generated Todo projections, and ordinary bootstrap commits. It grants no authority to implement, claim, dispatch, delegate, create worktrees, change live MCP/service/tunnel state, add the submodule, or mutate a downstream repository.

The release manifest intentionally records `source_parent_commit`, not a self-referential release commit. Skills records the resulting validated Project Control commit later in its release lock.
