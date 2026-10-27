# Adaptive, low-ceremony epics

Use a Todo task for a durable outcome, not for every action an agent can take.
An effective package gives the eventual Codex root enough architectural
understanding to execute intelligently: why it matters, the current and
intended system, decisions and rationale, hard boundaries, risks, acceptance,
useful references, and deviations that need escalation. It can then inspect,
implement, test, and make ordinary local retries without materializing an
inspect → implement → test → review chain as separate durable records.

Create another task only for a meaningful durable boundary: an independently
useful outcome, dependency, ownership/worktree boundary, parallel stream,
interface freeze, integration point, materially different risk, or independent
acceptance boundary. The root chooses ordinary local decomposition, concrete
APIs, test sequence, and delegated implementation; it is not a dispatcher
following a precomputed script.

Keep conditional alternatives declarative. Record the decision domain and the
evidence that selects a branch; materialize work only after the guarded choice
is made. A join waits for the selected branch and unconditional prerequisites,
not every hypothetical alternative.

Use compact `project_overview`, `project_frontier`, and `project_delta` first.
Each reports authority, freshness, coverage, and a top-level cursor. Expand an
exact cursor only when a decisive fact is absent; stale cursors require refresh,
never silent substitution. Use `source_context` for a bounded set of exact
paths or symbols. Broad research is worthwhile only when its compact digest is
cheaper than direct root investigation; include conclusion, source identity,
re-fetch references, counterevidence, and coverage limits.

Use semantic context fragments as a hierarchy, not a second task plan:
`run_charter` carries overall purpose, end state, and global boundaries;
`lane_brief` describes an outcome stream and coordination relationship; and
`task_brief` gives the durable local outcome, constraints, acceptance, and
delegated choices. Keep deeper source/design/research references fetchable on
demand. Generated briefs carry execution context; they do not prescribe
microtasks.

An authorized root or head may preserve a non-obvious, expensive-to-rediscover,
future-useful discovery as an authored context note anchored to the relevant
project, repository, path, symbol, task, run/lane, interface, or decision.
It is non-authoritative context, not progress, coordination, scratch reasoning,
or routine test output. Promote a commitment to a decision, invariant, or
interface instead. Retain source identity when useful so readers can assess
staleness; do not silently delete stale notes.

Administrative lifecycle is deliberately cheap but remains controlled: a root
controller, or a parallel head explicitly authorized by that root, may issue a
single scoped prepare, recovery, or integration operation and retain its
deterministic receipt. A worker may perform that bounded operation only when
the root/head delegated it explicitly. Multi-step ceremony is reserved for
destructive, ambiguous, or conflicting state, not routine preparation.

## Example

`CEL-INGEST` owns a durable outcome: import a sample format while preserving
deterministic IDs and the compatibility reader. Its planner records two
alternatives: use the existing parser if a bounded fixture demonstrates lossless
mapping, otherwise add a format adapter. The owner checks the fixture, records
the chosen branch, asks one scoped worker to implement the selected change, and
runs the package's normal tests. One consolidated completion attaches the
commit, test result, decision evidence, and any remaining compatibility risk.

No separate durable tasks are needed for the fixture inspection, worker start,
unit-test invocation, or review of an unambiguous patch.
