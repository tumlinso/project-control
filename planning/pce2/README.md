# PCE2 — Usable Semantic Operations

Project Control revision and Todo bootstrap package · 20 September 2026

**Goal:** minimize the reasoning an agent spends operating its environment, not minimize its engineering judgment. The planner supplies clear mechanical direction; capable agents choose implementation tactics; infrastructure handles repeatable mechanics and explains genuine decisions.

PCE2 **replaces the PCE1 design/bootstrap**, rather than adding a second full program. It retains the safety/correctness work that is still needed, removes root-exclusive execution and universal preview/apply ceremony, and adds independently discovered ordinary-tool and delegation fixes. It does not mutate old planning records merely by existing.

Start with [BOOTSTRAP.md](BOOTSTRAP.md) and [DESIGN.md](DESIGN.md). Six durable outcomes and two independent native plans are in [machine/outcomes.json](machine/outcomes.json), [Project Control](machine/project-control.todo-plan.json) and [Skills](machine/skills.todo-plan.json). [REVIEW.md](REVIEW.md) records findings and limits; [ACCEPTANCE.md](ACCEPTANCE.md) defines public-boundary tests.

**Implementation allowance:** an uncalibrated target of **1.6–2.4 million aggregate model tokens**, with the existing **3-million shared stop-work ceiling**, including every agent/retry/review and any PCE1 work already spent on this revision. No fixed delegate-launch, parallel-head or test-pass quotas. Read [machine/usage.json](machine/usage.json). The package is not a billing limiter.

The familiar six workflow tool names remain the ordinary entry points. A specialized **`maintain_execution`** entry point is proposed for authorized local agents; it is not observer-accessible and not reserved for a model called “root.” Examples in DESIGN.md are proposed interfaces, not commands available today.

This package does not implement the product or establish that its acceptance scenarios pass. [evidence/validation.json](evidence/validation.json) states which package/native-plan checks actually ran. Do not confuse shared-schema validation with a successful diff against an unreadable target authority.

No Cellerator/GlassHelix source, ledger, workload or NF1A continuation is authorized by this bootstrap. End with a qualified candidate and a still-paused readiness handoff. Activate deployments only through the existing authorized host-owner path.
