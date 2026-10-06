# PA1 live compatibility inspection

Observed 6 October 2026. Configured read-only researcher inspected the live
authorities through non-agentic overview/frontier/search and installed native
validation. The root retained full fresh native receipts during final acceptance.
No PA1 implementation or workflow mutation occurred.

| Authority | UUID | Revision | Native diff |
| --- | --- | --- | --- |
| project-control | 76dc6bf0-1223-4dda-bf8b-c306fb7721a7 | 923 | 8 added tasks, 7 gates; no modified tasks or warnings |
| skills | 460468fe-ee72-4a64-a565-87cfec640d0c | 1017 | 3 added tasks, 2 gates; no modified tasks or warnings |

The installed command is `/home/tumlinson/.local/bin/project-control`.
The validate branch calls `validate_native_plan`; apply uses a separate branch
in `src/project_control/cli.py:301-318`. Complete command output and observation
preconditions are retained in the two `*-native-validation.json` sidecars.

Frontier packet re-fetch references:

- project-control: `pkt_7d6e3f0e19b24d0fa95c875623311882`, revision 923.
- skills: `pkt_9bb700148c804c09ba7a1ecb878348e1`, revision 1017.

Ready lane heads were `PC-PCE2-RUNTIME` and `SK-PCE2-OPERATE`. Blocked tasks
included `PC-AS1-INQUIRY-CACHE` and `SK-AS1-INQUIRY-CACHE`. Each frontier reported
a stale-heartbeat dispatch. Exact lookups of sampled proposed PA1 IDs returned
not_found, consistent with the full native diff identifying every proposed task
as an addition.

Coverage limits: broad PA1 scopes overlap current runtime, source, docs and tests,
but exact active file ownership was not established because detail packets were
truncated. No claim was acquired, stale dispatch recovered, source implementation
qualified, production question executed, or runtime binding changed. Overview
was partial due absent authored orientation. Refresh current frontier/claim
detail before scheduling; do not treat this snapshot as lasting clearance.

Cross-authority handoffs in `machine/cross-authority.json` are root-enforced
evidence boundaries and are not automatically satisfied native dependencies.
Verify current producer receipts and consumer agreement before each consumer
starts. Preserve the existing AS1 source/docs/qualification edits and other work.
