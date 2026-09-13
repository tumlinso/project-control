"""Fixed, bounded host diagnostics for the local-investigator broker."""
from __future__ import annotations

import os
import json
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Literal

from ..config import ProjectControlConfig
from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_envelope
from ..security import SecurityError, redact_output, redact_text
from ..subprocesses import CommandError, FixedCommandRunner
from ..terminal import BubblewrapSandbox

MachineDiagnostic = Literal[
    "gpu_summary", "gpu_topology", "gpu_processes", "host_memory",
    "filesystem_capacity", "services", "processes", "system", "pcie_devices",
    "storage_block", "network_state", "project_control_logs", "versions", "proc_sys", "filesystem",
]
_MAX_ROWS = 16
_MAX_TEXT_BYTES = 16 * 1024
_MAX_ENTRIES_SCANNED = 2048
_MAX_SCAN_SECONDS = 1.0
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_DENIED_COMPONENTS = frozenset({
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".codex",
    ".huggingface", ".password-store", ".git-credentials", ".netrc", ".npmrc",
    ".pypirc", "gh", "gcloud", "rclone", "credentials", "id_rsa", "id_ed25519",
    "known_hosts", "cookies", "browser", "mozilla", "chromium", "auth", "sessions",
    "keyring", "keyrings", "wallet", "bitwarden", "1password", "environ", "fd",
    "mem", "root", "cwd", "exe",
})
_SAFE_PROC_ROOTS = frozenset({
    "cpuinfo", "meminfo", "loadavg", "uptime", "version", "mounts",
    "filesystems", "pressure", "sys",
})
_FIXED_DIAGNOSTIC_EXECUTABLES = frozenset({
    "nvidia-smi", "systemctl", "journalctl", "lspci", "lsblk", "python3",
    "cc", "cmake", "nvcc",
})


class _SandboxedDiagnosticRunner:
    """Run only broker-owned diagnostic argv in a no-network read-only mount."""

    def __init__(self) -> None:
        self._sandbox = BubblewrapSandbox()
        self._runner = FixedCommandRunner(max_capture_bytes=32 * 1024)

    def run(self, argv, *, cwd: Path, timeout: float = 5.0, check: bool = True, **_kwargs):
        if not argv or argv[0] not in _FIXED_DIAGNOSTIC_EXECUTABLES:
            raise CommandError("machine_diagnostic_executable_not_allowlisted")
        if not self._sandbox.probe():
            raise CommandError("machine_diagnostic_sandbox_unavailable")
        executable = shutil.which(argv[0], path="/usr/local/cuda/bin:/usr/sbin:/usr/bin:/bin")
        if executable is None:
            raise CommandError("machine_diagnostic_executable_unavailable")
        command = [
            str(self._sandbox.executable), "--die-with-parent", "--unshare-net",
            "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--new-session",
            "--cap-drop", "ALL", "--ro-bind", "/", "/",
        ]
        # NVML requires read/write device descriptors even for queries. Only
        # the fixed nvidia-smi query argv reaches these mounts; the model never
        # receives a device handle or chooses flags.
        if argv[0] == "nvidia-smi":
            for device in sorted(Path("/dev").glob("nvidia*")):
                command.extend(("--dev-bind", str(device), str(device)))
        command.extend([
            "--tmpfs", "/tmp", "--tmpfs", "/home", "--tmpfs", "/root",
            "--clearenv", "--setenv", "PATH", "/usr/local/cuda/bin:/usr/sbin:/usr/bin:/bin",
            "--setenv", "HOME", "/home", "--setenv", "TMPDIR", "/tmp",
            "--setenv", "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}",
            "--", "/usr/bin/prlimit", "--cpu=3", "--as=536870912",
            "--nproc=128", "--nofile=64", "--fsize=0", "--", executable, *argv[1:],
        ])
        return self._runner.run(command, cwd=Path("/"), timeout=min(timeout, 4.0), check=check)


def _host_processes() -> list[dict[str, Any]]:
    values = []
    deadline = time.monotonic() + _MAX_SCAN_SECONDS
    scanned = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        scanned += 1
        if scanned > _MAX_ENTRIES_SCANNED or time.monotonic() >= deadline:
            break
        try:
            name = (entry / "comm").read_text(encoding="utf-8").strip()[:128]
            status = {}
            for line in (entry / "status").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                if key in {"VmRSS", "State"}:
                    status[key] = value.strip()
            rss = int(status.get("VmRSS", "0 kB").split()[0])
            values.append({"process": name, "rss_kib": rss, "state": status.get("State", "")[:32]})
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    values.sort(key=lambda item: (-item["rss_kib"], item["process"]))
    return values[:_MAX_ROWS]


