from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .config import DEFAULT_DENY_PATTERNS


MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
SOURCE_TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".rst", ".toml", ".yaml", ".yml", ".json", ".cmake",
    ".sh", ".bash", ".zsh", ".py", ".c", ".cc", ".cpp", ".cxx", ".cu", ".cuh",
    ".h", ".hh", ".hpp", ".hxx", ".txt",
})
SOURCE_TEXT_NAMES = frozenset({"CMakeLists.txt", "Makefile", "Dockerfile", "AGENTS.md"})
PRIVATE_OUTPUT_KEYS = frozenset({
    "database_path", "state_root", "worktree_path", "model_path", "service_endpoint",
    "raw_log", "transcript", "stdout", "stderr", "environment", "command_line",
})
SENSITIVE_KEY = re.compile(
    r"(^|[_-])(token|secret|credential|password|api[_-]?key|private[_-]?key)($|[_-])",
    re.IGNORECASE,
)
SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/-]+=*|(?<![A-Za-z0-9_])(?:sk|tok|toc|tos|tol)_[A-Za-z0-9_-]{12,})"
)


class SecurityError(ValueError):
    pass


def stable_public_id(kind: str, *private_values: object) -> str:
    """Return a stable non-secret identifier without disclosing private path material."""
    if not kind or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", kind):
        raise ValueError("invalid public ID kind")
    material = "\0".join(str(value) for value in private_values).encode()
    return f"{kind}-" + hashlib.sha256(material).hexdigest()[:16]


def is_allowlisted_text_path(relative_path: Path) -> bool:
    return relative_path.name in SOURCE_TEXT_NAMES or relative_path.suffix.casefold() in SOURCE_TEXT_SUFFIXES


def _matches(path: str, pattern: str) -> bool:
    pure = PurePosixPath(path)
    return pure.match(pattern) or fnmatch.fnmatchcase(path, pattern)


def is_denied(relative_path: Path, extra_patterns: list[str] | None = None) -> bool:
    normalized = relative_path.as_posix()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    patterns = (*DEFAULT_DENY_PATTERNS, *(extra_patterns or ()))
    return any(_matches(normalized, pattern) for pattern in patterns)


def resolve_registered_path(
    root: Path,
    relative: str,
    *,
    deny_patterns: list[str] | None = None,
    require_file: bool = True,
) -> Path:
    requested = Path(relative)
    if requested.is_absolute() or ".." in requested.parts:
        raise SecurityError("path must be relative and contained in the registered repository")
    if not requested.parts:
        raise SecurityError("path is required")
    if is_denied(requested, deny_patterns):
        raise SecurityError("path is denied")
    resolved_root = root.resolve(strict=True)
    try:
        resolved = (resolved_root / requested).resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise SecurityError("path is unavailable or escapes the registered repository") from exc
    if require_file and not resolved.is_file():
        raise SecurityError("path is not a regular file")
    return resolved


def read_bounded_text(
    root: Path,
    relative: str,
    *,
    deny_patterns: list[str] | None = None,
    max_bytes: int = MAX_TEXT_FILE_BYTES,
) -> str:
    target = resolve_registered_path(root, relative, deny_patterns=deny_patterns)
    size = target.stat().st_size
    if size > max_bytes:
        raise SecurityError("text file exceeds the read limit")
    payload = target.read_bytes()
    if b"\x00" in payload:
        raise SecurityError("binary file rejected")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecurityError("non-UTF-8 file rejected") from exc


def redact_text(value: str) -> str:
    return SENSITIVE_VALUE.sub("[REDACTED]", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_output(value: Any) -> Any:
    """Apply secret redaction plus MCP-only private provenance suppression."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key) in PRIVATE_OUTPUT_KEYS or SENSITIVE_KEY.search(str(key)) else redact_output(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_output(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_output(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value
