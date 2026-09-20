# PCE2 runtime bounded progress

Base source: `f894427c8fd2d3a73f641f946074b1ecc22e33ef`.

This slice adds local `project-control doctor --json` diagnostics for one
verified runtime, an unconfigured runtime, and an identity mismatch. The
diagnostic reports a precise supported action and never emits ambient process
environment values. It also labels Project Control's local-worker surface as
an observation adapter and reports that Project Control has no owned
tool-capable launcher; this does not classify Todo-managed executors.

Validation after the final bounded edit:

```text
uv run python -m unittest tests.test_runtime_identity tests.test_cli_profiles tests.test_domain_adapters
```

Final result: 29 tests passed; `git diff --check` passed. Earlier corrective
runs caught missing imports and an incorrect expectation about the existing
doctor exit status; both were corrected before the final pass. No deployment
was activated. The tested runtime changes are saved in the focused Git commit
`Unify plan preview runtime and expose actionable diagnostics`.

The suite includes public `doctor --json` coverage with a controlled fake Todo
runtime and configuration. J06 still lacks a real configured tool-capable
launcher/grant invocation.

## Bound preview routing follow-up

Validate and handoff preview now uses Todo's existing verified in-process
read-only `Service.plan_validate` and `Service.plan_diff` when Project Control
has a bound runtime. The compatibility subprocess route remains only for an
unbound setup. A missing Todo revision returns `todo_authority_unavailable`
before preview constructs a provider, native service, or temporary proposal.

Focused validation:

```text
uv run python -m unittest tests.test_todo_authority tests.test_workflow_binding tests.test_runtime_identity tests.test_cli_profiles
```

This ran 42 tests successfully before review corrections. Final corrections
were validated with `uv run python -m unittest tests.test_todo_authority
tests.test_workflow_binding` (19 passed), covering native read-only service
selection, lazy reader construction, required bound refresh, and runtime
binding. The final public integration module below passed 7 tests separately.
J20 still needs complete
public-route paired-runtime and missing-authority acceptance beyond this slice.
No complete all-agent usage meter was available, so this record makes no
measured-savings claim.

## Bounded maintenance launch seam

The Codex startup profile now exposes `maintain_execution` for one exact,
host-issued clean-stopped recovery. Its opaque authorization record is signed,
project/task/revision/fingerprint scoped, bound to the trusted startup
principal, expires, can be revoked before use, and retains only a private
completed receipt for an authorized lost-reply replay. The model cannot supply
or change the principal, issue a mandate, or promote a local code child into a
maintenance host. Observer and mutator profiles do not register the tool.

`prepare_maintenance_assignment` is a host-only helper. It creates the scoped
mandate and returns `launch_required` with the objective, preservation
constraints, scope and executable public next call. Project Control still owns
no tool-capable launcher, so it never claims that this assignment has started
a process. Recovery remains the installed Todo `RecoveryEngine` operation;
live, unknown, dirty and stale/nonexact targets remain rejected by its fresh
inspection and lock.

This is a finite RUNTIME increment, not a general grant framework or a claim,
workspace, retirement or scheduler rewrite. Full disposable-process public
journey qualification remains broader than this slice. The focused paired
subprocess journey is covered by `tests.test_maintenance_journey`: a clean
stopped writable implementer lane receives `launch_required`, maintains its
same target through the public maintenance seam, then canonical public
`next_task(task_id=target)` claims that target. It uses the local Skills
recovery fixture and a fresh process with the verified paired runtime; no live
authority or donor is touched.

The actual paired runtime public journey runs in a test-owned fresh subprocess
with the installed Skills source on `PYTHONPATH` and
`PROJECT_CONTROL_SKILLS_ROOT`, against a freshly bootstrapped temporary
repository authority. The ordinary focused command is:

```text
uv run python -m unittest tests.test_plan_preview
```

All 7 tests passed. The added case exercises public MCP `plan_preview` validate
and handoff through the strict bound runtime, verifies the native Todo service
result shape, and confirms the disposable authority's Git and Todo identities
remain unchanged.

Focused independent review found no remaining material correctness issue.
The tests bind the local Skills source; a separately packaged paired release
is not yet qualified. No deployment or live donor mutation was performed.