def _host_network_state() -> dict[str, Any]:
    interfaces = []
    for entry in sorted(Path("/sys/class/net").iterdir(), key=lambda item: item.name)[:_MAX_ROWS]:
        try:
            interfaces.append({
                "name": entry.name[:64],
                "state": (entry / "operstate").read_text(encoding="ascii").strip()[:32],
                "mtu": int((entry / "mtu").read_text(encoding="ascii").strip()),
            })
        except (OSError, ValueError):
            continue
    routes = []
    try:
        lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:_MAX_ROWS + 1]
        for line in lines:
            fields = line.split()
            if len(fields) >= 8:
                routes.append({"interface": fields[0][:64], "destination_hex": fields[1][:16],
                               "gateway_hex": fields[2][:16], "mask_hex": fields[7][:16]})
    except OSError:
        pass
    return {"interfaces": interfaces, "routes": routes}


def _sensitive_path(relative: Path) -> bool:
    for part in relative.parts:
        folded = part.casefold()
        if folded in _DENIED_COMPONENTS or any(word in folded for word in ("credential", "private_key", "api_key")):
            return True
        if folded == ".env" or folded.startswith(".env."):
            return True
        if folded.endswith((".pem", ".key")) or folded in {"token", "secret", "password"}:
            return True
    return False


def _runtime_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return (base / "project-control").expanduser().resolve()


def _bounded_lines(value: str, limit: int = _MAX_ROWS) -> list[str]:
    lines = []
    for line in value.splitlines():
        clean = _ANSI_ESCAPE.sub("", line)
        clean = "".join(character for character in clean if character == "\t" or character.isprintable())
        if clean.strip():
            lines.append(redact_text(clean)[:256])
    return lines[:limit]


