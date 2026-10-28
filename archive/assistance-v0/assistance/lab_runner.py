"""Fail-closed execution for bounded Project Control scratch experiments.

The runner accepts an already captured source directory. It never makes or
repairs a snapshot, and it never applies scratch output to that source.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import time
from typing import Callable, Literal, Sequence
import uuid


CGROUP_ROOT = Path("/sys/fs/cgroup")
MAX_CPU_CORES = 1.0
MAX_MEMORY_BYTES = 1024 * 1024 * 1024
MAX_PIDS = 32
MAX_WALL_SECONDS = 30.0
MAX_OUTPUT_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARG_BYTES = 12 * 1024
ARTIFACT_NAME = "result.bin"


class LabRunnerError(RuntimeError):
    """Base class for bounded runner errors."""


class ContainmentUnavailable(LabRunnerError):
    """Raised when the requested isolation cannot be proven on this host."""


class EffectCleanupUnverified(ContainmentUnavailable):
    """An effect identity exists, but its process cleanup is not proven."""

    def __init__(self, identity: "OwnedProcessIdentity") -> None:
        super().__init__("effect cleanup is unverified; retain the intent as unknown")
        self.identity = identity


class InvalidExecutionRequest(LabRunnerError):
    """Raised for an invalid command, grant, snapshot or attempt directory."""


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """Explicit CPU-only limits; values may be lowered but never raised."""

    cpu_cores: float = 1.0
    memory_bytes: int = MAX_MEMORY_BYTES
    pids: int = MAX_PIDS
    timeout_seconds: float = MAX_WALL_SECONDS
    output_bytes: int = MAX_OUTPUT_BYTES
    artifact_bytes: int = MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if not 0 < self.cpu_cores <= MAX_CPU_CORES:
            raise InvalidExecutionRequest("cpu grant must be in (0, 1] cores")
        if not 1 <= self.memory_bytes <= MAX_MEMORY_BYTES:
            raise InvalidExecutionRequest("memory grant exceeds the 1 GiB ceiling")
        if not 1 <= self.pids <= MAX_PIDS:
            raise InvalidExecutionRequest("pid grant exceeds the 32 process ceiling")
        if not 0 < self.timeout_seconds <= MAX_WALL_SECONDS:
            raise InvalidExecutionRequest("timeout grant exceeds the 30 second ceiling")
        if not 0 <= self.output_bytes <= MAX_OUTPUT_BYTES:
            raise InvalidExecutionRequest("output grant exceeds the 64 KiB ceiling")
        if not 0 <= self.artifact_bytes <= MAX_ARTIFACT_BYTES:
            raise InvalidExecutionRequest("artifact grant exceeds the 64 MiB ceiling")


@dataclass(frozen=True, slots=True)
class OwnedProcessIdentity:
    """Durable identity sufficient to reconcile one exact runner effect."""

    pid: int
    start_time_ticks: int
    cgroup_path: str
    cgroup_inode: int
    effect_id: str


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Observed result. Non-``ok`` statuses never imply a complete artifact."""

    status: Literal[
        "ok", "command_failed", "timeout", "output_limit", "descendants_remaining",
        "containment_unavailable", "interrupted", "unknown", "cleanup_unverified",
    ]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    elapsed_ms: float
    artifact_path: Path | None
    artifact_sha256: str | None
    artifact_bytes: int
    containment_backend: str
    effect_id: str
    process_identity: OwnedProcessIdentity | None
    cleanup_verified: bool


@dataclass(frozen=True, slots=True)
class _Cgroup:
    path: Path
    inode: int


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)) or not 1 <= len(argv) <= 64:
        raise InvalidExecutionRequest("argv must contain between 1 and 64 strings")
    values = tuple(argv)
    if any(not isinstance(item, str) or not item or "\0" in item for item in values):
        raise InvalidExecutionRequest("argv contains an invalid value")
    if any(len(item.encode("utf-8")) > 2048 for item in values):
        raise InvalidExecutionRequest("argv item exceeds 2048 bytes")
    if sum(len(item.encode("utf-8")) for item in values) > MAX_ARG_BYTES:
        raise InvalidExecutionRequest("argv exceeds 12 KiB")
    return values


