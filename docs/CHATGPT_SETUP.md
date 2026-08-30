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
7. Because tool schema v3 changes discovery, reconnect or recreate the custom
   app, then verify discovery returns exactly these fifteen tools:
   `project_overview`, `project_delta`, `project_frontier`, `inspect`,
   `evidence`, `plan_preview`, `agent_status`, `performance_status`,
   `architecture_context`, `coordination_view`, `source_context`,
   `history_trace`, `impact_preview`, `program_context`, and `terminal_capture`.
   The fourteen v2 tools are read-only/idempotent. `terminal_capture` is
   intentionally non-read-only and non-idempotent because it owns bounded
   app-private PTY runtime state; it is non-destructive and closed-world.
8. Start a fresh ChatGPT conversation with the app enabled and run
   `project_overview` against the registered disposable workspace before adding
   active engineering projects.

ChatGPT may snapshot tool definitions at connection time. After any future tool
schema change, explicitly reconnect or recreate the app. This v3 addition
requires that reconnect. The original eight v1 calls and all six additive v2
calls remain compatible and unchanged within the frozen fourteen-tool v2
contract.

`project-control doctor --json` reports whether the required bubblewrap backend
is installed, whether its bounded namespace/mount probe succeeds, and whether
the installed systemd address-family/namespace policy is compatible. A typical
bonded call launches with `kill_after_capture=false`
and a short unique label; later calls supply that label as `session` to capture
the same emulator/process. The default `kill_after_capture=true` cleans up the
whole owned process group. Bonded sessions end when Project Control restarts and
must then be launched again.

Codex does not use this custom app or tunnel; it uses the separately configured
stdio profile described in `CODEX_SETUP.md`. Deep research may use this app only
for its read/fetch behavior.
