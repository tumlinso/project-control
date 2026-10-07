"""CPU-only admission and spec checks for the service-owned GPU runner."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import time
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pa1_gpu_experiment.py"
spec = importlib.util.spec_from_file_location("pa1_gpu_experiment", SCRIPT)
gpu = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = gpu
spec.loader.exec_module(gpu)


GPUS = [
    "GPU-00000000-0000-4000-8000-000000000001",
    "GPU-00000000-0000-4000-8000-000000000002",
    "GPU-00000000-0000-4000-8000-000000000003",
    "GPU-00000000-0000-4000-8000-000000000004",
]
TARGETS = [
    {"target_id": "target-a", "session_id": "session-a", "daemon_epoch": "epoch-a",
     "slot_id": "slot-a", "gpu_uuids": GPUS[:2], "state": "released_verified"},
    {"target_id": "target-b", "session_id": "session-b", "daemon_epoch": "epoch-b",
     "slot_id": "slot-b", "gpu_uuids": GPUS[2:], "state": "released_verified"},
]


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _attempt(tmp_path: Path):
    attempt = tmp_path / "attempt"
    source = tmp_path / "source-snapshot"
    attempt.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    (source / "scripts").mkdir(mode=0o700)
    fixture = SCRIPT.with_name("pa1_gpu_smoke.cu").read_bytes()
    (source / "scripts" / "pa1_gpu_smoke.cu").write_bytes(fixture)
    (source / "scripts" / "pa1_gpu_smoke.cu").chmod(0o600)
    manifest_payload = {
        "format": "project-control-lab-snapshot/1",
        "files": [{"path": "scripts/pa1_gpu_smoke.cu", "size": len(fixture),
                   "mode": 0o600, "sha256": hashlib.sha256(fixture).hexdigest()}],
    }
    manifest = source / ".lab-snapshot.json"
    _write_private(manifest, (json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return attempt, source, manifest, fixture


def _marker(request: dict[str, object], path: Path, *, expires: float | None = None) -> bytes:
    body = {
        "format": gpu.RELEASE_FORMAT,
        "state": "released_verified",
        "experiment_id": request["experiment_id"],
        "attempt_id": request["attempt_id"],
        "source_manifest_sha256": request["source_manifest_sha256"],
        "source_artifact_sha256": request["source_artifact_sha256"],
        "release_request_id": request["release_request_id"],
        "proof_digest": request["release_proof_digest"],
        "issued_at": time.time(),
        "valid_until": expires if expires is not None else time.time() + 45,
        "targets": [
            {**target, "gpu_uuids": sorted(target["gpu_uuids"])}
            for target in sorted(request["release_targets"], key=lambda value: value["target_id"])
        ],
    }
    raw = (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_private(path, raw)
    return raw


def _request(tmp_path: Path):
    attempt, source, manifest, fixture = _attempt(tmp_path)
    marker_path = attempt / "release-marker.json"
    payload: dict[str, object] = {
        "format": gpu.REQUEST_FORMAT,
        "task_id": "PC-PA1-LAB",
        "experiment_id": "00000000-0000-4000-8000-000000000011",
        "attempt_id": "00000000-0000-4000-8000-000000000012",
        "hypothesis": "The fixture transforms each item identically on all four visible devices.",
        "attempt_dir": str(attempt),
        "source_snapshot_path": str(source),
        "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "source_artifact_sha256": hashlib.sha256(fixture).hexdigest(),
        "gpu_uuids": GPUS,
        "release_marker_path": str(marker_path),
        "release_marker_sha256": "0" * 64,
        "release_request_id": "release-request-a",
        "release_proof_digest": "a" * 64,
        "release_targets": TARGETS,
        "wall_seconds": 60,
        "cpu_threads": 2,
    }
    marker_raw = _marker(payload, marker_path)
    payload["release_marker_sha256"] = hashlib.sha256(marker_raw).hexdigest()
    request_path = attempt / "request.json"
    _write_private(request_path, (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return request_path, payload, attempt, source, marker_path


def test_typed_request_emits_exact_four_gpu_controller_spec_and_private_intent(tmp_path):
    request_path, _, attempt, _, _ = _request(tmp_path)

    result = gpu.prepare(request_path)

    assert result["status"] == "prepared"
    intent_path = Path(result["effect_intent"])
    assert stat.S_IMODE(intent_path.stat().st_mode) == 0o600
    intent = json.loads(intent_path.read_text())
    assert intent["state"] == "prepared"
    assert intent["gpu_uuids"] == sorted(GPUS)
    spec_path = Path(result["controller_spec_path"])
    controller_spec = json.loads(spec_path.read_text())
    assert stat.S_IMODE(spec_path.stat().st_mode) == 0o600
    assert controller_spec["resources"] == {
        "gpus": 4, "gpu_uuids": sorted(GPUS), "cpu_threads": 2,
        "ram_bytes": 1_073_741_824,
    }
    assert controller_spec["benchmark"]["build_argv"][:5] == [
        str(gpu.CUDA_TOOLKIT / "bin" / "nvcc"), "-std=c++17", "-O2", "-arch=sm_70",
        "-cudart=static",
    ]
    assert controller_spec["benchmark"]["build_argv"][5] == str(
        Path(json.loads((attempt / "request.json").read_text())["source_snapshot_path"]) / "scripts" / "pa1_gpu_smoke.cu")
    assert controller_spec["benchmark"]["build_argv"][-1] == str(attempt / "pa1_gpu_smoke")
    assert controller_spec["timeout"] == 60
    assert controller_spec["paths"] == []
    assert "--go-real" in controller_spec["argv"]
    assert result["controller_invocation"][-3:] == ["--spec", str(spec_path), "--json"]


def test_dry_run_emits_spec_without_creating_intent_or_controller_state(tmp_path, capsys):
    request_path, _, attempt, *_ = _request(tmp_path)

    assert gpu.main(["--dry-run", "--request", str(request_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run"
    assert "controller_spec" in result
    assert not (attempt / "effect-intent.json").exists()
    assert not (attempt / "controller-state").exists()


@pytest.mark.parametrize(("key", "value", "message"), [
    ("gpu_uuids", GPUS[:3], "exactly four"),
    ("wall_seconds", 61, "wall_seconds"),
    ("cpu_threads", 3, "cpu_threads"),
    ("hypothesis", "", "hypothesis"),
])
def test_request_rejects_scope_expansion_and_missing_hypothesis(tmp_path, key, value, message):
    request_path, payload, *_ = _request(tmp_path)
    payload[key] = value
    request_path.write_text(json.dumps(payload))
    request_path.chmod(0o600)

    with pytest.raises(gpu.ExperimentError, match=message):
        gpu.load_request(request_path)


def test_prepare_rejects_expired_or_mismatched_root_release_marker(tmp_path):
    request_path, payload, _, _, marker_path = _request(tmp_path)
    raw = _marker(payload, marker_path, expires=time.time() - 1)
    payload["release_marker_sha256"] = hashlib.sha256(raw).hexdigest()
    request_path.write_text(json.dumps(payload))
    request_path.chmod(0o600)

    with pytest.raises(gpu.ExperimentError, match="expired"):
        gpu.prepare(request_path)
    assert not (Path(payload["attempt_dir"]) / "effect-intent.json").exists()


def test_go_real_fails_closed_before_child_without_active_controller_receipt(tmp_path, monkeypatch):
    request_path, _, attempt, _, _ = _request(tmp_path)
    gpu.prepare(request_path)
    binary = attempt / "pa1_gpu_smoke"
    binary.write_bytes(b"never executed")
    binary.chmod(0o700)
    monkeypatch.delenv("TODO_GPU_LEASE_RECEIPT", raising=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", ",".join(GPUS))

    with pytest.raises(gpu.ExperimentError, match="TODO_GPU_LEASE_RECEIPT"):
        gpu.go_real(request_path)
    assert not (attempt / "effect-result.json").exists()


def test_foreground_receipt_must_match_live_controller_project_and_visible_uuid_set(tmp_path, monkeypatch):
    receipt_path = tmp_path / "lease.json"
    receipt = {
        "format": "CUDA-FOREGROUND-LEASE/1", "state": "active",
        "owner_id": "foreground:owner-a", "pid": 4321,
        "project_root": str(gpu.REPO_ROOT),
        "resource_ids": [f"accelerator:{value}" for value in GPUS],
    }
    _write_private(receipt_path, json.dumps(receipt).encode())
    monkeypatch.setenv("TODO_GPU_LEASE_RECEIPT", str(receipt_path))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3")
    monkeypatch.setattr(gpu.os, "kill", lambda _pid, _signal: None)
    original_read_bytes = Path.read_bytes

    def read_bytes(path):
        if str(path) == "/proc/4321/cmdline":
            return b"python\0cuda_controller.py\0run\0"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(gpu.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        stdout="\n".join(f"{value},{index}" for index, value in enumerate(GPUS))))

    observed = gpu.load_foreground_receipt(tuple(GPUS), gpu.REPO_ROOT)

    assert observed["owner_id"] == "foreground:owner-a"
    assert observed["visible_gpu_uuids"] == GPUS


def test_effect_intent_in_launching_state_cannot_be_replayed(tmp_path):
    request_path, _, attempt, _, _ = _request(tmp_path)
    gpu.prepare(request_path)
    intent_path = attempt / "effect-intent.json"
    intent = json.loads(intent_path.read_text())
    intent["state"] = "launching"
    intent_path.write_text(json.dumps(intent))
    intent_path.chmod(0o600)
    request, raw = gpu.load_request(request_path)

    with pytest.raises(gpu.ExperimentError, match="prepared effect intent"):
        gpu._read_intent(request, request_path, raw)


def test_source_manifest_or_source_bytes_racing_after_prepare_are_rejected(tmp_path):
    request_path, _, _, source, _ = _request(tmp_path)
    gpu.prepare(request_path)
    fixture = source / "scripts" / "pa1_gpu_smoke.cu"
    fixture.write_text("changed after preparation")
    fixture.chmod(0o600)

    with pytest.raises(gpu.ExperimentError, match="fixture source digest changed"):
        gpu.go_real(request_path)


def test_request_cannot_point_private_output_inside_canonical_repository(tmp_path):
    request_path, payload, *_ = _request(tmp_path)
    payload["attempt_dir"] = str(gpu.REPO_ROOT / "attempt")
    request_path.write_text(json.dumps(payload))
    request_path.chmod(0o600)

    with pytest.raises(gpu.ExperimentError, match="outside the canonical repository"):
        gpu.prepare(request_path)
