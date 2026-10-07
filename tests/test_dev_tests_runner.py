from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("pc-dev", "dev_tests.sh", "pa1_source_tests.sh")
PIN_ENV = (
    "PROJECT_CONTROL_RELEASE_MANIFEST",
    "PROJECT_CONTROL_RELEASE_DIGEST",
    "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT",
    "CODING_WORKFLOW_RUNTIME_FINGERPRINT",
    "PROJECT_CONTROL_RUNTIME_PYTHON",
    "CODING_WORKFLOW_RUNTIME_PYTHON",
    "CODING_WORKFLOW_SKILLS_ROOT",
    "PROJECT_CONTROL_TEST_SKILLS_ROOT",
    "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256",
    "PROJECT_CONTROL_LOCAL_RUNTIME_ROOT",
    "PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256",
)


def _checkout(tmp_path: Path, *, symlink_venv: bool = False, with_python: bool = True) -> Path:
    repo = tmp_path / "checkout"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / name, scripts / name)

    source = repo / "src"
    pc = source / "project_control"
    todo = source / "todo_orchestrator"
    pc.mkdir(parents=True)
    todo.mkdir(parents=True)
    (pc / "__init__.py").write_text("SOURCE_PACKAGE = 'project_control'\n", encoding="utf-8")
    (pc / "cli.py").write_text(
        """import json, os, sys
import project_control, todo_orchestrator

def main():
    print(json.dumps({
        'args': sys.argv[1:],
        'project_control': project_control.__file__,
        'todo_orchestrator': todo_orchestrator.__file__,
        'provider': os.environ.get('PC_TEST_PROVIDER_TOKEN'),
        'canonical_content_root': os.environ.get('PROJECT_CONTROL_SKILLS_ROOT'),
        'content_root': os.environ.get('PROJECT_CONTROL_OBSERVER_SKILLS_ROOT'),
        'pins': {name: os.environ.get(name) for name in (
            'PROJECT_CONTROL_RELEASE_MANIFEST', 'PROJECT_CONTROL_RELEASE_DIGEST',
            'PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT', 'CODING_WORKFLOW_RUNTIME_FINGERPRINT',
            'PROJECT_CONTROL_RUNTIME_PYTHON', 'CODING_WORKFLOW_RUNTIME_PYTHON',
            'CODING_WORKFLOW_SKILLS_ROOT',
            'PROJECT_CONTROL_TEST_SKILLS_ROOT', 'PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256',
            'PROJECT_CONTROL_LOCAL_RUNTIME_ROOT', 'PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256',
            'PYTHONHOME',
        )},
    }))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    (todo / "__init__.py").write_text("SOURCE_PACKAGE = 'todo_orchestrator'\n", encoding="utf-8")
    helpers = repo / "tests" / "todo"
    helper_scripts = helpers / "scripts"
    helper_scripts.mkdir(parents=True)
    (helper_scripts / "__init__.py").write_text("SOURCE = 'test helper shadow package'\n", encoding="utf-8")
    (helper_scripts / "qualify_assistance_live.py").write_text("SOURCE = 'wrong helper shadow'\n", encoding="utf-8")
    (scripts / "qualify_assistance_live.py").write_text("SOURCE = 'checkout script'\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_source_probe.py").write_text(
        """import os
from pathlib import Path
import project_control, todo_orchestrator
from scripts import qualify_assistance_live

def test_source_packages_and_clean_pins():
    root = Path.cwd().resolve()
    assert Path(project_control.__file__).resolve().is_relative_to(root / 'src')
    assert Path(todo_orchestrator.__file__).resolve().is_relative_to(root / 'src')
    assert Path(qualify_assistance_live.__file__).resolve() == root / 'scripts/qualify_assistance_live.py'
    assert os.environ.get('PC_TEST_PROVIDER_TOKEN') == 'keep-provider'
    assert not os.environ.get('PROJECT_CONTROL_SKILLS_ROOT')
    assert os.environ.get('PROJECT_CONTROL_OBSERVER_SKILLS_ROOT') == '/operator/content'
    for name in %r:
        assert not os.environ.get(name), name
    assert not os.environ.get('PYTHONHOME')
    assert os.environ.get('PYTHONPATH') == f'{root}/src:{root}'
""" % (PIN_ENV,),
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = ['tests']\n", encoding="utf-8")

    venv_target = repo / ".dev-env" if symlink_venv else repo / ".venv"
    bin_dir = venv_target / "bin"
    bin_dir.mkdir(parents=True)
    if with_python:
        python = bin_dir / "python"
        python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)
    if symlink_venv:
        (repo / ".venv").symlink_to(venv_target.name)

    fake_bin = repo / "fake-bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(
        "#!/bin/sh\nprintf '%s\\n%s\\n%s\\n' \"$UV_CACHE_DIR\" \"$UV_PROJECT_ENVIRONMENT\" \"$*\" > \"$PC_DEV_SETUP_RECORD\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return repo


def _pinned_environment(repo: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({name: f"/stale/{name.lower()}" for name in PIN_ENV})
    environment.update(
        {
            "PROJECT_CONTROL_RELEASE_DIGEST": "a" * 64,
            "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "b" * 64,
            "CODING_WORKFLOW_RUNTIME_FINGERPRINT": "c" * 64,
            "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256": "d" * 64,
            "PROJECT_CONTROL_LOCAL_RUNTIME_MANIFEST_SHA256": "e" * 64,
            "PROJECT_CONTROL_SKILLS_ROOT": "/operator/canonical-content",
            "PYTHONHOME": "/stale/pythonhome",
            "PYTHONPATH": "/foreign/pythonpath",
            "PC_TEST_PROVIDER_TOKEN": "keep-provider",
            "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": "/operator/content",
        }
    )
    return environment


def _run(repo: Path, script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "scripts" / script), *args],
        cwd=repo,
        env=os.environ.copy() if env is None else env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_run_mode_uses_checkout_packages_and_forwards_cli_arguments(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    result = _run(
        repo,
        "pc-dev",
        "run",
        "serve",
        "observer",
        "--host",
        "127.0.0.1",
        "--port",
        "8768",
        env=_pinned_environment(repo),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["args"] == ["serve", "observer", "--host", "127.0.0.1", "--port", "8768"]
    assert Path(payload["project_control"]).resolve() == repo / "src/project_control/__init__.py"
    assert Path(payload["todo_orchestrator"]).resolve() == repo / "src/todo_orchestrator/__init__.py"
    assert payload["provider"] == "keep-provider"
    assert payload["canonical_content_root"] == "/operator/canonical-content"
    assert payload["content_root"] == "/operator/content"
    assert all(value is None for value in payload["pins"].values())


def test_test_and_legacy_wrappers_run_hermetic_source_pytest(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    environment = _pinned_environment(repo)
    result = _run(repo, "dev_tests.sh", "tests/test_source_probe.py", "-q", env=environment)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "1 passed" in result.stdout

    result = _run(
        repo,
        "pa1_source_tests.sh",
        "-c",
        "import project_control, todo_orchestrator; print(project_control.__file__); print(todo_orchestrator.__file__)",
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert str(repo / "src/project_control/__init__.py") in result.stdout
    assert str(repo / "src/todo_orchestrator/__init__.py") in result.stdout


def test_setup_uses_repo_cache_and_preserves_existing_venv_symlink(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, symlink_venv=True)
    link_before = os.readlink(repo / ".venv")
    record = tmp_path / "uv-setup-record"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": str(repo / "fake-bin") + os.pathsep + environment.get("PATH", ""),
            "PC_DEV_SETUP_RECORD": str(record),
        }
    )
    result = _run(repo, "pc-dev", "setup", env=environment)
    assert result.returncode == 0, result.stderr
    cache_dir, project_environment, arguments = record.read_text().splitlines()
    assert Path(cache_dir) == repo / ".cache/uv"
    assert Path(project_environment) == repo / ".venv"
    assert arguments == "sync --frozen --group dev"
    assert os.readlink(repo / ".venv") == link_before

    environment["UV_CACHE_DIR"] = str(tmp_path / "provided-cache")
    result = _run(repo, "pc-dev", "setup", env=environment)
    assert result.returncode == 0, result.stderr
    cache_dir, _, _ = record.read_text().splitlines()
    assert cache_dir == str(tmp_path / "provided-cache")


def test_missing_environment_prints_setup_instruction(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, with_python=False)
    result = _run(repo, "pc-dev", "test", "tests/test_source_probe.py")
    assert result.returncode == 2
    assert "run scripts/pc-dev setup" in result.stderr