def _gpu_csv(raw: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in raw.splitlines()[:_MAX_ROWS]:
        values = [item.strip()[:128] for item in line.split(",")]
        if len(values) == len(fields):
            rows.append(dict(zip(fields, values, strict=True)))
    return rows


def _filesystem_root(config: ProjectControlConfig, project: str, spec: dict[str, Any]) -> Path:
    root_kind = spec.get("root")
    if root_kind == "repository":
        alias = spec.get("repository")
        if not isinstance(alias, str) or alias not in config.workspaces[project].repositories:
            raise SecurityError("filesystem_repository_not_registered")
        root = config.workspaces[project].repositories[alias].root
    elif root_kind == "home":
        root = Path.home()
    elif root_kind in {"mnt", "opt", "srv", "var_log", "var_lib", "etc", "usr_local", "proc", "sys"}:
        root = {"mnt": Path("/mnt"), "opt": Path("/opt"), "srv": Path("/srv"),
                "var_log": Path("/var/log"), "var_lib": Path("/var/lib"),
                "etc": Path("/etc"), "usr_local": Path("/usr/local"),
                "proc": Path("/proc"), "sys": Path("/sys")}[root_kind]
    else:
        raise SecurityError("filesystem_root_not_allowlisted")
    relative = Path(str(spec.get("path", ".")))
    if relative.is_absolute() or ".." in relative.parts or _sensitive_path(relative):
        raise SecurityError("filesystem_path_denied")
    if root_kind == "proc" and (not relative.parts or relative.parts[0] not in _SAFE_PROC_ROOTS):
        raise SecurityError("filesystem_proc_path_denied")
    resolved_root = root.resolve(strict=True)
    target = (resolved_root / relative).resolve(strict=True)
    if target != resolved_root and resolved_root not in target.parents:
        raise SecurityError("filesystem_path_escapes_root")
    if _sensitive_path(target.relative_to(resolved_root)):
        raise SecurityError("filesystem_path_denied")
    return target


def _filesystem_inspection(config: ProjectControlConfig, project: str, spec: dict[str, Any]) -> dict[str, Any]:
    target = _filesystem_root(config, project, spec)
    operation = spec.get("operation")
    if operation == "list":
        if not target.is_dir():
            raise SecurityError("filesystem_not_directory")
        entries = []
        scanned = 0
        deadline = time.monotonic() + _MAX_SCAN_SECONDS
        stopped_early = False
        with os.scandir(target) as stream:
            for entry in stream:
                scanned += 1
                if scanned > _MAX_ENTRIES_SCANNED or time.monotonic() >= deadline:
                    stopped_early = True
                    break
                relative = Path(entry.name)
                if _sensitive_path(relative) or entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False) or entry.is_file(follow_symlinks=False):
                    entries.append({"name": entry.name[:128], "kind": "directory" if entry.is_dir(follow_symlinks=False) else "file"})
        entries.sort(key=lambda item: item["name"])
        return {"operation": "list", "entries": entries[:_MAX_ROWS],
                "scan_truncated": stopped_early or len(entries) > _MAX_ROWS}
    if operation == "stat":
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISDIR(metadata.st_mode):
            raise SecurityError("filesystem_special_file_denied")
        return {"operation": "stat", "kind": "directory" if target.is_dir() else "file", "size_bytes": metadata.st_size}
    if operation == "read":
        if not target.is_file() or target.stat().st_size > _MAX_TEXT_BYTES:
            raise SecurityError("filesystem_read_denied_or_too_large")
        payload = target.read_bytes()
        if b"\x00" in payload:
            raise SecurityError("filesystem_binary_rejected")
        text = payload.decode("utf-8", errors="strict")
        if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", text):
            raise SecurityError("filesystem_private_key_rejected")
        return {"operation": "read", "text": redact_text(text)[:_MAX_TEXT_BYTES]}
    if operation == "search":
        query = str(spec.get("query", ""))
        if not target.is_dir() or not query or len(query) > 256:
            raise SecurityError("filesystem_search_invalid")
        matches = []
        scanned = 0
        deadline = time.monotonic() + _MAX_SCAN_SECONDS
        for current, directories, files in os.walk(target, followlinks=False):
            base = Path(current)
            directories[:] = sorted(name for name in directories if not _sensitive_path((base / name).relative_to(target)))
            for name in sorted(files):
                scanned += 1
                if scanned > _MAX_ENTRIES_SCANNED or time.monotonic() >= deadline or len(matches) >= _MAX_ROWS:
                    return {"operation": "search", "matches": matches, "scan_truncated": True}
                child = base / name
                relative = child.relative_to(target)
                if child.is_symlink() or _sensitive_path(relative):
                    continue
                try:
                    if child.stat().st_size > _MAX_TEXT_BYTES:
                        continue
                    text = child.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", text):
                    continue
                if query.casefold() in text.casefold():
                    matches.append({"path": relative.as_posix()[:256], "excerpt": redact_text(next((line for line in text.splitlines() if query.casefold() in line.casefold()), ""))[:256]})
        return {"operation": "search", "matches": matches, "scan_truncated": False}
    raise SecurityError("filesystem_operation_not_allowlisted")


