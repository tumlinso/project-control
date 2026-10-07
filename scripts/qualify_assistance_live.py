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
import re
import stat
import sys
import threading
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
from project_control.as1_contracts import canonical_digest  # noqa: E402


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


def _isolated_composition(state_root: Path, supervisor_state_root: Path | None = None,
                          private_fixture_root: Path | None = None):
    from project_control.app import Runtime
    from project_control.config import load_config
    from project_control.observer_analysis import SkillsObserverAnalysisProvider
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
    config = load_config()
    if private_fixture_root is not None:
        config = _ephemeral_fixture_config(config, private_fixture_root)
    return identity, Runtime(config), backend


def _ephemeral_fixture_config(config: Any, fixture_root: Path):
    """Add a private fixture alias under the existing authoritative workspace."""
    from project_control.config import RepositoryConfig

    fixture_root = fixture_root.resolve(strict=True)
    if not fixture_root.is_dir():
        raise QualificationError("held-out fixture root is not a directory")
    private_config = config.model_copy(deep=True)
    project = "project-control"
    if project not in private_config.workspaces:
        raise QualificationError("held-out fixture requires the existing project-control authority")
    workspace = private_config.workspaces[project]
    alias = "pa1-heldout-fixture"
    if alias in workspace.repositories:
        raise QualificationError("temporary held-out repository alias already exists")
    workspace.repositories[alias] = RepositoryConfig(root=fixture_root)
    return private_config


def _compose_isolated_surface(state_root: Path, runtime: Any, backend: Any):
    from project_control.as1_surface import compose_surface
    from project_control.profiles import MCPProfile

    return compose_surface(runtime, MCPProfile.OBSERVER,
                           state_directory=state_root, backend=backend)


