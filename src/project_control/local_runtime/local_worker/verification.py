from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable


class VerificationError(RuntimeError):
    pass


def _runtime_contracts():
    from .canonical_runtime import bind
    bind(Path.cwd())
    from todo_orchestrator.runtime import normalize_command_spec
    return normalize_command_spec


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def run_verification(root: str | Path, commands: Iterable[object], *, phase: str) -> dict[str, Any]:
    base = Path(root).resolve()
    normalize = _runtime_contracts()
    results = []
    for raw in commands:
        spec = normalize(raw)
        cwd = (base / str(spec["cwd"])).resolve()
        if not _inside(cwd, base) or not cwd.is_dir():
            raise VerificationError(f"{phase} command cwd escapes the workspace")
        environment = dict(os.environ)
        environment.update(spec["env"])
        started = time.perf_counter()
        try:
            process = subprocess.run(
                spec["argv"], cwd=cwd, env=environment, text=True, capture_output=True,
                timeout=float(spec["timeout_seconds"]), check=False,
            )
            returncode = process.returncode
            stdout, stderr = process.stdout, process.stderr
        except subprocess.TimeoutExpired as error:
            returncode = 124
            stdout = error.stdout or ""
            stderr = error.stderr or ""
        results.append({
            "schema_version": 1,
            "phase": phase,
            "argv": list(spec["argv"]),
            "cwd": str(spec["cwd"]),
            "returncode": returncode,
            "stdout": stdout[:8000],
            "stderr": stderr[:8000],
            "stdout_omitted_chars": max(len(stdout) - 8000, 0),
            "stderr_omitted_chars": max(len(stderr) - 8000, 0),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        })
    return {"format": "LOCAL-WORKER-VERIFICATION/1", "phase": phase,
            "ok": all(item["returncode"] == 0 for item in results), "results": results}


def require_verification(root: str | Path, commands: Iterable[object], *, phase: str) -> dict[str, Any]:
    report = run_verification(root, commands, phase=phase)
    if not report["results"]:
        raise VerificationError(f"{phase} requires at least one command")
    if not report["ok"]:
        raise VerificationError(f"{phase} failed")
    return report
