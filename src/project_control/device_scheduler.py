"""Authenticated Project Control clients for the Device Scheduler authority.

The persistent selector contains only an endpoint, protected token-file path,
and (optionally) an operator-registered model backend ID. No allocation ledger
or mutable authority is stored here; leases and backend lifecycle stay on the
Device Scheduler service.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class DeviceSchedulerError(RuntimeError):
    """A Device Scheduler request or authority operation failed safely."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(code if message is None else f"{code}: {message}")


class UnsupportedDeviceSchedulerOperation(DeviceSchedulerError):
    """The old Project Control contract has no safe scheduler equivalent."""


class DeviceSchedulerReclamationPending(DeviceSchedulerError):
    """The scheduler has not verified that the released allocation is free."""


@dataclass(frozen=True, slots=True)
class ResourceAuthority:
    mode: str = "local"
    base_url: str | None = None
    token_file: str | None = None
    model_backend_id: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"local", "device_scheduler"}:
            raise ValueError("resource authority mode must be local or device_scheduler")
        if self.mode == "local":
            if any(value is not None for value in (self.base_url, self.token_file, self.model_backend_id)):
                raise ValueError("local authority must not contain scheduler settings")
            return
        if not isinstance(self.base_url, str) or not isinstance(self.token_file, str):
            raise ValueError("Device Scheduler authority requires base_url and token_file")
        _normalize_base_url(self.base_url)
        token = Path(self.token_file)
        if not token.is_absolute():
            raise ValueError("Device Scheduler token_file must be absolute")
        if self.model_backend_id is not None and not _identifier(self.model_backend_id):
            raise ValueError("model_backend_id must be an identifier")

    @classmethod
    def from_dict(cls, value: object) -> "ResourceAuthority":
        if not isinstance(value, Mapping):
            raise ValueError("resource authority must be an object")
        if set(value) != {"schema_version", "mode", "base_url", "token_file", "model_backend_id"}:
            raise ValueError("resource authority has unknown or missing fields")
        if value.get("schema_version") != 1:
            raise ValueError("resource authority schema_version must be 1")
        return cls(mode=value["mode"], base_url=value["base_url"], token_file=value["token_file"],
                   model_backend_id=value["model_backend_id"])

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "mode": self.mode, "base_url": self.base_url,
                "token_file": self.token_file, "model_backend_id": self.model_backend_id}


def _identifier(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and all(char.isalnum() or char in "._:-" for char in value))


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Device Scheduler URL must be loopback HTTP without credentials, query, or fragment")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.casefold() == "localhost"
    if not loopback or parsed.path not in {"", "/"}:
        raise ValueError("Device Scheduler URL must be a loopback origin")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Device Scheduler URL port is invalid") from None
    authority = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if port is not None:
        authority += f":{port}"
    return urlunsplit(("http", authority, "", "", ""))


def resource_authority_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return base / "project-control" / "resource-authority.json"


