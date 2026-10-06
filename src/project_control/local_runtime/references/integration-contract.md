# CORE4 integrated flow v1

`local_worker.py integrate` accepts one `CORE4-INTEGRATION-REQUEST/1`. The
request names the parent todo task and claim, bounded child scopes, and either a
read-only terminal execution or a writable fake-backend execution. The parent
claim token and restricted child token are never returned.

The controller uses public, frozen boundaries only:

1. `todo child create`, heartbeat/report, and status retain work authority.
2. The terminal worker requests one bounded `CTXPP-CONTEXT-PACKET/1` and uses
   isolated source state.
3. Writable work starts from `source-identity-v1`, runs baseline and external
   verification in a detached worktree, and emits a scoped content-hashed patch.
4. Parent-side guarded acceptance rejects stale or conflicting source and runs
   current-source verification. It never completes the parent task.
5. Only an accepted patch is submitted to read-only CUDA registry discovery.
   Healthy no-change, `NEEDS_CODEX`, stale, failed, and preempted outcomes remain
   silent and never auto-queue CUDA work.

`NEEDS_CODEX` is a successful terminal hand-back. Preemption does not discard
todo state or alter canonical source. CUDA discovery returns matching campaign
identities only; it does not rank ambiguous matches, acquire GPUs, or run a
benchmark. No path downloads models, installs harnesses, or starts recursive
agents.

The compact `CORE4-INTEGRATED-RESULT/1` contains task and child identities,
terminal status, summary, changed paths, guarded acceptance state, CUDA campaign
IDs, and `parent_task_completed=false`.

V2 is additive. `CORE4-INTEGRATION-REQUEST/2` selects a fixture or measured
real execution in its `execution` object. `LCW-REQUEST/2` then composes the
task-oriented packet, authorized read-only snapshot, persistent supervisor,
bounded harness, and a compact normalized result. Each task evicts its one-shot
harness and releases the compatible llama.cpp endpoint to its idle TTL; it does
not evict the shared server. V1 remains the deterministic fake fixture.

For ordinary read-only or writable delegation, prefer the public `delegate` command.
Expert `integrate --request` and `run --request` surfaces remain available for
deterministic fixtures and explicit integrations.

The public nonblocking `delegate` path freezes its bounded semantic packet
before supervisor admission. This ordering is part of the admission contract:
packet preparation, then GPU admission, then detached launch, then todo child
creation. A detached real request therefore consumes a prepared packet rather
than discovering an absent index after it has acquired work authority.
