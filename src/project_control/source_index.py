from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .cache import ensure_private_cache_dir
from .security import is_allowlisted_text_path, is_denied, redact_text, resolve_registered_path


MAX_INDEX_FILE_BYTES = 2 * 1024 * 1024
MAX_INDEX_FILES = 20_000


@dataclass(frozen=True)
class LexicalMatch:
    path: str
    line: int
    excerpt: str
    score: float


class SourceLexicalIndex:
    """Disposable FTS5 index keyed by explicit source identity."""

    def __init__(self, repository_id: str, source_identity: str):
        key = hashlib.sha256(f"{repository_id}\0{source_identity}".encode()).hexdigest()
        self.directory = ensure_private_cache_dir("source-index", key[:24])
        self.database = self.directory / "index.sqlite3"

    def build(self, root: Path, tracked_paths: Iterable[str], deny_patterns: list[str]) -> dict[str, int]:
        root = root.resolve(strict=True)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(path UNINDEXED, line UNINDEXED, content)")
            connection.execute("DELETE FROM documents")
            indexed_files = indexed_lines = 0
            for relative in sorted(set(tracked_paths))[:MAX_INDEX_FILES]:
                rel = Path(relative)
                if is_denied(rel, deny_patterns) or not is_allowlisted_text_path(rel):
                    continue
                try:
                    path = resolve_registered_path(root, relative, deny_patterns=deny_patterns)
                    if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                        continue
                    payload = path.read_bytes()
                    if b"\0" in payload:
                        continue
                    text = payload.decode("utf-8")
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
                rows = [(relative, number, redact_text(line)[:2000]) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
                connection.executemany("INSERT INTO documents(path,line,content) VALUES(?,?,?)", rows)
                indexed_files += 1
                indexed_lines += len(rows)
            connection.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('complete','1')")
            connection.commit()
            return {"files": indexed_files, "lines": indexed_lines}
        finally:
            connection.close()

    def is_complete(self) -> bool:
        if not self.database.is_file():
            return False
        try:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()
                return bool(row and row[0] == "1")
            finally:
                connection.close()
        except sqlite3.Error:
            return False

    def search(self, query: str, *, offset: int = 0, limit: int = 50) -> list[LexicalMatch]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,128}", query)
        if not tokens or not self.database.is_file():
            return []
        expression = " OR ".join(f'"{token}"' for token in tokens[:16])
        connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT path,line,content,bm25(documents) FROM documents WHERE documents MATCH ? ORDER BY bm25(documents),path,line LIMIT ? OFFSET ?",
                (expression, max(1, min(limit, 200)), max(0, offset)),
            ).fetchall()
        finally:
            connection.close()
        return [LexicalMatch(str(path), int(line), str(content), float(score)) for path, line, content, score in rows]
