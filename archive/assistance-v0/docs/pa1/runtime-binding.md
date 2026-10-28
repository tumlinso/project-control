# PA1 local runtime binding

Project Control owns one canonical `local_worker.*` namespace in
`src/project_control/local_runtime/`. Source-mode inspection reads
`receiver-manifest.json` and validates its full file set and SHA256 values
without importing runtime modules. `bind_local_runtime()` repeats that check,
rejects a preloaded namespace, and installs a finder for the complete
`local_worker.*` namespace. Its loader hashes each source file immediately
before compiling the raw bytes, so it does not read or write timestamp-based
`.pyc` files. A later source or manifest change in the same process requires a
restart. Supervisor children start through
`python -m project_control.runtime_binding local_worker.supervisor ...` so
they use the same verified loader.

Installed packages require a schema-2 release manifest whose digest is
provided by trusted startup configuration. The manifest must pin
`local_runtime_binding.path` to `project_control/local_runtime` and include the
receiver manifest digest and canonical file-map fingerprint. An installed
package without that binding fails closed; it cannot fall back to the old
Skills supplier path. The already-running production service remains on its
existing code and verified release until a candidate passes qualification and
the reversible promotion is authorized.

The receiver manifest is refreshed only through the explicit
`scripts/refresh_local_runtime_manifest.py` command. It requires source-root
and source-commit provenance, prints a dry run by default, and writes only
with `--write`. Candidate installation validates the package-owned manifest
and copies its digest/fingerprint into the release manifest. Runtime import or
status checks never refresh or bless source files.

This binding establishes source and import identity. It does not qualify a
model, start the supervisor, or establish GPU readiness.
