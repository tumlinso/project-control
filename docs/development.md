# Development

Use one checkout, one Python environment, and one launcher for Project Control
and bundled Todo. Source edits are loaded by a new process; they do not require
a package build, release-manifest refresh, or deployment candidate.

## Set up

Install Python 3.11 or newer and `uv`, then run this once for a new checkout or
when dependencies change:

```sh
scripts/pc-dev setup
```

This runs `uv sync --frozen --group dev` into `.venv`. An existing repository
`.venv` symlink is followed and kept in place. The default writable uv cache is
the ignored repository-local `.cache/uv`; `UV_CACHE_DIR` can select another
cache. `run` and `test` never synchronize dependencies. If the environment is
missing, they print the setup command and stop.

## Run source

The launcher imports both packages from this checkout's `src/` and scrubs
inherited release, interpreter, and foreign Python path pins. It preserves
optional domain-content configuration for runtime operations. Use the normal
Project Control CLI after `run`:

```sh
scripts/pc-dev run --help
scripts/pc-dev run serve observer --host 127.0.0.1 --port 8768
scripts/pc-dev run serve mutator
```

These commands run in the foreground. Stop with Ctrl+C and rerun after edits.
For managed services, see [deployment](deployment.md). After a batch of
executable Project Control or Todo Python changes, run focused source checks,
then restart both `project-control-inference.service` and
`project-control.service` before live assistance use. The supervisor
startup-attests the Project Control package, so restarting HTTP alone can leave
the supervisor bound to the prior source identity. Do not restart for every
edit or test: source tests need no service, and external domain-documentation
changes do not invalidate executable identity. Restart only for an isolated
unit configuration change when no executable package changed. Inference stays
demand-driven.

Project Control and bundled Todo executable identity is derived independently
of optional CUDA/C++ or skill documentation. The old
`PROJECT_CONTROL_SKILLS_ROOT` variable is a content-only compatibility alias;
`PROJECT_CONTROL_OBSERVER_SKILLS_ROOT` is the preferred optional content root.
Changing a content tree does not require rebuilding or re-identifying bundled
application code.

The source HTTP unit also needs SQLite's query-only WAL/SHM bookkeeping to be
permitted in canonical Todo state directories. If HTTP reports that a Todo
database cannot be opened while host-side reads work, follow the reviewed
metadata-only `ReadWritePaths` procedure in [deployment](deployment.md#allow-source-http-to-read-registered-todo-state).
The helper defaults to a dry run; applying requires an explicit `--project`
or `--all-projects` selector. It does not initialize Todo state or restart
services.

## Run tests

Make changes in small steps. For a bug, first reproduce it with a focused test
or minimal fixture, add a regression assertion, then run the impacted test and
nearby shared-boundary tests. Expand toward the default smoke or broader suite
when the change crosses packages, runtime boundaries, or security/authority
contracts. Do not require a broad rebuild or full-suite run for every source
edit. Do not turn a failure into a new skip/xfail or weaken an assertion just to
get a green result. Tests with real environment prerequisites can use explicit
skip/xfail markers when their reason and scope are clear. Fix flaky or hanging
fixtures, or record the remaining limitation plainly.

Keep production packages under `src/`, tests under `tests/`, and test support
fixtures discoverable from the test tree. This follows
[pytest's good integration practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html)
and [uv's project environment model](https://docs.astral.sh/uv/guides/projects/).
Use a fast source-fixture base, targeted integration checks, and a limited set
of actual service or end-to-end checks when they answer a real acceptance need;
there is no required unit/integration ratio. See Google's discussion of
[keeping end-to-end testing focused](https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html).

The default command runs a deliberately small CPU smoke selection for launcher
environment handling, runtime identity, workflow binding, readiness, and a
focused mutator boundary:

```sh
scripts/pc-dev test
scripts/pc-dev test -q
```

Selectors and pytest options pass through directly:

```sh
scripts/pc-dev test tests/test_workflow_binding.py -q
scripts/pc-dev test tests/assistance/test_lab_snapshot.py -vv -k rejects
scripts/pc-dev test tests --collect-only
scripts/pc-dev test tests
```

The last command runs every collected test, including legacy cases that may
depend on host facilities; it is an expanded suite, not a promise that all
tests are hermetic or deployment acceptance. `--collect-only` checks discovery
and import collection; it does not run test assertions. When no selector is
supplied, options such as `-q` still use the default smoke selection.
`scripts/dev_tests.sh`
and `scripts/pa1_source_tests.sh` are compatibility wrappers; new work should
use `scripts/pc-dev`.

CI keeps distinct signals:

```sh
scripts/pc-dev test -q
scripts/pc-dev test \
  tests/assistance/test_broker_close_persistence.py \
  tests/assistance/test_execution_cleanup_recovery.py \
  tests/assistance/test_lab_snapshot.py
scripts/pc-dev test tests --collect-only -q
```

The first runs the options-only smoke. The second checks three selected
assistance regressions. The third checks broad discovery/import collection and
does not run assertions. None claims that every collected test passed or that
a live deployment was qualified.

Focused source tests should use temporary fixtures to cover isolated
contracts. A large collection count means pytest discovered that many test
cases; it does not establish that they all pass, are independent, cover every
user path, or qualify a running service. Read the pass/fail/skip summary and
selected paths. Keep evidence separate by level:

| Check | What it establishes | What it does not establish |
| --- | --- | --- |
| Import or `--collect-only` check | Python modules import and test cases are discoverable | Assertions, working service, live authority |
| Focused source test | The selected source contract passes against its fixtures | Unselected tests or real MCP behavior |
| Default smoke | A small CPU selection of core source paths passes | Expanded suite, inference, GPU, or model quality |
| Actual MCP request | The running configured endpoint initializes and serves that request | Model quality, CUDA LAB, or all workflows |
| CPU/CUDA LAB and recovery exercise | The exercised live lifecycle and cleanup path worked | Other unexercised acceptance obligations |
| Frozen candidate qualification | The packaged runtime and deployment candidate passed its checks | Source code added afterward or unrelated live scenarios |

Do not present a collection count or smoke result as comprehensive acceptance.
For a user-facing epic, map the remaining acceptance items to source, fixture,
MCP, CPU/CUDA LAB, recovery, and deployment evidence, and state gaps explicitly.
GPU, inference/model, and service checks are run only when the assigned change
needs them or during the corresponding deployment/qualification stage.
For the PA1 assistance work, see
[the current source qualification guide](pa1/source-qualification.md); older
candidate-specific handoffs are retained as historical evidence.

## State and workflow boundaries

Keep ledgers, project UUIDs, SQLite databases, claims, queued work, snapshots,
and uncommitted source edits intact. Source testing should use temporary state
fixtures where possible and must not require an active Project Control task or
running service. Authorized direct rescue work can proceed from the checkout
when the service is unavailable. Semantic workflow mutations remain guarded by
Todo's transaction and authorization implementation.
