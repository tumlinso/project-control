# PC-WF2-L-S

Role `implementer`; workspace mode `isolated_merge`. Parent `PC-WF2-L-COORD`.

## Serial queue

1. `PC-WF2-S01` — Implement strict work-profile and versioned plan validation
2. `PC-WF2-S02` — Persist profiles with transactional semantic revisions
3. `PC-WF2-S03` — Add compatibility-safe legacy plan normalization
4. `PC-WF2-S04` — Expose task profiles through native dispatch and inspection
5. `PC-WF2-S05` — Promote the reviewed bootstrap profile catalog
6. `PC-WF2-S06` — Lower structured pre-ledger metadata without notes encoding
7. `PC-WF2-S07` — Qualify schema migration and profile routing invariants

Use the authoritative next_task/inspect_task interface for this exact local run/lane. Discover its installed signature; do not guess flags or reuse old run IDs. Read only the current task sheet, referenced contracts and changed producers. Verify required source commits are present. Return a bounded handoff with source/evidence IDs, unresolved limitations and the next safe action. Do not republish the whole transcript.

The integrator is a durable exclusive destination, never an isolated producer. Before producers finish, resolve integration-task binding and exact recorded base using the installed owner API. Serial phases may require explicit reviewed rebind or a new phase lane; do not force stale bases. The coordinator remains claimable while work is ongoing and closes after local I90; the epic is last.
