# Development

Project Control and Todo Orchestrator are tested from this checkout in one
Python environment. There is no separate executable supplier checkout to
install or pair for routine source work.

## Set up once

Install Python 3.11 or newer and `uv`, then run:

```sh
scripts/pc-dev setup
```

This runs `uv sync --frozen --group dev` into `.venv`. The command follows the
existing repository-local `.venv` symlink without replacing it. By default,
uv caches downloads under the ignored `.cache/uv`; setting `UV_CACHE_DIR`
selects another cache directory. Setup is explicit and is not repeated by
`run` or `test`.

If the environment is missing, both commands stop and tell you to run setup.
Source edits do not trigger dependency installation, package builds, or
candidate creation.

## Run the checkout

Pass the normal Project Control CLI arguments after `run`:

```sh
scripts/pc-dev run --help
scripts/pc-dev run serve observer --host 127.0.0.1 --port 8768
scripts/pc-dev run serve mutator
```

The observer and mutator examples run in the foreground. Stop a server with
Ctrl+C and rerun the same command after source edits. To restart a managed
service, use:

```sh
systemctl --user restart project-control-inference.service project-control.service
```

Both this host's inference supervisor and HTTP service use checkout source. The
supervisor remains demand-driven. Restart both after edits to the embedded
runtime or HTTP service; frontend-only edits need only the HTTP service. A
release-backed deployment keeps using its selected release.

The source launcher imports both `project_control` and `todo_orchestrator`
from `src/`. It removes inherited release manifests and digests, runtime
fingerprints and interpreter pins, receiver path/hash overrides, legacy
executable roots, `PYTHONHOME`, and foreign `PYTHONPATH`. It preserves
configured content and provider settings for `run`, including
`PROJECT_CONTROL_SKILLS_ROOT` and the optional observer skills content root.

## Test source

Run a quick deterministic CPU smoke selection covering launcher sanitation,
runtime identity, workflow binding, readiness, and a focused mutator case using
bundled Todo source:

```sh
scripts/pc-dev test
```

Pass pytest selectors and options to run another selection:

```sh
scripts/pc-dev test tests/test_workflow_binding.py -q
scripts/pc-dev test tests/assistance/test_lab_snapshot.py -vv -k rejects
```

The test command imports both packages from `src/` and adds `tests/todo/` for
Todo test helpers. It does not start the Project Control service, inference
supervisor, model server, or GPU work. The old `scripts/dev_tests.sh` and
`scripts/pa1_source_tests.sh` entrypoints delegate to `scripts/pc-dev` for
compatibility; new work should use the single entrypoint directly.

CI intentionally runs a reduced CPU source smoke set, not the entire suite.
It covers broker close persistence, execution cleanup recovery, and detached
snapshot behavior with temporary state. Broader paired workflow integration
is available from this unified checkout. Keep the remaining test limitations
visible and expand CI only with hermetic source fixtures; do not treat a green
smoke run as full-suite or live-service qualification.
