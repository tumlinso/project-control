# PC-WF2-T05 — Measure end-to-end token economy and answer coverage

**Status: proposed, not implemented.** Native role: `implementer`. Profile: `{"context_depth": "focused", "difficulty": "complex", "risk": "high", "work_type": "performance"}`.

## Purpose and mechanism

Create a fixed workload corpus and retain old/full, new/compact and delegated-digest transcripts with tokenizer ID or clearly labelled estimate. Include child input/output, duplicated startup context, retries and root rereads.

## Inputs and ordering

Prerequisites: `PC-WF2-T04`.

Preceding tasks in the same lane also impose serial order. A producer being done is insufficient if its qualified source is absent from this worktree. See `09_PARALLELISM_AND_INTEGRATION.md`.

## Source ownership

Write scope: `src/project_control/normalize.py`, `src/project_control/response_budget.py`, `src/project_control/observation_refs.py`, `src/project_control/services/source_context.py`, `src/project_control/services/overview.py`, `src/project_control/services/frontier.py`, `src/project_control/services/planning.py`, `tests/wf2_acceptance/t`.

Read the sealed package and exact source ledger references. New source/test paths in this plan are **proposed**, not claims that files already exist. Source scope changes require the actual authoritative workflow; never bypass a denied operation with direct SQL.

## Acceptance

1. **PC-WF2-T05-A01:** Compact narrow-read bytes improve by target 60 percent on the recorded corpus without dropping required facts.
2. **PC-WF2-T05-A02:** Root intake improves by target 70 percent for deep-research cases, or non-promotion is documented.
3. **PC-WF2-T05-A03:** Total model usage is reported where observable; no guaranteed subscription savings are fabricated.

## Evidence and completion

Gate `PC-WF2-T05-G` invokes the delivered runner. Evidence kind is `tests`. No acceptance record is shipped as passed. The runner reads an explicit external `WF2_BINDINGS` file.

Implement the acceptance file `tests/wf2_acceptance/t/test_pc_wf2_t05.py` and the exact cases:

    WF2Acceptance.test_compact_narrow_read_bytes_improve_by_target_60_percent_on_the_recorded_corpus_without_dropping_r
    WF2Acceptance.test_root_intake_improves_by_target_70_percent_for_deep_research_cases_or_non_promotion_is_documented
    WF2Acceptance.test_total_model_usage_is_reported_where_observable_no_guaranteed_subscription_savings_are_fabricated

These are proposed test names, not existing tests or empty stubs. They must execute meaningful assertions against real code and include the corresponding negative controls. Additional existing regression suites are required by the task mechanism and review record. Zero tests, skipped cases and expected-failure substitutions fail qualification.

Commit source under test, run the gate, and place review/evidence outside repository roots. Each assertion must cite hashed evidence. An independent reviewer checks the substance; JSON shape alone does not prove correctness. Publish owned interfaces before completion where required. Preserve negative results and report external blockers honestly.

## Delegation and autonomy

After the user launches the controller, execute within this task and the accepted scope without asking for every engineering choice. Prefer the configured inexpensive worker; request a bounded read-only researcher for deep archaeology and a capable independent reviewer for consequential claims. Keep model settings outside task semantics. Do not load the full catalog into every worker; use this sheet, its producers and relevant source only. See `07_CODEX_AGENT_ROUTING.md` and the lane handoff.
