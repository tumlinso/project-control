from __future__ import annotations

import os
import hashlib
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.cuda import CudaReadAdapter
from .adapters.git import GitReadAdapter
from .adapters.host import HostReadAdapter
from .adapters.local_worker import LocalWorkerReadAdapter
from .adapters.todo import TodoReadAdapter
from .cache import RevisionCache
from .config import ProjectControlConfig
from .models import ProjectSnapshot, RepositoryIdentity, utc_now
from .registry import WorkspaceRegistry
from .subprocesses import CommandError, FixedCommandRunner


REQUIRED_TODO_READ_CAPABILITIES = (
    "semantic_state",
    "semantic_anchor",
    "semantic_delta",
    "semantic_workflow",
    "export",
)


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

    def local_diagnostics(self) -> dict[str, Any]:
        return {
            "status": "available" if self.compatible else "unavailable",
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
    value = (_provider_version(script), digest, tuple(supported), set(supported) == set(REQUIRED_TODO_READ_CAPABILITIES))
    _PROVIDER_CACHE[cache_key] = value
    return value


def resolve_todo_provider(config: ProjectControlConfig, workspace_id: str) -> TodoProviderResolution:
    workspace = config.workspaces[workspace_id]
    raw_candidates = [
        ("workspace_config", workspace.skills_root, False),
        ("global_config", config.skills_root, False),
        (
            "service_environment",
            Path(os.environ["PROJECT_CONTROL_SKILLS_ROOT"]) if os.environ.get("PROJECT_CONTROL_SKILLS_ROOT") else None,
            False,
        ),
        ("legacy_agents_fallback", Path("/home/tumlinson/.agents/skills"), True),
        ("legacy_codex_fallback", Path("/home/tumlinson/.codex/skills"), True),
    ]
    seen: set[Path] = set()
    incompatible: TodoProviderResolution | None = None
    for source, candidate, legacy in raw_candidates:
        if legacy and incompatible is not None:
            return incompatible
        if candidate is None:
            continue
        root = candidate.expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        script = root / "todo-orchestrator" / "scripts" / "todo.py"
        if not script.is_file():
            continue
        try:
            version, identity, capabilities, compatible = _probe_todo_entrypoint(script.resolve())
        except OSError:
            continue
        warnings = ("legacy_skills_root_fallback",) if legacy else ()
        result = TodoProviderResolution(
            root, script.resolve(), compatible, source, version, identity, capabilities, warnings,
            None if compatible else "todo_entrypoint_incompatible",
        )
        if compatible:
            return result
        if incompatible is None:
            incompatible = result
    return incompatible or TodoProviderResolution(
        None, None, False, None, None, None, (), (), "skills_root_unavailable"
    )


def resolve_skills_root(config: ProjectControlConfig, workspace_id: str) -> Path | None:
    provider = resolve_todo_provider(config, workspace_id)
    return provider.skills_root if provider.compatible else None


class SnapshotBuilder:
    def __init__(self, config: ProjectControlConfig):
        self.config = config
        self.registry = WorkspaceRegistry(config)
        self.cache: RevisionCache[ProjectSnapshot] = RevisionCache()

    def build(self, workspace_id: str, *, include_host: bool = False, campaign: str | None = None) -> ProjectSnapshot:
        workspace = self.registry.workspace(workspace_id)
        repositories: dict[str, RepositoryIdentity] = {}
        fingerprints: dict[str, str] = {}
        git_adapters: dict[str, GitReadAdapter] = {}
        warnings: list[str] = []
        provider_warnings: dict[str, list[str]] = {}
        for alias in sorted(workspace.repositories):
            registered = self.registry.repository(workspace_id, alias)
            try:
                git = GitReadAdapter(registered.root)
                identity = git.identity()
                repositories[alias] = RepositoryIdentity(
                    commit=identity.commit,
                    dirty=identity.dirty,
                    working_tree_fingerprint=identity.status_fingerprint,
                )
                fingerprints[alias] = identity.status_fingerprint
                git_adapters[alias] = git
            except Exception:
                warnings.append(f"git_identity_unavailable:{alias}")
        todo_revision = None
        project_uuid = None
        todo_status: dict[str, Any] = {}
        todo_tables: dict[str, list[dict[str, Any]]] = {}
        todo_semantic: dict[str, Any] = {}
        todo_workflow: dict[str, Any] = {}
        authority = workspace.authority_repository
        provider = resolve_todo_provider(self.config, workspace_id)
        skills_root = provider.skills_root if provider.compatible else None
        if authority and authority in git_adapters and skills_root and provider.todo_script:
            try:
                todo = TodoReadAdapter(
                    self.registry.repository(workspace_id, authority).root,
                    provider.todo_script,
                ).observe()
                todo_revision = todo.revision
                project_uuid = todo.project_uuid
                todo_status = todo.status
                todo_status["component_authority"] = {
                    key: component.public() for key, component in todo.components.items()
                }
                todo_status["observation_consistency"] = todo.consistency
                todo_semantic = todo.semantic
                todo_workflow = todo.workflow
                tables = todo.state.get("tables", {})
                if isinstance(tables, dict):
                    todo_tables = {key: value for key, value in tables.items() if isinstance(value, list)}
                provider_warnings["todo"] = list(todo.warnings)
                provider_warnings["todo_provider"] = list(provider.warnings)
            except Exception as exc:
                code = getattr(exc, "code", None)
                provider_warnings["todo"] = [code if isinstance(code, str) else "todo_authority_unavailable"]
        else:
            if not authority:
                provider_warnings["todo"] = ["todo_not_configured"]
            elif skills_root is None:
                provider_warnings["todo"] = [provider.error_code or "skills_root_unavailable"]
            else:
                provider_warnings["todo"] = ["todo_read_command_unavailable"]

        authority_root = self.registry.repository(workspace_id, authority).root if authority else next(iter(workspace.repositories.values())).root
        cuda = CudaReadAdapter(authority_root).status(campaign)
        worker = LocalWorkerReadAdapter(LocalWorkerReadAdapter.current_state_path()).status()
        host = HostReadAdapter().capacity() if include_host else {}
        provider_warnings["cuda"] = list(cuda.get("warnings", []))
        provider_warnings["worker"] = list(worker.get("warnings", []))
        if include_host:
            provider_warnings["host"] = list(host.get("warnings", []))
        cache_key = (
            workspace_id,
            tuple((key, item.commit, item.dirty, fingerprints.get(key)) for key, item in repositories.items()),
            todo_revision,
            include_host,
            campaign,
        )
        snapshot = ProjectSnapshot(
            workspace_id=workspace_id,
            display_name=workspace.display_name,
            observed_at=utc_now(),
            todo_revision=todo_revision,
            project_uuid=project_uuid,
            repositories=repositories,
            repository_fingerprints=fingerprints,
            todo_status=todo_status,
            todo_tables=todo_tables,
            todo_semantic=todo_semantic,
            todo_workflow=todo_workflow,
            local_worker=worker,
            cuda=cuda,
            host=host,
            warnings=list(dict.fromkeys(warnings)),
            provider_warnings=provider_warnings,
        )
        return self.cache.put(cache_key, snapshot)
