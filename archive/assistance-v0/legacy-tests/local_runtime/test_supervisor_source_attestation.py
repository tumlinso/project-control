"""Source package identity is attested once when the supervisor starts."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.local_runtime import assert_receiver_module
from local_worker import supervisor

assert_receiver_module(supervisor)


def _server(supervisor_file: Path, state_root: Path):
    context = {"contract": "test-runtime"}
    identity = SimpleNamespace(root=state_root, public=lambda: context)
    receiver = SimpleNamespace(
        manifest_sha256="a" * 64,
        fingerprint="b" * 64,
        source_commit="working-tree",
    )
    backend = SimpleNamespace(
        repo_root=state_root,
        service_state_root=state_root,
        profile={},
        observer_status=lambda: {"slots": []},
        _slots={},
        ttl=900,
        idle_eviction_enabled=True,
    )
    with patch.object(supervisor, "__file__", str(supervisor_file)), \
         patch.object(supervisor, "bind_canonical_runtime", return_value=(identity, context)), \
         patch.object(supervisor, "bind_local_runtime", return_value=receiver):
        return supervisor.SupervisorServer(backend, root=state_root / "runtime", observer_only=True)


def test_supervisor_holds_startup_pc_fingerprint_until_a_new_bootstrap(tmp_path):
    package = tmp_path / "project_control"
    module_dir = package / "local_runtime/local_worker"
    module_dir.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    changed_module = package / "non_receiver_module.py"
    changed_module.write_text("VALUE = 1\n", encoding="utf-8")
    supervisor_file = module_dir / "supervisor.py"
    supervisor_file.write_text("SOURCE = 'first'\n", encoding="utf-8")
    state_root = tmp_path / "state"

    original = _server(supervisor_file, state_root)
    original_fingerprint = original._project_control_fingerprint
    original_status = original._observer_status()
    assert original_status["project_control_fingerprint"] == original_fingerprint
    assert original_status["runtime_mode"] == "source"
    assert original_status["source_root"] == str(supervisor_file.resolve().parents[4])
    assert original_status["python_executable"] == str(Path(sys.executable).resolve())

    changed_module.write_text("VALUE = 2\n", encoding="utf-8")
    assert original._observer_status()["project_control_fingerprint"] == original_fingerprint

    restarted = _server(supervisor_file, state_root)
    assert restarted._project_control_fingerprint != original_fingerprint
    assert restarted._observer_status()["project_control_fingerprint"] == restarted._project_control_fingerprint
