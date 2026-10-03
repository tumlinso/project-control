# Advisory skill knowledge

Project Control owns skill discovery, bounded retrieval, indexing, and execution
routing. Skills supply domain knowledge and semantic relationships. Their content
has `origin: agent_skill`, `authority: advisory_instruction`, and
`mutation_authority: false`; authoritative project and Todo state takes precedence.

Configure `observer_skills_root` in Project Control configuration, or set
`PROJECT_CONTROL_OBSERVER_SKILLS_ROOT`. The default is `~/.agents/skills`. This
knowledge root is independent of repositories and the verified
`PROJECT_CONTROL_SKILLS_ROOT` execution/Todo runtime. Personal document edits
invalidate relevant skill evidence/index identities, never runtime identity.

## Public use

Prefer `skill_context(query="Volta register pressure")`. It searches skill names
and descriptions when `skill="auto"`, then retrieves bounded sections through
Project Control's shared lexical index and validated semantic relationships.
An opaque skill ID can select a specific corpus.

For explicit inspection:

```text
skill_list(query="CUDA")
skill_read(skill_id=<returned ID>, resource="SKILL.md")
skill_read(skill_id=<returned CUDA ID>, resource="references/architectures/volta/router.md")
```

Discovery returns metadata and relative resource names, not all instructions.
Reads report content identity, freshness and continuation lines. Discovery/context
continuations are opaque strings; pass them back unchanged. Public tools accept
no filesystem root, project registration, or execution backend selection.

Direct retrieval covers at most two resources/8 KiB of selected material. Indexed
retrieval covers up to eight resources/32 KiB. Larger retrieval or broad synthesis
across at least four resources selects machine analysis. Project Control sends
only selected immutable JSON evidence, below 64 KiB per packet, in at most four
serial packets per call. The machine receives no filesystem roots, commands, workflow
handles, or filesystem capabilities. The observer gets compact cited findings;
capacity failure returns typed unavailable/partial evidence, never a corpus dump.
Continuation and coverage describe remaining relevant evidence.

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
