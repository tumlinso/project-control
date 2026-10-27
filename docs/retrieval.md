# Find enough context without learning the internals

## Start broad, then select a subject

Use `overview()` to find registered project IDs and repositories. `overview(project="demo")` introduces that project; `frontier(project="demo")` focuses on current work. `search(project="demo", query="parser")` performs lexical discovery. Exact lookup uses a typed query such as `{"kind":"symbol","target":"parse"}`.

For a context bundle, use explicit targets and relationships:

```json
{
  "project": "demo",
  "targets": [{"kind": "path", "repository": "main", "target": "src/parser.py"}],
  "include": ["implementation", "callers", "tests", "changes"]
}
```

Pass that object to `context`. An exact symbol target can use `kind: symbol` with an identifier returned by `search`; ambiguity returns candidates. Revisions and ranges select source, rather than a presentation mode. Observer responses include relevant source; Local responses emphasize locations and associations.

## Declare guidance across repositories

A source repository may contain `.project-context.json`:

```json
{"guidance": [{"target": "guides:setup.md", "name": "Build instructions"}]}
```

Here `guides` is another registered repository. A qualified `other-project/repository:path` reference names a separately registered project. The calling profile must be permitted to read that project. Context expands declared guidance within the requested budget and retains the declaration and target source identities. Incidental prose and lexical matches do not acquire the meaning of guidance declarations.

Existing Project Control metadata declarations and orientation remain application-owned project knowledge. They do not require packaging guidance as a skill.

## Use an existing C++ index

cpp-context-compiler keeps its own source analysis. Prepare an index explicitly, then export it without refresh:

```sh
ctxpp --root /path/to/source --state-dir /private/ctxpp-state scan
ctxpp --root /path/to/source --state-dir /private/ctxpp-state --read-only --json export > /private/ctxpp-export.json
```

Register that export at server startup:

```sh
export PROJECT_CONTROL_CPP_EXPORTS='{"demo/main":"/private/ctxpp-export.json"}'
```

Keys are registered `project/repository` pairs. Project Control reads the export through its normal source provider. Retrieval does not run a compiler, initialize ctxpp, refresh an index or rewrite source. Missing/stale or partial structural coverage stays explicit; canonical files and Git history remain readable.

## Read evidence from another application

A separately configured neutral evidence store can expose Project-Lab observations:

```sh
export PROJECT_CONTROL_EVIDENCE_STORES='{"demo":[{"database":"/private/lab-state/evidence.sqlite3","artifact_dir":"/private/lab-state/evidence-artifacts"}]}'
```

The reader opens existing stores read-only. It preserves the owning application, source references and artifact identities. Todo outcomes remain Todo-owned; assessments retain their author and criteria. An unavailable store produces partial coverage with a next action, not an empty claim of success.

## Continue a partial result

Follow the returned structured continuation with its original targets and revision. Source continuations bind to exact bytes; a changed source requires a fresh query. Check omissions to distinguish a result limit, missing provider, ambiguity or unavailable owner. A mention of a test is a candidate association, not evidence that a test ran.
