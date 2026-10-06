# Broker-facing observer port

## Central persistent inference owner

All Project Control investigator/skill profiles and HTTP/local MCP transports
connect to one operator-managed `SupervisorServer` and its single persistent
`ProductionBackend` pool. Frontends use
`SupervisorClient(state_root, root=state_root / "runtime")` as the observer
backend. They never construct `ProductionBackend`, call `ensure_running`, spawn
a supervisor, or fall back to a private model pool. A missing owner raises
`central_supervisor_unavailable`. `client.close()` closes no model or session;
each RPC already owns and closes its own short-lived Unix connection. Explicit
`close_observer_session(session_id, deadline_epoch=...)` releases only that
session and preserves the existing 900-second idle warm residency.

Each exclusive observer session is borrowed by the connecting frontend process
identified by Unix peer PID and process start, with the requested absolute
deadline capped at 300 seconds (300 seconds when omitted). An undeliverable
open response releases only the sessions created by that request. Housekeeping
also releases that exact session when its borrower process is proven gone or
replaced, or its deadline expires. Unknown process presence retains the session
until expiry. Active turns defer release until native session cleanup succeeds;
the server does not evict the pool or cancel another client's accepted work.
Verified session release keeps healthy model PIDs warm for later borrowers.
The borrower is the persistent HTTP/MCP frontend process, not an individual
HTTP request caller: a request disconnect does not cancel an accepted job.
Sessions remain exclusive; no logical multiplexing or shared active model
lease is introduced.

The explicit JSON RPC methods are:

| Client method | Operation | Parameters |
| --- | --- | --- |
| `observer_status(deadline_epoch=None)` | `observer-status` | `deadline_epoch` |
| `analyze_observer_packet(packet)` | `observer-analyze` | `packet` |
| `run_observer_turn(request)` | `observer-turn` | `request` |
| `open_observer_sessions(count, compute_profile="narrow", parallelism="default", deadline_epoch=None)` | `observer-open` | `count`, `compute_profile`, `parallelism`, `deadline_epoch` |
| `close_observer_session(session_id, deadline_epoch=None)` | `observer-close` | `session_id`, `deadline_epoch` |

Observer operations never autostart. Packet/turn deadlines are inside their
respective JSON objects; open/status/close deadlines are top-level parameters.
Invalid, nonfinite, or expired deadlines fail closed. Open/analyze/turn deadlines
are capped at 300 seconds by the owner; the client bounds the entire IPC wait to
the supplied remaining deadline and at most 300 seconds. Frontend IPC timeout
does not cancel an accepted durable broker job or evict a warm model.

`observer-status` is a memory-only projection; it performs no model health HTTP
or GPU query. The reply includes `observer_contract="PC-OBSERVER-SUPERVISOR/1"`,
`observer_only`, `supervisor_pid`, `supervisor_process_start` (string `/proc`
start ticks), startup `source_sha256` of `local_worker/supervisor.py`,
`service_state_root`, `runtime_root`, `allowed_gpu_uuids`, `runtime_identity`,
and `idle_ttl_seconds`. Known slots include `slot_id`, `server_pid`, `owner_id`,
`gpu_uuids`, `service_lease_id`, `model_id`, and `model_sha256`. The client checks
the reported owner PID against `SO_PEERCRED`, its live process start identity,
and canonical runtime identity. Later observer RPC sockets must match the last
validated owner PID/start, refusing a restart between qualification and use.
The PC consumer must additionally check the
expected source hash, contract, fixed roots and approved GPU UUIDs before use.

The wire protocol is one newline-terminated JSON object per connection, with
`{"ok":true,"data":...}` or `{"ok":false,"error":...}` responses. Requests
and responses are each limited to 512 KiB. Malformed/non-object/nonfinite,
multiple, incomplete and oversized frames have explicit errors. Connections
are bounded to 32, with a 10-second complete-frame read/write timeout. The
server checks same-UID `SO_PEERCRED`; clients check private mode-0700 runtime
directory and mode-0600 socket/sidecars, rejecting symlinks and foreign owners.
Independent connections execute concurrently, while a shared limit of two
observer analyze/turn executions spans all clients. Existing model lifecycle
locks and the pool's two-slot limit remain authoritative. Polling and sidecar
updates run in their own thread so a cold start cannot block accept/status.

Only the root/operator starts or stops the central owner. An example startup is:

```bash
python -m local_worker.supervisor --serve --repo-root "$observer_state" \
  --service-state-root "$observer_state" --runtime-root "$observer_state/runtime" \
  --observer-only --allowed-gpu-uuid "$approved_uuid_1" \
  --allowed-gpu-uuid "$approved_uuid_2"
```

Use the verified paired release Python and `PYTHONPATH`/canonical runtime
environment. `PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR` supplies the fixed
state root when flags omit it; in observer-only mode an explicit conflicting
root is refused. `CORE4_SUPERVISOR_RUNTIME_DIR` supplies the runtime root and
must agree with explicit settings and the central `state_root/runtime`.
`PROJECT_CONTROL_OBSERVER_GPU_UUIDS` is a nonempty JSON list of approved UUIDs;
explicit repeated GPU flags may narrow that list, never widen it. Existing
production-profile restrictions are also preserved. Observer-only mode requires
fixed state root and a nonempty GPU allowlist and disables maintenance RPCs;
private same-UID `stop` remains operator-controlled and requires verified
quiescence. Legacy maintenance startup defaults remain available without
`--observer-only`.

