"""Nonblocking bounded local-worker adapter."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from ._process import FixedArgvAdapter, Runner


class LocalWorkerAdapter(FixedArgvAdapter):
    def __init__(self, executable: Path, *, runner: Runner = subprocess.run):
        super().__init__(executable, runner=runner)

    def delegate(
        self, *, repo: Path, parent_claim_ref: str, objective_ref: str, packet_ref: str, mode: str
    ) -> dict[str, Any]:
        # References are resolved behind the adapter boundary; raw claim tokens
        # and packet bodies are never placed in the model-facing response.
        return self.invoke(
            [
                "delegate", "--repo", str(repo), "--parent-claim-ref", parent_claim_ref,
                "--objective-ref", objective_ref, "--packet-ref", packet_ref, "--mode", mode, "--json",
            ],
            cwd=repo,
            timeout=20,
            output_limit=4 * 1024,
        )

    def collect(self, *, repo: Path, execution_id: str) -> dict[str, Any]:
        return self.invoke(
            ["collect", "--repo", str(repo), "--execution-id", execution_id, "--nonblocking", "--json"],
            cwd=repo,
            timeout=10,
            output_limit=4 * 1024,
        )
