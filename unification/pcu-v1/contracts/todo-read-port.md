# PCU-TODO-READ-PORT/1

Todo Orchestrator exposes an explicit in-process, normalized, read-only facade. Calls fail closed on authority or schema mismatch and do not change database bytes, revision, events, snapshots, projections, Git state, sessions, claims, dispatches, children, gates, resources, or locks. Outputs preserve the current semantic read contracts and explicit partial-degradation behavior.
