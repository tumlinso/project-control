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
                                                  -> read-only authority adapters
```

The ChatGPT-facing server uses stateless Streamable HTTP with JSON responses.
It binds to loopback and is exposed only through a trusted Secure MCP Tunnel.
Configuration, cache, logs, and temporary plan files live under the app's own
XDG directories. Registered repositories are opened only for bounded reads.

Security is enforced structurally: workspace IDs replace arbitrary roots,
subprocess argument vectors are fixed, paths are resolved beneath allowlisted
roots, deny patterns and text limits are applied, and every unavailable source
degrades to an explicit partial result instead of causing a repair mutation.
