# AS1 observer read-only contract

All eleven observer tools advertise `readOnlyHint: true`,
`destructiveHint: false`, and `openWorldHint: false` in MCP `tools/list`.
`investigate` and `skill` retain `idempotentHint: false`: an inquiry may schedule
service-owned analysis or refresh stale evidence. Exact literal repeats nevertheless
reuse the existing inquiry. The remaining observer tools retain
`idempotentHint: true`.

Read-only describes access to project source and Todo authority; private cache,
packets and scheduler state remain writable. Public inquiry schemas have no job ID.
Only thinking responses ask callers to repeat the identical question later and
avoid variants. Busy means not accepted and suggests search, read or evidence
for contextualizing or refining a later question. Use read or evidence for
authoritative selected source. See [as1-inquiry-cache.md](as1-inquiry-cache.md).

Observer local analysis has limited reasoning. Use it for bounded evidence
gathering, targeted read-only source or machine inspection, straightforward
grounded summaries, and installed-skill lookup. Its advantage is access to
registered-source, broker, command, and skill context the caller may not have
directly, not stronger reasoning. Keep broad architecture, difficult inference,
multi-project synthesis, strategy, and consequential decisions with the caller;
the caller may ask narrow factual subquestions and decide from the answers.
Observer analysis provides no implementation delegation, coding, or network
authority.

Annotations are client hints, not authorization. Startup profile registration,
dispatch allowlists, trusted scopes, source fences and the command sandbox retain
their enforcement. Mutator tools retain their write annotations. Tests verify the
actual composed MCP tools and schemas, supported terminal answer projection,
private-field removal, and material source freshness without inference.

The following record describes the preceding annotation correction, not current
inquiry-cache release qualification.

The correction was installed on the existing AS1 service on 2026-10-05.
Live HTTP `tools/list` confirmed all eleven observer tools are read-only;
readiness and the tunnel upstream were verified against the new runtime.
The installed source pair and validation limits are recorded in
`planning/adaptive-surface-v1/validation/release/observer-readonly-correction.json`.
There are 46 passing distinct targeted cases. Sixteen skill-suite failures
reproduce unchanged against the previous Project Control commit `0efcdd2`;
the full skill suite is therefore not passing.
