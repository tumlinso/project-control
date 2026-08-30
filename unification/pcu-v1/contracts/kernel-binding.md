# PCU-KERNEL-BINDING/1

Todo Orchestrator owns database discovery and migration, transactions, revisions and events, identity, sessions, claims, runs, lanes, capabilities, coordination, gates, evidence, resources, locks, workspaces, integration, snapshots, projections, and recovery.

Project Control imports the canonical Todo API in-process from a verified candidate environment containing the local `project-control` and `todo-orchestrator` distributions. `PROJECT_CONTROL_SKILLS_ROOT` is canonical; `CODING_WORKFLOW_SKILLS_ROOT` is temporarily accepted with a bounded warning. Initialization verifies source/package identity and rejects missing, skewed, ambiguous, or rebound runtimes. Request-time `sys.path` mutation, MCP subprocess delegation, MCP `ClientSession` recursion, and independent workflow-table joins are forbidden.

The normal read path is a shared in-process read-only facade with parity to existing normalized observations. The observer may temporarily retain its current subprocess read adapter only as a fail-closed compatibility fallback until parity is proven.
