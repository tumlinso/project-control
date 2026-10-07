# PA1 single-pass testing handoff

## Candidate and boundary

- **Candidate source:** `b9806e2373a97236f6bc73ec970238cef4dfb371`.
- **Immutable candidate:** `/home/tumlinson/.local/share/project-control/releases/unified-20261007-finalize-r10`.
- **Release manifest SHA-256:** `41dd9924e30ca854f53eaad11cf7073c43c3eaae4b321574f6ab5f04004e24a7`.
- **Skills source:** `5c69981f8d1755c3408e4f45adbf340c02c4dfe8`; CUDA skill changes are preserved.
- **Activation:** R10 is selected through `/home/tumlinson/.local/share/project-control/current`. The candidate-bound HTTP observer remains available; inference is stopped with the durable release veto active. The final model-free census at `/home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/final-runtime-census.json` reports `verified=true`, no errors, inactive inference, no GPU applications/resources, no active jobs or slots, automatic preparation off, and the veto active.
- **Current focused source check:** at the candidate source commit, `scripts/pa1_source_tests.sh -m pytest -q tests/assistance/test_owned_release.py tests/assistance/test_broker_close_persistence.py tests/assistance/test_execution_cleanup_recovery.py tests/assistance/test_lab.py tests/assistance/test_lab_snapshot.py tests/assistance/test_lab_cli.py` passed **108 tests, 1 skipped, 9 subtests** in 8.23 seconds. This is source-level evidence only. Recorded check summary: `/home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/source-checks-final.txt`.

Source changes include exact dead-owner/recovery attestation and stale-slot cleanup, explicit private snapshot-parent modes, and scoped LAB planning on the `narrow` compute profile with `layer` parallelism. Previous source checks also covered warm-status projection, bounded CLI deadlines, cancellation, and foreground GPU handoff fixtures. These checks do not establish live model or device behavior.

## Existing live evidence and current limits

The last R10 scoped CPU attempt is **not qualified**. Its planner failed with `lab_planner_incomplete_proposal`; the recorded sequence contains no `effect_intent`, so no experiment launched. The scope was cancelled, with `cleanup_pending=false`. Preserve this as a failed pre-effect attempt and collect the error artifacts at `.../practical-final-r1/cpu-r10/`; do not rewrite the receipt or repeat the same case in the same testing pass.

Older R6 evidence showed concurrent layer-split role calls on GPU pairs 0+2 and 1+3 without reload. Older R7 returned a source-grounded answer with a conservative freshness warning. Those results are historical and limited to their candidate lineage; they do not qualify R10. R8/R9 CPU attempts also failed before effects for recorded reasons. The mandatory 40-case matrix remains partial and `final_acceptance` remains false.

R10 has not had a comprehensive entrypoint pass, a fresh grounded-answer pass, a successful scoped CPU experiment, or a live CUDA yield/run/release/rewarm/resume pass. In-flight cancellation and restart recovery have only source/fixture coverage. Model quality, latency, residency under current R10, and full acceptance remain unvalidated.

## One-pass execution protocol

Run this as one predeclared pass. Create a new mode-0700 private artifact directory for each live helper; preserve every intent, output, status, error, cancellation, and cleanup receipt. Record all failures and continue only with independent cases whose safety preconditions still hold. Do not patch and rerun during the pass; aggregate failures for a bounded follow-up. Use the selected installed CLI at `/home/tumlinson/.local/bin/project-control`, the registered fixture project/source/skill IDs, and the canonical state stores. Keep automatic preparation off.

### Unified entrypoints and grounded answers

Start with cold/model-free identity checks:

```sh
project-control --help
project-control assistance status
curl -sS -i http://127.0.0.1:8768/healthz
curl -sS -i http://127.0.0.1:8768/version
curl -sS -i http://127.0.0.1:8768/readyz
```