SIGTERM/SIGINT stop accepting requests, drain and wait for outstanding calls,
then use native eviction and verify quiescence. The default shared shutdown
wait is 330 seconds (`--shutdown-timeout-seconds`, maximum 330). The root should
quiesce broker work before shutdown, use `TimeoutStopSec=360`, `KillMode=process`
and `SendSIGKILL=no`, and qualify native cleanup. A lifecycle lock held by a
cold start can delay drain until its bounded observer deadline. Failed native
quiescence reports `supervisor_shutdown_not_quiescent` and retains protected
residency/lease evidence; no arbitrary process or foreign owner is signaled.

CPU fixtures with actual Unix sockets establish transport, concurrent sessions,
capacity and lifecycle behavior only. They do not qualify deployed GPU/model
inference or useful scientific answers.

## Read-only execution port

`local_worker.observer_runtime.ObserverWorkerPort` reuses
`ProductionBackend.run_observer_turn`, including checked installed model policy,
resource admission and warm slots. It adds no daemon, queue, scheduler, download,
network fallback or durable database. The broker must dispatch committed jobs
independently of MCP request lifetime and preserve observations after eviction.

Construct `ReadOnlyCommandRunner(roots, packetize=..., credential_paths=...)`
with trusted registered roots and credential exclusions. Never derive mounts
from a model response. Commands take argv, cwd, timeout and byte limits, and
run in Bubblewrap with read-only mounts, private process/network namespaces,
cleared environment, dropped capabilities, masked credentials and synthetic
devices. Scratch is disposable. Output is bounded while pipes are drained;
timeout and completion clean up the process group. A missing sandbox or failed
packet commit returns an honest failure. Packetization is supplied by the broker.
Command provenance is explicitly coarse and volatile, including observations
outside a nominated project. Do not infer exact file dependencies from argv.

Construct `ObserverWorkerPort(backend, command=..., tools=..., fence=...,
checkpoint=...)`. `tools(name, arguments)` returns a packetized read-only shared
tool result; it must use the investigator policy and broker storage, never a
coder/mutator profile. Allowed tools are command, log, overview, delta, frontier,
search, evidence, impact, history and machine. Recursive investigate/skill,
read adapters, child delegation, task claims and mutation are denied before
the callback. Machine diagnostics use fixed broker adapters, never device mounts.

`run(request)` accepts job_id, positive attempt, mode (`investigate` or `skill`),
question, optional scope/hints/observations, max_steps (1..12 model rounds), session_id and
existing compute_profile/parallelism.
The default six-round budget reserves its final model round for final JSON
synthesis from retained observed evidence, with unresolved work reported as
partial. At most five model-requested tools are dispatched; a tool proposal in
the final round is refused without another model turn. Authoritative final
source validation reads retain the same overall deadline.
Skill requests include a broker-registered `skill={name,root}` constrained to the runner's trusted mounts. No whole-project
overview, instruction files, or previous caller context is automatically added.
Skill mode prompts the agent to read installed SKILL.md and traverse its own
maps/references; indexes remain advisory. Final selections are checked against
sandbox-observed resource hashes and line ranges within the registered root.
The installed entry must be the first successful skill read, and every selected
resource must have been read by the agent before its final answer. Direct
`cat` argv reads record exact path, hash and line count in the command packet;
other commands retain coarse provenance. A verifier read cannot substitute for
agent navigation. Verification rereads only to check freshness and ranges.

The result echoes job_id and attempt and has status completed, partial, yielding,
queued_after_eviction or stale_attempt. Results contain compact findings,
observations and unresolved questions; skill results include a source selection.
Resume using job-owned observations, not hidden reasoning or opaque KV state.
Explicit supervisor session leases may be shared for warm model reuse because
each turn sends independent explicit messages. No transcript is kept on the port.

`fence(job_id, attempt)` must check the broker's current generation. It is checked
before and after inference/tools and before packetization/checkpoint. Checkpoint
and final broker commits must ALSO perform atomic generation checks in their own
transaction: a Python callback check cannot close a transaction race. Late
attempts return stale_attempt without publishing model answers. An immutable
packet created just before eviction may survive, but cannot update a new attempt.
Checkpoint receives observed evidence only. Disconnects and lease expiry belong
to broker lifecycle; this synchronous port never cancels an accepted durable job.

Existing coding delegation/collection remains functional through internal kernel
APIs. The compatibility MCP's native routing wrapper excludes those handlers
from tool listings and rejects dispatch with `temporarily_inactive`. The canonical
kernel server and methods remain unchanged for explicit maintenance and tests.
This guard covers the Skills-owned compatibility fallback entrypoint selected
when the current Project Control CLI is absent. The preferred installed Project
Control CLI still receives the original `serve codex` forwarding call. Its native
profile discovery/dispatch and deployed startup are a PC-SURFACE consumer
requirement, followed by end-to-end API-03/API-04 and paired SQA validation; this
worker source change does not claim those producer/deployment checks passed.

Scripted model transports and supervisor fixtures establish protocol behavior,
not inference. Qualification must separately exercise the installed verified
model under the host CUDA interlock and demonstrate useful evidence-backed
answers and skill selections. GPU launches require coordinated admission.
