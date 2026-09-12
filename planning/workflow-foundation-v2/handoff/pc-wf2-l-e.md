# PC-WF2-L-E

Role `implementer`; workspace mode `isolated_merge`. Parent `PC-WF2-L-COORD`.

## Serial queue

1. `PC-WF2-E01` — Move admin persistence queries behind typed core methods
2. `PC-WF2-E02` — Record external worktree intent and reconciliation
3. `PC-WF2-E03` — Test partial side effects and uncertain outcomes

Use the authoritative next_task/inspect_task interface for this exact local run/lane. Discover its installed signature; do not guess flags or reuse old run IDs. Read only the current task sheet, referenced contracts and changed producers. Verify required source commits are present. Return a bounded handoff with source/evidence IDs, unresolved limitations and the next safe action. Do not republish the whole transcript.

The integrator is a durable exclusive destination, never an isolated producer. Before producers finish, resolve integration-task binding and exact recorded base using the installed owner API. Serial phases may require explicit reviewed rebind or a new phase lane; do not force stale bases. The coordinator remains claimable while work is ongoing and closes after local I90; the epic is last.
