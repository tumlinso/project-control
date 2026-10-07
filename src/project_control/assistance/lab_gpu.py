"""Bounded GPU LAB adapter built around Project Control's CUDA controller.

Model supplied content is data to this module, never a host command. The
controller is invoked with one trusted wrapper command; generated CUDA source
is compiled and executed in separate bubblewrap/cgroup sandboxes.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Any, Callable, Mapping, Protocol, Sequence

if __package__ in {None, ""}:
    # `cuda_controller` launches this trusted installed file with Python -I.
    # Re-anchor imports to the package adjacent to this file, never PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from project_control.assistance import lab_runner
else:
    from . import lab_runner


MAX_CPU_CORES = 2.0
MAX_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_PIDS = 32
MAX_PHASE_SECONDS = 60.0
MAX_PROBE_SECONDS = 10.0
MAX_OUTPUT_BYTES = 64 * 1024
MAX_CONTROLLER_RESPONSE_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARG_BYTES = 12 * 1024
GPU_UUID = re.compile(r"^GPU-[0-9a-fA-F-]{16,64}$")
ARTIFACT_NAME = "result.bin"
PROGRAM_NAME = "program"
NO_FOREGROUND_CODES = frozenset({"foreground_build_failed", "foreground_resource_contention",
                                 "requested_accelerator_unavailable", "foreign_gpu_activity",
                                 "gpu_not_quiescent"})
PRE_ADMISSION_CODES = frozenset({"foreground_build_failed", "requested_accelerator_unavailable"})


class GpuLabError(RuntimeError):
    """GPU execution was rejected or could not be proven contained."""


class GpuLabUnavailable(GpuLabError):
    """Required foreground, device, toolchain, or containment proof is absent."""


class GpuLabCancelled(GpuLabError):
    """The experiment was cancelled before a new irreversible phase."""


class LabScopeLike(Protocol):
    project: str
    gpu_uuids: tuple[str, ...]
    toolchain_root: str | None


class LabProposalLike(Protocol):
    argv: tuple[str, ...]
    artifacts: Sequence[object]
    source_citations: Sequence[object]
    hypothesis: str
    measurements: Sequence[str]
    stop_rule: str


@dataclass(frozen=True, slots=True)
class GpuLimits:
    """Hard ceilings for each contained build and execution phase."""

    cpu_cores: float = MAX_CPU_CORES
    memory_bytes: int = MAX_MEMORY_BYTES
    pids: int = MAX_PIDS
    build_seconds: float = MAX_PHASE_SECONDS
    run_seconds: float = MAX_PHASE_SECONDS
    output_bytes: int = MAX_OUTPUT_BYTES
    artifact_bytes: int = MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if not 0 < self.cpu_cores <= MAX_CPU_CORES:
            raise GpuLabError("GPU LAB CPU limit exceeds 2 cores")
        if not 1 <= self.memory_bytes <= MAX_MEMORY_BYTES:
            raise GpuLabError("GPU LAB memory limit exceeds 1 GiB")
        if not 1 <= self.pids <= MAX_PIDS:
            raise GpuLabError("GPU LAB pid limit exceeds 32")
        if not 0 < self.build_seconds <= MAX_PHASE_SECONDS or not 0 < self.run_seconds <= MAX_PHASE_SECONDS:
            raise GpuLabError("GPU LAB build and run limits may not exceed 60 seconds")
        if not 0 <= self.output_bytes <= MAX_OUTPUT_BYTES or not 0 <= self.artifact_bytes <= MAX_ARTIFACT_BYTES:
            raise GpuLabError("GPU LAB output or artifact limit is too large")


@dataclass(frozen=True, slots=True)
class ForegroundLease:
    path: Path
    owner_id: str
    pid: int
    process_start: str
    project_root: Path
    resource_ids: tuple[str, ...]
    visible_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SandboxedPhase:
    phase: str
    status: str
    returncode: int | None
    stdout: bytes
    stderr: bytes
    elapsed_ms: float
    artifact_path: Path | None
    artifact_sha256: str | None
    artifact_bytes: int
    process_identity: Mapping[str, object]
    cleanup_verified: bool
    cgroup_removed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase, "status": self.status, "returncode": self.returncode,
            "stdout": self.stdout.decode("utf-8", errors="replace"),
            "stderr": self.stderr.decode("utf-8", errors="replace"),
            "elapsed_ms": self.elapsed_ms, "artifact_path": str(self.artifact_path) if self.artifact_path else None,
            "artifact_sha256": self.artifact_sha256, "artifact_bytes": self.artifact_bytes,
            "process_identity": dict(self.process_identity), "cleanup_verified": self.cleanup_verified,
            "cgroup_removed": self.cgroup_removed,
        }


def _safe_root(value: str | Path, label: str, *, directory: bool = True) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise GpuLabError(f"{label} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GpuLabError(f"{label} is unavailable") from exc
    if directory and not resolved.is_dir():
        raise GpuLabError(f"{label} must be a directory")
    return resolved


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > 2048:
        raise GpuLabError(f"{label} path is invalid")
    rel = PurePosixPath(value)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise GpuLabError(f"{label} path must be relative and normalized")
    return rel


def _private_root(path: Path) -> Path:
    if path.is_symlink():
        raise GpuLabError("attempt directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not resolved.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise GpuLabError("attempt directory must be an owned private directory")
    return resolved


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_private_json(path: Path, value: Mapping[str, object], *, create_only: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only and path.exists():
            raise GpuLabError("GPU effect phase intent already exists")
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _process_start(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return raw.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _gpu_map() -> dict[str, tuple[int, int]]:
    """Return exact UUID -> (CUDA controller index, Linux device minor).

    CUDA indices and Linux character-device minors are independent namespaces;
    only the latter may be used to choose ``/dev/nvidiaN`` nodes.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,index,minor_number", "--format=csv,noheader,nounits"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"}, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GpuLabUnavailable("NVIDIA UUID to device mapping is unavailable") from exc
    if result.returncode != 0:
        raise GpuLabUnavailable("NVIDIA UUID to device mapping failed")
    mapping: dict[str, tuple[int, int]] = {}
    minors: set[int] = set()
    for line in result.stdout.decode("utf-8", errors="strict").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if (len(parts) != 3 or not GPU_UUID.fullmatch(parts[0])
                or not parts[1].isdigit() or not parts[2].isdigit()):
            raise GpuLabUnavailable("NVIDIA returned a malformed UUID to device mapping")
        index = int(parts[1])
        minor = int(parts[2])
        if parts[0] in mapping or index in {item[0] for item in mapping.values()} or minor in minors:
            raise GpuLabUnavailable("NVIDIA UUID to device mapping is ambiguous")
        mapping[parts[0]] = (index, minor)
        minors.add(minor)
    if not mapping:
        raise GpuLabUnavailable("no NVIDIA devices are visible")
    return mapping


def _approved_uuids(scope: LabScopeLike) -> tuple[str, ...]:
    raw = getattr(scope, "gpu_uuids", None)
    if not isinstance(raw, (tuple, list)) or not raw or len(raw) > 4:
        raise GpuLabError("GPU scope must approve between one and four exact GPU UUIDs")
    values = tuple(raw)
    if any(not isinstance(value, str) or not GPU_UUID.fullmatch(value) for value in values):
        raise GpuLabError("GPU scope contains an invalid UUID")
    if len(set(values)) != len(values):
        raise GpuLabError("GPU scope contains duplicate UUIDs")
    tools = getattr(scope, "tools", None)
    if not isinstance(tools, (tuple, list)) or "cuda" not in tools:
        raise GpuLabError("GPU scope must explicitly approve the cuda tool")
    return values


