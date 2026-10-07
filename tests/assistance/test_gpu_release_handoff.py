"""CPU-only validation of inert, proof-bound GPU handoff issuance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import time
import uuid

import pytest

from project_control.assistance.lab_snapshot import MANIFEST_NAME
from scripts import pa1_gpu_experiment as gpu
from scripts import pa1_gpu_release_handoff as handoff


GPUS = [
    "GPU-00000000-0000-4000-8000-000000000001",
    "GPU-00000000-0000-4000-8000-000000000002",
    "GPU-00000000-0000-4000-8000-000000000003",
    "GPU-00000000-0000-4000-8000-000000000004",
]


def _write_private(path: Path, payload: dict) -> bytes:
    raw = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _fixture(tmp_path: Path):
    root = tmp_path / "qualification"
    root.mkdir(mode=0o700)
    snapshot = root / "gpu-source-snapshot"
    (snapshot / "scripts").mkdir(parents=True, mode=0o700)
    (snapshot / "scripts/pa1_gpu_smoke.cu").write_bytes(b"// detached fixture\n")
    (snapshot / "scripts/pa1_gpu_smoke.cu").chmod(0o600)
    source = (snapshot / "scripts/pa1_gpu_smoke.cu").read_bytes()
    manifest = {
        "format": "project-control-lab-snapshot/1",
        "head_commit": "a" * 40,
        "base_commit": "a" * 40,
        "status_fingerprint": "b" * 64,
        "total_bytes": len(source),
        "files": [{"path": "scripts/pa1_gpu_smoke.cu", "mode": 0o600,
                   "size": len(source), "sha256": hashlib.sha256(source).hexdigest()}],
    }
    manifest_raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (snapshot / MANIFEST_NAME).write_bytes(manifest_raw)
    (snapshot / MANIFEST_NAME).chmod(0o600)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    source_sha = hashlib.sha256(source).hexdigest()
    experiment_id, attempt_id = str(uuid.uuid4()), str(uuid.uuid4())
    target_placeholders = [{
        "target_id": f"placeholder-{index}", "session_id": f"placeholder-session-{index}",
        "daemon_epoch": f"placeholder-epoch-{index}", "slot_id": f"placeholder-slot-{index}",
        "gpu_uuids": GPUS[index * 2:index * 2 + 2],
    } for index in range(2)]
    receipt = {
        "format": "PC-PA1-GPU-SOURCE-RECEIPT/1",
        "status": "captured_detached_source_only",
        "source_root": "/canonical/project-control",
        "source_head_commit": "a" * 40,
        "source_base_commit": "a" * 40,
        "source_status_fingerprint": "b" * 64,
        "snapshot_root": str(snapshot),
        "snapshot_manifest_path": str(snapshot / MANIFEST_NAME),
        "snapshot_manifest_sha256": manifest_sha,
        "snapshot_manifest": manifest,
        "source_artifact_path": str(snapshot / "scripts/pa1_gpu_smoke.cu"),
        "source_artifact_sha256": source_sha,
        "snapshot_inventory": [{"path": "scripts/pa1_gpu_smoke.cu", "mode": 0o600,
                                 "size": len(source), "sha256": source_sha}],
        "uuid_mapping": {"status": "placeholder_only_pending_root_release_proof"},
        "request_template_path": str(root / "gpu-request-TEMPLATE.json"),
        "request_template_is_executable": False,
        "release_proof_pending": True,
    }
    _write_private(root / "gpu-source-receipt.json", receipt)
    template = {
        "format": gpu.REQUEST_FORMAT,
        "task_id": "PC-PA1-LAB",
        "experiment_id": experiment_id,
        "attempt_id": attempt_id,
        "hypothesis": "The detached fixture computes out[i] = 3*i + 1 exactly on four released GPUs.",
        "attempt_dir": str(root / "gpu-attempt"),
        "source_snapshot_path": str(snapshot),
        "source_manifest_sha256": manifest_sha,
        "source_artifact_sha256": source_sha,
        "gpu_uuids": GPUS,
        "release_marker_path": str(root / "gpu-attempt/release-marker.json"),
        "release_marker_sha256": "0" * 64,
        "release_request_id": "PENDING_ROOT_RELEASE_REQUEST",
        "release_proof_digest": "0" * 64,
        "release_targets": target_placeholders,
        "wall_seconds": 60,
        "cpu_threads": 2,
    }
    _write_private(root / "gpu-request-TEMPLATE.json", template)
    host = tmp_path / "canonical-host"
    host.mkdir(mode=0o700)
    supervisor = tmp_path / "canonical-supervisor"
    supervisor.mkdir(mode=0o700)
    return root, snapshot, host, supervisor, receipt, template


def _reports(root: Path, host: Path, supervisor: Path, *, now: float | None = None):
    now = time.time() if now is None else now
    proofs = []
    for index in range(2):
        proofs.append({
            "format": "PC-PA1-OWNED-RELEASE-PROOF/1",
            "request_id": "release-id-current",
            "session_id": f"session-{index}",
            "daemon_epoch": f"daemon-{index}",
            "slot_id": f"slot-{index}",
            "owner_id": f"owner-{index}",
            "host_lease_id": f"owner-{index}",
            "residency_generation": f"generation-{index}",
            "server_pid": 12000 + index,
            "server_process_start": f"proc-start-{index}",
            "gpu_uuids": GPUS[index * 2:index * 2 + 2],
            "process_released": True,
            "memory_released": True,
            "host_released": True,
            "observation": {"observed_unix": now - 5, "processes": []},
        })
    proof_digest = handoff._canonical_json_digest({"request_id": "release-id-current", "proofs": proofs})
    targets = [{"target_id": proof["session_id"], "session_id": proof["session_id"],
                "daemon_epoch": proof["daemon_epoch"], "slot_id": proof["slot_id"],
                "gpu_uuids": sorted(proof["gpu_uuids"]), "state": "released_verified"}
               for proof in proofs]
    rows = [{"session_id": proof["session_id"], "state": "released_verified",
             "release_request_id": "release-id-current", "proof": proof} for proof in proofs]
    report = {
        "format": handoff.RELEASE_REPORT_FORMAT,
        "status": "released_verified",
        "release_result": "released_verified",
        "release_request_id": "release-id-current",
        "physical_state": "released_verified",
        "release_verified_at": now - 10,
        "release_verified_sessions": 2,
        "release_verified_proof_digest": "d" * 64,
        "proof_digest": proof_digest,
        "proofs": proofs,
        "sessions": rows,
        "gpu_release_targets_ready": True,
        "gpu_release_targets": targets,
        "dispatcher_closed": True,
        "supervisor": {"service_state_root": str(supervisor)},
        "completed_unix": now - 10,
    }
    _write_private(root / "current-release-report.json", report)

    checks = []
    for index, proof in enumerate(proofs):
        target = targets[index]
        observed = now - 1
        checks.append({
            "target_id": target["target_id"],
            "session_id": proof["session_id"],
            "daemon_epoch": proof["daemon_epoch"],
            "slot_id": proof["slot_id"],
            "owner_id": proof["owner_id"],
            "gpu_uuids": target["gpu_uuids"],
            "observed_at": observed,
            "process": {"method": "nvidia-smi-query-compute-apps+procfs-identity",
                        "state": "absent", "pid": proof["server_pid"],
                        "process_start": proof["server_process_start"],
                        "observed_pids": [], "observed_at": observed},
            "host_lease": {
                "method": "HostCoordinator(create=False)+HostResourceFacade.owner/conflicts/list",
                "root": str(host), "owner_id": proof["owner_id"],
                "session_id": proof["session_id"], "daemon_epoch": proof["daemon_epoch"],
                "slot_id": proof["slot_id"], "gpu_uuids": target["gpu_uuids"],
                "owner": {"owner_id": proof["owner_id"], "state": "released",
                          "residency_release_verified": 1, "residency_protected": 0,
                          "resources": [], "server_pid": proof["server_pid"],
                          "server_process_start": proof["server_process_start"]},
                "conflicts": [], "accelerator_inventory": GPUS, "observed_at": observed,
            },
            "supervisor_slot": {"method": "central_status", "slot_id": target["slot_id"],
                                "daemon_epoch": target["daemon_epoch"], "state": "idle",
                                "active": False, "owner_id": proof["owner_id"],
                                "gpu_uuids": target["gpu_uuids"], "observed_at": observed},
        })
    evidence = {
        "format": handoff.QUIESCENCE_FORMAT,
        "state": "quiescent_verified",
        "release_request_id": "release-id-current",
        "release_proof_digest": proof_digest,
        "supervisor_state_root": str(supervisor),
        "canonical_host_root": str(host),
        "observed_at": now - 1,
        "valid_until": now + 30,
        "target_checks": checks,
    }
    _write_private(root / "quiescence-evidence.json", evidence)
    return root / "current-release-report.json", root / "quiescence-evidence.json", proof_digest


def test_inert_plan_validates_evidence_without_creating_attempt(tmp_path):
    root, _snapshot, host, supervisor, _receipt, _template = _fixture(tmp_path)
    release_path, evidence_path, _ = _reports(root, host, supervisor)

    result = handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                                   quiescence_evidence_path=evidence_path,
                                   canonical_host_root=host, issue=False)

    assert result["status"] == "validated_plan"
    assert result["device_query_performed"] is False
    assert result["execution_started"] is False
    assert not (root / "gpu-attempt").exists()


def test_issue_writes_exact_downstream_request_and_marker_but_never_runs(tmp_path):
    root, snapshot, host, supervisor, _receipt, template = _fixture(tmp_path)
    release_path, evidence_path, proof_digest = _reports(root, host, supervisor)

    result = handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                                   quiescence_evidence_path=evidence_path,
                                   canonical_host_root=host, marker_ttl_seconds=30, issue=True)

    request_path = root / "gpu-attempt/request.json"
    marker_path = root / "gpu-attempt/release-marker.json"
    request, _ = gpu.load_request(request_path)
    marker = gpu.validate_release_marker(request, marker_path)
    assert result["status"] == "issued_inert_handoff"
    assert result["device_query_performed"] is False
    assert result["execution_started"] is False
    assert request.source_snapshot_path == snapshot
    assert request.experiment_id == template["experiment_id"]
    assert request.release_proof_digest == proof_digest
    assert marker["valid_until"] - marker["issued_at"] == 30
    assert request.gpu_uuids == tuple(sorted(GPUS))
    assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(marker_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "gpu-attempt").stat().st_mode) == 0o700
    assert not (root / "gpu-attempt/cuda-controller-spec.json").exists()


def test_partial_release_report_is_rejected_without_using_its_old_proof(tmp_path):
    root, _snapshot, host, supervisor, _receipt, _template = _fixture(tmp_path)
    release_path, evidence_path, _ = _reports(root, host, supervisor)
    report = json.loads(release_path.read_text())
    report["status"] = "pending_or_failed"
    report["physical_state"] = "pending"
    _write_private(release_path, report)

    with pytest.raises(handoff.HandoffError, match="not freshly verified"):
        handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                              quiescence_evidence_path=evidence_path,
                              canonical_host_root=host, issue=True)
    assert not (root / "gpu-attempt").exists()


def test_quiescence_must_bind_exact_native_owner_pid_and_slot(tmp_path):
    root, _snapshot, host, supervisor, _receipt, _template = _fixture(tmp_path)
    release_path, evidence_path, _ = _reports(root, host, supervisor)
    evidence = json.loads(evidence_path.read_text())
    evidence["target_checks"][0]["host_lease"]["owner"]["residency_protected"] = 1
    _write_private(evidence_path, evidence)

    with pytest.raises(handoff.HandoffError, match="native host-lease absence"):
        handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                              quiescence_evidence_path=evidence_path,
                              canonical_host_root=host, issue=True)
    assert not (root / "gpu-attempt").exists()


def test_stale_quiescence_evidence_and_ttl_above_sixty_are_refused(tmp_path):
    root, _snapshot, host, supervisor, _receipt, _template = _fixture(tmp_path)
    release_path, evidence_path, _ = _reports(root, host, supervisor)
    evidence = json.loads(evidence_path.read_text())
    evidence["valid_until"] = evidence["observed_at"] + 61
    _write_private(evidence_path, evidence)

    with pytest.raises(handoff.HandoffError, match="60-second lifetime"):
        handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                              quiescence_evidence_path=evidence_path,
                              canonical_host_root=host, issue=True)
    assert not (root / "gpu-attempt").exists()
    with pytest.raises(handoff.HandoffError, match="marker TTL"):
        handoff.build_handoff(qualification_root=root, release_report_path=release_path,
                              quiescence_evidence_path=evidence_path,
                              canonical_host_root=host, marker_ttl_seconds=61)

