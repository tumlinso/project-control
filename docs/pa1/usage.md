# Local assistance controls

Project Control's assistance CLI keeps goals, source preparation, and local
inference under separate operator controls. It is demand-only by default, and
notifications are off. `status`, `goal`, `focus`, `quiet`, `release`, and
`resume` do not start an inference worker. An explicit `ask` or `run` starts the
existing observer broker for that request; ordinary chat lines are also
explicit questions.

Replace `<project>` with a registered workspace ID. If a workspace has an
authority repository, that repository is used by default. For a workspace
without one, select a repository explicitly with `--repository`; the command
does not guess between multiple repositories.

## Check state and record intent

```sh
project-control assistance status
project-control assistance goal <project> "Understand the parser's quoted-token behavior"
```

`status` reads the saved focus and power state without starting the dispatcher
or contacting the model service. A goal is saved as a user-authored notebook
card. It does not enable automatic work, create a project task, or grant source
access.

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

These controls are implemented in the source checkout; installation,
activation, and live qualification of this candidate remain pending. The
isolated scratch laboratory is not yet available. Status and control commands
above are model-free. The `ask`, `run`, and question lines in `chat` are the
explicit inference entry points.
