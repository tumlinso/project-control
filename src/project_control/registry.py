from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import ProgramConfig, ProjectControlConfig, RepositoryConfig, WorkspaceConfig


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

    def resolve_workspace(self, selector: str, *, allowed_workspaces: set[str] | frozenset[str]):
        """Resolve a human project selector without crossing the caller's allowlist.

        Canonical IDs win exact matches. Display names and registered repository
        aliases/root basenames are case-insensitive labels; exact registered root
        paths are matched separately. Ambiguity candidates contain canonical
        IDs only and are drawn exclusively from ``allowed_workspaces``.
        """
        if not isinstance(selector, str) or not selector.strip():
            raise RegistryError("unknown workspace")
        selector = selector.strip()
        permitted = set(allowed_workspaces) & self.config.workspaces.keys()
        if selector in permitted:
            return selector, ()
        if selector in self.config.workspaces:
            raise RegistryError("workspace_not_permitted")

        matches: set[str] = set()
        folded = " ".join(selector.split()).casefold()
        path_selector = None
        try:
            supplied_path = Path(selector).expanduser()
            if supplied_path.is_absolute():
                path_selector = supplied_path.resolve(strict=False)
        except (OSError, RuntimeError):
            path_selector = None

        for workspace_id in sorted(permitted):
            workspace = self.config.workspaces[workspace_id]
            if workspace.display_name and " ".join(workspace.display_name.split()).casefold() == folded:
                matches.add(workspace_id)
            for alias, repository in workspace.repositories.items():
                try:
                    root = repository.root.expanduser().resolve(strict=False)
                except (OSError, RuntimeError):
                    continue
                if path_selector is not None and root == path_selector:
                    matches.add(workspace_id)
                for label in (alias, root.name):
                    if label.casefold() == folded:
                        matches.add(workspace_id)

        if len(matches) == 1:
            return next(iter(matches)), ()
        if matches:
            return None, tuple(sorted(matches))
        raise RegistryError("workspace_not_permitted")

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

    def program(self, program_id: str) -> ProgramConfig:
        validate_id(program_id, "program")
        try:
            return self.config.programs[program_id]
        except KeyError as exc:
            raise RegistryError("unknown program") from exc

    def program_workspaces(
        self,
        *,
        program_id: str | None = None,
        workspaces: list[str] | None = None,
    ) -> tuple[list[str], dict[str, object]]:
        """Resolve a configured or explicit query grouping.

        The returned metadata makes the non-authoritative nature of membership
        explicit so callers cannot accidentally present it as a dependency.
        """

        explicit = list(workspaces or [])
        if bool(program_id) == bool(explicit):
            raise RegistryError("select exactly one configured program or explicit workspace list")
        if program_id:
            program = self.program(program_id)
            selected = list(program.workspaces)
            metadata: dict[str, object] = {
                "id": program_id,
                "display_name": program.display_name,
                "selection": "configured_program",
            }
        else:
            if len(explicit) > 16:
                raise RegistryError("explicit workspace list exceeds 16")
            if len(explicit) != len(set(explicit)):
                raise RegistryError("explicit workspace list must be unique")
            selected = explicit
            metadata = {"id": None, "display_name": None, "selection": "explicit_workspace_list"}
        for workspace_id in selected:
            self.workspace(workspace_id)
        metadata["membership_semantics"] = "query_grouping_only"
        metadata["architectural_authority"] = False
        return selected, metadata

    def add_workspace(
        self,
        workspace_id: str,
        repository_alias: str,
        root: Path,
        *,
        authority: bool = False,
        display_name: str | None = None,
        live_links: Mapping[str, Path] | None = None,
        gitlink_policy: str | None = None,
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
                repositories={repository_alias: RepositoryConfig(
                    root=resolved, live_links=dict(live_links or {}),
                    gitlink_policy=gitlink_policy if gitlink_policy is not None else "reject",
                )},
            )
            self.config.workspaces[workspace_id] = workspace
            return
        existing = workspace.repositories.get(repository_alias)
        merged_live_links = dict(existing.live_links if existing else {})
        if live_links is not None:
            merged_live_links.update(live_links)
        workspace.repositories[repository_alias] = RepositoryConfig(
            root=resolved,
            live_links=merged_live_links,
            gitlink_policy=(gitlink_policy if gitlink_policy is not None
                            else existing.gitlink_policy if existing else "reject"),
        )
        if authority or workspace.authority_repository is None:
            workspace.authority_repository = repository_alias
        if display_name:
            workspace.display_name = display_name

    def remove_workspace(self, workspace_id: str) -> None:
        self.workspace(workspace_id)
        del self.config.workspaces[workspace_id]
