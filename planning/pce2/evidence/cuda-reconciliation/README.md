# CUDA retrieval reconciliation evidence

This is a **selection-only** replay of 32 researcher-defined CUDA/V100 questions. It uses the repository's actual `SkillContext`, registry, lexical index, ranking, semantic expansion, routing, packet construction and continuation code. A recording callback cites packet IDs and returns a fixed placeholder. There are **zero GPU/model calls**; topic-regex presence is a reference-coverage proxy, never answer correctness, model quality, performance, or scientific qualification.

The original capture completed before source or corpus edits. `before-corpus.zip` preserves every registry-visible raw resource, including binary archive inputs. `before.json.gz` records source commit and file hashes, corpus identity, text-resource identities, manifest resources and edges, original links, normalized body hashes, selected section IDs/provenance/bytes, exact lexical hits, full recording packets, budget-visible pages, reported coverage, routes and continuation cursors. Normalization replaces only relative Markdown path-link destinations; prose, external URL destinations, fragment links and link labels remain intact. Stable manifest and heading IDs allow atlas filename changes to be compared separately from content.

Baseline: 32 machine routes; 6,888 selected section occurrences across queries, including 3,391 atlas occurrences and 599 FULL aggregate occurrences; 238 exact duplicate selected-text excess occurrences and 1,007 exact duplicate lexical-excerpt excess occurrences. Selected text totals 5,269,524 bytes; packets total 7,894,560 encoded bytes; all continuation outputs total 617,837 encoded bytes. These are sums across queries, not unique corpus sizes. All 32 queries match their topic regex in at least one selected section. Each query exposes selected, packet-considered, and actually visible coverage separately; page `coverage` retains the implementation's reported counts for discrepancy inspection.

No resource role is inferred as authority. Original manifest metadata is retained when present. FULL identification is a filename-based descriptive counter; the baseline parser has no aggregate-role policy. Exact duplicate counters use identical full section text or identical lexical excerpt text, respectively, and count occurrences beyond the first; overlapping nonidentical spans are not counted as duplicates.

Run from repository root:

```sh
.venv/bin/python planning/pce2/evidence/cuda-reconciliation/replay.py after
.venv/bin/python planning/pce2/evidence/cuda-reconciliation/audit.py planning/pce2/evidence/cuda-reconciliation/after.json.gz
```

Receipts and snapshots refuse overwrite. The replay exhausts continuations, checks inventory stability, and pins each selected text identity through ordinary service revalidation. `audit.py` verifies all pinned text hashes against the raw ZIP, query count, skill/section IDs, packet limits, output budgets, continuation completion and zero GPU calls.

Baseline validation: `tests/test_skill_context.py`: **14 passed in 1.04s**. `before-audit.txt`: receipt audit **PASS**. `before-summary.json` provides the exact compressed receipt hash.
