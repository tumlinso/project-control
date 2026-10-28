# Develop without disturbing a running service

Use an isolated worktree and environment. Source checks do not require a service restart, live model, GPU or assigned workflow task.

## Setup

```sh
scripts/pc-dev setup --foundation-repo /path/to/project-foundations
```

The launcher builds the shared packages from the explicit foundations checkout. Add `--todo-wheel /path/to/the/pinned-todo.whl` for workflow operations; pairing checks still apply. Source-only retrieval needs no Todo authority.

There is intentionally no committed dependency lock while foundations are supplied as local wheels. Setup resolves dependencies in an ignored per-checkout scratch project, keeping its machine-specific wheel paths out of source control. A fresh setup may fetch ordinary third-party dependencies; the private cache supports subsequent offline setup.

## A private candidate

Choose a private development directory and export its paths:

```sh
export XDG_CONFIG_HOME="$PWD/.dev/config"
export XDG_STATE_HOME="$PWD/.dev/state"
export XDG_CACHE_HOME="$PWD/.dev/cache"
scripts/pc-dev run init
scripts/pc-dev run workspace add demo main /absolute/path/to/a/disposable-project
scripts/pc-dev run serve observer --host 127.0.0.1 --port 18768
```

Do not reuse production private state, install service units, edit active launchers, or change the selected sealed release. Check the candidate's effective paths and profile first. Use disposable repositories for examples that change registration or workflow state.

## Focused checks

```sh
scripts/pc-dev test
scripts/pc-dev test path/to/the/affected_test.py -q
```

The smoke selection executes active-product assertions. Collection-only output establishes discovery, not correctness. Expand to shared-boundary tests when a change crosses package or authority boundaries. Retired inference/service tests remain with their archive.

Use fixtures for Codex planner turns and scheduler operations. Any optional real-agent smoke uses `gpt-6-luna` at low effort, with no automatic escalation. Comprehensive benchmarking and formal live model/GPU qualification are separate work.

For retrieval performance, check that a file read avoids all-worktree inspection, a narrow trace avoids unrelated projects, and an unchanged query reuses observations. Keep timing checks small and deterministic.

Orientation can reuse worktree observations for up to three seconds and retains their observation times. Exact source reads and explicit Git inspection use their own current source observations.

Report source tests, package installation, MCP transport, live execution and deployment separately. Preserve unknown work and authority state; never repair generated projections or SQLite authorities by hand. The earlier guide is preserved in the assistance archive.
