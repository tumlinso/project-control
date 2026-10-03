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

The live shared launcher now selects the committed-source candidate
`/home/tumlinson/.local/share/project-control/candidates/skill-observer-905baff-20261003`.
The service was restarted successfully, and the old launcher is preserved for rollback.
Live `/healthz`, `/readyz`, and `/version` return 200. HTTP discovery exposes 20
observer tools; shared-launcher stdio exposes 25 Codex tools. Public discovery,
instruction reads, atlas supporting reads, and C++ indexed retrieval passed.

Live atlas machine testing reached inference after restart: the earlier GPU-capacity
failure no longer appeared. Synthesis failed with `observer_provider_malformed_output`.
The result remained bounded (5,502 bytes), with two fallback evidence entries and a
retry continuation; zero sections were falsely reported as analyzed. Successful live
synthesis is still unqualified. See `live-deployment/http-public.json` and
`live-deployment/stdio-public.json`. No unrelated GPU task was terminated.

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

The live service now exposes the three tools. Investigate the machine backend's
malformed response before claiming successful synthesis. The public live qualification
script with `--live-machine` limits inference to one selected packet; the separate
live HTTP script exercises the activated service without backend substitution.
Corpus editorial cleanup and removal of old routing prototypes remain a separate
future pass. Shared machine routing is implemented, not deferred to that pass.

Broader PCE2 runtime/surface acceptance remains open; this delivery does not close
those tasks or alter paused programs.
