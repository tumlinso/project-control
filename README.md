# Project Control

Project Control gives coding agents useful, source-grounded context about software. It helps them find implementations, understand relationships, inspect changes, locate tests and guidance, and retrieve recorded evidence.

An agent can request a symbol's implementation, callers, associated tests and recent changes together. Results identify their sources and explain missing or uncertain coverage. Retrieval and inspection need no intermediary model.

## Choose a tool

| You want to... | Use |
| --- | --- |
| Find a project and its entry points | `overview` |
| Understand current work and blockers | `frontier` |
| Inspect changes or recorded history | `delta` |
| Find text or resolve an exact entity | `search` |
| Gather implementation, tests, callers and guidance | `context` |
| Follow dependencies or affected dependents | `trace` |
| Retrieve outcomes and provenance | `evidence` |
| Check the development environment | `machine` |
| Read exact files and ranges remotely | Observer `read` |
| Explore files, Git state and build metadata remotely | Observer `inspect` |

Start with `overview()` to discover registered project IDs. Use literal text search for discovery or an exact typed target for lookup. Select the relationships you need with `context`. A partial result explains what is missing and how to continue; it does not necessarily mean access was denied.

## Context for each caller

**Observer / ChatGPT** receives coherent bundles with relevant source included. Its ten tools support remote exploration without shell access.

**Local / Codex** receives dense guidance, precise source locations and useful associations. Native filesystem and Git tools handle routine local inspection. Its twelve tools include Todo's `next_task`, `inspect_task`, `coordinate_task` and `finish_task`.

The **operator / mutator** profile adds `plan`, `amend_project` and `maintain_execution`, for fifteen tools. There is no compact/extended parameter: choose targets, relationships, revisions, ranges and limits; the profile handles presentation. Source retrieval works without Todo.

## Try the source

Use Python 3.11+ and `uv`, with a sibling checkout of [Project Foundations](https://github.com/tumlinso/project-foundations):

```sh
scripts/pc-dev setup --foundation-repo ../project-foundations
scripts/pc-dev run --help
```

Follow [development](docs/development.md) to register a disposable project and run a candidate on its own port and private state. Workflow profiles additionally need the verified standalone Todo package, supplied with `--todo-wheel` during setup. Ordinary retrieval needs no inference service.

## Applications and packages

- **Project Control** provides development context and inspection.
- **[Project-Lab](https://github.com/tumlinso/project-lab)** conducts scheduled experiments and preserves results.
- **[Project Foundations](https://github.com/tumlinso/project-foundations)** contains the independent source and evidence packages used by both applications.
- **[Device Scheduler](https://github.com/tumlinso/device-scheduler)** allocates resources and supervises physical execution.
- **Todo Orchestrator** owns plans, tasks, workflow, verification and acceptance.
- **cpp-context-compiler** supplies specialized C++ analysis and context.

Python structural retrieval and configured C++ exports supplement file, lexical and Git retrieval. Dynamic or ambiguous relationships stay labelled. Guidance declarations, dependencies, explicit links and incidental mentions remain distinct.

See [retrieval recipes](docs/retrieval.md) for context, guidance, C++ exports and peer evidence, [architecture](docs/architecture.md) for ownership, and [deployment](docs/deployment.md) for the candidate/live-service boundary. The [assistance archive](archive/assistance-v0/README.md) preserves the former investigation and inference implementation outside the installed application. Existing evidence and notes retain their original identities and attribution.
