# PCE2 remaining scope: evidence-led follow-up

This is a prioritization record, not a claim that all 24 PCE2 acceptance
groups are complete.  PCE2 remains a flexible guide: Project Control should
remove repeated workflow reconstruction while leaving the implementer free to
choose efficient, complete tactics within the supplied direction.

**Observed source identities.** Project Control `5b1c0640bf8b7e1680155a952a2a825798e674f9`; Skills
`f4d544476d7cc047a9a8d15e31a6d657f093ef53`.  The Project Control observer
reported workflow revision 796 on 2026-09-20.  Its worktree was dirty, so this
record cites source locations rather than claiming a clean release snapshot.

## Covered or reusable now

| Area | Existing behavior and evidence | Boundary |
|---|---|---|
| Ordinary entry | `next_task` returns the work packet without a compulsory inspection (`todo_orchestrator/workflow/protocol.py`, `WorkflowProtocol.next_task`; `service.py`, `WorkflowKernel.next_task`). | Explicit run focus is absent. |
| Bounded context | First-class composition already computes known-manifest deltas (`context_fragments.py`, `ContextFragmentStore.compose_first_class`), and oversized entry context preserves the claim with an `inspect_task` receipt (`service.py`, `WorkflowKernel.next_task`). Existing fragment tests cover changed and unchanged manifests. | The public entry call does not accept a known manifest. |
| Action guidance | The public action projection intersects role policy and the issued capability (`protocol.py`, `action_policy`). | It does not cure an incorrect focus selection. |
| Conservative evidence reuse | Fingerprinted `file_exists` and `pattern` gates can be reused; supporting focused results are recorded in `BOUNDED_CANDIDATE.md` and `RUNTIME_PROGRESS.md`. | Command, resource, and managed-workspace gates deliberately rerun. |
| Bounded recovery | Separate issuer, trusted operator, and ordinary implementer process transfer has been activated and recorded in `RUNTIME_PROGRESS.md`. | No Project-Control-owned model launcher is required or proposed. |

## Selected bounded follow-up

The main thread selected three changes for this pass: remove unnecessary
workflow rituals from guidance, add exact run focus, and correct collection's
mutation annotation. The guidance also records the owner's division of work:
the main thread owns reasoning, synthesis and meaningful decisions; subagents
gather evidence, execute tightly scoped assignments, and report findings and
blockers for direction. They wait before consequential changes.

The source observations below explain the selection; they are not a request
to implement every remaining acceptance group. Release evidence records which
selected changes have shipped.

All three selected changes are now implemented and activated: PC `11d5ad5`
and Skills `27c59e9`. `BOUNDED_CANDIDATE.md` records the paired release and
focused checks. The findings below describe the assessment's starting state;
the remaining implementation candidate is deliberate delegation context,
when a concrete task needs it. This does not close the original full package.

## Ranked findings

1. **Exact run focus and no wrong claim.** Add optional `run_id` alongside
   `task_id` through the Project Control wrapper and Skills MCP/protocol/kernel
   call.  When an unqualified task id occurs in more than one active run,
   return a compact run/lane choice and do not claim.  The current kernel query
   selects `ORDER BY l.run_id,l.id LIMIT 1` in `service.py`, while public
   signatures accept only `task_id` in `protocol.py` and `mcp/server.py`.
   Add a disposable two-active-run duplicate-task test.  This directly closes
   the demonstrated J01 gap and prevents expensive recovery from a wrong lane.

2. **Declare collection as mutating.** In Project Control,
   `workflow_tools.py` annotates `collect_delegation` as read-only, but Skills
   `WorkflowKernel.collect_delegation` updates child execution/attempt state
   and records `workflow.child.collected` through `service.mutate`.  Change
   only the MCP annotation and add/retain a focused contract test.  This is a
   small truthfulness correction, not a redesign of delegation.

3. **Defer until a concrete delegation need.** Child delegation currently
   derives a strict child scope by selecting the first file under a parent
   scope and sends generic constraints with empty source/interface references
   (`service.py`, `_child_scope` and `delegate_task`).  That falls short of J05,
   but fixing it needs a declared source-target/task-context contract and
   enforced writer handoff.  Build that bounded contract only when a real
   planned task needs delegation; do not add an index or generic packet system
   preemptively.

## Deliberate deferrals

Do not add an automatic model launcher, broad amendment/retirement framework,
general scope/index layer, or command/resource/workspace evidence cache without
a reproduced friction case.  The existing PCE2 design explicitly permits a
ready-to-launch host assignment, and the current candidate record states which
gate types intentionally rerun.  Context-delta transport is a plausible later
small improvement because the composition mechanism already exists, but it is
not ahead of exact focus or correct mutation metadata.
