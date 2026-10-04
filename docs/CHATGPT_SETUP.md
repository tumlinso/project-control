# ChatGPT observer setup

This procedure connects only the Project Control **observer** profile. Keep it
bound to `127.0.0.1`; do not expose port 8767 publicly.

## Local service

1. Initialize the owner-only configuration:

   ```bash
   uv run project-control config init
   uv run project-control config migrate --dry-run
   uv run project-control workspace add disposable source /absolute/path/to/disposable/repo --authority
   uv run project-control doctor --json
   ```

2. Copy `deployment/project-control.service` to
   `~/.config/systemd/user/project-control.service`. Adjust `WorkingDirectory`
   only if this checkout is not at `~/project-control`, then run:

   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now project-control.service
   systemctl --user status project-control.service
   curl --fail http://127.0.0.1:8767/healthz
   curl --fail http://127.0.0.1:8767/readyz
   ```

The MCP endpoint is `http://127.0.0.1:8767/mcp` and uses stateless Streamable
HTTP with JSON responses. Trusted service startup selects the observer profile;
client metadata cannot change it. The server registers no Todo workflow tool,
and direct hidden-name invocation is denied before Todo is reached.

## OpenAI account connection gate

These steps require the user's OpenAI account and are intentionally not
performed by Codex:

1. Enable Developer Mode in ChatGPT.
2. Create or select an OpenAI Secure MCP Tunnel.
3. Install the official tunnel client locally and configure it with the tunnel
   credentials from the OpenAI account. Forward only to
   `http://127.0.0.1:8767/mcp`. Never paste credentials into Codex chat or store
   them in this repository.
4. The files `deployment/tunnel-client.yaml.example` and
   `deployment/tunnel-client.service.example` are templates. Copy them into the
   owner-only `~/.config/project-control/` directory, reconcile executable and
   field names with the installed official client's help, and store credentials
   only in its supported secret store or a `0600` local environment file.
5. Run `uv run project-control doctor --tunnel --json`, then enable the tunnel
   client service.
6. Create a custom ChatGPT app named `project-control` using that tunnel.
7. Reconnect or recreate the custom app after the AS1 schema change, then
   verify exactly 11 observer tools: `overview`, `delta`, `frontier`, `search`,
   `evidence`, `impact`, `history`, `machine`, `read`, `investigate`, `skill`.
   Hidden workflow/control names and removed legacy tools are denied at dispatch.
8. Start a fresh conversation and explicitly call `overview` for the registered
   disposable project before adding active engineering projects. An omitted
   project gives the registered catalog; overview is never automatically injected.

ChatGPT may snapshot definitions at connection time. Reconnect after a surface
change and verify the actual live tool list. Source documentation does not prove
candidate qualification or live cutover. The observer supports compact (default),
standard and extended detail; local profiles do not support extended.

Codex does not use this custom app or tunnel; it uses the separately configured
stdio profile described in `CODEX_SETUP.md`. Deep research may use this app only
for its read/fetch behavior.

## Observer usage

Use the eight shared information tools for direct evidence. `frontier` supplies
active work and coordination; `overview` supplies purpose and architecture.
`search` retains discovery and accepts exact typed semantic entities through a
direct canonical lookup, for example `query={"kind": "task", "target": "T1"}`.
Exact file reads use `read` with registered project/repository and relative paths.
There is no observer shell or public Project Control `find`.

Use `investigate` to submit bounded read-only questions and poll durable job IDs;
continue useful work when a job is pending. `skill` searches the installed catalog
or requests guidance through the same job broker. Project Control brokers source
access and verified excerpts; the local worker reads installed SKILL.md and follows
its authored routes. Start compact and request more detail deliberately. Examples,
packet coverage and retry semantics are in [the adaptive surface guide](as1-surface.md).

For Todo/bootstrap preparation, preserve durable intent, constraints, acceptance,
rationale, uncertainty and references. Observer findings and packet hints grant
no project or workflow mutation authority.
