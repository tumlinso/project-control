# Adaptive role surface

The AS1 frontend uses one shared information implementation with startup-bound
profiles. This guide describes the source surface; candidate qualification and
live activation require their own acceptance evidence. The name/mode authority
is [surface.json](../planning/adaptive-surface-v1/contracts/surface.json).

| Profile | Tools | Detail | Native files and shell |
| --- | --- | --- | --- |
| Observer (HTTP) | Eight shared tools plus `read`, `investigate`, `skill` (11) | compact, standard, extended | No |
| Coder (`codex` compatibility name, stdio) | Eight shared tools plus four workflow tools (12) | compact, standard | Yes |
| Mutator (local stdio) | Eight shared tools plus `investigate`, four workflow tools, `plan`, `amend_project`, `maintain_execution` (16) | compact, standard | Yes |
| Investigator (internal) | Eight shared tools plus `command`, `log` (10) | compact, standard | Yes, within the read-only sandbox |
| Skill assembler (internal investigator mode) | Same ten internal tools, home at registered Skills root | compact, standard | Yes, within the read-only sandbox |

The eight shared tools are `overview`, `delta`, `frontier`, `search`, `evidence`,
`impact`, `history`, and `machine`. The four workflow tools are `next_task`,
`inspect_task`, `coordinate_task`, and `finish_task`. Native local files, shell,
`find`, `rg`, Git, and native skills remain native capabilities. Project Control
`read` and `skill` are observer adapters, not local MCP substitutes.

Startup is lazy: no profile automatically receives overview, scans every
registered project, or loads a model. Empty skill catalog discovery and retrieval of
current cached answers need no inference. The runtime supervisor and resource interlock
own inference residency and GPU policy; callers do not choose models or GPUs.

## Choosing a tool

- `overview()` discovers the registered catalog; `overview(project="demo")`
  gives purpose, architecture, constraints, and source-backed orientation.
- `frontier(project="demo")` gives current claims, work, blockers, lanes,
  integration and recovery attention. Use it to understand coordination.
- `search(project="demo", query="packet retention")` preserves discovery.
  `search(project="demo", query={"kind": "task", "target": "T1"})` uses the
  canonical exact lookup directly. Missing exact records remain missing; they
  do not trigger fuzzy retrieval or filesystem discovery. There is no public
  Project Control `find` tool.
- `evidence` distinguishes declarations and source mentions from current passes,
  stale historical passes, pending/failed evidence, and terminal frozen success.
  `impact` reports typed dependency witnesses and provider gaps; `history`
  traces material recorded events and Git ancestry. Neither guesses causality.
- `machine` returns bounded observed host facts with observation time. It does
  not launch benchmarks, reserve GPUs, or run arbitrary privileged commands.

For an observer, exact file content uses registered project/repository IDs and
relative paths, for example `read(project="demo", paths=["README.md"])`.
Batch elements retain independent statuses and exact identities/ranges. Requested
symlinks, escaping paths and unsupported binaries are refused; historical reads
use the requested revision. Local agents use native file access instead.

## Packets, jobs, and skill navigation

Information results carry `status`, `packet`, `data`, `sources`, and `coverage`.
Source locators identify registered projects/repositories, relative paths and
actual content/revision identity. Semantic records carry typed IDs rather than
invented file paths. Compact is the default; 2 KiB compact, 8 KiB standard, and
64 KiB observer extended are soft whole-response budgets. Omissions, stale or
unavailable sources, and exact continuations stay visible. Retained packet aliases
are scoped references to evidence, never mutation capabilities.

`investigate(question="What invalidates this packet?", project="demo",
request_id="packet-question-1")` reads a cached inquiry. Public calls return a
supported `completed`/`partial` answer or `thinking`, `busy`, `unavailable`; they
do not accept a job ID or expose scheduler metadata. Only thinking advises
continuing useful work and repeating the identical question later without
variants. Busy means not accepted: use search, read or evidence to contextualize
or refine a later question. Missing providers and unavailable results are explicit.

One global cache/log retains the last 50 answered inquiries across trusted
callers and profiles. Identity is the original literal question, mode, selected
skill and project/authority context; caller principal/profile are not cache keys.
Detail, request IDs and advisory hints do not duplicate the inquiry. Caller
project/source allowlists still govern reuse. Private hints and their derived
answers remain protected; a global index does not grant access to private context. Pending
exact repeats preserve its scheduler state. Current answers return immediately.
A stale supported answer starts a new generation seeded with its old answer,
evidence and changed-source information so the agent preserves still-valid work.
The private scheduler permits two executing and four waiting inquiries, a
30 second foreground wait and 300 second lifetime.

Observer `skill()` cheaply lists the installed catalog. Use
`skill(query="Volta register pressure", skill="cuda", request_id="volta-1")`
for a cached skill inquiry with the same literal-repeat and freshness behavior.
Investigation and skill share the private durable broker and retained evidence;
consultation remains advisory and does not record applied skill use automatically.
See [as1-inquiry-cache.md](as1-inquiry-cache.md) for empty terminal results and
material dependency freshness.

Project Control brokers authorized access, persistence, freshness, exact source
reads and provenance. The local read-only worker performs semantic navigation:
it reads the installed `SKILL.md` first, then follows authored maps, references
and prerequisites. With no selected skill, the registered discovery guide leads
to the installed catalog; each selected skill's own entry remains authoritative.
Indexes and graphs can accelerate navigation but do not replace this agentic
routing. Results separate small labeled worker synthesis from broker-verified
original excerpts, hashes and line ranges. Missing prerequisites, changed text,
redaction and unread resources remain explicit omissions.

## Workflow, control, and inactive features

Coder starts substantial work with `next_task`, retrieves needed handle-scoped
context through `inspect_task`, synchronizes through `coordinate_task`, and
uses `finish_task` for disposition and required gates. Claims and mutation
preconditions remain canonical Todo authority. Read-only questions need no claim.

Mutator also has `investigate` and the four workflow tools. `plan` consolidates
validate/diff/apply/amend/supersede/retire; `amend_project` handles typed durable
semantic declarations; `maintain_execution` handles diagnose/prepare/execute
through native current-state guards and principal-bound grants. These operations
cannot authorize themselves through caller role strings, packet hints or approval
prose. CLI owner compatibility remains separate from ordinary model permissions.

`delegate_task` and `collect_delegation` preserve their implementation and history
but are absent from discovery and rejected at dispatch as `temporarily_inactive`.
Feature metadata records the reason and explicit operator reenable policy. There
is no timed or automatic reactivation. Use configured Codex subagents for bounded
coding/research delegation under the owning root claim.

Old public names such as `project_overview`, `architecture_context`,
`source_context`, generic `inspect`, `skill_list/read/context`, `plan_preview`,
`apply_plan`, `terminal_capture` and `performance_probe` are absent from ordinary
AS1 discovery and dispatch. Backend and CLI implementations may remain for
compatibility and history. Hiding names alone is insufficient: profile and mode
checks occur before backend dispatch, using trusted startup identity.

## Standalone paired delivery

Project Control and Skills remain standalone repositories. An explicit paired
release manifest binds distinct authority UUIDs, exact commits, runtime identities
and producer receipts. Do not install a Project Control source copy or submodule
in Skills. A frozen required Skills runtime in a candidate is a deployment input,
not merged source ownership. Preserve prior launchers/candidates for rollback and
queued forward state; never restore an old Todo database over newer work.

Producer ports and qualification limits are documented in
[context](as1-context.md), [trace](as1-trace.md), [jobs](as1-jobs.md),
[skill](as1-skill.md), and [control](as1-control.md).
