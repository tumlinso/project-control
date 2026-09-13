"""Fixed, bounded host diagnostics for the local-investigator broker."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Literal

from ..config import ProjectControlConfig
from ..models import ProjectSnapshot, ToolEnvelope, envelope
from ..normalize import bounded_envelope
from ..security import SecurityError, redact_output, redact_text
from ..subprocesses import CommandError, FixedCommandRunner

MachineDiagnostic = Literal[
    "gpu_summary", "gpu_topology", "gpu_processes", "host_memory",
    "filesystem_capacity", "services", "processes", "system", "pcie_devices",
    "storage_block", "network_state", "project_control_logs", "versions", "proc_sys", "filesystem",
]
_MAX_ROWS = 16
_MAX_TEXT_BYTES = 16 * 1024
_DENIED_COMPONENTS = frozenset({".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".codex", ".huggingface", "gh", "credentials", ".git-credentials", ".netrc", "id_rsa", "id_ed25519", "known_hosts", "cookies", "browser", "mozilla", "chromium", "auth", "sessions", "keyring", "wallet", "environ", "fd", "mem", "root", "cwd", "exe"})


def _runtime_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return (base / "project-control").expanduser().resolve()


def _bounded_lines(value: str, limit: int = _MAX_ROWS) -> list[str]:
    return [redact_text(line)[:256] for line in value.splitlines() if line.strip()][:limit]


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
    elif root_kind in {"mnt", "opt", "srv", "var_log", "var_lib", "etc", "usr_local"}:
        root = {"mnt": Path("/mnt"), "opt": Path("/opt"), "srv": Path("/srv"),
                "var_log": Path("/var/log"), "var_lib": Path("/var/lib"),
                "etc": Path("/etc"), "usr_local": Path("/usr/local")}[root_kind]
    else:
        raise SecurityError("filesystem_root_not_allowlisted")
    relative = Path(str(spec.get("path", ".")))
    if relative.is_absolute() or ".." in relative.parts or any(part.casefold() in _DENIED_COMPONENTS for part in relative.parts):
        raise SecurityError("filesystem_path_denied")
    resolved_root = root.resolve(strict=True)
    target = (resolved_root / relative).resolve(strict=True)
    if target != resolved_root and resolved_root not in target.parents:
        raise SecurityError("filesystem_path_escapes_root")
    if any(part.casefold() in _DENIED_COMPONENTS for part in target.relative_to(resolved_root).parts):
        raise SecurityError("filesystem_path_denied")
    return target


def _filesystem_inspection(config: ProjectControlConfig, project: str, spec: dict[str, Any]) -> dict[str, Any]:
    target = _filesystem_root(config, project, spec)
    operation = spec.get("operation")
    if operation == "list":
        if not target.is_dir():
            raise SecurityError("filesystem_not_directory")
        return {"operation": "list", "entries": [{"name": entry.name[:128], "kind": "directory" if entry.is_dir() else "file"}
                                                for entry in sorted(target.iterdir(), key=lambda item: item.name)[:_MAX_ROWS]
                                                if entry.name.casefold() not in _DENIED_COMPONENTS and not entry.is_symlink() and (entry.is_dir() or entry.is_file())]}
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
        return {"operation": "read", "text": redact_text(payload.decode("utf-8", errors="strict"))[:_MAX_TEXT_BYTES]}
    if operation == "search":
        query = str(spec.get("query", ""))
        if not target.is_dir() or not query or len(query) > 256:
            raise SecurityError("filesystem_search_invalid")
        matches = []
        for child in sorted(target.rglob("*")):
            if len(matches) >= _MAX_ROWS:
                break
            if child.is_symlink() or not child.is_file() or child.stat().st_size > _MAX_TEXT_BYTES or any(part.casefold() in _DENIED_COMPONENTS for part in child.relative_to(target).parts):
                continue
            try:
                text = child.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if query.casefold() in text.casefold():
                matches.append({"path": child.relative_to(target).as_posix()[:256], "excerpt": redact_text(next((line for line in text.splitlines() if query.casefold() in line.casefold()), ""))[:256]})
        return {"operation": "search", "matches": matches}
    raise SecurityError("filesystem_operation_not_allowlisted")


def machine_inspection(
    config: ProjectControlConfig, snapshot: ProjectSnapshot, *, project: str,
    diagnostic: MachineDiagnostic, filesystem: dict[str, Any] | None = None,
    runner: FixedCommandRunner | None = None,
) -> ToolEnvelope:
    """Execute one allowlisted, non-mutating diagnostic without exposing argv/output."""
    command_runner = runner or FixedCommandRunner(max_capture_bytes=32 * 1024)
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
            raw = command_runner.run([
                "ps", "-eo", "pid=,comm=,rss=,stat=", "--no-headers",
            ], cwd=Path("/"), timeout=2.0).stdout
            processes = []
            for line in raw.splitlines()[:_MAX_ROWS]:
                fields = line.split(None, 3)
                if len(fields) == 4:
                    processes.append({"process": fields[1][:128], "rss_kib": fields[2][:32],
                                      "state": fields[3][:32]})
            data["processes"] = processes
        elif diagnostic == "system":
            raw = command_runner.run(["uname", "-srmo"], cwd=Path("/"), timeout=2.0).stdout
            data["kernel"] = _bounded_lines(raw, 1)
        elif diagnostic == "pcie_devices":
            raw = command_runner.run(["lspci", "-mm"], cwd=Path("/"), timeout=2.0).stdout
            data["devices"] = _bounded_lines(raw)
        elif diagnostic == "storage_block":
            raw = command_runner.run(["lsblk", "-J", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS"], cwd=Path("/"), timeout=2.0).stdout
            data["block_rows"] = _bounded_lines(raw)
        elif diagnostic == "network_state":
            raw = command_runner.run(["ip", "-brief", "address"], cwd=Path("/"), timeout=2.0).stdout
            data["interfaces"] = _bounded_lines(raw)
        elif diagnostic == "project_control_logs":
            raw = command_runner.run(["journalctl", "--user-unit", "project-control.service", "-n", "16", "--no-pager", "-o", "cat"], cwd=Path("/"), timeout=2.0, check=False).stdout
            data["log_lines"] = _bounded_lines(raw)
        elif diagnostic == "versions":
            data["versions"] = {"python": _bounded_lines(command_runner.run(["python3", "--version"], cwd=Path("/"), timeout=2.0).stdout, 1),
                                "compiler": _bounded_lines(command_runner.run(["cc", "--version"], cwd=Path("/"), timeout=2.0, check=False).stdout, 1)}
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
