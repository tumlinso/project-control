# Future write profile

The v1 `project-control` app is permanently read-only. If the user's ChatGPT
plan later permits write actions, they must ship as a separate, explicit
capability profile or app release with its own authorization, threat model,
tool discovery, auditing, and user consent.

That future profile may reuse the normalized read backend. It must not silently
add mutation tools to this app, reinterpret `plan_preview` as authority, or
bypass Codex, todo-orchestrator, Git, local-worker, CUDA, or resource ownership.
No dormant write tools or hidden mutation switches exist in the current server.
