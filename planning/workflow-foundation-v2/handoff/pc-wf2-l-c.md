# PC-WF2-L-C

Role `implementer`; workspace mode `isolated_merge`. Parent `PC-WF2-L-COORD`.

## Serial queue

1. `PC-WF2-C01` — Specify the embedded-core authority boundary
2. `PC-WF2-C02` — Freeze schema, snapshot, and evidence contracts
3. `PC-WF2-C03` — Publish shared engine and cutover interfaces

Use the authoritative next_task/inspect_task interface for this exact local run/lane. Discover its installed signature; do not guess flags or reuse old run IDs. Read only the current task sheet, referenced contracts and changed producers. Verify required source commits are present. Return a bounded handoff with source/evidence IDs, unresolved limitations and the next safe action. Do not republish the whole transcript.

The integrator is a durable exclusive destination, never an isolated producer. Before producers finish, resolve integration-task binding and exact recorded base using the installed owner API. Serial phases may require explicit reviewed rebind or a new phase lane; do not force stale bases. The coordinator remains claimable while work is ongoing and closes after local I90; the epic is last.
