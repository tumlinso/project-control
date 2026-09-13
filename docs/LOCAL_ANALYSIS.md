# Optional local observer analysis

Project Control exposes `local_investigate(project, question, effort)` as the
preferred observer entry point. A versioned, bounded broker supplies initial
architecture context, validates the local model's structured read requests,
executes them through Project Control's existing read-only services, and returns
an evidence-linked answer separated into facts, inferences, and uncertainty.
`quick`, `standard`, and `deep` impose hard round, read, byte, and time ceilings;
source and project identity are pinned and drift returns `refresh_required`.

The broker also accepts `inspect_machine` for bounded GPU, topology, process,
memory, filesystem, Project Control service, kernel, device, log, and runtime
observations. Project Control owns fixed diagnostic commands and validates
structured filesystem reads under registered repositories and useful local
roots including home, `/mnt`, `/proc`, and `/sys`. Traversal, symlink escape,
special files, process secret surfaces, credential stores, private keys, and
oversized or binary reads are rejected; returned text is redacted and bounded.
External diagnostics run unprivileged in a no-network, read-only bubblewrap
sandbox with private scratch directories and resource ceilings. The interface
exposes no shell, caller-selected argv, privilege, socket handle, or mutation
surface.

The lower-level observer-analysis provider remains an internal primitive. It
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