def read_resource_authority(path: Path | None = None, *, validate_credentials: bool = True) -> ResourceAuthority:
    """Read the private, persistent authority selector; absence means legacy."""
    target = path or resource_authority_path()
    try:
        info = target.lstat()
    except FileNotFoundError:
        return ResourceAuthority()
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600):
        raise DeviceSchedulerError("resource_authority_config_not_private")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        authority = ResourceAuthority.from_dict(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DeviceSchedulerError("resource_authority_config_invalid") from exc
    if authority.mode == "device_scheduler" and validate_credentials:
        # Validate credentials on every use. Missing or changed credentials
        # must never reactivate the retired local allocator as a fallback.
        read_token_file(Path(authority.token_file or ""))
    return authority


def write_resource_authority(authority: ResourceAuthority, path: Path | None = None) -> Path:
    """Atomically persist an operator-selected resource authority."""
    target = path or resource_authority_path()
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_info = parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid():
        raise DeviceSchedulerError("resource_authority_directory_invalid")
    os.chmod(parent, 0o700)
    raw = json.dumps(authority.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    temporary = parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def read_token_file(path: Path) -> str:
    if not path.is_absolute():
        raise DeviceSchedulerError("device_scheduler_token_path_not_absolute")
    try:
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600):
            raise DeviceSchedulerError("device_scheduler_token_not_private")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise DeviceSchedulerError("device_scheduler_token_unavailable") from exc
    if not token or "\n" in token or "\r" in token:
        raise DeviceSchedulerError("device_scheduler_token_invalid")
    return token


def _run_awaitable(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="pc-device-scheduler") as pool:
        return pool.submit(asyncio.run, awaitable).result()


class DeviceSchedulerMcpClient:
    """Minimal authenticated client over the public MCP operation registry."""

    def __init__(self, authority: ResourceAuthority, *, timeout_seconds: float = 30.0) -> None:
        if authority.mode != "device_scheduler":
            raise ValueError("DeviceSchedulerMcpClient requires the selected authority")
        if not 0.1 <= timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be from 0.1 through 300")
        self.authority = authority
        self.timeout_seconds = float(timeout_seconds)

    async def _call_async(self, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        try:
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise DeviceSchedulerError("device_scheduler_mcp_client_unavailable") from exc
        tool_name = operation.replace(".", "_")
        token = read_token_file(Path(self.authority.token_file or ""))
        client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                   timeout=httpx.Timeout(self.timeout_seconds))
        try:
            async with streamable_http_client(self.authority.base_url + "/mcp", http_client=client) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, {"arguments": dict(arguments)})
            payload = getattr(result, "structuredContent", None)
            if not isinstance(payload, dict):
                for block in getattr(result, "content", ()):
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError:
                            payload = None
                        break
            if not isinstance(payload, dict):
                raise DeviceSchedulerError("device_scheduler_invalid_response")
            error = payload.get("error")
            if getattr(result, "isError", False) or isinstance(error, Mapping):
                code = str(error.get("code", "operation_failed")) if isinstance(error, Mapping) else "operation_failed"
                message = str(error.get("message", "scheduler operation failed")) if isinstance(error, Mapping) else "scheduler operation failed"
                raise DeviceSchedulerError(code, message)
            return payload
        except DeviceSchedulerError:
            raise
        except Exception as exc:
            raise DeviceSchedulerError(f"device_scheduler_transport_{type(exc).__name__}") from exc
        finally:
            await client.aclose()

    async def call(self, operation: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not _identifier(operation.replace(".", "_")):
            raise ValueError("operation name is invalid")
        return await self._call_async(operation, dict(arguments or {}))


_ACTIVE_LEASE_STATES = frozenset({"active", "release_requested", "reclaiming", "unknown", "expired"})
_GPU_ALIASES = frozenset({"accelerator", "gpu", "nvidia_gpu", "cuda_gpu"})


def _as_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DeviceSchedulerError(f"Device Scheduler returned an invalid {name}")
    return dict(value)


def _process_start(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class DeviceSchedulerHostResourceFacade:
    """Compatibility view of Project Control owner DTOs backed only by DS leases."""

    def __init__(self, client: Any, *, lease_ttl_seconds: float = 60.0,
                 clock: Any = time.monotonic, sleeper: Any = time.sleep) -> None:
        if client is None or not callable(getattr(client, "call", None)):
            raise TypeError("client must expose call(operation, arguments)")
        self._client = client
        self._ttl = float(lease_ttl_seconds)
        self._clock = clock
        self._sleep = sleeper
        self._drain_callbacks: dict[str, Any] = {}

    def _call(self, operation: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            value = self._client.call(operation, dict(arguments or {}))
            if hasattr(value, "__await__"):
                value = _run_awaitable(value)
        except DeviceSchedulerError as exc:
            if exc.code in {"conflict", "resource_conflict", "admission_denied", "idempotency_conflict"}:
                raise _NoGrant(exc.code) from exc
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in {"conflict", "resource_conflict", "admission_denied", "idempotency_conflict"}:
                raise _NoGrant(str(code)) from exc
            raise DeviceSchedulerError(f"{operation}_failed_{code or type(exc).__name__}") from exc
        result = _as_mapping(value, f"{operation} response")
        error = result.get("error")
        if isinstance(error, Mapping):
            code = str(error.get("code", "operation_failed"))
            if code in {"conflict", "resource_conflict", "admission_denied", "idempotency_conflict"}:
                raise _NoGrant(code)
            raise DeviceSchedulerError(f"{operation} failed ({code})")
        return result

    def _resources(self) -> list[dict[str, Any]]:
        result = self._call("resources.list")
        values = result.get("resources")
        if not isinstance(values, list):
            raise DeviceSchedulerError("resources.list omitted resources")
        resources = []
        for value in values:
            item = _as_mapping(value, "resource")
            nested = item.get("resource")
            if isinstance(nested, Mapping):
                item = {**dict(nested), **{key: child for key, child in item.items() if key != "resource"}}
            resources.append(item)
        return resources

    @staticmethod
    def _kind(resource: Mapping[str, Any]) -> str:
        return str(resource.get("kind", ""))

    @staticmethod
    def _legacy_id(resource: Mapping[str, Any]) -> str:
        kind = str(resource.get("kind", "")).casefold()
        attrs = resource.get("attributes") if isinstance(resource.get("attributes"), Mapping) else {}
        if kind in _GPU_ALIASES and isinstance(attrs.get("uuid"), str):
            return f"accelerator:{attrs['uuid']}"
        return str(resource.get("resource_id", ""))

    @staticmethod
    def _domain_members(resource: Mapping[str, Any]) -> set[str]:
        if str(resource.get("kind", "")).casefold() != "isolation":
            return set()
        attrs = resource.get("attributes") if isinstance(resource.get("attributes"), Mapping) else {}
        members = attrs.get("member_resource_ids", ())
        return {str(item) for item in members} if isinstance(members, (list, tuple, set)) else set()

    @classmethod
    def _tags(cls, resource: Mapping[str, Any], all_resources: list[Mapping[str, Any]]) -> dict[str, str]:
        attrs = resource.get("attributes") if isinstance(resource.get("attributes"), Mapping) else {}
        caps = resource.get("capacities") if isinstance(resource.get("capacities"), Mapping) else {}
        tags: dict[str, str] = {}
        for key in ("uuid", "pci_bus_id", "name", "index_observation"):
            if attrs.get(key) is not None:
                tags["index" if key == "index_observation" else key] = str(attrs[key])
        for source, target, factor in (("memory_free_bytes_observation", "memory_free_mib", 1024 * 1024),):
            try:
                if attrs.get(source) is not None:
                    tags[target] = str(int(float(attrs[source]) / factor))
            except (TypeError, ValueError, OverflowError):
                pass
        if attrs.get("utilization_percent_observation") is not None:
            tags["utilization_percent"] = str(attrs["utilization_percent_observation"])
        try:
            if caps.get("memory_bytes") is not None:
                tags["memory_total_mib"] = str(int(float(caps["memory_bytes"]) / (1024 * 1024)))
        except (TypeError, ValueError, OverflowError):
            pass
        rid = str(resource.get("resource_id", ""))
        domains = [item for item in all_resources if rid in cls._domain_members(item)]
        if len(domains) == 1:
            attrs = domains[0].get("attributes") if isinstance(domains[0].get("attributes"), Mapping) else {}
            tags["nvlink_domain"] = str(attrs.get("domain_id", domains[0].get("resource_id", "")))
        return tags

    def list(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        resources = self._resources()
        wanted = "gpu" if isinstance(kind, str) and kind.casefold() in _GPU_ALIASES else kind
        result = []
        for resource in resources:
            actual = self._kind(resource)
            legacy_kind = "accelerator" if actual.casefold() in _GPU_ALIASES else actual
            if kind is not None and legacy_kind != ("accelerator" if wanted == "gpu" else wanted):
                continue
            capacities = resource.get("capacities") if isinstance(resource.get("capacities"), Mapping) else {}
            result.append({"id": self._legacy_id(resource), "kind": legacy_kind,
                           "tags": self._tags(resource, resources),
                           "enabled": resource.get("available", True) is not False and bool(capacities)})
        return sorted(result, key=lambda item: str(item["id"]))

    def discover_gpus(self) -> list[dict[str, Any]]:
        return self.list(kind="accelerator")

    def upsert(self, resources: list[dict[str, Any]]) -> None:
        raise UnsupportedDeviceSchedulerOperation("resource discovery is Device Scheduler-owned")

    def replace(self, kind: str, resources: list[dict[str, Any]]) -> None:
        raise UnsupportedDeviceSchedulerOperation("resource discovery is Device Scheduler-owned")

    def _requirements(self, raw: object | None) -> list[dict[str, Any]]:
        """Map only the verified GPU-only external lease subset of the old DTO."""
        try:
            from .host_resources.contracts import normalize_resource_request
            request = normalize_resource_request(raw)
        except (TypeError, ValueError) as exc:
            raise DeviceSchedulerError("legacy resource request is invalid") from exc
        if str(request["kind"]).casefold() not in _GPU_ALIASES:
            raise UnsupportedDeviceSchedulerOperation("external DS leases currently support only GPU-only allocations")
        if (request["cpu_heavy"] or request["cpu_threads"] or request["ram_bytes"]
                or request["exclusive_resources"] or request["isolate_pcie_root"]
                or request["isolate_nvlink_domain"]):
            raise UnsupportedDeviceSchedulerOperation(
                "legacy CPU, memory, topology, and conflict-domain guarantees are not covered by GPU quiescence leases"
            )
        raw_ids = request["ids"]
        count = request["count"]
        if bool(raw_ids) == bool(count):
            raise DeviceSchedulerError("external GPU lease requires exactly one of ids or positive count")

        resources = self._resources()
        aliases = {self._legacy_id(item): item for item in resources}
        wanted_tags = request["tags"]
        candidates = []
        for item in resources:
            capacities = item.get("capacities") if isinstance(item.get("capacities"), Mapping) else {}
            capability = item.get("external_lease")
            try:
                capacity = capacities.get("memory_bytes")
                valid_capacity = (not isinstance(capacity, bool) and isinstance(capacity, (int, float))
                                  and math.isfinite(float(capacity)) and float(capacity) > 0)
            except (TypeError, ValueError, OverflowError):
                valid_capacity = False
            if (self._kind(item).casefold() not in _GPU_ALIASES or item.get("available") is False
                    or not valid_capacity or not isinstance(capability, Mapping)
                    or capability.get("supported") is not True
                    or capability.get("mode") != "exclusive_gpu_quiescence"
                    or capability.get("required_allocation_mode") != "exclusive"
                    or capability.get("release_method") != "repeatable_read_only_observation"):
                continue
            if all(self._tags(item, resources).get(key) == value for key, value in wanted_tags.items()):
                candidates.append(item)
        candidates.sort(key=lambda item: self._legacy_id(item))
        if raw_ids:
            chosen = []
            for legacy_id in raw_ids:
                item = aliases.get(legacy_id)
                if item is None or item not in candidates:
                    raise UnsupportedDeviceSchedulerOperation(
                        f"requested GPU lacks a verified exclusive reclamation capability: {legacy_id}"
                    )
                chosen.append(item)
        else:
            by_adapter: dict[str, list[dict[str, Any]]] = {}
            for item in candidates:
                adapter_id = item.get("source_adapter_id")
                if isinstance(adapter_id, str) and adapter_id:
                    by_adapter.setdefault(adapter_id, []).append(item)
            groups = [values for _adapter, values in sorted(by_adapter.items()) if len(values) >= count]
            if not groups:
                raise _NoGrant("insufficient_verified_gpu_resources")
            chosen = groups[0][:count]
        adapter_ids = {item.get("source_adapter_id") for item in chosen}
        if len(adapter_ids) != 1 or not isinstance(next(iter(adapter_ids)), str):
            raise UnsupportedDeviceSchedulerOperation("external GPU lease must use one trusted reclamation adapter")
        requirements: list[dict[str, Any]] = []
        for item in chosen:
            capacity = item["capacities"]["memory_bytes"]
            budget = item["external_lease"].get("admission_memory_bytes", capacity)
            if (isinstance(budget, bool) or not isinstance(budget, (int, float))
                    or not math.isfinite(budget) or budget <= 0 or budget > capacity):
                raise UnsupportedDeviceSchedulerOperation("GPU admission budget is invalid")
            requirements.append({"kind": "gpu", "quantities": {"memory_bytes": float(budget)},
                                 "selectors": {"resource_id": str(item["resource_id"])},
                                 "mode": "exclusive", "same_host": True})
        return requirements

    @staticmethod
    def _owner_context(owner_id: str, *, kind: str, project_root: str | Path,
                       pid: int | None = None, job_id: str | None = None,
                       attempt_id: str | None = None, service_id: str | None = None) -> dict[str, str]:
        process_id = os.getpid() if pid is None else pid
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise DeviceSchedulerError("owner process pid must be positive")
        context = {"issuer": "project-control", "owner_id": owner_id, "owner_kind": kind,
                   "project_root": str(Path(project_root).expanduser().resolve()), "pid": str(process_id)}
        for key, value in (("job_id", job_id), ("attempt_id", attempt_id), ("service_id", service_id)):
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise DeviceSchedulerError(f"{key} must be non-empty")
                context[key] = value
        start = _process_start(process_id)
        if start:
            context["process_start"] = start
        return context

    @staticmethod
    def _background_owner_id(project_root: str | Path, job_id: str, attempt_id: str) -> str:
        root = str(Path(project_root).expanduser().resolve())
        return "pc:background:" + _digest({"project_root": root, "job_id": job_id, "attempt_id": attempt_id})[:32]

    def _request(self, owner_id: str, context: Mapping[str, str], request: object | None,
                 *, idempotency_key: str | None = None,
                 requirements: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        payload = {"request_id": owner_id, "owner_id": owner_id, "owner_context": dict(context),
                   "requirements": requirements if requirements is not None else self._requirements(request),
                   "lease_ttl_seconds": self._ttl,
                   "idempotency_key": idempotency_key or "pc-lease-" + _digest({"owner": owner_id, "request": request})[:40]}
        try:
            result = self._call("leases.request", {"request": payload})
        except _NoGrant:
            return None
        lease = result.get("lease", result)
        if not isinstance(lease, Mapping) or lease.get("owner_id") != owner_id:
            raise DeviceSchedulerError("leases.request returned an invalid owner")
        return dict(lease) if lease.get("state") == "active" else None

    def reserve_background(self, *, project_root: str | Path, job_id: str, attempt_id: str,
                           resource_request: object, pid: int | None = None) -> dict[str, Any] | None:
        if not isinstance(job_id, str) or not job_id or not isinstance(attempt_id, str) or not attempt_id:
            raise DeviceSchedulerError("background owner requires non-empty job and attempt IDs")
        owner_id = self._background_owner_id(project_root, job_id, attempt_id)
        context = self._owner_context(owner_id, kind="background", project_root=project_root,
                                      pid=pid, job_id=job_id, attempt_id=attempt_id)
        requirements = self._requirements(resource_request)
        lease = self._request(owner_id, context, resource_request,
                              idempotency_key="pc-bg-" + _digest({"owner": owner_id, "request": resource_request})[:40],
                              requirements=requirements)
        if lease is None:
            return None
        expected = {item["selectors"]["resource_id"]: item["quantities"]["memory_bytes"]
                    for item in requirements}
        allocations = lease.get("allocations")
        actual: set[str] = set()
        if isinstance(allocations, (list, tuple)):
            for allocation in allocations:
                if not isinstance(allocation, Mapping):
                    actual.add("")
                    continue
                actual.add(str(allocation.get("resource_id", "")))
                quantities = allocation.get("quantities") if isinstance(allocation.get("quantities"), Mapping) else {}
                if (allocation.get("mode") != "exclusive"
                        or quantities.get("memory_bytes") != expected.get(str(allocation.get("resource_id")))):
                    self._reject_grant(lease, "Device Scheduler grant did not match the verified exclusive GPU request")
        lease_context = lease.get("owner_context")
        if (actual != set(expected) or not isinstance(lease_context, Mapping)
                or dict(lease_context) != context):
            self._reject_grant(lease, "Device Scheduler grant did not match the Project Control owner request")
        return self._reservation(lease)

    def _reject_grant(self, lease: Mapping[str, Any], reason: str) -> None:
        lease_id = lease.get("lease_id")
        fencing_token = lease.get("fencing_token")
        if not isinstance(lease_id, str) or not lease_id or isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
            raise DeviceSchedulerReclamationPending("mismatched scheduler grant cannot be safely released")
        result = self._call("leases.release", {"lease_id": lease_id, "fencing_token": fencing_token})
        released = result.get("lease", result)
        if not isinstance(released, Mapping) or released.get("state") != "released":
            state = released.get("state", "unknown") if isinstance(released, Mapping) else "unknown"
            raise DeviceSchedulerReclamationPending(f"mismatched scheduler grant remains {state}")
        raise DeviceSchedulerError(reason)

    def reserve_service(self, *, project_root: str | Path, service_id: str,
                        resource_request: object, priority_class: str = "idle_model_residency",
                        pid: int | None = None) -> dict[str, Any] | None:
        raise UnsupportedDeviceSchedulerOperation("managed services must use an operator-registered Device Scheduler backend")

    def begin_foreground(self, *, project_root: str | Path, resource_request: object,
                         priority_class: str = "foreground_gpu", pid: int | None = None) -> dict[str, Any]:
        raise UnsupportedDeviceSchedulerOperation(
            "Project Control foreground handoff is unavailable until it supports a verified DS continuation; unmanaged allocation is disabled"
        )

    def _leases(self) -> list[dict[str, Any]]:
        result = self._call("leases.list")
        values = result.get("leases")
        if not isinstance(values, list):
            raise DeviceSchedulerError("leases.list omitted leases")
        return [_as_mapping(item, "lease") for item in values]

    def _matching_leases(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        result = []
        for lease in self._leases():
            context = lease.get("owner_context")
            if (not str(lease.get("owner_id", "")).startswith("pc:")
                    or not isinstance(context, Mapping) or context.get("issuer") != "project-control"
                    or context.get("owner_id") != lease.get("owner_id")):
                continue
            if owner_id is None or lease.get("owner_id") == owner_id:
                result.append(lease)
        return result

    def _current_lease(self, owner_id: str) -> dict[str, Any] | None:
        matches = self._matching_leases(owner_id)
        active = [item for item in matches if str(item.get("state")) in _ACTIVE_LEASE_STATES]
        if len(active) > 1:
            raise DeviceSchedulerError("multiple active DS leases match one Project Control owner")
        return active[0] if active else (matches[0] if matches else None)

    def _resources_for_lease(self, lease: Mapping[str, Any]) -> list[str]:
        resources = {str(item.get("resource_id")): item for item in self._resources()}
        result = []
        allocations = lease.get("allocations", ())
        if not isinstance(allocations, (list, tuple)):
            raise DeviceSchedulerError("lease allocations are invalid")
        context = lease.get("owner_context") if isinstance(lease.get("owner_context"), Mapping) else {}
        foreground = context.get("owner_kind") == "foreground"
        for allocation in allocations:
            if not isinstance(allocation, Mapping):
                raise DeviceSchedulerError("lease allocation is invalid")
            rid = str(allocation.get("resource_id", ""))
            resource = resources.get(rid)
            if resource is None:
                if rid.startswith("gpu:"):
                    parts = rid.split(":", 2)
                    if len(parts) == 3:
                        result.append("accelerator:" + parts[2])
                elif foreground:
                    result.append(rid)
                continue
            kind = self._kind(resource).casefold()
            if kind in _GPU_ALIASES:
                result.append(self._legacy_id(resource))
            elif foreground and kind not in {"cpu", "memory"}:
                result.append(rid)
        return sorted(set(result))

    def _reservation(self, lease: Mapping[str, Any]) -> dict[str, Any]:
        return {"owner_id": str(lease["owner_id"]), "resource_ids": self._resources_for_lease(lease)}

    def owner(self, owner_id: str) -> dict[str, Any] | None:
        lease = self._current_lease(owner_id)
        if lease is None:
            return None
        context = lease.get("owner_context")
        if not isinstance(context, Mapping) or context.get("issuer") != "project-control" or context.get("owner_id") != owner_id:
            return None
        allocations = lease.get("allocations", ())
        resources = {str(item.get("resource_id")): item for item in self._resources()}
        cpu_threads = 0
        ram_bytes = 0
        for allocation in allocations if isinstance(allocations, (list, tuple)) else ():
            if not isinstance(allocation, Mapping):
                continue
            resource = resources.get(str(allocation.get("resource_id", "")), {})
            quantities = allocation.get("quantities") if isinstance(allocation.get("quantities"), Mapping) else {}
            if str(resource.get("kind", "")).casefold() == "cpu":
                cpu_threads += int(float(quantities.get("cores", 0)))
            elif str(resource.get("kind", "")).casefold() == "memory":
                ram_bytes += int(float(quantities.get("bytes", 0)))
        try:
            pid = int(context["pid"]) if context.get("pid") else None
        except (TypeError, ValueError):
            pid = None
        state = str(lease.get("state", "unknown"))
        return {"id": owner_id, "owner_id": owner_id, "owner_kind": context.get("owner_kind", "external"),
                "project_root": context.get("project_root"), "job_id": context.get("job_id"),
                "attempt_id": context.get("attempt_id"), "service_id": context.get("service_id"),
                "pid": pid, "process_start": context.get("process_start"),
                "state": state, "preempt_requested": None, "priority_class": None, "preemptible": False,
                "residency_protected": False, "residency_metadata_json": "{}",
                "residency_release_verified": False, "cpu_threads": cpu_threads, "ram_bytes": ram_bytes,
                "acquired_at": None, "heartbeat_at": None,
                "resources": [] if state == "released" else self._resources_for_lease(lease),
                "lease_id": lease.get("lease_id"), "fencing_token": lease.get("fencing_token"),
                "expires_at": lease.get("expires_at")}

    def owners_for_attempt(self, project_root: str | Path, attempt_id: str) -> list[dict[str, Any]]:
        root = str(Path(project_root).expanduser().resolve())
        values = []
        for lease in self._matching_leases():
            context = lease.get("owner_context")
            if context.get("project_root") == root and context.get("attempt_id") == attempt_id:
                owner = self.owner(str(lease.get("owner_id", "")))
                if owner is not None:
                    values.append(owner)
        return sorted(values, key=lambda item: str(item.get("expires_at", "")))

    def conflicts(self, resource_ids: Iterable[str]) -> list[str]:
        wanted = {str(item) for item in resource_ids}
        if not wanted:
            return []
        resources = self._resources()
        ids = {self._legacy_id(item): str(item.get("resource_id")) for item in resources}
        ids.update({str(item.get("resource_id")): str(item.get("resource_id")) for item in resources})
        translated = {ids[item] for item in wanted if item in ids}
        if len(translated) != len(wanted):
            raise DeviceSchedulerError("cannot map resource IDs to current DS inventory")
        conflicts = []
        for lease in self._leases():
            if str(lease.get("state")) not in _ACTIVE_LEASE_STATES:
                continue
            allocations = lease.get("allocations", ())
            if isinstance(allocations, (list, tuple)) and any(
                    isinstance(item, Mapping) and str(item.get("resource_id")) in translated for item in allocations):
                conflicts.append(str(lease.get("owner_id", "")))
        return sorted(set(filter(None, conflicts)))

    def wait_for_quiescence(self, resource_ids: Iterable[str], *, timeout_seconds: float = 30.0,
                            poll_seconds: float = 0.1) -> bool:
        deadline = self._clock() + max(0.0, timeout_seconds)
        while self.conflicts(resource_ids):
            if self._clock() >= deadline:
                return False
            self._sleep(max(0.01, poll_seconds))
        return True

    def activate_foreground(self, reservation: Mapping[str, Any]) -> bool:
        owner_id = reservation.get("owner_id") if isinstance(reservation, Mapping) else None
        owner = self.owner(owner_id) if isinstance(owner_id, str) else None
        requested = reservation.get("resource_ids") if isinstance(reservation, Mapping) else None
        return bool(owner and owner.get("state") == "active" and isinstance(requested, (list, tuple))
                    and sorted(str(item) for item in requested) == sorted(owner.get("resources", ())))

    def heartbeat(self, owner_id: str, *, pid: int | None = None) -> None:
        lease = self._current_lease(owner_id)
        if lease is None or lease.get("state") != "active":
            raise DeviceSchedulerError("no active Device Scheduler lease for owner")
        context = lease.get("owner_context")
        if not isinstance(context, Mapping) or context.get("issuer") != "project-control":
            raise DeviceSchedulerError("lease is outside the Project Control owner namespace")
        if pid is not None and str(pid) != str(context.get("pid")):
            raise UnsupportedDeviceSchedulerOperation("owner process identity is immutable under DS leases")
        self._call("leases.renew", {"lease_id": lease["lease_id"], "fencing_token": lease["fencing_token"],
                                     "ttl_seconds": self._ttl})

    def release(self, owner_id: str, *, residency_quiescence: dict[str, Any] | None = None,
                residency_capability: str | None = None, generation: str | None = None) -> None:
        if any(value is not None for value in (residency_quiescence, residency_capability, generation)):
            raise UnsupportedDeviceSchedulerOperation("legacy residency evidence is not accepted by DS")
        lease = self._current_lease(owner_id)
        if lease is None or lease.get("state") == "released":
            return
        result = self._call("leases.release", {"lease_id": lease["lease_id"],
                                                "fencing_token": lease["fencing_token"]})
        released = result.get("lease", result)
        if not isinstance(released, Mapping) or released.get("state") != "released":
            state = released.get("state", "unknown") if isinstance(released, Mapping) else "unknown"
            raise DeviceSchedulerReclamationPending(f"Device Scheduler release remains {state}")

    def compound_gpu_bundles(self, count: int) -> list[dict[str, Any]]:
        resources = self._resources()
        isolation = [item for item in resources if self._kind(item).casefold() == "isolation"]
        gpus = [item for item in resources if self._kind(item).casefold() in _GPU_ALIASES]
        groups: dict[str, list[dict[str, Any]]] = {}
        for gpu in gpus:
            rid = str(gpu.get("resource_id", ""))
            domains = [item for item in isolation if rid in self._domain_members(item)]
            groups.setdefault(str(domains[0].get("resource_id")) if len(domains) == 1 else rid, []).append(gpu)
        import itertools
        candidates = [combo for group in groups.values() for combo in itertools.combinations(group, count)]
        if not candidates:
            candidates = list(itertools.combinations(gpus, count))
        result = []
        for combo in candidates:
            ids = sorted(self._legacy_id(item) for item in combo)
            ds_ids = {str(item.get("resource_id")) for item in combo}
            domains = sorted(str(item.get("resource_id")) for item in isolation
                             if self._domain_members(item).intersection(ds_ids))
            if not self.conflicts([*ids, *domains]):
                result.append({"resource_ids": ids, "exclusive_resources": domains,
                               "selection": {"topology_domains": domains}})
        return sorted(result, key=lambda item: (len(item["exclusive_resources"]), item["resource_ids"]))

    def release_observation_counts(self) -> tuple[int, int, int]:
        # Count every visible active lease, not only PC leases: external users
        # must conservatively block Project Control physical-release claims.
        active = [lease for lease in self._leases() if lease.get("state") in _ACTIVE_LEASE_STATES]
        return len(active), sum(len(lease.get("allocations", ())) for lease in active), 0

    def owner_activity(self, owner_ids: list[str]) -> tuple[int, int]:
        wanted = set(owner_ids)
        active = [lease for lease in self._matching_leases() if lease.get("owner_id") in wanted
                  and lease.get("state") in _ACTIVE_LEASE_STATES]
        return len({str(lease.get("owner_id")) for lease in active}), sum(
            len(lease.get("allocations", ())) for lease in active
        )

    def reconcile_current_service_owners(self, **_: Any) -> list[str]:
        raise UnsupportedDeviceSchedulerOperation("DS owns lease reconciliation; no local stale-owner sweep is permitted")

    def preempt_requested(self, owner_id: str) -> bool:
        raise UnsupportedDeviceSchedulerOperation("DS leases do not expose a legacy preemption signal")

    def request_preemption(self, owner_id: str) -> bool:
        raise UnsupportedDeviceSchedulerOperation("lease release is not an implicit preemption grant")

    def set_priority(self, owner_id: str, priority_class: str) -> bool:
        raise UnsupportedDeviceSchedulerOperation("legacy owner priority mutation has no lease equivalent")

    def protect_residency(self, owner_id: str, *, memory_baseline: dict[str, float]) -> dict[str, Any]:
        raise UnsupportedDeviceSchedulerOperation("residency protection belongs to an operator-registered DS backend")

    def record_residency_process(self, owner_id: str, **_: Any) -> None:
        raise UnsupportedDeviceSchedulerOperation("DS backend owns managed process tracking")

    def register_drain_callback(self, owner_id: str, callback: Any) -> None:
        raise UnsupportedDeviceSchedulerOperation("in-process drain callbacks are unsupported under DS authority")

    def drain_if_requested(self, owner_id: str) -> bool:
        raise UnsupportedDeviceSchedulerOperation("DS leases do not expose a legacy preemption signal")


class _NoGrant(DeviceSchedulerError):
    """A scheduler conflict means no lease was granted."""
