# 5. Coherent reads and token-efficient interaction

## One workflow snapshot, multiple projections

The internal core produces one immutable workflow read snapshot under a coherent DB transaction/revision. Status, semantic state, workflow summaries and exports are projections of that snapshot rather than four independently interpreted workflow truths. Typed records belong internally; JSON/dicts remain a serialization boundary. Source and external engine observations retain independent identities and reported skew—no global atomicity is claimed.

Separate durable workflow revision from observation time, lease/heartbeat freshness and external source state. Capture one as-of clock for a response. A cache keyed only by workflow revision must not freeze a fresh heartbeat forever. Test stale/dead sessions and invalidation across unrelated source changes. Read-only observers never repair state.

## Three retrieval levels

A controller usually needs a compact frontier or current-task brief. A researcher receives detailed evidence for a bounded question. Full debug/audit responses are explicit expansions. First remove duplicated envelopes, irrelevant historical worktrees and repeated aliases; do not solve token cost by cutting an important warning or source body in half.

Budget the **whole serialized MCP response**, including top-level project, data, cursor, preconditions, warnings and expansion metadata. Byte budgets and model token estimates are separate reported units. A soft budget may emit complete mandatory units with `budget_exceeded`; a hard budget too small for minimum semantics returns a structured insufficient-budget result rather than corrupted source.

Introduce opaque observation/evidence references that bind exact workspace, permission domain, revision, source identity, fields read and expiry. A reference is not authorization. The server may retain complete preconditions internally or use a verifiable stateless token. Expired/lost references require explicit reobservation; they never silently rebind to newer data. Server restart and different clients must have documented behavior. Full preconditions remain available for reviewed mutation.

## Retrieval correctness is part of economy

Use exact source path/range or symbol identity, requested relation sets and independent cursors per result stream. Advance by results actually delivered, not a fixed offset after discarding entries to meet a budget. Bind cursors to query/filter/sort/schema/source identity. Deduplicate matches referenced from multiple relation buckets instead of serializing each excerpt repeatedly.

Whole declarations/functions should be preserved where semantic context requires them. Report omitted mandatory/optional classes and actionable expansion routes. An immutable-commit request must not silently consult a working-tree semantic index. Lexical matches are not callers/callees proof. Content SHA, metadata identity and index-generation identity must not be conflated. Live façade symlink targets need their own byte identity; a clean Git symlink does not mean unchanged target content.

## Workload-based acceptance

`machine/token_budget_cases.json` defines representative questions and proposed limits. Measure baseline and candidate using the exact same fixture/source identities and available tokenizer. Report whole response bytes, token estimate/method, call count, repeated bytes, evidence coverage, latency and cache state. A target is an engineering acceptance goal, not a promised reduction in subscription credits.

Controller + researcher total input/output and actual usage, when exposed, matter. A root saving 30k tokens by launching three workers that each reread 40k is not automatically a win. Use one researcher by default, bounded topic ownership, compact evidence digests and targeted parent re-fetch. Keep omitted counterevidence and uncertainty visible. Do not force delegation for a simple local lookup.
