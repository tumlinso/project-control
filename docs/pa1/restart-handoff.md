# PA1 restart handoff — overnight operator hold

The latest operator instruction is to finish and validate ASSIST, then pause
before LAB or live qualification, save a restart handoff, and leave no background
inference running. ASSIST is accepted at semantic revision 1057 with all nine
required CPU/source gates passed; see `docs/pa1/assist-native-acceptance.json`.
Do not resume merely because a date or quiet window has elapsed.
Explicit operator resume is required; no overnight automation was created.

## Current integration boundary

Canonical checkout: `/home/tumlinson/project-control`, baseline HEAD
`1b1026845d3fff5a27ad539a386b24649ba48f68`. Changes are in the shared working
tree, not a commit. Preserve all unknown edits. The nine initially modified
files and their hashes are in `docs/pa1/execution-start.json`; they remained
unchanged at the last source check. Do not reset, clean, or overwrite Todo
state, generated projections, canonical source, worktrees or existing services.

Bootstrap is under `planning/project-assistance-v1`; the original archive is
preserved. Treat package documents as proposed requirements, with the current
operator hold taking precedence. Project Control/Todo semantic state is the
workflow authority; query it through supported tools rather than editing files
or databases.

PC FAST, RUNTIME, FRAMES and KNOWLEDGE have native CPU/source acceptance.
Skills HANDOFF, RETIRE and aggregate SK-PA1-0000 have native acceptance against
the actual installed Skills tree. ASSIST is done/validated at revision 1057;
all nine required gates passed and its claim is released. LAB,
QUALIFY and PC-PA1-0000 remain unfinished. No LAB claim has been acquired.

## Delivered source and interaction

The simple CLI is described in `docs/pa1/usage.md`: model-free status, user goal,
focus, quiet, release and independent resume controls; explicit ask/run/chat;
advisory handoff, dismissal and explicit acceptance of a suggestion as user
intent. Automatic preparation and notifications default off. A focus alone
never starts a service. Automatic work requires selected paths, explicit
permission and a finite window, and only an already running selected observer
service watches sources.

Attention uses the existing private broker and its two execution slots,
reserves six cumulative root turns and at most 300 seconds before callback,
and caps a focus at four roots, 24 turns and 900 active seconds. Missing trusted
usage retains the conservative reservation. Foreground work takes priority.
Goals and advisory notes confer no power, execution or Todo mutation authority.

Accepted ASSIST security fixes include exact selected-file scope at automatic
worker information ports, an exact selected-file host-read adapter, denied
broader automatic reads/commands, inherited sealed scopes, atomic concurrent reservations and descriptor-based no-follow
source hashing. The required gates are accepted; live usefulness remains
unqualified until QUALIFY.

## Receiver and Skills transition

Project Control owns the sole canonical `local_worker.*` implementation under
`src/project_control/local_runtime` (42 receiver files). The current receiver
manifest SHA256 is
`6c86a5721f3a08b4651d7444198a89c963ad162023b869eede43a254e8708041`,
fingerprint
`9d5c01632e6fbad11649362d20edb7b74f6606c47348879487d96e4f9119e7e7`.
Re-fetch and verify these identities on resume. Strict source binding rejects
changed hashes; installed binding additionally requires exact schema-2 release
identity. Source fixtures may clear inherited release manifest/digest pins;
installed negative checks must remain enforced. The raw source loader rejects
ambient/preloaded foreign namespaces and ignores bytecode as authority.

Skills contains forwarding/navigation material, not another implementation.
Its native transition acceptance observed the earlier 576aaf receiver manifest;
that remains historical evidence, not a qualification of today's receiver.
Fresh installed/producer binding must be verified in QUALIFY. See
`docs/pa1/handoffs/skills-retire-to-pc-qualify.json` and actual installed rollback
bundle `/home/tumlinson/.agents/skills/docs/pa1/rollback/operator-bundle`.
Portable original supplier archive SHA256:
`91ed6ba26758f026ac2d4a57a378eb0f5382539232453ff1f362df8fdddeb099`.
Restore scripts refuse unknown changes; do not force them or restore an older
Todo database over current state. Independent Todo/CUDA/ctxpp authorities and
the originally dirty catalog were preserved.

