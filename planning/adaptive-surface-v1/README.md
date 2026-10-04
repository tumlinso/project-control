# Project Control — Adaptive Surface v1 (AS1)

**Implementation bootstrap, not an implemented release.** Prepared 4 October 2026 against live Project Control and Skills observations. This package is a complete implementation mandate, native Todo plans, acceptance contract, and a safe bootstrap/validation harness. No live repository, Todo authority, service, or GPU reservation was changed while preparing it.

## The point

Project Control should maximize **useful engineering progress per unit of model attention, elapsed time, compute, and coordination effort**. It supplies authoritative context, continuity, and safe execution mechanics so agents can spend their judgment on the engineering. It must not become an administrative exercise, a mandatory retrieval maze, or a second source of truth.

The target is not merely fewer tool names. It is a consolidated, adaptive helper surface: directly inspect when the target is known, search when discovery is needed, trace deterministically when relationships matter, delegate read-only exploration when a local computer is useful, and mutate plans through the existing transactional authority.

## Start

Give the implementing root agent **START_HERE.md**. Its first executable entry point is:

```bash
python3 scripts/bootstrap.py check
```

This checks the package only. Staging, native runtime validation, and plan application are explicit separate operations described in `START_HERE.md` and `spec/07-bootstrap-and-release.md`.

## Contents

| Location | Purpose |
|---|---|
| `EXECUTION_HANDOFF.md` | Fresh-context bounded Codex workers, root lifecycle ownership and same-base integration order |
| `START_HERE.md` | Copy-ready implementing-agent mandate and bootstrap sequence |
| `spec/00-purpose-and-decisions.md` | Requirements, consistency corrections, role economics |
| `spec/01-tools-and-profiles.md` | Complete target surface and contracts |
| `spec/02-packets-and-scouts.md` | Packet identities, hints, durable jobs, queue, log, sandbox |
| `spec/03-impact-history-evidence.md` | Dependency providers, trace semantics, freshness, temporal and validation evidence |
| `spec/04-skills-and-resources.md` | Skill adapter, direct authoritative extracts, native skill usage, GPU eviction |
| `spec/05-mutation-and-registration.md` | Plan, semantic registration and maintenance contracts |
| `spec/06-acceptance-and-economics.md` | Behavioral acceptance and per-role efficiency evaluation |
| `spec/07-bootstrap-and-release.md` | Existing-work reconciliation, installation, rollout and rollback |
| `planning/*.todo-plan.json` | Independent native schema-v3 plans for the two authorities |
| `planning/outcomes.json` and `WORKPACKAGES.md` | Outcome-sized work, acceptance ownership and implementation anchors |
| `planning/cross-authority.json` | Explicit producer/consumer handoffs; never fake foreign task dependencies |
| `contracts/` | Machine-readable surface, requirement ledger, cases, examples and schemas |
| `prompts/` | Compact role instructions and the skill-assembler mode |
| `scripts/` | Package checker, safe staging/native validation/import, acceptance runner |
| `tests/` | Tests of the package and bootstrap, not product acceptance claims |
| `sources/` | Live evidence with paths, identities, limitations, and primary technical references |
| `validation/` | What was actually checked during preparation; no fabricated product results |

All new API examples are **target contracts to implement**, not commands already present on the installed server. Existing bootstrap calls are marked as current interfaces. The separate Project Control and Skills Todo databases remain separate.
