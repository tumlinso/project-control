# Bounded PCE2 candidate

This record preserves the bounded releases below. They do not complete all
six PCE2 outcomes or all 24 acceptance groups. NF1A remains paused.

## Current guidance and run-focus release

Activated on 2026-09-20 from Project Control
`56da5f34426b9d607cb568a17a5f004a267a5ad6` and Skills
`6cf647f66bdd37668dfa219f7c7816192aae5c65`:

- Candidate: `/home/tumlinson/.local/share/project-control/candidates/pce2-focus-56da5f3`.
- Candidate identity: `e9d6de3ea3cf142f81d0021a198e2ecfaf4239c7ab74f79fab6772701a7cad4d`.
- Manifest SHA-256: `ed99177ad31cfa06ca4d29d533a6201301704786b46b04fcc6c26c58b04c7d67`.
- Rollback record: `/home/tumlinson/.local/state/project-control/activations/pce2-focus-20260920T171604Z`.

The short assessment in `REMAINING_SCOPE.md` selected this bounded follow-up.
Guidance permits direct read-only research, uses inspection only for missing
context, and explains completion's required validation. Durable server
instructions assign reasoning, synthesis and meaningful decisions to the main
thread; subagents gather evidence, execute tightly scoped assignments and
report back for direction. `collect_delegation` is correctly annotated as
mutating. Optional `next_task(run_id=...)` constrains both resume and claim;
ambiguous task-only focus returns choices without a committed mutation, with
the check repeated inside the claim transaction.

Code commits are PC `11d5ad5` and Skills `27c59e9`. Focused validation passed:
21 PC tool/profile tests, 18 Skills protocol tests and 12 lane-resume tests.
The installed paired candidate passed the public entry/bind/complete journey
with explicit run focus (1 test, 2.219 seconds). Fresh stdio discovery verified
the optional run selector, mutating collection annotation, and main-thread
reasoning instruction. The live service's manifest/digest matched this
candidate; health and readiness returned HTTP 200 (10 workspaces). Root
performed release cutover and final integration; previous qualification was
reused rather than repeated. Existing MCP connections need reconnect to load
the new schema and instructions.

The maintenance evidence below belongs to the previous release and is
retained as supporting evidence, not represented as a newly repeated test.

## Previous maintenance candidate

- Historical bounded candidate: Project Control
  `f4160ce4e274bbfb82c71d7db8330cf9db3002ac`, Skills
  `9cf3c019d98568fb5e355e972a488cf623f354f7`, identity digest
  `0a15f5e6b41af1d738ea1fcde0c021b99c63702daefd59f6630ff82f982b2025`.
- Maintenance candidate:
  `/home/tumlinson/.local/share/project-control/candidates/pce2-maintenance-ab630b1`;
  Project Control `ab630b110afdb1340cb6e53762aa3906ff9b0976`, Skills
  `f4d544476d7cc047a9a8d15e31a6d657f093ef53`, candidate identity digest
  `66e58bc15a4ec901c6ef2f612c60907a485b0ab69997b75047aa6adbd5769a85`,
  manifest SHA-256
  `60eb7bccbd44ebbf89906a7de893dfe3d2ed99db24f97973470fc29b06c911cc`.

## Previous maintenance activation

The maintenance candidate was activated through the digest-checked atomic shared
launcher replacement. The retained rollback record is
`/home/tumlinson/.local/state/project-control/activations/pce2-maintenance-20260920T162008Z`.

The effective user service unit was preserved and restarted successfully. Live
PID `129124` resolved to that manifest/digest; `/healthz` and `/readyz`
returned HTTP 200, with readiness reporting 10 workspaces. Existing connected
Codex MCP clients require reconnect; the active stdio process cannot be
hot-switched. Ordinary Codex remains fail-closed for maintenance until a
trusted host starts the explicitly bound operator.

The earlier bounded build directories are historical and superseded. The prior
launcher remains available for rollback.

## Executed public journeys

The historical candidate Python ran `tests.test_pce2_workflow_journey` and
`tests.test_maintenance_journey` together: **2 tests passed in 4.219 seconds**.
The maintenance candidate ran `tests.test_maintenance_journey`: **1 test
passed in 4.461 seconds** with frozen release binding and isolated XDG state.

1. Ordinary entry supplies its work packet without recommending a mandatory
   inspection. The caller adds required gates through `coordinate_task`,
   repeats the same binding without advancing the revision, validates, and
   completes through public MCP tools.
2. A trusted host prepares a scoped maintenance assignment. An unconfigured
   host and a differently bound operator are rejected. The intended operator
   calls public `maintain_execution`, then public `next_task` claims the same
   repaired task. No post-recovery lane edits or private completion helper are
   used. Assignment reports `launch_required`; no model launcher is invented.

The maintenance qualification used separate issuer CLI, trusted operator MCP, and
ordinary implementer MCP processes. It exercises the emitted verified launch
packet and the exact returned same-task `next_task` recommendation. This is not
evidence that a paid model or installed external agent launcher was exercised:
an external tool-capable host still starts the operator.

## Supporting changes and evidence

- Runtime diagnostics and bound read-only plan previews are committed in
  `88483dc`; the final changed preview paths passed 19 focused tests plus the
  7-test real preview module.
- Ordinary action projection and context continuation are committed in
  `497a878`; the reviewed protocol corrections passed 18 protocol tests,
  following the broader 50-test entry/context run.
- Live required-gate binding and conservative file-check evidence reuse are
  committed in `f423882`; 38 focused gate/audit/protocol tests passed.
- Maintenance and explicit startup principal binding are committed in
  `a1c2bb8` and `92594f6`; 27 focused maintenance/profile/tool tests passed.
- Capability resolution no longer scans unrelated locator hints. Read-only
  validation derives the authoritative session root before writable access.
  Commits `8067958` and `9cf3c01` passed 20 integration/isolated-worktree tests,
  including a stale sibling-worktree hint regression.
- Focused independent review identified authorization, replay, scope,
  fingerprint and provenance defects; those findings were corrected before
  the final candidate qualification. Tests were rerun when affected code or
  runtime identity changed, not as repeated unchanged validation.

## Remaining scope

Full PCE2 qualification remains open. In particular, there is no Project
Control-owned model launcher, no general amend/retire/supersede flow,
and no proof covering every large-context, explicit-run-focus, delegation,
delivery or adverse lifecycle acceptance scenario. Ordinary command,
resource, and managed-workspace gates still rerun: only fingerprinted
`file_exists`/`pattern` evidence outside managed workspaces is reused.
Identical gate binding is a serial no-op, not a new transaction-level
contention primitive.

The existing exact-target recovery bridge was reused to prove a useful
maintenance path before attempting a larger kernel mandate system. This is an
intentional adaptation of PCE2, preserving its purpose rather than expanding
infrastructure for its own sake. It does not declare the Skills maintenance
outcome complete.

All-agent provider usage was not available as one complete meter. No measured
token-savings claim or verified aggregate-budget certificate is asserted.
