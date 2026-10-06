# PA1 package preflight

Observed 2026-10-06. This is inspection evidence only; the plans remain unapplied and runtime remains unchanged.

## Package result

The archive staging receipt records SHA-256 `b223b49bd3fa337fc50a0da7f8935c256b8af4a1fa904d18014ef0bb3b85124c`, exact extracted-byte agreement, and the preserved source `HEAD` (`1b1026845d3fff5a27ad539a386b24649ba48f68`) in the sibling `staging-receipt.json`.

The package checker passed against a temporary mirror containing exactly the 40 manifest-listed package files plus the manifest. The fresh machine-readable result is `package-check.json`: 38 source records, 9 outcomes, 11 native task records, 40 planned acceptance cases, 12 evaluation cases, 6 parsed Python files, 18 local Markdown links, and no cycles or consistency errors. This package-check step did not perform product acceptance, native Todo validation, model/GPU work, or application. The separate live inspection and final installed native validation subsequently passed both plans; see `INSTALLATION-READINESS.md` and the native receipts.

The checker is read-only in its default mode: it reads and hashes the manifest inventory, parses JSON/Python and links, and checks graph/plan consistency. It sets `sys.dont_write_bytecode`; it does not import the Todo package unless `--native` is selected. Its manifest check rejects every extra file under the package root (`scripts/check_package.py:82-105`). The controller moved all inspection sidecars into this sibling directory and reran the checker directly against the final staged package; it passed. Preserve this separation so the original manifest inventory remains directly verifiable.

## Native plan validation for later adoption

The supported read-only CLI is:

```sh
project-control plan validate --project project-control --file planning/project-assistance-v1/machine/project-control.todo-plan.json
project-control plan validate --project skills --file planning/project-assistance-v1/machine/skills.todo-plan.json
```

Both workspace IDs are currently registered in Project Control configuration. The command is registered by `src/project_control/cli.py:120-122` and dispatched to `validate_native_plan` (`cli.py:297-302`), which opens the Todo service with `read_only=True`, validates and diffs the plan, and verifies the authority UUID/revision did not change (`src/project_control/mutation.py:148-177`). It requires the private Project Control config, each configured workspace's matching Todo authority, a configured trusted Skills/Todo runtime root, and a passing workflow runtime identity binding. For release-bound operation, use the installed CLI and its already-bound canonical Todo runtime; do not satisfy this prerequisite with an ambient `PYTHONPATH` or by weakening identity checks. No native command was run in this preflight.

The package's `scripts/check_package.py --native` is a separate optional check: it imports `todo_orchestrator.plan.validate_plan` for structural native validation but does not establish the live Project Control workspace diff. The checker docstring and `validation/README.md` distinguish these roles. Neither path applies a plan.

## Dependency order and blockers

The same-authority graphs pass structural checks. `PC-PA1-FAST` precedes `SK-PA1-HANDOFF`; that handoff is required before `PC-PA1-RUNTIME`. After runtime handoff, `PC-PA1-FRAMES` and `SK-PA1-RETIRE` can proceed; `PC-PA1-KNOWLEDGE` follows frames. `PC-PA1-ASSIST` and `PC-PA1-LAB` follow both frames and knowledge. `PC-PA1-QUALIFY` follows assist, lab, and the Skills retirement handoff. Cross-authority edges are evidence conditions, not Todo dependencies (`machine/cross-authority.json`); verify them before starting consumers. The two supplied plans each define a serial default lane. If adopted while other runs exist, explicitly select the new run as directed in the package README.

Native preflight is now complete: both live plans validate with all tasks added and no modifications or warnings. Remaining scheduling prerequisites are current work/ownership coordination and review of the reported stale dispatches before an authorized start. Revalidate immediately before the separately authorized apply operation. This package's 40 acceptance cases and 12 evaluation cases remain planned; the inclusion of synthetic baseline fixtures does not establish product behavior.

## Evidence locators

- `IMPLEMENTER_START.md`: intended first step and preservation constraints.
- `docs/06-fast-development.md`: staged validation and empirical experiment boundaries.
- `docs/07-acceptance-and-rollout.md`: outcome sequence, acceptance, rollout and rollback constraints.
- `docs/08-decisions-and-scope.md`: user decisions and deferred capabilities.
- `validation/README.md`: limits of package, fixture, native, and product validation.
- `machine/outcomes.json`, `machine/cross-authority.json`, both `machine/*.todo-plan.json` files: counts, dependency edges, runs and same-authority scopes.
- `scripts/check_package.py:82-105, 129-175, 207-225`: manifest, plan/dependency and source/link checks.
- `src/project_control/cli.py:120-125, 297-302`; `src/project_control/mutation.py:46-53, 148-177`; `src/project_control/workflow_binding.py:53-84`: CLI and trusted-runtime prerequisites.
