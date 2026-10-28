# Planning Workflow v2

Use this guide for a new substantial project or a major graph revision.

1. Inspect repository reality, `AGENTS.md`, current Git/dirty state, relevant skills, interfaces, tests, and existing v2 status before asking questions.
2. Identify the actual objective, constraints, success criteria, destructive boundaries, and any choices that genuinely require a human.
3. Decompose stable task nodes and hierarchy. Separate task completion from checkpoints and experiment dispositions.
4. Add typed dependencies. Use checkpoints for early downstream release, barriers for fan-in, decisions for finite conditional branches, and interfaces for contract compatibility.
5. Declare exclusive/read roots and critical-section locks. Default unclear work to serial; do not call it parallel-safe merely because tasks sound independent.
6. Declare scarce resources at the phase that needs them. Benchmark resources normally belong to gates, not the entire coding claim.
7. Convert done criteria into gates and relevant input fingerprints. Use `evaluated_not_promoted` when evaluation—not promotion—is the required outcome.
8. Capture global rules once as invariants and reference them from affected tasks.
9. Generate a JSON plan, validate it, inspect `plan diff`, and apply it transactionally.
10. Run `todo ready` and `todo explain` on representative nodes before inviting parallel pickup.

Planning is complete when another chat can run `todo continue --json`, receive one safe task capsule, and proceed without architecture restatement.