def _resolve_device_minors(mapping: Mapping[str, tuple[int, int]], approved: Sequence[str],
                           cuda_indices: Sequence[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Verify controller indices while selecting only Linux minors for mounts."""
    if tuple(mapping.get(value, (None, None))[0] for value in approved) != tuple(cuda_indices):
        raise GpuLabUnavailable("GPU UUID to CUDA index mapping changed after controller lease issuance")
    selected = tuple(mapping[value][1] for value in approved)
    ungranted = tuple(sorted(minor for value, (_index, minor) in mapping.items() if value not in approved))
    if len(set(selected)) != len(selected) or set(selected) & set(ungranted):
        raise GpuLabUnavailable("GPU UUID to device minor mapping is ambiguous")
    return selected, ungranted


def _phase_timeout(maximum: float, deadline_epoch: float) -> float:
    remaining = deadline_epoch - time.time()
    if remaining <= 0:
        raise GpuLabUnavailable("GPU LAB scope deadline expired before the next contained phase")
    return min(maximum, remaining)


def _controller_admission_started(code: object) -> bool:
    """Reflect native controller order; this says nothing about payload start."""
    return code not in PRE_ADMISSION_CODES


def _validate_lease(path_value: str | None, expected: tuple[str, ...], project_root: Path) -> ForegroundLease:
    if not path_value:
        raise GpuLabUnavailable("CUDA controller lease receipt is missing")
    path = Path(path_value)
    if not path.is_absolute() or path.is_symlink():
        raise GpuLabUnavailable("CUDA controller lease receipt path is invalid")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GpuLabUnavailable("CUDA controller lease receipt is unreadable") from exc
    resource_ids = receipt.get("resource_ids") if isinstance(receipt, dict) else None
    exact_resources = tuple(sorted(f"accelerator:{value}" for value in expected))
    if (not isinstance(receipt, dict) or receipt.get("format") != "CUDA-FOREGROUND-LEASE/1"
            or receipt.get("state") != "active" or not isinstance(resource_ids, list)
            or tuple(sorted(resource_ids)) != exact_resources):
        raise GpuLabUnavailable("CUDA controller lease does not match the approved UUIDs")
    try:
        observed_project = Path(str(receipt.get("project_root", ""))).resolve(strict=True)
    except OSError as exc:
        raise GpuLabUnavailable("CUDA controller project identity is unavailable") from exc
    pid = receipt.get("pid")
    owner_id = receipt.get("owner_id")
    if (observed_project != project_root or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1
            or not isinstance(owner_id, str) or not owner_id.startswith("foreground:")):
        raise GpuLabUnavailable("CUDA controller lease identity is invalid")
    start = _process_start(pid)
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError as exc:
        raise GpuLabUnavailable("CUDA controller owner is no longer live") from exc
    if start is None or b"cuda_controller.py" not in command:
        raise GpuLabUnavailable("lease owner is not the live CUDA foreground controller")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if not visible or any(not item.isdecimal() for item in visible):
        raise GpuLabUnavailable("CUDA controller visible device list is invalid")
    indices = tuple(int(item) for item in visible)
    current_map = _gpu_map()
    if tuple(current_map.get(value, (None, None))[0] for value in expected) != indices:
        raise GpuLabUnavailable("CUDA visible devices do not map to the approved UUIDs")
    return ForegroundLease(path, owner_id, pid, start, observed_project, exact_resources, indices)


def _validate_owner_terminal(lease: ForegroundLease, owner: Mapping[str, object] | None,
                             expected: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(owner, Mapping):
        raise GpuLabUnavailable("native foreground owner record is unavailable")
    owner_kind = owner.get("owner_kind", owner.get("kind"))
    resources = owner.get("resources")
    exact_resources = tuple(sorted(f"accelerator:{value}" for value in expected))
    if (owner.get("id") != lease.owner_id or owner_kind != "foreground" or owner.get("state") != "released"
            or not isinstance(resources, list) or resources
            or owner.get("pid") != lease.pid or str(owner.get("process_start")) != lease.process_start
            or lease.resource_ids != exact_resources):
        raise GpuLabUnavailable("native foreground owner is not terminal for the exact controller process")
    if _process_start(lease.pid) is not None:
        raise GpuLabUnavailable("CUDA controller process remains live after foreground return")
    return {
        "owner_id": lease.owner_id, "pid": lease.pid, "process_start": lease.process_start,
        "gpu_uuids": list(expected), "resource_ids": list(lease.resource_ids),
        "state": "released", "resources": [], "verified": True,
    }


def _artifact_and_citations(proposal: LabProposalLike, proposal_root: Path,
                            snapshot_root: Path) -> tuple[list[dict[str, object]], list[Path]]:
    raw_artifacts = getattr(proposal, "artifacts", None)
    if not isinstance(raw_artifacts, (tuple, list)) or not raw_artifacts:
        raise GpuLabError("GPU proposal must contain at least one source artifact")
    files: list[dict[str, object]] = []
    source_files: list[Path] = []
    for item in raw_artifacts:
        rel = _safe_relative(getattr(item, "path", None), "proposal artifact")
        if rel.suffix != ".cu":
            raise GpuLabError("GPU LAB currently accepts CUDA .cu source artifacts only")
        path = proposal_root.joinpath(*rel.parts)
        if path.is_symlink():
            raise GpuLabError("proposal source artifacts may not be symlinks")
        try:
            resolved = path.resolve(strict=True)
            raw = resolved.read_bytes()
        except OSError as exc:
            raise GpuLabError("proposal source artifact is unavailable") from exc
        if not resolved.is_relative_to(proposal_root) or not resolved.is_file() or len(raw) > MAX_ARTIFACT_BYTES:
            raise GpuLabError("proposal source artifact escapes its approved root or exceeds the size limit")
        content = getattr(item, "content", None)
        expected = content.encode("utf-8") if isinstance(content, str) else content
        if not isinstance(expected, bytes) or raw != expected:
            raise GpuLabError("proposal source artifact bytes differ from the typed proposal")
        files.append({"path": rel.as_posix(), "sha256": _sha256(raw), "bytes": len(raw)})
        source_files.append(resolved)
    citations = getattr(proposal, "source_citations", None)
    if not isinstance(citations, (tuple, list)):
        raise GpuLabError("GPU proposal source citations are malformed")
    for item in citations:
        rel = _safe_relative(getattr(item, "path", None), "source citation")
        digest = getattr(item, "sha256", None)
        path = snapshot_root.joinpath(*rel.parts)
        if path.is_symlink() or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise GpuLabError("source citation identity is invalid")
        try:
            resolved = path.resolve(strict=True)
            raw = resolved.read_bytes()
        except OSError as exc:
            raise GpuLabError("cited snapshot source is unavailable") from exc
        if not resolved.is_relative_to(snapshot_root) or not resolved.is_file() or _sha256(raw) != digest:
            raise GpuLabError("cited source differs from the detached snapshot")
    return files, source_files


def _stage_proposal(source_files: Sequence[Path], records: Sequence[Mapping[str, object]],
                    attempt: Path) -> Path:
    """Copy only the selected immutable proposal files into the attempt."""
    staged = attempt / "proposal"
    staged.mkdir(mode=0o700)
    for source, record in zip(source_files, records, strict=True):
        rel = _safe_relative(record.get("path"), "proposal artifact")
        target = staged.joinpath(*rel.parts)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = source.read_bytes()
        if _sha256(raw) != record.get("sha256") or len(raw) != record.get("bytes"):
            raise GpuLabError("proposal artifact changed while staging")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        try:
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                target.unlink()
            except OSError:
                pass
            raise
    return staged.resolve(strict=True)


def _runtime_arguments(proposal: LabProposalLike) -> tuple[str, ...]:
    args = getattr(proposal, "argv", None)
    if not isinstance(args, (tuple, list)) or len(args) > 32:
        raise GpuLabError("GPU proposal runtime arguments must be a bounded string array")
    values = tuple(args)
    if not values or values[0] != "cuda":
        raise GpuLabError("GPU proposal argv must begin with the approved cuda tool identifier")
    values = values[1:]
    if any(not isinstance(arg, str) or "\0" in arg or len(arg.encode()) > 2048 for arg in values):
        raise GpuLabError("GPU proposal has an invalid runtime argument")
    if sum(len(arg.encode()) for arg in values) > MAX_ARG_BYTES:
        raise GpuLabError("GPU proposal runtime arguments exceed 12 KiB")
    return values


def _sandbox_command(bwrap: str, *, snapshot: Path, proposal_root: Path, toolchain: Path,
                     binary: Path, result: Path, source_names: Sequence[str], phase: str,
                     argv: Sequence[str], device_minors: Sequence[int], authorized_uuids: Sequence[str], artifact_bytes: int,
                     project_root: Path) -> list[str]:
    command = [bwrap, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
               "--unshare-net", "--cap-drop", "ALL", "--tmpfs", "/"]
    for base in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")):
        if base.exists():
            command.extend(("--ro-bind", str(base), str(base)))
    command.extend(("--dir", "/etc"))
    ld_cache = Path("/etc/ld.so.cache")
    if ld_cache.is_file():
        command.extend(("--ro-bind", str(ld_cache), "/etc/ld.so.cache"))
    command.extend(("--ro-bind", str(toolchain), str(toolchain), "--proc", "/proc", "--dev", "/dev",
                    "--tmpfs", "/tmp", "--size", str(MAX_ARTIFACT_BYTES), "--tmpfs", "/home",
                    "--tmpfs", "/run", "--dir", "/workspace", "--ro-bind", str(snapshot), "/workspace",
                    "--dir", "/proposal", "--ro-bind", str(proposal_root), "/proposal",
                    "--dir", "/artifacts", "--dir", "/tmp/home", "--chdir", "/workspace"))
    if phase == "build":
        command.extend(("--bind", str(binary), f"/artifacts/{PROGRAM_NAME}"))
    else:
        command.extend(("--ro-bind", str(binary), f"/artifacts/{PROGRAM_NAME}"))
    command.extend(("--bind", str(result), f"/artifacts/{ARTIFACT_NAME}", "--clearenv",
                    "--setenv", "PATH", f"{toolchain / 'bin'}:/usr/bin:/bin", "--setenv", "HOME", "/tmp/home",
                    "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"))
    if phase in {"probe", "run"}:
        for node in (Path("/dev/nvidiactl"), Path("/dev/nvidia-uvm"), Path("/dev/nvidia-uvm-tools")):
            if node.exists():
                command.extend(("--dev-bind", str(node), str(node)))
        for minor in device_minors:
            node = Path(f"/dev/nvidia{minor}")
            try:
                info = node.stat()
            except OSError as exc:
                raise GpuLabUnavailable(f"authorized NVIDIA device node {node.name} is unavailable") from exc
            if not stat.S_ISCHR(info.st_mode):
                raise GpuLabUnavailable("authorized NVIDIA device path is not a character device")
            command.extend(("--dev-bind", str(node), str(node)))
    if phase == "run":
        command.extend(("--setenv", "CUDA_VISIBLE_DEVICES", ",".join(authorized_uuids),
                        "--setenv", "CUDA_DEVICE_ORDER", "PCI_BUS_ID"))
        command.extend(("--", "/usr/bin/prlimit", f"--fsize={artifact_bytes}", "--nofile=64", "--", *argv))
    else:
        command.extend(("--", "/usr/bin/prlimit", f"--fsize={artifact_bytes}", "--nofile=64", "--", *argv))
    return command


def _current_parent() -> Path:
    try:
        return lab_runner._current_cgroup_parent()
    except lab_runner.ContainmentUnavailable as exc:
        raise GpuLabUnavailable("Project Control delegated cgroup parent is unavailable") from exc


def _new_cgroup(effect_id: str, limits: GpuLimits) -> tuple[Path, int]:
    parent = _current_parent()
    required = {"cpu", "memory", "pids"}
    controllers = set((parent / "cgroup.controllers").read_text().split())
    enabled = set((parent / "cgroup.subtree_control").read_text().replace("+", "").split())
    if not required <= controllers or not os.access(parent, os.W_OK | os.X_OK):
        raise GpuLabUnavailable("delegated cgroup lacks writable CPU, memory, and pid limits")
    if not required <= enabled:
        (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")
        enabled = set((parent / "cgroup.subtree_control").read_text().replace("+", "").split())
    if not required <= enabled or (parent / "cgroup.procs").read_text().split():
        raise GpuLabUnavailable("delegated cgroup controllers or empty-parent invariant failed")
    path = parent / ("pc-gpu-lab-" + hashlib.sha256(effect_id.encode()).hexdigest()[:24] + "-" + uuid.uuid4().hex[:6])
    try:
        path.mkdir(mode=0o700)
        (path / "memory.max").write_text(str(limits.memory_bytes))
        (path / "memory.swap.max").write_text("0")
        (path / "pids.max").write_text(str(limits.pids))
        quota = max(1, int(limits.cpu_cores * 100_000))
        (path / "cpu.max").write_text(f"{quota} 100000")
        if (path / "memory.swap.max").read_text().strip() != "0":
            raise OSError("swap limit was not applied")
        if not (path / "cgroup.kill").exists():
            raise OSError("cgroup.kill is unavailable")
        inode = path.stat().st_ino
    except OSError as exc:
        try:
            path.rmdir()
        except OSError:
            pass
        raise GpuLabUnavailable("failed to create fully limited GPU cgroup") from exc
    return path, inode


def _populated(path: Path) -> bool:
    try:
        for line in (path / "cgroup.events").read_text().splitlines():
            if line.startswith("populated "):
                return line.partition(" ")[2] == "1"
    except OSError:
        return True
    return True


def _kill_cgroup(path: Path, inode: int) -> bool:
    try:
        if path.stat().st_ino != inode:
            return False
        (path / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not _populated(path):
                return True
            time.sleep(0.01)
    except OSError:
        return False
    return not _populated(path)


def _remove_cgroup(path: Path, inode: int) -> bool:
    try:
        if path.stat().st_ino != inode or _populated(path):
            return False
        path.rmdir()
        return True
    except OSError:
        return False


def _run_contained(argv: Sequence[str], *, snapshot: Path, proposal_root: Path,
                   toolchain: Path, binary: Path, result_path: Path, source_names: Sequence[str],
                   phase: str, device_minors: Sequence[int], authorized_uuids: Sequence[str], effect_id: str, timeout: float,
                   limits: GpuLimits, on_started: Callable[[Mapping[str, object]], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> SandboxedPhase:
    if not argv or len(argv) > 64 or any(not isinstance(x, str) or not x or "\0" in x for x in argv):
        raise GpuLabError("contained command argv is invalid")
    phase_limit = limits.build_seconds if phase == "build" else (MAX_PROBE_SECONDS if phase == "probe" else limits.run_seconds)
    if timeout <= 0 or timeout > phase_limit:
        raise GpuLabError("contained phase deadline exceeds its approved limit")
    if result_path.exists() or result_path.is_symlink():
        raise GpuLabError("phase artifact path already exists")
    fd = os.open(result_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    bwrap = shutil.which("bwrap")
    if not bwrap or not lab_runner._sandbox_probe(bwrap):
        raise GpuLabUnavailable("required bubblewrap namespaces are unavailable")
    command = _sandbox_command(bwrap, snapshot=snapshot, proposal_root=proposal_root,
                               toolchain=toolchain, binary=binary, result=result_path,
                               source_names=source_names, phase=phase, argv=argv, device_minors=device_minors,
                               authorized_uuids=authorized_uuids, artifact_bytes=limits.artifact_bytes,
                               project_root=snapshot)
    cgroup, inode = _new_cgroup(effect_id, limits)
    gate_read = gate_write = -1
    try:
        gate_read, gate_write = os.pipe()
    except OSError as exc:
        cleanup = _kill_cgroup(cgroup, inode)
        removed = _remove_cgroup(cgroup, inode)
        if not cleanup or not removed:
            raise GpuLabUnavailable("failed to create process gate and cgroup cleanup is unverified") from exc
        raise GpuLabUnavailable("failed to create contained process gate") from exc
    wrapper = ("import os,sys; g=int(sys.argv[1]); p=sys.argv[2]; "
               "os.write(os.open(p,os.O_WRONLY|os.O_CLOEXEC),str(os.getpid()).encode()); "
               "t=os.read(g,1); os.close(g); os._exit(125) if t!=b'\\x01' else os.execv(sys.argv[3],sys.argv[3:])")
    launch = [sys.executable, "-c", wrapper, str(gate_read), str(cgroup / "cgroup.procs"), *command]
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    identity: dict[str, object] = {}
    streams: dict[object, bytearray] = {}
    cleanup = False
    status = "ok"
    try:
        process = subprocess.Popen(launch, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True,
                                   env={"PATH": "/usr/bin:/bin"}, pass_fds=(gate_read,))
        os.close(gate_read)
        start = _process_start(process.pid)
        if start is None:
            raise GpuLabUnavailable("contained runner process identity is unavailable")
        deadline_join = time.monotonic() + 1
        while str(process.pid) not in (cgroup / "cgroup.procs").read_text().split():
            if process.poll() is not None or time.monotonic() >= deadline_join:
                raise GpuLabUnavailable("contained runner did not enter its exact cgroup")
            time.sleep(0.005)
        identity = {"pid": process.pid, "process_start": start, "cgroup_path": str(cgroup),
                    "cgroup_inode": inode, "effect_id": effect_id}
        if on_started is not None:
            on_started(identity)
        os.write(gate_write, b"\x01")
        os.close(gate_write)
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        selector = selectors.DefaultSelector()
        for stream in streams:
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
        end = time.monotonic() + timeout
        combined = 0
        while True:
            if cancelled is not None and cancelled():
                status = "interrupted"
                break
            if time.monotonic() >= end:
                status = "timeout"
                break
            if process.poll() is not None and not selector.get_map():
                if _populated(cgroup):
                    status = "descendants_remaining"
                break
            for key, _ in selector.select(min(0.05, max(0.0, end - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), min(4096, limits.output_bytes - combined + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                keep = chunk[:max(0, limits.output_bytes - combined)]
                streams[key.fileobj].extend(keep)
                combined += len(chunk)
                if combined > limits.output_bytes:
                    status = "output_limit"
                    break
            if status != "ok":
                break
        if status != "ok" or _populated(cgroup):
            cleanup = _kill_cgroup(cgroup, inode)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            cleanup = _kill_cgroup(cgroup, inode) and cleanup
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                cleanup = False
        cleanup = cleanup or (not _populated(cgroup) and process.poll() is not None)
        selector.close()
        for stream in streams:
            if stream is not None:
                stream.close()
        artifact_bytes = result_path.stat().st_size
        if artifact_bytes > limits.artifact_bytes:
            status = "output_limit"
        digest = _sha256(result_path.read_bytes()) if artifact_bytes else None
        removed = _remove_cgroup(cgroup, inode)
        if not cleanup or not removed:
            status = "cleanup_unverified"
        return SandboxedPhase(phase, status if status != "ok" or process.returncode == 0 else "command_failed",
                              process.returncode, bytes(streams[process.stdout]), bytes(streams[process.stderr]),
                              round((time.monotonic() - started) * 1000, 3), result_path if artifact_bytes else None,
                              digest, artifact_bytes, identity, cleanup, removed)
    except BaseException:
        if process is not None:
            _kill_cgroup(cgroup, inode)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        raise
    finally:
        for descriptor in (gate_read, gate_write):
            if descriptor < 0:
                continue
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            cgroup_exists = cgroup.exists() and cgroup.stat().st_ino == inode
        except OSError:
            cgroup_exists = False
        if cgroup_exists and _populated(cgroup):
            _kill_cgroup(cgroup, inode)
        if cgroup_exists:
            _remove_cgroup(cgroup, inode)


def _source_name(path: Path, root: Path) -> str:
    return "/proposal/" + path.relative_to(root).as_posix()


class GpuLabExecutor:
    """Callable adapter for LabSessionService's injected ``gpu_executor`` seam.

    ``quiesce`` and ``resume`` are authenticated supervisor RPC hooks. The
    read-only ``owner_reader`` observes native CUDA host ownership after the
    foreground controller exits; the adapter never edits host resource state.
    """

    def __init__(self, *, project_roots: Mapping[str, str | Path],
                 cuda_controller: str | Path, quiesce: Callable[..., Mapping[str, object]],
                 resume: Callable[..., Mapping[str, object]],
                 owner_reader: Callable[[str], Mapping[str, object] | None],
                 controller_runner: Callable[[Path, Path, float], Mapping[str, object]] | None = None,
                 limits: GpuLimits = GpuLimits(), clock: Callable[[], float] = time.time):
        self.project_roots = {key: _safe_root(value, "registered project root") for key, value in project_roots.items()}
        self.cuda_controller = _safe_root(cuda_controller, "CUDA controller", directory=False)
        self.quiesce = quiesce
        self.resume = resume
        self.owner_reader = owner_reader
        self.controller_runner = controller_runner or self._run_controller
        self.limits = limits
        self.clock = clock

    @staticmethod
    def _run_controller(spec_path: Path, controller: Path, timeout: float) -> Mapping[str, object]:
        try:
            completed = subprocess.run(
                [sys.executable, str(controller), "run", "--spec", str(spec_path), "--json"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={key: value for key, value in os.environ.items()
                     if key not in {"PYTHONPATH", "PYTHONHOME", "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"}},
                timeout=max(1.0, timeout), check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GpuLabUnavailable("CUDA foreground controller failed to return") from exc
        if len(completed.stdout) > MAX_CONTROLLER_RESPONSE_BYTES or len(completed.stderr) > MAX_OUTPUT_BYTES:
            raise GpuLabUnavailable("CUDA controller output exceeded its bound")
        try:
            payload = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise GpuLabUnavailable("CUDA foreground controller returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise GpuLabUnavailable("CUDA foreground controller did not complete successfully")
        if completed.returncode != 0 and payload.get("ok") is True:
            raise GpuLabUnavailable("CUDA controller returned success with a nonzero process status")
        return {**payload, "_controller_returncode": completed.returncode,
                "_controller_stderr_sha256": _sha256(completed.stderr)}

    def __call__(self, scope: LabScopeLike, proposal: LabProposalLike, snapshot_root: Path,
                 proposal_root: Path, *, session_id: str, effect_id: str, deadline: float,
                 cancelled: Callable[[], bool]) -> Mapping[str, Any]:
        return self.execute(scope, proposal, snapshot_root, proposal_root, session_id=session_id,
                            effect_id=effect_id, deadline=deadline, cancelled=cancelled)

    def execute(self, scope: LabScopeLike, proposal: LabProposalLike, snapshot_root: Path,
                proposal_root: Path, *, session_id: str, effect_id: str, deadline: float,
                cancelled: Callable[[], bool] = lambda: False,
                checkpoint: Callable[[str, Mapping[str, object]], None] | None = None) -> Mapping[str, Any]:
        uuids = _approved_uuids(scope)
        project_key = getattr(scope, "project", None)
        project = self.project_roots.get(project_key) if isinstance(project_key, str) else None
        if project is None:
            raise GpuLabError("GPU scope project is not registered")
        toolchain_raw = getattr(scope, "toolchain_root", None)
        if not isinstance(toolchain_raw, str) or not toolchain_raw:
            raise GpuLabError("GPU scope must select a CUDA toolchain")
        toolchain = _safe_root(toolchain_raw, "approved CUDA toolchain")
        compiler = toolchain / "bin" / "nvcc"
        if compiler.is_symlink() or not compiler.is_file() or not os.access(compiler, os.X_OK):
            raise GpuLabError("approved CUDA toolchain has no executable bin/nvcc")
        snapshot = _safe_root(snapshot_root, "detached source snapshot")
        proposal_dir = _safe_root(proposal_root, "proposal artifact directory")
        if snapshot == proposal_dir or snapshot in proposal_dir.parents or proposal_dir in snapshot.parents:
            raise GpuLabError("snapshot and proposal artifact roots must be separate")
        files, source_files = _artifact_and_citations(proposal, proposal_dir, snapshot)
        runtime_args = _runtime_arguments(proposal)
        attempt_base = proposal_dir.parent / "gpu-attempts"
        if attempt_base.is_symlink():
            raise GpuLabError("private GPU attempt root must not be a symlink")
        attempt = _private_root(attempt_base /
                                ("gpu-lab-" + hashlib.sha256(f"{session_id}:{effect_id}".encode()).hexdigest()[:24]))
        if attempt == snapshot or attempt in snapshot.parents or snapshot in attempt.parents:
            raise GpuLabError("attempt root overlaps the source snapshot")
        phase_root = attempt / "phases"
        phase_root.mkdir(mode=0o700, exist_ok=True)
        staged_proposal = _stage_proposal(source_files, files, attempt)
        binary = attempt / PROGRAM_NAME
        build_artifact = phase_root / "build-output.bin"
        run_artifact = phase_root / ARTIFACT_NAME
        if binary.exists() or binary.is_symlink():
            raise GpuLabError("GPU attempt binary path already exists")
        fd = os.open(binary, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o700)
        os.close(fd)
        build_intent = {
            "format": "PC-GPU-LAB-PHASE-INTENT/1", "phase": "build", "effect_id": effect_id,
            "session_id": session_id, "scope_project": project_key, "gpu_uuids": list(uuids),
            "snapshot_root": str(snapshot), "proposal_root": str(staged_proposal),
            "proposal_artifacts": files, "created_at": self.clock(),
        }
        _atomic_private_json(phase_root / "build-intent.json", build_intent, create_only=True)
        if cancelled():
            raise GpuLabCancelled("GPU LAB was cancelled before foreground resource release")
        resource_ids = sorted(f"accelerator:{value}" for value in uuids)
        remaining = float(deadline) - self.clock()
        if remaining <= 0:
            raise GpuLabCancelled("GPU LAB session deadline expired before execution")
        quiesce = self.quiesce(request_id=effect_id, resource_ids=resource_ids, deadline_epoch=deadline)
        quiesce = dict(quiesce)
        if (quiesce.get("format") != "PC-MODEL-FOREGROUND-HANDOFF/1" or quiesce.get("status") != "quiesced"
                or quiesce.get("request_id") != effect_id or sorted(quiesce.get("resource_ids", [])) != resource_ids):
            raise GpuLabUnavailable("model residency did not provide an exact verified quiescence receipt")
        continuation_id = quiesce.get("continuation_id")
        slots = quiesce.get("slots")
        if not isinstance(continuation_id, str) or not continuation_id or not isinstance(slots, list):
            raise GpuLabUnavailable("model handoff receipt lacks continuation or slot identities")
        seen_slots: set[str] = set()
        for slot in slots:
            if (not isinstance(slot, Mapping) or not isinstance(slot.get("slot_id"), str)
                    or slot["slot_id"] in seen_slots or slot.get("released") is not True
                    or slot.get("process_released") is not True or slot.get("memory_released") is not True
                    or slot.get("host_released") is not True):
                raise GpuLabUnavailable("model handoff slot lacks exact release proof")
            seen_slots.add(str(slot["slot_id"]))
            slot_gpus = slot.get("gpu_uuids")
            if not isinstance(slot_gpus, list) or not set(slot_gpus) <= set(uuids):
                raise GpuLabUnavailable("model handoff slot exceeds the approved GPU UUID scope")
        _atomic_private_json(phase_root / "quiesced.json", quiesce, create_only=True)
        if checkpoint:
            checkpoint("quiesced", quiesce)
        if cancelled():
            raise GpuLabCancelled("GPU LAB was cancelled after model resources were released")
        remaining = float(deadline) - self.clock()
        if remaining <= 0:
            raise GpuLabCancelled("GPU LAB session deadline expired while yielding model resources")
        source_names = [_source_name(path, proposal_dir) for path in source_files]
        source_scope = getattr(scope, "source_paths", None)
        if not isinstance(source_scope, (tuple, list)) or not source_scope:
            raise GpuLabError("GPU scope must name at least one approved repository source path")
        source_paths = [str(_safe_relative(item, "approved scope source path")) for item in source_scope]
        build_command = [str(compiler), "-std=c++17", "-O2", "-lineinfo", "-I/workspace",
                         "-Xcompiler=-fno-omit-frame-pointer", "-arch=sm_70", "-o",
                         f"/artifacts/{PROGRAM_NAME}", *source_names]
        payload_request = {
            "format": "PC-GPU-LAB-PAYLOAD/1", "project_root": str(project), "gpu_uuids": list(uuids),
            "deadline_epoch": float(deadline),
            "snapshot_root": str(snapshot), "proposal_root": str(staged_proposal), "toolchain_root": str(toolchain),
            "binary": str(binary), "build_artifact": str(build_artifact), "run_artifact": str(run_artifact),
            "source_names": source_names, "runtime_args": list(runtime_args), "effect_id": effect_id,
            "limits": {"cpu_cores": self.limits.cpu_cores, "memory_bytes": self.limits.memory_bytes,
                       "pids": self.limits.pids, "build_seconds": self.limits.build_seconds,
                       "run_seconds": self.limits.run_seconds, "output_bytes": self.limits.output_bytes,
                       "artifact_bytes": self.limits.artifact_bytes},
            "build_argv": build_command,
        }
        request_path = attempt / "payload-request.json"
        _atomic_private_json(request_path, payload_request, create_only=True)
        spec = {
            "schema_version": 1, "project_root": str(project), "command_cwd": str(project),
            "paths": source_paths,
            "argv": [sys.executable, "-I", str(Path(__file__).resolve()), "payload", str(request_path)],
            "benchmark": {"build_argv": [sys.executable, "-I", str(Path(__file__).resolve()),
                                           "build", str(request_path)]},
            "build_timeout": min(self.limits.build_seconds, remaining),
            "resources": {"gpus": len(uuids), "gpu_uuids": list(uuids), "cpu_threads": int(self.limits.cpu_cores),
                          "ram_bytes": self.limits.memory_bytes},
            "timeout": remaining,
            "preempt_grace_seconds": min(30, max(1, remaining)),
        }
        spec_path = attempt / "cuda-controller-spec.json"
        _atomic_private_json(spec_path, spec, create_only=True)
        if checkpoint:
            checkpoint("foreground_prepared", {"spec_sha256": _sha256(spec_path.read_bytes()),
                                                "request_sha256": _sha256(request_path.read_bytes())})
        lease: ForegroundLease | None = None
        controller_result: Mapping[str, object] | None = None
        execution_error: BaseException | None = None
        probe: Mapping[str, object] | None = None
        build: Mapping[str, object] | None = None
        run: Mapping[str, object] | None = None
        terminal: dict[str, object] | None = None
        resume_receipt: Mapping[str, object] | None = None
        try:
            controller_result = self.controller_runner(spec_path, self.cuda_controller, remaining)
            build_receipt_path = phase_root / "build-result.json"
            if build_receipt_path.exists():
                build_record = json.loads(build_receipt_path.read_text(encoding="utf-8"))
                build = build_record.get("build") if isinstance(build_record, dict) and isinstance(build_record.get("build"), dict) else None
            lease_path = controller_result.get("lease_receipt")
            if isinstance(lease_path, str):
                # The payload records the live lease before it starts either phase.
                lease_record_path = phase_root / "foreground-lease.json"
                if lease_record_path.exists():
                    lease = _lease_from_mapping(json.loads(lease_record_path.read_text()), project)
                    if Path(lease_path).resolve(strict=True) != lease.path.resolve(strict=True):
                        raise GpuLabUnavailable("controller response lease receipt differs from the payload receipt")
            payload_receipt = phase_root / "payload-result.json"
            if payload_receipt.exists():
                payload = json.loads(payload_receipt.read_text(encoding="utf-8"))
                probe = payload.get("probe") if isinstance(payload.get("probe"), dict) else None
                run = payload.get("run") if isinstance(payload.get("run"), dict) else None
                if lease is None and isinstance(payload.get("lease"), dict):
                    lease = _lease_from_mapping(payload["lease"], project)
            if lease is not None:
                owner = self.owner_reader(lease.owner_id)
                terminal = _validate_owner_terminal(lease, owner, uuids)
                _atomic_private_json(phase_root / "foreground-terminal.json", terminal, create_only=True)
                if checkpoint:
                    checkpoint("foreground_terminal", terminal)
            else:
                if (isinstance(controller_result, Mapping)
                        and controller_result.get("code") in NO_FOREGROUND_CODES
                        and not controller_result.get("lease_receipt")
                        and not (phase_root / "foreground-lease.json").exists()):
                    execution_error = None
                else:
                    raise GpuLabUnavailable("controller returned without a payload lease receipt")
            if not isinstance(controller_result, Mapping) or controller_result.get("ok") is not True:
                code = controller_result.get("code") if isinstance(controller_result, Mapping) else None
                if (lease is None and isinstance(build, Mapping) and build.get("cleanup_verified") is True
                        and build.get("cgroup_removed") is True and code == "foreground_build_failed"):
                    execution_error = None
                elif not isinstance(probe, Mapping) and not isinstance(build, Mapping) and not isinstance(run, Mapping):
                    raise GpuLabUnavailable("CUDA controller failed before returning contained phase evidence")
        except BaseException as exc:
            execution_error = exc
        finally:
            # Native admission/release is delegated to the authenticated RPC; no retry on failure.
            phase_records = [value for value in (probe, build, run) if isinstance(value, Mapping)]
            phases_safe = bool(phase_records) and all(value.get("cleanup_verified") is True
                                                       and value.get("cgroup_removed") is True
                                                       for value in phase_records)
            no_foreground_started = (lease is None and isinstance(controller_result, Mapping)
                                     and controller_result.get("code") in NO_FOREGROUND_CODES
                                     and not controller_result.get("lease_receipt")
                                     and not (phase_root / "foreground-lease.json").exists())
            no_effect: dict[str, object] | None = None
            if no_foreground_started:
                code = controller_result.get("code") if isinstance(controller_result, Mapping) else None
                no_effect = {
                    "format": "PC-GPU-LAB-NO-FOREGROUND-EFFECT/1", "effect_id": effect_id,
                    "controller_code": code, "controller_returned": True,
                    "payload_started": False, "lease_receipt": None,
                    "native_owner_claimed": False,
                }
                _atomic_private_json(phase_root / "no-foreground-effect.json", no_effect, create_only=True)
                if checkpoint:
                    checkpoint("no_foreground_effect", no_effect)
            if (terminal is not None and phases_safe) or no_foreground_started:
                try:
                    resume_receipt = self.resume(
                        request_id=effect_id, continuation_id=continuation_id,
                        resource_ids=resource_ids, deadline_epoch=deadline, veto=bool(cancelled()),
                    )
                    resume_receipt = dict(resume_receipt)
                    if (resume_receipt.get("request_id") != effect_id
                            or resume_receipt.get("continuation_id") != continuation_id
                            or sorted(resume_receipt.get("resource_ids", [])) != resource_ids
                            or resume_receipt.get("status") not in {"resumed", "partial", "pending", "vetoed"}):
                        raise GpuLabUnavailable("model resume receipt does not match its continuation")
                    _atomic_private_json(phase_root / "resume.json", resume_receipt, create_only=True)
                    if checkpoint:
                        checkpoint("resumed" if resume_receipt.get("status") != "vetoed" else "resume_vetoed", resume_receipt)
                except BaseException as exc:
                    execution_error = execution_error or GpuLabUnavailable("model resource rewarm could not be verified")
        if execution_error is not None:
            raise execution_error
        if resume_receipt is None:
            raise GpuLabUnavailable("GPU experiment cannot be interpreted before native cleanup and rewarm proofs")
        if terminal is None:
            controller_code = controller_result.get("code") if isinstance(controller_result, Mapping) else None
            return {
                "format": "PC-GPU-LAB-RESULT/1", "status": "not_started",
                "phase": "build" if controller_code == "foreground_build_failed" else "admission",
                "effect_id": effect_id, "scope_project": project_key, "gpu_uuids": list(uuids),
                "proposal_artifacts": files, "build": dict(build or {}), "probe": None, "run": None,
                "controller": {"code": controller_code,
                               "returned": True,
                               "admission_started": _controller_admission_started(controller_code)},
                "payload_started": False,
                "no_foreground_effect": True, "no_effect_receipt": no_effect, "foreground_terminal": None,
                "resume": dict(resume_receipt),
                "cleanup_verified": bool(build and build.get("cleanup_verified") and build.get("cgroup_removed")),
                "attempt_root": str(attempt),
            }
        phase_status = "completed" if isinstance(run, Mapping) and run.get("status") == "ok" else (
            "containment_rejected" if isinstance(probe, Mapping) and probe.get("status") != "ok" else
            "experiment_failed")
        return {
            "format": "PC-GPU-LAB-RESULT/1", "status": phase_status, "effect_id": effect_id,
            "scope_project": project_key, "gpu_uuids": list(uuids),
            "proposal_artifacts": files, "hypothesis": str(getattr(proposal, "hypothesis", "")),
            "measurements": list(getattr(proposal, "measurements", ())),
            "stop_rule": str(getattr(proposal, "stop_rule", "")), "probe": dict(probe or {}),
            "build": dict(build or {}), "run": dict(run) if isinstance(run, Mapping) else None,
            "foreground_terminal": terminal,
            "resume": dict(resume_receipt), "cleanup_verified": bool(phase_records and phases_safe
                                                                         and terminal.get("verified")),
            "attempt_root": str(attempt),
        }


def _lease_from_mapping(value: Mapping[str, object], project_root: Path) -> ForegroundLease:
    """Reconstruct the internally persisted, independently checked live lease."""
    expected = value.get("gpu_uuids")
    resource_ids = value.get("resource_ids")
    if (not isinstance(expected, list) or not expected or not isinstance(resource_ids, list)
            or tuple(sorted(resource_ids)) != tuple(sorted(f"accelerator:{item}" for item in expected))):
        raise GpuLabUnavailable("persisted CUDA lease does not match approved resources")
    pid = value.get("pid")
    owner = value.get("owner_id")
    start = value.get("process_start")
    raw_path = value.get("path")
    indices = value.get("visible_indices")
    if (isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(owner, str)
            or not isinstance(start, str) or not isinstance(raw_path, str) or not isinstance(indices, list)):
        raise GpuLabUnavailable("persisted CUDA lease identity is malformed")
    receipt_path = Path(raw_path)
    if not receipt_path.is_absolute() or receipt_path.is_symlink():
        raise GpuLabUnavailable("persisted CUDA lease receipt path is invalid")
    try:
        receipt_path = receipt_path.resolve(strict=True)
    except OSError as exc:
        raise GpuLabUnavailable("persisted CUDA lease receipt is unavailable") from exc
    if _process_start(pid) == start:
        raise GpuLabUnavailable("CUDA controller has not returned; native terminal state cannot be proven")
    return ForegroundLease(receipt_path, owner, pid, start, project_root,
                           tuple(sorted(resource_ids)), tuple(indices))


def _negative_cuda_probe(ungranted_nodes: Sequence[int]) -> str:
    """Trusted driver probe. It fails if an ungranted minor can open or allocate."""
    denied = json.dumps(list(ungranted_nodes), separators=(",", ":"))
    return (
        "import ctypes,json,os,sys\n"
        f"for i in {denied}:\n"
        " p=f'/dev/nvidia{i}'\n"
        " try:\n"
        "  fd=os.open(p,os.O_RDWR|os.O_CLOEXEC); os.close(fd); print('ungranted_device_node_opened:'+p); sys.exit(41)\n"
        " except (FileNotFoundError,PermissionError): pass\n"
        " except OSError as e:\n"
        "  if e.errno not in (2,13): raise\n"
        "lib=ctypes.CDLL('libcuda.so.1')\n"
        "def sym(*names):\n"
        " for name in names:\n"
        "  if hasattr(lib,name): return getattr(lib,name)\n"
        " raise RuntimeError('missing_cuda_driver_symbol:'+','.join(names))\n"
        "init=sym('cuInit'); init.argtypes=[ctypes.c_uint]; init.restype=ctypes.c_int\n"
        "if init(0)!=0: raise RuntimeError('cuda_driver_init_failed')\n"
        "count=ctypes.c_int(); get_count=sym('cuDeviceGetCount'); get_count.argtypes=[ctypes.POINTER(ctypes.c_int)]; get_count.restype=ctypes.c_int\n"
        "if get_count(ctypes.byref(count))!=0: raise RuntimeError('cuda_device_count_failed')\n"
        "get_uuid=sym('cuDeviceGetUuid_v2','cuDeviceGetUuid'); get_uuid.argtypes=[ctypes.c_void_p,ctypes.c_int]; get_uuid.restype=ctypes.c_int\n"
        "get_device=sym('cuDeviceGet'); get_device.argtypes=[ctypes.POINTER(ctypes.c_int),ctypes.c_int]; get_device.restype=ctypes.c_int\n"
        "create=sym('cuCtxCreate_v2','cuCtxCreate'); create.argtypes=[ctypes.POINTER(ctypes.c_void_p),ctypes.c_uint,ctypes.c_int]; create.restype=ctypes.c_int\n"
        "alloc=sym('cuMemAlloc_v2','cuMemAlloc'); alloc.argtypes=[ctypes.POINTER(ctypes.c_ulonglong),ctypes.c_size_t]; alloc.restype=ctypes.c_int\n"
        "free=sym('cuMemFree_v2','cuMemFree'); free.argtypes=[ctypes.c_ulonglong]; free.restype=ctypes.c_int\n"
        "destroy=sym('cuCtxDestroy_v2','cuCtxDestroy'); destroy.argtypes=[ctypes.c_void_p]; destroy.restype=ctypes.c_int\n"
        "authorized={bytes.fromhex(x.removeprefix('GPU-').replace('-','')) for x in sys.argv[1:]}; seen=set()\n"
        "for ordinal in range(count.value):\n"
        " raw=(ctypes.c_ubyte*16)()\n"
        " if get_uuid(ctypes.byref(raw),ordinal)!=0: raise RuntimeError('cuda_uuid_probe_failed')\n"
        " if bytes(raw) in authorized: seen.add(bytes(raw)); continue\n"
        " device=ctypes.c_int()\n"
        " if get_device(ctypes.byref(device),ordinal)!=0: raise RuntimeError('cuda_device_lookup_failed')\n"
        " context=ctypes.c_void_p()\n"
        " if create(ctypes.byref(context),0,device.value)==0:\n"
        "  address=ctypes.c_ulonglong()\n"
        "  alloc_rc=alloc(ctypes.byref(address),1)\n"
        "  if alloc_rc==0: free(address.value)\n"
        "  destroy(context); print('ungranted_cuda_context_succeeded'); sys.exit(42)\n"
        "if seen != authorized: raise RuntimeError('approved_cuda_uuid_not_visible')\n"
        "print('ungranted_cuda_access_blocked')\n"
    )


def native_owner_reader(owner_id: str) -> Mapping[str, object] | None:
    """Read an exact native host owner through the installed Todo façade."""
    from todo_orchestrator.background.host import HostCoordinator
    from todo_orchestrator.runtime.facade import HostResourceFacade

    return HostResourceFacade(HostCoordinator(create=False)).owner(owner_id)


def build_main(request_path: str) -> int:
    """Trusted CUDA-controller build hook; it runs before GPU reservation."""
    path = Path(request_path)
    if not path.is_absolute() or path.is_symlink():
        raise GpuLabUnavailable("GPU build request path is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != "PC-GPU-LAB-PAYLOAD/1":
        raise GpuLabUnavailable("GPU build request format is invalid")
    snapshot = _safe_root(str(payload["snapshot_root"]), "snapshot")
    proposal = _safe_root(str(payload["proposal_root"]), "proposal root")
    toolchain = _safe_root(str(payload["toolchain_root"]), "toolchain")
    compiler = toolchain / "bin" / "nvcc"
    if compiler.is_symlink() or not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise GpuLabUnavailable("approved CUDA compiler is unavailable")
    binary = Path(str(payload["binary"]))
    build_artifact = Path(str(payload["build_artifact"]))
    if not binary.is_absolute() or not binary.is_file() or binary.is_symlink():
        raise GpuLabUnavailable("private CUDA binary destination was not precreated")
    limits = GpuLimits(**payload["limits"])
    deadline_epoch = payload.get("deadline_epoch")
    if isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float)):
        raise GpuLabUnavailable("GPU build request has no finite scope deadline")
    source_names = tuple(payload["source_names"])
    for name in source_names:
        if not isinstance(name, str) or not name.startswith("/proposal/") or ".." in PurePosixPath(name).parts:
            raise GpuLabUnavailable("GPU build source path escapes proposal artifacts")
    expected_argv = [str(compiler), "-std=c++17", "-O2", "-lineinfo", "-I/workspace",
                     "-Xcompiler=-fno-omit-frame-pointer", "-arch=sm_70", "-o",
                     f"/artifacts/{PROGRAM_NAME}", *source_names]
    if payload.get("build_argv") != expected_argv:
        raise GpuLabUnavailable("GPU compiler command differs from the fixed trusted build contract")
    phase_root = path.parent / "phases"
    _atomic_private_json(phase_root / "build-running.json", {
        "format": "PC-GPU-LAB-PHASE-INTENT/1", "phase": "build",
        "effect_id": str(payload["effect_id"]) + "-build", "created_at": time.time(),
    }, create_only=True)
    try:
        phase_timeout = _phase_timeout(limits.build_seconds, float(deadline_epoch))
    except GpuLabUnavailable:
        record = {"format": "PC-GPU-LAB-BUILD/1", "status": "deadline_before_build",
                  "build": {"phase": "build", "status": "deadline_before_start", "returncode": None,
                            "cleanup_verified": True, "cgroup_removed": True,
                            "process_identity": {}, "elapsed_ms": 0},
                  "binary_sha256": None, "cleanup_verified": True, "cgroup_removed": True}
        _atomic_private_json(phase_root / "build-result.json", record, create_only=True)
        return 1
    build = _run_contained(expected_argv, snapshot=snapshot, proposal_root=proposal, toolchain=toolchain,
                           binary=binary, result_path=build_artifact, source_names=source_names,
                           phase="build", device_minors=(), authorized_uuids=tuple(payload["gpu_uuids"]),
                           effect_id=str(payload["effect_id"])+"-build", timeout=phase_timeout,
                           limits=limits, on_started=lambda identity: _atomic_private_json(
                               phase_root / "build-process.json", identity, create_only=True))
    binary_digest = _sha256(binary.read_bytes()) if binary.is_file() else None
    if build.status == "ok" and build.cleanup_verified and build.cgroup_removed:
        binary.chmod(0o500)
    record = {
        "format": "PC-GPU-LAB-BUILD/1", "status": build.status,
        "build": build.to_dict(), "binary_sha256": binary_digest,
        "cleanup_verified": build.cleanup_verified, "cgroup_removed": build.cgroup_removed,
    }
    _atomic_private_json(phase_root / "build-result.json", record, create_only=True)
    return 0 if build.status == "ok" and build.cleanup_verified and build.cgroup_removed else 1


def payload_main(request_path: str) -> int:
    """Trusted command called by the CUDA controller after foreground admission."""
    path = Path(request_path)
    if not path.is_absolute() or path.is_symlink():
        raise GpuLabUnavailable("GPU payload request path is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != "PC-GPU-LAB-PAYLOAD/1":
        raise GpuLabUnavailable("GPU payload request format is invalid")
    uuids = tuple(payload.get("gpu_uuids", ()))
    if not uuids or any(not isinstance(item, str) or not GPU_UUID.fullmatch(item) for item in uuids):
        raise GpuLabUnavailable("GPU payload request UUID list is invalid")
    project = _safe_root(str(payload["project_root"]), "registered project root")
    lease = _validate_lease(os.environ.get("TODO_GPU_LEASE_RECEIPT"), uuids, project)
    attempt = _safe_root(path.parent, "private GPU attempt directory")
    phase_root = attempt / "phases"
    _atomic_private_json(phase_root / "foreground-lease.json", {
        "path": str(lease.path), "owner_id": lease.owner_id, "pid": lease.pid, "process_start": lease.process_start,
        "project_root": str(lease.project_root), "gpu_uuids": list(uuids),
        "resource_ids": list(lease.resource_ids), "visible_indices": list(lease.visible_indices),
    }, create_only=True)
    snapshot = _safe_root(str(payload["snapshot_root"]), "snapshot")
    proposal = _safe_root(str(payload["proposal_root"]), "proposal root")
    toolchain = _safe_root(str(payload["toolchain_root"]), "toolchain")
    binary = Path(str(payload["binary"]))
    build_artifact = Path(str(payload["build_artifact"]))
    run_artifact = Path(str(payload["run_artifact"]))
    limits = GpuLimits(**payload["limits"])
    deadline_epoch = payload.get("deadline_epoch")
    if isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float)):
        raise GpuLabUnavailable("GPU payload request has no finite scope deadline")
    source_names = tuple(payload["source_names"])
    build_record_path = path.parent / "phases" / "build-result.json"
    if not build_record_path.is_file() or build_record_path.is_symlink():
        raise GpuLabUnavailable("CUDA build receipt is missing before foreground payload")
    build_record = json.loads(build_record_path.read_text(encoding="utf-8"))
    if (not isinstance(build_record, dict) or build_record.get("status") != "ok"
            or build_record.get("cleanup_verified") is not True or build_record.get("cgroup_removed") is not True
            or not binary.is_file() or binary.is_symlink()
            or _sha256(binary.read_bytes()) != build_record.get("binary_sha256")):
        raise GpuLabUnavailable("CUDA build receipt or immutable binary identity is invalid")
    build = build_record.get("build")
    if not isinstance(build, dict):
        raise GpuLabUnavailable("CUDA build receipt has no contained process evidence")
    for name in source_names:
        if not isinstance(name, str) or not name.startswith("/proposal/") or ".." in PurePosixPath(name).parts:
            raise GpuLabUnavailable("GPU build source path escapes proposal artifacts")
    mapping = _gpu_map()
    selected_minors, ungranted_nodes = _resolve_device_minors(mapping, uuids, lease.visible_indices)
    _atomic_private_json(phase_root / "negative-probe-intent.json", {
        "format": "PC-GPU-LAB-PHASE-INTENT/1", "phase": "negative_isolation_probe",
        "effect_id": str(payload["effect_id"]) + "-probe", "gpu_uuids": list(uuids),
        "ungranted_uuids": sorted(value for value in mapping if value not in uuids),
        "device_map": {key: {"cuda_index": value[0], "linux_minor": value[1]}
                       for key, value in mapping.items()}, "created_at": time.time(),
    }, create_only=True)
    probe_result = phase_root / "negative-probe-output.bin"
    probe = _run_contained(["/usr/bin/python3", "-c", _negative_cuda_probe(ungranted_nodes), *uuids],
                           snapshot=snapshot, proposal_root=proposal, toolchain=toolchain,
                           binary=binary, result_path=probe_result, source_names=source_names,
                           phase="probe", device_minors=selected_minors, authorized_uuids=uuids,
                           effect_id=str(payload["effect_id"])+"-probe",
                           timeout=min(MAX_PROBE_SECONDS, _phase_timeout(MAX_PROBE_SECONDS, float(deadline_epoch))),
                           limits=limits, on_started=lambda identity: _atomic_private_json(
                               phase_root / "negative-probe-process.json", identity, create_only=True))
    if probe.status != "ok" or not probe.cleanup_verified or not probe.cgroup_removed:
        _atomic_private_json(phase_root / "payload-result.json", {"lease": _lease_json(lease),
                                                                    "probe": probe.to_dict(), "build": None, "run": None})
        return 1
    # Persist verified probe cleanup before starting proposal code. If the
    # remaining scope deadline expires at the run boundary, the controller
    # can still report a terminal lease and this no-run receipt for rewarm.
    _atomic_private_json(phase_root / "payload-result.json", {"lease": _lease_json(lease),
                                                                "probe": probe.to_dict(), "build": build,
                                                                "run": None})
    try:
        binary.chmod(0o500)
    except OSError as exc:
        raise GpuLabUnavailable("compiled CUDA artifact could not be sealed read-only") from exc
    _atomic_private_json(phase_root / "run-intent.json", {
        "format": "PC-GPU-LAB-PHASE-INTENT/1", "phase": "run",
        "effect_id": str(payload["effect_id"])+"-run", "created_at": time.time(),
    }, create_only=True)
    executable = ("/artifacts/" + PROGRAM_NAME, *payload.get("runtime_args", ()))
    run = _run_contained(executable, snapshot=snapshot, proposal_root=proposal, toolchain=toolchain,
                         binary=binary, result_path=run_artifact, source_names=source_names,
                         phase="run", device_minors=selected_minors, authorized_uuids=uuids,
                         effect_id=str(payload["effect_id"])+"-run",
                         timeout=_phase_timeout(limits.run_seconds, float(deadline_epoch)), limits=limits,
                         on_started=lambda identity: _atomic_private_json(
                             phase_root / "run-process.json", identity, create_only=True))
    _atomic_private_json(phase_root / "payload-result.json", {"lease": _lease_json(lease),
                                                                "probe": probe.to_dict(), "build": build,
                                                                "run": run.to_dict()})
    return 0 if (probe.status == "ok" and probe.cleanup_verified and probe.cgroup_removed
                 and run.status == "ok" and run.cleanup_verified and run.cgroup_removed) else 1


def _lease_json(lease: ForegroundLease) -> dict[str, object]:
    return {"path": str(lease.path), "owner_id": lease.owner_id, "pid": lease.pid, "process_start": lease.process_start,
            "project_root": str(lease.project_root), "gpu_uuids": [x.removeprefix("accelerator:") for x in lease.resource_ids],
            "resource_ids": list(lease.resource_ids), "visible_indices": list(lease.visible_indices)}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "payload":
        try:
            return payload_main(args[1])
        except BaseException as exc:
            sys.stderr.write(f"GPU LAB payload rejected: {type(exc).__name__}: {exc}\n")
            return 2
    if len(args) == 2 and args[0] == "build":
        try:
            return build_main(args[1])
        except BaseException as exc:
            sys.stderr.write(f"GPU LAB build rejected: {type(exc).__name__}: {exc}\n")
            return 2
    sys.stderr.write("lab_gpu.py is an internal CUDA-controller payload adapter\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
