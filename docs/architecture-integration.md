# Integrating the remaining workflow runtime

> **Historical migration plan.** The Todo and workflow adapter consolidation
> described here has since been implemented in this repository. Its ownership
> tables and open-work language are not a current backlog or authority to
> recreate the external supplier/adapter layout. For today's source development
> and test commands, use [development.md](development.md) and
> [AGENTS.md](../AGENTS.md). Keep the historical rationale and receipts below.

## Why ordinary development is expensive

Project Control is Python, but its workflow runtime is assembled from several
repositories and pinned source identities. A source edit and an installed release
are different environments. Rebuilding an isolated release is appropriate for
deployment; it should not be necessary to run a fixture test after a Python edit.

The October 7 source audit found these concrete problems:

- The active Skills directory contains CUDA and cpp-context-compiler. Todo's
  maintained source is in `skills_dev`, while development helpers still expected
  `skills/todo-orchestrator`.
- The Project Control development environment has an editable Project Control
  install but no installed Todo or coding-workflow adapter.
- Runtime identity uses a Skills-root location to identify application code.
  Observer documentation and executable workflow dependencies consequently share
  a setting even though their locations now differ.
- README and CI used unittest discovery. Hundreds of top-level pytest tests were
  omitted, and pytest was absent from the locked development dependencies.
- Live release and fingerprint environment variables can leak into source tests,
  making a fixture failure look like an installed runtime failure.

Local-coding-worker retirement is being handled separately. This document covers
the remaining application boundaries; it does not assign ownership of that work.

## Target ownership

| Component | Owner | Required change |
| --- | --- | --- |
| Todo transactions, state, plans, claims, background workflow | Project Control Python source | Move the runtime and its tests into this repository and release it together. |
| coding-workflow-mcp adapter | Project Control migration work | Move necessary runtime identity helpers, replace internal imports, retire forwarding CLI/MCP entry points after their registrations are changed. |
| Runtime configuration, identity, packaging, application routing | Project Control | Give application code one owner and one source identity. Keep document-provider configuration separate. |
| CUDA and cpp-context-compiler guidance and specialized tools | Domain Skills | Resolve these providers explicitly; do not use their directory as the identity of Todo. |
| Existing ledgers and state | Existing projects | Keep their identities, paths, formats, and history readable. Code relocation does not require ledger relocation. |

Todo can remain an internal package named `todo_orchestrator` during integration.
There is no need to rename every import or flatten the transaction engine into
the MCP server. One application can contain several well-defined modules.

## Integration order

1. **Make source testing independent of deployment.** Declare pytest, provide one
   source test entry point, and use the maintained external Todo source temporarily.
   Keep fixture tests separate from host, GPU, model, and installed-release checks.
2. **Bring Todo source and tests into Project Control.** Include the package in the
   wheel and the development environment. Remove the second repository from
   ordinary dependency installation and release construction. Preserve package
   names initially to make the change mechanical and reviewable.
3. **Remove the adapter cycle.** Project Control imports Todo; Todo's background
   worker imports `coding_workflow_mcp.runtime_identity`; the adapter forwards
   functionality back to Project Control or Todo. Replace this with one internal
   identity/configuration seam before removing the adapter package.
4. **Separate executable identity from document providers.** A candidate verifies
   its own packaged application. Observer content has its own configured roots and
   versions. Move any remaining application catalog/routing resources into the
   application; obsolete resources can be deleted after consumers are removed.
5. **Make clean-checkout CI representative.** Run pytest so both pytest functions
   and unittest cases are collected. Record supplier-dependent coverage explicitly
   until Todo is internal. Add installed-release qualification as a separate stage.

Steps 2 through 4 should be coordinated with the concurrent installer/runtime
changes. Do not introduce a second competing source-locator API during that work.

## Compatibility that matters

Keep readers for existing `.todo-orchestrator/project.json`, project UUIDs,
snapshots, and SQLite stores under the Git common directory's
`todo-orchestrator/<project-uuid>/state.sqlite3`, including supported explicit and
XDG overrides. Preserve schema migrations, event history, and snapshot semantics.
Use copied or temporary fixtures to qualify compatibility, never live ledgers.

Old live service names, CLI aliases, Skills package layout, and source-fingerprint
environment variables do not need to dictate the new architecture. Before removing
an old entry point, update its active service/client registration. Historical state
readers can remain after those entry points disappear.

## Testing policy during the transition

- A targeted CPU fixture run is valid evidence for a source change. Report its
  selectors and failures; do not describe it as full release qualification.
- Missing external imports, stale source paths, and leaked candidate pins are
  development-environment defects. Fix their setup rather than disabling runtime
  identity checks in production.
- Collect and count tests using pytest. A green unittest-only run does not establish
  coverage of pytest functions.
- Packaging, actual service transport, host interlocks, and model behavior require
  their respective qualification before deployment. They should not run as a
  prerequisite for every documentation or deterministic workflow edit.
- The temporary external Todo/adapter import path is removable scaffolding. It
  should disappear when those packages join the application.

## Source evidence

- Application/Todo binding: `src/project_control/workflow_binding.py` and
  `src/project_control/runtime_identity.py` (`bind_runtime`).
- Two-source construction: `packaging/installer.py` (`_freeze_skills` and
  installation paths).
- External adapter cycle: `skills_dev/todo-orchestrator/todo_orchestrator/background/worker.py`
  and `skills_dev/integrations/coding-workflow-mcp/coding_workflow_mcp/compat.py`.
- Development entry points: `scripts/pa1_source_tests.sh`, `pyproject.toml`,
  `uv.lock`, and `.github/workflows/test.yml`.

These observations describe the source audit on October 7, 2026. Concurrent runtime
retirement changes may remove individual consumers before the larger Todo move.
