# Historical assistance command cookbook

> **Check the live checkout before using these recipes.** This cookbook was
> written for an earlier installed runtime and contains candidate-specific
> assumptions. It is not authoritative for current profile schemas, service
> identity, task state, or authorization. Use
> [`scripts/pc-dev run assistance --help`](../../scripts/pc-dev) and the current
> [source qualification guide](source-qualification.md) for checkout behavior;
> use [deployment](../deployment.md) for service operations and reconnect MCP
> clients after a source restart. Current profile tools and arguments come from
> that connection's `tools/list`. The mutator rescue flow is advertised in its
> startup instructions and does not require the Todo Orchestrator skill.

The commands below are retained as historical operational context. Confirm
each command and permission against the current checkout and active Todo task
before use; do not infer that a historical trial or candidate is current.

## Historical recipes

Project Control's stable CLI (`/home/tumlinson/.local/bin/project-control`) and
Codex use one local assistance runtime. An explicit
`ask`, `run`, question in `chat`, or supported MCP/HTTP `investigate` or `skill`
request can start that runtime on demand. A model-free status or control call
does not start it. Notifications and automatic preparation are off by default.
The model stays warm while GPU resources are available; idle time alone does
not evict it. Pertinent CUDA or GPU LAB work can preempt it through resource
admission, after which eligible inference can rewarm when those resources are
released.

Replace `<project>` with a registered workspace ID. If a workspace has an
authority repository, that repository is used by default. For a workspace
without one, select a repository explicitly with `--repository`; the command
does not guess between multiple repositories.

## Check state and record intent

```sh
project-control assistance status
project-control assistance start
project-control assistance goal <project> "Understand the parser's quoted-token behavior"
```

`status` reads operator, runtime, and active-work state without starting
inference. `start` explicitly starts the shared inference service and waits up
to 120 seconds for the selected release and its receiver and Todo runtime
identities to verify. Explicit questions use the same service, so concurrent
cold requests share one serialized startup. On a mismatch or timeout, read the
reported reason and status; do not treat a failed readiness check as a
successful start. A goal is saved as a user-authored notebook card. It does
not enable automatic work, create a project task, or grant source access.

The model stays warm while resources are available, until resource admission,
a release request, or an explicit stop requires it to yield. Do not use elapsed
idle time as evidence that it was evicted or restarted. Status reports the
service and available release, residency, wait, active-work, and release
information without asking the model to run.

Set a focus to record what matters now. Source paths are optional for a
non-automatic focus:

```sh
project-control assistance focus <project> "Review parser edge cases" \
  --repository <alias> --path src/parser.py
```

Automatic preparation requires a separate, explicit opt-in, at least one
selected source path, and a finite window of at most 24 hours:

```sh
project-control assistance focus <project> "Review parser edge cases" \
  --repository <alias> --path src/parser.py --automatic --for 30m
```

Durations accept seconds, minutes, hours, or days (`30s`, `30m`, `2h`, `1d`)
and plain seconds. Source paths must be relative to the selected registered
repository. Private, generated, and symlinked paths are rejected. The focus
and its goal card are saved separately from the automatic inference grant.
Creating a focus does not start the observer service or watcher. Source-change
checks run only while the selected observer service is running and only for an
active automatic focus. Each focus is capped at four nominated change roots,
24 reserved turns, and 900 active seconds.

## Quiet and release controls

Quiet suppresses automatic work until its duration ends or it is explicitly
resumed. Give a duration or a timezone-qualified ISO timestamp:

```sh
project-control assistance quiet --for 45m
project-control assistance quiet --until 2026-10-06T20:00:00Z
project-control assistance resume --quiet
```

Request inference release separately. The persistent veto is recorded before
Project Control asks its existing supervisor to release owned resources:

```sh
project-control assistance release
project-control assistance release --for 1h
project-control assistance resume --release
```

If the supervisor cannot verify cleanup, the command reports a pending state.
That state does not claim that a model process or device memory has been
released. Resume each control independently, or clear both explicitly:

