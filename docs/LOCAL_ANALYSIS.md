# Optional local observer analysis

Project Control exposes `local_investigate(project, question, effort)` as the
preferred observer entry point. A versioned, bounded broker supplies initial
architecture context, validates the local model's structured read requests,
executes them through Project Control's existing read-only services, and returns
an evidence-linked answer separated into facts, inferences, and uncertainty.
`quick`, `standard`, and `deep` impose hard round, read, byte, and time ceilings;
source and project identity are pinned and drift returns `refresh_required`.

The lower-level `observer_analysis` read-only tool remains a primitive. It
passes an immutable JSON evidence packet (at most 64 KiB and 64 evidence IDs)
to Skills' existing serialized `ProductionBackend.analyze_observer_packet`
boundary.  The backend owns cached-model discovery, llama-server lifecycle,
and topology-aware reservations; Project Control never passes a repository
handle, workflow handle, callable tool, claim, or child-execution context.

Both paths are explicitly non-authoritative and mutation-free. A missing,
busy, malformed, or unsupported local provider deterministically returns
an explicit local-only fallback; they never escalate to a paid model.

The implementation reuses the installed cached model and llama-server support;
it does not import writable local-coding-worker protocol, coding-agent tools,
claims, recursive delegation, or scheduler machinery. One serialized model
service is sufficient.
