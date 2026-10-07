# Codex setup

Codex connects to Project Control through its configured stdio MCP registration.
For routine development, point that registration at this checkout's
`scripts/pc-dev` launcher. The registered profile, principal binding, workspace
configuration, and durable state are part of the existing local configuration;
preserve them when changing the command.

## Use the checkout

From the repository root:

```sh
scripts/pc-dev setup
scripts/pc-dev run --help
scripts/pc-dev run codex
```

The first command creates or synchronizes the normal repository environment.
The source launcher imports Project Control and bundled Todo directly from
`src/`, clears inherited release and interpreter pins, and does not build a
wheel. Configure Codex to invoke the absolute checkout path to `scripts/pc-dev`
with arguments `run`, `codex`. Restart the Project Control service after source
edits; reconnect the stdio MCP client after changing its registration or the
tools it has cached. See [development](development.md) for tests and
[deployment](deployment.md) for restart and readiness checks.

Read-only questions and investigations do not need a task claim. For substantial
workflow work, use the current Project Control workflow tools and task context
when available. A temporarily unavailable service does not prevent authorized
direct checkout work or source tests. Semantic workflow mutations still go
through Todo's transaction and authorization guards.

## Delegation

Delegate bounded research, implementation, testing, or review to configured
Codex subagents using the named profiles `scout`, `researcher`, `implementer`,
`reviewer`, or `parallel-head`. Keep ownership and source scope explicit. Do not
use Project Control's local observer service as a coding worker. Ordinary
workers do not recursively delegate. The root controller retains cross-cutting
decisions, integration, and final acceptance. Preserve unrelated edits, state,
and unknown work.

## Optional frozen candidate

Daily source development does not need a candidate build. When a frozen release
is specifically required, use the installer with explicit source and
destination paths:

```sh
python scripts/install.py \
  --project-control-root "$PWD" \
  --destination /absolute/path/to/candidate
```

An optional domain-content tree can be supplied with `--skills-root
/absolute/path/to/content`. Candidate construction does not activate or restart
services. Keep executable provenance and optional content separate; missing
domain content does not invalidate bundled PC/Todo code. See
[deployment](deployment.md) for the source restart path and the release boundary.
