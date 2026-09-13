# Optional local observer analysis

Project Control's initial observer-analysis provider is intentionally disabled.
It accepts no claims, sessions, children, repository writes, coding tools, or
GPU reservations, and returns deterministic `observer_analysis_disabled` when
called. Deterministic compact MCP reads remain the normal path.

The bounded reuse probe on 2026-09-13 found an installed `llama-server` binary
but no resident loopback service. Starting a model would require a model choice
and serving setup, which exceeds this release's stop-line. No model was
downloaded, no service was started, and no scheduler or local-worker behavior
was changed. A future opt-in provider may consume immutable bounded evidence
packets only after it can reuse an already healthy installed service; otherwise
it must retain this explicit fallback.
