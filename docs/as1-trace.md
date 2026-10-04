# AS1 dependency traces

`TraceService` is the callable host port for `InformationService(impact_provider=...)`.
The host supplies registered workspace configuration, canonical snapshot and
optional `Service.project_context()` readers, a trusted `ContextHost`, and any
installed read-only export providers. It never accepts a caller principal,
project UUID, unregistered filesystem root, raw graph key or SQL selector.

```python
trace = TraceService(config, snapshots, host, semantic_provider=project_context,
                     providers=(CtxppExportProvider(compiler_inputs), installed_export))
information = InformationService(config, packets, snapshots, host,
                                 impact_provider=trace)
result = information.call('impact', project='demo', targets=['src/provider.py'],
                          mode='paths', change_class='body', max_nodes=200)
```

The direct port accepts `targets` (one to 32 relative paths or exact objects with
`project`, `repository`, `kind`, `id`, `path`), `mode=paths|snippets`,
`change_class=body|interface|configuration|generator|removal|unknown`, node/edge/time
budgets, an optional lexical `query`, `since` generation, and a continuation
`cursor`. Unsupported parameters/selectors fail. File selectors seed every
indexed symbol in the same file, as well as its file node. Multiple matching
repositories remain ambiguous until a repository is specified. Repository
identity combines the registered alias and Git common-directory identity;
node identity also includes project UUID, kind and stable producer identity.

The result reports observed/declared dependencies with directed witness chains,
provider state and input manifests, per-repository Git observations, unknown
scope, nonpropagating relationships, fanout and cycle/shared-path counts. It
never calls a structural trace a proven behavioral break or a safe change.
Consumer-to-provider imports/includes/calls/build inputs/dependencies are
traversed in reverse. Producer-to-output generation relations traverse forward.
Ownership, containment, documentation mentions and lexical candidates do not
expand the dependency closure. Change classes currently all use conservative
propagation, including body changes with unchanged signatures.

A breadth-first adjacency traversal has visited sets and explicit node/edge/time
budgets; there is no implicit depth cap or heuristic path rejection. When a
budget is exhausted, omitted groups and a generation/request-bound cursor are
returned. Repeat with a larger budget and that cursor to recompute the bounded
prefix in the same generation. A changed generation requires refresh; this is
not a persisted paused traversal. Deltas report added, removed and changed edge
records plus full provider state; eight generations are retained in this service
instance. Restart/eviction returns refresh-required rather than an invented empty
delta. Graph records and fragment replacements publish under one service lock.

## Provider coverage

* The existing `ProjectGraph` supplies canonical Todo entity/dependency records;
  the overlay retains original direction and `project_declared` provenance.
  Workflow ownership and evidence metadata remain distinct from source-call proof.
* Python AST fragments resolve explicit modules and relative imports, including
  imported child modules. They retain exact import lines and whole-file content
  digests. Definitions/export fingerprints participate in resolution inputs.
  This is syntax import coverage: reflection, dynamic imports, attribute shadowing,
  monkey-patching, imported aliases and dynamic dispatch remain gaps.
* TOML/JSON adapters record supported `pyproject.toml`, `package.json` and
  `Cargo.toml` dependency declarations. They do not infer external package-to-source
  mappings or execute a build. Unknown versions/environment mappings remain
  unresolved. Explicit canonical relations can bind those identities.
* Markdown explicit relative links are typed `mentions`; prose is not promoted
  into dependency evidence. Changed supporting note anchors become `needs_review`.
* Canonical semantic context must have the observed owner UUID and revision.
  Exact relation repositories resolve only through trusted registration (alias,
  Git identity or that registration's exact root). `uses` is a declared dependency;
  foreign `identity` targets require explicit `target_version` matching the
  foreign owner's registered identity version. Both owners' semantic revisions
  and registry hashes bind the edge. Unsupported/inaccessible relationships stay
  visible and untraversed. `generation` declarations use authority-repository
  roots and typed exact input anchors; source changes make those edges stale.
* `CtxppExportProvider` reads existing native `CTXPP-INDEX/1` symbols, directed
  call edges and file include exports plus `CTXPP-MANIFEST/1`, independently
  checking confined file bytes. It does not run ctxpp, download a toolchain,
  invoke a build, or use mtime metadata as semantic proof. The native include
  export does not retain exact include lines and reports this range omission.
  A trusted installed `config_observer(observation, meta)` returns typed input
  determinants and `verified=True` only after producer-native normalization
  agrees for configuration, command recipes, core/environment, generated inputs
  and resolution dependencies. Absent verification returns unknown freshness;
  degraded/incomplete exports and source mismatch remain partial/stale.
* Installed language-neutral exports implement `TraceProvider`: `name`,
  `detect(observation)`, `observe(observation)` for the full typed determinant
  manifest, and `refresh(observation, inputs)` returning `ProviderFragment`.
  The service validates entity/edge schemas, provider/generation/input identities,
  supported relation kinds and registered project scope. Canonical provider
  options are passed in the observation only when the installed port declares
  those keys in `supported_options`; unsupported options return partial. Host
  registry and revision inputs invalidate prior installed fragments. Relative file inputs
  are independently checked with confined reads. Non-file determinants are the
  trusted producer port's responsibility; a callback is not a compiler frontend.
  A TypeScript directed export fixture demonstrates extension without another
  public tool. Unsupported languages and producer failures do not fail the
  whole tool, but make coverage partial.
* Optional ctxpp/Git lexical inspection uses the existing `CtxppReadAdapter` and
  remains a separate nonpropagating candidate halo. Grep never manufactures calls.

Completeness is reported per provider, repository, relation set and freshness,
including providers at every reached project. Syntax coverage is deliberately
partial even when all known import edges resolve. No uncovered language or
unresolved edge can produce an authoritative complete dependency closure.
Per-project observations are independent; there is no global atomic source
snapshot. This module supplies the information port; profile and app wiring
belongs to the downstream surface task.

## Incremental source identities

Initial refresh reads supported source units. Warm reconciliation uses Git
tracked/untracked membership, Git dirty content identities and stat identities
as invalidation hints. Re-read fragments carry SHA256 content identities rather
than mtime proof. Unchanged AST/text fragments are reused; membership, manifest,
configuration and exports fingerprints re-resolve unchanged consumers. Renames,
deletions, new/untracked consumers and semantic registry changes alter generations.
Watcher loss forces syntax reconciliation, clears installed fragment/digest caches,
and reports its coverage effect. Reads use the existing descriptor-confined source
port; symlink leaves and intermediate symlink components are refused. Provider
input digest caches also bind parent descriptor identities.

Every repository and semantic revision is revalidated after provider operations.
Concurrent mutations stale all affected graph segments, including foreign edge
ends. Paths mode does not fetch affected source bodies after selection. Snippets
read only selected nodes and compare exact graph content identities; changed or
unavailable bytes return stale/omitted, never guessed text. Snippet text uses the
canonical redaction policy and reports the selected line range and omitted lines.

The current cache is process-local, and Git membership/status reconciliation is
bounded to the installed Git adapter's behavior. The tracer does not claim a
persistent watcher service, transactional cross-repository snapshots, exhaustive
CMake/shell interpretation, or biological/production validation. Tests run real
Git repositories, canonical packet integration, disposable explicit source-bound
native-format C++/TypeScript exports, genuine installed ctxpp production on a
disposable source tree, races and invalidations. The genuine producer case binds
normalized config/compile commands and current core/tool/compiler/source hashes,
while preserving unknown runtime-library/system-header dimensions. Fixture configuration
verification is not deployed compiler-provider qualification.