def _private_directory(path: Path) -> Path:
    """Create or validate a caller-owned, private per-attempt directory."""
    if path.exists() and path.is_symlink():
        raise InvalidExecutionRequest("attempt directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise InvalidExecutionRequest("attempt directory must be an owned directory")
    if info.st_mode & 0o077:
        raise InvalidExecutionRequest("attempt directory must not be accessible by group or others")
    return resolved


def _prepare_cgroup(parent: Path | None, grant: ExecutionGrant, effect_id: str) -> _Cgroup:
    if parent is None:
        parent = _current_cgroup_parent()
    root = CGROUP_ROOT.resolve(strict=True)
    try:
        resolved_parent = parent.resolve(strict=True)
        resolved_parent.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContainmentUnavailable("cgroup parent is outside the cgroup v2 hierarchy") from exc
    if resolved_parent == root or resolved_parent.is_symlink():
        raise ContainmentUnavailable("cgroup parent must be a delegated child scope")
    try:
        controllers = set((resolved_parent / "cgroup.controllers").read_text().split())
        enabled = set((resolved_parent / "cgroup.subtree_control").read_text().replace("+", "").split())
    except OSError as exc:
        raise ContainmentUnavailable("delegated cgroup controls are unreadable") from exc
    required = {"cpu", "memory", "pids"}
    if not required <= controllers:
        raise ContainmentUnavailable("delegated cgroup lacks cpu, memory or pids controllers")
    if not os.access(resolved_parent, os.W_OK | os.X_OK):
        raise ContainmentUnavailable("delegated cgroup parent is not writable")
    try:
        members = (resolved_parent / "cgroup.procs").read_text().split()
    except OSError as exc:
        raise ContainmentUnavailable("delegated cgroup process membership is unreadable") from exc
    if members:
        raise ContainmentUnavailable("delegated effect parent must be empty under the cgroup v2 no-internal-process rule")
    if not required <= enabled:
        try:
            (resolved_parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")
            enabled = set((resolved_parent / "cgroup.subtree_control").read_text().replace("+", "").split())
        except OSError as exc:
            raise ContainmentUnavailable("delegated cgroup controllers cannot be enabled safely") from exc
        if not required <= enabled:
            raise ContainmentUnavailable("delegated cgroup did not enable all required controllers")
    name = "pc-lab-" + hashlib.sha256(effect_id.encode()).hexdigest()[:20] + "-" + uuid.uuid4().hex[:8]
    path = resolved_parent / name
    try:
        path.mkdir(mode=0o700)
        (path / "memory.max").write_text(str(grant.memory_bytes))
        (path / "memory.swap.max").write_text("0")
        (path / "pids.max").write_text(str(grant.pids))
        quota = max(1, int(grant.cpu_cores * 100_000))
        (path / "cpu.max").write_text(f"{quota} 100000")
        if (path / "memory.swap.max").read_text().strip() != "0":
            raise OSError("cgroup did not apply the zero-swap limit")
        inode = path.stat().st_ino
    except OSError as exc:
        try:
            path.rmdir()
        except OSError:
            pass
        raise ContainmentUnavailable("failed to create fully limited delegated cgroup") from exc
    return _Cgroup(path=path, inode=inode)


def _current_cgroup_parent() -> Path:
    """Resolve the exact caller cgroup or its one named delegated controller parent."""
    try:
        entries = [line for line in Path("/proc/self/cgroup").read_text().splitlines()
                   if line.startswith("0::")]
        if len(entries) != 1:
            raise ValueError("missing unique cgroup v2 entry")
        relative = entries[0][3:].lstrip("/")
        if not relative or ".." in Path(relative).parts:
            raise ValueError("invalid cgroup v2 path")
        current = (CGROUP_ROOT / relative).resolve(strict=True)
        if current.name == "controller":
            parent = current.parent
            if not parent.name.endswith(".service"):
                raise ValueError("controller subgroup parent is not a transient service unit")
            if (parent / "cgroup.procs").read_text().split():
                raise ValueError("delegated service parent is not empty")
            return parent
        return current
    except (OSError, ValueError) as exc:
        raise ContainmentUnavailable("current cgroup v2 identity is unavailable") from exc


def current_cgroup_ready() -> bool:
    """Return whether this exact scope can host a bounded effect cgroup."""
    try:
        parent = _current_cgroup_parent()
        available = set((parent / "cgroup.controllers").read_text().split())
        members = (parent / "cgroup.procs").read_text().split()
        return (
            {"cpu", "memory", "pids"} <= available
            and not members
            and os.access(parent, os.W_OK | os.X_OK)
            and (parent / "cgroup.kill").exists()
        )
    except (OSError, ContainmentUnavailable, ValueError):
        return False


def _process_start_time(pid: int) -> int | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        # comm is parenthesized and may contain spaces or parentheses.
        close = text.rfind(")")
        fields = text[close + 2 :].split()
        return int(fields[19])  # field 22; remainder starts at field 3
    except (OSError, ValueError, IndexError):
        return None


def _make_bwrap_command(
    executable: str,
    snapshot: Path,
    artifact: Path,
    argv: tuple[str, ...],
    artifact_bytes: int,
    proposal: Path | None = None,
) -> list[str]:
    command = [
        executable,
        "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid", "--unshare-net",
        "--cap-drop", "ALL", "--tmpfs", "/",
    ]
    for base in (Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")):
        if base.exists():
            command.extend(("--ro-bind", str(base), str(base)))
    command.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/home",
                    "--tmpfs", "/root", "--tmpfs", "/run", "--dir", "/tmp/home", "--dir", "/workspace",
                    "--ro-bind", str(snapshot), "/workspace"))
    if proposal is not None:
        command.extend(("--dir", "/proposal", "--ro-bind", str(proposal), "/proposal"))
    command.extend(("--dir", "/artifacts",
                    "--bind", str(artifact), f"/artifacts/{ARTIFACT_NAME}", "--chdir", "/workspace",
                    "--clearenv", "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
                    "--setenv", "HOME", "/tmp/home", "--setenv", "TMPDIR", "/tmp",
                    "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--", "/usr/bin/prlimit",
                    f"--fsize={artifact_bytes}", "--nofile=64", "--", *argv))
    return command


def _sandbox_probe(executable: str) -> bool:
    command = [
        executable, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
        "--unshare-net", "--cap-drop", "ALL", "--tmpfs", "/", "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
    ]
    for base in (Path("/lib"), Path("/lib64")):
        if base.exists():
            command.extend(("--ro-bind", str(base), str(base)))
    command.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                    "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--", "/usr/bin/true"))
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"},
                                timeout=2, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cgroup_populated(path: Path) -> bool:
    try:
        for line in (path / "cgroup.events").read_text().splitlines():
            if line.startswith("populated "):
                return line.partition(" ")[2] == "1"
    except OSError:
        return True
    return True


