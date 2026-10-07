from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from project_control import cli
from project_control.assistance.lab import LabService
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _registered_project(root: Path) -> ProjectControlConfig:
    return ProjectControlConfig(workspaces={
        "sample": WorkspaceConfig(
            authority_repository="source",
            repositories={"source": RepositoryConfig(root=root)},
        ),
    })


def _delegated_lab_available() -> bool:
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        return False
    source_root = Path(cli.__file__).resolve().parent.parent
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
           "PYTHONPATH": str(source_root)}
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_BUS_ADDRESS"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    command = [
        systemd_run, "--user", "--wait", "--pipe", "--collect", "--quiet",
        f"--unit=project-control-lab-probe-{os.getpid()}.service",
        "--property=Delegate=yes", "--property=DelegateSubgroup=controller",
        "--property=RuntimeMaxSec=15s", f"--working-directory={source_root}",
        f"--setenv=PYTHONPATH={source_root}", "--",
        sys.executable, "-c",
        "from project_control.assistance.lab_runner import current_cgroup_ready; "
        "raise SystemExit(0 if current_cgroup_ready() else 1)",
    ]
    try:
        probe = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def test_lab_run_keeps_operator_argv_and_uses_registered_root(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "input.py")
    subprocess.run(
        ["git", "-c", "user.name=CLI Test", "-c", "user.email=cli@example.invalid",
         "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True, text=True,
    )
    state = tmp_path / "observer-state"
    config = _registered_project(repo)
    observed = {}

    def record_grant(_service, _operator, grant):
        observed["grant"] = grant
        return {"experiment_id": grant.experiment_id, "argv": list(grant.argv)}

    with patch.object(cli, "load_config", return_value=config), \
            patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(state)}), \
            patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=True), \
            patch.object(LabService, "run", record_grant):
        result = cli.main([
            "assistance", "lab", "run", "--project", "sample", "--source", "input.py",
            "--hypothesis", "check argument preservation", "--reference", "baseline",
            "--measure", "elapsed", "--measure", "output", "--stop-rule", "one run",
            "--", "python", "-c", "print('one; two')", "argument with spaces", "$(literal)",
        ])

    payload = json.loads(capsys.readouterr().out)
    grant = observed["grant"]
    assert result == 0
    assert grant.project == "sample"
    assert Path(grant.source_root) == repo.resolve()
    assert grant.repository == "source"
    assert grant.argv == ("python", "-c", "print('one; two')", "argument with spaces", "$(literal)")
    assert payload["argv"] == list(grant.argv)


def test_lab_run_rejects_unregistered_project_before_selection(tmp_path, capsys):
    config = ProjectControlConfig(workspaces={})
    with patch.object(cli, "load_config", return_value=config), \
            patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=True):
        result = cli.main([
            "assistance", "lab", "run", "--project", "unknown", "--source", "source.py",
            "--hypothesis", "h", "--reference", "r", "--measure", "m", "--stop-rule", "s",
            "--", "python", "-c", "pass",
        ])
    assert result == 2
    assert "project_not_registered" in capsys.readouterr().err


def test_lab_status_is_cold_when_private_state_does_not_exist(tmp_path, capsys):
    state = tmp_path / "observer-state"
    with patch.dict(os.environ, {"PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR": str(state)}):
        result = cli.main(["assistance", "lab", "status"])
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload == {"experiments": [], "reconciled": []}
    assert not state.exists()


def test_lab_run_uses_one_delegated_transient_unit_and_preserves_argv(tmp_path, capsys):
    args = [
        "assistance", "lab", "run", "--project", "sample", "--source", "input.py",
        "--hypothesis", "h", "--reference", "r", "--measure", "m1", "--measure", "m2",
        "--stop-rule", "s", "--", "python", "-c", "print('a; b')", "with spaces", "$(literal)",
    ]
    source_root = tmp_path / "trusted" / "src"
    source_root.mkdir(parents=True)
    (source_root / "project_control").mkdir()
    (source_root / "project_control" / "__init__.py").write_text("", encoding="utf-8")
    completed = subprocess.CompletedProcess([], 0, stdout='{"status":"finished"}\n', stderr="")

    with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=False), \
            patch.object(cli.shutil, "which", return_value="/usr/bin/systemd-run"), \
            patch.object(cli, "_lab_runtime_identity", return_value=(Path(sys.executable), source_root)), \
            patch.object(cli, "_lab_transient_unit_active", return_value=False), \
            patch.object(cli.subprocess, "run", return_value=completed) as run:
        result = cli.main(args)

    call = run.call_args
    command = call.args[0]
    assert result == 0
    assert command[0] == "/usr/bin/systemd-run"
    assert "--property=Delegate=yes" in command
    assert "--property=DelegateSubgroup=controller" in command
    assert f"--working-directory={source_root}" in command
    child = command[command.index("--") + 1:]
    assert child == [
        str(Path(sys.executable)), "-m", "project_control.cli", "assistance", "lab", "run",
        "--project", "sample", "--source", "input.py", "--hypothesis", "h", "--reference", "r",
        "--measure", "m1", "--measure", "m2", "--stop-rule", "s", "--", "python", "-c",
        "print('a; b')", "with spaces", "$(literal)",
    ]
    assert "shell" not in call.kwargs or call.kwargs["shell"] is False
    assert call.kwargs["env"]["PYTHONPATH"] == str(source_root)
    assert set(call.kwargs["env"]) <= {
        "PATH", "HOME", "PYTHONPATH", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
        "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_BUS_ADDRESS",
    }
    assert json.loads(capsys.readouterr().out) == {"status": "finished"}


