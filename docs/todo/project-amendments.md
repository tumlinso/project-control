# Canonical project semantic amendments

`Service.amend_project(request, principal=..., role="mutator", context=...)`
consumes the frozen `pc-project-amendment/1` wire object. Role, principal and
context are trusted **in-process host adapter** arguments, not authentication
proof supplied by a model. The adapter must establish project access and policy
before calling this method. `context.project_id` binds a registered project ID;
configured `configuration.registered_project_id` takes precedence. Without a
binding, only the exact canonical project UUID or project display name matches.

Preview requires the current `expected_revision` and durably captures the exact
affected records, local source hashes and applicable provider trust digest.
Apply rechecks them inside the canonical SQLite mutation transaction. A reviewed
operation can survive an unrelated semantic revision; an unreviewed stale apply
cannot. Rebinding an operation ID to another intent/payload refuses. Replay
returns the original receipt and current canonical readiness. Real no-ops and
preview/replay commit only operation-ledger data, without semantic revisions,
events or projection rewrites. Material changes increment the existing Todo
revision/event stream once. Projection failure returns a committed receipt and
explicit repair information instead of pretending the transaction failed.

Each action payload has an exact nonempty `id`. Identity requires `repository`
and `version`; relation requires `source`/`target` typed entity references plus
`relation`. Generation requires relative `roots`, typed source `inputs` and
`generator` provenance, which is never executed. Removal requires exact
`kind`/`id`/integer record `version` and retains prior history. Related local
relations are retired with the declaration; foreign references match neither
local project UUID nor repository and are preserved.

Skill use requires `skill`, `reason`, `status` (`consulted` or `applied`) and
source `anchors`. Orientation requires `fields` and `anchors`, with optional
`field_anchors` mapping field names to nonempty locator lists. Locator fields
are `project`, `repository`, relative `path`, SHA256 `content_sha256`, and
optional line bounds. Local anchors using UUID, display name or registered ID
all check actual bytes beneath the same registered root; aliases never bypass
freshness. Repository must match host `configuration.registered_repository_id`
when configured, otherwise the exact canonical root path or root directory name.
A local project UUID never authorizes an arbitrary repository alias. Foreign anchor freshness currently returns
`source_prerequisite_unavailable`; this kernel supplies no cross-repository
resolver or additional root access. Cross-project entity relations themselves
remain declarative and do not merge authorities.

`configure_provider` reads only host project configuration:

```json
{"project_providers": {"installed-provider": {"allowed_options": {"depth": [1, 2]}}}}
```

Its payload is `id`, `provider_id`, optional `options` and `anchors`. Unknown
providers/options and executable/install/root fields refuse. Semantic
configuration never installs or launches providers or grants directory access.

`Service.publish_project_context(request, claim_token=...)` is the scoped coder
hook. Request contains `kind` (`skill_use`, `finding`, `candidate_relation`),
optional authenticated `task_id`, and `payload` with `id` and source anchors.
The existing canonical claim token and actual task ownership scopes authorize
publication. Candidate relations remain candidates; publication cannot promote
authoritative project declarations. All source anchors are checked against task
scope and current local bytes. This hook is distinct from broad mutator policy.

`Service.project_context()` reads latest declarations, skill use, orientation
and targeted invalidation records. Orientation rows return `field_freshness`
with per-field `fresh`, `stale` or `unavailable` status from current bytes, using
shared anchors when no field override exists. Read does not increment revisions.
Snapshots retain exact persisted locators/history/operation receipts; their
orientation freshness is explicitly unavailable without a runtime root binding.
Material invalidations include old and new source anchors and affected local
relations. Related active context fragments invalidate; successful terminal task
proof, gates, unrelated fragments and retained dirty work are preserved.

Migration 12 is additive. Existing read-only core operations need migration 11,
so an unmigrated 11 authority remains readable without writes. New semantic
operations require 12 and return `schema_migration_required` until a supported
writable initialization migrates it. Old readonly exports report semantic
context unavailable rather than inventing empty records. Writable Service
initialization applies 12 through the existing canonical migration kernel.
Older binaries are not qualified to maintain new semantic snapshots; use the
bound candidate runtime after cutover. This extension does not replace native
plan, selective replan, retirement or maintenance kernels.
