import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

from tests.local_runtime import receiver_runtime_path


def test_source_owned_release_validation_ignores_stale_receiver_manifest(monkeypatch, tmp_path):
    monkeypatch.delenv("PROJECT_CONTROL_RELEASE_MANIFEST", raising=False)
    monkeypatch.delenv("PROJECT_CONTROL_RELEASE_DIGEST", raising=False)
    monkeypatch.delenv("PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256", raising=False)
    receiver_root = receiver_runtime_path()
    if str(receiver_root) not in sys.path:
        sys.path.insert(0, str(receiver_root))

    from project_control.runtime_binding import bind_local_runtime
    identity = bind_local_runtime(root=receiver_root)
    assert identity.source_commit == "working-tree"
    module = importlib.import_module("local_worker.supervisor")

    from project_control.observer_analysis import SkillsObserverAnalysisProvider
    provider = SkillsObserverAnalysisProvider()
    provider._state_root = tmp_path / "observer-state"
    client = provider._get_backend()
    assert type(client) is not module.SupervisorClient

    source_path = receiver_root / "local_worker/supervisor.py"
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    todo_fingerprint = hashlib.sha256(json.dumps(
        client.runtime_context, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()
    status = {"source_sha256": source_sha, "runtime_identity": client.runtime_context,
        "runtime_fingerprint": todo_fingerprint, "supervisor_pid": os.getpid(),
        "supervisor_process_start": "fixture-process-start", "daemon_epoch": "e" * 64}
    manifest_path = receiver_root / "receiver-manifest.json"
    original_read_text = Path.read_text
    stale_manifest = json.dumps({"files": {"local_worker/supervisor.py": "0" * 64}})

    def stale_read_text(path, *args, **kwargs):
        if path.resolve() == manifest_path.resolve():
            return stale_manifest
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", stale_read_text)
    client._validate_owned_release_status(status)

    strict_client = object.__new__(module.SupervisorClient)
    strict_client.runtime_context = client.runtime_context
    with pytest.raises(module.SupervisorError, match="receiver_source_identity_mismatch"):
        strict_client._validate_owned_release_status(status)

    def missing_read_text(path, *args, **kwargs):
        if path.resolve() == manifest_path.resolve():
            raise FileNotFoundError("manifest intentionally unavailable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", missing_read_text)
    client._validate_owned_release_status(status)
    with pytest.raises(module.SupervisorError, match="receiver_source_identity_unavailable"):
        strict_client._validate_owned_release_status(status)
