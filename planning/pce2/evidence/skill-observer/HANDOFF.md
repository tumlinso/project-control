# Skill observer delivery

Project Control now owns secure skill discovery, resource reads, shared FTS retrieval,
semantic expansion, and automatic execution routing. Individual skills contribute
advisory domain content and topology. Existing CUDA/atlas routers remain preserved
and are not imported or invoked by this machinery.

## Test package and public use

The isolated candidate is `/home/tumlinson/.local/share/project-control/candidates/skill-observer-20261003-r2`.
Its native stdio launcher is `bin/project-control-release codex`. The running service
has not been changed. The source remains uncommitted for review; earlier edits are
preserved. A wheel and review bundle are in `dist/skill-observer-20261003/`.

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
- Public tests prove synthetic direct retrieval, real CUDA and C++ indexed retrieval,
  atlas automatic machine routing, semantic relationships, bounded packet transport,
  and continuations. Native MCP stdio qualification passed against the final r2
  release with 25 Codex tools and no additional model calls.
- Two bounded live packet attempts were made. The first masked backend unavailability;
  the bridge was corrected. The retry reported no disjoint runtime-discovered GPU
  island available. Successful live synthesis remains unqualified. The typed fallback
  stays small and retains a retry continuation; no large corpus reaches the observer.
- Initial CUDA indexing now takes about five seconds, after removing repeated whole
  inventory scans from selected-resource reads. Existing source indexing remains
  streamed. This is functional qualification, not a retrieval-quality benchmark.

## Next testing

Use the isolated launcher to test the three tools without activating the live service.
When permitted machine capacity is available, rerun the public live qualification
script with `--live-machine`; it limits inference to one selected packet.
Corpus editorial cleanup and removal of old routing prototypes remain a separate
future pass. Shared machine routing is implemented, not deferred to that pass.

Broader PCE2 runtime/surface acceptance remains open; this delivery does not close
those tasks or alter paused programs.