def _runtime_report(identity: Any, backend: Any, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    report = {
        "root": str(identity.root), "source_root": identity.source_root,
        "source_commit": identity.source_commit,
        "manifest_sha256": identity.manifest_sha256,
        "fingerprint": identity.fingerprint,
        "file_count": identity.file_count,
        "configured_supervisor_state_root": str(backend._state_root),
    }
    if status is not None:
        report.update({
            "supervisor_runtime_fingerprint": status.get("runtime_fingerprint"),
            "supervisor_source_sha256": status.get("source_sha256"),
            "supervisor_pid": status.get("supervisor_pid"),
            "service_state_root": status.get("service_state_root"),
        })
    return report


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


def _fixture_repository(registry: Any, fixture_root: Path) -> tuple[str, str, Path]:
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
    return project, alias, repository_root


def _fixture_source_locators(registry: Any, evidence: list[dict[str, Any]],
                             fixture_root: Path) -> list[dict[str, Any]]:
    fixture_root = fixture_root.resolve(strict=True)
    project, alias, repository_root = _fixture_repository(registry, fixture_root)
    locators = []
    for item in evidence:
        source_path = (fixture_root / item["path"]).resolve(strict=True)
        if not source_path.is_relative_to(repository_root):
            raise QualificationError("fixture source escaped its registered repository")
        locators.append({"project": project, "repository": alias,
                         "path": source_path.relative_to(repository_root).as_posix(),
                         "content_sha256": item["sha256"]})
    return locators


def _insert_fixture_packet(composition: Any, case: Mapping[str, Any],
                           fixture_root: Path) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    evidence = _source_packet(case, fixture_root)
    project, repository, repository_root = _fixture_repository(
        composition.control.registry, fixture_root)
    case_scope = composition.scope(project)
    packet_ids = []
    for item in evidence:
        source_path = (fixture_root / item["path"]).resolve(strict=True)
        if not source_path.is_relative_to(repository_root):
            raise QualificationError("fixture source escaped its registered repository")
        relative_path = source_path.relative_to(repository_root).as_posix()
        response = composition.information.call(
            "read", project=project, repository=repository,
            paths=[relative_path], detail="standard")
        packet_id = response.get("packet") if isinstance(response, dict) else None
        if response.get("status") != "ok" or not isinstance(packet_id, str):
            raise QualificationError(f"public source read failed for {item['path']}")
        packet_result = composition.store.lookup(packet_id, access_scope=case_scope)
        if packet_result.status != "ok" or packet_result.packet is None:
            raise QualificationError(f"public source read packet is unavailable for {item['path']}")
        packet = packet_result.packet
        if (len(packet.sources) != 1 or packet.sources[0].project != project
                or packet.sources[0].repository != repository
                or packet.sources[0].path != relative_path
                or packet.sources[0].content_sha256 != item["sha256"]):
            raise QualificationError(f"public source read identity mismatch for {item['path']}")
        packet_ids.append(packet.packet_id)
    return case_scope, packet_ids, evidence


def _assert_source_packet_freshness(composition: Any, scope: Mapping[str, Any],
                                   packet_ids: list[str]) -> dict[str, Any]:
    """Require source hints to pass the production freshness verifier pre-inquiry."""
    if not packet_ids:
        raise QualificationError("source case has no public read packets")
    probe = {
        "scope": dict(scope), "mode": "investigate", "hints": list(packet_ids),
        "evidence_packets": list(packet_ids), "findings": [
            {"text": "source freshness probe", "evidence_packets": list(packet_ids)}],
        # The verifier only checks result existence when source citations exist;
        # reuse the actual read packet instead of inventing a result artifact.
        "result_packet": packet_ids[0],
    }
    result = composition.jobs.freshness_provider(probe)
    if not isinstance(result, dict) or result.get("fresh") is not True:
        changes = result.get("changed_sources", []) if isinstance(result, dict) else []
        compact = [{key: item.get(key) for key in ("reference", "path", "reason", "dependency")
                    if key in item} for item in changes[:8] if isinstance(item, dict)]
        raise QualificationError("public source read packets fail current freshness verification: "
                                 + json.dumps(compact, sort_keys=True)[:500])
    return result


def _resolve_finding_citations(composition: Any, packet_ids: list[str],
                               scope: Mapping[str, Any], fixture_root: Path
                               ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Resolve only finding-cited broker direct_cat packets to current fixture bytes."""
    root = fixture_root.resolve(strict=True)
    citations: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    seen_packets: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    for packet_id in packet_ids:
        if not isinstance(packet_id, str) or packet_id in seen_packets:
            continue
        seen_packets.add(packet_id)
        lookup = composition.store.lookup(packet_id, access_scope=scope)
        record: dict[str, Any] = {"packet_id": packet_id, "status": "rejected"}
        if lookup.status != "ok" or lookup.packet is None:
            record["reason"] = f"scoped_lookup_{lookup.status}"
            records.append(record)
            continue
        packet = lookup.packet
        payload = packet.payload
        reads = payload.get("source_reads") if packet.tool == "command" else None
        if (payload.get("status") != "completed" or payload.get("exit_code") != 0
                or payload.get("truncated") is True or payload.get("timed_out") is True):
            record["reason"] = "command_not_completed_cleanly"
            records.append(record)
            continue
        if not isinstance(reads, list) or len(reads) != 1:
            record["reason"] = "source_read_count_not_one"
            records.append(record)
            continue
        read = reads[0]
        if not isinstance(read, dict) or read.get("method") != "direct_cat":
            record["reason"] = "source_read_not_direct_cat"
            records.append(record)
            continue
        raw_path, expected_hash, stdout = read.get("path"), read.get("content_sha256"), payload.get("stdout")
        if (not isinstance(raw_path, str) or not Path(raw_path).is_absolute()
                or not isinstance(expected_hash, str) or len(expected_hash) != 64
                or not isinstance(stdout, str)):
            record["reason"] = "source_read_identity_incomplete"
            records.append(record)
            continue
        try:
            source_path = Path(raw_path).resolve(strict=True)
            if not source_path.is_file() or not source_path.is_relative_to(root):
                raise ValueError("source is outside fixture")
            source_bytes = source_path.read_bytes()
            source_text = source_bytes.decode("utf-8", errors="strict")
        except (OSError, UnicodeError, ValueError):
            record["reason"] = "source_path_unavailable_or_outside_fixture"
            records.append(record)
            continue
        actual_hash = hashlib.sha256(source_bytes).hexdigest()
        try:
            stdout_bytes = stdout.encode("utf-8", errors="strict")
        except UnicodeError:
            record["reason"] = "command_stdout_not_utf8"
            records.append(record)
            continue
        if actual_hash != expected_hash or stdout_bytes != source_bytes:
            record["reason"] = "source_bytes_hash_or_stdout_mismatch"
            records.append(record)
            continue
        relative_path = source_path.relative_to(root).as_posix()
        record.update({"status": "resolved", "path": relative_path,
                       "sha256": actual_hash, "method": "broker_direct_cat_verified"})
        records.append(record)
        citation_key = (relative_path, actual_hash)
        if citation_key not in seen_sources:
            citations.append({"path": relative_path, "sha256": actual_hash,
                              "excerpt": source_text})
            seen_sources.add(citation_key)
    return citations, records


def _finding_packet_ids(findings: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict) and isinstance(finding.get("evidence_packets"), list):
                refs.extend(ref for ref in finding["evidence_packets"] if isinstance(ref, str))
    return list(dict.fromkeys(refs))


def _extension_answer_errors(case_id: Any, answer: str) -> list[str]:
    if case_id == "A31" and not re.search(r"```python\s*.*?```", answer, flags=re.DOTALL):
        return ["required runnable Python code block missing"]
    return []


def _lookup_inquiry_job_linkage(composition: Any, *, case: Mapping[str, Any],
                                scope: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve a public inquiry to its isolated durable job without reading content.

    ``inquire`` intentionally returns only ``analysis_unavailable`` for a
    negative terminal result. The public lookup requires a job ID, so compute
    the exact canonical inquiry key and read its isolated index before using
    the service's scoped public lookup. This is read-only and reveals only a
    bounded identifier/status/classification tuple.
    """
    jobs = composition.jobs
    identity = canonical_digest({
        "question": case["question"],
        "context": jobs.inquiry_context(scope, jobs.analysis_runtime_identity),
        "mode": "investigate", "skill": None,
    })
    with jobs._db() as db:
        row = db.execute("SELECT jobs.id FROM inquiry_index JOIN jobs ON jobs.id=inquiry_index.job "
                         "WHERE inquiry_index.identity=?", (identity,)).fetchone()
    if row is None:
        return None
    job_id = row["id"]
    snapshot = jobs.lookup(job_id, access_scope=scope)
    if snapshot.get("status") != "ok" or not isinstance(snapshot.get("job"), dict):
        return {"lookup_method": "isolated_inquiry_index_then_scoped_lookup",
                "lookup_status": snapshot.get("status"), "job_id": job_id}
    job = snapshot["job"]
    answer = job.get("answer")
    findings = job.get("findings")
    result_ref = job.get("result_packet")
    if isinstance(result_ref, str):
        result = composition.store.lookup(result_ref, access_scope=scope)
        if result.status == "ok" and result.packet is not None:
            payload = result.packet.payload
            if answer is None:
                answer = payload.get("answer")
            if findings is None:
                findings = payload.get("findings")
    answer_observed = isinstance(answer, str) and bool(answer.strip())
    finding_count = len(findings) if isinstance(findings, list) else 0
    output_observed = answer_observed or finding_count > 0
    diagnostic = None
    if job.get("status") in {"completed", "partial", "failed", "cancelled"}:
        diagnostic = next((item for item in jobs.inquiry_failure_diagnostics(limit=50)
                           if item.get("job_id") == job_id), None)
    return {
        "lookup_method": "isolated_inquiry_index_then_scoped_lookup",
        "lookup_status": "ok", "job_id": job_id,
        "job_status": job.get("status"), "attempt": job.get("attempt"),
        "failure_class": diagnostic.get("failure_class") if diagnostic else None,
        "private_job_answer_observed": answer_observed,
        "private_job_findings_count": finding_count,
        "actual_inference_confirmed": (job.get("status") in {"completed", "partial"}
                                        and output_observed),
        "actual_inference_evidence": "isolated_scoped_job_answer_or_findings" if output_observed else None,
    }


def _inquire_to_terminal(composition: Any, *, case: Mapping[str, Any], scope: Mapping[str, Any],
                         hint_aliases: list[str], deadline: float,
                         on_attempt: Any | None = None) -> dict[str, Any]:
    attempted = False
    linkage = None
    last_result: dict[str, Any] = {"status": "unavailable", "reason": "phase_deadline_exhausted"}
    while time.monotonic() < deadline:
        if not attempted:
            attempted = True
            if on_attempt is not None:
                on_attempt()
        try:
            last_result = composition.jobs.inquire(
                case["question"], access_scope=scope, hints=hint_aliases,
                foreground_timeout=min(30, max(0, deadline - time.monotonic())))
        except Exception as error:
            last_result = {"status": "unavailable",
                           "reason": f"{type(error).__name__}:{error}"[:300]}
        try:
            linkage = _lookup_inquiry_job_linkage(composition, case=case, scope=scope) or linkage
        except Exception as error:
            # Keep the failed correlation explicit; never turn a missing lookup
            # into an invented job identifier.
            linkage = {"lookup_method": "isolated_inquiry_index_then_scoped_lookup",
                       "lookup_status": f"{type(error).__name__}"[:80]}
        if last_result.get("job") and last_result.get("status") in {"completed", "partial", "failed", "cancelled"}:
            return {"public_result": last_result, "job_linkage": linkage}
        if last_result.get("status") not in {"thinking", "busy"}:
            return {"public_result": last_result, "job_linkage": linkage}
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    return {"public_result": {"status": "unavailable", "reason": "phase_deadline_exhausted"},
            "job_linkage": linkage}


def _visible_inference_confirmed(status: str, answer: Any, findings: Any) -> bool:
    """Count only a visible successful answer/findings, never an attempted call."""
    return (status in {"completed", "partial"}
            and (isinstance(answer, str) and bool(answer.strip())
                 or isinstance(findings, list) and bool(findings)))


def _same_failure_stop(failures: Mapping[str, int]) -> bool:
    return any(count >= 2 for count in failures.values())


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


def rescore_existing_report(report_path: Path, *, output_path: Path | None = None,
                           case_set_path: Path | None = None) -> tuple[dict[str, Any], Path]:
    """Rescore a terminal live report from its existing public result and packet store."""
    report_path = report_path.expanduser().resolve(strict=True)
    if not report_path.is_file() or report_path.is_symlink():
        raise QualificationError("rescore input must be a regular report file")
    artifact_root = report_path.parent.resolve(strict=True)
    root_info = artifact_root.stat()
    if root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
        raise QualificationError("rescore artifact directory must be private and user-owned")
    try:
        source_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"cannot read original report: {error}") from error
    if not isinstance(source_report, dict) or source_report.get("format") != "pa1-live-source-qualification/1":
        raise QualificationError("rescore input is not a live source qualification report")
    if source_report.get("status") == "running":
        raise QualificationError("cannot rescore while the isolated job service is running")
    cleanup = source_report.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("isolated_job_service_stopped") is not True:
        raise QualificationError("rescore requires a terminal report with isolated job service stopped")
    state_root_value = source_report.get("isolated_state_root")
    if not isinstance(state_root_value, str):
        raise QualificationError("original report has no isolated state root")
    state_root = Path(state_root_value).expanduser().resolve(strict=True)
    if state_root != (artifact_root / "as1-state").resolve(strict=True):
        raise QualificationError("isolated packet store is not the report's private state directory")
    fixture_value = source_report.get("fixture_root")
    package_value = source_report.get("package_root")
    if not isinstance(fixture_value, str) or not isinstance(package_value, str):
        raise QualificationError("original report is missing fixture or evaluation contract identity")
    fixture_root = Path(fixture_value).expanduser().resolve(strict=True)
    package_root = Path(package_value).expanduser().resolve(strict=True)
    contract, _ = load_eval_contract(package_root)
    cases = {item["id"]: item for item in contract["cases"]}
    case_set_sha256 = None
    case_set_identity_status = "pinned_evaluation_contract"
    if source_report.get("phase") == "extension":
        if case_set_path is None:
            case_set_path = REPO / "docs/pa1/live-extension-cases.json"
        case_set_path = case_set_path.expanduser().resolve(strict=True)
        try:
            extension_payload = json.loads(case_set_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise QualificationError(f"cannot read extension rescore case set: {error}") from error
        extension_cases = extension_payload.get("cases") if isinstance(extension_payload, dict) else None
        if not isinstance(extension_cases, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not isinstance(item.get("evidence_paths"), list)
                for item in extension_cases):
            raise QualificationError("extension rescore case set is malformed")
        extension_by_id = {item["id"]: item for item in extension_cases}
        report_ids = {row.get("case_id") for row in source_report.get("cases", [])
                      if isinstance(row, dict)}
        if report_ids - extension_by_id.keys():
            raise QualificationError("extension rescore case set does not cover every report case")
        cases.update(extension_by_id)
        case_set_sha256 = sha256_file(case_set_path)
        recorded_set = source_report.get("extension_case_set")
        case_set_identity_status = (
            "bound_by_original_report" if isinstance(recorded_set, dict)
            and recorded_set.get("sha256") == case_set_sha256 else
            "current_rescore_case_set_not_bound_to_original_report")
    elif case_set_path is not None:
        raise QualificationError("--rescore-case-set only applies to extension reports")

    from project_control.app import Runtime
    from project_control.config import load_config

    class NoInferenceBackend:
        available = False

        def __init__(self):
            self._state_root = Path("/")

        def close(self):
            return None

    config = load_config()
    if source_report.get("held_out") is True:
        config = _ephemeral_fixture_config(config, fixture_root)
    composition = _compose_isolated_surface(state_root, Runtime(config), NoInferenceBackend())
    try:
        project = None
        workspace_meta = source_report.get("fixture_workspace")
        if isinstance(workspace_meta, dict) and isinstance(workspace_meta.get("project"), str):
            project = workspace_meta["project"]
        if project is None:
            project, _, _ = _fixture_repository(composition.control.registry, fixture_root)
        scope = composition.scope(project)
        rescored_cases = []
        for row in source_report.get("cases", []):
            if not isinstance(row, dict):
                continue
            case_id = row.get("case_id")
            case = cases.get(case_id)
            answer = row.get("answer")
            if case is None or not isinstance(answer, str) or not answer.strip():
                rescored_cases.append({"case_id": case_id, "status": "not_rescored",
                                       "reason": "no_public_visible_answer_or_contract_case",
                                       "original_score": row.get("score")})
                continue
            findings = row.get("findings", [])
            citations, resolution = _resolve_finding_citations(
                composition, _finding_packet_ids(findings), scope, fixture_root)
            score = score_case(case, {"answer": answer, "citations": citations}, fixture_root)
            extension_errors = _extension_answer_errors(case_id, answer)
            if extension_errors:
                score["citation_errors"].extend(extension_errors)
                score["status"] = "failed"
                score["qualified"] = False
                score["failure_layer"] = "validation"
            rescored_cases.append({"case_id": case_id, "public_status": row.get("status"),
                                   "original_score": row.get("score"), "rescore": score,
                                   "citation_packet_resolution": resolution,
                                   "derived_citations": citations})
        receipt = {
            "format": "pa1-live-source-rescore/1", "status": "rescored",
            "execution_mode": "existing_public_result_and_scoped_packet_store",
            "model_calls": 0, "original_report": str(report_path),
            "original_report_sha256": sha256_file(report_path),
            "fixture_root": str(fixture_root), "isolated_state_root": str(state_root),
            "scope_project": project, "cases": rescored_cases,
            "case_set_sha256": case_set_sha256,
            "case_set_identity_status": case_set_identity_status,
            "limits": ["only finding-cited packet IDs considered",
                       "only clean single-file broker direct_cat reads accepted",
                       "current fixture bytes must equal broker stdout and source hash",
                       "no original report fields overwritten"],
            "completed_unix": time.time(),
        }
    finally:
        composition.close()

    if output_path is None:
        output_path = artifact_root / "rescore-report.json"
    output_path = output_path.expanduser().resolve()
    if output_path == report_path or not output_path.is_relative_to(artifact_root):
        raise QualificationError("rescore receipt must be a separate file inside the original private artifact root")
    if output_path.exists() or output_path.is_symlink():
        raise QualificationError("rescore receipt already exists; choose a new output path")
    return receipt, output_path


def _preflight_failure_report(report: dict[str, Any], error: Exception) -> dict[str, Any]:
    """Record a bounded pre-inquiry failure without implying model execution."""
    report["status"] = "preflight_failed"
    report["inference_performed"] = False
    report["inquiry_count"] = 0
    report["inquiries_attempted"] = 0
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
        "inquiry_count": 0, "inquiries_attempted": 0, "jobs_identified": 0,
        "inquiry_count_semantics": "distinct public inquiry calls attempted",
        "inference_performed_semantics": "visible successful answer or findings observed",
        "confirmed_inference_count": 0,
        "actual_inference_confirmed": False,
        "actual_inference_count": 0,
        "actual_inference_case_ids": [],
        "actual_inference_confirmation_source": "isolated_scoped_job_answer_or_findings; content not copied",
        "model_turn_count": "not_exposed_by_public_job_result",
        "gaps": ["model_turn_usage_not_exposed_by_public_job_result",
                 "prefill_and_reasoning_token_counts_not_exposed",
                 "two_preparation_inquiries_not_run", "request_scratch_not_applied"],
    }
    if phase == "extension" and case_set is not None:
        report["extension_case_set"] = {
            "path": str(case_set.resolve(strict=True)),
            "sha256": sha256_file(case_set),
        }
    _atomic_json(private_root / "report.json", report)
    composition = None
    job_service_started = False
    try:
        # Query canonical owner metadata before constructing or starting any
        # isolated AS1 service. central_status is read-only and never starts a daemon.
        identity, runtime, backend = _isolated_composition(
            state_root, supervisor_state_root,
            private_fixture_root=fixture_root if phase == "heldout" else None)
        report["runtime"] = _runtime_report(identity, backend)
        if phase == "heldout":
            report["fixture_workspace"] = {
                "project": "project-control", "repository": "pa1-heldout-fixture",
                "root": str(fixture_root.resolve(strict=True)),
                "registration": "in_memory_only_under_existing_authority",
            }
        status = backend.central_status(deadline_epoch=time.time() + 5)
        report["runtime"] = _runtime_report(identity, backend, status)
        if expected_runtime_fingerprint and status.get("runtime_fingerprint") != expected_runtime_fingerprint:
            raise QualificationError("supervisor runtime fingerprint differs from the required live identity")
        composition = _compose_isolated_surface(state_root, runtime, backend)
        packets = []
        for case in cases:
            case_scope, packet_ids, evidence = _insert_fixture_packet(composition, case, fixture_root)
            source_freshness = _assert_source_packet_freshness(composition, case_scope, packet_ids)
            packets.append({"case": case, "scope": case_scope, "packet_ids": packet_ids,
                            "evidence": evidence, "source_freshness": source_freshness})
        composition.jobs.start()
        job_service_started = True
        report["status"] = "running"
        report["cleanup"] = {"isolated_job_service_started": True}
        report["started_unix"] = time.time()
        _atomic_json(private_root / "report.json", report)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import uuid

        request_rows = {}
        for item in packets:
            request_material = {"question": item["case"]["question"],
                                "scope": item["scope"], "hint_packet_ids": item["packet_ids"],
                                "mode": "investigate"}
            request_rows[item["case"]["id"]] = {
                "request_sha256": _json_hash(request_material),
                "attempt_id": "pa1-live-" + uuid.uuid4().hex,
                "started": time.monotonic(),
            }

        progress_lock = threading.Lock()
        attempted_cases: set[str] = set()

        def mark_attempt(case_id: str) -> None:
            with progress_lock:
                attempted_cases.add(case_id)
                report["inquiries_attempted"] = len(attempted_cases)
                report["inquiry_count"] = len(attempted_cases)
                report["attempted_case_ids"] = sorted(attempted_cases)
                _atomic_json(private_root / "report.json", report)

        results = {}
        failures: dict[str, int] = {}
        stop_dispatch = False
        for offset in range(0, len(packets), INITIAL_BUDGET["max_parallel_executions"]):
            wave = packets[offset:offset + INITIAL_BUDGET["max_parallel_executions"]]
            with ThreadPoolExecutor(max_workers=INITIAL_BUDGET["max_parallel_executions"],
                                    thread_name_prefix="pa1-live-case") as pool:
                futures = {
                    pool.submit(_inquire_to_terminal, composition, case=item["case"], scope=item["scope"],
                                hint_aliases=item["packet_ids"], deadline=deadline,
                                on_attempt=lambda ident=item["case"]["id"]: mark_attempt(ident)): item
                    for item in wave
                }
                try:
                    for future in as_completed(futures, timeout=max(1, deadline - time.monotonic())):
                        item = futures[future]
                        case = item["case"]
                        try:
                            results[case["id"]] = future.result()
                        except Exception as error:
                            results[case["id"]] = {
                                "public_result": {"status": "unavailable",
                                                  "reason": f"{type(error).__name__}:{error}"[:300]},
                                "job_linkage": None,
                            }
                except TimeoutError:
                    for future, item in futures.items():
                        if not future.done():
                            future.cancel()
                            results[item["case"]["id"]] = {
                                "public_result": {"status": "unavailable",
                                                  "reason": "phase_deadline_exhausted"},
                                "job_linkage": None,
                            }

            for item in wave:
                case = item["case"]
                envelope = results.get(case["id"], {
                    "public_result": {"status": "unavailable", "reason": "not_run_budget_exhausted"},
                    "job_linkage": None,
                })
                result = envelope.get("public_result", {})
                linkage = envelope.get("job_linkage") if isinstance(envelope.get("job_linkage"), dict) else {}
                if not linkage.get("job_id"):
                    try:
                        linkage = _lookup_inquiry_job_linkage(
                            composition, case=case, scope=item["scope"]) or linkage
                    except Exception as error:
                        linkage = {**linkage,
                                   "lookup_method": "isolated_inquiry_index_then_scoped_lookup",
                                   "lookup_status": f"{type(error).__name__}"[:80]}
                job = result.get("job") if isinstance(result.get("job"), dict) else {}
                row: dict[str, Any] = {
                    "case_id": case["id"], "status": result.get("status", "failed"),
                    "attempt_id": request_rows[case["id"]]["attempt_id"],
                    "request_sha256": request_rows[case["id"]]["request_sha256"],
                    "elapsed_seconds": round(time.monotonic() - request_rows[case["id"]]["started"], 4),
                    "job_id": job.get("job_id") or linkage.get("job_id"),
                    "job_status": job.get("status") or linkage.get("job_status"),
                    "attempt": job.get("attempt") or linkage.get("attempt"),
                    "job_linkage_method": linkage.get("lookup_method"),
                    "job_linkage_status": linkage.get("lookup_status"),
                    "result_packet": job.get("result_packet"),
                    "evidence_packets": job.get("evidence_packets", []),
                    "observations": result.get("observations", []),
                    "usage": "not_exposed_by_public_job_result",
                    "model_turn_count": "not_exposed_by_public_job_result",
                    "reasoning_tokens": "not_exposed_by_public_job_result",
                    "prefill_time": "not_exposed_by_public_job_result",
                }
                if linkage.get("failure_class"):
                    row["failure_class"] = linkage["failure_class"]
                row["private_job_answer_observed"] = bool(linkage.get("private_job_answer_observed"))
                row["private_job_findings_count"] = int(linkage.get("private_job_findings_count", 0) or 0)
                row["actual_inference_confirmed"] = bool(linkage.get("actual_inference_confirmed"))
                row["actual_inference_evidence"] = linkage.get("actual_inference_evidence")
                result_ref = job.get("result_packet")
                result_packet = composition.store.lookup(result_ref, access_scope=item["scope"]) if result_ref else None
                payload = result_packet.packet.payload if result_packet and result_packet.status == "ok" else {}
                row["answer"] = payload.get("answer", job.get("answer"))
                row["findings"] = payload.get("findings", job.get("findings", []))
                row["unresolved_questions"] = payload.get("unresolved_questions", job.get("unresolved_questions", []))
                finding_refs = _finding_packet_ids(row["findings"])
                citations, citation_resolution = _resolve_finding_citations(
                    composition, finding_refs, item["scope"], fixture_root)
                row["citation_packet_resolution"] = citation_resolution
                row["broker_cited_fixture_sources"] = citations
                row["inference_confirmed"] = _visible_inference_confirmed(
                    row["status"], row["answer"], row["findings"])
                if row["inference_confirmed"]:
                    row["actual_inference_confirmed"] = True
                    row["actual_inference_evidence"] = "visible_public_answer_or_findings"
                if row["inference_confirmed"] and isinstance(row["answer"], str) and row["answer"].strip():
                    row["score"] = score_case(case, {"answer": row["answer"], "citations": citations}, fixture_root)
                elif not row["inference_confirmed"]:
                    failure = str(row.get("failure_class") or result.get("reason") or result.get("status") or "incomplete")
                    row["failure_class"] = failure[:180]
                    failures[failure] = failures.get(failure, 0) + 1
                else:
                    row["answer"] = None
                report["cases"].append(row)
            report["jobs_identified"] = len([row for row in report["cases"] if row.get("job_id")])
            confirmed_ids = [row["case_id"] for row in report["cases"] if row.get("inference_confirmed")]
            report["confirmed_inference_case_ids"] = confirmed_ids
            report["confirmed_inference_count"] = len(confirmed_ids)
            report["inference_performed"] = bool(confirmed_ids)
            actual_ids = [row["case_id"] for row in report["cases"]
                          if row.get("actual_inference_confirmed")]
            report["actual_inference_case_ids"] = actual_ids
            report["actual_inference_count"] = len(actual_ids)
            report["actual_inference_confirmed"] = bool(actual_ids)
            with progress_lock:
                _atomic_json(private_root / "report.json", report)
            if _same_failure_stop(failures):
                report["stop_reason"] = "two_same_class_failures"
                stop_dispatch = True
                break

        if stop_dispatch:
            attempted = set(attempted_cases)
            for item in packets:
                case_id = item["case"]["id"]
                if case_id not in attempted and not any(row.get("case_id") == case_id for row in report["cases"]):
                    report["cases"].append({"case_id": case_id, "status": "not_run_stop_rule",
                                            "inference_confirmed": False})

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
        report["jobs_identified"] = len([row for row in report["cases"] if row.get("job_id")])
        report["status"] = ("completed_with_gaps" if report["inquiry_count"] and
                            all(row.get("status") in {"completed", "partial"} for row in report["cases"])
                            else "partial")
        report["completed_unix"] = time.time()
    except Exception as error:
        if composition is None or not job_service_started:
            _preflight_failure_report(report, error)
        else:
            report["status"] = "partial"
            report["failure"] = {"class": type(error).__name__, "reason": str(error)[:300]}
            report["completed_unix"] = time.time()
    finally:
        if composition is not None:
            stopped = composition.close()
            report["cleanup"] = {"isolated_job_service_started": job_service_started,
                                 "isolated_job_service_stopped": bool(stopped) if job_service_started else None,
                                 "isolated_cache_retained_for_review": True,
                                 "central_supervisor_stopped": False}
            if job_service_started and report["cleanup"]["isolated_job_service_stopped"] is not True:
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
    parser.add_argument("--rescore-report", type=Path,
                        help="rescore an existing terminal live report without model calls")
    parser.add_argument("--rescore-output", type=Path,
                        help="write a separate rescore receipt inside the source artifact root")
    parser.add_argument("--rescore-case-set", type=Path,
                        help="reviewed extension case-set JSON for extension rescoring")
    args = parser.parse_args(argv)
    if args.rescore_report is not None:
        if args.execute_live or args.output is not None or args.artifact_root is not None:
            raise QualificationError("rescore cannot be combined with live execution or --output")
        receipt, receipt_path = rescore_existing_report(
            args.rescore_report, output_path=args.rescore_output,
            case_set_path=args.rescore_case_set)
        _atomic_json(receipt_path, receipt)
        sys.stdout.write(json.dumps({"status": receipt["status"],
                                     "receipt_path": str(receipt_path),
                                     "original_report_sha256": receipt["original_report_sha256"]},
                                    sort_keys=True) + "\n")
        return 0
    if args.rescore_output is not None:
        raise QualificationError("--rescore-output requires --rescore-report")
    if args.rescore_case_set is not None:
        raise QualificationError("--rescore-case-set requires --rescore-report")
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