```sh
project-control assistance resume --all
```

Stop the shared service only after Project Control has cancelled active work
and verified release of resources it owns:

```sh
project-control assistance stop
project-control assistance status
```

If stop reports `needs_coordination` or `not_stopped`, the release veto remains
active and the service may still be running. Inspect status and resolve the
owned work or release proof first. To withdraw the durable release veto and
allow future explicit demand, use `project-control assistance resume --release`.
That command clears the veto; it does not itself start inference. Use `start`
or an explicit question when ready to make the runtime available again.

## Ask, chat, and review suggestions

An ask is explicit model-backed work when the configured local observer runtime
is available:

```sh
project-control assistance ask --project <project> "What changed in the parser?"
project-control assistance run --project <project> "Check the quoted-token path"
project-control assistance chat --project <project>
```

In chat, enter `/status`, `/quiet 45m`, `/release`, `/resume quiet`,
`/resume release`, `/resume all`, `/accept NOTE_ID`, or `/exit`. Use `/ask TEXT`
for an explicit question; any other non-command line is also treated as a
question. Waiting for input does not hold an inference session.

The CLI and Codex question paths share the same broker, inference service, and
resource admission. A fresh cached answer can be returned without starting
inference; new or stale work uses verified demand startup. Choose CLI `start`
and `stop` for explicit service controls; ordinary model-free controls remain
available while inference is stopped.

The foreground `ask` command and questions entered in `chat` wait up to five
minutes for a terminal inquiry result. If the inquiry is still incomplete,
the CLI requests cancellation for that exact question and waits up to two
minutes for its worker cleanup. It reports `unavailable` when cancellation or
cleanup is unconfirmed and does not submit another inquiry after the deadline.
A timeout is not evidence that an answer completed.

Request a proposed handoff for the current or named focus:

```sh
project-control assistance handoff <project>
project-control assistance handoff <project> --focus-id <focus-id>
```

The handoff is advisory and requires an operator decision. User goal cards take
priority over inferred suggestions. To make one named model suggestion a user
goal, accept it explicitly:

```sh
project-control assistance accept <project> <note-id>
```

To dismiss a suggestion, copy its `focus_id` and `input_fingerprint` from the
suggestion's coverage metadata:

```sh
project-control assistance dismiss <project> <focus-id> <input-fingerprint>
```

Acceptance records user intent only; it does not publish a finding or create a
Todo task. Source text and model output cannot run these controls by themselves.

## Qualification status

Automatic preparation remains off unless enabled explicitly with selected
registered source paths and a finite duration. Keep it off during a manual
practical trial. LAB provides a separate scope preview, one-time authorization,
and bounded autonomous execution; see [the practical trial walkthrough](practical-trial.md)
for a short sequence and [the finalization plan](finalization-plan.md) for the
acceptance boundaries. The preview displays the project, paths, tools, resource
selection, time limit, and experiment limit. Review those values before using
the returned session ID with `lab authorize`; the authorization permits only
that displayed finite scope. Use `lab status` to inspect progress, and use
`lab resume` or `lab cancel` with the scope ID to manage an interrupted or
unwanted run.

The command surface and source implementation do not by themselves prove that
the selected installed release is current or live-qualified. Check the latest
candidate-bound receipts in [the qualification handoff](qualification-handoff.md)
and [the live qualification plan](live-qualification-plan.md). The operator
authorized a bounded live practical trial; its outcomes and limits belong in
the current handoff, not in these command descriptions. Existing
case-level and live receipts remain in [acceptance results](acceptance-results.json)
and [live validation](live-validation.json); preserve their original scope and
limitations when deciding what is currently usable. A feature with no current
installation and live evidence remains pending qualification.

For source development, the source `.venv` link now resolves to `.dev-venv`.
This keeps development dependency operations separate from the selected
immutable runtime. Use the stable CLI for operator controls and consult the
current handoff before treating a command path as live-qualified.
