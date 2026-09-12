"""Small, process-isolated differential runner used by WF2 acceptance tests.

The runner deliberately knows nothing about a Todo database.  Callers supply
the old and candidate commands and the registered authority roots that fixture
state must never touch.  This keeps the parity harness usable while the
runtime is relocated, rather than baking in either runtime's import path.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Iterable, Mapping, Sequence


class DifferentialError(RuntimeError):
    """Raised when a runtime result is malformed or semantically different."""


@dataclass(frozen=True)
class RuntimeCase:
    command: tuple[str, ...]
    fixture_root: Path
    label: str


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


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def require_disposable_fixture(fixture_root: Path, registered_authority_roots: Iterable[Path]) -> Path:
    """Reject a fixture that is or sits under an authority registered for live use."""
    fixture = _resolved(fixture_root)
    for authority in registered_authority_roots:
        live = _resolved(authority)
        if fixture == live or fixture.is_relative_to(live):
            raise DifferentialError(f"fixture root {fixture} overlaps registered authority {live}")
    return fixture


def _run(case: RuntimeCase, registered_authority_roots: Iterable[Path]) -> Mapping[str, object]:
    fixture = require_disposable_fixture(case.fixture_root, registered_authority_roots)
    fixture.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        case.command,
        cwd=fixture,
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": __import__("os").environ.get("PATH", ""), "WF2_FIXTURE_ROOT": str(fixture)},
    )
    if completed.returncode:
        raise DifferentialError(f"{case.label} exited {completed.returncode}: {completed.stderr.strip()}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DifferentialError(f"{case.label} did not emit JSON") from exc
    if not isinstance(result, dict):
        raise DifferentialError(f"{case.label} emitted a non-object result")
    return result


def compare_case(
    old: RuntimeCase,
    candidate: RuntimeCase,
    *,
    registered_authority_roots: Iterable[Path],
    nondeterministic_fields: Iterable[str] = (),
) -> ScenarioResult:
    """Run both runtimes in fresh subprocesses and reject any unallowed delta."""
    forbidden = set(nondeterministic_fields)
    old_result = dict(_run(old, registered_authority_roots))
    candidate_result = dict(_run(candidate, registered_authority_roots))
    missing = set(old_result).symmetric_difference(candidate_result) - forbidden
    if missing:
        raise DifferentialError(f"missing result fields: {sorted(missing)}")
    for key in sorted(set(old_result) | set(candidate_result)):
        if key in forbidden:
            old_result.pop(key, None)
            candidate_result.pop(key, None)
    if old_result != candidate_result:
        raise DifferentialError(f"semantic result mismatch: old={old_result!r}, candidate={candidate_result!r}")
    return ScenarioResult(old=old_result, candidate=candidate_result)
