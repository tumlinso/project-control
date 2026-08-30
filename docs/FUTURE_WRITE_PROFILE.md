# Future mutation profiles

The Project Control **observer** profile is permanently project-read-only. Tool
schema v3's `terminal_capture` is a bounded observational execution aperture
whose only mutable state is an app-private live PTY registry; it has no project,
Git, Todo, workflow, source, worker, or performance mutation authority.

The **codex** profile already exposes one deliberately bounded mutation surface:
the exact six canonical Todo workflow tools. Those tools do not turn Project
Control into a second authority. They bind in-process to Todo Orchestrator's
`WorkflowProtocol`, where all transactions, scheduling, claims, capabilities,
completion, recovery, and SQLite semantics remain. They are never registered on
the observer.

Any future mutation beyond that workflow protocol must ship as a separate, explicit
capability profile or app release with its own authorization, threat
model, tool discovery, auditing, and user consent. It must not silently add
mutation tools to the observer, reinterpret `plan_preview` as authority, or
bypass Todo Orchestrator, Git, local-worker, CUDA, or resource ownership.
No dormant write tools or hidden project-mutation switches exist in the observer.

Project Control can emit an inert `ProposalEnvelope` containing intent, a
structured proposed change, observation preconditions, a deterministic digest,
and `authority_to_apply=false`. A future separate write profile may consume one
only after revalidating every todo semantic fingerprint and revision, workflow
fingerprint and revision, repository commit, worktree HEAD/fingerprint, run,
task/lane, context-fragment version/hash, and interface state/version/hash.
Staleness is a rejection condition, not permission to guess or recover.

No project mutation handler, placeholder endpoint, discoverable project-write
tool, feature flag, or dormant application path exists in the observer query
server.
`terminal_capture` cannot consume or apply a proposal envelope and does not
alter these future-write preconditions.
