# Local worker resource policy

`AdapterService` resource coordination is opt-in. Construct it with a host
resource coordinator and project root, then include `resource_request` in the
start context. Existing contexts and adapter lifecycle calls behave unchanged.

A service starts as active local delegation and becomes idle model residency
after startup and between runs. While a bounded run is executing it is promoted
to active local delegation. A host preemption signal is handled cooperatively by the service heartbeat:
the adapter is drained, evicted, and its physical lease is released. A request
that has not started returns `NEEDS_CODEX`; completed adapter output is retained
if eviction arrives after the run. Todo task and evidence state are never owned
or modified by this service layer.

Resource acquisition does not download a model or start an adapter until the
runtime-discovered bundle is actually reserved. There are no recursive agents
and no hard-coded GPU indices or topology assumptions.

The production supervisor owns a demand-driven service pool whose configured
capacity is capped at two. Each slot is one llama server, one runtime-discovered
GPU island, and at most one active service lease. An acquire reuses a compatible
idle slot before lazily creating another slot; it never preloads the second
model merely because another island exists. A third concurrent acquire returns
a retryable resource-unavailable result instead of creating a queue. Cold model
starts are serialized, while inference on already-resident disjoint slots is
independent. Each successful acquire returns a slot ID and service lease ID;
qualified release, idle expiry, failure, and preemption affect only that slot.
The legacy unqualified release is accepted only when exactly one active lease
exists.

Production promotion requires two disjoint runtime-discovered islands, two
overlapping accepted public delegations, hot PID reuse without another model
load, third-worker rejection, selective and global CUDA preemption, at least
4 GiB free VRAM on every GPU with both services resident, and at least 8 GiB
host `MemAvailable`. If any dual-only guard fails, the implementation remains
available but production capacity stays one.

Todo remains the authority for task independence and writable-scope conflicts.
The supervisor only allocates service/GPU capacity. Independent workers retain
separate child tokens, source snapshots or worktrees, Qwen runtime directories,
budgets, and service leases. Selective CUDA preemption drains only overlapping
slots; a foreground reservation spanning both islands drains both. If runtime
discovery yields one suitable island, the ordinary single-worker path is
unchanged.

`CORE4-MODEL-SERVICE/2` profiles bind a verified model hash to the runtime
allocation and llama.cpp launch settings. GPU UUIDs are supplied through
`CUDA_VISIBLE_DEVICES`; supported server flags are detected once from the
installed binary. Startup is health-gated, output is retained at the declared
log path, eviction terminates the process group, and bounded idle TTL plus
`wait_for_quiescence` make VRAM release observable.
