# First practical assistance trial

This walkthrough uses only an already registered workspace and source path.
Replace angle-bracket placeholders with values from Project Control's current
workspace and LAB preview; do not invent IDs. It describes the intended user
flow, not proof that a particular runtime is installed or qualified. Check the
current candidate-bound receipts in [the qualification handoff](qualification-handoff.md)
before relying on live behavior.

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
selected release or runtime identities do not match. A warm service remains
resident while resources are available. Status, quiet, release, and other
model-free controls do not start it.

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
`--gpu-uuid` only for a GPU scope you intend to authorize. GPU hook wiring and
live qualification are still pending in this source snapshot. Do not attempt a
GPU experiment until the selected release exposes the GPU path and has current
native admission and containment evidence. If the required toolchain or proof
is unavailable, do not run it.

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
CUDA proposal and applies its own fixed compiler command. The current source
has the GPU contract, but the complete device handoff and containment path
still requires live qualification; do not interpret a preview or source test
as permission to launch a GPU workload.

## Leave inference stopped for the night

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

Practical trial readiness means these supported paths work with current
candidate-bound evidence. It is separate from the mandatory 40-case product
acceptance matrix; see the [full bounded plan](live-qualification-plan.md).
