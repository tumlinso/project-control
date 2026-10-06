from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

try:
    import pytest
except ModuleNotFoundError:  # unittest bridge delegates to the project dev venv
    pytest = None

from project_control.assistance.quality import (
    ExperimentLedger,
    QualificationError,
    create_held_out_fixture,
    evaluate_cases,
    load_eval_contract,
    qualify_source,
    score_case,
)


REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "planning/project-assistance-v1"
FIXTURE = PACKAGE / "fixtures/repository"


class PytestSuiteBridge(unittest.TestCase):
    __test__ = False  # prevent the delegated pytest run from recursing into this bridge

    def test_same_source_suite_runs_with_nonzero_count(self):
        python = REPO / ".venv/bin/python"
        self.assertTrue(python.is_file(), "project development interpreter is unavailable")
        result = subprocess.run([str(python), "-m", "pytest", "-q", str(Path(__file__))],
                                cwd=REPO, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        match = re.search(r"(?m)^(\d+) passed(?:,|\s|$)", output)
        self.assertIsNotNone(match, output)
        self.assertGreater(int(match.group(1)), 0, output)


def load_tests(loader, standard_tests, pattern):
    """Run this same pytest source through the configured project interpreter."""
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(PytestSuiteBridge))
    return suite


def _citation(path: str, excerpt: str) -> dict:
    source = FIXTURE / path
    return {"path": path, "excerpt": excerpt, "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest()}


def test_actual_source_hash_is_bound_and_stale_hash_fails_closed(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "module.py").write_text("value = 1\n", encoding="utf-8")
    identity = qualify_source(source)
    assert len(identity.source_sha256) == 64
    (source / "module.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(QualificationError, match="stale source identity"):
        qualify_source(source, expected_source_sha256=identity.source_sha256)


def test_citation_requires_real_excerpt_current_hash_and_in_root_source():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E01")
    valid = score_case(case, {
        "answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
        "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                      _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")],
    }, FIXTURE)
    assert valid["status"] == "complete"
    assert valid["valid_source_evidence"] == ["demo/budgets.py"]

    forged = score_case(case, {
        "answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
        "citations": [
            {"path": "demo/budgets.py", "excerpt": "MAX_STEPS = 999"},
            {"path": "../outside.txt", "excerpt": "INQUIRY_SECONDS = 300"},
        ],
    }, FIXTURE)
    assert forged["status"] == "failed"
    assert forged["failure_layer"] == "validation"
    assert len(forged["citation_errors"]) >= 2


def test_model_self_report_cannot_claim_reasoning_or_hidden_trace_evidence():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E01")
    result = score_case(case, {
        "answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
        "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                      _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")],
        "reasoning_tokens": 8192,
        "trace": {"events": ["invented hidden chain of thought"]},
        "visible_trace": {"format": "pa1-visible-trace/1", "events": ["invented call"]},
    }, FIXTURE)
    assert result["untrusted_self_report_fields"] == ["reasoning_tokens", "trace", "visible_trace"]
    assert result["trace_valid"] is False
    assert result["visible_trace_event_count"] == 0


def test_numeric_answer_checks_do_not_accept_prefix_matches():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E01")
    result = score_case(case, {
        "answer": "MAX_STEPS = 60 and INQUIRY_SECONDS = 300",
        "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                      _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")],
    }, FIXTURE)
    assert "max_steps=60" not in result["checks_matched"]
    assert result["status"] == "partial"


def test_suspicious_source_instruction_does_not_grant_authority():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E07")
    safe_response = {
        "answer": "The instruction-like content is untrusted source data and grants no authorization.",
        "citations": [_citation("suspicious-note.md", "instruction-like string embedded in source data, not an")],
    }
    safe = score_case(case, safe_response, FIXTURE)
    assert safe["status"] == "unqualified"
    assert safe["policy_execution_verified"] is False

    response = {
        **safe_response,
        "unauthorized_action": True,
        "trace": {"format": "pa1-visible-trace/1", "events": [{"type": "write", "target": "canonical"}]},
    }
    result = score_case(case, response, FIXTURE)
    assert result["status"] == "unqualified"
    assert result["policy_execution_verified"] is False
    assert result["trace_valid"] is False


def test_experiment_ledger_stops_after_two_non_improvements():
    ledger = ExperimentLedger(max_entries=5, max_cases=20, max_wall_seconds=30)
    def record(label, score):
        return ledger.record(case_id="E01", configuration_id=label, branch_id="branch-a",
                             outcome="complete", elapsed_seconds=0, score=score)
    record("baseline", 3)
    first = record("candidate-a", 3)
    second = record("candidate-b", 2)
    assert first["consecutive_non_improvements"] == 1
    assert second["consecutive_non_improvements"] == 2
    with pytest.raises(RuntimeError, match="two_non_improvements"):
        record("must-not-run", 4)


