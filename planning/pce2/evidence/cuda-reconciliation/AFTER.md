# Selection-only after comparison

The same 32 questions, 16,384-byte output budgets, recording analysis provider and exhausted continuations were replayed after atlas/manifest and parser changes. No GPU or model calls occurred. Corpus and code-file hashes, actual lexical queries, stable IDs, resource roles, packet ordering, visible evidence and continuations are pinned in `after.json.gz`; raw inputs are in `after-corpus.zip`. The audit also verifies that all selected machine sections occur in recording packets, and that reported total sections equal the independently retrieved set.

| Metric (sum across 32 queries) | Before | After |
|---|---:|---:|
| Selected sections | 6,888 | 1,833 |
| Atlas section occurrences, including navigation | 3,391 | 830 |
| Stable-heading section occurrences (including navigation) | 2,372 | 699 |
| FULL aggregate section occurrences | 599 | 0 |
| Exact duplicate selected-text excess | 238 | 0 |
| Exact duplicate lexical-excerpt excess | 1,007 | 294 |
| Selected text bytes | 5,269,524 | 2,875,632 |
| Recording packet bytes | 7,894,560 | 4,049,753 |
| Output bytes over all continuation pages | 617,837 | 376,366 |
| Continuation pages | 60 | 40 |

Packet bytes fell 48.7%; selected bytes fell 45.4%. Stable atlas card participation remains 32/32 queries. All 193 resources with recognized stable heading IDs preserve their link-destination-normalized technical bodies; 190 changed paths. The same topic-regex proxy matched all 32 queries in each phase. This broad proxy measures topic-reference presence only.

Eight fixed target expectations, derived from original canonical titles before the after run, give a more discriminating check. Expected stable targets were selected in 7/8 before and 5/8 after, and visible in 5/8 in both phases. Q06 lost both expected shuffle/lookup cards M04/C03; Q27 lost persistent-queue card M30. Tensor numerical-fingerprint E10 was absent for Q30 in both phases. These are recorded coverage gaps, not silently adjusted targets. Q20 gained visible M35 and placed it in the first eight packet sections. Q10 gained visible R05. Q01's first eight packet sections are operational guides; creative Q15's first eight include canonical C17, C03, C08 and C37. Q27 instead begins with deep references and needs root review.

Ordinary FULL exclusion is asserted over all 32 selected sets. Separate bounded explicit reads confirm excluded FULL/generated resources remain readable (`explicit-reads.json`). The eight stable-ID expectations and all per-query before/after roles, packet/visible ordering and coverage discrepancies are in `comparison.json`; summary hashes and totals are in `comparison-summary.json`. Both receipt audits pass, and no reported-versus-visible count discrepancies occur at these 16,384-byte budgets. This does not qualify smaller budgets or live model answer quality.

The root owns final acceptance and Git integration. Baseline commit: `4090f28`. After artifacts remain uncommitted for root review.
