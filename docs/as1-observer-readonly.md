# AS1 observer read-only contract

All eleven observer tools advertise `readOnlyHint: true`,
`destructiveHint: false`, and `openWorldHint: false` in MCP `tools/list`.
`investigate` and `skill` advertise `idempotentHint: false`: a repeated analysis
request without a `request_id` can create another service-private job. The
remaining observer tools retain `idempotentHint: true`.

Read-only describes access to project content, source and Todo authority. It
does not promise zero internal bookkeeping writes. Private packets, job state,
request deduplication and restart recovery remain available. Analysis guidance
describes context, evidence and installed skills; when pending, callers continue
useful work, poll with `job_id` and reuse `request_id` for retries. Use `read` or
`evidence` for authoritative selected source.

Annotations are client hints, not authorization. Startup profile registration,
the dispatch allowlist, scoped job admission, source fences and the read-only
command sandbox retain their existing enforcement. The mutator tools `plan`,
`amend_project` and `maintain_execution` retain write annotations. No API,
status, backend, schema, role or persistence behavior changes with this correction.

Focused validation uses the actual composed server's `list_tools()` metadata,
observer instructions, public admission/retry/polling and forbidden-principal
lookup. The AS1 surface, jobs and skill suites exercise the existing source
fences, admission limits, deduplication and restart persistence with scripted
CPU ports; they do not establish live deployment or GPU inference qualification.
