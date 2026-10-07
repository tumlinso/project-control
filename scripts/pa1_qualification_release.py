#!/usr/bin/env python3
"""Release only the owned AS1 sessions recorded by one PA1 qualification store.

The default mode validates the qualification artifact and prints a plan. An
explicit ``--execute-release`` is required to recompose its isolated JobService
and call the trusted owned-resource release API. No pool or GPU maintenance API
is used here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _import_root in (REPO, REPO / "src"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from project_control.assistance.power import trusted_operator_control  # noqa: E402


class ReleaseError(ValueError):
    """Invalid qualification artifact, state root, or release evidence."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_dir(path: Path, *, must_exist: bool = True) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ReleaseError("private directory must be an absolute non-symlink path")
    if path != path.resolve(strict=must_exist):
        raise ReleaseError("private directory path must be canonical")
    if not path.exists():
        if must_exist:
            raise ReleaseError("private directory is unavailable")
        path.mkdir(parents=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ReleaseError("private directory must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ReleaseError("private directory must not grant group or other access")
    return path.resolve(strict=True)


def _load_artifact(artifact_root: Path, as1_state_root: Path,
                   supervisor_state_root: Path) -> tuple[Path, dict[str, Any], bytes]:
    root = _private_dir(artifact_root)
    state = _private_dir(as1_state_root)
    supervisor = _private_dir(supervisor_state_root)
    if not state.is_relative_to(root) or state == root:
        raise ReleaseError("AS1 state root must be contained by the private qualification artifact root")
    if (root / "release-report.json").exists() or (root / "release-report.json").is_symlink():
        raise ReleaseError("release-report.json already exists; preserve it and use a new artifact copy")
    report_path = root / "report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ReleaseError("qualification report.json is unavailable")
    report_bytes = report_path.read_bytes()
    try:
        report = json.loads(report_bytes, object_pairs_hook=_reject_duplicate_keys,
                            parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseError("qualification report is malformed") from error
    if not isinstance(report, dict) or report.get("format") not in {
            "pa1-live-source-qualification/1", "pa1-assistance-economics/1"}:
        raise ReleaseError("qualification report format is unsupported")
    report_state = report.get("isolated_state_root")
    if not isinstance(report_state, str) or Path(report_state).resolve(strict=False) != state:
        raise ReleaseError("AS1 state root differs from the one recorded in the qualification report")
    cleanup = report.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("central_supervisor_stopped") is not False:
        raise ReleaseError("qualification artifact does not confirm that the central supervisor was left running")
    if report.get("format") == "pa1-live-source-qualification/1":
        stopped = cleanup.get("isolated_job_service_stopped")
    else:
        stopped = cleanup.get("isolated_job_service_stopped")
    if stopped is not True:
        raise ReleaseError("qualification artifact does not confirm isolated JobService shutdown")
    observed_supervisor = (report.get("runtime", {}).get("service_state_root")
                           if isinstance(report.get("runtime"), dict)
                           else report.get("runtime", {}).get("supervisor_state_root")
                           if isinstance(report.get("runtime"), dict) else None)
    if observed_supervisor is not None and Path(str(observed_supervisor)).resolve(strict=False) != supervisor:
        raise ReleaseError("canonical supervisor state root differs from the qualification report")
    return root, report, report_bytes


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ReleaseError(f"non-finite JSON number is forbidden: {value}")


def _write_private(path: Path, value: dict[str, Any]) -> None:
    raw = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _release_snapshot(jobs) -> dict[str, Any]:
    """Read only the JobService's persisted release state and owner proofs."""
    with jobs._db() as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        power = (db.execute("""SELECT release_request_id,release_veto,physical_state,
            release_verified_at,release_verified_sessions,release_verified_proof_digest,
            release_verified_targets_digest FROM pa1_power_state WHERE singleton=1""").fetchone()
                 if "pa1_power_state" in tables else None)
        rows = (db.execute("""SELECT session_id,state,release_request_id,release_proof
            FROM pa1_owned_resource_sessions WHERE state<>'superseded'
            ORDER BY session_id""").fetchall()
                if "pa1_owned_resource_sessions" in tables else [])
    state = dict(power) if power is not None else {}
    sessions = []
    proofs = []
    for row in rows:
        item = dict(row)
        proof_text = item.pop("release_proof", None)
        proof = json.loads(proof_text) if proof_text else None
        item["proof"] = proof
        sessions.append(item)
        if proof is not None:
            proofs.append(proof)
    request_id = state.get("release_request_id")
    targets = []
    for proof in proofs:
        targets.append({
            "target_id": proof["session_id"],
            "session_id": proof["session_id"],
            "daemon_epoch": proof["daemon_epoch"],
            "slot_id": proof["slot_id"],
            "gpu_uuids": sorted(proof["gpu_uuids"]),
            "state": "released_verified",
        })
    target_uuids = [gpu for target in targets for gpu in target["gpu_uuids"]]
    gpu_compatible = (len(targets) == 2 and all(len(item["gpu_uuids"]) == 2 for item in targets)
                      and len(target_uuids) == 4 and len(set(target_uuids)) == 4)
    proof_digest = _sha256(_json_bytes({"request_id": request_id, "proofs": proofs})) if proofs else None
    return {
        **state,
        "sessions": sessions,
        "proofs": proofs,
        "proof_digest": proof_digest,
        "gpu_release_targets": targets if gpu_compatible else None,
        "gpu_release_targets_ready": gpu_compatible,
    }


def _atomic_persist_release(root: Path, payload: dict[str, Any]) -> Path:
    path = root / "release-report.json"
    if path.exists() or path.is_symlink():
        raise ReleaseError("release-report.json already exists; preserve it and use a new artifact copy")
    _write_private(path, payload)
    return path


def _current_supervisor_identity(backend, expected_state_root: Path,
                                 expected_runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    status = backend.central_status(deadline_epoch=time.time() + 5)
    observed = status.get("service_state_root")
    if not isinstance(observed, str) or Path(observed).resolve(strict=False) != expected_state_root:
        raise ReleaseError("live supervisor does not match the explicit canonical state root")
    expected_runtime = expected_runtime or {}
    expected_fingerprint = expected_runtime.get("supervisor_runtime_fingerprint")
    expected_source = expected_runtime.get("supervisor_source_sha256")
    if expected_fingerprint and status.get("runtime_fingerprint") != expected_fingerprint:
        raise ReleaseError("live supervisor runtime fingerprint differs from the qualification observation")
    if expected_source and status.get("source_sha256") != expected_source:
        raise ReleaseError("live supervisor source hash differs from the qualification observation")
    return {
        "service_state_root": str(expected_state_root),
        "supervisor_pid": status.get("supervisor_pid"),
        "runtime_fingerprint": status.get("runtime_fingerprint"),
        "source_sha256": status.get("source_sha256"),
        "daemon_epoch": status.get("daemon_epoch"),
    }


def run_release(*, artifact_root: Path, as1_state_root: Path,
                supervisor_state_root: Path, max_retries: int = 2) -> dict[str, Any]:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 3:
        raise ReleaseError("max_retries must be in 0..3")
    root, qualification, report_bytes = _load_artifact(artifact_root, as1_state_root,
                                                        supervisor_state_root)
    # Reuse the exact composition seams that produced the qualification store.
    from scripts.qualify_assistance_live import _compose_isolated_surface, _isolated_composition

    composition = None
    release_result: str | None = None
    attempt_results = []
    supervisor_identity: dict[str, Any] = {}
    failure: dict[str, str] | None = None
    close_failure: dict[str, str] | None = None
    try:
        _identity, runtime, backend = _isolated_composition(as1_state_root, supervisor_state_root)
        expected_runtime = qualification.get("runtime")
        supervisor_identity = _current_supervisor_identity(
            backend, supervisor_state_root,
            expected_runtime if isinstance(expected_runtime, dict) else None)
        runtime_root = Path(str(getattr(backend, "_state_root", supervisor_state_root))).resolve(strict=False)
        if runtime_root != supervisor_state_root:
            raise ReleaseError("recomposed backend is not bound to the canonical supervisor state root")
        composition = _compose_isolated_surface(as1_state_root, runtime, backend)
        control = trusted_operator_control()
        release_result = composition.jobs.release_owned_resources(
            control, reason="pa1-qualification-owned-release")
        first = _release_snapshot(composition.jobs)
        request_id = first.get("release_request_id")
        attempt_results.append({"attempt": 0, "result": release_result,
                                "request_id": request_id,
                                "physical_state": first.get("physical_state")})
        latest = first
        pending = release_result != "released_verified"
        for index in range(1, max_retries + 1):
            if not pending:
                break
            # PowerPolicy reuses its current unexpired release request, so this
            # public JobService operation retries the same committed identity.
            retry_result = composition.jobs.release_owned_resources(
                control, reason="pa1-qualification-owned-release")
            latest = _release_snapshot(composition.jobs)
            if latest.get("release_request_id") != request_id:
                raise ReleaseError("bounded retry changed the committed release request identity")
            release_result = retry_result or release_result
            attempt_results.append({"attempt": index, "result": retry_result,
                                    "request_id": latest.get("release_request_id"),
                                    "physical_state": latest.get("physical_state")})
            pending = retry_result != "released_verified"
        if latest.get("physical_state") != "released_verified":
            failure = {"class": "OwnedReleasePending",
                       "reason": "owner proof did not verify every tracked session"}
        else:
            latest = _release_snapshot(composition.jobs)
    except Exception as error:
        failure = {"class": type(error).__name__, "reason": str(error)[:300]}
        latest = _release_snapshot(composition.jobs) if composition is not None else {}
    finally:
        try:
            close_ok = composition.close() if composition is not None else None
        except Exception as error:
            close_ok = False
            close_failure = {"class": type(error).__name__, "reason": str(error)[:300]}

    payload = {
        "format": "PC-PA1-QUALIFICATION-OWNED-RELEASE/1",
        "status": "released_verified" if latest.get("physical_state") == "released_verified" else "pending_or_failed",
        "artifact_root": str(root),
        "qualification_report_sha256": _sha256(report_bytes),
        "qualification_format": qualification.get("format"),
        "qualification_status": qualification.get("status"),
        "as1_state_root": str(Path(as1_state_root).resolve(strict=True)),
        "supervisor": supervisor_identity,
        "release_result": release_result,
        "attempts": attempt_results,
        "release_request_id": latest.get("release_request_id"),
        "release_veto": latest.get("release_veto"),
        "physical_state": latest.get("physical_state"),
        "release_verified_at": latest.get("release_verified_at"),
        "release_verified_sessions": latest.get("release_verified_sessions"),
        "release_verified_proof_digest": latest.get("release_verified_proof_digest"),
        "release_verified_targets_digest": latest.get("release_verified_targets_digest"),
        "proof_digest": latest.get("proof_digest"),
        "sessions": latest.get("sessions", []),
        "proofs": latest.get("proofs", []),
        "gpu_release_targets_ready": latest.get("gpu_release_targets_ready", False),
        "gpu_release_targets": latest.get("gpu_release_targets"),
        "dispatcher_closed": close_ok,
        "close_failure": close_failure,
        "failure": failure,
        "completed_unix": time.time(),
    }
    path = _atomic_persist_release(root, payload)
    payload["release_report_path"] = str(path)
    payload["release_report_sha256"] = _sha256(path.read_bytes())
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--as1-state-root", type=Path, required=True)
    parser.add_argument("--supervisor-state-root", type=Path, required=True,
                        help="explicit canonical supervisor service-state root")
    parser.add_argument("--max-retries", type=int, default=2,
                        help="bounded retries of the existing committed release request (0..3)")
    parser.add_argument("--execute-release", action="store_true",
                        help="explicitly call JobService.release_owned_resources")
    args = parser.parse_args(argv)
    try:
        if isinstance(args.max_retries, bool) or not 0 <= args.max_retries <= 3:
            raise ReleaseError("max_retries must be in 0..3")
        root, report, report_bytes = _load_artifact(args.artifact_root, args.as1_state_root,
                                                     args.supervisor_state_root)
        if not args.execute_release:
            plan = {
                "status": "planned",
                "artifact_root": str(root),
                "qualification_format": report.get("format"),
                "qualification_status": report.get("status"),
                "qualification_report_sha256": _sha256(report_bytes),
                "as1_state_root": str(Path(args.as1_state_root).resolve(strict=True)),
                "supervisor_state_root": str(Path(args.supervisor_state_root).resolve(strict=True)),
                "execute_flag_required": "--execute-release",
                "max_retries": args.max_retries,
            }
            print(json.dumps(plan, sort_keys=True, allow_nan=False))
            return 0
        result = run_release(artifact_root=args.artifact_root,
                             as1_state_root=args.as1_state_root,
                             supervisor_state_root=args.supervisor_state_root,
                             max_retries=args.max_retries)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["status"] == "released_verified" else 2
    except Exception as error:
        print(json.dumps({"status": "refused", "error": f"{type(error).__name__}: {error}"},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
