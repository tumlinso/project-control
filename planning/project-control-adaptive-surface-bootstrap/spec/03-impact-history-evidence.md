# Deterministic impact, temporal trace, and evidence

## Do not equate relatedness with impact

The observed `impact_preview` uses explicit entities plus lexical seeds and one-hop graph neighbors. It is a useful backend starting point, not a complete dependency tracer. Its “proven impacts” are explicit targets; that does not prove that a proposed change breaks them. Replace this presentation with **observed/declared dependency traces, possible related candidates, and uncovered/unknown scope**.

Impact is a conservative answer to: “which consumers/contracts/build outputs/tests/notes require attention for this change?” It is not whole-program behavioral equivalence, a guarantee of exhaustive dynamic dispatch resolution, or an automatic test-passing claim.

## Canonical graph and direction

Node identity is project UUID + repository identity + kind + stable provider/entity identity. Relative path alone and `kind:id` alone collide across projects. Store file, symbol, package/interface, build target, generated artifact, task, gate/test, configuration, and semantic-note/decision nodes where supported. Keep source snippets outside adjacency indexes.

Normalize directed semantic edges, with relation, origin (`observed`, `project_declared`, `candidate`), producer/provider identity, exact source range/evidence, input manifest, generation and resolution status. An edge `consumer depends_on provider` is traversed **in reverse** for dependents-of-a-change. Calls/imports/includes/build inputs/generation/consumption need explicit direction and propagation rules. Containment, document mention, ownership and lexical similarity must not automatically propagate like code dependencies.

An optional declared change class (`body`, `interface`, `configuration`, `generator`, `removal`, `unknown`) can select conservative propagation rules. Unknown change expands conservatively. Export/interface fingerprints may stop unnecessary re-resolution, but changing an implementation can still change behavior for callers; do not prune behavioral dependents solely because a type signature is stable.

Traverse indexed adjacency incrementally with visited sets and a node/edge/time budget; group strongly connected components or otherwise ensure cycle-safe traversal. Preserve a witness chain from the seed to each result, including edge kinds and project transitions. Report fan-out counts, omitted groups and continuation. Do not silently cap depth at one or two hops. A priority queue can rank for display, but must not silently discard structural paths because a heuristic “cost” is high. Lexical candidates are an explicitly separate, non-propagating-by-default halo.

Snippets are fetched after selecting nodes, directly from the source version associated with the graph. Changed/unavailable text returns stale/refresh/omitted status rather than a guessed snippet. Trace/path mode never has to read all affected source bodies.

## Providers: reuse before building

Ship a provider contract with detect, index/refresh, supported relation kinds, stable identities, input dependencies, coverage and failure semantics. Prefer existing installed tooling/index exports. Do not have Project Control download toolchains or run arbitrary project builds on an observer read.

Required initial coverage for this program:

| Provider | Reliable initial scope | Explicit limits |
|---|---|---|
| Todo semantic/read port | task/interface/gate/checkpoint/ownership/declaration relationships | This is workflow truth, not automatically source-call truth |
| Existing ctxpp/compiler adapters | C/C++/CUDA symbols and relations genuinely returned by the installed compiler-backed provider | Compiler flags, include inputs, generated headers and build configuration must be included; no claim of callers from grep |
| Python syntax/import adapter | modules, explicit imports/relative imports, definitions and directly resolvable references | dynamic import, monkey-patching, reflection and unresolved aliases stay unknown/candidate |
| Package/build/manifest adapter | supported manifest dependencies, registered package mappings, depfiles/compile commands, generated inputs/outputs | Do not interpret arbitrary CMake/shell/code without the real build/tooling result; unsupported constructs are coverage gaps |
| Markdown/semantic reference adapter | explicit links, relative paths, stable entity refs, registered frontmatter/anchors | A mention is not a dependency unless declared as one; prose inference stays candidate |
| Language-neutral import adapter | reuse valid existing SCIP or equivalent symbol/reference exports, preserving producer identity | SCIP references do not by themselves prove call direction, dynamic dispatch, or data flow |
| Generic live search fallback | files and lexical candidates for unindexed/unavailable languages | Never contributes a falsely authoritative complete dependency closure |

TypeScript, Rust, Go, Java/C# and others can participate through installed compiler/LSP/index exports and narrow manifest adapters. The implementation must demonstrate the provider extension contract with at least an additional non-Python example/export fixture; it need not write a whole new compiler front end per language. Tree-sitter is an optional syntax extractor, not a substitute for name/type resolution. Absence of a language provider is visible, not a failure of the entire tool. This is complete heterogeneous-surface support with honest provider-scoped coverage, not a promise of identical semantic precision in every language.

## What is registered, and by whom

