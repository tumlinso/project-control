# 11. Manual bootstrap: install → preview → explicit import → verify

**Only the native `machine/*.todo-plan.json` is an apply input.** This procedure creates no implementation worktrees, claims or agents. Import may create active run metadata; that does not mean a worker is running. No separate guessed `activate` command is used.

## Install inert files

Extract the download into a staging directory. Run the top-level validation tool before copying anything. `tools/install_overlay.py` previews by default, requires explicit repository roots, and only copies the two new planning directories after `--apply --confirm INSTALL-WF2-PLANNING`. It rejects symlinks, conflicting files and non-Git roots. It never installs Codex settings or invokes Todo/Git mutation. Ordinary reviewed manual copying is also acceptable.

```sh
python3 -B tools/validate_bundle.py
python3 -B tools/install_overlay.py --project-control-root /actual/project-control --skills-root /actual/Skills
# Review the preview; only then repeat with --apply --confirm INSTALL-WF2-PLANNING.
```

Set PC/SK to the actual canonical registered authority roots, not an old worktree or guessed submodule. Set PCPY to the Python interpreter that imports the **installed configured Project Control release**, with the same release environment as its launcher. Python 3.11+ is required by the local package/companion. The script never installs dependencies.

```sh
export PC=/actual/project-control
export SK=/actual/Skills
export PCPY=/actual/project-control-release/bin/python
export WF2_EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/wf2-bootstrap.XXXXXX")"
```

Preserve all unrelated files and inspect staged changes. Commit only reviewed new package paths through your usual Git workflow; no branch/worktree creation is necessary for ingestion. If repository/global ignores omit JSON or evidence files, verify manifest coverage and add only the intended package files deliberately. Never blindly stage the whole tree.

## Validate the installed package and inspect the runtime

```sh
python3 -B "$PC/planning/workflow-foundation-v2/scripts/validate_package.py" --peer-package "$SK/planning/workflow-foundation-v2"
python3 -B "$SK/planning/workflow-foundation-v2/scripts/validate_package.py" --peer-package "$PC/planning/workflow-foundation-v2"
python3 -B "$PC/planning/workflow-foundation-v2/scripts/test_package.py"
python3 -B "$SK/planning/workflow-foundation-v2/scripts/test_package.py"
python3 -B "$PC/planning/workflow-foundation-v2/scripts/todo_bootstrap.py" inspect-runtime   --repo "$PC" --peer-repo "$SK" --runtime-python "$PCPY" --receipt "$WF2_EVIDENCE/runtime.json"
```

Review the runtime receipt: interpreter, module/tree hashes, signatures, configuration digest and release identity. It is a snapshot to review, not an automatically trusted claim. A random system Python may be missing the modules. Source/installed skew is a tooling blocker; do not fix it by bypassing the configured runtime.

The wrapper uses the source-verified current Python mutation API so it can apply the **previously reviewed preconditions**, rather than silently creating a fresh proposal at apply time. It rejects API/signature/runtime/configuration changes. B-stream implementation must retain compatibility while this old runtime drives the program. Future format changes require deliberate review of the bridge, not disabling checks.

## Review source and take a fresh preview

Inspect actual Git diffs and existing IDs/claims in both authorities. The recorded baseline commit must remain an ancestor. A later reviewed HEAD is permitted, but supplying a hash is the operator's assertion of review, not an automatic architecture audit.

```sh
export PC_REVIEW_HEAD="$(git -C "$PC" rev-parse HEAD)"
export SK_REVIEW_HEAD="$(git -C "$SK" rev-parse HEAD)"
export RUNTIME_SHA="$(sha256sum "$WF2_EVIDENCE/runtime.json" | cut -d' ' -f1)"
python3 -B "$PC/planning/workflow-foundation-v2/scripts/todo_bootstrap.py" preview   --repo "$PC" --peer-repo "$SK" --review-head "$PC_REVIEW_HEAD" --peer-review-head "$SK_REVIEW_HEAD"   --runtime-python "$PCPY" --runtime-receipt "$WF2_EVIDENCE/runtime.json" --runtime-review-sha256 "$RUNTIME_SHA"   --receipt "$WF2_EVIDENCE/pc-preview.json"
```

