# AS1 contract foundation

`src/project_control/as1_contracts.py` freezes `pc-adaptive-surface/1` wire and
role contracts against Project Control source
`39ac812281a7c5cd63c651bd142139bdbada2db8`. It imports the existing
`ToolEnvelope` and `ProposalEnvelope`; it does not instantiate another workflow,
registry, model supervisor, GPU lock, or writable Todo projection. The required
contract tests exercise policy rejection, exact routing, wire round trips,
read-only source adoption and producer/consumer verification.

## Producer and consumer interfaces

The strict Pydantic models `SourceLocator`, `InformationPacket`, `DurableJob`,
`SkillSelection`, `ProjectAmendment` and `ImpactEdge` serialize with
`model_dump(mode="json", exclude_none=True)` to the supplied AS1 schemas.
`ExactEntityQuery` is the typed branch of `SearchQuery`; `route_search` calls the
canonical exact lookup directly and returns its result even when unresolved.
Discovery strings retain the supplied discovery backend. Public `find` is
absent. Actual project/backend binding remains the downstream context work.

File source locators carry project, repository, relative path and exact content
SHA256. Semantic task/interface entities use `EntityReference` without invented
file paths or byte hashes. Relative syntax validation does not establish host
access: consumers must validate the trusted registered root and symlink policy
before reading. Line selections additionally reject reversed ranges.

Packet payload SHA256 uses `canonical_digest`: sorted keys, compact separators,
UTF-8, literal Unicode and finite JSON numbers. A payload tamper is rejected on
validation. `PacketStore.put` and `resolve(reference, access_scope=...)` provide
a small consumer port for subsequent persistence. Storage, scope authorization,
freshness checking, expiry tombstones, permanent alias reservations and audit
correlation remain PACKETS work. `None` from resolve must be distinguished by
that implementation from an explicit expired-body result. Hints are never
mutation capabilities.

Jobs preserve question, attempts, evidence-backed findings, unresolved questions
and queued-after-eviction state. Both investigate and skill use this wire model;
the job broker must supply durable acceptance, soft saturation, request replay,
attempt fencing and honest true-admission failures. Persistence is independent
of model/GPU residency. Skill selections are worker recommendations; the broker
must exact-read selected hashes/ranges and emit separate authoritative excerpts
and secondary synthesis. Consultation alone never records applied skill use.
The local read-only worker is the semantic navigator: it follows installed
`SKILL.md` instructions, maps and references as a native agent does. Existing
`skill_context` indexes and graphs are navigation/search accelerators, never a
competing routing policy. Project Control brokers access, persistence, freshness,
direct authoritative reads and provenance; selection models carry the worker
choice without imposing Project Control semantic routing decisions.

`ROLE_POLICIES` is immutable and `require_tool` rejects hidden names and invalid
detail before downstream dispatch. Eight information tools are shared.
Observer alone has read/skill adapters and extended detail. Coder/codex retains
four workflow tools; mutator adds investigation and typed transactional control.
Internal investigator and skill assembler have command/log, compact or standard
information access, no recursive investigation/skill and no project mutation.
No role injects overview. Existing delegate/collect implementation and records
remain intact; AS1 dispatch must reject them until explicit operator enablement.

`BACKEND_REUSE` points to the current canonical WorkflowProtocol, Todo semantic
read/export/declaration authority, Project Control information/retrieval services,
installed Skills tree, local-worker supervisor and CUDA interlock. Packet/job
state is service-private, not project authority. Existing `profiles.py` and
`workflow_tools.py` still advertise the PCE2 surface; wiring AS1 is SURFACE work.
`docs/ARCHITECTURE.md` is historical runtime documentation and not AS1 proof.

## Current work reconciliation

