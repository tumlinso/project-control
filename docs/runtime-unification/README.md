# Unified Project Control runtime

Operator authorization on 2026-10-06 replaces the old runtime entrypoints only.
All workspace repositories and their durable Todo state remain in place.
PA1 ASSIST is accepted; LAB and live qualification remain paused.

The standard surface is `project-control`, installed at
`/home/tumlinson/.local/bin/project-control`. It selects the immutable installed
release through `/home/tumlinson/.local/share/project-control/current`, clears
ambient Python and legacy identity overrides, and applies the same configuration
and private observer state for shell, Codex and the HTTP service.

Use `project-control workspace list` for registered workspaces. Existing Codex
clients use `project-control codex` automatically through their MCP registration.
The observer service is `project-control.service` at `127.0.0.1:8768/mcp`;
`project-control-as1.service` becomes an alias for that same service. Transport
profiles retain their existing permission boundaries while using the same
surface implementation, release, state and authority configuration.

`/home/tumlinson/.config/project-control/shell-env.sh` puts the stable launcher
first on PATH. The canonical checkout `.venv` becomes an alias of the selected
installed runtime, and `PROJECT_CONTROL_RUNTIME_PYTHON` identifies its Python.
Do not run `uv sync` against this alias: production runtime changes go through
`scripts/install.py` into a new release followed by an explicit selector switch.
A separate development environment can be created when development requires it.

The inference service is disabled and stopped. Its demand-only entrypoint also
selects the current release and verifies the receiver binding; do not start it
until the operator authorizes inference. No automatic preparation or model
qualification is enabled by this runtime rebuild. `/healthz` and `/version`
remain model-free; `/readyz` deliberately reports 503 while inference is off.
The previous inference-dependent tunnel refresh hook is retained in rollback,
not invoked during this offline-inference pause.

Old candidates, entrypoints and settings are retained beneath
`/home/tumlinson/.local/share/project-control/rollbacks/runtime-unification-20261006T2005`.
State backup and preservation coverage are recorded in `state-backup.json` and
`state-before.json`; historical archives and materialized worktrees are retained
in place. Do not restore older databases over current workspace state.

Existing stdio client processes retain their imported release until they
reconnect. New clients and the unified HTTP service select the current release.
A Codex restart/reconnect is sufficient; no alternate runtime path is needed.
See `cutover.json` and `validation.json` for the final deployed identity and checks.
