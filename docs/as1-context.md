# AS1 information service

`project_control.as1_context.InformationService` provides the eight shared information operations and observer-only `read`. Public MCP registration remains the downstream SURFACE task. Construction performs no automatic overview, model load, authority migration or project mutation.

```python
host = ContextHost("observer", principal, frozenset(registered_projects))
service = InformationService(config, packet_store, runtime.snapshot, host,
    semantic_provider=read_only_project_context,
    job_lookup=authorized_job_lookup,
    impact_provider=canonical_trace)
result = service.call("search", project="demo",
                      query={"kind": "task", "target": "T1"})
```

The host binds trusted profile, principal and registered project allowlist. Requests cannot choose these. `codex` uses coder packet scope. Packet scope includes profile, preventing a caller with the same principal from replaying observer file reads or extended results through a local profile. Local profiles use native find, rg and Git, and cannot request `read` or `extended`.

Each operation writes an immutable response payload, source manifest, omissions, freshness and cursor to `SQLitePacketStore`. Responses contain `status`, `packet`, `data`, `sources` and `coverage`. Whole-response budgets are soft UTF-8 byte budgets. Oversized exact text stays intact in the stored packet; the caller gets its required size and an exact packet continuation with permitted detail. Packet retrieval retains the original payload and avoids recursively nesting full packet metadata.

Exact typed search uses `inspect_subject(..., exact_only=True)`: canonical reconciled graph records and relationships, expected type filtering, exact identity/name resolution and explicit ambiguity. It does not run token-overlap matching, lexical retrieval, ctxpp fallback or Git discovery. Resolved typed paths return semantic graph records; observer file content is requested through `read`. Packet/job exact requests skip project snapshot construction. Packet aliases require trusted scope; the host-installed job lookup must enforce the supplied scope. Registration uses the read-only canonical semantic context port, including its actual revision and UUID.

Discovery retains architecture/graph entities, source/symbol/lexical services, path discovery and bounded live Git fallback. Phrase queries filter generic lexical hits that match only a boilerplate word. Repository and path scopes constrain source matches. Returned source hits receive actual working-tree hashes; search candidates are not dependency closure or correctness evidence.

Observer reads batch 1–32 registered relative files with independent status, ranges and source identities. UTF-8 bytes preserve newline spelling. Binary input, unsupported ranges, paths escaping the root, requested symlinks and historical Git symlink blobs are rejected. Git reads resolve the requested commit and never substitute working-tree bytes. Working-tree reads use descriptor traversal with `O_NOFOLLOW`, regular-file checks, before/after metadata, a second byte read and ancestor revalidation. Source policy redaction is labeled and never described as exact verbatim content. File size is limited to 8 MiB; a larger indivisible unit returns an explicit error.

Overview consumes versioned orientation and applied skill use from `Service.project_context`; missing authored orientation is labeled and registered entry points provide a deterministic fallback. The provider seam is read-only. Missing extension/migration 12 returns partial unavailability and never migrates a live migration-11 authority. Canonical project-context UUID/revision remain attributed independently of the project snapshot; revision skew is a partial coverage condition. Native UUID/root anchors become public registered project/repository aliases only when their identity maps to an authorized registration. Unknown source registration is explicit.

Frontier and scoped coordination reuse their canonical services. Delta supports canonical cursors and retained packet baselines, with unavailable baseline errors and packet parent provenance. Evidence reuses gate synthesis and adds distinct registered-unrun, running, failed, current-pass, stale-historical-pass and terminal-frozen classifications; source mentions remain assertions. History reuses material Todo events and exact typed anchor revisions, replaces exported commit ordering with actual Git ancestry, and reports non-ancestor or unavailable ranges. Git history is bounded to 100 commits per repository; reaching the bound is disclosed. Missing retained task/checkpoint/interface anchors are partial, not inferred.

Machine uses fixed read-only machine-inspection views for every profile and records observation time with a five-second volatile lifetime. It cannot launch benchmarks or lease operations. Machine providers that require an unavailable sandbox report partial diagnostics.

## Qualification and integration boundaries

`tests/as1/test_pc_as1_context.py` marks CTX-01 through CTX-11 and exercises real Git, canonical synthesis ports, immutable packet storage, policy rejection before dispatch and an isolated actual Skills `Service.project_context` fixture. It verifies this managed worktree's Python source identity. The source interpreter is `/home/tumlinson/project-control/.venv/bin/python`.

The Skills prerequisite was re-fetched as done at task revision 893; receipt source commit `94d07da9a1d865cdaf76f567a5060d05aae8cbae`, gate evidence `ca37839b-c7a1-4528-b8c5-c06c2f759fa5`. The receipt's current source docs hash was verified before consumption. This does not qualify a live migration/runtime cutover. TRACE supplies `impact_provider(project=..., detail=..., **params)`; CONTROL supplies scoped `job_lookup(reference, access_scope)`; root/SURFACE owns public wiring, claims, gates, commits and integration. No live science project or authority is modified by these fixtures.
