# Examples

## Good Triggers

- “Use `$todo-orchestrator` and continue.”
- “Decompose this migration into safe parallel work and execute it.”
- “Create a producer/consumer plan with an interface-freeze checkpoint.”
- “Coordinate these benchmarks across the available accelerators.”
- “Recover the orphaned task without discarding its dirty files.”
- “Migrate this existing `todos.md` project to transactional orchestration.”
- “Explicitly clean completed legacy ledgers.”

## Not Good Triggers

- “Rename this variable.”
- “Fix this one isolated test.”
- “Explain this function.”
- “Brainstorm only; do not create execution state.”

## Generic Parallel Shape

A useful project-neutral topology is:

```text
contract producer --checkpoint/interface freeze--> parallel consumers
baseline/evaluation tasks -----------------------> fan-in barrier
parallel experiments --implemented or evaluated_not_promoted-->
final barrier --> integration-exclusive task
```

Declare paths and named critical sections so shared build manifests or registries do not become accidental collision points. Put scarce hardware on the gate that uses it rather than leasing it for the entire coding task.
