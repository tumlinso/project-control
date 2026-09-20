# Agent-facing review

## Scope and evidence

This pass re-read the ordinary MCP adapter, canonical protocol, task entry and inspection, coordination, delegation/collection, role/capability policies, context composition, completion/gates, runtime binding, preview path and administrative authorization. It also reviewed the PCE1 package itself. Source commits observed were Project Control `f894427c8fd2d3a73f641f946074b1ecc22e33ef` and Skills `688dcc2e92ffc2af690c29734ef9d09c67d8e091`, unchanged from the first review. Relevant earlier lifecycle/host findings are retained explicitly, not claimed as newly reproduced incidents.

References S01–S23 and retained PCE1-E identifiers resolve to repository, commit, path and lines in [evidence/sources.json](evidence/sources.json). Product code/tests were inspected, not executed. One bounded local scout was inconclusive; direct source reads supplied the findings. No production first-class Codex launcher was established by that scout. [O01–O03]

## What makes the present toolkit harder than it needs to be

### 1. Advice often describes the protocol rather than useful work

`next_task` composes task context but recommends `inspect_task`; inspection usually recommends coordination; generic errors recommend `next_task` even when it cannot resolve their cause. Common allowed-action lists are not a precise assessment of what this caller may do now. This encourages low-information protocol loops. Return the goal, usable workspace, real constraints and actual next decision instead. “Ready to implement; no additional workflow call needed” is a valid result. [S01–S03]

### 2. Two authority descriptions disagree

`default_first_class_operations` includes delegation for coordinators and integrators, while the role table omits the `delegate_child` action the kernel requires. The general allowed-tool list does not reveal that contradiction. This is a concrete source inconsistency, not a recommendation to give every role every permission. Derive grant issuance, advertised actions and role validation from a shared policy, preserving scoped writing restrictions. [S04–S05]

### 3. Delegation targets the first file, not the requested task

The kernel's `_child_scope` returns the first sorted file found under an authorized directory, or the first of multiple paths. It does not use the delegated objective for that choice. `auto` selects the write-scope branch. The caller has no source-target parameter at this tool boundary. An agent can write a good objective and still get a child authorized for an irrelevant file. The fix is explicit optional targets plus declared-task defaults, not an LLM tasked with guessing permissions. [S06]

### 4. A smaller path set is confused with a safer delegation

The packet builder requires a strict spatial subset. Consequently, a parent with one file cannot delegate that whole file through this route, even for reading. Meanwhile, the packet factory already accepts actual constraints, source references, gates and interfaces, but the kernel passes a generic constraint and empty reference/gate lists. Safety should come from no authority amplification and controlled concurrent writes—not invented dummy files or deliberately inadequate context. Equal read scope is harmless within the same approved boundary; equal write scope still requires isolation or a real exclusive handoff. [S06–S07]

### 5. Tool presence is not working executor availability

The inspected Project Control binding constructs `WorkflowKernel` without local-worker or source adapters, and those adapters default to `None`. Their paths then return fallback states. A `fork` creates lane records, not necessarily a running process. A tool-capable maintenance delegate is also not the same thing as a local code child whose capability permits collection only. Discover the configured executor class once; bind an existing adapter, produce a ready host handoff, or say unavailable. Do not make the root diagnose model infrastructure to learn which choice exists. This finding applies to the inspected binding, not every possible external candidate. [S08–S10, S23, O03]

### 6. Important payload schemas live behind the public schema

The protocol knows required fields for actions such as arrival and interface publication, but MCP exposes a generic dictionary. Some fields are facts already known to the kernel: current source identity, the unique current task or applicable workspace. Publish typed action shapes and derive those facts when unambiguous. Caller input should be the decision—what to deliver, validate or change—not duplicate bookkeeping. [S01–S02]

### 7. Context machinery exists but the ordinary route does not fully use it

Hierarchical charter/lane/task packets and known-manifest deltas already exist. The `sync` schema accepts cursor/known-fragment fields, but the inspected kernel path does not forward them. Several inspection routes collect large run records before checking the response limit. Context has its own budget while the outer workflow result has another; entry assembles context after the claim transaction. A response-size failure must not obscure an already-committed claim. Wire the existing machinery, budget complete responses, and return retrievable committed receipts. [S02–S03, S10–S12]

### 8. Completion can repeat already requested validation

The integration part of `run_gates` executes required gates, the later task-gate loop can execute them again, and `finish_task` invokes completion gates. These are directly visible scheduling points; this review did not measure their actual GPU cost. Canonical input fingerprints and child-evidence checks already provide a starting point. Reuse must include the whole acceptance input contract and distinguish repeatable correctness evidence from fresh resource/quiescence observations. [S13–S14]

### 9. Validation can have surprising source-changing effects

For integration roles, `run_gates` also drives application/finalization. Separately, the administrative helper expects a sealed complete producer batch. Expose the selected effects before the call: validate, deliver, integrate serially, or integrate a declared batch. Preserve old combined behavior through a tested compatibility path, not as an undocumented default that new agents must discover. [S13; PCE1-E14]

### 10. Collection/fallback semantics invite wasted attention

Running children are normalized into a generic idle result; collecting may persist candidate state despite its read-only annotation. Launch occurs after authorization, leaving failure cleanup to be handled separately. Make each state literal enough to guide the caller, make retries idempotent, and return a meaningful next check or other work. Do not force the expensive originator to poll or reconstruct which child actually launched. [S01–S02, S06]

### 11. Current status can still describe the wrong execution

Default task selection scans active runs in creation order. The frontier labels all prerequisite edges as immediate blockers. Current donor observation still defaulted to `compat-v2`, and Cellerator's full-export warning remained present. Explicit execution focus is not a request to cancel all other active programs. A targeted question should not depend on successfully exporting the historical ledger. Missing authority is unknown, not “nothing to do.” [S03, S15, O02; PCE1-E21–E22]

### 12. Runtime/admin seams still push mechanics into the model

Plan preview constructs a separate configured-script route; retirement preparation still surfaces intent/request files and fingerprints; scoped non-TTY recovery authorization exists but its internal issuer is not a general authenticated delegation interface. Keep the existing runtime checks and lifecycle kernels, while giving callers a supported semantic route with the same identities. [S16–S18]

## What PCE1 got wrong

PCE1 identified valuable correctness work, but imposed the wrong agent-facing policy on top of it. Root-exclusive execution concentrates mechanical work in the expensive model. Universal preview/apply makes even already-approved preparation a two-step ritual. Fixed delegate-launch and test-pass ceilings are weak proxies for real cost and can block a cheaper route or adequate validation. Requiring a task claim to authorize its own repair is circular.

PCE2 replaces those policies with scoped mandates, ordinary automatic mechanics, conditional decision points and one shared usage ceiling. The exact PCE1 acceptance crosswalk is in [machine/pce1-mapping.json](machine/pce1-mapping.json). It does not discard source preservation, transactional freshness, membership closure, evidence validity or observer isolation.

## Retained work and explicit limits

The earlier review's recovery-resumability gap, retirement subset/whole-run hazard, targeted-amendment problem, Git clean/unknown issue, post-commit projection failure and host ownership edge cases remain relevant at the unchanged commits. Existing clean-workspace reactivation, input fingerprints, scoped recovery authorization, immutable artifact receipts, host stale sweeps and frozen-release checks should be reused. [PCE1-E04–E22]

The review supports a bounded consolidation of existing services and tool contracts, not a replacement orchestration system. It does not establish measured model-token savings, prove all live failures, or diagnose Project Control/GlassHelix's unreadable authorities. Those remain limitations to report rather than reasons to invent a database, launcher or successful test.
