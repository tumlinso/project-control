# Practical assistance trial walkthrough

Use the stable `project-control` CLI with an already registered workspace and
repository-relative source path. Replace angle-bracket placeholders with
values shown by Project Control and the LAB preview; do not invent IDs. The
operator authorized a bounded live practical trial. This walkthrough describes
the CLI flow; see [the current qualification handoff](qualification-handoff.md)
for the candidate identity, completed cases, and unresolved limits.

## Ask a grounded question

Check state first. This reads status without starting inference:

```sh
project-control assistance status
```

Then ask a question about a registered workspace. Select the actual workspace
ID shown in your Project Control configuration:

```sh
project-control assistance ask --project <registered-workspace-id> \
  "Summarize the relevant changes in <relative-source-path> and cite the source evidence."
```

An explicit question uses the shared CLI/Codex broker. A fresh cached answer
can return without starting inference; new or stale work starts or reuses the
shared service. Startup is bounded and reports a readiness error if the
selected release or runtime identities do not match. The service stays warm
while resources are available. Status and operator controls do not start
inference.

Foreground `ask` and chat questions wait up to five minutes for a terminal
result. If still incomplete, the CLI requests cancellation of that exact
inquiry and waits up to two minutes for worker cleanup. An unconfirmed
cancellation or cleanup returns `unavailable`; the CLI does not submit another
inquiry after its deadline. Treat timeout as incomplete work, not as an answer.

## Preview and authorize one LAB scope

Choose a registered workspace, one or more repository-relative source paths,
and a goal that can be tested safely. Preview is cold: it captures and displays
the proposed project, source, tool, resource, time, and experiment bounds but
does not start inference or execute the proposal.

```sh
project-control assistance lab preview \
  --project <registered-workspace-id> \
  --source <relative-source-path> \
  --goal "Test <specific behavior> using a bounded local check"
```

Review the returned scope and its `session_id`. The defaults are 600 seconds
and six experiments, including planning, waiting, execution, and interpretation.
The scope lists its allowed tools and resources. Add `--source` for each
additional path, `--tool` for each additional permitted executable, and
`--gpu-uuid` only for a GPU scope you intend to authorize. A GPU preview does
not itself establish that a GPU run is qualified. Before authorizing one,
check the current handoff for selected-release support and current admission,
containment, and cleanup evidence. If the required toolchain or proof is
unavailable, stop without running it.

Authorize the exact preview once, using the ID returned by that preview:

```sh
project-control assistance lab authorize <session_id-from-preview>
project-control assistance lab status --scope-id <session_id-from-preview>
```

Authorization starts the finite scope clock. It permits the controller to
plan and execute multiple experiments autonomously inside the displayed
project, path, tool, resource, and time bounds. It does not authorize changing
those bounds, writing canonical source, installing dependencies, or applying
a candidate.

## Run, inspect, resume, or cancel

Run the authorized scope. Planning and execution can start the shared inference
runtime; CPU and GPU effects remain subject to the displayed scope and native
containment/resource admission.

```sh
project-control assistance lab run --scope-id <session_id-from-preview>
project-control assistance lab status --scope-id <session_id-from-preview>
```

If the scope pauses or the process is interrupted, inspect its status before
resuming. Ambiguous effects are marked unknown and must be inspected; they are
never replayed automatically.

```sh
project-control assistance lab resume --scope-id <session_id-from-preview>
```

Cancel the scope when you want its remaining budget discarded:

```sh
project-control assistance lab cancel <session_id-from-preview>
```

CPU experiments run inside bounded containment. GPU experiments require an
explicit device UUID and the native CUDA foreground controller to grant the
devices after inference yields and releases its resources. Project Control
rewarm and interpretation happen after the CUDA owner releases the devices.
If device, process, filesystem, or cleanup proof is missing, execution must
stop before claiming a result. LAB keeps generated artifacts as reviewable
results; it does not apply them to canonical source. Any later canonical
change requires a separate review and authorization.

The CPU container is limited to one CPU, 1 GiB of memory, 32 processes, no
swap, a 30-second run, 64 KiB of captured output, and 64 MiB of artifacts.
For a GPU preview, pass each requested device as `--gpu-uuid` and the approved
toolchain location as `--toolchain-root`. The GPU adapter accepts a structured
CUDA proposal and applies its own fixed compiler command. Check the current
qualification handoff for selected-release GPU support and exact device,
admission, containment, and cleanup evidence; do not infer run eligibility
from a preview or source test.

## Stop or resume inference when you are done

Request release when no more inference should run, then check the result:

```sh
project-control assistance stop
project-control assistance status
```

Stop only reports success after owned work is cancelled and owned resources
are verified released. If it reports a pending or failed release, the durable
release veto remains active. Resolve or inspect the named work before
continuing. `project-control assistance resume --release` clears the veto; it
does not start inference. Finish with status and the physical release evidence
provided by the active runtime, not with an idle timer or a `pending` projection.

Use the final status and resource receipts linked from the current handoff to
confirm cleanup. A `pending` status field or an idle timer alone does not prove
physical release. Practical trial results remain separate from the mandatory
40-case product acceptance matrix; see the [full bounded plan](live-qualification-plan.md).
