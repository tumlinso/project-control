from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from project_control import cli
from project_control.assistance.lab import LabProject, LabScope, LabService, LabSessionService
from project_control.assistance import lab_cli
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
    assert "--property=Delegate=cpu memory pids" in command
    assert "--property=CPUAccounting=yes" in command
    assert "--property=MemoryAccounting=yes" in command
    assert "--property=TasksAccounting=yes" in command
    assert "--property=CPUWeight=100" in command
    assert "--property=DelegateSubgroup=controller" in command
    assert "--property=RuntimeMaxSec=45s" in command
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


def test_scoped_lab_parsers_keep_one_shot_run_and_add_scope_controls():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command")
    lab = commands.add_parser("lab")
    lab_commands = lab.add_subparsers(dest="lab_command", required=True)
    run = lab_commands.add_parser("run")
    run.add_argument("--project", required=True)
    run.add_argument("--source", action="append", required=True)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    status = lab_commands.add_parser("status")
    lab_cli.add_scoped_lab_parsers(lab_commands)
    lab_cli.configure_scoped_run_parser(run)

    scoped = parser.parse_args(["lab", "run", "--scope-id", "labscope_123"])
    one_shot = parser.parse_args(["lab", "run", "--project", "demo", "--source", "src.py", "--", "python3", "src.py"])
    preview = parser.parse_args(["lab", "preview", "--project", "demo", "--source", "src.py", "--goal", "measure", "--gpu-uuid", "GPU-00000000-0000-0000-0000-000000000001"])
    status_args = parser.parse_args(["lab", "status", "--scope-id", "labscope_123"])

    assert scoped.scope_id == "labscope_123"
    assert one_shot.project == "demo" and one_shot.source == ["src.py"]
    assert preview.tool is None and preview.gpu_uuid == ["GPU-00000000-0000-0000-0000-000000000001"]
    assert status_args.scope_id == "labscope_123"
    assert any(action.dest == "scope_id" for action in status._actions)


class _ScopedLabHarness:
    def __init__(self):
        self.calls = []
        self.lab_service = SimpleNamespace(operator=lambda: "operator")

    def resolve_project(self, session_id):
        assert session_id == "scope-1"
        return "demo"

    def preview(self, operator, scope):
        self.calls.append(("preview", operator, scope))
        return {"session_id": "scope-1", "state": "previewed"}

    def authorize(self, operator, session_id):
        self.calls.append(("authorize", operator, session_id))
        return {"session_id": session_id, "state": "authorized"}

    def run(self, operator, session_id):
        self.calls.append(("run", operator, session_id))
        return {"session_id": session_id, "state": "completed"}

    def resume(self, operator, session_id):
        self.calls.append(("resume", operator, session_id))
        return {"session_id": session_id, "state": "completed"}

    def cancel(self, operator, session_id):
        self.calls.append(("cancel", operator, session_id))
        return {"session_id": session_id, "state": "cancelled"}

    def status(self, operator, session_id=None):
        self.calls.append(("status", operator, session_id))
        return {"session_id": session_id, "state": "authorized"}


@pytest.mark.parametrize("command,expected_demand", [
    ("authorize", False), ("status", False), ("cancel", False), ("run", True), ("resume", True),
])
def test_scoped_lab_only_starts_demand_for_explicit_run_or_resume(command, expected_demand, capsys):
    service = _ScopedLabHarness()
    demands = []
    args = SimpleNamespace(lab_command=command, scope_id="scope-1", session_id="scope-1")
    if command == "cancel":
        args.scope_id = "scope-1"
    result = lab_cli.handle_scoped_lab(
        args,
        lab_service_factory=lambda _project: service,
        demand_start=lambda: demands.append("start") or {"status": "ready"},
    )
    assert result == 0
    assert demands == (["start"] if expected_demand else [])
    assert service.calls[-1][0] == command
    assert json.loads(capsys.readouterr().out)["state"] in {"authorized", "completed", "cancelled"}


