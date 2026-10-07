#!/usr/bin/env python3
"""Unified todo-orchestrator v2 CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from todo_orchestrator.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
