# V01 differential-harness contract

`tests/wf2_validation/differential_harness.py` runs the old and candidate
commands in separate subprocesses. Each command receives a fresh fixture root
through `WF2_FIXTURE_ROOT`; the harness rejects any fixture that overlaps a
registered live authority root supplied by the caller.

Only fields named by the caller as documented nondeterminism may be removed
before comparison. A changed status, a missing event field, malformed JSON, or
a failed child process is a test failure. The inventory report records the
existing/original test count separately from newly authored scenario names so
coverage cannot be silently replaced.