def test_scoped_lab_preview_uses_explicit_limits_and_never_starts_inference(monkeypatch, capsys):
    import project_control.assistance.lab as lab_module

    observed = {}

    def scope_factory(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(lab_module, "LabScope", scope_factory, raising=False)
    service = _ScopedLabHarness()
    demands = []
    args = SimpleNamespace(
        lab_command="preview", project="demo", source=["src/a.py", "tests/test_a.py"],
        goal="find a counterexample", tool=None, wall_seconds=600, max_experiments=6,
        gpu_uuid=["GPU-00000000-0000-0000-0000-000000000001"], toolchain_root=None,
    )
    result = lab_cli.handle_scoped_lab(
        args, lab_service_factory=lambda project: service,
        demand_start=lambda: demands.append(True),
    )
    assert result == 0
    assert observed == {
        "project": "demo", "source_paths": ("src/a.py", "tests/test_a.py"),
        "goal": "find a counterexample", "tools": ("python3", "cuda"), "wall_seconds": 600,
        "max_experiments": 6,
        "gpu_uuids": ("GPU-00000000-0000-0000-0000-000000000001",),
        "toolchain_root": None,
    }
    assert demands == []
    assert service.calls[0][0] == "preview"
    assert json.loads(capsys.readouterr().out)["session_id"] == "scope-1"


def test_experiment_planner_uses_trusted_policy_and_strict_json(monkeypatch):
    from project_control.assistance.lab_cli import make_experiment_planner

    class Provider:
        def __init__(self):
            self.request = None

        def investigate_turn(self, request):
            self.request = request
            return {"status": "available", "text": json.dumps({
                "hypothesis": "the boundary drops the last item",
                "source_citations": [{"path": "src/window.py", "sha256": "a" * 64}],
                "artifacts": [{"path": "test_boundary.py", "content": "assert run() == 4"}],
                "argv": ["python3", "test_boundary.py"],
                "measurements": ["exit code", "stdout"], "stop_rule": "one run",
                "done": False,
            })}

    provider = Provider()
    planner = make_experiment_planner(lambda: provider)
    request = {
        "session_id": "scope-1", "goal": "find boundary defect",
        "source_identity": {"repository": "source"},
        "sources": [{"path": "src/window.py", "sha256": "a" * 64, "text": "source"}],
        "prior_proposals": [], "receipts": [], "remaining_seconds": 600,
        "remaining_experiments": 6, "tools": ["python3"],
    }
    proposal = planner(request)
    assert proposal["argv"] == ["python3", "test_boundary.py"]
    assert provider.request["turn_policy_id"] == "experiment-plan-v1"
    assert provider.request["compute_profile"] == "narrow"
    assert provider.request["parallelism"] == "layer"
    assert "max_tokens" not in provider.request and "reasoning_mode" not in provider.request
    assert provider.request["response_format"]["schema"]["additionalProperties"] is False
    prompt = provider.request["messages"][0]["content"]
    assert len(prompt.encode("utf-8")) <= 10 * 1024
    assert '"path":"src/window.py"' in prompt

    provider.investigate_turn = lambda _request: {
        "status": "available", "text": "```json\n{}\n```",
    }
    with pytest.raises(ValueError, match="lab_planner_invalid_json"):
        planner(request)


@pytest.mark.parametrize("use_gpu", [False, True], ids=["cpu", "gpu"])
def test_scoped_cli_composition_uses_narrow_planner_and_runs_one_fake_effect(
        tmp_path, monkeypatch, capsys, use_gpu):
    """Exercise the installed command composition without model or device work."""
    from project_control.assistance import demand_runtime, lab_runner

    repo = tmp_path / "repo"
    source = repo / "src" / "pairs.py"
    source.parent.mkdir(parents=True)
    source.write_text("def pair_sum(values):\n    return sum(values)\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "src/pairs.py")
    subprocess.run(
        ["git", "-c", "user.name=LAB CLI", "-c", "user.email=lab@example.invalid",
         "commit", "-qm", "initial"], cwd=repo, check=True, capture_output=True, text=True,
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    gpu_uuid = "GPU-00000000-0000-0000-0000-000000000001"
    tools = ("python3", "cuda") if use_gpu else ("python3",)
    scope = LabScope(
        project="sample", source_paths=("src/pairs.py",),
        goal="exercise trusted planner and one contained adapter effect",
        tools=tools, gpu_uuids=(gpu_uuid,) if use_gpu else (),
    )
    service = LabService(
        projects={"sample": LabProject("sample", repo, "source")},
        state_root=tmp_path / "private-lab-state",
    )
    setup = LabSessionService(service, planner=lambda _request: {})
    preview = setup.preview(service.operator(), scope)
    setup.authorize(service.operator(), preview["session_id"])

    class FakeProvider:
        def __init__(self):
            self.requests = []
            self.client = SimpleNamespace(
                quiesce_for_foreground=self.quiesce,
                resume_after_foreground=self.resume,
            )

        def investigate_turn(self, request):
            self.requests.append(request)
            assert request["turn_policy_id"] == "experiment-plan-v1"
            assert request["compute_profile"] == "narrow"
            assert request["parallelism"] == "layer"
            assert "max_tokens" not in request and "reasoning_mode" not in request
            assert self.allowed_profiles == {"narrow"}
            if len(self.requests) > 1:
                proposal = {"hypothesis": "", "source_citations": [], "artifacts": [],
                            "argv": [], "measurements": [], "stop_rule": "", "done": True}
            elif use_gpu:
                proposal = {
                    "hypothesis": "exercise the approved CUDA adapter path",
                    "source_citations": [{"path": "src/pairs.py", "sha256": digest}],
                    "artifacts": [{"path": "probe.cu", "content": "// offline adapter fixture\n"}],
                    "argv": ["cuda", "run"], "measurements": ["receipt"],
                    "stop_rule": "one fake adapter call", "done": False,
                }
            else:
                proposal = {
                    "hypothesis": "exercise the approved Python adapter path",
                    "source_citations": [{"path": "src/pairs.py", "sha256": digest}],
                    "artifacts": [{"path": "probe.py", "content": "print('fixture')\n"}],
                    "argv": ["python3", "probe.py"], "measurements": ["exit status"],
                    "stop_rule": "one fake adapter call", "done": False,
                }
            return {"status": "available", "text": json.dumps(proposal)}

        def _checked_client(self, *, deadline_epoch):
            assert deadline_epoch > time.time()
            return self.client

        def quiesce(self, *, request_id, resource_ids, deadline_epoch):
            assert deadline_epoch > time.time()
            return {"format": "PC-MODEL-FOREGROUND-HANDOFF/1", "status": "quiesced",
                    "request_id": request_id, "resource_ids": resource_ids,
                    "continuation_id": "fixture-continuation", "slots": []}

        def resume(self, *, request_id, continuation_id, resource_ids, deadline_epoch, veto):
            assert continuation_id == "fixture-continuation" and veto is False
            assert deadline_epoch > time.time()
            return {"status": "resumed", "request_id": request_id,
                    "continuation_id": continuation_id, "resource_ids": resource_ids}

        allowed_profiles = {"narrow"}

    provider = FakeProvider()

    class FakeGpuLabExecutor:
        def __init__(self, *, project_roots, cuda_controller, quiesce, resume, owner_reader):
            assert project_roots == {"sample": repo}
            assert cuda_controller == runtime_root / "runtime-skills" / "cuda" / "scripts" / "cuda_controller.py"
            assert callable(owner_reader)
            self.quiesce, self.resume = quiesce, resume

        def __call__(self, active_scope, proposal, _snapshot_root, _proposal_root, *,
                     session_id, effect_id, deadline, cancelled):
            assert active_scope.gpu_uuids == (gpu_uuid,)
            assert proposal.argv == ("cuda", "run")
            assert session_id == preview["session_id"] and not cancelled()
            resource_ids = [f"accelerator:{gpu_uuid}"]
            quiet = self.quiesce(request_id=effect_id, resource_ids=resource_ids,
                                 deadline_epoch=deadline)
            assert quiet["status"] == "quiesced"
            resumed = self.resume(request_id=effect_id,
                continuation_id=quiet["continuation_id"], resource_ids=resource_ids,
                deadline_epoch=deadline, veto=False)
            artifact = proposal.artifacts[0]
            return {
                "format": "PC-GPU-LAB-RESULT/1", "status": "completed",
                "effect_id": effect_id, "scope_project": active_scope.project,
                "gpu_uuids": [gpu_uuid], "cleanup_verified": True,
                "proposal_artifacts": [{"path": artifact.path,
                    "sha256": hashlib.sha256(artifact.content.encode()).hexdigest(),
                    "bytes": len(artifact.content.encode())}],
                "foreground_terminal": {"verified": True, "state": "released",
                    "resources": [], "gpu_uuids": [gpu_uuid]},
                "probe": {"cleanup_verified": True, "cgroup_removed": True},
                "build": {"status": "ok", "cleanup_verified": True, "cgroup_removed": True},
                "run": {"status": "ok", "cleanup_verified": True, "cgroup_removed": True},
                "resume": resumed,
            }

    runtime_root = tmp_path / "selected-runtime"
    controller = runtime_root / "runtime-skills" / "cuda" / "scripts" / "cuda_controller.py"
    controller.parent.mkdir(parents=True)
    controller.write_text("# fake selected controller\n", encoding="utf-8")
    fake_config = _registered_project(repo)
    demand_providers = []

    monkeypatch.setattr(cli, "_scoped_lab_service", lambda _project: service)
    monkeypatch.setattr(cli, "_scoped_lab_in_transient_unit", lambda _command, _scope_id: -1)
    monkeypatch.setattr(cli, "load_config", lambda: fake_config)
    monkeypatch.setattr(cli, "_assistance_repository", lambda _config, _project: ("source", repo))
    monkeypatch.setattr("project_control.observer_analysis.SkillsObserverAnalysisProvider",
                        lambda: provider)
    monkeypatch.setattr(demand_runtime, "ensure_demand_runtime_ready",
                        lambda **kwargs: demand_providers.append(kwargs["provider"]) or {"status": "ready"})
    monkeypatch.setattr(demand_runtime, "capture_runtime_pin",
                        lambda: SimpleNamespace(release_root=runtime_root))
    monkeypatch.setattr("project_control.assistance.lab_gpu.GpuLabExecutor", FakeGpuLabExecutor)

    cpu_calls = []
    if use_gpu:
        def unexpected_cpu(*_args, **_kwargs):
            pytest.fail("GPU planner composition must not invoke the CPU runner")
        monkeypatch.setattr(lab_runner, "run_cpu", unexpected_cpu)
    else:
        from project_control.assistance.lab_runner import ExecutionResult

        def fake_cpu(snapshot_root, argv, grant, *, attempt_root, effect_id, **kwargs):
            cpu_calls.append((Path(snapshot_root), tuple(argv), grant.timeout_seconds, effect_id))
            return ExecutionResult(status="ok", returncode=0, stdout=b"fixture\n", stderr=b"",
                elapsed_ms=1.0, artifact_path=None, artifact_sha256=None, artifact_bytes=0,
                containment_backend="offline-test-double", effect_id=effect_id,
                process_identity=None, cleanup_verified=True)
        monkeypatch.setattr(lab_runner, "run_cpu", fake_cpu)

    result = cli._assistance_scoped_lab_command(SimpleNamespace(
        lab_command="run", scope_id=preview["session_id"], project=None,
    ))
    response = json.loads(capsys.readouterr().out)
    assert result == 0 and response["state"] == "completed"
    assert len(provider.requests) == 2
    assert demand_providers == [provider]
    if use_gpu:
        assert not cpu_calls
    else:
        assert len(cpu_calls) == 1 and cpu_calls[0][1] == ("python3", "probe.py")


def test_experiment_planner_bounds_prompt_and_discloses_source_and_history_omissions():
    from project_control.assistance.lab_cli import _fit_planner_prompt

    sources = [
        {"path": f"src/module_{index:02d}.py", "sha256": f"{index:064x}", "text": "x" * 12000}
        for index in range(24)
    ]
    payload = {
        "session_id": "scope-1", "goal": "inspect bounded source", "goal_truncated": False,
        "source_identity": {"manifest_sha256": "d" * 64}, "sources": [],
        "tools": ["python3"], "gpu_uuids": [], "toolchain_root": None,
        "artifact_mount": "/proposal", "mounts": {"source": "/workspace:ro"},
        "prior_proposals": [{"proposal_id": f"p-{i}", "sha256": "a" * 64} for i in range(30)],
        "receipts": [{"effect_id": f"e-{i}", "stdout": "private-output" * 1000,
                      "receipt": {"status": "finished", "stdout": "private-output" * 1000}}
                     for i in range(12)],
        "requested_source_count": len(sources), "omitted_source_count": 0,
        "truncated_source_count": 0, "omitted_proposal_count": 26,
        "omitted_receipt_count": 9, "remaining_seconds": 500,
        "remaining_experiments": 5,
    }
    packed, prompt = _fit_planner_prompt(payload, sources)

    assert len(prompt.encode("utf-8")) <= 10 * 1024
    assert packed["sources"][0]["path"] == "src/module_00.py"
    assert packed["sources"][0]["text"]
    assert packed["truncated_source_count"] >= 1
    assert packed["omitted_source_count"] + len(packed["sources"]) == len(sources)
    assert packed["omitted_proposal_count"] >= 26
    assert packed["omitted_receipt_count"] >= 9
    assert "private-output" not in prompt
    assert "receipt" in prompt


def test_experiment_planner_returns_inert_proposal_after_cancellation_during_turn():
    from project_control.assistance.lab_cli import make_experiment_planner

    state = {"cancelled": False}

    class Provider:
        def investigate_turn(self, _request):
            state["cancelled"] = True
            return {"status": "available", "text": "not json because cancellation wins"}

    planner = make_experiment_planner(Provider)
    request = {
        "session_id": "scope-1", "goal": "goal", "source_identity": {},
        "sources": [{"path": "a.py", "sha256": "a" * 64, "text": "x"}],
        "tools": ["python3"], "prior_proposals": [], "receipts": [],
        "remaining_seconds": 10, "deadline_epoch": time.time() + 10,
        "remaining_experiments": 1, "cancelled": lambda: state["cancelled"],
    }
    proposal = planner(request)
    assert proposal == {"hypothesis": "", "source_citations": [], "artifacts": [],
                       "argv": [], "measurements": [], "stop_rule": "", "done": True}


def test_experiment_planner_provider_supervisor_and_llama_adapter_schema_path():
    from project_control.assistance.policies import generation_settings, policy_for
    from project_control.observer_analysis import SkillsObserverAnalysisProvider
    from project_control.assistance.lab_cli import make_experiment_planner
    from project_control.local_runtime.local_worker import supervisor
    from project_control.local_runtime.local_worker.servers.llama_cpp import LlamaCppServerAdapter

    completion_payloads = []
    proposal_text = json.dumps({
        "hypothesis": "boundary loses final row",
        "source_citations": [{"path": "src/window.py", "sha256": "c" * 64}],
        "artifacts": [{"path": "test_boundary.py", "content": "assert window() == [1, 2]"}],
        "argv": ["python3", "test_boundary.py"],
        "measurements": ["return code"], "stop_rule": "one execution", "done": False,
    })

    def transport(_method, url, payload, _timeout):
        if url.endswith("/apply-template"):
            return 200, {"prompt": "template"}
        if url.endswith("/tokenize"):
            return 200, {"tokens": [1]}
        if url.endswith("/completion"):
            completion_payloads.append(payload)
            return 200, {"content": proposal_text, "tokens": [2],
                         "timings": {"predicted_n": 1, "predicted_ms": 1,
                                     "prompt_n": 1, "prompt_ms": 1,
                                     "prompt_per_second": 1000}}
        raise AssertionError(f"unexpected adapter request: {url}")

    adapter = LlamaCppServerAdapter(transport=transport)
    adapter._servers["fixture"] = {
        "evicted": False, "accepting": True, "canceled": set(), "base_url": "http://fixture",
        "profile": {"context_size": 32768}, "effective_context_size": 32768,
        "reasoning_state": {}, "last_completion_tokens": None, "active_requests": set(),
        "usage": {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0, "duration_ms": 0},
        "conservative_tokens_per_second": 0.01,
        "conservative_prompt_tokens_per_second": 1000,
    }

    class MockSupervisorClient:
        def run_observer_turn(self, request):
            response_format = supervisor._validate_response_format(request["response_format"])
            selected = policy_for(request["turn_policy_id"])
            wire_request = {
                "messages": request["messages"], "response_format": response_format,
                "timeout_seconds": request["timeout_seconds"],
                "deadline_epoch": request["deadline_epoch"],
                "turn_policy_id": selected.policy_id,
                "max_tokens": selected.visible_tokens,
                "reasoning_mode": selected.reasoning_mode,
                "logical_context_tokens": selected.logical_context_tokens,
                "turn_policy_instruction": selected.instruction,
                "reasoning_state_key": request.get("session_id", "planner-fixture"),
                "observer_generation": generation_settings(selected.policy_id),
            }
            value = adapter.run("fixture", wire_request)
            return {"status": "available", "text": value["text"],
                    "usage": value["usage"], "response_metadata": value["response_metadata"]}

    provider = SkillsObserverAnalysisProvider()
    provider._checked_client = lambda _deadline=None: MockSupervisorClient()
    planner = make_experiment_planner(lambda: provider)
    result = planner({
        "session_id": "scope-1", "goal": "test the selected file",
        "source_identity": {"manifest_sha256": "d" * 64},
        "sources": [{"path": "src/window.py", "sha256": "c" * 64,
                     "text": "def window(): return [1, 2]"}],
        "tools": ["python3"], "gpu_uuids": [], "toolchain_root": None,
        "prior_proposals": [], "receipts": [], "remaining_seconds": 45,
        "deadline_epoch": time.time() + 45, "remaining_experiments": 1,
    })

    assert result["hypothesis"] == "boundary loses final row"
    assert len(completion_payloads) == 1
    assert completion_payloads[0]["json_schema"]["required"] == sorted({
        "hypothesis", "source_citations", "artifacts", "argv", "measurements", "stop_rule", "done",
    })
    assert completion_payloads[0]["json_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("citation,argv,error", [
    ({"path": "outside.py", "sha256": "a" * 64}, ["python3", "t.py"], "lab_planner_source_outside_scope"),
    ({"path": "src/window.py", "sha256": "b" * 64}, ["python3", "t.py"], "lab_planner_source_identity_mismatch"),
    ({"path": "src/window.py", "sha256": "a" * 64}, ["bash", "t.sh"], "lab_planner_tool_outside_scope"),
])
def test_experiment_planner_rejects_uncited_or_ungranted_proposals(citation, argv, error):
    from project_control.assistance.lab_cli import _json_proposal

    proposal = {
        "hypothesis": "h", "source_citations": [citation],
        "artifacts": [{"path": "test.py", "content": "assert True"}],
        "argv": argv, "measurements": ["exit"], "stop_rule": "one", "done": False,
    }
    request = {"tools": ["python3"], "sources": [{"path": "src/window.py", "sha256": "a" * 64}]}
    with pytest.raises(ValueError, match=error):
        _json_proposal(proposal, request)


def test_experiment_planner_incomplete_proposal_reports_schema_and_missing_fields():
    from project_control.assistance.lab_cli import _json_proposal

    proposal = {
        "hypothesis": " ", "source_citations": [], "artifacts": [],
        "argv": [], "measurements": [], "stop_rule": "", "done": False,
    }
    with pytest.raises(ValueError) as exc_info:
        _json_proposal(proposal, {"tools": ["python3"], "sources": []})
    assert str(exc_info.value) == (
        "lab_planner_incomplete_proposal schema=experiment-plan-v1 "
        "missing_fields=argv,artifacts,hypothesis,measurements,source_citations,stop_rule"
    )
