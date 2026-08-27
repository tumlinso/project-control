from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adapters.git import GitReadAdapter, GitWorktreeIdentity
from .security import stable_public_id


class WorktreeSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class WorktreeSelection:
    repository: str
    registered_root_id: str
    common_id: str
    selected: GitWorktreeIdentity

    def public(self) -> dict[str, object]:
        item = self.selected
        return {
            "id": item.worktree_id,
            "repository": self.repository,
            "branch": item.branch,
            "head": item.head,
            "detached": item.detached,
            "dirty": item.dirty,
            "working_tree_fingerprint": item.working_tree_fingerprint,
            "dirty_paths": list(item.dirty_paths),
            "observed_at": item.observed_at,
        }


class WorktreeCatalog:
    """Verified read-only view of worktrees belonging to one registered repository."""

    def __init__(self, repository: str, registered_root: Path):
        self.repository = repository
        self.registered_root = registered_root.resolve(strict=True)
        self.git = GitReadAdapter(self.registered_root)
        self.common = self.git.common_dir()
        self.common_id = stable_public_id("git-common", self.common)
        self.registered_root_id = stable_public_id("registered-root", self.common, self.registered_root)

    def discover(self) -> tuple[GitWorktreeIdentity, ...]:
        # GitReadAdapter re-verifies the common directory of every listed root.
        return self.git.worktrees()

    def select(self, worktree_id: str | None = None) -> WorktreeSelection:
        values = self.discover()
        if not values:
            raise WorktreeSelectionError("repository_worktrees_unavailable")
        if worktree_id is None:
            selected = next((item for item in values if item.root == self.registered_root), None)
        else:
            selected = next((item for item in values if item.worktree_id == worktree_id), None)
        if selected is None:
            raise WorktreeSelectionError("unknown_or_unrelated_worktree")
        return WorktreeSelection(self.repository, self.registered_root_id, self.common_id, selected)
