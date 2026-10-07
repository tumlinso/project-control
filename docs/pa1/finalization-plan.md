# PA1 practical finalization plan

This plan defines a bounded path to a useful, inspectable Project Control trial. It does not record implementation, installation, qualification, or acceptance results. Those remain pending until supported source, runtime, and live receipts are attached to the exact candidate.

## Operating choices

- The supported user surface is Project Control's CLI and Codex. An explicit ask, run, chat question, or existing MCP/HTTP `investigate` or `skill` request may start the shared inference runtime. Model-free status, control, and read operations must not start inference.
- One systemd user service serializes shared runtime startup. Before starting, it verifies the selected immutable release and its receiver and Todo runtime identities. Identity mismatch or a durable release veto fails closed. Startup waits are bounded and report readiness or the specific failure.
- The configured layer-split model stays resident while resources are available. Idle time alone does not evict it. Pertinent foreground CUDA or LAB GPU work may preempt it; after the foreground owner releases its resources, Project Control may rewarm and continue eligible work.
- LAB scope is previewed and authorized once. Within that scope, the controller may run the bounded sequence autonomously. A new authorization is needed to expand scope or apply anything to canonical source.
- Automatic preparation remains off by default. It can be enabled only for explicitly selected sources and a finite budget with visible status and a clear stop control.
- Preserve all workspaces, Todo and job histories, release candidates, rollback targets, and user changes to the CUDA skill. Never reset these to make a candidate appear clean.

## Staged implementation and acceptance

### 1. Shared demand lifecycle and operator controls

Route every explicit inference entry point through one ensure-ready path backed by `project-control-inference.service`. Concurrent cold requests must converge on one startup. Readiness and status report the selected release identity, startup or failure state, residency, waits, active work, and verified resource release without starting inference merely to answer status.

Provide `assistance start`, `assistance stop`, and status controls. `stop` first cancels or releases Project Control-owned work through its normal owner paths, then stops the shared service. It must not kill foreign processes or claim a physical release without proof. Callers stop waiting at their own deadline while the bounded startup attempt remains serialized and observable.

**Accept when:** parallel cold demands start one correctly bound service; a wrong release or Todo identity is rejected; a model-free status call causes no startup; explicit stop produces verified cleanup; and a fresh CLI plus MCP/HTTP request uses the same release. Keep service/process identity receipts and do not treat `/readyz` being unavailable while stopped as a failure.

### 2. Trusted role budgets and warm residency

Preserve the physical calibration point: layer splitting, 64K service context, batch 4096, microbatch 1024, and parallelism 1. Reuse the existing adaptive request caps and token-rate limits. The broker's trusted role mapping selects `gather-v1` or `synthesis-v1`; LAB planning uses `experiment-plan-v1`. Carry those policy IDs through the Skills analysis provider and omit raw generation fields that conflict with the selected policy. Calls without a trusted policy retain legacy profile behavior.

Report requested, effective, and used context, reasoning-token, and visible-token budgets separately. Do not report hidden reasoning text. Disable idle-time eviction while retaining eviction or release when native resource admission needs the devices, the operator stops the service, or a durable release veto applies.

**Accept when:** focused tests prove each trusted policy reaches the supervisor and adapter intact, conflicting raw overrides are rejected, adaptive limits still apply, and a warm idle server stays resident past 15 minutes. A resource preemption must release only verified owned resources and must not rewarm while foreground work owns or is queued for them.

### 3. Scoped CPU and GPU LAB

Expose a simple `preview`, `authorize`, `run`, `resume`, and `cancel` lifecycle under the assistance LAB command. Preview shows the registered project and repository, allowed source paths, tools, selected resources, and maximum work. A local operator authorization starts one durable scope ID that can resume after an interruption. Default limits are 10 minutes and 6 experiments, counting planning, waiting, building, running, and interpretation. Users can see remaining time, experiment count, current phase, and terminal or uncertain effects.

Each experiment uses a fresh detached source snapshot. The model returns a structured proposal with a bounded command/artifact description, hashes, and source references; fenced model text is never treated as executable code. LAB cannot write canonical source, install dependencies, use the network, or apply a candidate. CPU execution keeps the existing limits: one CPU, 1 GiB memory, 32 processes, no swap, 30 seconds, 64 KiB output, and 64 MiB artifacts.

GPU execution requires explicit UUIDs, the CUDA foreground controller, and positive process/filesystem/device containment proof. Build and run stages are each bounded to 60 seconds. If containment or admission proof is unavailable, GPU execution stops before launch. The CUDA handoff checkpoints LAB state, yields and releases inference, runs under the foreground CUDA owner, verifies cleanup, rewarms inference when resources are returned, then resumes interpretation. Record effect intent before launch; reconcile uncertain outcomes without replaying them automatically.

**Accept when:** preview accurately shows the scope; one authorization supports multiple bounded experiments and resume; cancel and expiry suppress further work and rewarm; CPU containment blocks outside writes and network; a model-generated CPU counterexample produces a structured, source-backed result; and a GPU run completes the full yield/run/release/rewarm/resume cycle under recorded UUID and process evidence. A missing proof is a blocked result, not a pass.

### 4. Finite automatic preparation

Keep automatic preparation disabled unless the user opts in with selected registered sources and a finite window. Status must show selected sources, remaining time and budget, queued/prepared work, and whether a release or quiet control prevents work. Preparation must yield to foreground requests and resource admission. A foreground comparison uses equivalent work with preparation off and on; measure added wait, elapsed overhead, and evidence quality. If the comparison is too small or inconclusive, report that and leave the feature off by default.

**Accept when:** refresh, quiet, expiry, resource-yield, cancellation, and release-veto cases are bounded and visible; the opt-in expires; and foreground limits are met or the feature remains off.

### 5. Practical trial and release

Use existing tuning records for the installed model rather than repeating a broad parameter grid. Reuse the calibrated layer-split setting and make only the targeted comparisons needed to validate current trusted roles and dynamic budgets. Capture cold and warm calls, concurrent startup, both already-resident servers serving calls without reload, useful grounded answers, the CPU model-counterexample path, and the complete GPU handoff. Include restart recovery and prove interrupted effects are not replayed. Harmless semantic imperfections may be recorded; material false claims and unsupported or uncited claims fail the affected scenario.

Use a small predeclared set of practical cases. Allow at most one correction and one retest for each distinct live failure; stop on repeated failure, lost provenance, missing containment or resource proof, or exhausted time. Do not launch a broad qualification grid. Practical trial readiness means the user can safely try the supported features and inspect the results. It does not mean the separate mandatory 40-case product acceptance matrix is complete.

Build an immutable release from a frozen source identity, refresh all runtime manifests and bindings, retain the current release for rollback, and verify the supported entry points against the selected release after cutover. Commit and push the source and documentation changes, then confirm local and remote tips. End with all owned inference processes and leases stopped or physically released, no background inference running, automatic preparation off, and a restart handoff that identifies remaining unaccepted scenarios and exact evidence locations.

**Accept when:** every demonstrated feature is backed by a current candidate-bound receipt with its source, runtime, model or device identity and limits; rollback remains available; fresh CLI and MCP/HTTP entry points agree on release identity; remote commits are verified; and the final resource census proves no background inference or owned GPU lease remains. Report 40-case acceptance separately and leave it partial wherever its exact case receipts are absent.
