"""Lazy CUDA operation adapter; construction starts no GPU work."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from ._process import FixedArgvAdapter, Runner
from ...models import TodoError


class CudaAdapter(FixedArgvAdapter):
    def __init__(self, executable: Path, *, runner: Runner = subprocess.run):
        super().__init__(executable, runner=runner)

    def execute(self, *, repo: Path, operation: str, request_ref: str) -> dict[str, Any]:
        if operation not in {"status", "run", "benchmark", "profile"}:
            raise TodoError("invalid_cuda_operation", "CUDA adapter operation is not supported")
        return self.invoke(
            [operation, "--repo-root", str(repo), "--request-ref", request_ref, "--json"],
            cwd=repo,
            timeout=30,
            output_limit=8 * 1024,
        )
