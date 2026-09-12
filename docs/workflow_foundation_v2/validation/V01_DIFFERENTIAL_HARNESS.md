# V01 differential-harness contract

`tests/wf2_validation/differential_harness.py` runs explicitly pinned old and
candidate commands in separate subprocesses. The command executable must be
absolute and each side must carry a distinct pinned identity. Each command
receives a fresh, distinct fixture root through `WF2_FIXTURE_ROOT`; the harness
rejects either direction of overlap with a registered live authority root.

Only fields named by the caller as documented nondeterminism may be removed
before comparison. A changed status, a missing event field, malformed JSON, or
a failed child process is a test failure. The inventory report records the
existing/original test count separately from newly authored scenario names so
coverage cannot be silently replaced. `run_original_suite` executes an
explicit legacy-suite command and extracts its actual nonzero test count;
scenario inventory is recorded independently.

The mandatory `runtime_source` and `runtime_digest` probe fields identify the
two pinned runtimes and must differ as an identity pair; they are excluded from
semantic comparison only after that check because relocation changes location.
