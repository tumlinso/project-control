# Mutation profile v1

Project Control now implements a deliberately narrow general Todo plan write
surface in a separate **mutator** profile. This does not change the existing
profiles:

- **observer** remains permanently project-read-only with its frozen 15-tool
  surface. It has no `apply_plan`, write flag, hidden switch, or dormant
  mutator.
- **codex** retains its exact six Todo workflow tools and fourteen rich reads.
  The workflow protocol remains the ordinary path for claimed implementation
  work.
- **mutator** is an explicit trusted-startup stdio profile. It exposes the
  Codex 20-tool surface plus `apply_plan`, and excludes `terminal_capture`.

Profile selection is process configuration. MCP `clientInfo`, user agents,
annotations, model claims, and tool arguments cannot grant mutation authority.
The current local stdio transport deliberately avoids a new unauthenticated
network write service. Remote OpenAI read/write exposure, authentication, and
consent are later deployment policy, not part of v1.

## Proposal and authority

`ProposalEnvelope` remains inert and `authority_to_apply=false`. A proposal
contains intent, a native Todo plan in `proposed_change`, complete observation
preconditions, a deterministic digest, and creation time. It grants no
authority by itself.

The trusted mutator profile grants authority to consume a proposal only after
Project Control verifies the digest, re-observes the target, and compares the
proposal's complete relevant preconditions. Stale Todo revisions or semantic
fingerprints, workflow revisions or fingerprints, repository commits,
worktrees, context fragments, interfaces, run, tasks, or lanes fail closed.
Project Control does not guess, rebase, regenerate, or retry.

Todo Orchestrator remains the sole plan validation, dependency, interface,
scope, transaction, SQLite, event, and projection authority. Project Control
uses the verified in-process Todo runtime for validation and diff, then enters
Todo's own transaction exactly once through the canonical Project Control
front door. An empty diff is a deterministic no-op and never calls apply.

The returned mutation receipt is bounded response provenance, not a second
audit database. Todo's event history remains authoritative. `plan_preview`
remains nonmutating and is not reinterpreted as an apply endpoint.

## Bulk ingestion

The trusted local CLI compiles a versioned or compatibility pre-ledger package
to native Todo plan schema v2, validates it, and applies it through the same
mutation service used by MCP. V1 selects one repository authority at a time and
reports cross-authority dependencies separately. MCP `apply_plan` accepts only
proposal content: it has no local file path, shell, SQLite, force, or stale
recovery input. Large local files remain a CLI concern in v1.
