#!/usr/bin/env python3
"""Bridge a real A31 advisory into a detached, operator-run LAB experiment.

The default path only validates and prints a plan. ``--execute-lab`` is
required to create a disposable fixture copy and run its baseline and the
model-authored odd-tail test through Project Control's contained LabService.
No source is written to the checked-in fixture.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from typing import Any


REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "planning/project-assistance-v1/fixtures/repository"
sys.path.insert(0, str(REPO / "src"))

from project_control.assistance.lab import LabOperator, LabProject, LabSelection, LabService


class CounterexampleError(ValueError):
    """A live report or proposed test does not satisfy the bridge contract."""


_PYTHON_BLOCK = re.compile(r"```(?:python|py)[ \t]*\r?\n(?P<source>.*?)```", re.DOTALL)
_RESULT_STATUSES = {"completed", "partial"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_live_advisory(report_path: Path, artifact_root: Path) -> dict[str, Any]:
    """Load only a completed public A31 result rooted in its private artifact."""
    try:
        if artifact_root.is_symlink() or report_path.is_symlink():
            raise CounterexampleError("live report and artifact root must not be symlinks")
        root = artifact_root.resolve(strict=True)
        report_file = report_path.resolve(strict=True)
        report_file.relative_to(root)
        root_info = root.lstat()
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) & 0o077):
            raise CounterexampleError("live artifact root must be owned and private")
        info = report_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CounterexampleError("live report must be an owned regular file")
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except CounterexampleError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise CounterexampleError("live report or artifact root is unavailable") from exc
    if (not isinstance(report, dict)
            or report.get("format") != "pa1-live-source-qualification/1"
            or report.get("phase") != "extension"
            or report.get("execution_mode") != "live"
            or report.get("adapter_tier") != "public_as1_job_service"
            or report.get("inference_performed") is not True):
        raise CounterexampleError("report does not prove that live inference occurred")
    if report.get("status") not in {"completed_with_gaps", "partial"}:
        raise CounterexampleError("live report has no completed result status")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise CounterexampleError("live report case list is missing")
    matches = [case for case in cases if isinstance(case, dict) and case.get("case_id") == "A31"]
    if len(matches) != 1:
        raise CounterexampleError("live report must contain exactly one A31 result")
    case = matches[0]
    if (case.get("status") not in _RESULT_STATUSES
            or not isinstance(case.get("job_id"), str) or not case["job_id"].strip()
            or not isinstance(case.get("result_packet"), str) or not case["result_packet"].strip()
            or not isinstance(case.get("answer"), str) or not case["answer"].strip()):
        raise CounterexampleError("A31 live job, result reference, status or advisory is missing")
    return {"report": report, "case": case, "report_path": report_file,
            "report_sha256": sha256_bytes(report_file.read_bytes()), "artifact_root": root}


def extract_model_test(answer: str) -> tuple[str, dict[str, Any]]:
    """Extract and validate one narrowly scoped unittest from the advisory."""
    blocks = list(_PYTHON_BLOCK.finditer(answer))
    if len(blocks) != 1:
        raise CounterexampleError("A31 advisory must contain exactly one Python test code block")
    source = blocks[0].group("source")
    if not source.strip() or len(source.encode("utf-8")) > 2048:
        raise CounterexampleError("model-authored test is empty or exceeds the bounded source size")
    details = validate_unittest_source(source)
    return source, details


def validate_unittest_source(source: str) -> dict[str, Any]:
    """Permit only an import and one literal odd-tail unittest assertion."""
    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise CounterexampleError("model-authored test is not valid Python") from exc
    nodes = [node for node in module.body if not (isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))]
    imports: set[str] = set()
    classes: list[ast.ClassDef] = []
    for node in nodes:
        if isinstance(node, ast.Import) and len(node.names) == 1 and node.names[0].name == "unittest":
            imports.add("unittest")
        elif (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "demo.pairs"
              and len(node.names) == 1 and node.names[0].name == "pair_sum"
              and (node.names[0].asname in {None, "pair_sum"})):
            imports.add("pair_sum")
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
        else:
            raise CounterexampleError("test contains unsupported top-level Python")
    if imports != {"unittest", "pair_sum"} or len(classes) != 1:
        raise CounterexampleError("test must import unittest and demo.pairs.pair_sum and define one test class")
    test_class = classes[0]
    if (len(test_class.bases) != 1 or not isinstance(test_class.bases[0], ast.Attribute)
            or not isinstance(test_class.bases[0].value, ast.Name)
            or test_class.bases[0].value.id != "unittest"
            or test_class.bases[0].attr != "TestCase"
            or test_class.decorator_list or test_class.keywords
            or getattr(test_class, "type_params", [])):
        raise CounterexampleError("test class must directly inherit unittest.TestCase")
    methods = [node for node in test_class.body if isinstance(node, ast.FunctionDef)]
    extras = [node for node in test_class.body if not (isinstance(node, ast.FunctionDef)
              or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
              and isinstance(node.value.value, str))]
    if len(methods) != 1 or extras:
        raise CounterexampleError("test class must contain exactly one test method")
    method = methods[0]
    body = [node for node in method.body if not (isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))]
    if (not method.name.startswith("test") or method.decorator_list or len(method.args.args) != 1
            or method.args.args[0].arg != "self" or method.args.vararg or method.args.kwarg
            or method.args.kwonlyargs or method.args.defaults or len(body) != 1
            or not isinstance(body[0], ast.Expr) or not isinstance(body[0].value, ast.Call)):
        raise CounterexampleError("test method must contain one direct unittest assertion")
    assertion = body[0].value
    if (not isinstance(assertion.func, ast.Attribute) or assertion.func.attr != "assertEqual"
            or not isinstance(assertion.func.value, ast.Name) or assertion.func.value.id != "self"
            or len(assertion.args) != 2 or assertion.keywords):
        raise CounterexampleError("test method must call self.assertEqual once")
    actual, expected_node = assertion.args
    if (not isinstance(actual, ast.Call) or not isinstance(actual.func, ast.Name)
            or actual.func.id != "pair_sum" or len(actual.args) != 1 or actual.keywords
            or not isinstance(actual.args[0], ast.List)):
        raise CounterexampleError("assertion must call pair_sum on a literal input list")
    values = [_literal_number(node) for node in actual.args[0].elts]
    expected = _literal_number(expected_node)
    if len(values) < 3 or len(values) % 2 != 1 or values[-1] == 0:
        raise CounterexampleError("test input must have odd length and a nonzero unpaired tail")
    if sum(values) != expected:
        raise CounterexampleError("expected total must equal the sum of the literal test input")
    return {"class_name": test_class.name, "test_name": method.name,
            "input": values, "expected_total": expected}


def _literal_number(node: ast.AST) -> int | float:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number(node.operand)
        return -value
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool) and (not isinstance(node.value, float)
            or float("inf") > abs(node.value))):
        return node.value
    raise CounterexampleError("test values and expected total must be finite numeric literals")


def _run_git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=root, check=True, text=True,
                            capture_output=True, env=env)
    return result.stdout.strip()


def _fixture_digest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in ("demo", "tests"):
        directory = root / relative
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise CounterexampleError("fixture contains a symlink")
            if path.is_dir():
                result[path.relative_to(root).as_posix() + "/"] = (
                    f"directory:{stat.S_IMODE(path.stat().st_mode):04o}")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = (
                    f"file:{stat.S_IMODE(path.stat().st_mode):04o}:{sha256_bytes(path.read_bytes())}")
    return result


def _make_disposable_fixture(destination: Path) -> Path:
    source = destination / "source"
    source.mkdir(mode=0o700)
    for name in ("demo", "tests"):
        shutil.copytree(FIXTURE / name, source / name, symlinks=False)
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_AUTHOR_NAME": "LAB Counterexample", "GIT_AUTHOR_EMAIL": "lab@example.invalid",
                "GIT_COMMITTER_NAME": "LAB Counterexample", "GIT_COMMITTER_EMAIL": "lab@example.invalid"})
    _run_git(source, "init", "-q", env=env)
    _run_git(source, "add", "demo", "tests", env=env)
    _run_git(source, "commit", "-qm", "disposable fixture baseline", env=env)
    return source


def _selection(project: str, argv: tuple[str, ...], reference: str) -> LabSelection:
    return LabSelection(project=project, source_paths=("demo", "tests"),
                        hypothesis="The model-proposed odd-length unittest discriminates the final unpaired item.",
                        argv=argv, reference=reference,
                        expected_measurements="baseline passes; odd-tail proposed test fails with a nonzero exit",
                        stop_rule="one baseline and one contained counterexample invocation")


def execute_lab(live: dict[str, Any], test_source: str, test_details: dict[str, Any]) -> dict[str, Any]:
    """Run baseline and the exact live test in a fresh disposable Git copy."""
    artifact_root = live["artifact_root"]
    job_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", live["case"]["job_id"])[:80]
    run_root = artifact_root / f"model-counterexample-{job_id}-{uuid.uuid4().hex[:8]}"
    run_root.mkdir(mode=0o700, exist_ok=False)
    os.chmod(run_root, 0o700)
    source = _make_disposable_fixture(run_root)
    canonical_before = _fixture_digest(FIXTURE)
    source_initial = _fixture_digest(source)
    base_head = _run_git(source, "rev-parse", "HEAD")
    service = LabService(
        projects={"pa1-scratch": LabProject("pa1-scratch", source, "pa1-odd-tail-fixture")},
        state_root=run_root / "private-state")
    operator: LabOperator = service.operator()
    baseline_grant = service.select(operator, _selection(
        "pa1-scratch", ("python3", "-m", "unittest", "discover", "-s", "tests"),
        "existing fixture baseline tests"))
    baseline = service.run(operator, baseline_grant)
    baseline_journal = service.status(operator, baseline_grant.experiment_id)

    test_path = source / "tests" / "test_model_counterexample.py"
    if test_path.exists():
        raise CounterexampleError("disposable counterexample path already exists")
    source_bytes = test_source.encode("utf-8")
    test_path.write_bytes(source_bytes)
    os.chmod(test_path, 0o600)
    test_sha = sha256_bytes(source_bytes)
    source_before_lab = _fixture_digest(source)
    if source_before_lab.get("tests/test_model_counterexample.py") != f"file:0600:{test_sha}":
        raise CounterexampleError("exact model-authored test was not preserved in the scratch fixture")
    counterexample_grant = service.select(operator, _selection(
        "pa1-scratch", ("python3", "-m", "unittest", "discover", "-s", "tests",
                        "-p", "test_model_counterexample.py"),
        "model-authored A31 odd-tail unittest"))
    counterexample = service.run(operator, counterexample_grant)
    counterexample_journal = service.status(operator, counterexample_grant.experiment_id)
    failure_output = str(counterexample.get("stdout", "")) + "\n" + str(counterexample.get("stderr", ""))
    expected = test_details["expected_total"]
    observed_without_tail = sum(test_details["input"][:-1])
    assertion_failure = ("AssertionError" in failure_output and "FAIL:" in failure_output
                         and str(expected) in failure_output and str(observed_without_tail) in failure_output)
    source_after_lab = _fixture_digest(source)
    canonical_after = _fixture_digest(FIXTURE)
    evidence = {
        "format": "pa1-model-counterexample-lab-evidence/1",
        "acceptance_claim": "contained execution of the exact test extracted from a live A31 advisory",
        "created_unix": time.time(),
        "live_result": {
            "report_path": str(live["report_path"]), "report_sha256": live["report_sha256"],
            "case_id": "A31", "status": live["case"]["status"],
            "job_id": live["case"]["job_id"], "result_packet": live["case"]["result_packet"],
            "inference_performed": live["report"]["inference_performed"],
        },
        "model_test": {"path": "source/tests/test_model_counterexample.py",
                       "sha256": test_sha, "bytes": len(source_bytes),
                       "class_name": test_details["class_name"],
                       "test_name": test_details["test_name"],
                       "input": test_details["input"],
                       "expected_total": test_details["expected_total"]},
        "source": {"root": str(source), "base_head": base_head,
                   "baseline_source_inventory": source_initial,
                   "source_inventory_before_lab": source_before_lab,
                   "source_inventory_after_lab": source_after_lab,
                   "source_unchanged_during_lab": source_before_lab == source_after_lab,
                   "canonical_fixture_inventory_before": canonical_before,
                   "canonical_fixture_inventory_after": canonical_after,
                   "canonical_fixture_unchanged": canonical_before == canonical_after},
        "baseline": baseline,
        "counterexample": counterexample,
        "journal": {"baseline": baseline_journal, "counterexample": counterexample_journal},
        "checks": {
            "baseline_passed": baseline.get("outcome") == "positive" and baseline.get("returncode") == 0,
            "counterexample_is_negative": counterexample.get("outcome") == "negative"
                and isinstance(counterexample.get("returncode"), int)
                and counterexample["returncode"] != 0,
            "counterexample_failure_is_assertion_mismatch": assertion_failure,
            "baseline_cleanup_verified": baseline.get("cleanup_verified") is True,
            "counterexample_cleanup_verified": counterexample.get("cleanup_verified") is True,
            "both_contained": all("bubblewrap" in str(row.get("containment_backend", ""))
                                   and "cgroup-v2" in str(row.get("containment_backend", ""))
                                   for row in (baseline, counterexample)),
            "scratch_source_unchanged_during_lab": source_before_lab == source_after_lab,
            "canonical_source_unchanged": canonical_before == canonical_after,
        },
        "limits": ["One fixture input and one model-authored test were exercised.",
                   "No canonical source, patch application, Todo state, or production code was changed."],
    }
    evidence["status"] = "passed" if all(evidence["checks"].values()) else "failed"
    evidence_path = run_root / "evidence.json"
    _write_json_private(evidence_path, evidence)
    evidence["evidence_path"] = str(evidence_path)
    return evidence


def _write_json_private(path: Path, value: dict[str, Any]) -> None:
    data = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-report", type=Path, required=True)
    parser.add_argument("--live-artifact-root", type=Path, required=True)
    parser.add_argument("--execute-lab", action="store_true",
                        help="create a disposable fixture and perform the contained CPU runs")
    args = parser.parse_args(argv)
    live = load_live_advisory(args.live_report, args.live_artifact_root)
    test_source, details = extract_model_test(live["case"]["answer"])
    plan = {"status": "ready_to_execute" if not args.execute_lab else "running",
            "mode": "execute_lab" if args.execute_lab else "inert_plan",
            "live_job_id": live["case"]["job_id"],
            "live_result_packet": live["case"]["result_packet"],
            "proposed_test_sha256": sha256_bytes(test_source.encode("utf-8")),
            "test": details}
    if args.execute_lab:
        result = execute_lab(live, test_source, details)
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
        return 0 if result["status"] == "passed" else 2
    sys.stdout.write(json.dumps(plan, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
