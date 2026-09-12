# 2. Current source evidence and authority boundaries

See `evidence/source_ledger.json` for exact repository, commit, path and inspected line ranges. Entries distinguish source-verified behavior from observations, inferred failure paths and proposed changes. Source tool `file_identity` values are metadata-derived identities in this version, not necessarily byte SHA-256 hashes. The new bootstrap records actual file-byte hashes on host.

## Source-verified baseline

Project Control currently binds and validates an external Todo installation before importing its service; `mutation.py` exposes direct validation/diff and inert-proposal apply functions [S01]. Public MCP preview uses a separate adapter path and different JSON digest representation [S02]. This justifies consolidating those front doors while preserving transaction discipline.

Todo validates supported v2/v3 shapes but does not comprehensively forbid unknown task members; apply explicitly selects persisted fields [S03]. Consequently a useful-looking unknown field can validate yet not become state. `execution` already reports readiness such as ready, claimed and blocked-resource [S04]. The new name is `work_profile`.

Native graph validation combines parentage and task prerequisite edges [S05]. An epic with child prerequisites forms a cycle under that contract. WF2 therefore uses a root epic with no explicit child prerequisites, the existing aggregate child-readiness rule, and a claimable ordinary coordinator ahead of it. The failed construction probe was corrected; it is not concealed as a pass.

The current workflow role table does not give a validator ordinary edit-scope authority [S06]. The independent verification streams here author tests and therefore use the permitted `specialist` role with narrow test/doc scopes. Independence means different implementer/reviewer responsibility and evidence, not a misleading role label.

Todo's run completion exists already, but its lane-completion and plan-application paths do not invoke one common terminal reconciliation function [S07]. The observed old active run with closed lanes is real; its exact historical cause must be reproduced, not asserted from one snippet.

Ctxpp has independent packet/query machinery [S08]. PC's read adapter still scans JSONL and only metadata-checks freshness [S09]. PC source reads repeat global worktree identities and have list/range truncation concerns [S10]. These observations motivate targeted changes, not deletion of evidence safeguards.

## Baseline is not an eternal precondition

This delivery records PC main `70b863ab7972d6d722520d8ac2be2bb1d7d86d1c`, revision 599; Skills main `3652874793401944e3cde30a3134aeb5615316ba`, revision 796; and codex façade main `cfe4c610372faedfb44d0225c31d42b3132edc42`. Fresh on-host source and native authority checks are mandatory before import. The two repositories do not form one global database transaction.

The installed runtime may be frozen at a release snapshot different from a development checkout. Use the actual interpreter, release launcher environment, manifest and package identities. Do not put a development checkout on PYTHONPATH merely to make a failed runtime test disappear.
