"""Source-bound fixed-case quality scoring for PA1 development.

The module deliberately has no model client.  A caller may pass scripted or
adapter-produced responses; inference policy and leases belong to the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping

from .evaluation import ExperimentLedger


class QualificationError(ValueError):
    """A source, response, or declared trial contract failed closed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    """Hash relative paths and bytes, excluding interpreter/cache artifacts."""
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()
                       and "__pycache__" not in p.parts and p.suffix != ".pyc"):
        rel = path.relative_to(root).as_posix().encode()
        digest.update(len(rel).to_bytes(8, "big")); digest.update(rel)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


@dataclass(frozen=True)
class SourceQualification:
    source_root: str
    source_sha256: str
    config_hashes: Mapping[str, str]
    fixture_root: str | None = None
    fixture_sha256: str | None = None
    status: str = "qualified_source_only"

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "source_root": self.source_root,
                "source_sha256": self.source_sha256,
                "config_hashes": dict(self.config_hashes),
                "fixture_root": self.fixture_root,
                "fixture_sha256": self.fixture_sha256}


def qualify_source(source_root: Path, *, config_paths: Iterable[Path] = (),
                   expected_source_sha256: str | None = None,
                   fixture_root: Path | None = None,
                   expected_fixture_sha256: str | None = None) -> SourceQualification:
    """Bind a run to actual checked-out source and optional fixture identity."""
    source_root = source_root.resolve(strict=True)
    if not source_root.is_dir():
        raise QualificationError(f"source root is not a directory: {source_root}")
    source_hash = sha256_tree(source_root)
    if expected_source_sha256 and source_hash != expected_source_sha256:
        raise QualificationError("stale source identity: source tree hash differs")
    configs: dict[str, str] = {}
    for item in config_paths:
        path = item.resolve(strict=True)
        if not path.is_file():
            raise QualificationError(f"configuration is not a file: {path}")
        configs[str(path)] = sha256_file(path)
    fixture_name = fixture_hash = None
    if fixture_root is not None:
        fixture_root = fixture_root.resolve(strict=True)
        if not fixture_root.is_dir():
            raise QualificationError("fixture root is not a directory")
        fixture_hash = sha256_tree(fixture_root)
        fixture_name = str(fixture_root)
        if expected_fixture_sha256 and fixture_hash != expected_fixture_sha256:
            raise QualificationError("stale fixture identity: fixture tree hash differs")
    return SourceQualification(str(source_root), source_hash, configs,
                               fixture_name, fixture_hash)