def _kill_cgroup(cgroup: _Cgroup) -> bool:
    """Kill only processes in this unique effect cgroup and wait for release."""
    try:
        if cgroup.path.stat().st_ino != cgroup.inode:
            return False
        (cgroup.path / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not _cgroup_populated(cgroup.path):
                return True
            time.sleep(0.02)
    except OSError:
        return False
    return False


def _remove_cgroup(cgroup: _Cgroup) -> bool:
    try:
        if cgroup.path.stat().st_ino != cgroup.inode or _cgroup_populated(cgroup.path):
            return False
        cgroup.path.rmdir()
        return True
    except OSError:
        return False


def reconcile_process(identity: OwnedProcessIdentity) -> Literal["active", "exited", "unknown"]:
    """Reconcile a persisted receipt without signaling or replaying its effect."""
    try:
        cgroup = Path(identity.cgroup_path)
        root = CGROUP_ROOT.resolve(strict=True)
        start = _process_start_time(identity.pid)
        if start is not None and start != identity.start_time_ticks:
            return "unknown"
        try:
            resolved = cgroup.resolve(strict=True)
        except FileNotFoundError:
            return "exited" if start is None else "unknown"
        resolved.relative_to(root)
        if resolved.stat().st_ino != identity.cgroup_inode:
            return "unknown"
        if start is not None:
            members = set((resolved / "cgroup.procs").read_text().split())
            return "active" if str(identity.pid) in members else "unknown"
        return "exited" if not _cgroup_populated(resolved) else "unknown"
    except (OSError, ValueError):
        return "unknown"


def run_cpu(
    snapshot_root: Path,
    argv: Sequence[str],
    grant: ExecutionGrant = ExecutionGrant(),
    *,
    attempt_root: Path,
    cgroup_parent: Path | None = None,
    effect_id: str | None = None,
    on_started: Callable[[OwnedProcessIdentity], None] | None = None,
    proposal_root: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ExecutionResult:
    """Execute argv against a captured RO source under a delegated cgroup.

    Only the pre-created ``/artifacts/result.bin`` file is writable on the host.
    A missing sandbox or delegated cgroup is a hard failure; no host fallback is
    attempted. The artifact may be partial for any non-``ok`` terminal status.
    """
    args = _validate_argv(argv)
    if not isinstance(grant, ExecutionGrant):
        raise InvalidExecutionRequest("grant must be a typed ExecutionGrant")
    snapshot = Path(snapshot_root)
    if snapshot.is_symlink():
        raise InvalidExecutionRequest("snapshot root must not be a symlink")
    try:
        snapshot = snapshot.resolve(strict=True)
    except OSError as exc:
        raise InvalidExecutionRequest("snapshot root is unavailable") from exc
    if not snapshot.is_dir() or snapshot == Path("/"):
        raise InvalidExecutionRequest("snapshot root must be a captured directory")
    attempt = _private_directory(Path(attempt_root))
    if snapshot == attempt or snapshot in attempt.parents or attempt in snapshot.parents:
        raise InvalidExecutionRequest("snapshot and attempt directories must not overlap")
    proposal: Path | None = None
    if proposal_root is not None:
        raw_proposal = Path(proposal_root)
        if raw_proposal.is_symlink():
            raise InvalidExecutionRequest("proposal root must not be a symlink")
        try:
            proposal = raw_proposal.resolve(strict=True)
        except OSError as exc:
            raise InvalidExecutionRequest("proposal root is unavailable") from exc
        if (not proposal.is_dir() or proposal == Path("/") or snapshot == proposal
                or snapshot in proposal.parents or proposal in snapshot.parents
                or attempt == proposal or attempt in proposal.parents or proposal in attempt.parents):
            raise InvalidExecutionRequest("proposal root must be a separate immutable directory")
        total_proposal_bytes = 0
        for directory, dirnames, filenames in os.walk(proposal, followlinks=False):
            base = Path(directory)
            for name in dirnames + filenames:
                item = base / name
                info = item.lstat()
                if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise InvalidExecutionRequest("proposal tree contains a link or special file")
                if stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise InvalidExecutionRequest("proposal tree contains a hard-linked file")
                    total_proposal_bytes += info.st_size
                    if total_proposal_bytes > MAX_ARTIFACT_BYTES:
                        raise InvalidExecutionRequest("proposal tree exceeds the 64 MiB limit")
    effect = uuid.uuid4().hex if effect_id is None else effect_id
    if not isinstance(effect, str) or not effect or "\0" in effect or len(effect.encode("utf-8")) > 256:
        raise InvalidExecutionRequest("effect_id must be a bounded non-empty string")
    if cgroup_parent is not None:
        try:
            if cgroup_parent.resolve(strict=True) != _current_cgroup_parent().resolve(strict=True):
                raise ContainmentUnavailable("cgroup parent must be the exact current delegated scope")
        except OSError as exc:
            raise ContainmentUnavailable("cgroup parent identity is unavailable") from exc
    cgroup = _prepare_cgroup(cgroup_parent, grant, effect)
    if not (cgroup.path / "cgroup.kill").exists():
        _remove_cgroup(cgroup)
        raise ContainmentUnavailable("delegated cgroup lacks cgroup.kill descendant cleanup")
    bwrap = shutil.which("bwrap")
    if not bwrap or not _sandbox_probe(bwrap):
        _remove_cgroup(cgroup)
        raise ContainmentUnavailable("required bubblewrap namespaces or mounts are unavailable")
    artifact = attempt / ARTIFACT_NAME
    try:
        fd = os.open(artifact, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        _remove_cgroup(cgroup)
        raise InvalidExecutionRequest("attempt artifact path already exists or is unavailable") from exc
    os.close(fd)
    command = _make_bwrap_command(bwrap, snapshot, artifact, args, grant.artifact_bytes, proposal)
    # The trusted host-side gate joins the exact new process to its cgroup
    # before execing bubblewrap. Every descendant then inherits all three
    # kernel resource limits without a post-spawn race.
    gate_read, gate_write = os.pipe()
    wrapper = (
        "import os,sys; gate=int(sys.argv[1]); p=sys.argv[2]; "
        "os.write(os.open(p,os.O_WRONLY|os.O_CLOEXEC),str(os.getpid()).encode()); "
        "token=os.read(gate,1); os.close(gate); "
        "os._exit(125) if token != b'\\x01' else os.execv(sys.argv[3],sys.argv[3:])"
    )
    wrapper_command = [sys.executable, "-c", wrapper, str(gate_read), str(cgroup.path / "cgroup.procs"), *command]
    started = time.monotonic()
    try:
        process = subprocess.Popen(wrapper_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True,
                                   env={"PATH": "/usr/bin:/bin"}, pass_fds=(gate_read,))
    except OSError as exc:
        os.close(gate_read)
        os.close(gate_write)
        _kill_cgroup(cgroup)
        _remove_cgroup(cgroup)
        raise ContainmentUnavailable("sandbox process could not be started") from exc
    os.close(gate_read)
    start = _process_start_time(process.pid)
    identity = OwnedProcessIdentity(process.pid, start or 0, str(cgroup.path), cgroup.inode, effect)
    deadline_to_join = time.monotonic() + 1.0
    while start is not None:
        try:
            joined = str(process.pid) in (cgroup.path / "cgroup.procs").read_text().split()
        except OSError:
            joined = False
            start = None
            break
        if joined:
            break
        if process.poll() is not None or time.monotonic() >= deadline_to_join:
            start = None
            break
        time.sleep(0.005)
    if start is None:
        os.close(gate_write)
        _kill_cgroup(cgroup)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        cleaned = process.poll() is not None and not _cgroup_populated(cgroup.path)
        if cleaned:
            _remove_cgroup(cgroup)
        else:
            raise EffectCleanupUnverified(identity)
        raise ContainmentUnavailable("could not bind the effect receipt to an exact process identity")
    if on_started is not None:
        try:
            on_started(identity)
        except BaseException as exc:
            _kill_cgroup(cgroup)
            os.close(gate_write)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            cleanup_verified = process.poll() is not None and not _cgroup_populated(cgroup.path)
            _remove_cgroup(cgroup)
            if not cleanup_verified:
                raise EffectCleanupUnverified(identity) from exc
            raise
    try:
        os.write(gate_write, b"\x01")
    except OSError as exc:
        os.close(gate_write)
        _kill_cgroup(cgroup)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        cleanup_verified = process.poll() is not None and not _cgroup_populated(cgroup.path)
        if cleanup_verified:
            _remove_cgroup(cgroup)
            raise ContainmentUnavailable("launcher exited before workload release") from exc
        raise EffectCleanupUnverified(identity) from exc
    os.close(gate_write)
    selector = selectors.DefaultSelector()
    streams: dict[object, bytearray] = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
    combined = 0
    status: Literal["ok", "command_failed", "timeout", "output_limit", "descendants_remaining", "containment_unavailable", "interrupted", "unknown", "cleanup_unverified"] = "ok"
    deadline = started + grant.timeout_seconds
    try:
        while True:
            if should_cancel is not None and should_cancel():
                status = "interrupted"
                break
            if time.monotonic() >= deadline:
                status = "timeout"
                break
            if process.poll() is not None and not selector.get_map():
                if _cgroup_populated(cgroup.path):
                    status = "descendants_remaining"
                break
            for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), min(4096, grant.output_bytes - combined + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                kept = chunk[:max(0, grant.output_bytes - combined)]
                streams[key.fileobj].extend(kept)
                combined += len(chunk)
                if combined > grant.output_bytes:
                    status = "output_limit"
                    break
            if status != "ok":
                break
    except BaseException:
        status = "interrupted"
    finally:
        cleanup_verified = True
        if status != "ok" or _cgroup_populated(cgroup.path):
            cleanup_verified = _kill_cgroup(cgroup)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            cleanup_verified = _kill_cgroup(cgroup) and cleanup_verified
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                cleanup_verified = False
        cleanup_verified = cleanup_verified and not _cgroup_populated(cgroup.path)
        if not cleanup_verified:
            status = "cleanup_unverified"
        selector.close()
        for stream in streams:
            if stream is not None:
                stream.close()
    artifact_bytes = artifact.stat().st_size if artifact.exists() else 0
    if artifact_bytes > grant.artifact_bytes:
        # RLIMIT_FSIZE caps this one persistent file before the host filesystem
        # can grow beyond the grant. Keep the truncated file as partial evidence.
        status = "output_limit"
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.exists() else None
    removed = _remove_cgroup(cgroup)
    # A stale but verified-empty cgroup is recoverable housekeeping. A
    # populated or identity-mismatched cgroup remains an unknown live effect.
    if not cleanup_verified:
        status = "cleanup_unverified"
    del removed
    return ExecutionResult(
        status=status if status != "ok" or process.returncode == 0 else "command_failed",
        returncode=process.returncode,
        stdout=bytes(streams[process.stdout]),
        stderr=bytes(streams[process.stderr]),
        elapsed_ms=round((time.monotonic() - started) * 1000, 3),
        artifact_path=artifact if artifact_bytes else None,
        artifact_sha256=digest,
        artifact_bytes=artifact_bytes,
        containment_backend="bubblewrap+cgroup-v2",
        effect_id=effect,
        process_identity=identity,
        cleanup_verified=cleanup_verified,
    )
