"""Lazy cpp-context-compiler source inspection adapter."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from ._process import FixedArgvAdapter, Runner


class CtxppAdapter(FixedArgvAdapter):
    def __init__(self, executable: Path, *, runner: Runner = subprocess.run):
        super().__init__(executable, runner=runner)

    def inspect(self, *, repo: Path, target: str, intent: str, budget_tokens: int) -> dict[str, Any]:
        return self.invoke(
            ["--root", str(repo), "--json", "slice", target, "--intent", intent, "--budget-tokens", str(budget_tokens)],
            cwd=repo,
            timeout=30,
            output_limit=64 * 1024,
        )
