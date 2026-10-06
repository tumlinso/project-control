# Adapter contract

Adapters expose `inspect`, `start`, `health`, `run`, `cancel`, `drain`, `evict`,
and `usage`. `AdapterService` only routes these calls; it does not own todo
state, source authority, scheduling priority, or model selection policy.

Harness `start` creates a disposable task session; `run` performs one bounded
headless invocation. Qwen Code uses JSON output, safe mode, plan approval,
explicit tool exclusions, and turn/time/tool budgets. Codex CLI uses
`exec --json --ephemeral` with a read-only sandbox and no approval prompts.
Neither adapter resumes a prior agent session or recurses into another agent.

The llama.cpp adapter accepts only an existing local model path outside the
repository, binds loopback, checks `/health`, and sends non-streaming requests
to `/v1/chat/completions`. It never downloads a model or starts implicitly.

`drain` rejects new work while preserving current state. `cancel` is scoped to
an active harness process or server request ID. `evict` terminates owned
processes and deletes disposable harness state. Routine results and usage are
bounded; credentials and full transcripts are not returned.
