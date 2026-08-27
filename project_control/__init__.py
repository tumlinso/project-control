"""Validation-worktree shim for the repository's src layout."""

from pathlib import Path


_PACKAGE = Path(__file__).resolve().parent.parent / "src" / "project_control"
__path__ = [str(_PACKAGE)]
exec(compile((_PACKAGE / "__init__.py").read_bytes(), str(_PACKAGE / "__init__.py"), "exec"))
