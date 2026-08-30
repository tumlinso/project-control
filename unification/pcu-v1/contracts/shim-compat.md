# PCU-SHIM-COMPAT/1

For one compatibility release, `integrations/coding-workflow-mcp` preserves old executable and administrative entry points as minimal forwarding shims to verified Project Control Codex/admin entry points. It contains no independent backend, database, resolver, scheduler, capability store, transaction, claim, completion, or recovery business logic. A fail-safe fallback is retained only until validated cutover. The package is not deleted in PCU-V1 and is never a second live MCP registration.
