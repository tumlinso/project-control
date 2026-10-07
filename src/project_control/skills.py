"""Configured advisory skills: bounded discovery and read-only resource access.

This provider never registers repositories or participates in workflow identity.
All filesystem opens are read-only and directory-relative with symlinks rejected.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import yaml

from .security import is_allowlisted_text_path, is_denied, redact_output, stable_public_id

LABELS = {"origin": "agent_skill", "authority": "advisory_instruction", "mutation_authority": False}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_HEADER_BYTES = 8192
MAX_RESOURCES = 4096
MAX_CORPUS_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 8
MAX_SKILLS = 128
MAX_ENTRIES = 8192
TEXT_SUFFIXES = {".jsonl", ".tsv", ".csv", ".sha256"}
# Operational outputs are outside the advisory resource corpus, even if text.
# Exclusion applies equally to inventory and explicit reads; files stay untouched.
OPERATIONAL_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", ".ctxpp", ".cache", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "__pycache__", "node_modules", "build", "dist", "target",
    "CMakeFiles", ".tox", ".nox",
})


class SkillError(ValueError):
    def __init__(self, code: str, message: str = "Skill resource is unavailable") -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    directory: str
    metadata_identity: str
    freshness: str


def _identity(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _freshness(info: os.stat_result) -> str:
    return stable_public_id("freshness", *_stamp(info))


def _relative(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise SkillError("invalid_resource", "Resource must be a relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or ":" in part or any(ord(c) < 32 for c in part) for part in parts):
        raise SkillError("invalid_resource", "Resource must be a relative path")
    if any(part in OPERATIONAL_DIRECTORIES for part in parts) or len(parts) > MAX_DEPTH + 1 or any(is_denied(Path(part)) for part in parts) or is_denied(Path(value)) or is_denied(Path("skill") / value):
        raise SkillError("denied_resource", "Resource is denied")
    return tuple(parts)


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        root = root.expanduser()
        if not root.is_absolute():
            raise SkillError("invalid_root", "Configured skill root must be absolute")
        self.root = Path(os.path.abspath(root))
        self._root_stamp: tuple[int, int] | None = None
        try:
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise SkillError("unavailable", "Configured skill root is unavailable")
            self._root_stamp = (info.st_dev, info.st_ino)
        except FileNotFoundError:
            pass

    @contextmanager
    def _open(self, parts: tuple[str, ...] = (), *, directory: bool = False) -> Iterator[int]:
        """Pin each directory and verify every name still references its descriptor."""
        handles: list[int] = []
        links: list[tuple[int, str, tuple[int, ...]]] = []
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
            handles.append(fd)
            root_info = os.fstat(fd)
            if self._root_stamp is None:
                self._root_stamp = (root_info.st_dev, root_info.st_ino)
            if (root_info.st_dev, root_info.st_ino) != self._root_stamp:
                raise SkillError("stale_resource", "Configured skill root changed")
            for index, part in enumerate(parts):
                final_file = index == len(parts) - 1 and not directory
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if not final_file:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
                handles.append(child)
                info = os.fstat(child)
                if final_file and not stat.S_ISREG(info.st_mode):
                    raise SkillError("invalid_resource", "Resource is not a regular file")
                links.append((fd, part, _stamp(info)))
                fd = child
            yield fd
            if _stamp(os.stat(self.root, follow_symlinks=False)) != _stamp(root_info):
                raise SkillError("stale_resource", "Skill root changed during read")
            for parent, name, before in links:
                if _stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before:
                    raise SkillError("stale_resource", "Skill resource changed during read")
        except OSError as exc:
            raise SkillError("unavailable", "Skill resource is unavailable or unsafe") from exc
        finally:
            for handle in reversed(handles):
                os.close(handle)

    def _header(self, directory: str) -> tuple[dict, str, str]:
        with self._open((directory, "SKILL.md")) as fd:
            info = os.fstat(fd)
            if info.st_size > MAX_FILE_BYTES:
                raise SkillError("oversized", "Skill file exceeds the read limit")
            header = bytearray()
            lines: list[bytes] = []
            line = bytearray()
            for _ in range(MAX_HEADER_BYTES):
                item = os.read(fd, 1)
                if not item:
                    break
                header.extend(item)
                line.extend(item)
                if item == b"\n":
                    lines.append(bytes(line).strip())
                    line.clear()
                    if len(lines) == 1 and lines[0] != b"---":
                        raise SkillError("malformed_skill", "Skill requires YAML frontmatter")
                    if len(lines) > 1 and lines[-1] == b"---":
                        break
            else:
                raise SkillError("malformed_skill", "Skill frontmatter exceeds the limit")
            if len(lines) < 2 or lines[-1] != b"---":
                raise SkillError("malformed_skill", "Skill frontmatter is incomplete")
            try:
                text = bytes(header).decode("utf-8")
                # Reject aliases to avoid recursive or expansion-heavy metadata.
                if any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for t in yaml.scan(text)):
                    raise SkillError("malformed_skill", "Skill metadata aliases are unsupported")
                metadata = yaml.safe_load("\n".join(text.splitlines()[1:-1]))
            except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
                raise SkillError("malformed_skill", "Skill metadata is malformed") from exc
            if not isinstance(metadata, dict):
                raise SkillError("malformed_skill", "Skill metadata must be a mapping")
            for key, cap in (("name", 256), ("description", 4096)):
                value = metadata.get(key)
                if not isinstance(value, str) or not value.strip() or len(value.encode()) > cap or "\x00" in value:
                    raise SkillError("malformed_skill", "Skill name and description are required")
            if _stamp(os.fstat(fd)) != _stamp(info):
                raise SkillError("stale_resource", "Skill metadata changed during read")
            return metadata, _identity(bytes(header)), _freshness(info)

    def _discover(self) -> tuple[list[Skill], bool, int]:
        result = []
        rejected = 0
        with self._open(directory=True) as fd:
            names = []
            entries_seen = 0
            with os.scandir(fd) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_ENTRIES:
                        raise SkillError("inventory_limit", "Skill root inventory exceeds the limit")
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
            incomplete = len(names) > MAX_SKILLS
            for name in sorted(names)[:MAX_SKILLS]:
                try:
                    _relative(name)
                    metadata, digest, freshness = self._header(name)
                except SkillError:
                    rejected += 1
                    continue
                result.append(Skill(stable_public_id("skill", self.root, name), metadata["name"],
                                    metadata["description"], name, digest, freshness))
        return result, incomplete, rejected

    def get(self, skill_id: str) -> Skill:
        if not isinstance(skill_id, str) or len(skill_id) > 64:
            raise SkillError("unknown_skill", "Unknown skill ID")
        for skill in self._discover()[0]:
            if skill.id == skill_id:
                return skill
        raise SkillError("unknown_skill", "Unknown skill ID")

    def resources(self, skill_id: str) -> list[dict]:
        skill = self.get(skill_id)
        rows: list[dict] = []
        total_bytes = 0
        entries_seen = 0

        def walk(parts: tuple[str, ...]) -> None:
            nonlocal total_bytes, entries_seen
            with self._open((skill.directory, *parts), directory=True) as fd:
                entries = []
                with os.scandir(fd) as stream:
                    for entry in stream:
                        entries_seen += 1
                        if entries_seen > MAX_ENTRIES:
                            raise SkillError("inventory_limit", "Skill inventory exceeds the limit")
                        entries.append(entry.name)
                for name in sorted(entries):
                    path = "/".join((*parts, name))
                    try:
                        _relative(path)
                    except SkillError:
                        continue
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if len(parts) >= MAX_DEPTH:
                            raise SkillError("inventory_limit", "Skill resource depth exceeds the limit")
                        walk((*parts, name))
                    elif stat.S_ISREG(info.st_mode):
                        total_bytes += info.st_size
                        if len(rows) >= MAX_RESOURCES or total_bytes > MAX_CORPUS_BYTES:
                            raise SkillError("inventory_limit", "Skill inventory exceeds the limit")
                        rows.append({"path": path, "bytes": info.st_size, "freshness": _freshness(info)})
        walk(())
        return rows

    def read_bytes(self, skill_id: str, resource: str, *, max_bytes: int = MAX_FILE_BYTES) -> tuple[bytes, str]:
        payload, digest, _ = self._read_payload(skill_id, resource, max_bytes=max_bytes)
        return payload, digest

    def _read_payload(self, skill_id: str, resource: str, *, max_bytes: int = MAX_FILE_BYTES) -> tuple[bytes, str, str]:
        parts = _relative(resource)
        if not isinstance(max_bytes, int) or max_bytes < 1 or max_bytes > 64 * 1024 * 1024:
            raise SkillError("invalid_limit", "Invalid internal read limit")
        skill = self.get(skill_id)
        with self._open((skill.directory, *parts)) as fd:
            before = os.fstat(fd)
            if before.st_size > max_bytes:
                raise SkillError("oversized", "Skill resource exceeds the read limit")
            chunks = []
            size = 0
            while True:
                chunk = os.read(fd, min(65536, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise SkillError("oversized", "Skill resource exceeds the read limit")
            payload = b"".join(chunks)
            if _stamp(before) != _stamp(os.fstat(fd)) or len(payload) != before.st_size:
                raise SkillError("stale_resource", "Skill resource changed during read")
        return payload, _identity(payload), _freshness(before)

    def read_text(self, skill_id: str, resource: str) -> dict:
        _relative(resource)
        path = Path(resource)
        if not is_allowlisted_text_path(path) and path.suffix.casefold() not in TEXT_SUFFIXES:
            raise SkillError("unsupported_resource", "Skill resource is not supported text")
        # Freshness comes from the exact pinned descriptor used for this read.
        # _read_payload validates fstat and every directory link before returning.
        payload, digest, freshness = self._read_payload(skill_id, resource)
        try:
            text = payload.decode("utf-8")
        except UnicodeError as exc:
            raise SkillError("invalid_text", "Skill resource is not UTF-8 text") from exc
        if "\x00" in text:
            raise SkillError("invalid_text", "Binary skill resource rejected")
        return {**LABELS, "content": redact_output(text), "identity": digest, "freshness": freshness}

    def list(self, query: str = "", max_items: int = 20, continuation_cursor: dict | None = None) -> dict:
        if not isinstance(query, str) or len(query.encode()) > 4096 or not isinstance(max_items, int) or not 1 <= max_items <= 128:
            raise SkillError("invalid_request", "Invalid skill discovery limits")
        try:
            skills, incomplete, rejected = self._discover()
        except SkillError as exc:
            if exc.code == "unavailable":
                return {**LABELS, "status": "unavailable", "skills": [], "continuation_cursor": None}
            raise
        terms = query.casefold().split()
        skills = [s for s in skills if all(t in (s.name + " " + s.description).casefold() for t in terms)]
        identity = _identity(json.dumps([(s.id, s.metadata_identity, s.freshness) for s in skills]).encode())
        offset = 0
        if continuation_cursor is not None:
            if not isinstance(continuation_cursor, dict) or continuation_cursor.get("identity") != identity or continuation_cursor.get("query_identity") != _identity(query.encode()):
                raise SkillError("stale_cursor", "Refresh skill discovery")
            offset = continuation_cursor.get("offset")
            if not isinstance(offset, int) or not 0 <= offset <= len(skills):
                raise SkillError("invalid_cursor", "Invalid discovery cursor")
        rows = []
        warnings = []
        consumed = 0
        for skill in skills[offset:offset + max_items]:
            try:
                resources = self.resources(skill.id)
            except SkillError as exc:
                warnings.append({"skill_id": skill.id, "code": exc.code})
                consumed += 1
                continue
            row = {"id": skill.id, "name": skill.name, "description": skill.description,
                   "metadata_identity": skill.metadata_identity, "identity_scope": "frontmatter", "freshness": skill.freshness,
                   "resource_names": [r["path"] for r in resources[:64]], "resource_count": len(resources),
                   "resources_truncated": len(resources) > 64, "instruction_uri": f"skill://local/{skill.id}/SKILL.md"}
            if _json_bytes(redact_output(rows + [row])) > 30000:
                break
            rows.append(row)
            consumed += 1
        next_offset = offset + consumed
        cursor = {"identity": identity, "offset": next_offset, "query_identity": _identity(query.encode())} if next_offset < len(skills) else None
        return redact_output({**LABELS, "status": "ok", "skills": rows, "identity": identity,
                              "discovery_incomplete": incomplete or bool(warnings), "rejected_skills": rejected, "warnings": warnings[:16], "warning_count": len(warnings), "continuation_cursor": cursor})

    def read(self, skill_id: str, resource: str = "SKILL.md", line_start: int = 1,
             line_end: int | None = None, budget_bytes: int = 32768, expected_identity: str | None = None) -> dict:
        if not isinstance(budget_bytes, int) or not 1024 <= budget_bytes <= 65536:
            raise SkillError("invalid_budget", "Read budget must be between 1024 and 65536 bytes")
        if not isinstance(line_start, int) or line_start < 1 or (line_end is not None and (not isinstance(line_end, int) or line_end < line_start)):
            raise SkillError("invalid_range", "Invalid line range")
        data = self.read_text(skill_id, resource)
        if expected_identity is not None and expected_identity != data["identity"]:
            raise SkillError("stale_resource", "Skill content identity changed")
        lines = data.pop("content").splitlines(keepends=True)
        stop = min(len(lines), line_end or len(lines))
        result = {**data, "status": "ok", "skill_id": skill_id, "resource": resource,
                  "uri": f"skill://local/{skill_id}/{resource}", "line_start": line_start,
                  "line_end": line_start - 1, "content": "", "continuation_line": None,
                  "total_lines": len(lines)}
        for index in range(line_start - 1, stop):
            candidate = {**result, "content": result["content"] + lines[index], "line_end": index + 1,
                         "continuation_line": index + 2 if index + 1 < stop else None}
            if _json_bytes(candidate) > budget_bytes:
                if not result["content"]:
                    raise SkillError("line_exceeds_budget", "Resource line exceeds the response budget")
                result["continuation_line"] = index + 1
                break
            result = candidate
        return result
