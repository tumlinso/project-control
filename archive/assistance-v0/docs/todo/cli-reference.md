# v2 CLI Reference

Invoke `python <skill-dir>/scripts/todo.py <command> --repo-root <repo> --json`.

Under `--json`, stdout contains exactly one JSON response envelope:

```json
{"schema_version": 2, "ok": true, "code": "success", "data": {}}
```

Failures use the same envelope with `ok: false`, a stable `code`, and `error.message`/`error.details`.

## Stable Exit Codes

| Code | Meaning |
|---:|---|
| 0 | success |
| 10 | no actionable work |
| 11 | transaction, lock, claim, or resource contention |
| 12 | blocked by a declared safety condition |
| 13 | invalid, stale, released, or mismatched token |
| 14 | gate/checkpoint validation failure |
| 15 | plan/schema/argument validation failure |
| 16 | consistency, projection, database, or unexpected internal failure |

## Commands

- Project: `bootstrap`, `init`, `status`, `doctor`, `export`, `cleanup`
- Plans: `plan validate|diff|apply|scaffold`
- Pickup: `continue`, `claim`, `ready`, `explain`, `context`, `changes`
- Claim lifecycle: `pulse`, `release`, `handoff`, `block`, `complete`
- Graph: `decision set|status`, `checkpoint reach|revoke|status`, `barrier status|explain`
- Contracts: `interface freeze|revise|status`
- Critical sections: `lock acquire|release|status`, `exec --lock ... -- <command>`
- Resources: `resource discover|list|acquire|release|explain`
- Evidence: `gate list|run|explain`; `gate run --required` runs all required gates for the current claim
- Safety: `guard`, `audit`, `reconcile`, `recover inspect|release|adopt`
- Exceptional live recovery: `recover live-inspect`, interactive/manual
  `recover live-approve`, then one-use `recover live-override`. The live path is
  restricted to unchanged `coding-workflow` claims with no attached work; it
  is not ordinary claim adoption.
- Terminal finalization: `recover terminal-checkpoints TASK [--checkpoint ID]`
  publishes pending task-owned checkpoints from recorded successful completion
  provenance. It requires no claim token, never reopens the task, and is
  idempotent. It rejects failed/superseded owners, revoked checkpoints,
  ownership mismatches, and prerequisites that were not valid at completion.
- Owner emergency release: `recover force-release-inspect`, interactive/manual
  `recover force-release-approve`, then one-use `recover force-release`. This
  path accepts an arbitrary still-live owner-controlled claim when no child,
  gate, background/CUDA campaign, or demonstrably running attached process can
  continue mutating it. Changed scope requires explicit owner acknowledgement.
- Compatibility: `migrate markdown --dry-run|--apply`

`resource discover` is an optional NVIDIA inventory provider. The scheduling tables and commands remain generic.

## Claim Recovery Decision

1. If the current claim token is available, use ordinary `release`, `handoff`,
   `complete`, or `block`.
2. If the claim expired or became orphaned, use `recover inspect` followed by
   `recover release` or `recover adopt`.
3. If the claim is still live but its token was lost:
   - use `recover live-inspect|live-approve|live-override` only for an unchanged,
     verifiably `coding-workflow`-owned claim that needs a replacement facade
     claim;
   - use the owner force-release flow for any owner-controlled live claim that
     has no unsafe attached execution. If scope changed, the owner must inspect
     and explicitly acknowledge that preserved state.

The force-release contract is:

```bash
python <skill-dir>/scripts/todo.py recover force-release-inspect TASK \
  [--acknowledge-dirty] --repo-root <repo> --json
python <skill-dir>/scripts/todo.py recover force-release-approve TASK \
  --reason "<owner reason>" --ttl-seconds 300 [--acknowledge-dirty] \
  --repo-root <repo> --json
TODO_FORCE_RELEASE_APPROVAL='<token printed only after interactive confirmation>' \
python <skill-dir>/scripts/todo.py recover force-release TASK \
  --repo-root <repo> --json
```

Approval creation requires TTY stdin and stdout and exact task-ID entry; there
is no `--yes` bypass. The approval defaults to 300 seconds (bounded to 30-900),
is one-use, and is bound to the canonical repository, project UUID, task, UID,
project revision, and current claim fingerprint. The consuming command reads
the secret only from `TODO_FORCE_RELEASE_APPROVAL`, never argv.

`owned_scope_changed` is an acknowledgement gate, not a permanent lockout.
First inspect without the flag. After verifying that current files must remain
exactly as they are, rerun inspect and interactive approval with
`--acknowledge-dirty`. Approval records the baseline/current scope fingerprints
and dirty paths. Consumption refuses as stale if the current material scope
fingerprint changes again. Generated todo projections are excluded from that
staleness fingerprint because approval refreshes them itself. Release never
restores, deletes, stages, or commits repository files.
