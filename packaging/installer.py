from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class InstallError(RuntimeError):
    """A candidate could not be built, verified, or safely installed."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _git_value(root: Path, *arguments: str, runner: Runner = _run) -> str:
    completed = runner(("git", "-C", str(root), *arguments))
    if completed.returncode:
        raise InstallError(f"git inspection failed for {root}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CandidateIdentity:
    schema_version: int
    project_control_root: str
    project_control_commit: str
    project_control_tree: str
    todo_root: str
    todo_commit: str
    todo_tree: str
    python_executable: str


def source_identity(
    project_control_root: Path,
    skills_root: Path,
    *,
    runner: Runner = _run,
) -> CandidateIdentity:
    project_control_root = project_control_root.resolve()
    todo_root = (skills_root.resolve() / "todo-orchestrator")
    if not (project_control_root / "pyproject.toml").is_file():
        raise InstallError("Project Control source is missing pyproject.toml")
    if not (todo_root / "pyproject.toml").is_file():
        raise InstallError("Todo Orchestrator source is missing pyproject.toml")
    return CandidateIdentity(
        schema_version=1,
        project_control_root=str(project_control_root),
        project_control_commit=_git_value(project_control_root, "rev-parse", "HEAD", runner=runner),
        project_control_tree=_git_value(project_control_root, "rev-parse", "HEAD^{tree}", runner=runner),
        todo_root=str(todo_root),
        todo_commit=_git_value(todo_root, "rev-parse", "HEAD", runner=runner),
        todo_tree=_git_value(todo_root, "rev-parse", "HEAD^{tree}", runner=runner),
        python_executable=sys.executable,
    )


def _refuse_unsafe_destination(destination: Path, roots: Iterable[Path]) -> None:
    destination = destination.resolve()
    forbidden = {Path(sys.prefix).resolve(), *(root.resolve() for root in roots)}
    if destination in forbidden:
        raise InstallError(f"refusing candidate destination at live/source path: {destination}")
    if destination.exists():
        raise InstallError(f"candidate destination already exists: {destination}")


def _relocate_candidate_scripts(temporary: Path, destination: Path) -> None:
    """Bind generated scripts to the final candidate path before publication.

    Standard virtual-environment console scripts embed the interpreter's
    absolute path in their shebang.  Renaming the environment without updating
    those scripts leaves an otherwise valid candidate with an executable that
    points back to the removed staging directory.  Use distlib's portable
    shell/Python launcher form for direct shebangs and update other generated
    activation scripts before the one atomic rename.
    """

    bin_dir = temporary / "bin"
    temporary_bytes = os.fsencode(temporary)
    destination_bytes = os.fsencode(destination)
    temporary_python = os.fsencode(temporary / "bin" / "python")
    destination_python = str(destination / "bin" / "python")
    launcher = (
        "#!/bin/sh\n"
        f"'''exec' {shlex.quote(destination_python)} \"$0\" \"$@\"\n"
        "' '''\n"
    ).encode("utf-8")

    for script in bin_dir.iterdir():
        if not script.is_file() or script.is_symlink():
            continue
        data = script.read_bytes()
        newline = data.find(b"\n")
        first_line = data if newline < 0 else data[:newline]
        if first_line == b"#!" + temporary_python:
            body = b"" if newline < 0 else data[newline + 1 :]
            rewritten = launcher + body
        else:
            rewritten = data.replace(temporary_bytes, destination_bytes)
        if rewritten != data:
            script.write_bytes(rewritten)

    stale = [
        script.name
        for script in bin_dir.iterdir()
        if script.is_file()
        and not script.is_symlink()
        and temporary_bytes in script.read_bytes()
    ]
    if stale:
        raise InstallError(f"candidate scripts retain staging paths: {sorted(stale)!r}")


def _verify_promoted_entrypoints(destination: Path, *, runner: Runner) -> None:
    commands = (
        (str(destination / "bin" / "project-control"), "--help"),
        (str(destination / "bin" / "python"), "-m", "project_control", "--help"),
    )
    for command in commands:
        completed = runner(command)
        if completed.returncode:
            raise InstallError(
                f"promoted candidate entry point failed ({command[0]}): "
                f"{completed.stderr.strip()}"
            )


def build_candidate(
    *,
    project_control_root: Path,
    skills_root: Path,
    destination: Path,
    runner: Runner = _run,
) -> CandidateIdentity:
    """Build both local distributions into a new, isolated virtual environment.

    The destination is published with one rename only after installation succeeds.
    Existing paths are never replaced.
    """

    project_control_root = project_control_root.resolve()
    skills_root = skills_root.resolve()
    destination = destination.resolve()
    _refuse_unsafe_destination(destination, (project_control_root, skills_root))
    identity = source_identity(project_control_root, skills_root, runner=runner)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.building-", dir=destination.parent))
    published = False
    try:
        commands = (
            (sys.executable, "-m", "venv", str(temporary)),
            (
                str(temporary / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(skills_root / "todo-orchestrator"),
                str(project_control_root),
            ),
        )
        for command in commands:
            completed = runner(command)
            if completed.returncode:
                raise InstallError(
                    f"candidate command failed ({command[0]}): {completed.stderr.strip()}"
                )
        (temporary / "pcu-candidate.json").write_text(
            json.dumps(asdict(identity), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _relocate_candidate_scripts(temporary, destination)
        os.replace(temporary, destination)
        published = True
        _verify_promoted_entrypoints(destination, runner=runner)
    except BaseException:
        shutil.rmtree(destination if published else temporary, ignore_errors=True)
        raise
    return identity


@dataclass(frozen=True)
class RollbackInventory:
    schema_version: int
    coding_workflow_registration: Mapping[str, object]
    project_control_registration: Mapping[str, object]
    service_unit_sha256: str | None
    service_properties: Mapping[str, str]


def _json_object(raw: str) -> Mapping[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InstallError("registration discovery did not return JSON") from exc
    if not isinstance(value, dict):
        raise InstallError("registration discovery returned a non-object")
    return value


def capture_rollback_inventory(*, runner: Runner = _run) -> RollbackInventory:
    """Capture only bounded, non-secret installation state."""

    registration = runner(("codex", "mcp", "list", "--json"))
    if registration.returncode:
        raise InstallError("unable to capture Codex MCP registrations")
    registrations = _json_object(registration.stdout)
    service = runner(("systemctl", "--user", "cat", "project-control.service"))
    properties = runner(
        (
            "systemctl", "--user", "show", "project-control.service",
            "--property=LoadState", "--property=ActiveState",
            "--property=FragmentPath", "--property=ExecStart",
        )
    )
    fields = {
        key: value
        for line in properties.stdout.splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
    } if properties.returncode == 0 else {}
    unit_hash = hashlib.sha256(service.stdout.encode()).hexdigest() if service.returncode == 0 else None
    return RollbackInventory(
        schema_version=1,
        coding_workflow_registration=registrations.get("coding-workflow", {}),
        project_control_registration=registrations.get("project-control", {}),
        service_unit_sha256=unit_hash,
        service_properties=fields,
    )


@dataclass(frozen=True)
class CutoverStep:
    apply: tuple[str, ...]
    rollback: tuple[str, ...]


class AtomicCutover:
    """Run an explicit cutover plan, rolling back every applied step on error."""

    def __init__(self, steps: Sequence[CutoverStep], *, runner: Runner = _run) -> None:
        self._steps = tuple(steps)
        self._runner = runner

    def execute(self, *, authority_to_install: bool) -> None:
        if not authority_to_install:
            raise InstallError("live installation requires explicit authority_to_install")
        applied: list[CutoverStep] = []
        try:
            for step in self._steps:
                result = self._runner(step.apply)
                if result.returncode:
                    raise InstallError(f"cutover step failed: {' '.join(step.apply)}")
                applied.append(step)
        except BaseException as failure:
            rollback_failures: list[str] = []
            for step in reversed(applied):
                restored = self._runner(step.rollback)
                if restored.returncode:
                    rollback_failures.append(" ".join(step.rollback))
            if rollback_failures:
                raise InstallError(
                    f"cutover failed and rollback was incomplete: {rollback_failures}"
                ) from failure
            raise


def candidate_manifest_digest(candidate: Path) -> str:
    manifest = candidate / "pcu-candidate.json"
    if not manifest.is_file():
        raise InstallError("candidate identity manifest is missing")
    return _sha256_file(manifest)
