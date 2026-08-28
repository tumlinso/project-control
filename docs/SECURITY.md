# Security

Project-control's authoritative project/workflow/source query plane is a
structural read-only boundary, not a prompt convention.
It accepts registered workspace and repository aliases only. The local operator
may add absolute paths through the admin CLI; MCP callers cannot provide roots,
commands, URLs, credentials, or service endpoints.

The server accepts only literal loopback hosts and exposes Streamable HTTP at
`/mcp`. Remote access is through OpenAI Secure MCP Tunnel. There is no public
listener, OAuth implementation, resource endpoint, prompt endpoint, sampling,
elicitation, UI, generic file tool, arbitrary shell, or URL fetcher.

Tool schema v3 adds one explicit non-idempotent observational execution
capability, `terminal_capture`. It accepts only a contained executable and cwd
from a registered repository plus a literal bounded argument vector. It rejects
absolute paths, `..`, symlink escape, denied paths, non-executable files,
caller-controlled environments, command strings, URLs, and shell expansion.
It provides no stdin or signal API.

Terminal launch is fail-closed behind bubblewrap. The namespace contains the
selected repository read-only, required system runtime paths read-only, `/proc`
and minimal `/dev`, an isolated writable `/tmp`, an isolated empty HOME, cleared
environment with fixed PATH/TERM, dropped capabilities, and no network. Denied
repository paths and Git/todo/ctxpp internals are masked inside the read-only
mount. If bubblewrap is absent, doctor reports it unavailable and the tool does
not execute unsandboxed. The remaining service isolation controls stay in
force.
The service address-family allowlist includes `AF_NETLINK` because bubblewrap
uses `NETLINK_ROUTE` to initialize loopback inside the newly isolated network
namespace. This is kernel namespace setup, not external network access; the
child remains inside `--unshare-all` with no host network. Doctor reports the
probe and installed-service policy separately, using bounded codes for missing,
timeout, namespace, mount, permission, generic probe, and service-policy
failures. MCP execution remains fail-closed as `terminal_sandbox_unavailable`.

Each launch owns a new session/process group and real PTY. Default capture sends
graceful termination to the whole group, escalates to forced termination,
closes descriptors, and reaps. Retained sessions live only in a bounded,
thread-safe app-private registry: four per workspace, eight per service,
1 MiB per capture interval, 4 MiB lifetime stream, 30-second wait, and bounded
geometry/arguments. A bonded session expires after 30 minutes idle or four
hours total. Concurrent reads of one PTY cannot race. Service shutdown
terminates all bonded groups; none is orphaned or represented as durable state.

Canonical reads resolve symlinks and require containment beneath a registered
root. Default deny patterns cover Git internals, environment and credential
files, private keys, model weights, dependency trees, and Python caches without
blocking ordinary source identifiers containing `key`. Files must be allowlisted
UTF-8 text and binary-free. Narrow line-range reads stream bounded content and
therefore work for larger files without loading the whole file. Subprocesses use
fixed argument vectors, no shell, bounded output, operation-specific bounded
timeouts, and a minimal environment.
Read-only todo CLI calls additionally preserve only the state-location variables
required by its public resolver (`TODO_ORCHESTRATOR_STATE_DIR`, `XDG_STATE_HOME`,
and `HOME` when present) and use the server's running Python interpreter.

Todo operational truth is read through `semantic workflow`; task semantics and
history use `semantic state`, `semantic anchor`, and `semantic delta`. The
official read-only export only enriches semantic anchors. No observation calls
message sync, updates receipts/cursors, takes a claim, or invokes recovery.
Ctxpp never scans or refreshes a registered repository. Worker status reads an existing state file
without starting a supervisor. CUDA status reads existing artifacts and never
discovers with auto-queue, arms, enqueues, runs, profiles, reserves, or preempts.
Host inspection is limited to `/proc/meminfo` and a bounded `nvidia-smi` query.

Results redact secrets and omit raw tokens, database paths, GPU UUIDs, topology,
endpoints, environments, command lines, raw logs, profiler exports, and worker
transcripts. App-private cache, logs, configuration, and temporary files use XDG
directories with owner-only permissions and are never project authority.
The derived lexical cache is disposable and lives only under
`$XDG_CACHE_HOME/project-control/`; no cache is written into a repository,
Git-common directory, todo state, or `.ctxpp`.
Terminal results receive the same redaction after VT rendering and expose only
the screen framebuffer plus bounded lifecycle metadata—not raw PTY bytes,
stdout/stderr, transcripts, commands, host paths, environment, or PID.

Tunnel credentials belong only in the official client's supported secret store
or owner-only local configuration. They must never be committed or pasted into
ChatGPT or Codex.