def machine_inspection(
    config: ProjectControlConfig, snapshot: ProjectSnapshot, *, project: str,
    diagnostic: MachineDiagnostic, filesystem: dict[str, Any] | None = None,
    runner: FixedCommandRunner | None = None,
) -> ToolEnvelope:
    """Execute one allowlisted, non-mutating diagnostic without exposing argv/output."""
    command_runner = runner or _SandboxedDiagnosticRunner()
    data: dict[str, Any] = {"diagnostic": diagnostic, "status": "ok", "warnings": []}
    try:
        if diagnostic == "filesystem":
            if not filesystem:
                raise SecurityError("filesystem_request_missing")
            data["filesystem"] = _filesystem_inspection(config, project, filesystem)
        elif diagnostic == "gpu_summary":
            raw = command_runner.run([
                "nvidia-smi", "--query-gpu=index,name,driver_version,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ], cwd=Path("/"), timeout=2.0).stdout
            data["devices"] = _gpu_csv(raw, ("index", "name", "driver_version", "memory_total_mib", "memory_free_mib", "utilization_percent"))
        elif diagnostic == "gpu_topology":
            raw = command_runner.run(["nvidia-smi", "topo", "-m"], cwd=Path("/"), timeout=2.0).stdout
            data["topology_rows"] = _bounded_lines(raw)
        elif diagnostic == "gpu_processes":
            raw = command_runner.run([
                "nvidia-smi", "--query-compute-apps=process_name,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ], cwd=Path("/"), timeout=2.0, check=False).stdout
            data["processes"] = _gpu_csv(raw, ("process_name", "used_gpu_memory_mib"))
        elif diagnostic == "host_memory":
            values: dict[str, str] = {}
            for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                    values[key] = value.strip()[:64]
            data["memory"] = values
        elif diagnostic == "filesystem_capacity":
            workspace = config.workspaces[project]
            roots = [(f"repository:{alias}", item.root) for alias, item in sorted(workspace.repositories.items())]
            roots.append(("project_control_runtime", _runtime_root()))
            roots.append(("home", Path.home()))
            if Path("/mnt/block").is_dir():
                roots.append(("mnt_block", Path("/mnt/block")))
            capacities = []
            for label, root in roots[:_MAX_ROWS]:
                try:
                    root.stat()  # Reject absent roots rather than probing arbitrary paths.
                    usage = os.statvfs(root)
                    capacities.append({"root": label, "available_bytes": usage.f_bavail * usage.f_frsize,
                                       "total_bytes": usage.f_blocks * usage.f_frsize, "present": root.is_dir()})
                except OSError:
                    capacities.append({"root": label, "status": "unavailable"})
            data["filesystems"] = capacities
        elif diagnostic == "services":
            services = ("project-control.service",)
            states = []
            for service in services:
                raw = command_runner.run([
                    "systemctl", "--user", "show", service,
                    "--property=LoadState,ActiveState,SubState", "--no-pager",
                ], cwd=Path("/"), timeout=2.0, check=False).stdout
                fields = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
                states.append({"service": service, **{key: fields.get(key, "unknown")[:64]
                                                        for key in ("LoadState", "ActiveState", "SubState")}})
            data["services"] = states
        elif diagnostic == "processes":
            data["processes"] = _host_processes()
        elif diagnostic == "system":
            system = os.uname()
            data["kernel"] = [{"system": system.sysname, "release": system.release,
                               "machine": system.machine}]
        elif diagnostic == "pcie_devices":
            raw = command_runner.run(["lspci", "-mm"], cwd=Path("/"), timeout=2.0).stdout
            data["devices"] = _bounded_lines(raw)
        elif diagnostic == "storage_block":
            raw = command_runner.run(["lsblk", "-J", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS"], cwd=Path("/"), timeout=2.0).stdout
            parsed = json.loads(raw)
            data["block_devices"] = parsed.get("blockdevices", [])[:_MAX_ROWS] if isinstance(parsed, dict) else []
        elif diagnostic == "network_state":
            data.update(_host_network_state())
        elif diagnostic == "project_control_logs":
            raw = command_runner.run(["journalctl", "--user-unit", "project-control.service", "-n", "16", "--no-pager", "-o", "cat"], cwd=Path("/"), timeout=2.0, check=False).stdout
            data["log_lines"] = _bounded_lines(raw)
        elif diagnostic == "versions":
            versions = {}
            for name, argv in (
                ("python", ["python3", "--version"]), ("compiler", ["cc", "--version"]),
                ("cmake", ["cmake", "--version"]), ("cuda", ["nvcc", "--version"]),
            ):
                try:
                    result = command_runner.run(argv, cwd=Path("/"), timeout=2.0, check=False)
                    versions[name] = _bounded_lines(result.stdout or result.stderr, 2)
                except CommandError:
                    versions[name] = ["unavailable"]
            data["versions"] = versions
        elif diagnostic == "proc_sys":
            values = {}
            for path in (Path("/proc/loadavg"), Path("/proc/uptime"), Path("/proc/sys/kernel/osrelease")):
                try:
                    values[path.name] = path.read_text(encoding="utf-8")[:128].strip()
                except OSError:
                    continue
            data["values"] = values
        else:  # Defensive runtime guard for callers outside the Pydantic broker.
            raise ValueError("machine_diagnostic_not_allowlisted")
    except (CommandError, OSError, KeyError, ValueError):
        data["status"] = "partial"
        data["warnings"].append("machine_diagnostic_unavailable")
    return bounded_envelope(envelope("machine_inspection", snapshot, redact_output(data), compact_identity=True), 24 * 1024)
