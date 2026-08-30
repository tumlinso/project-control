from __future__ import annotations

import hashlib
import os
import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .config import ProjectControlConfig
from .subprocesses import CommandError, FixedCommandRunner


TODO_READ_PORT_CONTRACT = "PCU-TODO-READ-PORT/1"
REQUIRED_TODO_READ_CAPABILITIES = (
    "semantic.state",
    "semantic.anchor",
    "semantic.delta",
    "semantic.workflow",
    "export",
)

# The CLI probe reports internal compatibility labels, while the canonical
# in-process port advertises the exact operation names accepted by ``invoke``.
# Keep these sets separate so validating one transport cannot accidentally
# weaken or rename the other.
_REQUIRED_TODO_ENTRYPOINT_CAPABILITIES = (
    "semantic_state",
    "semantic_anchor",
    "semantic_delta",
    "semantic_workflow",
    "export",
)


@runtime_checkable
class TodoReadPort(Protocol):
    """Narrow adapter seam for Todo's canonical in-process read facade.

    The Skills-owned implementation supplies this object during process
    initialization.  Project Control neither imports a guessed module name nor
    mutates ``sys.path`` to find it at request time.
    """

    def identity(self) -> Mapping[str, Any]: ...

    def invoke(
        self,
        operation: str,
        *,
        repo_root: Path,
        arguments: tuple[str, ...] = (),
    ) -> Mapping[str, Any]: ...


TodoReadPortFactory = Callable[[Path], TodoReadPort | None]


@dataclass(frozen=True)
class TodoProviderResolution:
    skills_root: Path | None
    todo_script: Path | None
    compatible: bool
    selection_source: str | None
    version: str | None
    file_identity: str | None
    capabilities: tuple[str, ...]
    warnings: tuple[str, ...]
    error_code: str | None = None
    mode: str | None = None
    read_port: TodoReadPort | None = None

    def local_diagnostics(self) -> dict[str, Any]:
        return {
            "status": "available" if self.compatible else "unavailable",
            "mode": self.mode,
            "selection_source": self.selection_source,
            "skills_root": str(self.skills_root) if self.skills_root else None,
            "todo_entrypoint": str(self.todo_script) if self.todo_script else None,
            "version": self.version,
            "file_identity": self.file_identity,
            "capabilities": list(self.capabilities),
            "warnings": list(self.warnings),
            "error_code": self.error_code,
        }


_PROVIDER_CACHE: dict[tuple[str, int, int], tuple[str | None, str, tuple[str, ...], bool]] = {}


def _provider_version(script: Path) -> str | None:
    pyproject = script.parent.parent / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    value = data.get("project", {}).get("version") if isinstance(data.get("project"), dict) else None
    if value is not None:
        return str(value)
    try:
        commit = FixedCommandRunner(max_capture_bytes=4096).run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=script.parent,
            timeout=5.0, check=True,
        ).stdout.strip()
    except CommandError:
        return None
    return commit or None


def _probe_todo_entrypoint(script: Path) -> tuple[str | None, str, tuple[str, ...], bool]:
    stat = script.stat()
    cache_key = (str(script), stat.st_mtime_ns, stat.st_size)
    if cache_key in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[cache_key]
    digest = hashlib.sha256(
        f"{script.resolve()}\0{stat.st_dev}\0{stat.st_ino}\0{stat.st_size}\0{stat.st_mtime_ns}".encode()
    ).hexdigest()
    runner = FixedCommandRunner(max_capture_bytes=512 * 1024)
    probes = {
        "semantic_state": ["semantic", "state", "--help"],
        "semantic_anchor": ["semantic", "anchor", "--help"],
        "semantic_delta": ["semantic", "delta", "--help"],
        "semantic_workflow": ["semantic", "workflow", "--help"],
        "export": ["export", "--help"],
    }
    supported: list[str] = []
    for capability, arguments in probes.items():
        try:
            result = runner.run(
                [sys.executable, str(script), *arguments],
                cwd=script.parent,
                timeout=5.0,
                env={"TODO_ORCHESTRATOR_READ_ONLY": "1"},
                check=False,
            )
        except CommandError:
            continue
        if result.returncode == 0:
            supported.append(capability)
    value = (
        _provider_version(script), digest, tuple(supported),
        set(supported) == set(_REQUIRED_TODO_ENTRYPOINT_CAPABILITIES),
    )
    _PROVIDER_CACHE[cache_key] = value
    return value


