# Project Plan v2

The JSON plan is declarative input to one transactional upsert. Codex normally generates it; users should not have to hand-author it. Validate with `schemas/project-plan-v2.schema.json` and the CLI validator.

## Entities

- Tasks: stable IDs, hierarchy, kind, lifecycle, priority, policy, result/disposition, next action, tags, and notes.
- Dependencies: task completion (optionally allowed dispositions), checkpoint reached, interface state/version, barrier open, or safe decision equality/membership.
- Checkpoints: independently reached/revoked milestones, required gates, and published interface versions.
- Barriers: all-of or quorum fan-in across tasks, checkpoints, interfaces, validation tasks, and gates.
- Interfaces: owner, draft/frozen/revised/deprecated state, version, contract paths, and content hash.
- Ownership: exclusive roots, read roots, forbidden roots, named locks, artifacts, and parallel policy.
- Resources: generic classes/instances/capacity and phase-specific selectors such as `accelerator:any`.
- Gates: command, benchmark/evaluation, JSON predicate, file/pattern, task/checkpoint/interface, or manual evidence.
- Invariants: concise project rules scoped to referenced tasks and included selectively in capsules.
- Decisions: finite allowed values used only with equality or membership; there is no executable expression language.

## Generic Producer/Consumer Example

```json
{
  "schema_version": 2,
  "project": {"name": "Compiler Pipeline"},
  "interfaces": [
    {"id": "ir-contract", "owner_task_id": "DEFINE-IR", "contract_paths": ["include/ir.json"]}
  ],
  "barriers": [
    {"id": "LOWERING-FANIN", "mode": "all", "requirements": [
      {"type": "task", "id": "LOWER-A", "state": "done"},
      {"type": "task", "id": "LOWER-B", "state": "done"}
    ]}
  ],
  "tasks": [
    {
      "id": "DEFINE-IR", "kind": "workstream", "title": "Define IR", "parallel_policy": "parallel_safe",
      "scope": {"exclusive_paths": ["include"]},
      "checkpoints": [{"id": "IR-FROZEN", "publishes_interfaces": [{"id": "ir-contract", "version": "1"}]}]
    },
    {
      "id": "LOWER-A", "kind": "workstream", "title": "Lower target A", "parallel_policy": "parallel_safe",
      "depends_on": [{"type": "checkpoint", "checkpoint_id": "IR-FROZEN"}],
      "scope": {"exclusive_paths": ["src/a"], "read_paths": ["include/ir.json"]},
      "consumes_interfaces": [{"id": "ir-contract", "required_state": "frozen", "required_version": "1"}]
    }
  ]
}
```

Use `plan scaffold fanout`, `producer-consumers`, `benchmark`, or `integration-barrier` as a draft starting point.