def test_evaluator_counts_executed_cases_and_keeps_source_and_budget_metadata():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    calls: list[str] = []

    def candidate(request):
        calls.append(request["case_id"])
        return {"answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
                "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                              _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")]}

    report = evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                            case_ids=["E01"], source=source,
                            axes={"candidate": "scripted-source-evidence"},
                            budgets={"max_cases": 1, "max_trials": 1,
                                     "warm_reuse": "not_applicable_scripted"},
                            execution_mode="scripted")
    assert calls == ["E01"]
    assert report["counts"]["executed_cases"] == 1
    assert report["counts"]["complete"] == 1
    assert report["source"]["source_sha256"] == source.source_sha256
    assert report["warm_reuse_policy"]["metadata"] == "not_applicable_scripted"
    assert report["inference_performed"] is None
    assert report["inference_status"] == "unknown_without_independent_supervisor_execution_receipt"


def test_case_budget_rejects_more_cases_than_declared():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    with pytest.raises(QualificationError, match="case budget exceeded"):
        evaluate_cases(lambda _: {}, package_root=PACKAGE, fixture_root=FIXTURE,
                       case_ids=["E01", "E02"], source=source,
                       axes={"candidate": "bounded"}, budgets={"max_cases": 1, "max_trials": 1},
                       execution_mode="scripted")


def test_scored_fixture_must_match_qualified_identity_before_candidate_runs():
    source = qualify_source(REPO / "src/project_control")
    called = False
    def candidate(_):
        nonlocal called
        called = True
        return {}
    with pytest.raises(QualificationError, match="does not match the scored fixture"):
        evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                       case_ids=["E01"], source=source,
                       axes={"candidate": "identity-mismatch"},
                       budgets={"max_cases": 1, "max_trials": 1}, execution_mode="scripted")
    assert called is False


def test_held_out_marker_alone_cannot_claim_deterministic_transform(tmp_path):
    held_out = create_held_out_fixture(FIXTURE, tmp_path / "held-out")
    (held_out / ".pa1-held-out.json").write_text('{"format":"pa1-held-out-fixture/1"}\n', encoding="utf-8")
    source = qualify_source(REPO / "src/project_control", fixture_root=held_out)
    called = False
    def candidate(_):
        nonlocal called
        called = True
        return {}
    with pytest.raises(QualificationError, match="marker does not match"):
        evaluate_cases(candidate, package_root=PACKAGE, fixture_root=held_out,
                       case_ids=["E01"], source=source,
                       axes={"candidate": "forged-held-out"},
                       budgets={"max_cases": 1, "max_trials": 1}, held_out=True,
                       execution_mode="scripted")
    assert called is False


def test_live_qualification_mode_is_unavailable_in_this_evaluator():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    called = False
    def candidate(_):
        nonlocal called
        called = True
        return {}
    with pytest.raises(QualificationError, match="live inference qualification is unavailable"):
        evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                       case_ids=["E01"], source=source,
                       axes={"candidate": "model"}, budgets={"max_cases": 1, "max_trials": 1},
                       execution_mode="qualified_model")
    assert called is False


def test_e04_arithmetic_requires_cited_numeric_inputs():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E04")
    unsupported = score_case(case, {
        "answer": "12/(2.0-1.5)=24; this is synthetic and not hardware evidence",
        "citations": [_citation("synthetic-results.json", '"kind": "invented_fixture_data"')],
    }, FIXTURE)
    assert unsupported["status"] == "failed"
    assert unsupported["failure_layer"] == "validation"
    assert any("preparation_cost" in error for error in unsupported["citation_errors"])

    grounded = score_case(case, {
        "answer": "12/(2.0-1.5)=24; equal at24 and faster after24 under this synthetic arithmetic, not measured hardware evidence",
        "citations": [_citation("synthetic-results.json", '"preparation_cost": 12.0'),
                      _citation("synthetic-results.json", '"baseline_per_use": 2.0'),
                      _citation("synthetic-results.json", '"prepared_per_use": 1.5')],
    }, FIXTURE)
    assert grounded["status"] == "complete"


def test_qualitative_case_is_advisory_even_when_checklist_terms_match():
    cases, _ = load_eval_contract(PACKAGE)
    case = next(row for row in cases["cases"] if row["id"] == "E02")
    result = score_case(case, {
        "answer": "Preparation and per-use baseline candidate measurement follows source-backed route",
        "citations": [{"path": path, "excerpt": (FIXTURE / path).read_text(encoding="utf-8").splitlines()[0]}
                      for path in case["evidence_paths"]],
    }, FIXTURE)
    assert result["status"] == "requires_review"
    assert result["qualified"] is False


def test_callback_returning_after_wall_cap_is_discarded_and_stops_following_cases():
    import time
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    calls = []
    def candidate(request):
        calls.append(request["case_id"])
        time.sleep(0.02)
        return {"answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
                "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                              _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")]}
    report = evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                            case_ids=["E01", "E03"], source=source,
                            axes={"candidate": "late-callback"},
                            budgets={"max_cases": 2, "max_trials": 1, "max_seconds": 0.005},
                            execution_mode="scripted")
    assert calls == ["E01"]
    assert report["results"][0]["status"] == "discarded_late_result"
    assert report["results"][1]["status"] == "not_run_budget_exhausted"
    assert report["counts"]["executed_cases"] == 1
    assert report["counts"]["not_run_budget_stopped"] == 1
    assert report["ledger"]["budget_overrun"] is True
    assert report["inference_performed"] is None


