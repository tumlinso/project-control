# Separation delivery: functional integration report

This candidate makes Project Control a deterministic source and evidence instrument, extracts Project-Lab, and introduces independently installable Project Source and Project Evidence packages. It is a review candidate, not a production cutover.

## Source boundaries

| Repository | Candidate | Starting revision |
|---|---|---|
| project-control | `codex/project-control-overhaul`, package 0.5.0 | `c14ad76cef2de02c6a70c99b072eb81d9c5304a9` |
| project-foundations | initial `main`, packages 0.1.0 | new repository |
| project-lab | initial `main`, package 0.1.0 | new repository; extraction provenance records `c14ad76` |
| device-scheduler | `codex/project-control-overhaul` | `18a9a1d78dfc054a8abebad9e21f7f8fa0705496` |
| skills | `codex/project-control-overhaul`, cpp-context-compiler subtree | `0a6fab0ef005264a15b7c4d7b3cbb842bfdddfdf` |

Peer candidate commits are foundations `ca0f6787e6fcbe2cb66c593b0ed56a82734278ad`, Project-Lab `2fbaa0e66309c79c2e6ba68a14f7b8f30a3c87a3`, scheduler `73e4d8188a826287b0c67757f38f9229157c5de3` and skills `fcb0302f6dde83e66abcbc158da9d2e517cc7136`. The Project Control review branch identifies the enclosing delivery commit.

All candidates were developed in separate worktrees or new repositories under `.overhaul-workspaces`. Original uncommitted scheduler and skills changes were recorded separately and excluded. A final byte-hash, status and HEAD comparison confirmed their preservation, along with the original Project Control checkout and sealed release pointer. See [the preservation check](validation/preservation-verification.json). No active service, launcher, installed skill, credential or authoritative workflow database was changed. The only server started for acceptance was a private candidate on port 18768, which was stopped afterward.

## Delivered behavior

Project Control exposes the agreed 10 Observer, 12 Codex and 15 mutator tools, with profile-owned response policies and no public detail selector. Typed source operations work without Todo. Existing deterministic workflow routes retain lazy native-provider integration. Declared guidance can expand across permitted registered projects. Neutral evidence stores are opened read-only and preserve owning-application attribution alongside Todo-owned evidence.

Project Source provides bounded file/revision retrieval, incremental lexical indexing, Python structural relationships and a genuine cpp-context-compiler export adapter. Project Evidence supplies immutable observations, artifact identities, provenance, attributed assessments and compatibility readers. Neither package imports an application or model service.

Project-Lab provides the seven agreed API/CLI/MCP operation families. Its scheduler submits a CPU/memory controller separately from experiment allocations. Durable submission identities, captured inputs, cleanup receipts, result metrics and recovery remain inspectable. Codex proposals are validated and staged in candidate areas. The default planner configuration is `gpt-6-luna` with low effort; qualification used injected fixtures only.

Device Scheduler now advertises concrete operation and nested DTO schemas, scoped access diagnostics, a one-off runner and a supervised-reservation handle. Existing operation names and dispatch remain. cpp-context-compiler supports external state and explicitly read-only exports, with existing authoring, rename and sharding behavior retained and tested on disposable fixtures.

## Verification

The selections below overlap; their counts must not be added into a total qualification claim.

| Boundary | Result |
|---|---|
| Shared foundations | 12 deterministic unittest cases passed, including read-only evidence, incremental edits/deletions, historical reads, typed targets, caller witnesses and provider coverage |
| Project-Lab | 29 deterministic pytest cases passed, including real scheduler DTO validation with injected execution, lifecycle, duplicate submissions, timeouts, measurements, budgets and recovery |
| PC source/profile/contracts | 59 passed plus 6 subtests before final selector additions; selector/XDG/provider/workflow selection then passed 43 |
| PC independent retrieval acceptance | 7 passed after final selector changes |
| PC default smoke | final selection: 43 passed plus 6 subtests |
| Clean source setup without Todo | disposable checkout setup and default smoke passed: 43 tests plus 6 subtests; private scratch resolution keeps machine paths out of a committed lock |
| Retained workflow selection | 42 passed; audit selection 7 passed |
| PC collection | final selection: 595 tests collected without import errors |
| Final PC cross-boundary selection | 24 passed after documentation archival and selector changes |
| Snapshot cache regression | 4 passed: repeats, edits, worktree membership and volatile observations |
| Scheduler focused selection | 81 passed, including schemas, CLI, stdio forwarding, bounded runner and reservation semantics |
| C++ | external-state/export tests, 21 unit/runtime/context checks, genuine compiler export through ordinary server composition, addition/deletion invalidation, reversible rename and same-translation-unit sharding fixtures passed |
| Built packages | seven wheels built; fresh environment installed both applications, both foundations and all three scheduler packages without Todo |
| Installed product | exact profile catalogs and source contexts passed without Todo or archived inference modules; Project-Lab preview and seven-tool catalog passed |
| HTTP transport | installed Observer candidate initialized over MCP, listed ten tools and read a fixture file successfully |

