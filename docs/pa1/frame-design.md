# Controller decisions for resumable assistance

These are root integration decisions for the next PA1 outcome. The root owns
them because scheduling, public contracts, capability boundaries and runtime
policy selection cross worker ownership. They are not executed qualification.

The existing JobService remains the scheduler and owns its two execution
threads. Add versioned private continuation/wait metadata to its SQLite store;
do not create a frontend pool or a Todo scheduler. Keep public inquiry identity,
literal retries, original deadlines and the two-executing/four-waiting admission
contract. Internal descendants have separate bounded credits and cannot exhaust
the public waiting queue. Hide private descendants from public polling, inquiry
indices and logs. All internal dispatch uses the parent's intersected trusted
scope and current generation, never a model-supplied capability.

Use the current validated observations/checkpoints to reconstruct a worker
slice. Count successful model turns cumulatively across slices; ordinary yields
must not renew deadlines or reset the six-round public baseline. Distinguish
normal slice generations from failed execution retries. Stop after a completed
observation or terminal result; do not replay accepted tools. A dependency is a
typed private proposal, not a new public MCP tool or permission to run code.

Persist the parent checkpoint and exact wait/child intent before making child
work runnable. The parent keeps execution occupancy until its session release
is verified. Only then may children run. Reconciliation checks durable terminal
versions, so completion before wait registration and duplicate delivery wake
the same parent generation once. Reject cycles, stale generations, unauthorized
scope expansion and depth/credit exhaustion. Start with depth two, four children
per admitted root and a bounded global private frame cap. Failed children wake
parents with explicit failure evidence. Cancellation and expiry reject late
results as current output.

An independent watchdog handles deadline marking and reconciliation while
execution threads are blocked. Logical expiry and physical cleanup are separate:
unproved cleanup continues occupying resources. Never hold a database
transaction while acquiring resources, invoking inference or waiting on tools.

Trusted immutable versioned policies select gather/synthesis/experiment-plan
behavior and logical context/reasoning/visible limits. A private policy ID maps
to a validated registry; caller-supplied numerical settings cannot widen it.
Extend the supervisor's request-local turn contract rather than mutating a global
generation setting. Preserve physical weights, serving context and central
owner/lease identity. Record requested, effective and used budgets separately;
store visible context and operational summaries, not hidden reasoning.

Persist power state in the same private controller store. Demand-only is the
default. Quiet suppresses automatic roots and descendants; an inference-release
veto prevents rewarming until explicit resume. Status is read-only. Foreground
work precedes opportunistic work, which may occupy at most one model slot.
Preemption occurs at validated slice/effect boundaries; no instant-cancellation
promise or release accounting before owned-process cleanup is proved.

The later lab shares typed wait/effect identities with this controller. An
effect requires a durable intent and current explicit scratch grant. Missing
containment fails closed. Ambiguous interrupted effects stay interrupted or
unknown; recovery does not blindly replay them or apply canonical patches.
