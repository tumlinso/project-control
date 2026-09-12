# 6. Keep ctxpp separate and improve its consumption

Ctxpp owns C++ semantic indexing, compilation-configuration provenance, `.ctxpp` artifacts, packet construction, canonical-range references and separately approved transformations. Project Control owns why a task asks for context and how evidence relates to workflow. Local workers, CUDA tooling and standalone use remain first-class consumers.

Expose a small stable query facade over an existing current query store. It must support exact symbol IDs/qualified names, bounded locations and typed edges, with a declared manifest/index hash and schema. `read` must not imply scan, cache publication into the repository, repair, build, transformation or source mutation. Expensive refresh and source rewrite are separate explicit operations under the relevant engine policy.

Remove sibling-directory `sys.path` injection into Todo from packet source identity. Choose an independent algorithm-tagged contract or a genuinely tiny shared contract package with no workflow engine dependency. Algorithm/version/domain and covered files/configurations make comparability explicit. Do not casually claim two different fallbacks produce the same fingerprint.

PC currently searches index JSONL linearly [S09] while ctxpp has an indexed read store [S08]. Consume the stable query API rather than coupling Project Control to SQLite table layout or copying the context compiler. The language-neutral PC lexical index remains useful for Python, docs and unconfigured source; it should state lexical/partial confidence instead of pretending to be C++ semantic authority.

Strengthen nested packet validation, canonical hashes and coverage. A `current` packet must identify which compiler/target/configuration and source set it actually covers. Deliberately test fallback identity against the v2 schema, missing/stale stores, concurrent publication, unchanged file size/mtime with changed bytes, budget exhaustion and configuration changes.

Do not claim optional LibTooling probe output is a qualified production-equivalent backend. Preserve existing source-transform proof and consent requirements. A pure query must not acquire a workflow claim or host GPU lease. Expensive indexing may use optional host admission with a declared scope, but standalone operation cannot require a Project Control database.
