# Final selection-only replay

The same 32 questions, frozen eight stable-ID expectations, 16,384-byte budgets and recording callback were replayed against final source/corpus after the shared pruning fix. All continuations completed and all selected machine section IDs appeared in recording packets. There were zero GPU/model calls. This evidence measures reference selection, transport and displayed excerpts, not answer correctness, model quality or performance.

| Metric, summed over 32 questions | Before | Final after2 |
|---|---:|---:|
| Selected section occurrences | 6,888 | 2,336 |
| Atlas occurrences, including navigation | 3,391 | 984 |
| Technical atlas card section occurrences | 2,341 | 810 |
| FULL aggregate occurrences | 599 | 0 |
| Exact duplicate selected-text excess | 238 | 0 |
| Exact duplicate lexical-excerpt excess | 1,007 | 293 |
| Selected text bytes | 5,269,524 | 3,175,201 |
| Recording packet bytes | 7,894,560 | 4,637,406 |
| Output bytes over continuations | 617,837 | 429,332 |
| Continuation pages | 60 | 45 |

Selected bytes fell 39.74%, packet bytes 41.26%, and output bytes 30.51%. All 32 questions retain technical atlas card participation. All 190 technical cards retain their stable IDs and link-destination-normalized bodies, and all 190 changed filenames. Of 193 stable-heading resources, 192 bodies are unchanged; A00/START_HERE's navigation-only instruction update is intentional. The separate README navigation change and reversible substitutions are recorded in `packaging/cuda-reconciliation/atlas-navigation-delta.json`. FULL's exact byte identity remains unchanged and explicit reads remain available.

Frozen stable targets are selected and recording-packet-considered in 7/8 questions before and after2. Q06's M04/C03 and Q27's M30 are restored following the first-after regressions. E10 remains absent for Q30 in both phases. Visible target coverage improves from **4/8 to 6/8**, and first-eight-packet coverage improves from 4/8 to 5/8. Q06, Q10 and Q20 gain visible expected references; **Q27 still loses budget-visible M30 and top-eight placement**, although its full recording packet contains M30. That individual presentation limitation remains and must not be hidden by aggregate improvement. The earlier first-after prose's 5/8 baseline visible count was an arithmetic mistake; the exact receipt comparison shows 4/8.

Q01's first eight packet sections are operational guides. Creative Q15's first four are canonical C17, C03, C08 and C37. Q27 starts with deep references. All 32 routes are machine routes serviced here by the fixed recording callback. Topic-regex presence remains 32/32 in both phases, a deliberately weak proxy. No reported-versus-visible count discrepancies occur at these budgets.

`after2.json.gz` pins final source hashes, corpus/resource identities, roles, stable IDs, packet ordering, actual lexical queries, bytes, visible coverage and continuation cursors; `after2-corpus.zip` preserves raw final inputs. Receipt SHA256: `cda42a57d05d87400d749856d59eb7431cb82b1bac4cdcc3fe2e0d63c7b382ab`. `after2-audit.txt` passes hashes, skill/section IDs, selected-to-packet coverage, reported totals, packet/output limits and continuations. `after2-comparison.json` and `after2-comparison-summary.json` preserve the full comparison, including all five checks passing: FULL zero, selected duplicates zero, technical bodies preserved, baseline selected targets retained, and atlas participation 32/32. Prior explicit-read identities match final inputs (`after2-explicit-reads-audit.txt`).

Before and first-after artifacts remain intact. Baseline commit is `4090f28`; final evidence remains uncommitted for root integration and acceptance.
