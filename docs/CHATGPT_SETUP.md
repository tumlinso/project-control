# ChatGPT observer setup

This connects ChatGPT to the read-only Project Control observer over the
loopback HTTP endpoint. Keep the listener bound to `127.0.0.1`; expose it to a
remote client only through an explicitly configured, authenticated tunnel.

## Start the local source service

Set up the checkout when first installing dependencies or when they change:

```sh
scripts/pc-dev setup
```

The source service unit in `scripts/services/project-control.service` runs
`scripts/pc-dev run serve observer --host 127.0.0.1 --port 8768`. Preserve its
existing profile, principal binding, workspace configuration, ports, and state
locations when installing or editing a unit. Then:

```sh
systemctl --user daemon-reload
systemctl --user restart project-control.service
curl --fail http://127.0.0.1:8768/healthz
curl --fail http://127.0.0.1:8768/readyz
```

The MCP endpoint is `http://127.0.0.1:8768/mcp`. Readiness covers valid core
configuration and the bundled workflow engine. Inference and optional domain
content have separate status and are not prerequisites for core reads. To verify
the real surface, reconnect the MCP client, list tools, and make one read-only
call for a registered project.

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

Use `overview`, `frontier`, `search`, `evidence`, and the other currently
advertised read tools to ground questions in registered projects. Use
`investigate` for bounded read-only questions and poll its durable job ID when
the result is pending. Use `skill` for optional domain guidance when available.
Project Control verifies bundled executable code independently of optional
domain content. A missing skill catalog can limit that guidance without
preventing core project reads.

Observer findings do not grant mutation authority. Preserve project and Todo
state; semantic mutations use the authorized workflow path. See
[the current surface guide](as1-surface.md) for supported requests and response
semantics.
