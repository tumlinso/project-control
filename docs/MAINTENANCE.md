# Maintenance continuation

Use `project-control admin prepare-maintenance --repo REPO --task TASK --recipient PRINCIPAL` to issue one bounded operator assignment.  Add `--run RUN` when the task has more than one active membership; when recovery identifies one stopped target execution, that exact run is selected automatically.

The operator calls `maintain_execution` with the opaque authorization.  Its receipt reports a current, read-only continuation assessment.  A `recommended_next_call` is present only when the named run, lane, task, workspace, dependencies, and resources are currently eligible; invoke its exact arguments without substituting another run or workspace.  A replay reassesses current readiness and does not promise the historical result remains claimable.

Clean quarantined managed workspaces may be reactivated only through the signed exact continuation, after identity, base, HEAD, cleanliness, ownership, and integration checks.  Dirty retained work is preserved and requires an explicit adoption decision; maintenance does not reset, clean, or silently accept it.
