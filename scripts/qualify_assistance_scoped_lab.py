#!/usr/bin/env python3
"""Bounded installed-CLI qualification of one explicitly authorized LAB scope.

Default execution only prints the proposed command sequence. Live execution is
opt-in, records every command/result in a new private directory, and never
replays an ambiguous run. The receipt describes mechanical evidence; deciding
whether a model's experiment answers the scientific question remains explicit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

try:
    from qualification_runtime import (
        QualificationRuntimeError, clear_source_pins,
        runtime_provenance,
    )
except ModuleNotFoundError:
    from scripts.qualification_runtime import (  # type: ignore[no-redef]
        QualificationRuntimeError, clear_source_pins,
        runtime_provenance,
    )


SESSION_ID = re.compile(r"lab_session_[0-9a-f]{32}")


def command_plan(args: argparse.Namespace) -> list[list[str]]:
    prefix = getattr(args, "command_prefix", None) or [str(args.project_control)]
    preview = [*prefix, "assistance", "lab", "preview", "--project", args.project,
               "--goal", args.goal, "--wall-seconds", str(args.wall_seconds),
               "--max-experiments", str(args.max_experiments)]
    for source in args.source:
        preview.extend(("--source", source))
    for tool in args.tool:
        preview.extend(("--tool", tool))
    for device in args.gpu_uuid:
        preview.extend(("--gpu-uuid", device))
    if args.toolchain_root:
        preview.extend(("--toolchain-root", str(args.toolchain_root)))
    return [preview, [*prefix, "assistance", "lab", "authorize", "<session-id>"],
            [*prefix, "assistance", "lab", "run", "--scope-id", "<session-id>"],
            [*prefix, "assistance", "lab", "status", "--scope-id", "<session-id>"]]


def _write(root: Path, name: str, value: Any) -> None:
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _invoke(argv: list[str], *, timeout: float, root: Path, label: str,
            env: dict[str, str] | None = None) -> dict[str, Any]:
    # Persist the request before any CLI operation, including authorization.
    _write(root, label + "-intent.json", {"argv": argv, "timeout_seconds": timeout,
                                       "created_at": time.time()})
    started = time.monotonic()
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired as error:
        _write(root, label + "-result.json", {"state": "timeout_ambiguous",
                                             "elapsed_seconds": time.monotonic() - started})
        raise RuntimeError("installed_cli_timeout_ambiguous") from error
    record = {"returncode": result.returncode, "stdout": result.stdout,
              "stderr": result.stderr, "elapsed_seconds": time.monotonic() - started}
    _write(root, label + "-result.json", record)
    if result.returncode:
        raise RuntimeError("installed_cli_failed:" + label)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("installed_cli_returned_invalid_json:" + label) from error
    if not isinstance(value, dict):
        raise RuntimeError("installed_cli_returned_non_object:" + label)
    return value


def assess_status(status: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Assess exact durable effect records, rather than model-authored prose."""
    matches = [item for item in status.get("sessions", [])
               if isinstance(item, dict) and item.get("session_id") == session_id]
    if len(matches) != 1:
        raise RuntimeError("qualified_session_status_missing_or_ambiguous")
    session = matches[0]
    effects = session.get("effects", [])
    if not isinstance(effects, list):
        raise RuntimeError("qualified_session_effects_invalid")
    verified = bool(effects) and all(
        isinstance(effect, dict) and effect.get("state") == "completed"
        and isinstance(effect.get("receipt"), dict)
        and effect["receipt"].get("cleanup_verified") is True for effect in effects)
    return {"session_id": session_id, "state": session.get("state"),
            "effect_count": len(effects), "effects_cleanup_verified": verified,
            "mechanical_trial_passed": verified and session.get("state") in
            {"completed", "budget_exhausted"},
            "answer_quality_accepted": False,
            "limit": "Model usefulness and the claimed experimental conclusion require receipt review."}


