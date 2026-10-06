# Controller design for the bounded scratch laboratory

This is a proposed integration contract for the next native LAB outcome, not
implemented or qualified behavior. The controller retains workflow authority.

Use three bounded source owners: detached source capture, contained execution,
and experiment/candidate integration. Do not reuse linked Git worktrees as the
lab's writable workspace. Preserve canonical tracked dirty and untracked source,
Git administration, Todo state, and unrelated work. Capture selected registered
source into a private detached copy, reject unsafe paths and symlinks, exclude
credentials, service state, Git/Todo bookkeeping and generated outputs, and
recheck enumerated bytes and source identity after copying. A racing edit
defers the experiment rather than resetting the canonical tree. Preserve a
deterministic source manifest with paths, modes and content hashes.

The default grant is explicit trusted operator input for one registered project
and source selection, CPU-only, no network or host sockets/credentials/devices.
Model and source text can propose a typed experiment but cannot grant execution.
Start with one CPU, 1 GiB memory, 32 processes, 30 seconds wall time, 64 KiB
captured output, 64 MiB writable artifact ceiling, and 50 MiB source capture.
Validate all numbers as finite and reject excess scope before materialization.
GPU work requires an explicit resource grant and the existing foreground CUDA
interlock after owned inference cleanup has been verified; it must never nest a
second competing lease or terminate foreign profiling work.

Use a private process sandbox with a read-only detached source snapshot, writable
experiment directory and private home/tmp/cache. Clear inherited environment,
drop capabilities and isolate network, process and mount namespaces. Verify
actual child-process denial of outside writes, sockets, network, credentials
and device visibility. Unsupported containment fails closed; never substitute
uncontained execution. Bound resources, output and descendants, and terminate
only the exact owned process group with verified process identity.

Persist a private effect intent and exact generation/source/grant identity before
launch. Record launch ownership, bounded result, elapsed time, exit status,
executed test count and artifact hashes. After interruption, reconcile ownership
and mark an ambiguous effect unknown; do not replay it blindly. Completion is
empirical evidence with explicit coverage and uncertainty, not canonical truth.
Negative and inconclusive results remain visible.

Return patches as unpromoted candidates with exact captured base and current
source checks. Separate explicit parent acceptance and verification are required
before any canonical edit. No lab operation publishes Todo declarations or
calls the existing canonical patch-acceptance path automatically. Source,
tests, effect receipts and model/binary identities remain distinct witnesses.
