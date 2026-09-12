"""Small, process-isolated differential runner used by WF2 acceptance tests.

The runner deliberately knows nothing about a Todo database.  Callers supply
the old and candidate commands and the registered authority roots that fixture
state must never touch.  This keeps the parity harness usable while the
runtime is relocated, rather than baking in either runtime's import path.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping, Sequence


class DifferentialError(RuntimeError):
    """Raised when a runtime result is malformed or semantically different."""


@dataclass(frozen=True)
class RuntimeCase:
    command: tuple[str, ...]
    fixture_root: Path
    label: str
    pinned_identity: str
    environment: Mapping[str, str]
    expected_source_root: Path


@dataclass(frozen=True)
class ScenarioResult:
    old: Mapping[str, object]
    candidate: Mapping[str, object]


@dataclass(frozen=True)
class InventoryReport:
    original_test_count: int
    scenario_names: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "original_test_count": self.original_test_count,
            "scenario_count": len(self.scenario_names),
            "scenario_names": list(self.scenario_names),
        }


@dataclass(frozen=True)
class OriginalSuiteReport:
    command: tuple[str, ...]
    tests_run: int


_RUN_COUNT = re.compile(r"Ran (\d+) tests?")


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def require_disposable_fixture(fixture_root: Path, registered_authority_roots: Iterable[Path]) -> Path:
    """Reject a fixture that is or sits under an authority registered for live use."""
    fixture = _resolved(fixture_root)
    for authority in registered_authority_roots:
        live = _resolved(authority)
        if fixture == live or fixture.is_relative_to(live) or live.is_relative_to(fixture):
            raise DifferentialError(f"fixture root {fixture} overlaps registered authority {live}")
    return fixture


def _run(case: RuntimeCase, registered_authority_roots: Iterable[Path]) -> Mapping[str, object]:
    fixture = require_disposable_fixture(case.fixture_root, registered_authority_roots)
    if fixture.exists():
        raise DifferentialError(f"fixture root must be fresh: {fixture}")
    if not case.pinned_identity or not Path(case.command[0]).is_absolute():
        raise DifferentialError(f"{case.label} must name an absolute command and pinned identity")
    fixture.mkdir(parents=True)
    completed = subprocess.run(
        case.command,
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
        env={**case.environment, "WF2_FIXTURE_ROOT": str(fixture)},
    )
    if completed.returncode:
        raise DifferentialError(f"{case.label} exited {completed.returncode}: {completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DifferentialError(f"{case.label} did not emit JSON") from exc
    if not isinstance(result, dict):
        raise DifferentialError(f"{case.label} emitted a non-object result")
    source = Path(str(result.get("runtime_source", ""))).resolve(strict=False)
    if not source.is_relative_to(case.expected_source_root):
        raise DifferentialError(f"{case.label} imported outside its pinned source root: {source}")
    if not isinstance(result.get("runtime_digest"), str) or not result["runtime_digest"]:
        raise DifferentialError(f"{case.label} omitted its runtime digest")
    return result


def compare_case(
    old: RuntimeCase,
    candidate: RuntimeCase,
    *,
    registered_authority_roots: Iterable[Path],
    nondeterministic_fields: Iterable[str] = (),
) -> ScenarioResult:
    """Run both runtimes in fresh subprocesses and reject any unallowed delta."""
    if old.fixture_root == candidate.fixture_root:
        raise DifferentialError("old and candidate require distinct fixture roots")
    if old.pinned_identity == candidate.pinned_identity:
        raise DifferentialError("old and candidate require distinct pinned identities")
    old_result = dict(_run(old, registered_authority_roots))
    candidate_result = dict(_run(candidate, registered_authority_roots))
    if (old_result["runtime_source"], old_result["runtime_digest"]) == (candidate_result["runtime_source"], candidate_result["runtime_digest"]):
        raise DifferentialError("old and candidate resolved to the same runtime root/digest identity")
    # Source location/digest establish that this is a real two-runtime pair;
    # they are relocation identity, not workflow semantics to compare.
    compare_results(old_result, candidate_result,
                    nondeterministic_fields=set(nondeterministic_fields) | {"runtime_source", "runtime_digest"})
    return ScenarioResult(old=old_result, candidate=candidate_result)


def compare_results(
    old_result: Mapping[str, object], candidate_result: Mapping[str, object], *, nondeterministic_fields: Iterable[str] = ()
) -> None:
    """Compare already captured runtime payloads; used for explicit negative controls."""
    forbidden = set(nondeterministic_fields)
    old_result = dict(old_result)
    candidate_result = dict(candidate_result)
    missing = set(old_result).symmetric_difference(candidate_result) - forbidden
    if missing:
        raise DifferentialError(f"missing result fields: {sorted(missing)}")
    for key in sorted(set(old_result) | set(candidate_result)):
        if key in forbidden:
            old_result.pop(key, None)
            candidate_result.pop(key, None)
    if old_result != candidate_result:
        raise DifferentialError(f"semantic result mismatch: old={old_result!r}, candidate={candidate_result!r}")


def run_original_suite(command: Sequence[str], *, cwd: Path, environment: Mapping[str, str]) -> OriginalSuiteReport:
    """Run an explicitly selected legacy suite and record its real test count."""
    if not command or not Path(command[0]).is_absolute():
        raise DifferentialError("original suite command must be absolute and explicit")
    completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, env=environment)
    transcript = completed.stdout + completed.stderr
    match = _RUN_COUNT.search(transcript)
    if completed.returncode or not match or int(match.group(1)) == 0:
        raise DifferentialError("original suite did not complete with a nonzero test count: " + transcript[-1000:])
    return OriginalSuiteReport(command=tuple(command), tests_run=int(match.group(1)))


def runtime_cases_from_bindings(bindings_path: Path, fixture_parent: Path) -> tuple[RuntimeCase, RuntimeCase]:
    """Derive the frozen installed and source candidates from explicit WF2 bindings."""
    try:
        bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
        # Preserve the bound venv launcher path: resolving its Python symlink
        # would silently select the base interpreter instead of site-packages.
        runtime_python = Path(bindings["runtime_python"]).absolute()
        if not runtime_python.is_file():
            raise OSError("bound runtime_python does not exist")
        skills = Path(bindings["repositories"]["skills"]).resolve(strict=True)
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise DifferentialError("WF2_BINDINGS lacks a usable runtime_python/skills binding") from exc
    source_root = skills / "todo-orchestrator" / "todo_orchestrator"
    if not source_root.is_dir():
        raise DifferentialError("WF2 bindings do not identify the Todo source candidate")
    probe = (
        "import hashlib,json,pathlib,todo_orchestrator; "
        "from todo_orchestrator.models import ExecutionState; "
        "p=pathlib.Path(todo_orchestrator.__file__).resolve(); "
        "print(json.dumps({'runtime_source':str(p),'runtime_digest':hashlib.sha256(p.read_bytes()).hexdigest(),"
        "'status':ExecutionState.CLAIMED.value,'event':'claim'},sort_keys=True))"
    )
    # Retain interpreter activation variables, but control the import path
    # exactly: the installed probe has none and the candidate has only Skills.
    base_env = dict(os.environ)
    old = RuntimeCase((str(runtime_python), "-B", "-c", probe), fixture_parent / "old", "old-installed",
                      "installed-site-packages", {**base_env, "PYTHONPATH": ""},
                      runtime_python.parent.parent / "lib")
    candidate = RuntimeCase((str(runtime_python), "-B", "-c", probe), fixture_parent / "candidate", "candidate-source",
                            "skills-source", {**base_env, "PYTHONPATH": str(skills / "todo-orchestrator")}, source_root)
    return old, candidate
