"""CPU-only guards for PA1 qualification-owned session release orchestration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from scripts import pa1_qualification_release as release


GPUS = [
    "GPU-00000000-0000-4000-8000-000000000001",
    "GPU-00000000-0000-4000-8000-000000000002",
    "GPU-00000000-0000-4000-8000-000000000003",
    "GPU-00000000-0000-4000-8000-000000000004",
]


def _artifact(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    state = root / "as1-state"
    state.mkdir(mode=0o700)
    supervisor = tmp_path / "canonical-supervisor"
    supervisor.mkdir(mode=0o700)
    report = {
        "format": "pa1-live-source-qualification/1",
        "status": "completed_with_gaps",
        "isolated_state_root": str(state),
        "runtime": {"service_state_root": str(supervisor)},
        "cleanup": {"isolated_job_service_stopped": True,
                    "central_supervisor_stopped": False},
    }
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (root / "report.json").chmod(0o600)
    return root, state, supervisor


def _snapshot(request_id: str, physical: str):
    proofs = []
    for index in range(2):
        proofs.append({
            "format": "PC-PA1-OWNED-RELEASE-PROOF/1",
            "request_id": request_id,
            "session_id": f"session-{index}",
            "daemon_epoch": f"epoch-{index}",
            "slot_id": f"slot-{index}",
            "owner_id": f"owner-{index}",
            "host_lease_id": f"host-{index}",
            "residency_generation": f"generation-{index}",
            "server_pid": 8000 + index,
            "server_process_start": f"start-{index}",
            "gpu_uuids": GPUS[index * 2:index * 2 + 2],
            "process_released": True,
            "memory_released": True,
            "host_released": True,
            "observation": {"observed_unix": 1_800_000_000 + index, "processes": []},
        })
    return {
        "release_request_id": request_id,
        "release_veto": 1,
        "physical_state": physical,
        "release_verified_at": 1_800_000_010 if physical == "released_verified" else None,
        "release_verified_sessions": 2 if physical == "released_verified" else 0,
        "release_verified_proof_digest": "b" * 64 if physical == "released_verified" else None,
        "release_verified_targets_digest": "c" * 64 if physical == "released_verified" else None,
        "sessions": [{"session_id": item["session_id"], "state": physical,
                      "release_request_id": request_id, "proof": item} for item in proofs],
        "proofs": proofs,
        "proof_digest": release._sha256(release._json_bytes({"request_id": request_id,
                                                               "proofs": proofs})),
        "gpu_release_targets_ready": True,
        "gpu_release_targets": [{
            "target_id": item["session_id"], "session_id": item["session_id"],
            "daemon_epoch": item["daemon_epoch"], "slot_id": item["slot_id"],
            "gpu_uuids": sorted(item["gpu_uuids"]), "state": "released_verified",
        } for item in proofs] if physical == "released_verified" else None,
    }


def test_default_cli_only_plans_and_never_composes_or_releases(tmp_path, capsys, monkeypatch):
    root, state, supervisor = _artifact(tmp_path)
    monkeypatch.setattr(release, "run_release", lambda **_kwargs: pytest.fail("release invoked without flag"))

    assert release.main(["--artifact-root", str(root), "--as1-state-root", str(state),
                         "--supervisor-state-root", str(supervisor)]) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "planned"
    assert plan["execute_flag_required"] == "--execute-release"
    assert not (root / "release-report.json").exists()


def test_state_root_must_match_artifact_and_be_private(tmp_path):
    root, _, supervisor = _artifact(tmp_path)
    other = tmp_path / "other-state"
    other.mkdir(mode=0o700)

    with pytest.raises(release.ReleaseError, match="contained"):
        release._load_artifact(root, other, supervisor)


def test_release_retries_same_committed_request_and_persists_gpu_marker_targets(tmp_path, monkeypatch):
    root, state, supervisor = _artifact(tmp_path)

    class Jobs:
        calls = []

        def release_owned_resources(self, control, *, reason):
            self.calls.append(("release", reason, type(control).__name__))
            return "pending_owner_verification" if len(self.calls) == 1 else "released_verified"

    jobs = Jobs()
    composition = SimpleNamespace(jobs=jobs, close=lambda: True)
    backend = SimpleNamespace(
        _state_root=supervisor,
        central_status=lambda **_kwargs: {"service_state_root": str(supervisor),
            "supervisor_pid": 1234, "runtime_fingerprint": "fingerprint",
            "source_sha256": "a" * 64, "daemon_epoch": "epoch"},
    )
    harness = __import__("scripts.qualify_assistance_live", fromlist=["_compose_isolated_surface"])
    monkeypatch.setattr(harness, "_isolated_composition", lambda *_args: (object(), object(), backend))
    monkeypatch.setattr(harness, "_compose_isolated_surface", lambda *_args: composition)
    snapshots = iter([_snapshot("stable-request", "pending"),
                      _snapshot("stable-request", "released_verified"),
                      _snapshot("stable-request", "released_verified")])
    monkeypatch.setattr(release, "_release_snapshot", lambda _jobs: next(snapshots))

    result = release.run_release(artifact_root=root, as1_state_root=state,
                                 supervisor_state_root=supervisor, max_retries=1)

    assert result["status"] == "released_verified"
    assert [call[0] for call in jobs.calls] == ["release", "release"]
    assert all(call[1] == "pa1-qualification-owned-release" for call in jobs.calls)
    assert result["release_request_id"] == "stable-request"
    assert result["dispatcher_closed"] is True
    assert result["gpu_release_targets_ready"] is True
    assert len(result["gpu_release_targets"]) == 2
    stored = json.loads((root / "release-report.json").read_text())
    assert stored["proof_digest"] == result["proof_digest"]
    assert stat.S_IMODE((root / "release-report.json").stat().st_mode) == 0o600


def test_retry_cap_is_enforced_before_composition(tmp_path):
    root, state, supervisor = _artifact(tmp_path)
    with pytest.raises(release.ReleaseError, match="0..3"):
        release.run_release(artifact_root=root, as1_state_root=state,
                            supervisor_state_root=supervisor, max_retries=4)
