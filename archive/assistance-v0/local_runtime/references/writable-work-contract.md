# Writable work contract

Writable delegation begins from a current `source-identity-v1` record and
repository-relative write scopes authorized by the parent todo child execution.
It never grants task completion, scope expansion, commit, push, architecture,
or recursive-agent authority.

Materialization creates a detached temporary Git worktree at the exact commit,
applies tracked dirty bytes, copies untracked dirty files, and verifies declared
dirty-path content. A separate temporary index records the full initial tree,
including dirty overlay state. Baseline commands must pass before worker work.

After the worker, external verification must pass. The patch is the delta from
the temporary baseline tree, not from `HEAD`; existing user changes therefore
are not attributed to or replayed by the worker. Every changed path must remain
inside the declared write scopes. Patch bytes and metadata are stored as
content-hashed evidence outside the source worktree.

Acceptance is a parent-side operation. It requires the primary worktree's
source fingerprint and commit to still match the materialization identity,
rechecks patch scope and hash, and runs `git apply --check` before applying.
Current-source acceptance commands then run in the primary worktree. On gate
failure the patch is reversed; on stale source or conflict nothing is applied.
Successful acceptance still does not complete the parent todo task.

The public entry point is `local_worker.py delegate --claim-token
"$CLAIM_TOKEN" --mode writable --wait --json`. It derives write scopes and
focused command gates from the todo capsule, creates a write-access child, and
runs Qwen without shell access in the detached worktree. Actual Git state—not
the model's path claims—defines the patch. After guarded application, each todo
gate runs against current canonical source with the child token; the parent
then credits that exact evidence with `gate run --accept-child` and performs
ordinary `child accept`. Any gate or credit failure reverses only the candidate
patch and rejects the child.

Delegation scope translation selects at most 16 relevant existing write paths
inside the parent exclusive scope. `--objective` narrows the work request but
does not become a ctxpp symbol; a separate target is emitted only for a proven
authorized file path or an explicitly supplied bounded symbol.
