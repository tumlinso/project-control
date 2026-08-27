# Future write profile

The `project-control` read app is permanently read-only. If the user's ChatGPT
plan later permits write actions, they must ship as a separate, explicit
capability profile or app release with its own authorization, threat model,
tool discovery, auditing, and user consent.

That future profile may reuse the normalized read backend. It must not silently
add mutation tools to this app, reinterpret `plan_preview` as authority, or
bypass Codex, todo-orchestrator, Git, local-worker, CUDA, or resource ownership.
No dormant write tools or hidden mutation switches exist in the current server.

Project Control 0.2.0 can emit an inert `ProposalEnvelope` containing intent, a
structured proposed change, observation preconditions, a deterministic digest,
and `authority_to_apply=false`. A future separate write profile may consume one
only after revalidating every todo semantic fingerprint and revision, workflow
fingerprint and revision, repository commit, worktree HEAD/fingerprint, run,
task/lane, context-fragment version/hash, and interface state/version/hash.
Staleness is a rejection condition, not permission to guess or recover.

No mutation handler, placeholder endpoint, discoverable write tool, feature flag,
or dormant application path exists in the read server.
