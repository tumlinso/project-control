#!/usr/bin/env python3
"""Issue a short-lived, inert GPU request from fresh release and quiescence proof.

This helper never starts CUDA work. Its default mode validates and plans; only
``--issue`` writes the private request and release marker consumed by
``pa1_gpu_experiment.py``. A JobService release report alone is insufficient:
the caller must also provide target-bound, current root evidence for process,
native host-lease, and supervisor-slot absence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for _import_root in (REPO, REPO / "src"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from project_control.assistance.lab import _verify_snapshot  # noqa: E402
from scripts import pa1_gpu_experiment as gpu  # noqa: E402


RELEASE_REPORT_FORMAT = "PC-PA1-QUALIFICATION-OWNED-RELEASE/1"
QUIESCENCE_FORMAT = "PC-PA1-GPU-QUIESCENCE-EVIDENCE/1"
HANDOFF_FORMAT = "PC-PA1-GPU-HANDOFF/1"
GPU_ID = re.compile(r"GPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")


class HandoffError(ValueError):
    """Release, source, or current quiescence evidence is not sufficient."""


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise HandoffError(f"non-finite JSON number is forbidden: {value}")


def _read_private_json(path: Path) -> tuple[dict[str, Any], bytes]:
    if not path.is_absolute() or path.is_symlink() or path != path.resolve(strict=True):
        raise HandoffError("private JSON input path is invalid")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise HandoffError("JSON evidence must be a user-owned mode-0600 regular file")
    raw = path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandoffError("JSON evidence is malformed") from error
    if not isinstance(value, dict):
        raise HandoffError("JSON evidence must be an object")
    return value, raw


def _private_dir(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or path != path.resolve(strict=True):
        raise HandoffError("private directory path is invalid")
    info = path.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise HandoffError("directory must be user-owned and mode 0700")
    return path


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not HEX_64.fullmatch(value):
        raise HandoffError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _gpu(value: object) -> str:
    if not isinstance(value, str) or not GPU_ID.fullmatch(value):
        raise HandoffError("release proof must contain full NVIDIA GPU UUIDs")
    return "GPU-" + value[4:].lower()


def _canonical_json_digest(value: Any, *, newline: bool = True) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw + (b"\n" if newline else b"")).hexdigest()


def _safe_token(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or "\x00" in value:
        raise HandoffError(f"release proof {field} is invalid")
    return value


def _release_targets(report: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if (report.get("format") != RELEASE_REPORT_FORMAT or report.get("status") != "released_verified"
            or report.get("release_result") != "released_verified"
            or report.get("physical_state") != "released_verified"
            or report.get("dispatcher_closed") is not True):
        raise HandoffError("owned-release report is not freshly verified and closed")
    request_id = _safe_token(report.get("release_request_id"), "release_request_id")
    proof_digest = _digest(report.get("proof_digest"), "proof_digest")
    _digest(report.get("release_verified_proof_digest"), "release_verified_proof_digest")
    if report.get("release_verified_sessions") != 2:
        raise HandoffError("owned-release report must verify exactly two sessions")
    proofs = report.get("proofs")
    rows = report.get("sessions")
    if not isinstance(proofs, list) or len(proofs) != 2 or not isinstance(rows, list) or len(rows) != 2:
        raise HandoffError("owned-release report must contain exactly two owner proofs and session rows")
    if _canonical_json_digest({"request_id": request_id, "proofs": proofs}) != proof_digest:
        raise HandoffError("owner proof digest does not match the release report")

    targets = []
    proof_by_session = {}
    for proof in proofs:
        if not isinstance(proof, dict) or proof.get("format") != "PC-PA1-OWNED-RELEASE-PROOF/1":
            raise HandoffError("owned-release proof format is invalid")
        if proof.get("request_id") != request_id:
            raise HandoffError("owner proof belongs to a different release request")
        session = _safe_token(proof.get("session_id"), "session_id")
        if session in proof_by_session:
            raise HandoffError("owned-release proof session IDs are duplicated")
        for field in ("process_released", "memory_released", "host_released"):
            if proof.get(field) is not True:
                raise HandoffError("owned-release proof is missing process, memory, or host release")
        daemon = _safe_token(proof.get("daemon_epoch"), "daemon_epoch")
        slot = _safe_token(proof.get("slot_id"), "slot_id")
        owner = _safe_token(proof.get("owner_id"), "owner_id")
        if _safe_token(proof.get("host_lease_id"), "host_lease_id") != owner:
            raise HandoffError("owned-release proof owner and native host lease IDs differ")
        _safe_token(proof.get("residency_generation"), "residency_generation")
        pid = proof.get("server_pid")
        start = _safe_token(proof.get("server_process_start"), "server_process_start")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
            raise HandoffError("owned-release proof server PID is invalid")
        raw_gpus = proof.get("gpu_uuids")
        if not isinstance(raw_gpus, list) or len(raw_gpus) != 2:
            raise HandoffError("each released target must own exactly two GPUs")
        gpus = sorted(_gpu(value) for value in raw_gpus)
        if len(set(gpus)) != 2:
            raise HandoffError("released target GPU UUIDs must be unique")
        observation = proof.get("observation")
        if not isinstance(observation, dict):
            raise HandoffError("owner proof lacks host cleanup observation")
        observed_at = observation.get("observed_unix")
        processes = observation.get("processes")
        if (isinstance(observed_at, bool) or not isinstance(observed_at, (int, float))
                or not isinstance(processes, list)
                or any(isinstance(row, dict) and row.get("pid") == pid for row in processes)):
            raise HandoffError("owner cleanup observation does not prove target PID absence")
        proof_by_session[session] = proof
        targets.append({"target_id": session, "session_id": session,
                        "daemon_epoch": daemon, "slot_id": slot, "gpu_uuids": gpus,
                        "state": "released_verified", "owner_id": owner,
                        "server_pid": pid, "server_process_start": start})
    targets.sort(key=lambda item: item["target_id"])
    flattened = [gpu_id for target in targets for gpu_id in target["gpu_uuids"]]
    if len(flattened) != 4 or len(set(flattened)) != 4:
        raise HandoffError("two release targets must cover exactly four distinct GPU UUIDs")

    row_by_session = {}
    for row in rows:
        if not isinstance(row, dict):
            raise HandoffError("owned-release session row is invalid")
        session = row.get("session_id")
        if session in row_by_session:
            raise HandoffError("owned-release session rows are duplicated")
        if (row.get("state") != "released_verified" or row.get("release_request_id") != request_id
                or row.get("proof") != proof_by_session.get(session)):
            raise HandoffError("owned-release session row does not match its committed proof")
        row_by_session[session] = row
    if set(row_by_session) != set(proof_by_session):
        raise HandoffError("owned-release proof and session sets differ")

    reported_targets = report.get("gpu_release_targets")
    if report.get("gpu_release_targets_ready") is not True or not isinstance(reported_targets, list):
        raise HandoffError("owned-release report does not expose its exact two target pairs")
    marker_targets = [{key: target[key] for key in
                       ("target_id", "session_id", "daemon_epoch", "slot_id", "gpu_uuids", "state")}
                      for target in targets]
    if reported_targets != marker_targets:
        raise HandoffError("reported GPU targets differ from the exact owner proofs")
    return marker_targets, proof_digest


def _validate_quiescence(evidence: dict[str, Any], report: dict[str, Any],
                         targets: list[dict[str, Any]], proof_digest: str,
                         canonical_host_root: Path, now: float) -> None:
    if evidence.get("format") != QUIESCENCE_FORMAT or evidence.get("state") != "quiescent_verified":
        raise HandoffError("root quiescence evidence format or state is invalid")
    if (evidence.get("release_request_id") != report.get("release_request_id")
            or evidence.get("release_proof_digest") != proof_digest):
        raise HandoffError("quiescence evidence is not bound to this exact release proof")
    supervisor = report.get("supervisor")
    if not isinstance(supervisor, dict):
        raise HandoffError("owned-release report lacks canonical supervisor identity")
    expected_supervisor_root = supervisor.get("service_state_root")
    if evidence.get("supervisor_state_root") != expected_supervisor_root:
        raise HandoffError("quiescence evidence supervisor root differs from the release report")
    if evidence.get("canonical_host_root") != str(canonical_host_root):
        raise HandoffError("quiescence evidence was not collected under the explicit canonical host root")
    observed = evidence.get("observed_at")
    expires = evidence.get("valid_until")
    if (isinstance(observed, bool) or not isinstance(observed, (int, float))
            or isinstance(expires, bool) or not isinstance(expires, (int, float))
            or observed <= 0 or observed > now + 2 or expires <= observed
            or expires - observed > 60 or now > expires):
        raise HandoffError("root quiescence evidence is stale or exceeds its 60-second lifetime")
    checks = evidence.get("target_checks")
    if not isinstance(checks, list) or len(checks) != 2:
        raise HandoffError("quiescence evidence must contain one check for each released target")
    expected = {target["session_id"]: target for target in targets}
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise HandoffError("quiescence target check is invalid")
        session = check.get("session_id")
        target = expected.get(session)
        if target is None or session in seen:
            raise HandoffError("quiescence target set differs from the release target set")
        seen.add(session)
        checked_at = check.get("observed_at")
        if (isinstance(checked_at, bool) or not isinstance(checked_at, (int, float))
                or checked_at < observed - 5 or checked_at > now + 2 or checked_at > expires):
            raise HandoffError("a target quiescence check is outside the current evidence window")
        identity_fields = ("target_id", "session_id", "daemon_epoch", "slot_id")
        if any(check.get(key) != target.get(key) for key in identity_fields):
            raise HandoffError("quiescence target identity differs from the release proof")
        proof = next(item for item in report["proofs"] if item["session_id"] == session)
        check_gpu_values = check.get("gpu_uuids")
        if (check.get("owner_id") != proof["owner_id"] or not isinstance(check_gpu_values, list)
                or sorted(_gpu(value) for value in check_gpu_values) != target["gpu_uuids"]):
            raise HandoffError("quiescence evidence does not bind exact owner and GPU identities")
        process = check.get("process")
        if (not isinstance(process, dict)
                or process.get("method") != "nvidia-smi-query-compute-apps+procfs-identity"
                or process.get("state") != "absent"
                or process.get("pid") != proof["server_pid"]
                or process.get("process_start") != proof["server_process_start"]
                or process.get("observed_pids") != []
                or process.get("observed_at") != check.get("observed_at")):
            raise HandoffError("quiescence evidence lacks target-bound nvidia-smi and procfs absence")
        lease = check.get("host_lease")
        owner_record = lease.get("owner") if isinstance(lease, dict) else None
        inventory = lease.get("accelerator_inventory") if isinstance(lease, dict) else None
        if (not isinstance(lease, dict)
                or lease.get("method") != "HostCoordinator(create=False)+HostResourceFacade.owner/conflicts/list"
                or lease.get("root") != str(canonical_host_root)
                or lease.get("owner_id") != proof["owner_id"]
                or lease.get("session_id") != session
                or lease.get("daemon_epoch") != target["daemon_epoch"]
                or lease.get("slot_id") != target["slot_id"]
                or not isinstance(lease.get("gpu_uuids"), list)
                or sorted(_gpu(value) for value in lease["gpu_uuids"]) != target["gpu_uuids"]
                or not isinstance(owner_record, dict)
                or owner_record.get("owner_id") != proof["owner_id"]
                or owner_record.get("state") != "released"
                or type(owner_record.get("residency_release_verified")) is not int
                or owner_record.get("residency_release_verified") != 1
                or type(owner_record.get("residency_protected")) is not int
                or owner_record.get("residency_protected") != 0
                or owner_record.get("resources") != []
                or owner_record.get("server_pid") != proof["server_pid"]
                or owner_record.get("server_process_start") != proof["server_process_start"]
                or lease.get("conflicts") != []
                or not isinstance(inventory, list)
                or not set(target["gpu_uuids"]) <= {_gpu(value) for value in inventory}
                or lease.get("observed_at") != check.get("observed_at")):
            raise HandoffError("quiescence evidence lacks exact native host-lease absence")
        slot = check.get("supervisor_slot")
        slot_gpus = slot.get("gpu_uuids") if isinstance(slot, dict) else None
        if (not isinstance(slot, dict) or slot.get("method") != "central_status"
                or slot.get("slot_id") != target["slot_id"]
                or slot.get("daemon_epoch") != target["daemon_epoch"]
                or slot.get("state") not in {"idle", "absent"}
                or slot.get("active") is not False
                or slot.get("owner_id") not in {None, proof["owner_id"]}
                or not isinstance(slot_gpus, list)
                or sorted(_gpu(value) for value in slot_gpus) != target["gpu_uuids"]
                or slot.get("observed_at") != check.get("observed_at")):
            raise HandoffError("quiescence evidence lacks exact inactive supervisor-slot observation")
    if seen != set(expected):
        raise HandoffError("quiescence evidence omits a released owner target")


def _load_source(qualification_root: Path) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    receipt_path = qualification_root / "gpu-source-receipt.json"
    template_path = qualification_root / "gpu-request-TEMPLATE.json"
    receipt, _receipt_raw = _read_private_json(receipt_path)
    template_payload, template_raw = _read_private_json(template_path)
    if receipt.get("format") != "PC-PA1-GPU-SOURCE-RECEIPT/1" or receipt.get("status") != "captured_detached_source_only":
        raise HandoffError("detached source receipt format or state is invalid")
    if receipt.get("release_proof_pending") is not True:
        raise HandoffError("source receipt is not marked as awaiting root release proof")
    if receipt.get("request_template_is_executable") is not False:
        raise HandoffError("source receipt does not mark its template as non-executable")
    snapshot = _private_dir(Path(str(receipt.get("snapshot_root", ""))))
    if snapshot.parent != qualification_root:
        raise HandoffError("detached source snapshot must remain a sibling of the attempt directory")
    manifest_path = snapshot / ".lab-snapshot.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise HandoffError("detached source manifest is unavailable")
    manifest_raw = manifest_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_sha != receipt.get("snapshot_manifest_sha256"):
        raise HandoffError("detached source manifest differs from its private receipt")
    manifest = _verify_snapshot(snapshot, manifest_sha)
    if (manifest.get("format") != "project-control-lab-snapshot/1"
            or manifest.get("head_commit") != receipt.get("source_head_commit")
            or manifest.get("base_commit") != receipt.get("source_base_commit")
            or manifest.get("status_fingerprint") != receipt.get("source_status_fingerprint")):
        raise HandoffError("detached snapshot identity differs from its source receipt")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != 1 or entries[0].get("path") != "scripts/pa1_gpu_smoke.cu":
        raise HandoffError("detached snapshot must contain only the selected CUDA fixture")
    if receipt.get("snapshot_manifest") != manifest:
        raise HandoffError("detached manifest inventory differs from the private source receipt")
    inventory = receipt.get("snapshot_inventory")
    if inventory != [{key: entries[0][key] for key in ("path", "mode", "size", "sha256")}]:
        raise HandoffError("detached inventory receipt differs from the captured manifest")
    source_path = snapshot / "scripts/pa1_gpu_smoke.cu"
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if (source_hash != receipt.get("source_artifact_sha256")
            or source_hash != entries[0].get("sha256")):
        raise HandoffError("detached source artifact differs from its receipt and manifest")
    template_path_obj = Path(str(receipt.get("request_template_path", "")))
    if template_path_obj != template_path:
        raise HandoffError("source receipt points to an unexpected request template")
    try:
        request, _ = gpu.load_request(template_path)
    except Exception as error:
        raise HandoffError(f"GPU request template schema is invalid: {error}") from error
    if (request.source_snapshot_path != snapshot
            or request.source_manifest_sha256 != manifest_sha
            or request.source_artifact_sha256 != source_hash
            or request.attempt_dir != qualification_root / "gpu-attempt"):
        raise HandoffError("typed request template is not bound to this detached snapshot and planned attempt")
    return receipt, template_payload, template_raw


def _private_write(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def build_handoff(*, qualification_root: Path, release_report_path: Path,
                  quiescence_evidence_path: Path, canonical_host_root: Path,
                  marker_ttl_seconds: int = 45, issue: bool = False,
                  clock=time.time) -> dict[str, Any]:
    root = _private_dir(qualification_root)
    host_root = _private_dir(canonical_host_root)
    if isinstance(marker_ttl_seconds, bool) or not isinstance(marker_ttl_seconds, int) or not 1 <= marker_ttl_seconds <= 60:
        raise HandoffError("marker TTL must be in 1..60 seconds")
    if release_report_path.is_symlink():
        raise HandoffError("owned-release report cannot be a symlink")
    release_path = release_report_path.resolve(strict=True)
    if not release_path.is_relative_to(root):
        raise HandoffError("owned-release report must be inside the private qualification root")
    report, report_raw = _read_private_json(release_path)
    evidence, evidence_raw = _read_private_json(quiescence_evidence_path)
    targets, proof_digest = _release_targets(report)
    now = float(clock())
    _validate_quiescence(evidence, report, targets, proof_digest, host_root, now)
    receipt, template, template_raw = _load_source(root)
    attempt_dir = Path(str(template["attempt_dir"])).resolve(strict=False)
    if attempt_dir != root / "gpu-attempt" or attempt_dir.exists() or attempt_dir.is_symlink():
        raise HandoffError("planned GPU attempt path exists or differs from the non-executable template")
    if (attempt_dir == Path(str(template["source_snapshot_path"]))
            or attempt_dir.is_relative_to(Path(str(template["source_snapshot_path"])) )):
        raise HandoffError("GPU attempt directory must remain separate from the detached snapshot")
    target_gpus = sorted(gpu_id for target in targets for gpu_id in target["gpu_uuids"])
    marker = {
        "format": gpu.RELEASE_FORMAT,
        "state": "released_verified",
        "experiment_id": template["experiment_id"],
        "attempt_id": template["attempt_id"],
        "source_manifest_sha256": receipt["snapshot_manifest_sha256"],
        "source_artifact_sha256": receipt["source_artifact_sha256"],
        "release_request_id": report["release_request_id"],
        "proof_digest": proof_digest,
        "issued_at": now,
        "valid_until": now + marker_ttl_seconds,
        "targets": targets,
    }
    marker_raw = (json.dumps(marker, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    request = dict(template)
    request.update({
        "attempt_dir": str(attempt_dir),
        "source_snapshot_path": receipt["snapshot_root"],
        "source_manifest_sha256": receipt["snapshot_manifest_sha256"],
        "source_artifact_sha256": receipt["source_artifact_sha256"],
        "gpu_uuids": target_gpus,
        "release_marker_path": str(attempt_dir / "release-marker.json"),
        "release_marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
        "release_request_id": report["release_request_id"],
        "release_proof_digest": proof_digest,
        "release_targets": targets,
    })
    # Revalidate exact downstream schema without preparing, launching, or
    # querying a device. The template's file path remains outside the future
    # attempt, so use an in-memory parse of the same exact request fields.
    if set(request) != {"format", "task_id", "experiment_id", "attempt_id", "hypothesis",
                        "attempt_dir", "source_snapshot_path", "source_manifest_sha256",
                        "source_artifact_sha256", "gpu_uuids", "release_marker_path",
                        "release_marker_sha256", "release_request_id", "release_proof_digest",
                        "release_targets", "wall_seconds", "cpu_threads"}:
        raise HandoffError("generated GPU request fields do not match the downstream schema")
    if issue:
        if (attempt_dir.parent != root or (attempt_dir / "request.json").exists()
                or (attempt_dir / "release-marker.json").exists()):
            raise HandoffError("GPU attempt target already exists or escapes the qualification root")
        attempt_dir.mkdir(mode=0o700)
        try:
            _private_write(attempt_dir / "release-marker.json", marker_raw)
            request_raw = (json.dumps(request, sort_keys=True, indent=2, ensure_ascii=False,
                                      allow_nan=False) + "\n").encode("utf-8")
            _private_write(attempt_dir / "request.json", request_raw)
        except BaseException:
            # Preserve any partial private handoff for review; never delete a
            # path after it has been created.
            raise
    return {
        "format": HANDOFF_FORMAT,
        "status": "issued_inert_handoff" if issue else "validated_plan",
        "request_template_sha256": hashlib.sha256(template_raw).hexdigest(),
        "release_report_sha256": hashlib.sha256(report_raw).hexdigest(),
        "quiescence_evidence_sha256": hashlib.sha256(evidence_raw).hexdigest(),
        "source_manifest_sha256": receipt["snapshot_manifest_sha256"],
        "source_artifact_sha256": receipt["source_artifact_sha256"],
        "release_request_id": report["release_request_id"],
        "release_proof_digest": proof_digest,
        "gpu_uuids": target_gpus,
        "release_targets": targets,
        "marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
        "marker_valid_until": marker["valid_until"],
        "attempt_dir": str(attempt_dir),
        "request_path": str(attempt_dir / "request.json") if issue else None,
        "marker_path": str(attempt_dir / "release-marker.json") if issue else None,
        "request": request if not issue else None,
        "device_query_performed": False,
        "execution_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification-root", type=Path, required=True)
    parser.add_argument("--release-report", type=Path, required=True)
    parser.add_argument("--quiescence-evidence", type=Path, required=True)
    parser.add_argument("--canonical-host-root", type=Path, required=True)
    parser.add_argument("--marker-ttl-seconds", type=int, default=45)
    parser.add_argument("--issue", action="store_true",
                        help="write the inert private request and short-lived release marker")
    args = parser.parse_args(argv)
    try:
        result = build_handoff(qualification_root=args.qualification_root,
                               release_report_path=args.release_report,
                               quiescence_evidence_path=args.quiescence_evidence,
                               canonical_host_root=args.canonical_host_root,
                               marker_ttl_seconds=args.marker_ttl_seconds,
                               issue=args.issue)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except Exception as error:
        print(json.dumps({"status": "refused", "error": f"{type(error).__name__}: {error}"},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
