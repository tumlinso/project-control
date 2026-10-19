from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


class InstallError(RuntimeError):
    """A candidate could not be built, verified, or safely installed."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
TODO_LOCK = Path("config/todo-orchestrator-runtime.json")


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
    todo_wheel: str
    todo_wheel_sha256: str
    todo_version: str
    python_executable: str


def source_identity(
    project_control_root: Path,
    todo_wheel: Path,
    _skills_root: Path | None = None,
    *,
    runner: Runner = _run,
) -> CandidateIdentity:
    project_control_root = project_control_root.resolve()
    if not (project_control_root / "pyproject.toml").is_file():
        raise InstallError("Project Control source is missing pyproject.toml")
    todo_wheel = todo_wheel.expanduser()
    if todo_wheel.is_symlink() or not todo_wheel.is_file():
        raise InstallError("standalone Todo wheel is missing or unsafe")
    todo_wheel = todo_wheel.resolve(strict=True)
    version = _wheel_version(todo_wheel)
    project_commit = _git_value(project_control_root, "rev-parse", "HEAD", runner=runner)
    project_tree = _git_value(project_control_root, "rev-parse", "HEAD^{tree}", runner=runner)
    return CandidateIdentity(
        schema_version=2,
        project_control_root=str(project_control_root),
        project_control_commit=project_commit,
        project_control_tree=project_tree,
        todo_wheel=str(todo_wheel),
        todo_wheel_sha256=_sha256_file(todo_wheel),
        todo_version=version,
        python_executable=sys.executable,
    )


def _wheel_version(wheel: Path) -> str:
    if wheel.suffix != ".whl" or wheel.is_symlink() or not wheel.is_file():
        raise InstallError("standalone Todo wheel must be a regular .whl file")
    try:
        with zipfile.ZipFile(wheel) as archive:
            paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(paths) != 1:
                raise ValueError("missing or ambiguous wheel metadata")
            lines = archive.read(paths[0]).decode("utf-8").splitlines()
    except (OSError, zipfile.BadZipFile, UnicodeError, ValueError) as exc:
        raise InstallError("standalone Todo wheel is invalid") from exc
    fields: dict[str, str] = {}
    for line in lines:
        if line.startswith("Name: ") or line.startswith("Version: "):
            key, value = line.split(": ", 1)
            if key in fields:
                raise InstallError("standalone Todo wheel metadata has duplicate identity fields")
            fields[key] = value
    if fields != {"Name": "todo-orchestrator", "Version": "0.2.0"}:
        raise InstallError("standalone Todo wheel must be todo-orchestrator==0.2.0")
    return fields["Version"]


def _todo_runtime_lock(project_root: Path, todo_wheel: Path) -> dict:
    lock_path = project_root / TODO_LOCK
    try:
        value = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise InstallError("Project Control Todo pairing lock is missing or invalid") from exc
    expected = {"format", "version", "distribution", "distribution_version", "source_commit",
                "wheel_sha256", "package_fingerprint", "provisional"}
    if (not isinstance(value, dict) or set(value) != expected
            or value.get("format") != "project-control.todo-runtime-lock"
            or value.get("version") != 1 or value.get("distribution") != "todo-orchestrator"
            or value.get("distribution_version") != "0.2.0"
            or type(value.get("provisional")) is not bool):
        raise InstallError("Project Control Todo pairing lock has an unsupported shape or package version")
    if value["provisional"] and os.environ.get("PROJECT_CONTROL_ALLOW_PROVISIONAL_TODO") != "1":
        raise InstallError("provisional Todo pairing lock requires explicit staging opt-in")
    if not value["provisional"]:
        commit, wheel_hash, package_hash = value["source_commit"], value["wheel_sha256"], value["package_fingerprint"]
        if (not isinstance(commit, str) or len(commit) != 40
                or any(char not in "0123456789abcdef" for char in commit)):
            raise InstallError("Todo source commit pin is missing or invalid")
        for pin in (wheel_hash, package_hash):
            if (not isinstance(pin, str) or len(pin) != 64
                    or any(char not in "0123456789abcdef" for char in pin)):
                raise InstallError("Todo wheel hash or package fingerprint pin is missing or invalid")
    wheel_hash = _sha256_file(todo_wheel)
    if value["wheel_sha256"] is not None and value["wheel_sha256"] != wheel_hash:
        raise InstallError("standalone Todo wheel differs from the paired runtime lock")
    return value


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


def _source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _working_tree_inventory(root: Path, *, runner: Runner) -> dict[str, object]:
    """Hash the tracked and non-ignored untracked files present at build time."""
    root = root.resolve()
    raw_paths = _git_value(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", runner=runner)
    files: dict[str, dict[str, str]] = {}
    for raw in raw_paths.split("\0"):
        if not raw:
            continue
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise InstallError(f"git returned an unsafe source path: {raw!r}")
        path = root / relative
        if path.is_symlink():
            digest = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
            files[relative.as_posix()] = {"kind": "symlink", "sha256": digest}
        elif path.is_file():
            files[relative.as_posix()] = {"kind": "file", "sha256": _sha256_file(path)}
        else:
            files[relative.as_posix()] = {"kind": "missing", "sha256": ""}
    return {"root": str(root), "commit": _git_value(root, "rev-parse", "HEAD", runner=runner),
            "files": dict(sorted(files.items()))}


def _freeze_skills(skills: Path | None, temporary: Path, destination: Path) -> dict:
    snapshot = temporary / "runtime-skills"
    snapshot.mkdir(parents=True, exist_ok=True)
    for name in ("cuda", "cpp-context-compiler"):
        source = skills / name if skills is not None else None
        if source is not None and source.is_dir():
            shutil.copytree(source, snapshot / name, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".git", ".venv", "build", "dist", "*.egg-info", ".ctxpp", ".todo"))
    return {"schema_version": 4, "skills_root": str(destination / "runtime-skills"),
            "tools_fingerprint": _source_fingerprint(snapshot)}


def _bind_todo_package(temporary: Path, destination: Path, release: dict, todo_wheel: Path,
                       todo_lock: dict) -> None:
    packages = sorted(temporary.glob("lib/python*/site-packages/todo_orchestrator"))
    if len(packages) != 1 or not (packages[0] / "__init__.py").is_file():
        raise InstallError("candidate environment omitted or ambiguously installed standalone Todo package")
    package = packages[0]
    relative = package.relative_to(temporary)
    fingerprint = _source_fingerprint(package)
    if todo_lock["package_fingerprint"] is not None and todo_lock["package_fingerprint"] != fingerprint:
        raise InstallError("installed Todo source differs from the paired runtime lock")
    release["todo_distribution"] = {
        "name": "todo-orchestrator", "version": "0.2.0",
        "package_path": str((destination / relative).resolve()),
        "fingerprint": fingerprint,
        "wheel_sha256": _sha256_file(todo_wheel),
        "source_commit": todo_lock["source_commit"],
    }


def _refresh_candidate_receiver_manifest(root: Path) -> None:
    """Write the installed receiver's map from the files actually in the wheel."""
    manifest_path = root / "receiver-manifest.json"
    if manifest_path.is_symlink():
        raise InstallError("candidate receiver manifest path is invalid")
    try:
        metadata = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError, TypeError) as error:
        raise InstallError("candidate receiver manifest is missing or invalid") from error
    if (not isinstance(metadata, dict) or metadata.get("schema_version") != 1
            or not isinstance(metadata.get("source_root"), str)
            or not isinstance(metadata.get("source_commit"), str)):
        raise InstallError("candidate receiver manifest metadata is invalid")

    root = root.resolve(strict=True)
    files: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise InstallError("candidate receiver contains a symlink")
        relative = path.relative_to(root)
        if path.is_dir() and path.name == "__pycache__":
            continue
        if not path.is_file() or path.suffix == ".pyc" or relative.as_posix() == "receiver-manifest.json":
            continue
        resolved = path.resolve(strict=True)
        if root not in resolved.parents or not resolved.is_file():
            raise InstallError("candidate receiver source path is invalid")
        files[relative.as_posix()] = _sha256_file(resolved)
    if not files or "local_worker/__init__.py" not in files:
        raise InstallError("candidate receiver package is incomplete")

    metadata["files"] = dict(sorted(files.items()))
    manifest_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _bind_project_control_runtime(temporary: Path, *, allow_missing_stub: bool = False) -> dict[str, str] | None:
    """Pin the wheel-owned receiver bundle in the release manifest.

    The package directory is already importable through the installed wheel;
    no .pth or ambient Skills path is needed. The receiver manifest pins every
    transferred source/config/schema file and the release digest protects the
    binding record itself.
    """
    packages = sorted(temporary.glob("lib/python*/site-packages/project_control/local_runtime"))
    if not packages:
        # Unit-test runners may stub pip without creating an installed wheel.
        if allow_missing_stub:
            return None
        raise InstallError("candidate Project Control wheel omitted the receiver runtime")
    if len(packages) != 1:
        raise InstallError("candidate has ambiguous Project Control receiver package roots")
    root = packages[0].resolve()
    _refresh_candidate_receiver_manifest(root)
    manifest_path = root / "receiver-manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, ValueError, TypeError) as error:
        raise InstallError("candidate receiver manifest is missing or invalid") from error
    files = manifest.get("files") if isinstance(manifest, dict) and manifest.get("schema_version") == 1 else None
    if not isinstance(files, dict) or not files:
        raise InstallError("candidate receiver manifest has no source file map")
    normalized: dict[str, str] = {}
    for relative, expected in files.items():
        path = Path(relative) if isinstance(relative, str) else Path("invalid")
        if (not isinstance(relative, str) or not relative or path.is_absolute() or ".." in path.parts
                or relative == "receiver-manifest.json" or not isinstance(expected, str)
                or len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected)):
            raise InstallError("candidate receiver manifest entry is invalid")
        source = root / path
        try:
            resolved = source.resolve(strict=True)
        except OSError as error:
            raise InstallError("candidate receiver source file is missing") from error
        if root not in resolved.parents or source.is_symlink() or not resolved.is_file():
            raise InstallError("candidate receiver source path is invalid")
        if _sha256_file(resolved) != expected:
            raise InstallError("candidate receiver source file hash mismatch")
        normalized[path.as_posix()] = expected
    discovered: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise InstallError("candidate receiver contains a symlink")
        if path.is_dir() and path.name == "__pycache__":
            continue
        if path.is_file() and "__pycache__" not in path.relative_to(root).parts and path.suffix != ".pyc" and path.name != "receiver-manifest.json":
            discovered.add(path.relative_to(root).as_posix())
    if discovered != set(normalized) or not (root / "local_worker" / "__init__.py").is_file():
        raise InstallError("candidate receiver file set or package root is invalid")
    fingerprint = hashlib.sha256(json.dumps(dict(sorted(normalized.items())), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"path": "project_control/local_runtime",
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "fingerprint": fingerprint}


def build_candidate(
    *,
    project_control_root: Path,
    todo_wheel: Path,
    skills_root: Path | None = None,
    destination: Path,
    offline: bool = False,
    uv_cache_dir: Path | None = None,
    runner: Runner = _run,
) -> CandidateIdentity:
    """Build the Project Control distribution into an isolated virtual environment.

    The destination is published with one rename only after installation succeeds.
    Existing paths are never replaced.
    """

    project_control_root = project_control_root.resolve()
    todo_wheel = todo_wheel.expanduser()
    if todo_wheel.is_symlink() or not todo_wheel.is_file():
        raise InstallError("standalone Todo wheel is missing or unsafe; pass an explicit built .whl file")
    todo_wheel = todo_wheel.resolve(strict=True)
    skills_root = skills_root.resolve() if skills_root is not None else None
    destination = destination.resolve()
    unsafe_roots = [project_control_root, todo_wheel]
    if skills_root is not None:
        unsafe_roots.append(skills_root)
    _refuse_unsafe_destination(destination, unsafe_roots)
    if offline and uv_cache_dir is None:
        raise InstallError("offline builds require an explicit writable uv cache directory")
    if offline and not uv_cache_dir.expanduser().is_dir():
        raise InstallError(f"offline uv cache directory is unavailable: {uv_cache_dir}")
    identity = source_identity(project_control_root, todo_wheel, skills_root, runner=runner)
    todo_lock = _todo_runtime_lock(project_control_root, todo_wheel)
    tree_inventory = {
        "schema_version": 1,
        "project_control": _working_tree_inventory(project_control_root, runner=runner),
        "todo_distribution": {"version": identity.todo_version,
                              "wheel_sha256": identity.todo_wheel_sha256,
                              "wheel_name": Path(identity.todo_wheel).name},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.building-", dir=destination.parent))
    published = False
    try:
        if offline:
            uv = shutil.which("uv")
            if not uv:
                raise InstallError("offline builds require the uv executable")
            cache = str(uv_cache_dir.expanduser().resolve())
            commands = (
                (uv, "--cache-dir", cache, "venv", "--python", sys.executable, str(temporary)),
                (uv, "--cache-dir", cache, "pip", "install", "--offline", "--python",
                 str(temporary / "bin" / "python"), str(project_control_root)),
                (uv, "--cache-dir", cache, "pip", "install", "--offline", "--no-deps", "--python",
                 str(temporary / "bin" / "python"), str(todo_wheel)),
            )
        else:
            commands = (
                (sys.executable, "-m", "venv", str(temporary)),
                (str(temporary / "bin" / "python"), "-m", "pip", "install",
                 "--disable-pip-version-check", str(project_control_root)),
                (str(temporary / "bin" / "python"), "-m", "pip", "install",
                 "--disable-pip-version-check", "--no-deps", str(todo_wheel)),
            )
        for command in commands:
            completed = runner(command)
            if completed.returncode:
                raise InstallError(
                    f"candidate command failed ({command[0]}): {completed.stderr.strip()}"
                )
        release = _freeze_skills(skills_root, temporary, destination)
        _bind_todo_package(temporary, destination, release, todo_wheel, todo_lock)
        runtime_binding = _bind_project_control_runtime(temporary, allow_missing_stub=runner is not _run)
        if runtime_binding is not None:
            release["local_runtime_binding"] = runtime_binding
        inventory_path = temporary / "source-working-tree-inventory.json"
        inventory_path.write_text(json.dumps(tree_inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        release.update({"project_control_commit": identity.project_control_commit,
                        "todo_wheel_sha256": identity.todo_wheel_sha256,
                        "project_control_fingerprint": _source_fingerprint(project_control_root / "src" / "project_control"),
                        "source_working_tree_inventory": {
                            "path": inventory_path.name,
                            "sha256": _sha256_file(inventory_path),
                        }})
        (temporary / "release-manifest.json").write_text(json.dumps(release, indent=2, sort_keys=True) + "\n")
        release_digest = hashlib.sha256((temporary / "release-manifest.json").read_bytes()).hexdigest()
        launcher = temporary / "bin" / "project-control-release"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\n" +
            "unset PYTHONPATH PYTHONHOME CODING_WORKFLOW_SKILLS_ROOT CODING_WORKFLOW_RUNTIME_FINGERPRINT "
            "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256 "
            "PROJECT_CONTROL_RELEASE_MANIFEST PROJECT_CONTROL_RELEASE_DIGEST\n" +
            "export PROJECT_CONTROL_SKILLS_ROOT=" + shlex.quote(str(destination / "runtime-skills")) + "\n" +
            "export PROJECT_CONTROL_OBSERVER_SKILLS_ROOT=" + shlex.quote(str(destination / "runtime-skills")) + "\n" +
            "export PROJECT_CONTROL_RELEASE_MANIFEST=" + shlex.quote(str(destination / "release-manifest.json")) + "\n" +
            "export PROJECT_CONTROL_RELEASE_DIGEST=" + shlex.quote(release_digest) + "\n" +
            "exec " + shlex.quote(str(destination / "bin" / "project-control")) + ' "$@"\n')
        launcher.chmod(0o755)
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
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise InstallError("registration discovery returned duplicate object keys")
            result[key] = item
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise InstallError("registration discovery did not return JSON") from exc
    if isinstance(value, list):
        registrations = {}
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                raise InstallError("registration discovery returned a malformed record")
            name = item["name"]
            if name in registrations:
                raise InstallError("registration discovery returned duplicate names")
            registrations[name] = item
        return registrations
    if not isinstance(value, dict):
        raise InstallError("registration discovery returned neither an object nor a list")
    if any(not name.strip() or not isinstance(item, dict) or
           ("name" in item and item["name"] != name) for name, item in value.items()):
        raise InstallError("registration discovery returned a malformed record")
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