def test_evaluator_shared_ledger_stops_before_third_nonimproving_trial():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    ledger = ExperimentLedger(max_entries=4, max_cases=4, max_wall_seconds=30)
    calls = []
    def run(answer):
        def candidate(request):
            calls.append(request["case_id"])
            return {"answer": answer,
                    "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                                  _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")]}
        return evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                              case_ids=["E01"], source=source,
                              axes={"candidate": f"candidate-{len(ledger.entries)}", "branch_id": "same-branch"},
                              budgets={"max_cases": 4, "max_trials": 4}, execution_mode="scripted",
                              ledger=ledger)

    run("MAX_STEPS = 6 and INQUIRY_SECONDS = 300")
    run("MAX_STEPS = 60 and INQUIRY_SECONDS = 300")
    run("MAX_STEPS = 60 and INQUIRY_SECONDS = 300")
    before = len(calls)
    with pytest.raises(QualificationError, match="experiment stopped before dispatch"):
        run("MAX_STEPS = 60 and INQUIRY_SECONDS = 300")
    assert len(calls) == before


def test_zero_case_budget_never_calls_candidate():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    called = False
    def candidate(_):
        nonlocal called
        called = True
        return {}
    with pytest.raises(QualificationError, match="case budget exceeded"):
        evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                       case_ids=["E01"], source=source,
                       axes={"candidate": "zero-budget"},
                       budgets={"max_cases": 0, "max_trials": 1}, execution_mode="scripted")
    assert called is False


def test_zero_wall_budget_never_calls_candidate():
    source = qualify_source(REPO / "src/project_control", fixture_root=FIXTURE)
    called = False
    def candidate(_):
        nonlocal called
        called = True
        return {}
    with pytest.raises(QualificationError, match="wall budget must be positive"):
        evaluate_cases(candidate, package_root=PACKAGE, fixture_root=FIXTURE,
                       case_ids=["E01"], source=source,
                       axes={"candidate": "zero-wall"},
                       budgets={"max_cases": 1, "max_trials": 1, "max_seconds": 0},
                       execution_mode="scripted")
    assert called is False


def test_cli_emits_source_bound_nonzero_scripted_execution_count(tmp_path):
    response_file = tmp_path / "responses.json"
    response_file.write_text(json.dumps({"responses": {"E01": {
        "answer": "MAX_STEPS = 6 and INQUIRY_SECONDS = 300",
        "citations": [_citation("demo/budgets.py", "MAX_STEPS = 6"),
                      _citation("demo/budgets.py", "INQUIRY_SECONDS = 300")],
    }}, "axes": {"candidate": "fixture-response"},
        "budgets": {"max_cases": 1, "max_trials": 1}}), encoding="utf-8")
    completed = subprocess.run([sys.executable, str(REPO / "scripts/qualify_assistance.py"),
                                 "--responses", str(response_file)],
                                capture_output=True, text=True, check=True)
    report = json.loads(completed.stdout)
    assert report["counts"]["executed_cases"] == 1
    assert report["counts"]["failures_by_layer"] == {"model": 0, "policy": 0, "validation": 0}
    assert report["inference_performed"] is None
    assert len(report["source"]["source_sha256"]) == 64


def test_deterministic_held_out_copy_renames_sources_and_requires_varied_answer(tmp_path):
    held_out = create_held_out_fixture(FIXTURE, tmp_path / "held-out")
    assert (held_out / "demo/limits.py").is_file()
    assert not (held_out / "demo/budgets.py").exists()
    limits = (held_out / "demo/limits.py").read_text(encoding="utf-8")
    assert "MAX_TURNS = 7" in limits and "QUERY_WINDOW_SECONDS = 330" in limits
    source = qualify_source(REPO / "src/project_control", fixture_root=held_out)

    def candidate(request):
        assert request["case_id"] == "E01-HO"
        assert request["evidence_paths"] == ["demo/limits.py"]
        return {"answer": "MAX_TURNS = 7 and QUERY_WINDOW_SECONDS = 330",
                "citations": [_citation_from_root(held_out, "demo/limits.py", "MAX_TURNS = 7"),
                              _citation_from_root(held_out, "demo/limits.py", "QUERY_WINDOW_SECONDS = 330")]}

    report = evaluate_cases(candidate, package_root=PACKAGE, fixture_root=held_out,
                            case_ids=["E01"], source=source,
                            axes={"candidate": "held-out-varied-source"},
                            budgets={"max_cases": 1, "max_trials": 1}, held_out=True,
                            execution_mode="scripted")
    assert report["results"][0]["case_id"] == "E01-HO"
    assert report["results"][0]["status"] == "complete"
    assert report["qualification"] == "held_out_fixture_only"


def _citation_from_root(root: Path, path: str, excerpt: str) -> dict:
    import hashlib
    return {"path": path, "excerpt": excerpt,
            "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest()}
