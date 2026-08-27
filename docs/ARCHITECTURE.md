# Architecture

`project-control` is a read-only architectural control plane between ChatGPT
and registered engineering workspaces. The server resolves a stable workspace
ID through its owner-only local registry, gathers bounded facts through explicit
read adapters, normalizes one project snapshot per request, and synthesizes a
compact typed result.

Authority remains external. Todo controls plans, claims, gates, checkpoints,
interfaces, and decisions. Git and canonical source control source identity.
The local-worker and CUDA systems control their own lifecycle and resources.
Project-control never repairs or initializes those authorities.

The v2 data path is:

```text
ChatGPT -> fourteen read-only MCP tools -> synthesis services -> ProjectSnapshot
                                      -> transient ProjectReconciler
                                      -> multi-seed relationship/context graph
                                      -> disposable lexical source index
                                      -> read-only authority adapters
```

Todo computes todo-native lifecycle, supersession, checkpoint/gate relevance,
program membership, semantic anchors, and coalesced history once through its
additive `semantic` read commands. Project-control stores that result separately
from compatibility raw tables as `todo_semantic`; it does not reinterpret the
full todo lifecycle in every service. When an older installed todo lacks these
commands, raw-table behavior remains available with the explicit
`todo_semantic_unavailable` warning.

Todo's separate `semantic workflow` result is stored as `todo_workflow`. It is
authoritative at its own revision and is never discarded because status,
semantic state, or export observed a different revision. It is the sole source
for runs, first-class lane queues and roles, authoritative dispatches, typed
blocking messages, rendezvous, managed workspaces, patch and integration state,
recovery attention, context versions, safe parallel groups, and parent-linked
local children. Project-control does not reconstruct these meanings by joining
raw workflow tables. Semantic task state is independently authoritative at its
own transaction, while export is durable enrichment at its own revision.

Every authority read is a component observation with availability, operation,
revision, authority fingerprint, project UUID, observation time, stable source
identity, bounded error code, and revision skew. Matching revisions and
fingerprints are reported as consistent. Mismatches remain usable and are
reported as skew; project-control never pretends independently invoked commands
form one atomic snapshot and never waits for active agents to become quiescent.

First-class dispatches and subordinate local executions are different graph
node types. A first-class agent appears only when todo's normalized read proves
the live session/dispatch/claim/lane/context tuple. A local child remains linked
to its parent task and lane where known, but never enters the lane tree or
run-level fan-in.

`ProjectReconciler` combines the semantic work view with Git identity,
registered artifacts and gate evidence, CUDA facts/results, and observable
worker/host state. It assigns current/reference/historical/superseded relevance,
filters stale attention, checks cross-output consistency, and ranks current
judgment before any byte budget is applied. Overview, frontier, delta,
inspection, evidence, performance, and planning all consume this shared
transient view.

Focused `inspect` subjects are resolved by a deterministic request-local graph.
Its nodes and attributed edges come from todo relationships, registered paths
and artifacts, CUDA links, and Git identity. Exact IDs/names/aliases and
authoritative path or symbol relations precede bounded token overlap. The graph
is never persisted and ambiguous low-confidence candidates are not silently
selected. `architecture_context` deliberately uses a different multi-seed
algorithm: exact and lexical candidates are clustered by workflow, planning,
architecture, source, validation, evidence, and performance themes, expanded
through attributed graph relationships, and ranked without collapsing a broad
question to one entity. Results label authoritative facts, derived
relationships, heuristic relevance, inference, and missing evidence.

Git remains canonical source history. A registered repository is one Git common
repository, not one checkout directory. Read-only discovery uses
`git rev-parse --git-common-dir` and `git worktree list --porcelain`; every
verified worktree receives a stable opaque ID, HEAD, branch/detached state,
dirty paths, and working-tree fingerprint. Absolute worktree paths are internal
diagnostics only. A repository identity's commit identifies
committed content. `working_tree_fingerprint` is separately the SHA-256 of the
filtered porcelain status content; for a clean worktree it is therefore the
SHA-256 of empty content, not a fingerprint of the committed repository.

The ChatGPT-facing server uses stateless Streamable HTTP with JSON responses.
It binds to loopback and is exposed only through a trusted Secure MCP Tunnel.
Configuration, cache, logs, and temporary plan files live under the app's own
XDG directories. The derived context/index cache lives under
`$XDG_CACHE_HOME/project-control/`, is keyed by explicit source and authority
identities, is disposable, and is never source, workflow, history, or
performance authority. It uses structured relationships and lexical retrieval;
there is no vector database, filesystem monitor, or shadow event store.
Registered repositories are opened only for bounded reads.

Security is enforced structurally: workspace IDs replace arbitrary roots,
subprocess argument vectors are fixed, paths are resolved beneath allowlisted
roots, deny patterns and text limits are applied, and every unavailable source
degrades to an explicit partial result instead of causing a repair mutation.

No filesystem monitor, project-control history database, shadow Git history,
vector index, agent-attribution service, or background observer is part of this
architecture. The optional observer-analysis seam defaults to unavailable and
cannot create claims, children, lanes, GPU work, or source edits.

Older kernels degrade explicitly to `todo_workflow_semantic_unavailable`.
Existing task/evidence observation remains available, workflow-only collections
remain empty, and no fallback invents activity or performs recovery.

## Read-only future-write seam

Every new high-level response carries `ObservationPreconditions`: workspace and
project identity, todo and workflow revisions/fingerprints, repository commits,
worktree IDs/HEADs/fingerprints, relevant run/lane/task identities, versioned
fragments and interfaces, observation time, and provider skew. An inert
`ProposalEnvelope` may bind a proposed change to those observations with a
deterministic digest. It always states `authority_to_apply=false`.

The read server has no mutation handler, write-capability negotiation, dormant
write flag, or hidden apply path. Any future write-capable profile is a separate
application or explicit capability profile and must revalidate every
precondition against live authorities before doing anything.