`docs/as1-reconciliation-observation.json` contains actual bounded read-only
`inspect(kind=task)` observations made at 2026-10-04 17:28:18–22 UTC. Re-fetch
using the recorded project and exact `data.target`. Project Control revision
811/source `39ac812281a7c5cd63c651bd142139bdbada2db8` reports RUNTIME blocked by
scope, SURFACE and QUALIFY blocked by dependencies; all have no active claim.
Skills revision 888/source `988cedf0637a83772b62ebbe096abba0e604959f` reports
OPERATE ready and MAINTAIN/COLLABORATE blocked by dependency, also without claims.
These are bounded reconciled graph snapshots, marked truncated; they do not
certify any old successful gate or authorize task retirement.

| Existing work | AS1 source adoption / unfinished scope |
| --- | --- |
| PC-PCE2-RUNTIME | Preserve runtime identity, binding and workflow adapter; CONTRACT/PACKETS/JOBS own new behavior. |
| PC-PCE2-SURFACE | Preserve profile enforcement and canonical information backends; CONTEXT/CONTROL/SURFACE own frontend changes. |
| PC-PCE2-QUALIFY | Preserve historical evidence; QUALIFY/RELEASE require current AS1 journeys. |
| SK-PCE2-OPERATE | Preserve ordinary workflow/kernel guidance; SEMANTICS/ROUTING own new additions. |
| SK-PCE2-MAINTAIN | Preserve maintenance/preemption machinery; SEMANTICS/GPU own new semantics. |
| SK-PCE2-COLLABORATE | Preserve coding delegate/collect internals; RUNTIME/QUALIFY cover temporary surface disablement. |

`reconcile_existing_work` returns inert source-adoption decisions only for the
explicit reviewed mapping. A supplied exact source locator supports adoption of
implementation, not task completion. Missing proof returns
`needs_current_authority`; no task is superseded or retired. Any later authority
change needs a fresh supported owner transaction. `PCU-SK-40`, `C4Q-01`, paused
NF1A and every unreviewed task remain outside this mapping.

The untracked `planning/project-control-pce2`,
`planning/pce2/pce2-intermediate-review` and
`planning/pce2/pce2-second-round-review` archives are preserved. The tracked
PCE2 evidence explicitly distinguishes package validation from unexecuted
product acceptance. Tests verify adoption does not change the relevant actual
source/evidence bytes. No user science repository was mutated.

## Cross-authority delivery and release layout

`ProducerReceipt` version `pc-as1-producer-receipt/1` identifies the producing
project UUID/task, exact gate IDs, source identity, contract hashes, source-bound
artifact locators and observation time/status. `require_current` rejects wrong
producers, tasks, unsuccessful status and contract hashes, then requires the
receiver's canonical authority/evidence verification callback. The callback
must check exact producing gate/source relevance; a receipt itself grants no
mutation authority. Foreign tasks are not copied into local dependency graphs.
There is no cross-authority atomic transaction.

The user explicitly selected standalone repositories with an explicit paired
release manifest. Preserve Skills' existing staged `.gitmodules` and
`project-control` gitlink deletions. Do not restore the gitlink. Historical
package references to a required gitlink are superseded by this decision; root
owns any supported gate/spec amendments.

`PairedReleaseManifest` version `pc-as1-paired-release/1` contains distinct
Project Control and Skills authority UUIDs, repositories, exact commits,
verified runtime identities and producer receipts. It rejects a single authority
masquerading as both components and receipts from the wrong authority. It is a
wire contract, not release execution or runtime qualification. Downstream release
must verify both components and preserve queued forward state and rollback
launchers without restoring an old Todo database over newer work.

## Executed contract evidence

Run with the candidate dependency environment:

```sh
PATH="$PWD/.venv/bin:$PATH" python3 planning/adaptive-surface-v1/scripts/acceptance_gate.py --outcome PC-AS1-CONTRACT
```

The package pytest helper observes `as1_case` markers for CON-01, CON-02 and
CON-03. All 41 behavioral cases passed on this contract source, including shared
RFC3339 timestamp rejection and valid wire preservation across packets, jobs
and receipts. This is the
contract gate command execution; root owns native gate registration/completion,
commit identity and final task acceptance. It does not claim jobs, real inference,
GPU preemption, installed AS1 dispatch or paired-release acceptance.