Project Control auto-detects recognized languages/manifests and selects registered installed providers. It discovers explicit references automatically. Mutator uses `amend_project` for facts it cannot reliably infer: external package-to-project identity, a generator/output mapping, unusual contract dependency, provider overrides, semantic note anchors and applied skill relevance. A scoped coder can publish a candidate/used-skill context record through existing coordination; it does not acquire unrestricted registry administration.

Declarations remain durable Todo/project semantics; indexes are rebuildable projections. The caller expresses canonical project concepts/relative locators, never raw internal graph row IDs or SQL. Validation resolves ambiguity, checks scope/versions, and labels declaration authority separately from compiler observations. Declarations cannot manufacture proof that the code obeys them.

Cross-project resolution uses package/module/interface identity plus version/environment where relevant. Matching a name alone or sharing program membership is insufficient. A symlink is not a permission to traverse an unregistered project. Foreign projects are followed automatically only through resolved, permitted relationships; report unresolved and inaccessible edges. Source revisions and observation times are per project—there is no invented global atomic snapshot.

## Freshness: file bytes alone are not enough

Cache a provider fragment against **all inputs that determine it**: provider/version/config, source content, imported/exported interfaces, build flags, relevant manifests/lockfiles, environment resolution, generator inputs, and directory/glob/package-map membership when applicable. If B changes exports, unchanged source A may need re-resolution. New C may introduce a consumer of A even though neither A nor its previously known consumers changed.

Use file watchers as invalidation hints, backed by Git status/diff and reconciliation of additions/deletions/renames/untracked relevant files. Handle watcher overflow/restart explicitly. Git HEAD alone is insufficient for dirty trees; mtime/size alone is not content identity. Do not trust the old graph after an unobserved invalidation gap.

On a query:

1. Establish a per-repository observation/index generation and freshness policy.
2. Reconcile known invalidations, including newly created consumers and changed resolution inputs.
3. Refresh affected fragments or report them as stale/unavailable within a bounded budget.
4. Replace each fragment transactionally and invalidate dependent resolutions when its output changes.
5. Traverse a stable recorded graph generation; retain provenance for every cross-project segment.
6. Revalidate exact source spans for snippet output, or return a pinned older version labeled as such.

Unchanged fragment inputs and output fingerprints permit reuse. Semantic registration changes invalidate relevant resolution/graph fragments. Query caches depend on graph generations and discovery scope; not just the seed hash. A relevant unindexed file means closure completeness is unknown. “No known impacts in covered providers” must not become “safe to change.”

Report coverage by provider, repository, relation kind and freshness. Include pending/stale counts and whether a complete graph cut was available for the claimed scope. Human-authored notes become `needs_review` when supporting anchors change, not silently trustworthy and not automatically rewritten by an LLM. Do not scan/hash every file on every warm request: content-addressed fragments, dirty-path reconciliation, resolution dependencies and cached generations are the efficiency mechanism.

## History

Reuse canonical Todo material events, source identity, context fragments and causal links. For Git history, resolve revisions and use ancestry/range traversal (`rev-list`, `log` or the existing Git adapter). The currently observed comparison of commit SHA strings in `services/history.py` is incorrect for ancestry and must be repaired.

All advertised selectors must actually affect retrieval: `from/to` revision/time/commit and task/checkpoint/interface anchors. Scope unknown selectors explicitly; no accepted-but-ignored parameters. Preserve merges/renames/rewrites as far as provider coverage supports, and label bounded event retention. A task's current state is a lifecycle anchor, not a replacement for missing history.

Follow recorded cross-project provenance with independent revision/timestamp labels. A chronological sort can organize events, but only explicit recorded cause/supersession/answer references become causal edges. Return direct source/event locators and material changes rather than administrative noise.

## Evidence

Registered gate metadata and gate execution evidence are distinct. Return gate identity, owner, required/optional status, relevant criterion, latest execution/result, input/source identity, applicability/freshness and artifact locators when available. A pending gate is unvalidated. A current failure is contrary evidence. A current passing result supports the gate's actual criterion. A successful terminal task preserves its frozen completion proof without pretending it validates today's changed source.

Do not promote source/test text, an active claim, or a worker “done” report into proof of the requested behavioral claim. The observed rule `high if support and not contradictions` is not an adequate confidence metric: replace it with typed evidence applicability and coverage, or a clearly limited evidence-summary label. Include relevant missing required gates even when some source matches exist. Do not equate no matching evidence with disproof.

Performance evidence remains source-backed project context when relevant. Removing the universal performance-status/probe tools does not remove benchmark records from evidence or native CUDA workflows. Context notes authored by an agent are attributed assertions; link their raw result artifacts rather than laundering them into current measurement authority.