## Next authorized work after explicit resume

Refresh live PC/Skills authority and native source identities first. Request
PC-PA1-LAB explicitly through `next_task` once ASSIST is terminal and no scope
conflict exists. Use configured named Luna subagents for bounded routine work;
root retains lifecycle, architecture, integration and acceptance. No local-worker
coding delegation and no generic profile fallback. Ordinary children do not
spawn further children.

`docs/pa1/lab-design.md` is an unimplemented controller proposal. Three useful
owners are detached dirty/untracked capture, contained runner/receipts, and
experiment/candidate integration. Existing SnapshotBuilder only observes;
legacy workspace materialization writes linked Git administration and is not
suitable verbatim. `/usr/bin/bwrap` exists but actual containment has not been
probed. Verify real child restrictions before experiments, fail closed if
unsupported, persist effect intent before launch, reconcile interruption without
blind replay, and return source-bound patches without automatic canonical
promotion. Preserve foreign profiling and other active work.

QUALIFY must then execute the mandatory live-model/GPU/sandbox scenarios,
including A23 useful moving-source preparation, A31 genuine odd-tail fixture
counterexample, A33 owned inference release before GPU foreground work, A37
finite representative/held-out evaluation, A38 predeclared practical cost and
latency envelope, and A40 actual installed runtime/model/binary identity.
CPU fixtures establish none of those live properties. Keep automatic mode off
when usefulness or resource economics are unqualified. Preserve exact public
MCP tools/schemas and exact-answer cache/historical warning semantics.

Build only one selected final candidate after source/quality selection. The
existing `/tmp/pa1-wheel` wheel predates final source and is stale. Installer
source identity currently records HEAD/tree without complete dirty provenance;
its pip-from-source path needs offline dependency readiness before candidate
installation. Address these under QUALIFY's packaging scope, preserve the old
production release, and retain reversible rollback. No candidate deployment,
service restart, real inference or device computation has occurred in this
implementation session.

The adapter uses private template/tokenization/completion endpoints to enforce
reasoning and visible-stage bounds; profile policies have separate instructions,
logical context and token limits. They do not imply a reduced physical serving
KV allocation. Actual model identity/help/residency and foreground interlock
still require fresh qualification. Use the canonical host controller and exact
lease receipts; do not nest an independent same-GPU lease, infer exclusivity
from idle summaries, or kill foreign processes.

## Overnight shutdown evidence

Final evidence is in `docs/pa1/assist-native-acceptance.json`; restart state
is in `docs/pa1/restart-state.json`. A native publish_context
attempt to record the hold returned unexpected_internal_failure
(diag_RNe6jKzEIPpX1cxy); the task claim remained valid. Do not claim that
attempt updated the run charter. The operator instruction and saved controller
decisions are authoritative for this pause; the final
accepted native outcome note also records the hold. A read-only host scan on
2026-10-06 found no llama-server and one pre-existing supervisor PID 2173,
started at 06:44:02, using
`/home/tumlinson/.cache/project-control/as1-observer-analysis/runtime`.
It was not started or restarted by this work. The final scan below verifies
the same process and no model server. No automation or new background
production inference job was created.

The first native gate attempt had seven passes and two failed source suites.
The surface fixture inherited installed-release pins; inquiry had three such
errors and one timing-sensitive diagnostics error that passed isolated. Only
source-test environment fixtures changed; strict runtime guards stayed intact.
The single authorized native retry passed all nine gates. The response exceeded
8192 bytes after completion; semantic revision 1057 confirms acceptance and
claim release. No further issue search is needed.

Final host check at 19:54:43 UTC found no llama-server. Supervisor PID 2173
remained the pre-existing 06:44:02 process. No service was stopped/restarted,
no device query was made, and no new inference or production automatic focus
was started. All delegated work is complete; unused pending agents were
interrupted. See `docs/pa1/overnight-process-check.json`.

A source-only checkpoint is saved at `docs/pa1/overnight-source.tar.gz`, with
per-file hashes in `docs/pa1/overnight-source-files.json`. It contains no Git
or Todo authority directories and is not a deployment artifact.
