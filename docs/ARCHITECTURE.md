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

The data path is:

```text
ChatGPT -> eight read-only MCP tools -> synthesis services -> ProjectSnapshot
                                      -> transient ProjectReconciler
                                      -> request-local relationship graph
                                      -> read-only authority adapters
```

Todo computes todo-native lifecycle, supersession, checkpoint/gate relevance,
program membership, semantic anchors, and coalesced history once through its
additive `semantic` read commands. Project-control stores that result separately
from compatibility raw tables as `todo_semantic`; it does not reinterpret the
full todo lifecycle in every service. When an older installed todo lacks these
commands, raw-table behavior remains available with the explicit
`todo_semantic_unavailable` warning.

Todo's separate `semantic workflow` result is stored as `todo_workflow` and
revision-checked with status, export, and semantic state. It is the sole source
for runs, first-class lane queues and roles, authoritative dispatches, typed
blocking messages, rendezvous, managed workspaces, patch and integration state,
recovery attention, context versions, safe parallel groups, and parent-linked
local children. Project-control does not reconstruct these meanings by joining
raw workflow tables.

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

Architectural subjects are resolved by a deterministic request-local graph.
Its nodes and attributed edges come from todo relationships, registered paths
and artifacts, CUDA links, and Git identity. Exact IDs/names/aliases and
authoritative path or symbol relations precede bounded token overlap. The graph
is never persisted and ambiguous low-confidence candidates are not silently
selected.

Git remains canonical source history. A repository identity's commit identifies
committed content. `working_tree_fingerprint` is separately the SHA-256 of the
filtered porcelain status content; for a clean worktree it is therefore the
SHA-256 of empty content, not a fingerprint of the committed repository.

The ChatGPT-facing server uses stateless Streamable HTTP with JSON responses.
It binds to loopback and is exposed only through a trusted Secure MCP Tunnel.
Configuration, cache, logs, and temporary plan files live under the app's own
XDG directories. Registered repositories are opened only for bounded reads.

Security is enforced structurally: workspace IDs replace arbitrary roots,
subprocess argument vectors are fixed, paths are resolved beneath allowlisted
roots, deny patterns and text limits are applied, and every unavailable source
degrades to an explicit partial result instead of causing a repair mutation.

No filesystem monitor, project-control history database, shadow Git history,
vector index, agent-attribution service, or background observer is part of this
architecture.

Older kernels degrade explicitly to `todo_workflow_semantic_unavailable`.
Existing task/evidence observation remains available, workflow-only collections
remain empty, and no fallback invents activity or performs recovery.
