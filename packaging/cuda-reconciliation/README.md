# Atlas migration dry-run

Task: PC-PCE2-RUNTIME, root-owned PC-PCE2-RUN-1 / PC-PCE2-L-EXEC claim.
This package is a read-only migration proposal. No atlas, skill or archive files
have been changed. Root owns approval, staging, integration and lifecycle.

Run:

```sh
python packaging/cuda-reconciliation/plan_atlas_migration.py --source /home/tumlinson/.agents/skills/cuda/references/architectures/volta/v100_atlas --output packaging/cuda-reconciliation/atlas-migration-dry-run.json
```

The deterministic plan pins all 217 live source files and the original ZIP,
renames 190 canonical M/C/R/E/S documents to `ID-title-slug.md`, and preserves
237 current resource IDs and all 929 graph edges. Existing path-derived
`resource-*` and `need-*` IDs must never be regenerated. Canonical IDs, requires,
sources and NEED relationships remain unchanged.

Only `sources/SOURCES.md` and `sources/sources.jsonl` currently require technical
path substitutions (42 each). The planner verifies reverse-normalized bytes
equal the original bytes. Manifest/corpus paths and current bytes/hashes must
be updated structurally after root approval. SHA256SUMS preserves original present-file membership and exclusions with
renamed paths/current hashes; five absent archive-only prototypes are explicitly
recorded in the audit and omitted from the live checksum inventory.
Historical validation claims remain historical; migration verification must
not imply fresh CPU semantics, CUDA compilation or GPU measurement.

FULL preservation is straightforward: all 151 links point to existing internal
anchors; no canonical file paths occur in FULL. Its complete bytes and hash
remain unchanged. No duplicate legacy files or archive resolver are needed.
The migration plan provides the old-path-to-new-path archival lookup map.

Optional schema-v1 top-level resource fields: role, index_excluded and lineage. Proposed canonical
roles: M/C canonical, R deep_reference, E experiment, S evidence. NEED entries
and NEED_INDEX use semantic_index; entrypoints/README/field guide use navigation;
FULL uses aggregate_view and index_excluded=true. Manifest/corpus/checksum and
generated migration receipts are representation metadata and excluded from
ordinary indexing. Other technical prose and evidence remain indexable.
Any metadata fields should be attached without changing resource identity.

Before publication, verify exact source membership and hash pins; collision-free
renames; all unchanged graph IDs/edges; normalized content equality; byte-identical
FULL and ZIP; local references; metadata/checksum integrity. Stage under this
package only, then let root apply to the skill after baseline/proposal approval.

Verification performed: deterministic repeat planning, 190 unique destination
paths, 237 unique current resource IDs, 929 graph edges, 151 resolving FULL
anchors, source ZIP identity, and normalized path-only deltas. This is dry-run
verification, not an applied-corpus acceptance result.

The approved stage is now `staged-atlas/` (217 files), with
`atlas-migration-map.json` and `atlas-migration-audit.json`. It preserves all
22 NEED row spans and verifies 193 local links. FULL, ZIP, historical validation
records, and all technical content except explicit path substitutions are intact.

Root publication (coordinate other writers first):

```sh
python packaging/cuda-reconciliation/apply_atlas_migration.py
python packaging/cuda-reconciliation/apply_atlas_migration.py --apply
```

The default command verifies only. Publication validates exact live/staged
file membership and hashes, creates a complete sibling candidate, preserves the
original atlas in `atlas-backups/`, and swaps the entire directory. A failed
swap restores the original directory. No atlas code is executed. Temporary
fixture tests passed publication, exact backup preservation, live-drift rejection
and stage-drift rejection. The actual apply operation remains root-owned.

Root metadata correction: CPU_TEST_RESULTS is substantive validation evidence,
so its resource remains role=evidence and index_excluded=false. VALIDATION
remains excluded as an artifact-integrity inventory. The metadata delta changes
only the corpus exclusion flag; manifest/checksum bytes are unchanged.
`atlas-cpu-evidence-metadata-delta.json` records exact before/after pins.
The stage audit was reverified against the preserved original atlas backup.
For a future fresh stage after publication, use the staging script's
`--original-source PATH` option with that exact original backup.
