# Run and maintain the installed applications

The separation was deployed on 2026-10-28. See the [cutover record](cutover-2026-10-28.md) for exact revisions, preservation checks, known limits and rollback locations. Ordinary development still uses [private candidates](development.md).

## Project Control

The stable launcher is `~/.local/bin/project-control`. Local profiles and the user service select an immutable environment through `~/.local/state/project-control/current`. The legacy `~/.local/share/project-control/current` selector now follows that same pointer. Source edits do not change the installed runtime.

```sh
systemctl --user status project-control.service
curl --fail http://127.0.0.1:8768/version
curl --fail http://127.0.0.1:8768/readyz
```

HTTP serves Observer's ten tools. Codex launches twelve through `project-control codex`; `project-control mutator` exposes fifteen. Existing clients may need to reconnect to refresh their loaded executable and catalog. Retired inference is masked and its launcher is archived outside `PATH`.

Registration stays in `~/.config/project-control/config.toml`. Response packets and neutral evidence are under `~/.local/state/project-control/as1`; indexes use `~/.cache/project-control/source`. Peer evidence registrations are in `~/.config/project-control/evidence-stores.json`. An optional `cpp-exports.json` in that directory supplies the map described in [retrieval](retrieval.md). These are private local files, not package contents.

### Workflow client lifetime

Keep the official Codex or Mutator stdio connection open from `next_task` through authoring and `finish_task`. With the Python MCP SDK, keep both the outer `stdio_client(...)` context and the nested `ClientSession(...)` context alive while doing the work; initialize once and serialize foreground tool calls through that session. An idle client can wait for work or control messages without closing either context.

The stdio server owns automatic attempt renewal. The Observer HTTP service does not renew that session's work, and a helper that launches a fresh server for each tool call stops renewal when it exits. Handles stay bound to the same identity, profile, credentials and providers across reconnects. Use supported inspection and resumption after a disconnect; an expired lease grants no permission for further computation. Source-fenced handoff and reporting preserve an exact committed result for review without renewing that lease or claiming the producer process stopped.

### Large source inventories in workflow replies

Orchestration replies for Git effects and recovery keep source digests, revisions,
working-tree status, operation identities, native receipts and recovery intents.
Their `source_observation.inventory_summary` explicitly marks file-input and
artifact inventories as omitted, with exact counts and SHA-256 digests of each
canonical JSON inventory. Native validation still uses the complete source
observations. The standalone Todo API and its document ceiling are unchanged;
these summaries are presentation records, not replacement validation evidence.

### Accepted integration recovery

A failed private-ref integration may advertise an existing recovery intent after
fresh proof that it was not applied. For an accepted terminal attempt, the
original lease stays absent: the trusted host must revalidate its exact durable
source fence, no outstanding authored commands, resource owners or children,
and the complete source identity against accepted provenance. This proves scoped
authoring quiescence without asserting that a producer process stopped. Missing,
changed or mismatched proof leaves recovery unavailable. Review handles use the
privileged `plan(recover)` route; resume the same pending integration afterward.

Integration into an unchecked-out private ref uses in-core Git objects and an
expected-old ref compare-and-swap. It preserves unrelated ignored root payloads
without reading them, while retaining tracked/index and nonignored-file guards,
including initialized module identities. Managed source observation remains fully
strict. Custom merge-driver, renormalization or signing configuration retains
the previous full raw-root guard.

## Project-Lab

`project-lab` and `project-lab-worker` select `~/.local/state/project-lab/current`. Private configuration is `~/.config/project-lab/config.toml`; state is `~/.local/state/project-lab/data`. The application has a scoped scheduler caller and a separate Codex MCP entry. It needs no resident Lab daemon: the scheduler runs explicitly submitted controllers and effects.

```sh
project-lab status
project-lab status SESSION_ID
project-lab results SESSION_ID
project-lab compare SESSION_A SESSION_B
```

Historical sessions appear separately as inspect-only records. Status, results and comparisons remain readable; historical handles do not authorize execution. A new run explicitly authorizes resource and stopping budgets. See [Project-Lab setup](https://github.com/tumlinso/project-lab/blob/main/docs/configuration.md).

## Updates and rollback

Build a new environment from recorded wheels and exact dependencies, validate separately, retain the previous environment and private configuration, then atomically change the selector and restart only the relevant service. Preserve Todo pairing and package fingerprints. Do not install over an active environment.

Rollback changes executable selection and launch configuration, never task, experiment or scheduler authority revisions. Durable cutover backups contain rollback notes. Credentials and unrelated local source edits stay in place.

Production changes require authorization. Source development alone does not authorize restarts, pointer changes or data imports. Model/GPU qualification and comprehensive benchmarking remain separate work.
