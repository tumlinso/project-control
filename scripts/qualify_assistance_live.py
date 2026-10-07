#!/usr/bin/env python3
"""Run bounded PA1 source cases through the verified current supervisor.

This command is inert unless ``--execute-live`` is supplied. Live mode verifies
the already-running canonical supervisor before it starts an isolated public
AS1 job service; it never starts a supervisor or changes production cache state.
Artifacts are kept under a caller-provided private directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from project_control.assistance.quality import (  # noqa: E402
    QualificationError,
    _validate_held_out_fixture,
    load_eval_contract,
    qualify_source,
    score_case,
    sha256_file,
)


INITIAL_IDS = ("E01", "E02", "E03", "E04")
INITIAL_BUDGET = {"max_new_inquiries": 12, "wall_seconds": 900,
                  "max_parallel_executions": 2}
HELD_OUT_BUDGET = {"max_new_inquiries": 4, "wall_seconds": 300,
                   "max_parallel_executions": 2}
MAX_REQUEST_BYTES = 1024 * 1024
_RESPONSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["answer", "citations"],
    "properties": {
        "answer": {"type": "string", "minLength": 1, "maxLength": 12000},
        "citations": {
            "type": "array", "maxItems": 16,
            "items": {"type": "object", "additionalProperties": False,
                      "required": ["path", "excerpt"],
                      "properties": {
                          "path": {"type": "string", "maxLength": 256},
                          "excerpt": {"type": "string", "minLength": 1, "maxLength": 1200},
                          "sha256": {"type": "string", "maxLength": 64},
                          "line_start": {"type": "integer", "minimum": 1},
                          "line_end": {"type": "integer", "minimum": 1},
                      }},
        },
    },
}


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False,
                      allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _private_artifact_root(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise QualificationError("artifact root must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise QualificationError("artifact root must be a directory owned by the current user")
    resolved.chmod(0o700)
    if resolved.stat().st_mode & 0o077:
        raise QualificationError("artifact root permissions are not private")
    return resolved


def _load_cases(package_root: Path, fixture_root: Path, phase: str,
                case_set: Path | None) -> tuple[list[dict[str, Any]], bool]:
    contract, _ = load_eval_contract(package_root)
    if phase == "baseline":
        if case_set is not None:
            raise QualificationError("baseline cases come from the pinned E01-E04 contract")
        by_id = {item["id"]: item for item in contract["cases"]}
        return [dict(by_id[item]) for item in INITIAL_IDS], False
    if phase == "heldout":
        if case_set is not None:
            raise QualificationError("held-out cases come from the pinned E01-E04 contract")
        by_id = {item["id"]: item for item in contract["cases"]}
        held_cases = [dict(by_id[item]) for item in INITIAL_IDS]
        for case in held_cases:
            case["question"] = case["question"].replace("demo/budgets.py", "demo/limits.py")
            case["evidence_paths"] = [path.replace("demo/budgets.py", "demo/limits.py")
                                      for path in case["evidence_paths"]]
        return held_cases, True
    if case_set is None:
        raise QualificationError("extension phase requires a reviewed case-set JSON file")
    try:
        payload = json.loads(case_set.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"cannot read extension case set: {error}") from error
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if (not isinstance(cases, list) or not 1 <= len(cases) <= 4 or
            any(not isinstance(item, dict) or not isinstance(item.get("id"), str) or
                not isinstance(item.get("question"), str) or
                not isinstance(item.get("evidence_paths"), list) or
                any(not isinstance(path, str) for path in item["evidence_paths"])
                for item in cases)):
        raise QualificationError("extension cases require id, question, and evidence_paths")
    ids = [item["id"] for item in cases]
    if len(ids) != len(set(ids)):
        raise QualificationError("extension case IDs must be unique")
    for case in cases:
        if not case["evidence_paths"]:
            raise QualificationError(f"extension case {case['id']} has no source evidence")
    return [dict(item) for item in cases], False


def _effective_budget(phase: str, count: int, max_inquiries: int | None,
                      wall_seconds: int | None) -> dict[str, int]:
    if phase == "baseline":
        caps = INITIAL_BUDGET
    elif phase == "heldout":
        caps = HELD_OUT_BUDGET
    else:
        caps = {"max_new_inquiries": 4, "wall_seconds": 300,
                "max_parallel_executions": 2}
    inquiries = max_inquiries if max_inquiries is not None else caps["max_new_inquiries"]
    wall = wall_seconds if wall_seconds is not None else caps["wall_seconds"]
    if (isinstance(inquiries, bool) or not isinstance(inquiries, int) or
            not count <= inquiries <= caps["max_new_inquiries"]):
        raise QualificationError("declared inquiry budget is outside the phase cap or below case count")
    if isinstance(wall, bool) or not isinstance(wall, int) or not 1 <= wall <= caps["wall_seconds"]:
        raise QualificationError("declared wall budget is outside the phase cap")
    return {"max_new_inquiries": inquiries, "wall_seconds": wall,
            "max_parallel_executions": caps["max_parallel_executions"]}


def _source_packet(case: Mapping[str, Any], fixture_root: Path) -> list[dict[str, Any]]:
    packet = []
    total = 0
    root = fixture_root.resolve(strict=True)
    for raw in case["evidence_paths"]:
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise QualificationError(f"evidence path escapes fixture: {raw}")
        path = (root / relative).resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root):
            raise QualificationError(f"fixture evidence is unavailable: {raw}")
        content = path.read_bytes()
        total += len(content)
        if total > 2 * 1024 * 1024:
            raise QualificationError("case evidence exceeds the bounded source packet size")
        packet.append({"path": relative.as_posix(), "sha256": hashlib.sha256(content).hexdigest(),
                       "text": content.decode("utf-8", errors="strict")})
    return packet


def _model_response(response: Mapping[str, Any]) -> dict[str, Any]:
    text = response.get("text")
    if not isinstance(text, str):
        raise QualificationError("supervisor response has no visible text")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise QualificationError(f"model response is not valid JSON: {error.msg}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("answer"), str):
        raise QualificationError("model response does not satisfy the visible answer contract")
    citations = payload.get("citations")
    if not isinstance(citations, list):
        raise QualificationError("model response citations must be a list")
    # Discard all model-authored trace, usage, reasoning, and action claims.
    return {"answer": payload["answer"], "citations": citations}


def _request(case: Mapping[str, Any], evidence: list[dict[str, Any]], session_id: str,
             deadline_epoch: float, parallelism: str) -> dict[str, Any]:
    system = (
        "Answer only from the supplied fixture evidence. Return one JSON object with "
        "answer and citations. Each citation must contain path and an exact excerpt; "
        "include the supplied file SHA-256 and line bounds when known. Do not claim "
        "tool actions, hidden reasoning, or facts absent from the evidence."
    )
    user = json.dumps({"question": case["question"], "evidence": evidence},
                      ensure_ascii=False, separators=(",", ":"))
    request = {
        "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 1200, "timeout_seconds": 90,
        "compute_profile": "narrow", "parallelism": parallelism,
        "session_id": session_id, "deadline_epoch": deadline_epoch,
        "reasoning_mode": "auto",
        "response_format": {"type": "json_object", "schema": _RESPONSE_SCHEMA},
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise QualificationError("effective model request exceeds the supervisor request bound")
    return request


def _isolated_composition(state_root: Path, supervisor_state_root: Path | None = None):
    from project_control.app import Runtime
    from project_control.as1_surface import compose_surface
    from project_control.config import load_config
    from project_control.observer_analysis import SkillsObserverAnalysisProvider
    from project_control.profiles import MCPProfile
    from project_control.runtime_binding import bind_local_runtime

    identity = bind_local_runtime()
    if not identity.root.is_dir() or not identity.manifest_sha256:
        raise QualificationError("canonical receiver runtime identity is incomplete")
    # The provider captures this setting during construction. Keep the override
    # scoped to construction; its client retains the chosen canonical root.
    previous_state_root = os.environ.get("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR")
    if supervisor_state_root is not None:
        os.environ["PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR"] = str(
            supervisor_state_root.expanduser().resolve(strict=True))
    try:
        backend = SkillsObserverAnalysisProvider()
    finally:
        if supervisor_state_root is not None:
            if previous_state_root is None:
                os.environ.pop("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", None)
            else:
                os.environ["PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR"] = previous_state_root
    return Runtime(load_config()), backend


def _compose_isolated_surface(state_root: Path, runtime: Any, backend: Any):
    from project_control.as1_surface import compose_surface
    from project_control.profiles import MCPProfile

    return compose_surface(runtime, MCPProfile.OBSERVER,
                           state_directory=state_root, backend=backend)


def _slot_identity(status: Mapping[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    slots = status.get("slots")
    if not isinstance(slots, list):
        raise QualificationError("supervisor status has no slot inventory")
    by_id = {item.get("slot_id"): item for item in slots if isinstance(item, dict)}
    selected_ids = list(dict.fromkeys(str(item.get("slot_id")) for item in receipts
                                      if isinstance(item.get("slot_id"), str)))
    selected = [by_id[slot_id] for slot_id in selected_ids if slot_id in by_id]
    for slot in selected:
        if (not isinstance(slot.get("model_id"), str) or
                not isinstance(slot.get("model_sha256"), str) or
                not isinstance(slot.get("server_pid"), int)):
            raise QualificationError("supervisor slot identity is incomplete")
        executable = Path(f"/proc/{slot['server_pid']}/exe").resolve(strict=True)
        slot["binary_path"] = str(executable)
        slot["binary_sha256"] = sha256_file(executable)
    model_ids = {slot["model_id"] for slot in selected}
    model_hashes = {slot["model_sha256"] for slot in selected}
    # Observer status does not expose the daemon's opaque compatibility key.
    # Compare its public startup settings and bind the omission into the report.
    compatibility = {(slot.get("compute_profile"), slot.get("parallelism")) for slot in selected}
    binary_hashes = {slot.get("binary_sha256") for slot in selected}
    return {"slots": selected, "two_distinct_slots": len(selected) >= 2,
            "same_model_id": len(model_ids) == 1 and bool(model_ids),
            "same_model_sha256": len(model_hashes) == 1 and bool(model_hashes),
            "same_compatibility_settings": len(compatibility) == 1 and
                all(value is not None for value in next(iter(compatibility), (None, None))),
            "same_binary_sha256": len(binary_hashes) == 1 and bool(binary_hashes),
            "model_id": next(iter(model_ids)) if len(model_ids) == 1 else None,
            "model_sha256": next(iter(model_hashes)) if len(model_hashes) == 1 else None,
            "compatibility_key": "not_exposed_by_observer_status",
            "binary_path": selected[0]["binary_path"] if selected else None,
            "binary_sha256": next(iter(binary_hashes)) if len(binary_hashes) == 1 else None}


def _fixture_source_locators(registry: Any, evidence: list[dict[str, Any]],
                             fixture_root: Path) -> list[dict[str, Any]]:
    fixture_root = fixture_root.resolve(strict=True)
    registered = []
    for project in sorted(registry.config.workspaces):
        workspace = registry.workspace(project)
        for alias in sorted(workspace.repositories):
            repository = registry.repository(project, alias)
            if fixture_root.is_relative_to(repository.root):
                registered.append((len(repository.root.parts), project, alias, repository.root))
    if not registered:
        raise QualificationError("fixture is outside every registered repository root")
    _, project, alias, repository_root = max(registered)
    locators = []
    for item in evidence:
        source_path = (fixture_root / item["path"]).resolve(strict=True)
        if not source_path.is_relative_to(repository_root):
            raise QualificationError("fixture source escaped its registered repository")
        locators.append({"project": project, "repository": alias,
                         "path": source_path.relative_to(repository_root).as_posix(),
                         "content_sha256": item["sha256"]})
    return locators


def _insert_fixture_packet(composition: Any, scope: Mapping[str, Any], case: Mapping[str, Any],
                           fixture_root: Path) -> tuple[str, str, list[dict[str, Any]]]:
    evidence = _source_packet(case, fixture_root)
    locators = _fixture_source_locators(composition.control.registry, evidence, fixture_root)
    text = "\n\n".join(f"SOURCE {item['path']} sha256={item['sha256']}\n{item['text']}"
                         for item in evidence)
    packet = composition.store.create(tool="read", access_scope=scope,
        payload={"text": text, "source_files": [{"path": item["path"],
                  "sha256": item["sha256"], "text": item["text"]} for item in evidence]},
        sources=locators, ttl_seconds=None)
    return packet.alias, packet.packet_id, evidence


def _inquire_to_terminal(composition: Any, *, case: Mapping[str, Any], scope: Mapping[str, Any],
                         hint_alias: str, request_id: str, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        result = composition.jobs.inquire(
            case["question"], access_scope=scope, hints=[hint_alias], request_id=request_id,
            foreground_timeout=min(30, max(0, deadline - time.monotonic())))
        if result.get("job") and result.get("status") in {"completed", "partial", "failed", "cancelled"}:
            return result
        if result.get("status") not in {"thinking", "busy"}:
            return result
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return {"status": "unavailable", "reason": "phase_deadline_exhausted"}


def _owned_receipts(jobs: Any) -> list[dict[str, Any]]:
    with jobs._db() as db:
        rows = db.execute("SELECT session_id,state,close_receipt FROM pa1_owned_resource_sessions "
                          "WHERE state <> 'superseded' ORDER BY updated,session_id").fetchall()
    receipts = []
    for row in rows:
        try:
            receipt = json.loads(row["close_receipt"]) if row["close_receipt"] else {}
        except (TypeError, json.JSONDecodeError):
            receipt = {}
        receipts.append({"session_id": row["session_id"], "state": row["state"], **receipt})
    return receipts


def _preflight_failure_report(report: dict[str, Any], error: Exception) -> dict[str, Any]:
    """Record a bounded pre-inquiry failure without implying model execution."""
    report["status"] = "preflight_failed"
    report["inference_performed"] = False
    report["inquiry_count"] = 0
    report["failure"] = {"class": type(error).__name__, "reason": str(error)[:300]}
    report["cleanup"] = {"isolated_job_service_started": False,
                         "isolated_cache_retained_for_review": True,
                         "central_supervisor_stopped": False}
    report["completed_unix"] = time.time()
    return report


def run_live(*, package_root: Path, source_root: Path, fixture_root: Path,
             artifact_root: Path, phase: str, case_set: Path | None,
             max_inquiries: int | None,
             wall_seconds: int | None, parallelism: str,
             expected_runtime_fingerprint: str | None = None,
             expected_model_id: str | None = None,
             expected_model_sha256: str | None = None,
             expected_binary_sha256: str | None = None,
             supervisor_state_root: Path | None = None) -> dict[str, Any]:
    cases, heldout = _load_cases(package_root, fixture_root, phase, case_set)
    budget = _effective_budget(phase, len(cases), max_inquiries, wall_seconds)
    if phase == "heldout":
        _validate_held_out_fixture(fixture_root, package_root)
    private_root = _private_artifact_root(artifact_root)
    state_root = private_root / "as1-state"
    if state_root.exists():
        raise QualificationError("isolated AS1 state already exists; use a new artifact root")
    source = qualify_source(source_root, fixture_root=fixture_root)
    started = time.monotonic()
    deadline = started + budget["wall_seconds"]
    report: dict[str, Any] = {
        "format": "pa1-live-source-qualification/1", "status": "preflight",
        "phase": phase, "adapter_tier": "public_as1_job_service",
        "execution_mode": "live", "inference_performed": False,
        "qualification": "model_evidence_only",
        "source": source.as_dict(), "package_root": str(package_root.resolve(strict=True)),
        "case_contract_sha256": sha256_file(package_root / "machine/eval-cases.json"),
        "policy_sha256": sha256_file(package_root / "machine/evaluation-policy.json"),
        "budget": budget, "fixture_root": str(fixture_root.resolve(strict=True)),
        "held_out": heldout, "cases": [], "isolated_state_root": str(state_root),
        "inquiry_count": 0,
        "gaps": ["model_turn_usage_not_exposed_by_public_job_result",
                 "prefill_and_reasoning_token_counts_not_exposed",
                 "two_preparation_inquiries_not_run", "request_scratch_not_applied"],
    }
    _atomic_json(private_root / "report.json", report)
    composition = None
    try:
        # Query canonical owner metadata before constructing or starting any
        # isolated AS1 service. central_status is read-only and never starts a daemon.
        runtime, backend = _isolated_composition(state_root, supervisor_state_root)
        report["runtime"] = {
            "root": str(runtime.root), "source_root": runtime.source_root,
            "source_commit": runtime.source_commit,
            "manifest_sha256": runtime.manifest_sha256,
            "fingerprint": runtime.fingerprint,
            "file_count": runtime.file_count,
            "configured_supervisor_state_root": str(backend._state_root),
        }
        status = backend.central_status(deadline_epoch=time.time() + 5)
        report["runtime"].update({
            "supervisor_runtime_fingerprint": status.get("runtime_fingerprint"),
            "supervisor_source_sha256": status.get("source_sha256"),
            "supervisor_pid": status.get("supervisor_pid"),
            "service_state_root": status.get("service_state_root"),
        })
        if expected_runtime_fingerprint and status.get("runtime_fingerprint") != expected_runtime_fingerprint:
            raise QualificationError("supervisor runtime fingerprint differs from the required live identity")
        composition = _compose_isolated_surface(state_root, runtime, backend)
        scope = composition.scope(None)
        composition.jobs.start()
        report["status"] = "running"
        report["cleanup"] = {"isolated_job_service_started": True}
        packets = []
        for case in cases:
            alias, packet_id, evidence = _insert_fixture_packet(composition, scope, case, fixture_root)
            packets.append({"case": case, "alias": alias, "packet_id": packet_id,
                            "evidence": evidence})
        report["started_unix"] = time.time()
        _atomic_json(private_root / "report.json", report)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import uuid

        request_rows = {}
        for item in packets:
            request_material = {"question": item["case"]["question"],
                                "scope": scope, "hint_packet_id": item["packet_id"],
                                "hint_alias": item["alias"], "mode": "investigate"}
            request_rows[item["case"]["id"]] = {
                "request_sha256": _json_hash(request_material),
                "request_id": "pa1-live-" + uuid.uuid4().hex,
                "started": time.monotonic(),
            }

        report["inference_performed"] = True
        results = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pa1-live-case") as pool:
            futures = {
                pool.submit(_inquire_to_terminal, composition, case=item["case"], scope=scope,
                            hint_alias=item["alias"], request_id=request_rows[item["case"]["id"]]["request_id"],
                            deadline=deadline): item
                for item in packets
            }
            for future in as_completed(futures, timeout=max(1, budget["wall_seconds"])):
                item = futures[future]
                case = item["case"]
                try:
                    result = future.result()
                    results[case["id"]] = result
                except Exception as error:
                    results[case["id"]] = {"status": "unavailable",
                                            "reason": f"{type(error).__name__}:{error}"[:300]}

        failures: dict[str, int] = {}
        for item in packets:
            case = item["case"]
            result = results.get(case["id"], {"status": "not_run_budget_exhausted"})
            job = result.get("job") if isinstance(result.get("job"), dict) else {}
            row: dict[str, Any] = {
                "case_id": case["id"], "status": result.get("status", "failed"),
                "request_sha256": request_rows[case["id"]]["request_sha256"],
                "request_id": request_rows[case["id"]]["request_id"],
                "elapsed_seconds": round(time.monotonic() - request_rows[case["id"]]["started"], 4),
                "job_id": job.get("job_id"), "attempt": job.get("attempt"),
                "result_packet": job.get("result_packet"),
                "evidence_packets": job.get("evidence_packets", []),
                "observations": result.get("observations", []),
                "usage": "not_exposed_by_public_job_result",
                "reasoning_tokens": "not_exposed_by_public_job_result",
                "prefill_time": "not_exposed_by_public_job_result",
            }
            result_ref = job.get("result_packet")
            result_packet = composition.store.lookup(result_ref, access_scope=scope) if result_ref else None
            payload = result_packet.packet.payload if result_packet and result_packet.status == "ok" else {}
            row["answer"] = payload.get("answer", job.get("answer"))
            row["findings"] = payload.get("findings", job.get("findings", []))
            row["unresolved_questions"] = payload.get("unresolved_questions", job.get("unresolved_questions", []))
            refs = {ref for finding in row["findings"] if isinstance(finding, dict)
                    for ref in finding.get("evidence_packets", []) if isinstance(ref, str)}
            if item["packet_id"] in refs:
                row["broker_cited_fixture_sources"] = [
                    {"path": source["path"], "sha256": source["sha256"],
                     "excerpt": source["text"], "source_packet_id": item["packet_id"]}
                    for source in item["evidence"]]
            if result.get("status") in {"completed", "partial"} and isinstance(row["answer"], str):
                citations = [{"path": cite["path"], "sha256": cite["sha256"], "excerpt": cite["excerpt"]}
                             for cite in row.get("broker_cited_fixture_sources", [])]
                row["score"] = score_case(case, {"answer": row["answer"], "citations": citations}, fixture_root)
            else:
                failure = str(result.get("reason") or result.get("status") or "incomplete")
                row["failure_class"] = failure[:180]
                failures[failure] = failures.get(failure, 0) + 1
            report["cases"].append(row)
        if any(count >= 2 for count in failures.values()):
            report["stop_reason"] = "two_same_class_failures"

        receipts = _owned_receipts(composition.jobs)
        report["owned_sessions"] = [{key: row.get(key) for key in
            ("session_id", "state", "daemon_epoch", "slot_id", "owner_id", "server_pid",
             "server_process_start", "gpu_uuids", "residency_generation")}
            for row in receipts]
        provider_client = backend._get_backend()
        supervisor_status = provider_client.observer_status(deadline_epoch=time.time() + 5)
        observed_slots = _slot_identity(supervisor_status, receipts)
        report["runtime"]["supervisor_runtime_fingerprint"] = supervisor_status.get("runtime_fingerprint")
        report["runtime"]["supervisor_source_sha256"] = supervisor_status.get("source_sha256")
        report["model"] = observed_slots
        if expected_model_id and observed_slots.get("model_id") != expected_model_id:
            raise QualificationError("observed model ID differs from the required live identity")
        if expected_model_sha256 and observed_slots.get("model_sha256") != expected_model_sha256:
            raise QualificationError("observed model weight hash differs from the required live identity")
        if expected_binary_sha256 and observed_slots.get("binary_sha256") != expected_binary_sha256:
            raise QualificationError("observed server binary hash differs from the required live identity")
        report["two_interchangeable_slots_observed"] = bool(
            observed_slots.get("two_distinct_slots") and observed_slots.get("same_model_sha256") and
            observed_slots.get("same_compatibility_settings") and observed_slots.get("same_binary_sha256"))
        if not report["two_interchangeable_slots_observed"]:
            report["gaps"].append("two_interchangeable_slots_not_proven")
        report["elapsed_seconds"] = round(time.monotonic() - started, 4)
        report["inquiry_count"] = len([row for row in report["cases"] if row.get("job_id")])
        report["status"] = ("completed_with_gaps" if report["inquiry_count"] and
                            all(row.get("status") in {"completed", "partial"} for row in report["cases"])
                            else "partial")
        report["completed_unix"] = time.time()
    except Exception as error:
        if composition is None:
            _preflight_failure_report(report, error)
        else:
            report["status"] = "partial"
            report["failure"] = {"class": type(error).__name__, "reason": str(error)[:300]}
            report["completed_unix"] = time.time()
    finally:
        if composition is not None:
            stopped = composition.close()
            report["cleanup"] = {"isolated_job_service_started": True,
                                 "isolated_job_service_stopped": bool(stopped),
                                 "isolated_cache_retained_for_review": True,
                                 "central_supervisor_stopped": False}
            if report["cleanup"]["isolated_job_service_stopped"] is not True:
                report["status"] = "partial_cleanup_pending"
        report["inference_performed"] = bool(report.get("inference_performed"))
        _atomic_json(private_root / "report.json", report)
    return report


def source_plan(package_root: Path, source_root: Path, fixture_root: Path) -> dict[str, Any]:
    contract, _ = load_eval_contract(package_root)
    source = qualify_source(source_root, fixture_root=fixture_root)
    return {
        "format": "pa1-live-source-qualification-plan/1", "status": "planned",
        "inference_performed": False, "source": source.as_dict(),
        "case_ids": list(INITIAL_IDS), "initial_budget": INITIAL_BUDGET,
        "held_out_budget": HELD_OUT_BUDGET,
        "counterexample_case_id": "A31", "continuation_case_id": "A37",
        "case_contract_sha256": sha256_file(package_root / "machine/eval-cases.json"),
        "evidence_case_count": len(contract.get("cases", [])),
        "required_live_inputs": ["explicit live execution flag",
                                  "root-owned resource interlock",
                                  "new private artifact root"],
        "status_gaps": ["live_identity_not_observed", "model_calls_not_run",
                        "public_as1_job_journey_not_run"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=REPO / "planning/project-assistance-v1")
    parser.add_argument("--source", type=Path, default=REPO / "src/project_control")
    parser.add_argument("--fixture", type=Path,
                        default=REPO / "planning/project-assistance-v1/fixtures/repository")
    parser.add_argument("--phase", choices=("baseline", "heldout", "extension"), default="baseline")
    parser.add_argument("--case-set", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--parallelism", choices=("default", "layer", "tensor"), default="default")
    parser.add_argument("--max-inquiries", type=int)
    parser.add_argument("--wall-seconds", type=int)
    parser.add_argument("--expect-runtime-fingerprint")
    parser.add_argument("--expect-model-id")
    parser.add_argument("--expect-model-sha256")
    parser.add_argument("--expect-binary-sha256")
    parser.add_argument("--supervisor-state-root", type=Path,
                        help="explicit existing canonical supervisor service-state root")
    parser.add_argument("--execute-live", action="store_true",
                        help="explicitly run the bounded live source cases")
    parser.add_argument("--output", type=Path, help="write the inert plan or final report here")
    args = parser.parse_args(argv)
    package_root = args.package.resolve(strict=True)
    source_root = args.source.resolve(strict=True)
    fixture_root = args.fixture.resolve(strict=True)
    if not args.execute_live:
        report = source_plan(package_root, source_root, fixture_root)
    else:
        if args.artifact_root is None:
            raise QualificationError("live execution requires a caller-provided private --artifact-root")
        report = run_live(package_root=package_root, source_root=source_root,
                          fixture_root=fixture_root, artifact_root=args.artifact_root,
                          phase=args.phase, case_set=args.case_set,
                          max_inquiries=args.max_inquiries,
                          wall_seconds=args.wall_seconds, parallelism=args.parallelism,
                          expected_runtime_fingerprint=args.expect_runtime_fingerprint,
                          expected_model_id=args.expect_model_id,
                          expected_model_sha256=args.expect_model_sha256,
                          expected_binary_sha256=args.expect_binary_sha256,
                          supervisor_state_root=args.supervisor_state_root)
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        if args.execute_live:
            output_path = args.output.resolve()
            artifact_path = Path(args.artifact_root).resolve()
            if not output_path.is_relative_to(artifact_path):
                raise QualificationError("live report copies must stay inside the private artifact root")
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.output.write_text(encoded, encoding="utf-8")
        args.output.chmod(0o600)
    else:
        sys.stdout.write(encoded)
    return 2 if args.execute_live and report.get("status") == "preflight_failed" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
