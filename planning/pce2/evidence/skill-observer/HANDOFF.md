# Skill observer delivery

Project Control now owns secure skill discovery, resource reads, shared FTS retrieval,
semantic expansion, and automatic execution routing. Individual skills contribute
advisory domain content and topology. Existing CUDA/atlas routers remain preserved
and are not imported or invoked by this machinery.

## Test package and public use

The original isolated test candidate is `/home/tumlinson/.local/share/project-control/candidates/skill-observer-20261003-r2`.
Its native stdio launcher is `bin/project-control-release codex`. The implementation
is now committed and pushed as Project Control `905baff` and Skills `714d0d7`.
Earlier untracked review packages and the archive sidecar remain preserved.
A wheel and review bundle are in `dist/skill-observer-20261003/`.

The live shared launcher now selects the repaired committed-source candidate
`/home/tumlinson/.local/share/project-control/candidates/skill-observer-packet-repair-20261003`,
with Project Control `b30f881` and trusted Skills runtime `45bb7f6`.
The service was restarted successfully, and the old launcher is preserved for rollback.
Live `/healthz`, `/readyz`, and `/version` return 200. HTTP discovery exposes 20
observer tools; shared-launcher stdio exposes 25 Codex tools. Public discovery,
instruction reads, atlas supporting reads, and C++ indexed retrieval passed.

The first activated atlas machine test reached inference but failed with
`observer_provider_malformed_output`; its bounded fallback and honest zero analyzed
sections are preserved in `live-deployment/http-public.json`. The trusted packet
backend was repaired to require schema-constrained summaries and valid evidence
citations, then the final candidate was activated and the service restarted again.
The unchanged original auto atlas query now passes through real HTTP MCP: four
machine summaries, 152 of 188 selected sections analyzed, eight evidence excerpts,
and an 11,479-byte response in 124.27 seconds. A continuation remains for the other
selected sections. See `packet-repair/http-public.json` and
`packet-repair/stdio-public.json`. No backend substitution or unrelated GPU task
termination was used.

The public read tools are:

```text
skill_list(query="CUDA")
skill_read(skill_id=<returned ID>, resource="SKILL.md")
skill_context(query="overview V100 atlas prerequisites NVLink", skill="auto")
skill_context(query="FP8", skill=<CUDA ID>)
```

Prefer `skill_context` for normal use; pass returned continuations unchanged.
Discovery and reads require no project registration or caller-supplied root.
`observer_skills_root` / `PROJECT_CONTROL_OBSERVER_SKILLS_ROOT` configures advisory
knowledge; the default is `~/.agents/skills`. It is separate from the verified
`PROJECT_CONTROL_SKILLS_ROOT` execution/Todo runtime. Personal text and atlas changes
invalidate relevant knowledge identities, not runtime identity.

## Corpus integration and boundary

The original atlas ZIP is unchanged. Explicit administrative ingestion published
216 substantive files under `cuda/references/architectures/volta/v100_atlas`, with
237 semantic resources and 929 relationships, preserving text bytes. Five prototype
tool/probe files remain preserved in the ZIP. No editorial pruning or rewriting was
performed. `atlas-ingestion.json` records source identity and unchanged Todo runtime
fingerprints before and after publication. Root performed this cross-workspace
publication and package integration; bounded workers implemented and tested code.

Registered skill IDs and relative resources are the only public addressing scheme.
Reads reject traversal, absolute paths, symlinks, private/operational resources,
invalid text, oversized inputs, and detected races. Indexes live in private caches.
Skill content cannot choose backends or execute code. Machine analysis receives
selected immutable packets with opaque identities and relative evidence references,
without filesystem roots, commands, tools, or workflow handles. All content is
`agent_skill` / `advisory_instruction` / `mutation_authority: false`.

## Qualification and limits

- Application/regression suite: 536 passed, one skipped, 40 subtests passed.
- Two pinned WF2 tests require unavailable `WF2_BINDINGS`; the initial full-suite
  result and explicit exclusion are preserved in `test-qualification.json`.
- Latest follow-up focused suites: provider 44 passed; routing 14 passed; packet
  bridge 24 passed with six subtests. Independent review findings are resolved.
- Packet-repair tests: trusted backend supervisor 39 passed plus two adapter tests;
  Project Control packet bridge 25 passed with three subtests.
- Public tests prove synthetic direct retrieval, real CUDA and C++ indexed retrieval,
  atlas automatic machine routing, semantic relationships, bounded packet transport,
  and continuations. Native MCP stdio qualification passed against the final r2
  release with 25 Codex tools and no additional model calls.
- Earlier bounded live packet attempts were made. The first masked backend unavailability;
  the bridge was corrected. The retry reported no disjoint runtime-discovered GPU
  island available. These historical failures remain in `public-live.json` and
  `public-live-retry.json`. Postrepair live HTTP synthesis is qualified without
  fixtures: automatic machine routing covered 90 relevant resources / 166,355
  selected bytes; all four returned summaries have validated evidence citations.
  Partial coverage and continuation remain explicit; no large corpus reaches the
  observer. This does not qualify the scientific content of the atlas.
- Initial CUDA indexing now takes about five seconds, after removing repeated whole
  inventory scans from selected-resource reads. Existing source indexing remains
  streamed. This is functional qualification, not a retrieval-quality benchmark.

## Next testing

The live service exposes the three tools and the repaired machine route passed.
The public qualification script with `--live-machine` limits inference to one
selected packet; `packet-repair/qualify_live_http.py` exercises the activated service
with the original auto query and no backend substitution.
Corpus editorial cleanup and removal of old routing prototypes remain a separate
future pass. Shared machine routing is implemented, not deferred to that pass.

Broader PCE2 runtime/surface acceptance remains open; this delivery does not close
those tasks or alter paused programs.