def _runtime_stamp(value: dict[str, Any]) -> dict[str, Any]:
    stamp: dict[str, Any] = {"runtime": value.get("runtime"),
                             "interpreter": value.get("interpreter"),
                             "cli_command": value.get("cli_command")}
    for name in ("project_control", "todo", "receiver"):
        identity = value.get(name)
        if isinstance(identity, dict):
            stamp[name] = {key: identity.get(key) for key in
                           ("path", "root", "fingerprint", "file_count") if key in identity}
    release = value.get("release")
    if isinstance(release, dict):
        stamp["release"] = {key: release.get(key) for key in ("root", "manifest_sha256")}
    demand = value.get("demand_runtime")
    if isinstance(demand, dict):
        stamp["demand_runtime"] = {key: demand.get(key) for key in
                                   ("mode", "source_root", "project_control_fingerprint",
                                    "receiver_fingerprint",
                                    "todo_runtime_fingerprint", "skills_root")}
    return stamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=("release", "source"), default="release",
                        help="qualify a digest-pinned release or the current source checkout")
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument("--dry-run", action="store_true",
                           help="print selected identity and command plan without service calls")
    parser.add_argument("--project-control", type=Path,
                        help="explicit release CLI; source mode always uses scripts/pc-dev")
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--gpu-uuid", action="append", default=[])
    parser.add_argument("--toolchain-root", type=Path)
    parser.add_argument("--wall-seconds", type=int, default=600)
    parser.add_argument("--max-experiments", type=int, default=2)
    parser.add_argument("--artifact-root", type=Path)
    execution.add_argument("--execute-live", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.wall_seconds <= 600 or not 1 <= args.max_experiments <= 6:
        parser.error("qualification budgets must fit the LAB scope ceilings")
    if bool(args.gpu_uuid) != bool(args.toolchain_root):
        parser.error("GPU qualification requires explicit UUIDs and a toolchain")
    source_checkout = Path(__file__).resolve().parents[1]
    try:
        if args.runtime == "source":
            clear_source_pins()
            args.project_control = source_checkout / "scripts" / "pc-dev"
            args.command_prefix = [str(args.project_control), "run"]
            provenance = runtime_provenance("source", repository_root=source_checkout)
        else:
            provenance = runtime_provenance("release", repository_root=source_checkout,
                                            release_cli=args.project_control)
            args.project_control = Path(provenance["cli_command"][0])
            args.command_prefix = [str(args.project_control)]
    except (QualificationRuntimeError, OSError, ValueError) as error:
        parser.error(str(error))
    plan = command_plan(args)
    if not args.execute_live:
        print(json.dumps({"state": "dry_run", "commands": plan,
                          "runtime_provenance": provenance,
                          "inference_started": False, "gpu_work_started": False}, indent=2))
        return 0
    if args.artifact_root is None or not args.artifact_root.is_absolute():
        parser.error("live qualification requires a new absolute --artifact-root")
    if not args.project_control.is_absolute() or not args.project_control.is_file():
        parser.error("live qualification requires a valid source launcher or installed CLI path")
    root = args.artifact_root
    if root.resolve().is_relative_to(source_checkout) or root.exists() or root.is_symlink():
        parser.error("artifact root must be new and outside the source checkout")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    _write(root, "qualification-intent.json", {"commands": plan,
        "wall_seconds": args.wall_seconds, "max_experiments": args.max_experiments,
        "cli_path": str(args.project_control), "cli_resolved": str(args.project_control.resolve()),
        "cli_sha256": hashlib.sha256(args.project_control.read_bytes()).hexdigest(),
        "runtime_provenance": provenance,
        "runtime_identity_before": _runtime_stamp(provenance),
        "created_at": time.time(), "automatic_effect_replay": False})
    child_env = dict(os.environ)
    if args.runtime == "source":
        clear_source_pins(child_env)

    runtime_identity_after: dict[str, Any] | None = None

    def assert_runtime_unchanged() -> None:
        nonlocal runtime_identity_after
        observed = runtime_provenance(
            args.runtime, repository_root=source_checkout,
            release_cli=args.project_control if args.runtime == "release" else None)
        runtime_identity_after = _runtime_stamp(observed)
        if runtime_identity_after != _runtime_stamp(provenance):
            raise RuntimeError("qualification_runtime_changed")

    def invoke(argv: list[str], *, timeout: float, label: str) -> dict[str, Any]:
        assert_runtime_unchanged()
        value = _invoke(argv, timeout=timeout, root=root, label=label, env=child_env)
        assert_runtime_unchanged()
        return value
    session_id: str | None = None
    try:
        preview = invoke(plan[0], timeout=30, label="preview")
        session_id = preview.get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            raise RuntimeError("installed_cli_preview_session_id_invalid")
        if preview.get("authorized") is not False or preview.get("inference_started") is not False:
            raise RuntimeError("installed_cli_preview_not_cold")
        for command in plan[1:]:
            command[:] = [session_id if item == "<session-id>" else item for item in command]
        authorized = invoke(plan[1], timeout=15, label="authorize")
        if authorized.get("session_id") != session_id or authorized.get("state") != "authorized":
            raise RuntimeError("installed_cli_authorization_invalid")
        deadline = float(authorized["deadline"])
        invoke(plan[2], timeout=max(1, deadline - time.time()) + 15, label="run")
        status = invoke(plan[3], timeout=15, label="status")
        receipt = assess_status(status, session_id)
        receipt["runtime_identity_before"] = _runtime_stamp(provenance)
        receipt["runtime_identity_after"] = runtime_identity_after
        receipt["runtime_identity_stable"] = (
            runtime_identity_after == _runtime_stamp(provenance))
        if not receipt["runtime_identity_stable"]:
            receipt["mechanical_trial_passed"] = False
            receipt["reason"] = "qualification_runtime_changed"
        _write(root, "qualification-receipt.json", receipt)
        print(json.dumps({**receipt, "artifact_root": str(root)}, sort_keys=True))
        return 0 if receipt["mechanical_trial_passed"] else 1
    except (OSError, RuntimeError, KeyError, ValueError) as error:
        # Cancellation revokes future work. It is deliberately not represented
        # as proof that an ambiguous already-started effect has disappeared.
        cancellation: Any = None
        if session_id is not None:
            try:
                cancel_argv = [*args.command_prefix, "assistance", "lab", "cancel", session_id]
                cancellation = _invoke(cancel_argv, timeout=15, root=root, label="cancel", env=child_env)
                try:
                    assert_runtime_unchanged()
                except RuntimeError as identity_error:
                    cancellation = {"response": cancellation,
                                    "runtime_identity_failure": str(identity_error)}
            except (OSError, RuntimeError) as cancel_error:
                cancellation = {"error": str(cancel_error)}
        receipt = {"state": "failed", "reason": str(error), "session_id": session_id,
                   "cancellation": cancellation, "cleanup_verified": False,
                   "automatic_effect_replay": False, "artifact_root": str(root),
                   "runtime_identity_before": _runtime_stamp(provenance),
                   "runtime_identity_after": runtime_identity_after,
                   "runtime_identity_stable": (
                       runtime_identity_after == _runtime_stamp(provenance))}
        _write(root, "qualification-receipt.json", receipt)
        print(json.dumps(receipt, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
