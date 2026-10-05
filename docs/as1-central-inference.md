# Central observer inference

Project Control frontend processes use `SkillsObserverAnalysisProvider` as a
client adapter. Every profile, stdio connection and HTTP frontend connects with
`SupervisorClient(observer_analysis_state_root(), root=state_root / "runtime")`.
The operator owns one persistent Skills `SupervisorServer` and its
`ProductionBackend`. Frontends neither construct a backend nor start or recover
that daemon. Closing a frontend disconnects its client; it cannot evict the
central warm model pool. Explicit job session release remains owned broker
cleanup, separate from frontend shutdown.

The operator must start the central service in observer-only mode with the
same trusted configuration as the frontend. `PROJECT_CONTROL_SKILLS_ROOT`
selects the release-bound installed module. Existing release manifest and
canonical Todo runtime identity validation remain in effect.
`PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR` selects the private state root;
the socket lives at `runtime/supervisor.sock` beneath it.
`PROJECT_CONTROL_OBSERVER_GPU_UUIDS`, when set, is a nonempty JSON list of GPU
UUIDs and must match the daemon's configured allowlist. The optional
`PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256` pins the installed supervisor
source explicitly. Settings are captured at provider construction.

Before inference or session admission, the adapter requests cheap
`observer_status` metadata. It requires `observer_contract` equal to
`PC-OBSERVER-SUPERVISOR/1`, the installed module's SHA256, matching private
state/runtime paths, `observer_only=true`, a positive supervisor PID and a
nonempty process start identity, and the configured GPU allowlist. The native
client retains canonical runtime identity validation and private Unix socket
ownership checks. Missing or mismatched ownership fails closed. No GPU topology
probe or model admission occurs during this status handshake.

The adapter forwards `analyze_observer_packet`, `run_observer_turn`,
`open_observer_sessions`, and `close_observer_session`. Existing bounded JSON
packet and conversational formats, audit call IDs and model request envelopes
remain intact. Foreground turn and session admission deadlines cover the owner
handshake, IPC and model inference; the adapter caps their model budget at 60
seconds and status handshakes at two seconds. Explicit session cleanup has a
10-second IPC deadline. The durable broker still owns foreground waiting,
job lifetime, generation fences, packets, read-only authority context and the
source command sandbox.

`/healthz` reports `central_inference` availability and safe owner identity.
`/readyz` requires configured workspaces and a validated central owner.
Central absence, source/root/policy/ownership mismatches and transport timeout
remain distinct from the daemon's admission-busy reasons. There is no local
backend or network fallback.

The CPU qualification in `tests/as1/test_pc_as1_central_inference.py` uses fake
inference and the native Unix client. Two differently configured subprocesses
observe the same owner/model identity, client close leaves that owner intact,
and delayed RPC reads respect the caller deadline. These checks establish
protocol and isolation behavior, not GPU residency or useful model inference.
Real simultaneous stdio/HTTP qualification belongs to the root operator.
