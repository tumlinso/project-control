# Adaptive, low-ceremony epics

Use a Todo task for a durable outcome, not for every action an agent can take.
An effective package states its objective, authority boundary, invariants,
acceptance evidence, meaningful alternatives, and stop conditions. It can then
inspect, implement, test, and make ordinary local retries without materializing
an inspect → implement → test → review chain as separate durable records.

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
