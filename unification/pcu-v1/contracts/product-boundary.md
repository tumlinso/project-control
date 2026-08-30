# PCU-V1 product boundary

Project Control is the only model-facing product. Todo Orchestrator remains in Skills and is the sole transactional workflow kernel, SQLite semantic authority, canonical `WorkflowKernel`, and `WorkflowProtocol`. Project Control retains its rich read services and binds directly to Todo Orchestrator in-process. No MCP implementation calls another MCP implementation, and no workflow business logic is copied into Project Control.

Project Control remains independently cloneable and testable. After its standalone release is validated, Skills may pin that existing history as the `project-control` Git submodule. Historical repositories, UUIDs, databases, events, tasks, sessions, claims, snapshots, worktrees, branches, interfaces, and commits remain intact. New history is ordinary forward history only.

The `coding-workflow` name is a temporary forwarding compatibility alias, never a second implementation or second live registration. Real downstream migration is outside PCU-V1. Cellerator is a read-only sentinel; any rehearsal uses a genuinely independent clone.
