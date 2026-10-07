# Emergency rescue finalization — 2026-10-07

This records core rescue evidence, not PA1 product acceptance. Use
[development](development.md), [deployment](deployment.md), and
[AGENTS.md](../AGENTS.md) for the current operating instructions.

## Source and deployment

- Source checkout: `/home/tumlinson/project-control`; both Python packages load
  from its `src` tree. The changes in this record accompany the rescue commit.
- `scripts/pc-dev setup` completed using the frozen development dependencies,
  repository-local cache, and preserved `.venv -> .dev-venv` symlink. The initial
  sandboxed attempt could not reach PyPI; the authorized network retry succeeded.
- Source commands clear inherited release/interpreter pins. Run and Python
  commands default the analysis state directory to the existing services'
  `$HOME/.cache/project-control/as1-observer-analysis`; explicit overrides remain
  supported. Test modes clear that state setting.
- Both source user services restarted together. HTTP health/readiness passed;
  actual HTTP MCP tool listing and workflow reads succeeded at revision 1149.
  Fresh mutator stdio advertised the bundled rescue path and read the same ledger.
- One initial HTTP frontier read after restart returned a compatibility fallback
  with no semantic revision. Subsequent overview and frontier reads returned
  revision 1149. The transient cause was not established; verify a real ledger
  revision after deployment rather than accepting an empty fallback as evidence.
- Cold supervisor metadata verified the current source package binding, with
  zero resident model slots, active leases, or admissions. The existing release
  veto remained active. No model was requested.
- Prior service configuration remains in the private ignored backup directory
  `.project-control-test/source-service-backup-20261007T132049Z`; the previous
  installed R10 candidate remains available for rollback. No state was relocated.

## Focused verification

- Final `scripts/pc-dev test -q`: **23 passed**.
- Runtime and frozen-release identity selection: **13 passed**.
- Direct source-supervisor startup-attestation test: **1 passed** after fixing
  its package-qualified helper import.
- Temporary stopped-task rescue authorization and live-owner refusal: **1 passed**.
- Final collection-only check: **2,127 collected**, no collection errors. This
  does not report assertion results for the expanded suite.
- Static bounded review found no blocking defect. `git diff --check` passed.

## Todo recovery

Project UUID `76dc6bf0-1223-4dda-bf8b-c306fb7721a7` and all history were preserved.
The mutator diagnosed stopped ownership for `PC-PA1-LAB`, but correctly refused
a clean delegated grant because its claimed scope contained retained changes.
The controller used the supported interactive owner recovery, with the user's
rescue authorization and exact task confirmation, to preserve that work.

Recovery audit `2dd715ff-669f-44f2-b439-89df2e046692` applied three actions at
revision 1149: retire the stopped dispatch, release expired claim
`b0f4603d-b9ed-4755-9c36-27940c148143`, and retire its capability. The receipt
reported `files_mutated: false`. LAB remains `attention_required`, not completed
or newly claimed. Its stale session no longer appears in the frontier.

Two other stale dispatch records remain visible:
`48983c8c-dd82-45fe-b125-ae6f78d4ec3e` and
`4ea78cd3-8bdd-478b-8c17-97d40f3b8261`. They were not deleted or broadly recovered.
`PC-PA1-QUALIFY` remains blocked by LAB; retained-work review and live
qualification are subsequent work.

## Deferred verification

Agentic tools, grounded model answers, autonomous LAB, CUDA lifecycle, active
cancellation/crash qualification, expanded-suite execution, and frozen-release
qualification were not exercised. Previously reported empty-proposal validation
and stalled readonly-audit test failures were not retested in this final pass.
This rescue makes development and core operations usable; it does not close
the assistance epic or declare comprehensive acceptance.