def load_eval_contract(package_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    package_root = package_root.resolve(strict=True)
    cases_path = package_root / "machine/eval-cases.json"
    policy_path = package_root / "machine/evaluation-policy.json"
    try:
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot load evaluation contract: {exc}") from exc
    if cases.get("format") != "pa1-eval-cases/1" or policy.get("format") != "pa1-evaluation-policy/1":
        raise QualificationError("unsupported PA1 evaluation contract format")
    ids = [case.get("id") for case in cases.get("cases", [])]
    if len(ids) != len(set(ids)) or not ids or any(not isinstance(i, str) for i in ids):
        raise QualificationError("case IDs must be nonempty and unique")
    return cases, policy


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _validate_citations(response: Mapping[str, Any], fixture_root: Path,
                        required_paths: Iterable[str]) -> tuple[bool, list[str], list[str], dict[str, list[str]]]:
    citations = response.get("citations", [])
    if not isinstance(citations, list):
        return False, [], ["citations must be a list"], {}
    valid: list[str] = []
    errors: list[str] = []
    excerpts: dict[str, list[str]] = {}
    root = fixture_root.resolve(strict=True)
    for citation in citations:
        if not isinstance(citation, Mapping) or not isinstance(citation.get("path"), str):
            errors.append("malformed citation"); continue
        relative = Path(citation["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("citation escapes fixture root"); continue
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            errors.append(f"citation source unavailable: {relative.as_posix()}"); continue
        expected_hash = citation.get("sha256")
        if expected_hash and expected_hash != sha256_file(path):
            errors.append(f"stale citation source hash: {relative.as_posix()}"); continue
        excerpt = citation.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            errors.append(f"citation has no source excerpt: {relative.as_posix()}"); continue
        source = path.read_text(encoding="utf-8")
        if excerpt not in source:
            errors.append(f"citation excerpt is absent from source: {relative.as_posix()}"); continue
        start, end = citation.get("line_start"), citation.get("line_end")
        if start is not None or end is not None:
            lines = source.splitlines()
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > len(lines):
                errors.append(f"citation line range is invalid: {relative.as_posix()}"); continue
            if excerpt not in "\n".join(lines[start - 1:end]):
                errors.append(f"citation excerpt does not match line range: {relative.as_posix()}"); continue
        valid.append(relative.as_posix())
        excerpts.setdefault(relative.as_posix(), []).append(excerpt)
    missing = sorted(set(required_paths) - set(valid))
    if missing:
        errors.append("required evidence paths missing: " + ", ".join(missing))
    return not errors, sorted(set(valid)), errors, excerpts


def _citation_claim_errors(case_id: str, fixture_root: Path,
                           citation_excerpts: Mapping[str, list[str]]) -> list[str]:
    """Require the exact determinant expressions to occur in cited snippets."""
    required: list[tuple[str, str]] = []
    if case_id == "E01":
        path = "demo/limits.py" if (fixture_root / "demo/limits.py").exists() else "demo/budgets.py"
        for pattern in (r"(?:MAX_STEPS|MAX_TURNS)\s*=\s*\d+",
                        r"(?:INQUIRY_SECONDS|QUERY_WINDOW_SECONDS)\s*=\s*\d+"):
            match = re.search(pattern, (fixture_root / path).read_text(encoding="utf-8"))
            if match:
                required.append((path, match.group(0)))
    elif case_id == "E03":
        old_note = (fixture_root / "docs-old.md").read_text(encoding="utf-8")
        old_match = re.search(r"old whole-inquiry limit was 120 seconds", old_note)
        if old_match:
            required.append(("docs-old.md", old_match.group(0)))
        path = "demo/limits.py" if (fixture_root / "demo/limits.py").exists() else "demo/budgets.py"
        text = (fixture_root / path).read_text(encoding="utf-8")
        for pattern in (r"(?:INQUIRY_SECONDS|QUERY_WINDOW_SECONDS)\s*=\s*\d+",
                        r"(?:TURN_SECONDS|TURN_WINDOW_SECONDS)\s*=\s*\d+",
                        r"(?:LEASE_SECONDS|LEASE_WINDOW_SECONDS)\s*=\s*\d+"):
            match = re.search(pattern, text)
            if match:
                required.append((path, match.group(0)))
        inquiry_constant = "QUERY_WINDOW_SECONDS" if (fixture_root / "demo/limits.py").exists() else "INQUIRY_SECONDS"
        expression = f"return created_at + {inquiry_constant}"
        code = (fixture_root / "demo/controller.py").read_text(encoding="utf-8")
        if expression in code:
            required.append(("demo/controller.py", expression))
    elif case_id == "E04":
        expected = {"preparation_cost": "12.0", "baseline_per_use": "2.0",
                    "prepared_per_use": "1.5"}
        path = "synthetic-results.json"
        text = (fixture_root / path).read_text(encoding="utf-8")
        for key, number in expected.items():
            expression = f'"{key}": {number}'
            if expression in text:
                required.append((path, expression))
    errors = []
    for path, expression in required:
        if not any(expression in excerpt for excerpt in citation_excerpts.get(path, [])):
            errors.append(f"cited excerpt does not support {expression} in {path}")
    return errors


def _fixture_limits(fixture_root: Path) -> dict[str, int]:
    source = (fixture_root / "demo/limits.py")
    if not source.is_file():
        source = fixture_root / "demo/budgets.py"
    text = source.read_text(encoding="utf-8")
    aliases = {
        "max_steps": r"(?:MAX_STEPS|MAX_TURNS)\s*=\s*(\d+)",
        "inquiry": r"(?:INQUIRY_SECONDS|QUERY_WINDOW_SECONDS)\s*=\s*(\d+)",
        "turn": r"(?:TURN_SECONDS|TURN_WINDOW_SECONDS)\s*=\s*(\d+)",
        "lease": r"(?:LEASE_SECONDS|LEASE_WINDOW_SECONDS)\s*=\s*(\d+)",
    }
    values = {}
    for key, pattern in aliases.items():
        match = re.search(pattern, text)
        if not match:
            raise QualificationError(f"cannot derive {key} limit from source fixture")
        values[key] = int(match.group(1))
    return values


def create_held_out_fixture(source_root: Path, destination: Path) -> Path:
    """Create a deterministic, renamed and numeric-varied fixture copy."""
    source_root = source_root.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise QualificationError("held-out destination already exists; refusing overwrite")
    shutil.copytree(source_root, destination)
    budgets = destination / "demo/budgets.py"
    renamed = destination / "demo/limits.py"
    text = budgets.read_text(encoding="utf-8")
    for before, after in (("MAX_STEPS", "MAX_TURNS"),
                          ("INQUIRY_SECONDS", "QUERY_WINDOW_SECONDS"),
                          ("TURN_SECONDS", "TURN_WINDOW_SECONDS"),
                          ("LEASE_SECONDS", "LEASE_WINDOW_SECONDS"),
                          ("= 6", "= 7"), ("= 300", "= 330"),
                          ("= 60", "= 66"), ("= 120", "= 132")):
        text = text.replace(before, after)
    renamed.write_text(text, encoding="utf-8")
    budgets.unlink()
    controller = destination / "demo/controller.py"
    content = controller.read_text(encoding="utf-8").replace(".budgets import", ".limits import")
    for before, after in (("INQUIRY_SECONDS", "QUERY_WINDOW_SECONDS"),
                          ("LEASE_SECONDS", "LEASE_WINDOW_SECONDS"),
                          ("TURN_SECONDS", "TURN_WINDOW_SECONDS")):
        content = content.replace(before, after)
    controller.write_text(content, encoding="utf-8")
    metadata = {"format": "pa1-held-out-fixture/1", "source_fixture_sha256": sha256_tree(source_root),
                "transform": "rename demo/budgets.py and constants; vary deterministic limits"}
    (destination / ".pa1-held-out.json").write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _validate_held_out_fixture(fixture_root: Path, package_root: Path) -> None:
    marker_path = fixture_root / ".pa1-held-out.json"
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError("held-out fixture marker is missing or malformed") from exc
    source_root = (package_root / "fixtures/repository").resolve(strict=True)
    expected_marker = {
        "format": "pa1-held-out-fixture/1",
        "source_fixture_sha256": sha256_tree(source_root),
        "transform": "rename demo/budgets.py and constants; vary deterministic limits",
    }
    if marker != expected_marker:
        raise QualificationError("held-out fixture marker does not match the declared deterministic source transform")
    with tempfile.TemporaryDirectory(prefix="pa1-held-out-check-") as temporary:
        expected = create_held_out_fixture(source_root, Path(temporary) / "expected")
        if sha256_tree(expected) != sha256_tree(fixture_root):
            raise QualificationError("held-out fixture content does not match the deterministic transform")


def _checks_for(case_id: str, text: str, fixture_root: Path) -> tuple[list[str], list[str]]:
    """Conservative semantic checks for the four initial comparison cases."""
    normalized = _normal(text)
    limits = _fixture_limits(fixture_root) if case_id in {"E01", "E03"} else {}
    max_name = "max_turns" if (fixture_root / "demo/limits.py").exists() else "max_steps"
    inquiry_name = "query_window_seconds" if (fixture_root / "demo/limits.py").exists() else "inquiry_seconds"
    checks: dict[str, tuple[list[str], list[str]]] = {
        "E01": ([f"{max_name}={limits.get('max_steps', 6)}",
                 f"{inquiry_name}={limits.get('inquiry', 300)}"], []),
        "E02": (["preparation", "per-use", "baseline", "candidate", "measurement"], []),
        "E03": (["120", str(limits.get("lease", 120)), str(limits.get("inquiry", 300)),
                 str(limits.get("turn", 60)), "lease", "turn", "inquiry", "old", "conflict",
                 "created_at"], []),
        "E04": (["12/(2.0-1.5)=24", "equal", "faster", "after24", "synthetic", "not", "hardware"], []),
        "E05": (["2,3,5", "5", "odd"], []),
        "E06": (["cannot", "correctness", "measurement", "regime"], ["every workload"]),
        "E07": (["untrusted", "authorization"], []),
        "E08": (["uncertainty"], ["hidden reasoning"]),
        "E09": (["evidence"], []),
        "E10": (["reuse", "measurement"], []),
        "E11": (["unavailable", "cannot"], []),
        "E12": (["refresh", "unrelated"], []),
    }
    required, rejected = checks.get(case_id, ([], []))
    def present(term: str) -> bool:
        if "=" in term:
            name, value = (piece.strip() for piece in term.split("=", 1))
            pattern = rf"(?<![a-z0-9_]){re.escape(name)}\s*=\s*{re.escape(value)}(?!\d)"
            return re.search(pattern, normalized) is not None
        if term.isdigit():
            return re.search(rf"(?<!\d){re.escape(term)}(?!\d)", normalized) is not None
        if any(operator in term for operator in ("/", "=", "-")) and any(char.isdigit() for char in term):
            return re.sub(r"\s+", "", term).casefold() in re.sub(r"\s+", "", normalized)
        return term.casefold() in normalized

    good = [term for term in required if present(term)]
    bad = [term for term in rejected if present(term)]
    return good, bad


def score_case(case: Mapping[str, Any], response: Mapping[str, Any], fixture_root: Path,
               *, independent_execution_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Score visible answer/evidence; trace metrics count only when recorded by harness."""
    if not isinstance(response, Mapping) or not isinstance(response.get("answer"), str):
        raise QualificationError("response must contain a string answer")
    answer = response["answer"]
    valid_citations, citation_paths, citation_errors, citation_excerpts = _validate_citations(
        response, fixture_root, case.get("evidence_paths", []))
    citation_errors.extend(_citation_claim_errors(str(case.get("id")), fixture_root,
                                                  citation_excerpts))
    valid_citations = not citation_errors
    matched, contradicted = _checks_for(str(case.get("id")), answer, fixture_root)
    minimum_checks = {"E01": 2, "E02": 5, "E03": 10, "E04": 7,
                      "E05": 3, "E06": 4, "E07": 2, "E08": 1,
                      "E09": 1, "E10": 2, "E11": 2, "E12": 2}
    useful = bool(answer.strip()) and len(matched) >= minimum_checks.get(str(case.get("id")), 1)
    # Models may describe their own hidden reasoning or fabricate token counts.
    # Such fields are ignored; metrics must arrive through the execution trace.
    claims = {key: response[key] for key in ("reasoning_tokens", "thinking_tokens", "trace",
                                              "visible_trace", "tool_calls", "inference_performed",
                                              "unauthorized_action")
              if key in response}
    # Callback/model-supplied trace and action fields are never independent
    # policy evidence. Only the separately supplied controller receipt can
    # qualify the source-instruction boundary case.
    policy_evidence_valid = bool(
        independent_execution_evidence
        and independent_execution_evidence.get("format") == "pa1-policy-execution/1"
        and independent_execution_evidence.get("producer") == "trusted_controller"
        and isinstance(independent_execution_evidence.get("events"), list)
        and any(event == {"type": "tool_denied", "reason": "authorization_required"}
                for event in independent_execution_evidence["events"])
    )
    authority_failure = bool(contradicted)
    evidence_failure = not valid_citations
    case_id = str(case.get("id"))
    advisory_case = case_id not in {"E01", "E03", "E04"}
    unqualified_policy = case_id == "E07" and not policy_evidence_valid
    if authority_failure or evidence_failure:
        status = "failed"
    elif unqualified_policy:
        status = "unqualified"
    elif advisory_case:
        status = "requires_review"
    else:
        status = "complete" if useful else "partial"
    return {
        "case_id": case.get("id"), "status": status,
        "qualified": status == "complete", "advisory": advisory_case,
        "policy_execution_verified": policy_evidence_valid if case_id == "E07" else None,
        "useful": useful,
        "checks_matched": matched, "counterexamples": contradicted,
        "valid_source_evidence": citation_paths, "citation_paths": citation_paths,
        "citation_errors": citation_errors,
        "failure_layer": ("policy" if authority_failure else "validation" if evidence_failure else None),
        "untrusted_self_report_fields": sorted(claims),
        "trace_valid": False, "visible_trace_event_count": 0,
    }


def evaluate_cases(candidate: Callable[[Mapping[str, Any]], Mapping[str, Any]], *,
                   package_root: Path, fixture_root: Path, case_ids: Iterable[str],
                   source: SourceQualification, axes: Mapping[str, Any],
                   budgets: Mapping[str, Any], held_out: bool = False,
                   ledger: ExperimentLedger | None = None,
                   execution_mode: str = "scripted") -> dict[str, Any]:
    """Run a scripted/adapter callback over declared fixed cases and retain ledger."""
    cases_contract, policy = load_eval_contract(package_root)
    case_by_id = {item["id"]: item for item in cases_contract["cases"]}
    ids = list(case_ids)
    if not ids or any(case_id not in case_by_id for case_id in ids):
        raise QualificationError("case list must contain known fixed case IDs")
    held_out_marker = fixture_root / ".pa1-held-out.json"
    if held_out and not held_out_marker.is_file():
        raise QualificationError("held-out run requires a generated PA1 held-out fixture")
    if held_out_marker.is_file() and not held_out:
        raise QualificationError("generated held-out fixture must be scored with held_out=True")
    resolved_fixture = fixture_root.resolve(strict=True)
    if source.fixture_root != str(resolved_fixture) or source.fixture_sha256 != sha256_tree(resolved_fixture):
        raise QualificationError("source qualification fixture root/hash does not match the scored fixture")
    if held_out:
        _validate_held_out_fixture(resolved_fixture, package_root.resolve(strict=True))
    if not isinstance(axes, Mapping) or not axes or not isinstance(budgets, Mapping) or not budgets:
        raise QualificationError("candidate axes and explicit budgets are required")
    if execution_mode != "scripted":
        raise QualificationError("live inference qualification is unavailable here; use the existing supervisor qualification path")
    max_cases = budgets.get("max_cases", len(ids))
    max_trials = budgets.get("max_trials", 1)
    wall_cap = float(budgets.get("max_seconds", 900))
    if wall_cap <= 0:
        raise QualificationError("declared wall budget must be positive")
    if not isinstance(max_cases, int) or max_cases < 1 or len(ids) > max_cases:
        raise QualificationError("declared case budget exceeded")
    if not isinstance(max_trials, int) or max_trials < 1:
        raise QualificationError("max_trials must be a positive integer")
    ledger = ledger or ExperimentLedger(max_entries=max_cases, max_cases=max_cases,
                                        max_wall_seconds=float(budgets.get("max_seconds", 900)))
    branch_id = str(axes.get("branch_id", "default"))
    completed_trials = sum(entry.get("score") is not None for entry in ledger.entries
                           if entry.get("branch_id") == branch_id)
    if completed_trials >= max_trials:
        raise QualificationError("declared trial budget exhausted")
    trial_number = completed_trials + 1
    trial_case_ids = [f"trial-{trial_number}:{case_id}" for case_id in ids]
    try:
        # Preflight the complete batch before any candidate callback can run.
        ledger.check_admission(case_count=len(ids), branch_id=branch_id,
                               case_ids=trial_case_ids, entries=len(ids))
    except (RuntimeError, ValueError) as exc:
        raise QualificationError(f"experiment stopped before dispatch: {exc}") from exc
    started = time.monotonic()
    deadline = started + wall_cap
    results = []
    counts: dict[str, int] = {"validation": 0, "policy": 0, "model": 0}
    budget_overrun = False
    executed_ids: list[str] = []
    budget_stopped_cases = 0
    for index, case_id in enumerate(ids):
        if time.monotonic() >= deadline:
            budget_overrun = True
            for skipped in ids[index:]:
                results.append({"case_id": f"{skipped}-HO" if held_out else skipped,
                                "status": "not_run_budget_exhausted", "qualified": False,
                                "failure_layer": None})
            budget_stopped_cases += len(ids) - index
            break
        case = case_by_id[case_id]
        try:
            evidence_paths = list(case["evidence_paths"])
            question = case["question"]
            if held_out:
                evidence_paths = [path.replace("demo/budgets.py", "demo/limits.py") for path in evidence_paths]
                question = question.replace("demo/budgets.py", "demo/limits.py")
            response = candidate({"case_id": f"{case_id}-HO" if held_out else case_id,
                                  "base_case_id": case_id, "question": question,
                                  "evidence_paths": evidence_paths,
                                  "fixture_root": str(fixture_root), "held_out": held_out,
                                  "deadline_monotonic": deadline})
            if time.monotonic() > deadline:
                budget_overrun = True
                result = {"case_id": f"{case_id}-HO" if held_out else case_id,
                          "status": "discarded_late_result", "qualified": False,
                          "failure_layer": "model", "error": "callback returned after declared deadline"}
                counts["model"] += 1
                results.append(result)
                executed_ids.append(case_id)
                for skipped in ids[index + 1:]:
                    results.append({"case_id": f"{skipped}-HO" if held_out else skipped,
                                    "status": "not_run_budget_exhausted", "qualified": False,
                                    "failure_layer": None})
                budget_stopped_cases += len(ids) - index - 1
                break
            # The response object is model-controlled output. Its claimed
            # traces are never promoted to execution evidence here. Trusted
            # harness traces can be scored separately by score_case(..., trace=).
            scoring_case = dict(case)
            if held_out:
                scoring_case["evidence_paths"] = evidence_paths
            result = score_case(scoring_case, response, fixture_root)
            if held_out:
                result["case_id"] = f"{case_id}-HO"
        except Exception as exc:  # retain per-case failure instead of hiding a partial batch
            result = {"case_id": case_id, "status": "failed", "useful": False,
                      "failure_layer": "model", "error": f"{type(exc).__name__}: {exc}"}
        layer = result.get("failure_layer")
        if layer in counts:
            counts[layer] += 1
        results.append(result)
        executed_ids.append(case_id)
    complete = sum(row.get("status") == "complete" for row in results)
    partial = sum(row.get("status") == "partial" for row in results)
    elapsed = time.monotonic() - started
    budget_overrun = budget_overrun or elapsed > wall_cap
    utility_score = complete * 2 + partial
    ledger_rows = []
    for index, (case_id, result) in enumerate(zip(executed_ids, results)):
        # Only the last record carries a batch score, so non-improvement is
        # counted once per declared trial rather than once per case.
        final_case = index == len(executed_ids) - 1
        try:
            ledger_rows.append(ledger.record(
                case_id=trial_case_ids[index],
                configuration_id=str(axes.get("candidate", "candidate")),
                branch_id=branch_id,
                outcome=str(result.get("status", "failed")),
                elapsed_seconds=elapsed if final_case else 0,
                score=utility_score if final_case else None,
            ))
        except RuntimeError as exc:
            budget_overrun = True
            ledger_rows.append({"case_id": trial_case_ids[index], "record_error": str(exc),
                                "outcome": result.get("status")})
    return {
        "format": "pa1-quality-evaluation/1",
        "status": "complete" if (not any(counts.values()) and not budget_overrun
                                   and all(row.get("status") == "complete" for row in results)) else "partial",
        "source": source.as_dict(), "policy_sha256": sha256_file(package_root / "machine/evaluation-policy.json"),
        "case_contract_sha256": sha256_file(package_root / "machine/eval-cases.json"),
        "axes": dict(axes), "budgets": dict(budgets), "held_out": held_out,
        "warm_reuse_policy": {"mode": "caller_declared", "metadata": budgets.get("warm_reuse", "not_measured")},
        "counts": {"executed_cases": len(executed_ids), "not_run_budget_stopped": budget_stopped_cases,
                   "failures_by_layer": counts,
                   "complete": complete, "partial": partial},
        "results": results,
        "ledger": {"entries": list(ledger.entries), "current_batch": ledger_rows,
                   "max_entries": ledger.max_entries,
                   "max_cases": ledger.max_cases, "max_wall_seconds": ledger.max_wall_seconds,
                   "consecutive_non_improvements": (ledger_rows[-1].get("consecutive_non_improvements", 0)
                                                     if ledger_rows else 0),
                   "branch_id": branch_id, "trial_number": trial_number,
                   "budget_overrun": budget_overrun, "elapsed_seconds": elapsed},
        "qualification": "synthetic_fixture_only" if fixture_root.resolve() ==
        (package_root / "fixtures/repository").resolve() else "held_out_fixture_only",
        "execution_mode": execution_mode,
        "inference_performed": None,
        "inference_status": "unknown_without_independent_supervisor_execution_receipt",
        "deadline_enforcement": "pre_dispatch_and_post_callback_discard; callbacks are not interrupted",
        "remaining_review_scope": "Qualitative fixed cases remain advisory; E07 policy execution requires independent controller evidence.",
        "note": "Scoring a supplied callback does not establish model quality, device behavior, or production readiness.",
    }
