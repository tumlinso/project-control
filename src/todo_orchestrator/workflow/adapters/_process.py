"""Bounded subprocess transport shared by specialized adapters."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..foundation import require_bounded_payload
from ...models import TodoError


Runner = Callable[..., subprocess.CompletedProcess[str]]


class FixedArgvAdapter:
    def __init__(self, executable: Path, *, runner: Runner = subprocess.run):
        self.executable = executable
        self.runner = runner

    def invoke(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        output_limit: int,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if isinstance(arguments, (str, bytes)):
            raise TodoError("unsafe_adapter_argv", "Adapter arguments must be a fixed sequence")
        argv = [str(self.executable), *[str(item) for item in arguments]]
        result = self.runner(
            argv,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            shell=False,
            env=dict(environment) if environment is not None else None,
        )
        if result.returncode:
            raise TodoError(
                "specialized_adapter_failed",
                "Specialized engine failed; inspect the bounded diagnostic ID",
                details={"returncode": result.returncode},
            )
        try:
            value = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise TodoError("invalid_specialized_response", "Specialized engine returned an invalid envelope") from None
        if not isinstance(value, dict):
            raise TodoError("invalid_specialized_response", "Specialized engine response must be an object")
        require_bounded_payload(value, limit=output_limit, code="specialized_response_too_large")
        return value
