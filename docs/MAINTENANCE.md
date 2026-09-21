# Maintenance continuation

Use `project-control admin prepare-maintenance --repo REPO --task TASK --recipient PRINCIPAL` to issue one bounded operator assignment.  Add `--run RUN` when the task has more than one active membership; when recovery identifies one stopped target execution, that exact run is selected automatically.

The operator calls `maintain_execution` with the opaque authorization.  Its receipt reports a current, read-only continuation assessment.  A `recommended_next_call` is present only when the named run, lane, task, workspace, dependencies, and resources are currently eligible; invoke its exact arguments without substituting another run or workspace.  A replay reassesses current readiness and does not promise the historical result remains claimable.

Clean quarantined managed workspaces may be reactivated only through the signed exact continuation, after identity, base, HEAD, cleanliness, ownership, and integration checks.  Dirty retained work is preserved and requires an explicit adoption decision; maintenance does not reset, clean, or silently accept it.

For an exact run replacement, issue a separate opaque assignment:

```text
project-control admin prepare-supersession --repo REPO --intent intent.json --recipient PRINCIPAL
```

The intent names `source_run_id`, `successor_run_id`, and `reason`. Optional
`preserved_work_handoffs` name a quarantined source workspace/lane, a successor
lane/task, and `adopt_dirty: true` when retained material work is explicitly
accepted. The host derives complete unfinished source membership and Git
identity; callers never assemble row digests or a raw mutation request. The
operator supplies only the opaque grant to `maintain_execution`. Its successful
or replayed receipt reassesses readiness and returns exact `next_task` arguments
(`repo_root`, `run_id`, and `task_id`) only when ready; use those unchanged. Changed source bytes, a false identity, a
missing dirty adoption, foreign live membership, or active source artifacts
refuse without cleanup.

An adopted `isolated_merge` producer must have exactly one unfinished
integrator or validator task declared by the successor run. That task becomes
the producer's integration destination; absent or ambiguous destinations refuse
the replacement before retirement.

Supersession binds the reviewed authority revision and fingerprint. If the
authority changes before application, it refuses and the owner prepares a new
assignment; it never refreshes a signed replacement request automatically.
