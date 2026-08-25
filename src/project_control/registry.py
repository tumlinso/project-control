from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class RegistryError(ValueError):
    pass


def validate_id(value: str, kind: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise RegistryError(f"invalid {kind} ID")
    return value


@dataclass(frozen=True)
class RegisteredRepository:
    workspace_id: str
    alias: str
    root: Path


class WorkspaceRegistry:
    def __init__(self, config: ProjectControlConfig):
        self.config = config

    def workspace(self, workspace_id: str) -> WorkspaceConfig:
        validate_id(workspace_id, "workspace")
        try:
            return self.config.workspaces[workspace_id]
        except KeyError as exc:
            raise RegistryError("unknown workspace") from exc

    def repository(self, workspace_id: str, alias: str | None = None) -> RegisteredRepository:
        workspace = self.workspace(workspace_id)
        selected = alias or workspace.authority_repository
        if not selected:
            if len(workspace.repositories) != 1:
                raise RegistryError("repository alias is required")
            selected = next(iter(workspace.repositories))
        validate_id(selected, "repository")
        try:
            root = workspace.repositories[selected].root.resolve(strict=True)
        except KeyError as exc:
            raise RegistryError("unknown repository") from exc
        except FileNotFoundError as exc:
            raise RegistryError("registered repository is unavailable") from exc
        if not root.is_dir():
            raise RegistryError("registered repository root is not a directory")
        return RegisteredRepository(workspace_id, selected, root)

    def add_workspace(
        self,
        workspace_id: str,
        repository_alias: str,
        root: Path,
        *,
        authority: bool = False,
        display_name: str | None = None,
    ) -> None:
        validate_id(workspace_id, "workspace")
        validate_id(repository_alias, "repository")
        resolved = root.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise RegistryError("repository root is not a directory")
        workspace = self.config.workspaces.get(workspace_id)
        if workspace is None:
            workspace = WorkspaceConfig(
                display_name=display_name,
                authority_repository=repository_alias,
                repositories={repository_alias: RepositoryConfig(root=resolved)},
            )
            self.config.workspaces[workspace_id] = workspace
            return
        workspace.repositories[repository_alias] = RepositoryConfig(root=resolved)
        if authority or workspace.authority_repository is None:
            workspace.authority_repository = repository_alias
        if display_name:
            workspace.display_name = display_name

    def remove_workspace(self, workspace_id: str) -> None:
        self.workspace(workspace_id)
        del self.config.workspaces[workspace_id]