Default is refusal of dirty work. `--acknowledge-dirty` is available for explicitly reviewed dirty bytes, including installed uncommitted package files or generated projections; it binds the exact source snapshot and never cleans it. Runtime receipts must remain outside both repositories.

The preview runs full exact native validation/diff and checks all task additions, no updates, no unresolved warnings, correct UUID, coherent revision, and collisions across task/run/lane/checkpoint/interface/gate/lock/barrier/invariant namespaces. It fails if authoritative inventories are unavailable. The remote construction probes are not a substitute.

## Explicitly import PC once

Read the preview and verify its proposed additions. After deliberate approval, supply its digest and exact confirmation:

```sh
export PC_PREVIEW_SHA="$(sha256sum "$WF2_EVIDENCE/pc-preview.json" | cut -d' ' -f1)"
python3 -B "$PC/planning/workflow-foundation-v2/scripts/todo_bootstrap.py" apply   --repo "$PC" --peer-repo "$SK" --review-head "$PC_REVIEW_HEAD" --peer-review-head "$SK_REVIEW_HEAD"   --runtime-python "$PCPY" --runtime-receipt "$WF2_EVIDENCE/runtime.json" --runtime-review-sha256 "$RUNTIME_SHA"   --reviewed-preview "$WF2_EVIDENCE/pc-preview.json" --review-sha256 "$PC_PREVIEW_SHA"   --confirm APPLY-PC-WF2-RUN-V1 --receipt "$WF2_EVIDENCE/pc-import.json"
```

Preview validity is one hour; changed source/package/runtime/authority requires a new review, not a forced retry. An attempt marker is created before the one mutating call. An interruption means outcome may be unknown: inspect actual state and preserve records. Do not retry a consumed preview or automatically roll back the first successful import.

## Verify PC, then preview/import/verify Skills

Plan import may update generated tracked projections. Reinspect/preserve them and use `--acknowledge-dirty` only after review. Verification calls the native validator/diff again and requires a no-op, plus exact task fields, all expected entities and lane roles/modes/queue order. It requires initial tasks still planned; do not dispatch between import and this check.

```sh
python3 -B "$PC/planning/workflow-foundation-v2/scripts/todo_bootstrap.py" verify   --repo "$PC" --peer-repo "$SK" --review-head "$PC_REVIEW_HEAD" --peer-review-head "$SK_REVIEW_HEAD"   --runtime-python "$PCPY" --runtime-receipt "$WF2_EVIDENCE/runtime.json" --runtime-review-sha256 "$RUNTIME_SHA"   --acknowledge-dirty --receipt "$WF2_EVIDENCE/pc-verified.json"
```

Now take a **fresh** Skills preview using its copy of the wrapper, `--repo "$SK" --peer-repo "$PC"`, swapped reviewed HEAD arguments, and `sk-preview.json`. After reviewing that exact preview, use `--confirm APPLY-SK-WF2-RUN-V1` and `sk-import.json`; then `verify` to `sk-verified.json`. Add `--acknowledge-dirty` consistently only for reviewed dirty state. Do not reuse the PC preview or assume two imports are atomic. Both complete sequences are also available as printable commands in `BOOTSTRAP_COMMANDS.md` at the top of the bundle.

An exact repeat of an already installed namespace is not a new bootstrap. Use verify; do not reset done tasks or overwrite live progress by reapplying this starting plan. Later profile promotion is a separate supported administrative change.

## Stop before execution

Install and smoke-test the separately reviewed Codex companion as described there. This is not an MCP authority mutation. Keep its selected live configuration paths and credentials out of the package. When both imports are verified and you deliberately choose to execute, launch one controller with `handoff/START_CONTROLLER.md` from Project Control. No supplied bootstrap script launches it.

During execution, configure `WF2_BINDINGS` from `machine/execution_bindings.example.json`, using actual interpreters, canonical roots, external evidence root and peer receipt locations. Acceptance files and review records are future implementation work, not prefilled passes.