def test_lab_run_fails_closed_when_transient_runner_is_unavailable(capsys):
    with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=False), \
            patch.object(cli.shutil, "which", return_value=None):
        result = cli.main([
            "assistance", "lab", "run", "--project", "sample", "--source", "input.py",
            "--hypothesis", "h", "--reference", "r", "--measure", "m", "--stop-rule", "s",
            "--", "python", "-c", "pass",
        ])
    assert result == 2
    assert "lab_transient_runner_unavailable" in capsys.readouterr().err


def test_lab_run_fails_closed_when_source_identity_is_shadowed(capsys):
    with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=False), \
            patch.object(cli.shutil, "which", return_value="/usr/bin/systemd-run"), \
            patch.object(cli.importlib.util, "find_spec",
                         return_value=SimpleNamespace(origin="/tmp/shadow/project_control/cli.py")), \
            patch.object(cli.subprocess, "run") as run:
        result = cli.main([
            "assistance", "lab", "run", "--project", "sample", "--source", "input.py",
            "--hypothesis", "h", "--reference", "r", "--measure", "m", "--stop-rule", "s",
            "--", "python", "-c", "pass",
        ])
    assert result == 2
    assert "lab_cli_source_identity_unavailable" in capsys.readouterr().err
    run.assert_not_called()


def test_lab_run_does_not_recursively_submit_an_unready_transient_unit(capsys):
    with patch("project_control.assistance.lab_runner.current_cgroup_ready", return_value=False), \
            patch.object(cli, "_lab_transient_unit_active", return_value=True), \
            patch.object(cli.subprocess, "run") as run:
        result = cli.main([
            "assistance", "lab", "run", "--project", "sample", "--source", "input.py",
            "--hypothesis", "h", "--reference", "r", "--measure", "m", "--stop-rule", "s",
            "--", "python", "-c", "pass",
        ])
    assert result == 2
    assert "lab_delegated_cgroup_unavailable" in capsys.readouterr().err
    run.assert_not_called()


def test_lab_cli_runs_disposable_fixture_in_delegated_unit(tmp_path, monkeypatch, capsys):
    if not _delegated_lab_available():
        pytest.skip("delegated systemd user unit is unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "input.txt").write_text("captured source\n", encoding="utf-8")
    (repo / "auth.json").write_text("PRIVATE_AUTH_SENTINEL\n", encoding="utf-8")
    (repo / "runtime-state.json").write_text("PRIVATE_RUNTIME_SENTINEL\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "input.txt")
    subprocess.run(
        ["git", "-c", "user.name=CLI Test", "-c", "user.email=cli@example.invalid",
         "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True, text=True,
    )

    config_root = tmp_path / "config"
    config_dir = config_root / "project-control"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "schema_version = 2\n\n[workspaces.sample]\nauthority_repository = \"source\"\n"
        "[workspaces.sample.repositories.source]\n"
        f"root = {json.dumps(str(repo.resolve()))}\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    state = tmp_path / "observer-state"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setenv("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", str(state))
    monkeypatch.setattr("project_control.assistance.lab_runner.current_cgroup_ready", lambda: False)

    result = cli.main([
        "assistance", "lab", "run", "--project", "sample", "--source", "input.txt",
        "--hypothesis", "read one selected file", "--reference", "fixture contents",
        "--measure", "stdout", "--stop-rule", "one command", "--",
        "/usr/bin/cat", "input.txt",
    ])
    captured = capsys.readouterr()
    assert result == 0, captured.err
    receipt = json.loads(captured.out)

    assert receipt["status"] == "ok"
    assert receipt["containment_backend"] == "bubblewrap+cgroup-v2"
    assert receipt["argv"] == ["/usr/bin/cat", "input.txt"]
    assert receipt["stdout"] == "captured source\n"
    assert "PRIVATE_AUTH_SENTINEL" not in json.dumps(receipt)
    assert "PRIVATE_RUNTIME_SENTINEL" not in json.dumps(receipt)

    # Read back the private record to ensure only the explicitly selected file
    # reached the runner snapshot despite credential/state siblings existing.
    service = _lab_service_for_test("sample", repo, state / "lab")
    record = service.status(service.operator(), receipt["experiment_id"])
    paths = [item["path"] for item in record["experiments"][0]["source_manifest"]["files"]]
    assert paths == ["input.txt"]


def _lab_service_for_test(project: str, root: Path, state_root: Path) -> LabService:
    from project_control.assistance.lab import LabProject

    return LabService(projects={project: LabProject(project, root, "source")}, state_root=state_root)
