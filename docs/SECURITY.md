# Security

Project-control is a structural read-only boundary, not a prompt convention.
It accepts registered workspace and repository aliases only. The local operator
may add absolute paths through the admin CLI; MCP callers cannot provide roots,
commands, URLs, credentials, or service endpoints.

The server accepts only literal loopback hosts and exposes Streamable HTTP at
`/mcp`. Remote access is through OpenAI Secure MCP Tunnel. There is no public
listener, OAuth implementation, resource endpoint, prompt endpoint, sampling,
elicitation, UI, generic file tool, arbitrary shell, or URL fetcher.

Canonical reads resolve symlinks and require containment beneath a registered
root. Default deny patterns cover Git internals, environment and credential
files, private keys, model weights, dependency trees, and Python caches without
blocking ordinary source identifiers containing `key`. Files must be UTF-8
text, binary-free, and no larger than 2 MiB. Subprocesses use fixed argument
vectors, no shell, bounded output, short timeouts, and a minimal environment.

Todo is read only through public status/export/ready/explain/changes and plan
validate/diff behavior covered by non-mutation tests. Ctxpp never scans or
refreshes a registered repository. Worker status reads an existing state file
without starting a supervisor. CUDA status reads existing artifacts and never
discovers with auto-queue, arms, enqueues, runs, profiles, reserves, or preempts.
Host inspection is limited to `/proc/meminfo` and a bounded `nvidia-smi` query.

Results redact secrets and omit raw tokens, database paths, GPU UUIDs, topology,
endpoints, environments, command lines, raw logs, profiler exports, and worker
transcripts. App-private cache, logs, configuration, and temporary files use XDG
directories with owner-only permissions and are never project authority.

Tunnel credentials belong only in the official client's supported secret store
or owner-only local configuration. They must never be committed or pasted into
ChatGPT or Codex.
