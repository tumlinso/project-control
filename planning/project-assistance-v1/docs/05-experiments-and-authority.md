# 5. Useful experiments without unsolicited project mutation

## Separate behavior from capability

A “reviewer” name is not a security boundary. Define trusted capability envelopes independently of gather/reason/experiment behavior:

1. **Observe:** existing registered-source and information access, existing bounded read-only command sandbox, no new project execution or publication authority.
2. **Scratch:** an explicit user/operator grant permits temporary code and permitted tests against a captured source snapshot, within CPU/memory/storage/time and optional GPU limits.
3. **Project mutation:** only an explicit task/delegation with canonical Todo scope, capabilities, acceptance, and gates. It is not inherited from Scratch.

Keep remote observer tools read-only under their current contract. An ordinary question must not silently launch newly authorized builds, network operations, GPU tests, or canonical publications. It can consume already collected permitted experiment evidence, or explain what experiment needs approval. Initial interactive experiments go through the local operator controller. Any broader observer-triggered experimentation is a product/permission decision, not a prompt tweak.

## Reuse isolation, not acceptance side effects

The retained local-worker machinery already distinguishes source identity, detached work, baseline tests, a candidate patch, and guarded parent acceptance [S10–S12]. Reuse that materialization and comparison logic where appropriate. Do not invoke its canonical apply/accept path from a background experiment.

Capture the exact source snapshot used: repository identity, commit, dirty tracked overlay, relevant untracked files, configuration and dependency determinants. Verify capture rather than mixing half-old and half-new source while another agent edits. A linked worktree alone is not a security sandbox and can share Git administrative state. Prefer a detached materialization or private copy with the canonical checkout and Git control paths not writable or reachable through a writable shared index. Do not reset/clean the user's working tree to obtain a convenient baseline.

Build and execute in a service-owned experiment directory with a private home, temporary directory, caches and output locations. Mount only required source/toolchain inputs read-only. Disable network and credentials by default. Bound process count, CPU, memory, disk/output bytes and wall time, and clean up owned process groups. Prevent host socket, Git metadata, device and environment leakage. Compilation and unit tests are arbitrary code execution even when the command is named `pytest` or `ctest`; containment must apply to descendants too.

If the necessary sandbox or dependency set is unavailable, return an honest limitation. Do not automatically install packages, download models, enable networking, or weaken containment. Permit narrowly approved dependency preparation as a separate operator action.

## An experiment is a question with an evidence contract

Before execution, record the hypothesis, input identity, reference/baseline, proposed variant, permitted actions, resource budget, expected measurements, stop rule, and what would alter the conclusion. After execution retain commands, toolchain/environment identities, exit status, test counts, bounded output, artifact hashes, timings, and limitations. Keep generated patches separate from the source snapshot and identify their exact base.

A passing generated test only establishes what it exercised. An unexecuted test plan is not a test result. Compilation failure is not evidence that a mechanism is fundamentally bad. A microbenchmark speedup is not an end-to-end win. Compare like-for-like inputs, correctness, preparation cost, warm/cold conditions, and resource interference before recommending promotion.

For a Cellerator-style reusable structure, a useful small experiment measures preparation cost P, baseline per-use cost B and prepared per-use cost H. When B > H, P/(B-H) estimates a break-even reuse count under those measured conditions; if B <= H there is no finite break-even in that simple model. This is an analytical aid, not a substitute for measuring the actual workload. The included fixture uses synthetic numbers only.

## GPU experiments and the two-slot constraint

An experiment requiring the model's GPUs must not wait while its parent holds those GPUs for inference. Save the continuation and experiment request, release the model session, then let the non-model runner obtain the normal foreground CUDA lease. The existing host interlock decides the interference domain. Full inference eviction is verified before the benchmark starts. After it ends, retain results, release experiment resources, and only then allow a continuation to reacquire inference if current power policy permits.

Record relevant topology, device identities, driver/toolchain settings and interference conditions. Never kill an unrelated process to obtain resources. A busy or uncertain resource state yields deferred/unavailable, not unsafe execution. The current hardware observation showed V100 NV6 pairs 0–2 and 1–3, but every real execution must use current verified topology [O03, O04].

Model-free test execution can continue while both model slots are suspended, subject to separate host limits. A slot being free is not proof that spare RAM, CPU, disk bandwidth, or a GPU interference domain is free.

## Replay, cancellation and promotion

Persist effect intent and an attempt identity before starting. On restart, reconcile the exact owned process and any complete receipt. Do not duplicate an active test or report a partial artifact as complete. Ambiguous failures remain unknown/interrupted, with bounded fresh-attempt reruns where permitted.

An interesting experiment can produce a notebook record, a suggested follow-up, or a candidate patch. It cannot create project Todos, publish an architecture decision, apply a patch, complete a parent task, or commit/push without the existing explicit authority. Reactivating legacy `delegate_task`/`collect_delegation` is independent of enabling the scratch lab and stays gated by an operator decision [S03, S08].

Negative and inconclusive results are acceptable outcomes when correctly scoped. The aim is to resolve uncertainty economically, not to force every experiment into an improvement proposal.