Expected: all entrypoints resolve to the same selected R10 manifest; health/version return 200; readyz returns 503 while the release veto holds. Reconnect a fresh Codex MCP transport and verify initialize plus `tools/list` reports the unified 12-tool schema (expected schema SHA-256 `9223c29700eac956491b838388ba3a1da20caa68951bfc0a0d361c8d2b13595e`). Run from `/home/tumlinson/project-control`. Substitute the fixture identifiers from the registered workspace/skill inventory before execution. Explicitly clear the release veto immediately before this live block (`project-control assistance resume --release`); this does not start inference. Then make one registered-source question and one registered-skill question with the installed harness below. Require answer citations to match the exact registered source and review the answer for grounded claims; a harness receipt alone does not accept answer quality.

```sh
python /home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/run-selected-no-bytecode.py \
  scripts/qualify_assistance_installed.py --execute-live \
  --project <registered-fixture-project> \
  --question "Summarize the documented behavior of <specific fixture> and cite its source." \
  --skill <registered-fixture-skill> \
  --skill-question "What does this skill say about <specific behavior>? Cite the source." \
  --out-dir /tmp/pa1-r10-entrypoint-grounded-<new-id> \
  --wall-seconds 600
```

After each helper stops inference, clear its durable release veto explicitly before the next live case. The harness covers selected-release startup, grounded inquiry/skill use, both configured roles, and cleanup in one bounded invocation. Preserve its `receipt.json` and command artifacts. Do not use a cached answer as evidence of a fresh inference; inspect the receipt and inquiry IDs.

### Scoped CPU LAB

The helper defaults to a cold preview. The following is the one authorized live CPU attempt; use a fresh absolute artifact path outside the checkout:

```sh
python /home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/run-selected-no-bytecode.py \
  scripts/qualify_assistance_scoped_lab.py \
  --project-control /home/tumlinson/.local/bin/project-control \
  --project project-control \
  --source planning/project-assistance-v1/fixtures/repository/demo/pairs.py \
  --goal "Compare pair_sum([1,2,3]) with sum([1,2,3]); report the observed values and cite the source." \
  --wall-seconds 600 --max-experiments 2 \
  --artifact-root /tmp/pa1-r10-cpu-lab-<new-id> --execute-live
```

