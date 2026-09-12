# Research routing and evidence digests

Use this guidance to route bounded research without making a provider, model,
or conversation a source of workflow authority.  The controller keeps
decomposition, cross-workstream decisions, integration, and final acceptance.
An implementer owns its first-class task.  A researcher answers one bounded,
independent question.  A scout locates known material.  A reviewer independently
reopens decisive evidence for a consequential claim.

## Select the smallest appropriate hand

Treat **context depth** and **difficulty** as independent requirements.
Context depth says how much relevant source, history, or authority state the
question needs; difficulty says how much reasoning or review it needs.  Neither
field grants permission, sets priority, selects a model, or turns a task into a
workflow lane.  Record requested and observed executor details separately when
the active profile can expose them.

For a simple known-path lookup, use a scout or inspect the bounded source
directly.  For one independent deep question, default to one read-only
researcher.  Add another researcher only for a separately bounded question or
when contradictory evidence needs independent resolution.  Do not use broad
fanout, recursive researchers, or repeated polling as a substitute for a clear
question.

A helper or native local-worker child remains subordinate to its parent claim.
It is not a durable workflow lane, does not receive a lane ID, and cannot claim,
complete, or coordinate the parent's first-class task.  Only the authoritative
workflow may create or assign durable lanes.

## Required compact research digest

Every research return must include all of the following, even when the answer
is negative:

1. **Question and bounded scope** — what was investigated and what was excluded.
2. **Conclusion** — the answer, clearly separated from recommendations or
   inference.
3. **Evidence** — exact re-fetch references: repository identity, commit or
   authority revision, path and line range, or an exact task/interface/evidence
   ID.  Include the command or observer route only when it is necessary to
   reproduce the observation.
4. **Contradictory evidence and uncertainty** — material conflicts, stale or
   unverified observations, and their effect on confidence.  Absence of a
   conflict search is itself an uncertainty, not proof of agreement.
5. **Coverage and gaps** — sources, pages, time interval, or state examined;
   what was not examined; and why the coverage is sufficient or insufficient.
6. **Next action** — a bounded parent re-fetch, targeted follow-up, decision, or
   an explicit statement that no further action is required.

Aim for at most 1,200 tokens or 6 KiB unless a material warning requires more.
Return raw records only when their exact fields are needed.  A shorter digest
may not omit identity, contradictory evidence, uncertainty, coverage, or a
security-relevant warning.

## Parent behavior after a digest

The parent consumes the digest instead of repeating its exploration.  It may
re-fetch only decisive, disputed, stale, or insufficiently identified evidence,
or request a targeted follow-up.  It stops once the bounded question is
answered.  A digest is evidence, not an authorization capability and not a
completion record; workflow actions continue through the parent task's opaque
handle and the authoritative workflow interface.

## Research-session qualification

Role files express a requested configuration. They do not prove what a spawned
session actually received. Record the requested agent class, model, and
reasoning effort separately from the actual model, effort, tool inventory,
sandbox, and approval state observed in that child session. If any actual value
is unavailable, say `unobserved`; do not infer it from a role name, a TOML
setting, or the parent session.

Researchers use a separately enforced observer-only MCP surface. Configure an
explicit allowlist of read-only observer tools and exclude `terminal_capture`
and every inherited workflow mutation tool (`next_task`, `coordinate_task`,
`delegate_task`, `collect_delegation`, and `finish_task`). Do not pass an opaque
parent workflow handle to a researcher. Before relying on the role, inspect the
child's effective server and tool inventory and retain the observed result with
the digest.

Read-only instructions are behavior guidance, not a security boundary. The
parent's live sandbox and approval overrides can be reapplied to children, so a
role file claiming read-only access is insufficient. Use a session whose actual
sandbox, approval policy, MCP server set, and filesystem permissions have been
observed to enforce the required boundary; otherwise report the research path as
unqualified.

If the configured inexpensive model or observer-only surface is unavailable,
fail that dispatch explicitly with the unavailable component and requested
configuration. Do not silently retry using a more expensive model, inherit the
parent's workflow tools, or broaden permissions. Escalation is a new,
controller-authorized decision with its requested and actual configuration
recorded separately.
