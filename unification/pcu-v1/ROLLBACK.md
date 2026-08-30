# PCU-V1 rollback inventory — Project Control

The original standalone Project Control commit is `603d48dbc4010487e74858541b5e3df50d770177`; the original Skills commit is `35e7c5085a69cde8b74e8b18725ebceb69e4ea0c`. Bootstrap and future release rollback use new ordinary revert commits only. Never reset, rebase, amend, squash, force-push, filter, or rewrite history.

The original observer service unit is `/home/tumlinson/.config/systemd/user/project-control.service`, SHA-256 `ab4a5c371803698fdcbcf0e32282df481c6dc86478212d5a219d72adedcd6fd5`. It uses the standalone checkout and `/home/tumlinson/.local/state/project-control/venvs/project-control-0.3.1-603d48d/bin/project-control serve`. Restore this captured unit and executable path without deleting the candidate or standalone checkout; preserve the endpoint and tunnel.

The original live Codex registration is `coding-workflow`, using `/home/tumlinson/.local/share/coding-workflow-mcp/venv/bin/python -m coding_workflow_mcp`. Restore that captured registration only after removing a failed `project-control` registration, and do not delete the candidate. Never run both registrations concurrently.

If a future Skills submodule addition is uncommitted, remove only the explicitly reviewed `project-control` gitlink and `.gitmodules` stanza. If committed, revert it with a new revert commit. Never delete the standalone checkout or copy `.git`. Future implementation/release commits are reverted with ordinary new commits in dependency order. Todo UUIDs, databases, history, worktrees, stale sessions, and compatibility material remain.