Expected success is a completed bounded session with verified cleanup and a source-backed counterexample (the fixture's pair sum is 3 versus the built-in sum 6). A planner/proposal failure before an effect is a failed LAB case, even when cancellation succeeds. Inspect `qualification-receipt.json`, `preview-*`, `authorize-*`, `run-*`, `status-*`, and `cancel-*`; do not edit or retry the proposal during this pass.

### CUDA yield, run, release, and rewarm

Only proceed if CPU case records are complete, CUDA admission is available, and no foreign/native workload conflicts with the four explicit devices. The smoke source requires **exactly four visible devices**, so include every UUID below. Use the approved toolchain root and let the scoped LAB integration invoke the CUDA foreground controller; this is the path that exercises inference yield, resource ownership, cleanup/release, rewarm, and LAB interpretation together.

```sh
python /home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/run-selected-no-bytecode.py \
  scripts/qualify_assistance_scoped_lab.py \
  --project-control /home/tumlinson/.local/bin/project-control \
  --project project-control \
  --source scripts/pa1_gpu_smoke.cu \
  --goal "Compile and run the fixed four-device correctness smoke; report all per-device checks." \
  --tool cuda \
  --gpu-uuid GPU-21131915-1488-23af-38dd-1743ae1f5cc8 \
  --gpu-uuid GPU-d745da9c-7649-8334-41f1-483afa6f3206 \
  --gpu-uuid GPU-cf22c41f-5b58-77b1-3535-8fadd1ca6505 \
  --gpu-uuid GPU-6c1cac7f-a360-0aef-ba98-2828bfd1db1a \
  --toolchain-root /opt/nvidia/hpc_sdk/Linux_x86_64/26.1/cuda/12.9 \
  --wall-seconds 600 --max-experiments 2 \
  --artifact-root /tmp/pa1-r10-cuda-lab-<new-id> --execute-live
```

Expected smoke output reports four passed devices, each checking 4096 elements and its expected sum; effect receipts must prove the CUDA owner released all four `accelerator:<GPU-UUID>` resources and Project Control re-warmed before interpretation. A preview, short reservation, `CUDA_VISIBLE_DEVICES`, or standalone kernel run is not a yield/release/rewarm pass. If the LAB path does not invoke the pinned controller and provide ownership receipts, record the case blocked and do not substitute a manual GPU run.

### Cancellation and restart recovery

For scope cancellation, use only the ID returned by the preview, then verify terminal status and effect cleanup:

```sh
project-control assistance lab cancel <scope-id>
project-control assistance lab status --scope-id <scope-id>
```

For restart recovery, use a separate scope and checkpoint its ID and current phase before a controlled graceful service stop. Do not kill a system process or delete/recreate state. Verify the same scope after the service is explicitly started; resume only when status proves there is no unknown/in-flight effect. An effect with unknown outcome must remain unreplayed and be reported for reconciliation.

```sh
project-control assistance status
project-control assistance stop
project-control assistance lab status --scope-id <scope-id>
project-control assistance resume --release
project-control assistance start
project-control assistance lab status --scope-id <scope-id>
# Only if the persisted state is safely resumable:
project-control assistance lab resume --scope-id <scope-id>
```

Capture before/after status, scope/job/effect IDs, process identity, and receipts. A restored process or status response does not prove an effect completed; rely on durable effect and cleanup records. The durable release veto must remain effective until an explicit `assistance resume --release` is issued by the operator.

### Final cleanup

Cancel any remaining test scopes and ask the owner to stop inference. Verify model-free status, then preserve the independent census and all per-scope cleanup receipts:

```sh
project-control assistance stop
project-control assistance status
python /home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/run-selected-no-bytecode.py \
  /home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/final-census.py \
  --expected-manifest-sha256 41dd9924e30ca854f53eaad11cf7073c43c3eaae4b321574f6ab5f04004e24a7 \
  --artifact /tmp/pa1-r10-final-census-<new-id>.json
```

Run host-level census/control with the necessary host permissions; sandbox-hidden devices or service buses are not valid physical-release evidence. Pass requires inference service inactive with PID 0, all owned GPU processes/resources released, no active owned jobs or slots, no pending release, automatic preparation off, and release veto active. Keep the candidate HTTP observer only if its health/version identity matches R10. Do not claim physical release from `pending`, idle time, or service state alone; use the final runtime census and resource-owner receipts.

## Safety controls and evidence locations

The canonical stores are under `/home/tumlinson/.cache/project-control/as1-observer-analysis` and host runtime `/tmp/codex-todo-orchestrator-1000`; inspect them through supported Project Control interfaces only. Keep experiments inside their displayed source scope and private scratch artifacts. CPU LAB limits are one CPU, 1 GiB RAM, 32 processes, no swap, 30-second execution, 64 KiB captured output, and 64 MiB artifacts. GPU LAB has explicit UUID admission and 60-second build/run bounds. No dependencies, network access, canonical-source writes, Todo projection edits, database edits, service-wide process kills, or replay of ambiguous effects.

Existing run artifacts are under `/home/tumlinson/.local/state/project-control/qualification/pa1-20261007/practical-final-r1/`, including the failed `cpu-r10/` attempt and root final census/source-check receipts. Append each new run under a new private directory, preserve historical failures, and return a single evidence index with candidate identity, commands, outcome per case, receipt paths/digests, remaining uncertainty, and cleanup proof. This handoff describes testing work; it does not authorize activation or acceptance beyond the explicit testing authorization already provided.

The root transferred the native `PC-PA1-LAB` task in run `PC-PA1-RUN-1` through the supported handoff API; it remains pending live qualification. Private receipt: `.../practical-final-r1/testing-handoff-receipt.json`. Later documentation commits do not change the frozen candidate source commit above.
