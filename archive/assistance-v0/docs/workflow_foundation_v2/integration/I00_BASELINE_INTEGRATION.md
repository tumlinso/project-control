# I00 baseline integration receipt

`PC-WF2-I00` integrates the reviewed A03, C03, and V01 producer commits on
the phase base `23c177371860018574f61f327df4af262de6af56`.  The authoritative
integration queue and gate evidence remain in Project Control; this source
receipt makes the developmental boundary reviewable without becoming a
second workflow authority.

## Test discovery

The dependency-free `scripts/run_wf2_acceptance.py` discovers every lane
directory under `tests/wf2_acceptance`; it is the stable program-level test
entry point even when pytest is not installed. The integrated baseline suites are present under `tests/wf2_acceptance/a`,
`tests/wf2_acceptance/c`, and `tests/wf2_acceptance/v`; the I-lane guard is
`tests/wf2_acceptance/i/test_i00_integration.py`.  The guard fails if those
roots or their required test modules disappear.

## Interface publication

`PC-WF2-IF-AUTHORITY` version 1 is owned by completed producer
`PC-WF2-C03`; its contract is
`docs/workflow_foundation_v2/contracts/authority.md`.  Publication is a
root-owned lifecycle call against this committed integration source.  The
Todo interface record—not this document—holds the frozen state and content
hash.

## Release preservation

This milestone creates developmental source only.  It does not swap a
launcher, restart the deployed release, relocate either Todo authority, or
perform the later I50 cutover.  The frozen control runtime remains
`/home/tumlinson/project-control/.venv-codex-live-links-70b863a/bin/python`.
The integration commit changes only WF2 documentation, schemas, and tests;
production release paths remain unchanged.
