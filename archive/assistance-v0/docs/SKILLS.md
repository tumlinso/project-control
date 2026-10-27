# Advisory skill knowledge

Project Control brokers catalog access, bounded retrieval, indexing, persistence
and provenance. The local worker follows installed Skills instructions for
semantic navigation. Skills supply domain knowledge and relationships. Their content
has `origin: agent_skill`, `authority: advisory_instruction`, and
`mutation_authority: false`; authoritative project and Todo state takes precedence.

Configure `observer_skills_root` in Project Control configuration, or set
`PROJECT_CONTROL_OBSERVER_SKILLS_ROOT`. The default is `~/.agents/skills`. This
knowledge root is independent of repositories and the verified
`PROJECT_CONTROL_SKILLS_ROOT` execution/Todo runtime. Personal document edits
invalidate relevant skill evidence/index identities, never runtime identity.

## Public use

Observer `skill()` cheaply returns installed catalog names/descriptions without
loading a model. Use `skill(query="Volta register pressure", skill="cuda",
request_id="volta-1")` for guidance; an omitted skill uses the registered discovery
guide and installed catalog. Poll with `skill(job_id=<returned ID>)`, preserving
the returned project scope. Local profiles use native skills, not this MCP adapter.

Project Control brokers authorization, durable jobs, exact source reads, hashes,
freshness and provenance. The local worker is the semantic navigator: it first
reads each selected installed `SKILL.md`, then follows authored maps, references
and prerequisites. Indexes and graphs may accelerate access but do not replace
that routing. The discovery guide is bootstrap navigation, not authority over a
selected skill's own entry. Skill and investigate use one durable broker.

Results separate small labeled synthesis from verified original excerpts with
canonical skill/resource identity, content hashes and exact original ranges.
Unread resources, stale text, missing prerequisites, redaction or unsupported
registrations remain explicit omissions. Synthesis is advisory; excerpts retain
their original source authority. Polling revalidates selected source bytes; an old
packet alias is historical evidence, not a promise of current freshness.
See [the adapter contract](as1-skill.md) and [surface examples](as1-surface.md).

Local native skill instructions remain available through the installed filesystem.
No public `skill_list`, `skill_read` or `skill_context` alias is advertised by AS1.
Public requests cannot choose filesystem roots, runtime models or execution policy.

## Boundary and archive ingestion

Skill IDs resolve only inside the configured registry. Relative reads reject
traversal, absolute paths, symlinks, denied resources, binary/invalid UTF-8 content,
size overruns, stale identities, and races. Reads never execute supporting code.
Outputs retain Project Control secret/path redaction and serialized byte budgets.
Indexes are disposable private caches; no skill content becomes Todo authority.

Archive ingestion is an explicit local administrative action, never an observer
read. Preview the preserved atlas with:

```text
project-control admin ingest-skill-archive --skill <CUDA ID> --resource references/architectures/volta/V100_SXM2_Circuit_Bending_Atlas.zip --destination references/architectures/volta/v100_atlas
```

Add `--apply` to publish validated extraction. Ingestion preserves substantive
text bytes and the source ZIP, generates corpus provenance/semantic metadata,
and rejects unsafe archive paths, unsupported members and decompression limits.
Differing existing material is never overwritten. Original routing/probe code
remains in the ZIP and is not invoked for retrieval.

Corpus editorial cleanup and removal of redundant skill-side routers remain
future work. Project Control's registry and context machinery require neither.
