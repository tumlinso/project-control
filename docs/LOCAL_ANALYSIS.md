# Optional local observer analysis

Project Control exposes an optional `observer_analysis` read-only tool.  It
passes an immutable JSON evidence packet (at most 64 KiB and 64 evidence IDs)
to Skills' existing serialized `ProductionBackend.analyze_observer_packet`
boundary.  The backend owns cached-model discovery, llama-server lifecycle,
and topology-aware reservations; Project Control never passes a repository
handle, workflow handle, callable tool, claim, or child-execution context.

Every response is explicitly non-authoritative and mutation-free.  A missing,
busy, malformed, or unsupported local provider deterministically returns
`authoritative_compact_envelope`; compact MCP reads remain the normal path.

The implementation reuses the installed cached model and llama-server support;
it does not import writable local-coding-worker protocol, coding-agent tools,
claims, recursive delegation, or scheduler machinery. One serialized model
service is sufficient.