def _root_candidates(config: ProjectControlConfig, workspace_id: str) -> list[tuple[str, Path | None, tuple[str, ...]]]:
    workspace = config.workspaces[workspace_id]
    canonical = os.environ.get("PROJECT_CONTROL_SKILLS_ROOT")
    legacy = os.environ.get("CODING_WORKFLOW_SKILLS_ROOT")
    return [
        ("workspace_config", workspace.skills_root, ()),
        ("global_config", config.skills_root, ()),
        ("service_environment", Path(canonical) if canonical else None, ()),
        (
            "compatibility_environment",
            Path(legacy) if legacy and not canonical else None,
            ("coding_workflow_skills_root_deprecated",),
        ),
        ("legacy_agents_fallback", Path("/home/tumlinson/.agents/skills"), ("legacy_skills_root_fallback",)),
        ("legacy_codex_fallback", Path("/home/tumlinson/.codex/skills"), ("legacy_skills_root_fallback",)),
    ]


def _verify_read_port(port: TodoReadPort, root: Path) -> tuple[str | None, str | None, tuple[str, ...], str | None]:
    try:
        identity = dict(port.identity())
    except Exception:
        return None, None, (), "todo_read_port_identity_unavailable"
    if identity.get("contract") != TODO_READ_PORT_CONTRACT:
        return None, None, (), "todo_read_port_contract_mismatch"
    raw_root = identity.get("skills_root")
    try:
        reported_root = Path(str(raw_root)).expanduser().resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return None, None, (), "todo_read_port_identity_invalid"
    if reported_root != root:
        return None, None, (), "todo_read_port_authority_mismatch"
    capabilities = tuple(str(item) for item in identity.get("capabilities", ()))
    if not set(REQUIRED_TODO_READ_CAPABILITIES).issubset(capabilities):
        return None, None, capabilities, "todo_read_port_schema_incompatible"
    source_identity = identity.get("source_identity")
    if not isinstance(source_identity, str) or not source_identity:
        return None, None, capabilities, "todo_read_port_identity_invalid"
    version = identity.get("version")
    return str(version) if version is not None else None, source_identity, capabilities, None


def resolve_todo_provider(
    config: ProjectControlConfig,
    workspace_id: str,
    *,
    read_port_factory: TodoReadPortFactory | None = None,
) -> TodoProviderResolution:
    """Resolve exactly one verified Todo provider without runtime path mutation.

    An explicitly supplied in-process factory is authoritative.  If it returns
    an object whose contract or authority identity is wrong, resolution fails
    closed instead of silently selecting the subprocess fallback.
    """

    seen: set[Path] = set()
    incompatible: TodoProviderResolution | None = None
    for source, candidate, warnings in _root_candidates(config, workspace_id):
        if source.startswith("legacy_") and incompatible is not None:
            return incompatible
        if candidate is None:
            continue
        root = candidate.expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)

        if read_port_factory is not None:
            try:
                port = read_port_factory(root)
            except Exception:
                return TodoProviderResolution(
                    root, None, False, source, None, None, (), warnings,
                    "todo_read_port_initialization_failed", "in_process", None,
                )
            if port is not None:
                version, identity, capabilities, error = _verify_read_port(port, root)
                return TodoProviderResolution(
                    root, None, error is None, source, version, identity, capabilities,
                    warnings, error, "in_process", port if error is None else None,
                )

        script = root / "todo-orchestrator" / "scripts" / "todo.py"
        if not script.is_file():
            continue
        try:
            version, identity, capabilities, compatible = _probe_todo_entrypoint(script.resolve())
        except OSError:
            continue
        result = TodoProviderResolution(
            root, script.resolve(), compatible, source, version, identity, capabilities,
            warnings,
            None if compatible else "todo_entrypoint_incompatible", "subprocess", None,
        )
        if compatible:
            return result
        if incompatible is None:
            incompatible = result
    return incompatible or TodoProviderResolution(
        None, None, False, None, None, None, (), (), "skills_root_unavailable", None, None,
    )


def resolve_skills_root(
    config: ProjectControlConfig,
    workspace_id: str,
    *,
    read_port_factory: TodoReadPortFactory | None = None,
) -> Path | None:
    provider = resolve_todo_provider(config, workspace_id, read_port_factory=read_port_factory)
    return provider.skills_root if provider.compatible else None
