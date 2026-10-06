"""Fresh-process regressions for the assistance package's public imports."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
EXPORTS = """
from project_control.assistance import (
    EvaluationResult,
    ExperimentLedger,
    ScriptedStep,
    classify_failure,
    run_source_evaluation,
    source_observation,
)
assert all((EvaluationResult, ExperimentLedger, ScriptedStep,
            classify_failure, run_source_evaluation, source_observation))
"""


def _fresh_import(source: str) -> None:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class PackageImportTests(unittest.TestCase):
    def test_job_service_import_first_then_package_exports_in_fresh_process(self):
        _fresh_import(
            "from project_control.as1_jobs import JobService\n"
            "assert JobService.__module__ == 'project_control.as1_jobs'\n"
            + EXPORTS
        )

    def test_package_exports_first_then_job_service_import_in_fresh_process(self):
        _fresh_import(
            EXPORTS
            + "from project_control.as1_jobs import JobService\n"
            "assert JobService.__module__ == 'project_control.as1_jobs'\n"
        )
