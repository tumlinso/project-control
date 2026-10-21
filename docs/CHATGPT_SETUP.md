> The observer is the read-only Project Control front door. It retains its
> existing 11-tool catalog; use the live MCP manifest to verify the active
> names and schemas after deployment.

# ChatGPT observer setup

This connects ChatGPT to the read-only Project Control observer over the
loopback HTTP endpoint. Keep the listener bound to `127.0.0.1`; expose it to a
remote client only through an explicitly configured, authenticated tunnel.

## Use the local observer

The standard `project-control.service` serves the release selected by
`~/.local/state/project-control/current`. Preserve its profile, principal
binding, workspace configuration, port, and state locations when operating the
service. Check the active service and endpoints with:

```sh
systemctl --user daemon-reload
systemctl --user restart project-control.service
curl --fail http://127.0.0.1:8768/healthz
curl --fail http://127.0.0.1:8768/readyz
```

The MCP endpoint is `http://127.0.0.1:8768/mcp`. Readiness covers valid core
configuration and the workflow engine. Inference and optional domain content
have separate status and are not prerequisites for core reads. To verify the
real surface, reconnect the MCP client, list tools, and make one read-only call
for a registered project.

For explicit source development, use `scripts/pc-dev setup` and its source
commands from the checkout. This does not change the sealed release selected by
the service or its `current` pointer. Do not point the production observer at a
development checkout as part of routine source testing.

## Connect ChatGPT

These steps require the user's OpenAI account and are performed by the account
owner:

1. Enable Developer Mode in ChatGPT and create or select an authenticated MCP
   tunnel.
2. Configure the tunnel client to forward only to
   `http://127.0.0.1:8768/mcp`. Keep credentials in the client's supported
   secret store or a local owner-only file; never put them in this repository
   or a chat message.
3. Start the tunnel client and add its endpoint as a custom ChatGPT app.
4. Reconnect the app after a server surface change. Verify the actual listed
   observer tools and make a read-only call before relying on the connection.

Tunnel client commands and configuration fields depend on the installed client;
use that client's current help. This repository does not bundle or install
tunnel credentials. Codex uses a separate stdio registration described in
[Codex setup](CODEX_SETUP.md).

## Observer use

Use `overview`, `search`, `delta`, `frontier`, `history`, `evidence`, and
`impact` to ground questions in registered projects and migrated Todo
information. The observer remains read-only and does not need a task claim.
Use `investigate` for bounded read-only questions and poll its durable job ID
when the result is pending. Use `skill` for optional domain guidance when
available. Project Control verifies executable code independently of optional
domain content; a missing skill catalog does not prevent core project reads.

Observer findings do not grant mutation authority. Agents working through a
separate authorized profile begin with
`next_task(project, repository?, task_id?, run_id?)`, then pass its returned
handle to `inspect_task`, `coordinate_task`, and `finish_task`. The current
compact profile catalogs are observer 11, coder 12, and mutator 16 tools.
Preserve project and Todo state; semantic mutations go through that authorized
path. See
[the current surface guide](as1-surface.md) for supported requests and response
semantics.
