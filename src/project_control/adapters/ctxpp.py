from __future__ import annotations

import json
import fnmatch
from pathlib import Path
from typing import Any, Iterable

from .git import GitReadAdapter


class CtxppReadAdapter:
    """Reads an existing ctxpp artifact only; it never invokes ctxpp or scans."""

    def __init__(self, root: Path, git: GitReadAdapter | None = None, deny_patterns: Iterable[str] = ()):
        self.root = root.resolve(strict=True)
        self.git = git or GitReadAdapter(self.root)
        self.deny_patterns = tuple(deny_patterns)

    def inspect(self, target: str, *, max_items: int = 30) -> dict[str, Any]:
        target = target.strip()
        if not target or len(target) > 256:
            raise ValueError("bounded ctxpp target is required")
        index = self.root / ".ctxpp" / "index.jsonl"
        if not index.is_file():
            return self._git_fallback(target, "semantic_context_unavailable")
        matches: list[dict[str, Any]] = []
        scanned_bytes = 0
        with index.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                scanned_bytes += len(line.encode("utf-8"))
                if scanned_bytes > 16 * 1024 * 1024:
                    break
                if target.casefold() not in line.casefold():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    compact = self._compact(item)
                    path = str(compact.get("path") or compact.get("file") or "")
                    if not any(fnmatch.fnmatch(path, denied) for denied in self.deny_patterns):
                        matches.append(compact)
                if len(matches) >= max(1, min(max_items, 100)):
                    break
        if not matches:
            return self._git_fallback(target, "semantic_target_not_found")
        freshness = self._freshness_for(matches)
        if freshness == "stale":
            return self._git_fallback(target, "semantic_context_stale")
        warnings = [] if freshness == "metadata_current" else ["semantic_freshness_unknown"]
        return {"status": "ok", "source": "existing_ctxpp_index", "freshness": freshness, "matches": matches, "warnings": warnings}

    @staticmethod
    def _compact(item: dict[str, Any]) -> dict[str, Any]:
        keys = ("id", "name", "qualified_name", "kind", "path", "file", "line", "start_line", "end_line", "signature", "record", "parse_status")
        return {key: item[key] for key in keys if key in item and not str(item[key]).startswith("/")}

    def _freshness_for(self, matches: list[dict[str, Any]]) -> str:
        metadata = self.root / ".ctxpp" / "cache" / "freshness.json"
        if not metadata.is_file() or metadata.stat().st_size > 32 * 1024 * 1024:
            return "unknown"
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "unknown"
        files = value.get("files", {}) if isinstance(value, dict) else {}
        checked = False
        for match in matches:
            relative = match.get("path") or match.get("file")
            record = files.get(relative) if isinstance(relative, str) and isinstance(files, dict) else None
            if not isinstance(record, dict):
                continue
            checked = True
            source = self.root / relative
            try:
                stat = source.stat()
            except OSError:
                return "stale"
            if stat.st_size != record.get("size") or stat.st_mtime_ns != record.get("mtime_ns"):
                return "stale"
        return "metadata_current" if checked else "unknown"

    def _git_fallback(self, target: str, warning: str) -> dict[str, Any]:
        matches = self.git.grep(target, max_items=30, deny_patterns=self.deny_patterns)
        warnings = [warning]
        if not matches and warning != "semantic_target_not_found":
            warnings.append("semantic_target_not_found")
        return {"status": "partial" if matches else "ok", "source": "bounded_git_grep", "freshness": "working_tree", "matches": matches, "warnings": warnings}
