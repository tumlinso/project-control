from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json

from .adapters.cuda import CudaReadAdapter
from .adapters.git import GitReadAdapter
from .adapters.host import HostReadAdapter
from .adapters.todo import TodoReadAdapter
from .cache import RevisionCache
from .config import ProjectControlConfig
from .models import ProjectSnapshot, RepositoryIdentity, WorktreeIdentity, utc_now
from .registry import WorkspaceRegistry
from .security import stable_public_id
from .todo_authority import (
    TodoProviderResolution,
    TodoReadPortFactory,
    resolve_todo_provider,
)


class SnapshotBuilder:
    def __init__(self, config: ProjectControlConfig, *, todo_read_port_factory: TodoReadPortFactory | None = None,
                 todo_principal: str = "project-control-observer", todo_profile: str = "observer"):
        self.config = config
        self.registry = WorkspaceRegistry(config)
        self.cache: RevisionCache[ProjectSnapshot] = RevisionCache()
        # Worktree status is an observation, not a cheap repository property.
        # Preserve its original observed_at while reusing it for at most the
        # cache TTL. Exact source reads and explicit Git inspection bypass this.
        self._worktree_cache: RevisionCache[tuple] = RevisionCache()
        self.todo_read_port_factory = todo_read_port_factory
        self.todo_principal = todo_principal
        self.todo_profile = todo_profile
        self._todo_providers: dict[str, TodoProviderResolution] = {}

    def _todo_provider(self, workspace_id: str) -> TodoProviderResolution:
        provider = self._todo_providers.get(workspace_id)
        if provider is None:
            workspace = self.registry.workspace(workspace_id)
            authority_root = (self.registry.repository(workspace_id, workspace.authority_repository).root
                             if workspace.authority_repository else None)
            provider = resolve_todo_provider(
                self.config, workspace_id, read_port_factory=self.todo_read_port_factory,
                project_root=authority_root, principal=self.todo_principal, profile=self.todo_profile,
            )
            self._todo_providers[workspace_id] = provider
        return provider

    @staticmethod
    def _worktree_membership(common: Path) -> tuple:
        directory = common / "worktrees"
        try:
            return tuple(sorted((entry.name, entry.stat().st_mtime_ns)
                                for entry in directory.iterdir()))
        except OSError:
            return ()

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
                common = git.common_dir()
                worktree_key = (alias, str(registered.root), identity.commit,
                    identity.status_fingerprint, self._worktree_membership(common))
                worktree_values = self._worktree_cache.get(worktree_key)
                if worktree_values is None:
                    worktree_values = git.worktrees()
                    self._worktree_cache.put(worktree_key, worktree_values)
                repositories[alias] = RepositoryIdentity(
                    commit=identity.commit,
                    dirty=identity.dirty,
                    working_tree_fingerprint=identity.status_fingerprint,
                    git_common_id=stable_public_id("git-common", common),
                    current_worktree_id=next(
                        (item.worktree_id for item in worktree_values if item.root == registered.root.resolve()),
                        None,
                    ),
                    worktrees={
                        item.worktree_id: WorktreeIdentity(
                            id=item.worktree_id,
                            repository=alias,
                            branch=item.branch,
                            head=item.head,
                            detached=item.detached,
                            dirty=item.dirty,
                            working_tree_fingerprint=item.working_tree_fingerprint,
                            dirty_paths=list(item.dirty_paths),
                            observed_at=item.observed_at,
                        )
                        for item in worktree_values
                    },
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
        component_authority: dict[str, Any] = {}
        authority = workspace.authority_repository
        provider = self._todo_provider(workspace_id)
        if authority and authority in git_adapters and provider.compatible and provider.read_port:
            try:
                todo = TodoReadAdapter(
                    self.registry.repository(workspace_id, authority).root,
                    None,
                    read_port=provider.read_port,
                ).observe()
                todo_revision = todo.revision
                project_uuid = todo.project_uuid
                todo_status = todo.status
                todo_status["component_authority"] = {
                    key: component.public() for key, component in todo.components.items()
                }
                component_authority = todo_status["component_authority"]
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
            elif not provider.compatible or provider.read_port is None:
                provider_warnings["todo"] = [provider.error_code or "todo_provider_unavailable"]
            else:
                provider_warnings["todo"] = ["todo_read_port_unavailable"]

        authority_root = self.registry.repository(workspace_id, authority).root if authority else next(iter(workspace.repositories.values())).root
        cuda = CudaReadAdapter(authority_root).status(campaign)
        worker = {"status": "archived", "reason": "local investigator runtime moved out of Project Control"}
        host = HostReadAdapter().capacity() if include_host else {}
        provider_warnings["cuda"] = list(cuda.get("warnings", []))
        provider_warnings["worker"] = list(worker.get("warnings", []))
        if include_host:
            provider_warnings["host"] = list(host.get("warnings", []))
        cache_key = (
            workspace_id,
            tuple((
                key, item.commit, item.dirty, fingerprints.get(key),
                tuple((worktree_id, value.head, value.working_tree_fingerprint) for worktree_id, value in item.worktrees.items()),
            ) for key, item in repositories.items()),
            todo_revision,
            include_host,
            campaign,
            # Volatile providers and availability changes must not reuse a
            # snapshot just because the Git/Todo revisions stayed unchanged.
            hashlib.sha256(json.dumps({"cuda": cuda, "host": host,
                "warnings": warnings, "providers": provider_warnings},
                sort_keys=True, default=str).encode()).hexdigest(),
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(deep=True)
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
            component_authority=component_authority,
            local_worker=worker,
            cuda=cuda,
            host=host,
            warnings=list(dict.fromkeys(warnings)),
            provider_warnings=provider_warnings,
        )
        self.cache.put(cache_key, snapshot.model_copy(deep=True))
        return snapshot
