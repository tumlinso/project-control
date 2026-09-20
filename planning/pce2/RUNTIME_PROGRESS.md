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
The original development fixture did not qualify a separately packaged paired
release; the activated follow-up below supplies that bounded release evidence.

## Activated maintenance-host follow-up

The bounded maintenance route now has an owner CLI preparation surface:
`project-control admin prepare-maintenance --repo ROOT --task TASK --recipient PRINCIPAL`.
It returns the exact opaque mandate and an allowlisted, identity-derived
same-runtime operator launch packet. The operator is a trusted startup-bound
Codex stdio MCP server; an external tool-capable host starts it and makes the
public `maintain_execution` call. Project Control does not create a model
launcher or select/claim the implementer's work.

The authorization replay path now retains the already verified signed payload
with its receipt, so both first execution and same-principal receipt replay
recommend `next_task(repo_root, task_id)` for the exact repaired task.

Candidate `/home/tumlinson/.local/share/project-control/candidates/pce2-maintenance-ab630b1`
was built from Project Control `ab630b110afdb1340cb6e53762aa3906ff9b0976`
and Skills `f4d544476d7cc047a9a8d15e31a6d657f093ef53`; its identity digest is
`66e58bc15a4ec901c6ef2f612c60907a485b0ab69997b75047aa6adbd5769a85` and
manifest SHA-256 is
`60eb7bccbd44ebbf89906a7de893dfe3d2ed99db24f97973470fc29b06c911cc`.
The installed candidate passed `tests.test_maintenance_journey` in 4.461
seconds with isolated XDG state and frozen release binding. Atomic shared
launcher cutover and user-service restart succeeded; live PID `129124`
verified the manifest/digest, `/healthz` and `/readyz` returned 200, and
readiness reported 10 workspaces. Rollback is retained at
`/home/tumlinson/.local/state/project-control/activations/pce2-maintenance-20260920T162008Z`.

This qualifies separate issuer, operator, and implementer process transfer for
the bounded same-task recovery journey. Full PCE2 remains open; NF1A remains
paused.