The installed checks are recorded in [package acceptance](validation/installed-acceptance.json) and [HTTP acceptance](validation/http-acceptance.json). Package dependencies came from the isolated wheelhouse; ordinary third-party dependencies were installed into a fresh private environment. No installed production package was replaced.

One broader provider selection had 38 passes and four sandbox-limited failures: three descendant-quiescence results were unknown and one Unix-socket bind was denied. A legacy scheduler ASGI test timed out after 15 seconds without reaching a reported assertion; its HTTP fixture remains unqualified here. Scheduler stdio/schema and application contracts passed. No live scheduler, Codex turn, GPU effect, comprehensive benchmark or packaged production release was qualified.

## Performance sanity

The [reproducible probe](../preservation/performance_sanity.py) and [raw report](../preservation/performance_sanity.json) use a disposable 26-file Python repository. Complete implementation/caller/test context took 51.726 ms cold, 19.460 ms warm and 23.723 ms after one edit. The index changed 26, zero and one file respectively; returned hashes matched the edited bytes.

Snapshot memoization alone showed no measurable benefit in this fixture: median 6.080 ms bypassed versus 6.037 ms cached. The worktree observation was acquired once; current Git identity and volatile metadata still contributed repeated work. These are descriptive local timings, not an end-to-end speed claim. Narrow source operations avoid mandatory full snapshots, and warm queries reuse indexed fragments; broader workload latency still needs measurement when useful.

## Archives, compatibility and limits

The [archive manifest](../archive/assistance-v0/MANIFEST.md) maps original paths and source commits to archived or extracted destinations. Retired inference, inquiry, attention, model-selected skills, deployments and tests are outside installed packages and active discovery. Historical design material remains available without becoming current guidance.

Copied legacy packets, notes and experiments were exercised through compatibility readers/imports. IDs and provenance remain; interrupted experiments stay inspectable until explicit recovery. Notes and model suggestions remain attributed assertions, not independent proof. No live data was migrated.

Python relationships are static observations with explicit ambiguity; dynamic dispatch cannot be inferred completely. Other languages retain lexical/file/history coverage. C++ exports describe their recorded index state and content determinants; read-only retrieval never refreshes the compiler index. Guidance declarations, structural edges and lexical candidates retain different meanings. Result ceilings and continuation do not promise unavailable coverage.

New repositories preserve extraction provenance. No new open-source license was invented; public visibility does not itself grant a license. Production authentication, actual Codex execution, physical GPU cleanup and broad crash/recovery qualification remain separate evidence layers.

## Candidate launch

Follow [development](development.md) for source setup with the sibling foundations checkout, private XDG directories and the candidate port. An Observer launch is:

```sh
export XDG_CONFIG_HOME="$PWD/.dev/config"
export XDG_STATE_HOME="$PWD/.dev/state"
export XDG_CACHE_HOME="$PWD/.dev/cache"
scripts/pc-dev run serve observer --host 127.0.0.1 --port 18768
```

Use `scripts/pc-dev run codex` for the local stdio profile. Project-Lab's setup guide supplies exact sibling-wheel installation, preview, scheduler and worker commands. A fresh source-only candidate needs no Todo; workflow operations require the separately verified compatible Todo wheel.

Review and publication stop here. Merging, production installation, live-state migration, release-pointer changes and service restarts require a separate cutover operation.
