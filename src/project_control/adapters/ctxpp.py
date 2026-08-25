from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .git import GitReadAdapter


class CtxppReadAdapter:
    """Reads an existing ctxpp artifact only; it never invokes ctxpp or scans."""

    def __init__(self, root: Path, git: GitReadAdapter | None = None):
        self.root = root.resolve(strict=True)
        self.git = git or GitReadAdapter(self.root)

    def inspect(self, target: str, *, max_items: int = 30) -> dict[str, Any]:
        target = target.strip()
        if not target or len(target) > 256:
            raise ValueError("bounded ctxpp target is required")
        index = self.root / ".ctxpp" / "index.jsonl"
        if not index.is_file() or index.stat().st_size > 64 * 1024 * 1024:
            return self._git_fallback(target, "semantic_context_unavailable")
        source_mtime = max(
            (path.stat().st_mtime for path in self.root.rglob("*")
             if path.is_file() and ".ctxpp" not in path.parts and path.suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".cu", ".cuh"}),
            default=0.0,
        )
        if source_mtime > index.stat().st_mtime:
            return self._git_fallback(target, "semantic_context_stale")
        matches: list[dict[str, Any]] = []
        with index.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if target.casefold() not in line.casefold():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    matches.append(self._compact(item))
                if len(matches) >= max(1, min(max_items, 100)):
                    break
        return {
            "status": "ok" if matches else "partial",
            "source": "existing_ctxpp_index",
            "freshness": "mtime_current",
            "matches": matches,
            "warnings": [] if matches else ["semantic_target_not_found"],
        }

    @staticmethod
    def _compact(item: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "name", "qualified_name", "kind", "path", "line", "start_line", "end_line", "signature")
        return {key: item[key] for key in keys if key in item and not str(item[key]).startswith("/")}

    def _git_fallback(self, target: str, warning: str) -> dict[str, Any]:
        result = self.git._git(
            "grep", "-n", "-I", "-F", "--", target,
            "*.c", "*.cc", "*.cpp", "*.cxx", "*.h", "*.hh", "*.hpp", "*.cu", "*.cuh",
        )
        matches = []
        for line in result.splitlines()[:30]:
            path, separator, rest = line.partition(":")
            if separator:
                line_number, _, excerpt = rest.partition(":")
                matches.append({"path": path, "line": line_number, "excerpt": excerpt[:240]})
        return {"status": "partial", "source": "bounded_git_grep", "matches": matches, "warnings": [warning]}
