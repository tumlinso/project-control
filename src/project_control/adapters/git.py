from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..subprocesses import CommandError, FixedCommandRunner


@dataclass(frozen=True)
class GitIdentity:
    commit: str
    dirty: bool
    status_fingerprint: str
    changed_paths: tuple[str, ...]


class GitReadAdapter:
    _ALLOWED = {
        "rev-parse",
        "status",
        "log",
        "show",
        "diff",
        "merge-base",
        "ls-files",
        "grep",
    }

    def __init__(self, root: Path, runner: FixedCommandRunner | None = None):
        self.root = root.resolve(strict=True)
        self.runner = runner or FixedCommandRunner()

    def _git(self, operation: str, *arguments: str, timeout: float = 5.0) -> str:
        if operation not in self._ALLOWED:
            raise ValueError("Git operation is not read-only allowlisted")
        return self.runner.run(
            ["git", operation, *arguments], cwd=self.root, timeout=timeout
        ).stdout

    def identity(self) -> GitIdentity:
        commit = self._git("rev-parse", "--verify", "HEAD").strip()
        raw = self._git("status", "--porcelain=v2", "-z", "--untracked-files=all")
        records = tuple(item for item in raw.split("\x00") if item)
        relevant_records: list[str] = []
        paths: list[str] = []
        for record in records:
            fields = record.split(" ")
            path = fields[-1] if fields else ""
            if path and not path.startswith(".todo-orchestrator/") and path not in {"todos.md", "todo-status.md"} and not path.startswith("todos/"):
                paths.append(path)
                relevant_records.append(record)
        import hashlib

        fingerprint = hashlib.sha256("\x00".join(relevant_records).encode()).hexdigest()
        return GitIdentity(commit, bool(relevant_records), fingerprint, tuple(sorted(set(paths))))

    def recent_commits(self, max_items: int = 20) -> list[dict[str, str]]:
        count = max(1, min(max_items, 100))
        raw = self._git("log", f"-{count}", "--format=%H%x09%cI%x09%s")
        commits = []
        for line in raw.splitlines():
            sha, timestamp, subject = (line.split("\t", 2) + ["", ""])[:3]
            commits.append({"commit": sha, "observed_at": timestamp, "subject": subject})
        return commits

    def commit_at_or_before(self, timestamp: str) -> str | None:
        if not timestamp or timestamp.startswith("-") or len(timestamp) > 64:
            raise ValueError("invalid Git time anchor")
        value = self._git("log", "-1", f"--before={timestamp}", "--format=%H").strip()
        return value or None

    def diff_names(self, older: str, newer: str = "HEAD", max_items: int = 200) -> list[dict[str, str]]:
        raw = self._git("diff", "--name-status", older, newer)
        changes = []
        for line in raw.splitlines()[: max(1, min(max_items, 500))]:
            parts = line.split("\t")
            if len(parts) >= 2:
                changes.append({"status": parts[0], "path": parts[-1]})
        return changes

    def tracked_files(self, prefixes: Iterable[str] = ()) -> list[str]:
        arguments = ["-z", "--", *prefixes] if prefixes else ["-z"]
        return sorted(item for item in self._git("ls-files", *arguments).split("\x00") if item)

    def show_text(self, revision: str, relative_path: str, max_bytes: int = 2 * 1024 * 1024) -> str:
        if revision.startswith("-") or relative_path.startswith("-") or ".." in Path(relative_path).parts:
            raise ValueError("invalid Git object request")
        value = self._git("show", f"{revision}:{relative_path}")
        if len(value.encode("utf-8")) > max_bytes:
            raise ValueError("Git object exceeds text limit")
        return value

    def grep(self, pattern: str, *, max_items: int = 50, deny_patterns: Iterable[str] = ()) -> list[dict[str, object]]:
        """Return bounded canonical source matches; Git's no-match exit is normal."""
        if not pattern or pattern.startswith("-") or len(pattern) > 512:
            raise ValueError("invalid Git grep pattern")
        limit = max(1, min(max_items, 200))
        pathspecs = [
            "*.c", "*.cc", "*.cpp", "*.cxx", "*.cu", "*.cuh", "*.h", "*.hh", "*.hpp", "*.hxx", "*.py",
            ":(exclude).git/**", ":(exclude).todo-orchestrator/runtime/**", ":(exclude).ctxpp/**",
            ":(exclude)node_modules/**", ":(exclude)__pycache__/**",
        ]
        pathspecs.extend(f":(exclude,glob){item}" for item in deny_patterns if item and not item.startswith("-"))
        result = self.runner.run(
            ["git", "grep", "-n", "-I", "-F", "--", pattern, *pathspecs],
            cwd=self.root,
            timeout=8.0,
            check=False,
        )
        if result.returncode == 1:
            return []
        if result.returncode != 0:
            raise CommandError("Git source search unavailable")
        matches: list[dict[str, object]] = []
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            matches.append({"path": parts[0], "line": int(parts[1]), "excerpt": parts[2][:500]})
            if len(matches) >= limit:
                break
        return matches
