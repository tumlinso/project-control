from __future__ import annotations

import os
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


def resolve_skills_root(config: ProjectControlConfig, workspace_id: str) -> Path | None:
    workspace = config.workspaces[workspace_id]
    candidates = [
        workspace.skills_root,
        config.skills_root,
        Path(os.environ["PROJECT_CONTROL_SKILLS_ROOT"]) if os.environ.get("PROJECT_CONTROL_SKILLS_ROOT") else None,
        Path("/home/tumlinson/.agents/skills"),
        Path("/home/tumlinson/.codex/skills"),
    ]
    for candidate in candidates:
        if candidate and (candidate / "todo-orchestrator" / "scripts" / "todo.py").is_file():
            return candidate.resolve()
    return None


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
        authority = workspace.authority_repository
        skills_root = resolve_skills_root(self.config, workspace_id)
        if authority and authority in git_adapters and skills_root:
            try:
                todo = TodoReadAdapter(
                    self.registry.repository(workspace_id, authority).root,
                    skills_root / "todo-orchestrator" / "scripts" / "todo.py",
                ).observe()
                todo_revision = todo.revision
                project_uuid = todo.project_uuid
                todo_status = todo.status
                todo_semantic = todo.semantic
                tables = todo.state.get("tables", {})
                if isinstance(tables, dict):
                    todo_tables = {key: value for key, value in tables.items() if isinstance(value, list)}
                provider_warnings["todo"] = list(todo.warnings)
            except Exception as exc:
                code = getattr(exc, "code", None)
                provider_warnings["todo"] = [code if isinstance(code, str) else "todo_authority_unavailable"]
        else:
            if not authority:
                provider_warnings["todo"] = ["todo_not_configured"]
            elif skills_root is None:
                provider_warnings["todo"] = ["skills_root_unavailable"]
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
            local_worker=worker,
            cuda=cuda,
            host=host,
            warnings=list(dict.fromkeys(warnings)),
            provider_warnings=provider_warnings,
        )
        return self.cache.put(cache_key, snapshot)
