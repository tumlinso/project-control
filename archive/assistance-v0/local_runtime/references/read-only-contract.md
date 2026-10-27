# Read-only worker contract

`local-coding-worker` is a bounded executor, not a task system or architect.
Todo-orchestrator owns authorization and completion; ctxpp packets are routing
evidence; canonical repository source is code authority.

The controller accepts one `LCW-REQUEST/1`, validates deterministic eligibility,
heartbeats the child token through the public todo CLI, and requests one
`CTXPP-CONTEXT-PACKET/1`. Only declared repository-relative scopes are copied
to a temporary snapshot. Symlinks are rejected and all copied paths have write
bits removed before the backend sees them. The snapshot is deleted afterward.

The read-only roles are `explain`, `debug`, `review`, and `test_plan`. The
deterministic `fake` backend remains supported; production v2 uses one bounded
Qwen invocation through the persistent local-model supervisor. There is one
backend call and no recursive delegation or general agent loop.

The controller reports `succeeded` for normalized `no_change` and reports
`needs_codex` when canonical target freshness, semantic relationship trust, or
packet coverage is insufficient. `NEEDS_CODEX` is a successful hand-back for
frontier judgment. The result always has an empty `changed_paths` array and
never contains credentials.

The normal production entry point is `local_worker.py delegate --claim-token
"$CLAIM_TOKEN" --mode readonly --wait --json`. It derives the objective,
1–16 relevant scopes, and required gates from `todo context`, creates a read-access child,
and keeps that child alive while routing and validating the result. Bounded
packet, model-outcome, worker-result, and telemetry evidence is stored under
the Git common directory. The parent token, whole ledger, global Qwen state,
and previous transcripts are never placed in the model prompt.

The additive `--objective` argument narrows a delegation without asserting a
literal ctxpp target. A ctxpp path is included only when an existing file can be
proven inside the parent scope, including a relevant selected source-file scope;
otherwise public nonblocking delegation returns `not_eligible` before GPU
admission or child creation.

Public nonblocking delegation also performs ctxpp packet preflight before GPU
admission. For a configured repository whose semantic index is absent, one
Git-common lock serializes idempotent initialization and a bounded scan of the
proven source target. The resulting hash-verified, semantic, scope-contained
packet is carried into the detached child and revalidated there. If preparation
or validation fails, the result is `not_eligible` with no admission, child,
scope lease, or model process.
