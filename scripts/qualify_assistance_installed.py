#!/usr/bin/env python3
"""Bounded qualification of the installed Project Control assistance runtime.

The command is inert unless ``--execute-live`` is present. Run it with the
selected release's Python interpreter in isolated mode; this file deliberately
does not add the source checkout to ``sys.path``. It qualifies cold concurrent
startup, one public question, one registered-skill question, stable supervisor
identity, and a proven stop. It is not a calibration or broad acceptance grid.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping


class QualificationError(RuntimeError):
    pass


MAX_WALL_SECONDS = 600
MAX_INQUIRY_SUBMISSIONS = 2
MAX_TURNS_PER_SUBMISSION = 6
MAX_DIRECT_ROLE_TURNS = 2
MAX_OUTPUT_BYTES = 1024 * 1024
SKILL_HELPER = r'''import json, sys
import project_control
from pathlib import Path
from project_control.cli import _assistance_composition
from project_control.runtime_binding import RELEASE_DIGEST_VARIABLE, RELEASE_MANIFEST_VARIABLE

project, skill, query, expected_root, expected_digest = sys.argv[1:]
module = Path(project_control.__file__).resolve(strict=True)
root = Path(expected_root).resolve(strict=True)
if not module.is_relative_to(root):
    raise RuntimeError("installed_package_outside_selected_release")
manifest = Path(__import__("os").environ.get(RELEASE_MANIFEST_VARIABLE, "")).resolve(strict=True)
if not manifest.is_relative_to(root) or __import__("os").environ.get(RELEASE_DIGEST_VARIABLE) != expected_digest:
    raise RuntimeError("selected_release_pin_mismatch")
config, composition = _assistance_composition(project)
try:
    composition.start()
    value = composition.skills.inquire(access_scope=composition.scope(project), query=query, skill=skill)
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
finally:
    composition.jobs.shutdown(timeout=2)
'''

ROLE_HELPER = r'''import concurrent.futures, hashlib, json, os, sys, time
from pathlib import Path
import project_control
from project_control.observer_analysis import SkillsObserverAnalysisProvider
from project_control.runtime_binding import RELEASE_DIGEST_VARIABLE, RELEASE_MANIFEST_VARIABLE

expected_root, expected_digest = sys.argv[1:]
root = Path(expected_root).resolve(strict=True)
module = Path(project_control.__file__).resolve(strict=True)
manifest = Path(os.environ.get(RELEASE_MANIFEST_VARIABLE, "")).resolve(strict=True)
if (not module.is_relative_to(root) or not manifest.is_relative_to(root)
        or os.environ.get(RELEASE_DIGEST_VARIABLE) != expected_digest
        or not Path(sys.prefix).resolve().is_relative_to(root)):
    raise RuntimeError("installed_release_identity_mismatch")

provider = SkillsObserverAnalysisProvider()
identity_keys = ("supervisor_pid", "supervisor_process_start", "daemon_epoch",
                 "runtime_fingerprint", "receiver_fingerprint")
def status_snapshot():
    value = provider.central_status()
    return value
before = status_snapshot()
identity = {key: before.get(key) for key in identity_keys}
if any(value in (None, "") for value in identity.values()):
    raise RuntimeError("supervisor_identity_incomplete_before_direct_turns")
open_started = time.monotonic()
opened = provider.open_sessions(2, compute_profile="narrow", parallelism="default",
                                deadline_epoch=time.time() + 240)
open_elapsed = time.monotonic() - open_started
if not isinstance(opened, dict) or opened.get("status") != "available":
    raise RuntimeError("two_direct_turn_sessions_unavailable")
session_ids = opened.get("session_ids")
if not isinstance(session_ids, list) or len(session_ids) != 2 or len(set(session_ids)) != 2:
    raise RuntimeError("two_distinct_session_leases_not_returned")
assignments = []
responses = []
errors = []
closed = []
try:
    leased = status_snapshot()
    if any(leased.get(key) != identity[key] for key in identity_keys):
        raise RuntimeError("supervisor_identity_changed_while_opening_direct_sessions")
    slots = leased.get("slots")
    if not isinstance(slots, list):
        raise RuntimeError("slot_inventory_unavailable")
    for session_id in session_ids:
        matches = [slot for slot in slots if isinstance(slot, dict)
                   and slot.get("service_lease_id") == session_id]
        if len(matches) != 1:
            raise RuntimeError("leased_session_slot_mapping_unavailable")
        slot = matches[0]
        assignments.append({"session_id": session_id, "slot_id": slot.get("slot_id"),
            "server_pid": slot.get("server_pid"), "server_process_start": slot.get("server_process_start"),
            "gpu_uuids": slot.get("gpu_uuids"), "parallelism": slot.get("parallelism"),
            "compute_profile": slot.get("compute_profile"), "state": slot.get("state"),
            "leased": slot.get("leased"), "model_id": slot.get("model_id"),
            "model_sha256": slot.get("model_sha256")})

    source_text = "Fixture note: The documented default execution mode is layer parallelism."
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    evidence = [{"reference": "qualification-inline-fixture.md#default-mode",
                 "sha256": source_hash, "text": source_text}]
    schema = {"type": "object", "additionalProperties": False,
        "required": ["answer", "citations", "uncertainty"],
        "properties": {
            "answer": {"type": "string", "minLength": 1, "maxLength": 500},
            "citations": {"type": "array", "minItems": 1, "maxItems": 2,
                "items": {"type": "object", "additionalProperties": False,
                    "required": ["reference", "sha256", "excerpt"],
                    "properties": {"reference": {"type": "string", "maxLength": 120},
                        "sha256": {"type": "string", "maxLength": 64},
                        "excerpt": {"type": "string", "minLength": 1, "maxLength": 200}}}},
            "uncertainty": {"type": "string", "maxLength": 240}}}
    instructions = ("Answer only from the source evidence. Do not add facts or infer beyond it. "
                    "Cite the exact reference, SHA-256, and a verbatim excerpt. Return the required JSON.")
    turns = [
        {"role": "gather-v1", "session_id": session_ids[0],
         "turn_policy_id": "gather-v1", "question": "Extract the documented execution mode."},
        {"role": "synthesis-v1", "session_id": session_ids[1],
         "turn_policy_id": "synthesis-v1", "question": "State the execution mode with its exact source."},
    ]
    def invoke(turn):
        request = {"format": "PC-LOCAL-INVESTIGATOR-TURN/2",
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": json.dumps({"question": turn["question"],
                            "evidence": evidence}, sort_keys=True, separators=(",", ":"))}],
            "timeout_seconds": 60, "compute_profile": "narrow", "parallelism": "default",
            "session_id": turn["session_id"], "deadline_epoch": time.time() + 65,
            "turn_policy_id": turn["turn_policy_id"],
            "response_format": {"type": "json_object", "schema": schema}}
        start = time.monotonic()
        response = provider.investigate_turn(request)
        elapsed = time.monotonic() - start
        schema_valid = False
        citation_valid = False
        try:
            parsed = json.loads(response.get("text", ""))
            schema_valid = (isinstance(parsed, dict)
                and set(parsed) == {"answer", "citations", "uncertainty"}
                and isinstance(parsed.get("answer"), str) and bool(parsed["answer"].strip())
                and isinstance(parsed.get("citations"), list) and len(parsed["citations"]) == 1
                and isinstance(parsed.get("uncertainty"), str))
            citation = parsed["citations"][0] if schema_valid else None
            citation_valid = (isinstance(citation, dict)
                and citation.get("reference") == evidence[0]["reference"]
                and citation.get("sha256") == source_hash
                and isinstance(citation.get("excerpt"), str) and bool(citation["excerpt"])
                and citation["excerpt"] in source_text)
        except (TypeError, ValueError, KeyError, IndexError):
            pass
        return turn, response, elapsed, schema_valid, citation_valid
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_turns = [pool.submit(invoke, turn) for turn in turns]
        for future in concurrent.futures.as_completed(future_turns, timeout=75):
            try:
                turn, response, elapsed, schema_valid, citation_valid = future.result()
                responses.append({"policy_id": turn["turn_policy_id"],
                    "session_id": turn["session_id"], "status": response.get("status"),
                    "response_text": response.get("text"), "usage": response.get("usage"),
                    "turn_policy": response.get("turn_policy"),
                    "response_metadata": response.get("response_metadata"),
                    "warm_model_reused": response.get("warm_model_reused"),
                    "model_id": response.get("model_id"),
                    "parallelism": response.get("parallelism"),
                    "compute_profile": response.get("compute_profile"),
                    "response_schema_valid": schema_valid,
                    "source_citation_valid": citation_valid,
                    "elapsed_seconds": round(elapsed, 4)})
            except Exception as error:
                errors.append(type(error).__name__)
finally:
    for session_id in session_ids:
        try:
            closed.append({"session_id": session_id, **provider.close_session(session_id)})
        except Exception as error:
            closed.append({"session_id": session_id, "released": False,
                           "failure": type(error).__name__})

after = status_snapshot()
stable = all(after.get(key) == identity[key] for key in identity_keys)
after_slots = after.get("slots") if isinstance(after.get("slots"), list) else []
after_by_id = {item.get("slot_id"): item for item in after_slots
               if isinstance(item, dict) and item.get("slot_id")}
slot_processes_stable = (len(assignments) == 2 and all(
    item.get("slot_id") in after_by_id
    and after_by_id[item["slot_id"]].get("server_pid") == item.get("server_pid")
    and after_by_id[item["slot_id"]].get("server_process_start") == item.get("server_process_start")
    and after_by_id[item["slot_id"]].get("gpu_uuids") == item.get("gpu_uuids")
    for item in assignments))
assignments_proven = (len(assignments) == 2
    and all(isinstance(item.get("slot_id"), str) and item.get("slot_id") for item in assignments)
    and len({item["slot_id"] for item in assignments}) == 2
    and all(isinstance(item.get("server_pid"), int) and item.get("server_pid") > 0
            and isinstance(item.get("server_process_start"), str) and item.get("server_process_start")
            for item in assignments)
    and len({(item["server_pid"], item["server_process_start"]) for item in assignments}) == 2
    and all(isinstance(item.get("gpu_uuids"), list) and item.get("gpu_uuids") for item in assignments)
    and not set(assignments[0].get("gpu_uuids") or []) & set(assignments[1].get("gpu_uuids") or []))
if len(responses) != 2:
    errors.append("one_or_more_direct_turns_missing")
print(json.dumps({"status": "available" if len(responses) == 2 and stable else "partial",
    "supervisor_identity": identity, "supervisor_identity_stable": stable,
    "slot_processes_stable_across_calls": slot_processes_stable,
    "costs": {"session_acquisition_elapsed_seconds": round(open_elapsed, 4),
        "cold_role_costs": "not_separable_from_two_session_acquisition; see shared elapsed time",
        "warm_role_costs": {item["policy_id"]: {"elapsed_seconds": item["elapsed_seconds"],
            "usage": item.get("usage"), "turn_policy": item.get("turn_policy")}
            for item in responses}},
    "session_open": opened, "slot_assignments": assignments,
    "distinct_layer_pair_assignment_proven": assignments_proven and slot_processes_stable
        and all(item.get("parallelism") == "layer" for item in assignments),
    "turns": responses, "errors": errors, "session_close": closed,
    "after": after}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
provider.close()
'''


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _private_new_directory(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise QualificationError("artifact directory must be new; refusing reuse")
    parent = path.parent.resolve(strict=True)
    parent_stat = parent.stat()
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise QualificationError("artifact parent must be an owned directory")
    target = parent / path.name
    target.mkdir(mode=0o700)
    resolved = target.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise QualificationError("artifact directory ownership invalid")
    resolved.chmod(0o700)
    if resolved.stat().st_mode & 0o077:
        raise QualificationError("artifact directory is not private")
    return resolved


def _selected_release() -> tuple[Path, Path, str]:
    manifest_text = os.environ.get("PROJECT_CONTROL_RELEASE_MANIFEST", "")
    digest = os.environ.get("PROJECT_CONTROL_RELEASE_DIGEST", "")
    if not manifest_text or len(digest) != 64:
        raise QualificationError("selected release pins are missing")
    manifest = Path(manifest_text).expanduser().resolve(strict=True)
    raw = manifest.read_bytes()
    if _sha256(raw) != digest:
        raise QualificationError("selected release manifest digest mismatch")
    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise QualificationError("selected release manifest is invalid") from error
    if not isinstance(document, dict) or document.get("schema_version") != 2:
        raise QualificationError("selected release manifest schema mismatch")
    root = manifest.parent
    current = (Path.home() / ".local/share/project-control/current").resolve(strict=True)
    if current != root:
        raise QualificationError("selected release differs from current pointer")
    module_spec = __import__("importlib.util", fromlist=["find_spec"]).find_spec("project_control")
    origin = getattr(module_spec, "origin", None)
    if not origin or not Path(origin).resolve(strict=True).is_relative_to(root):
        raise QualificationError("installed project_control import is outside selected release")
    if not Path(sys.prefix).resolve().is_relative_to(root):
        raise QualificationError("qualification interpreter is outside selected release")
    return root, manifest, digest


def _parse_json_result(result: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    out = result.stdout or ""
    err = result.stderr or ""
    if len(out.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES:
        raise QualificationError(f"{label} output exceeded the bound")
    if result.returncode != 0:
        raise QualificationError(f"{label} failed: {(err or out).strip()[:300]}")
    try:
        value = json.loads(out)
    except (ValueError, TypeError) as error:
        raise QualificationError(f"{label} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{label} returned a non-object")
    return value


def _status_identity(status: Mapping[str, Any], expected_digest: str | None = None) -> dict[str, Any]:
    runtime = status.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("readiness") != "verified_ready":
        raise QualificationError("installed runtime is not identity-verified ready")
    keys = ("release_digest", "receiver_fingerprint", "todo_runtime_fingerprint",
            "supervisor_pid", "supervisor_process_start", "daemon_epoch")
    identity = {key: runtime.get(key) for key in keys}
    if any(identity[key] in (None, "") for key in keys):
        raise QualificationError("runtime identity receipt is incomplete")
    if expected_digest and identity["release_digest"] != expected_digest:
        raise QualificationError("runtime reports a different selected release")
    if (not isinstance(identity["supervisor_pid"], int)
            or isinstance(identity["supervisor_pid"], bool)
            or not isinstance(identity["daemon_epoch"], str)
            or len(identity["daemon_epoch"]) != 64):
        raise QualificationError("runtime supervisor identity is malformed")
    return identity


def _work_is_idle(status: Mapping[str, Any]) -> bool:
    work = status.get("work")
    if not isinstance(work, dict):
        return False
    if work.get("active_work_truncated") is True:
        return False
    for key in ("active_work", "active_jobs", "active_slots", "active_execution_slots"):
        value = work.get(key)
        if isinstance(value, list) and value:
            return False
    for key in ("active_count", "active_admissions", "active_leases"):
        value = work.get(key)
        if isinstance(value, int) and value != 0:
            return False
    resources = work.get("owned_resources")
    if isinstance(resources, dict):
        if resources.get("release_pending") is True:
            return False
        for key in ("active_sessions", "active_leases", "active_admissions"):
            value = resources.get(key)
            if isinstance(value, int) and value != 0:
                return False
        states = resources.get("states")
        if isinstance(states, dict) and any(
                isinstance(count, int) and count > 0 and state != "released_verified"
                for state, count in states.items()):
            return False
    return True


class InstalledQualification:
    def __init__(self, *, cli: Path, project: str, question: str, skill: str,
                 skill_question: str, output_dir: Path, wall_seconds: int,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 clock: Callable[[], float] = time.monotonic):
        self.cli = cli
        self.project = project
        self.question = question
        self.skill = skill
        self.skill_question = skill_question
        self.output_dir = output_dir
        self.wall_seconds = wall_seconds
        self.runner = runner
        self.clock = clock
        self.events: list[dict[str, Any]] = []
        self.started_by_run = False
        self.release_root, self.manifest, self.release_digest = _selected_release()
        self.cli = cli.resolve(strict=True)
        if not self.cli.is_relative_to(self.release_root):
            raise QualificationError("CLI executable is outside selected release")
        self.deadline = self.clock() + wall_seconds

    def _remaining(self, limit: float = 30) -> float:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise QualificationError("qualification wall budget exhausted")
        return min(limit, remaining)

    def _run(self, args: list[str], label: str, *, timeout: float = 30) -> dict[str, Any]:
        self.events.append({"event": "effect_intent", "operation": label,
                            "argv": args, "at_monotonic": self.clock()})
        result = self.runner(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, timeout=self._remaining(timeout),
                             check=False, cwd=str(self.release_root))
        value = _parse_json_result(result, label)
        self.events.append({"event": "effect_result", "operation": label,
                            "returncode": result.returncode, "result": value,
                            "at_monotonic": self.clock()})
        return value

    def _status(self) -> dict[str, Any]:
        return self._run([str(self.cli), "assistance", "status"], "status", timeout=10)

    def _start_concurrently(self) -> dict[str, Any]:
        argv = [str(self.cli), "assistance", "start"]
        # From this point the run owns the start request and must attempt the
        # supported stop path even when a concurrent receipt is malformed.
        self.started_by_run = True
        self.events.append({"event": "effect_intent", "operation": "concurrent_start",
                            "calls": 2, "at_monotonic": self.clock()})
        results: list[subprocess.CompletedProcess[str]] = []
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.runner, argv, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, timeout=self._remaining(120), check=False,
                                   cwd=str(self.release_root)) for _ in range(2)]
            try:
                for future in as_completed(futures, timeout=self._remaining(125)):
                    try:
                        results.append(future.result())
                    except Exception as error:  # recorded as a bounded process failure
                        errors.append(type(error).__name__)
            except TimeoutError:
                errors.append("concurrent_start_timeout")
        if errors or len(results) != 2:
            raise QualificationError("concurrent start did not return two bounded results")
        receipts = [_parse_json_result(item, "assistance start") for item in results]
        identities = []
        for receipt in receipts:
            if receipt.get("status") != "ready":
                raise QualificationError("assistance start did not return ready")
            expected = {"release_digest": self.release_digest}
            if receipt.get("release_digest") != self.release_digest:
                raise QualificationError("start receipt release identity mismatch")
            for field in ("receiver_fingerprint", "todo_runtime_fingerprint",
                          "supervisor_pid", "supervisor_process_start", "daemon_epoch"):
                if receipt.get(field) in (None, ""):
                    raise QualificationError("start receipt identity is incomplete")
                expected[field] = receipt[field]
            identities.append(expected)
        if identities[0] != identities[1]:
            raise QualificationError("concurrent starts observed different supervisor epochs")
        self.events.append({"event": "effect_result", "operation": "concurrent_start",
                            "identity": identities[0], "at_monotonic": self.clock()})
        return identities[0]

    def _skill(self) -> dict[str, Any]:
        helper = [sys.executable, "-I", "-c", SKILL_HELPER,
                  self.project, self.skill, self.skill_question,
                  str(self.release_root), self.release_digest]
        value = self._run(helper, "registered_skill_question", timeout=300)
        return value

    def _direct_roles(self) -> dict[str, Any]:
        helper = [sys.executable, "-I", "-c", ROLE_HELPER,
                  str(self.release_root), self.release_digest]
        return self._run(helper, "concurrent_trusted_role_turns", timeout=420)

    def _inquiry_record(self, operation: str, value: Mapping[str, Any]) -> dict[str, Any]:
        budget = value.get("usage") if isinstance(value.get("usage"), dict) else None
        return {"operation": operation, "status": value.get("status"),
                "visible_output_present": bool(value.get("answer") or value.get("text")
                                                or value.get("excerpts") or value.get("resources")),
                "sources_present": bool(value.get("sources") or value.get("citations")),
                "policy_telemetry": budget.get("budget") if isinstance(budget, dict) else None,
                "policy_telemetry_status": "available" if isinstance(budget, dict) else "not_exposed_by_public_response"}

    def run(self) -> dict[str, Any]:
        self.output_dir = _private_new_directory(self.output_dir)
        started = time.time()
        intent = {"schema_version": 1, "status": "intent_recorded",
                  "release_root": str(self.release_root),
                  "release_manifest_sha256": self.release_digest,
                  "project": self.project, "operations": ["concurrent_start", "ask", "skill", "stop"],
                  "wall_budget_seconds": self.wall_seconds,
                  "max_inquiry_submissions": MAX_INQUIRY_SUBMISSIONS,
                  "broker_turn_limit_per_submission": MAX_TURNS_PER_SUBMISSION,
                  "direct_role_turns": MAX_DIRECT_ROLE_TURNS,
                  "potential_model_turns": (MAX_INQUIRY_SUBMISSIONS * MAX_TURNS_PER_SUBMISSION
                                             + MAX_DIRECT_ROLE_TURNS),
                  "model_turn_count": {"broker_inquiry_turns": "unknown_no_supported_audit",
                                       "direct_role_turns": MAX_DIRECT_ROLE_TURNS},
                  "created_at_epoch": started}
        _atomic_json(self.output_dir / "intent.json", intent)
        receipt: dict[str, Any] = {"schema_version": 1, "status": "running", "intent": intent,
                                  "events": self.events, "inquiries": []}
        _atomic_json(self.output_dir / "receipt.json", receipt)
        try:
            before = self._status()
            runtime_before = before.get("runtime")
            if (not isinstance(runtime_before, dict)
                    or runtime_before.get("readiness") != "inactive"
                    or runtime_before.get("active_state") not in {"inactive", None}
                    or runtime_before.get("main_pid") not in {0, None}
                    or runtime_before.get("release_veto_active") is True
                    or not _work_is_idle(before)):
                raise QualificationError("cold qualification requires an inactive, idle service without a release veto")
            identity = self._start_concurrently()
            ready = self._status()
            if _status_identity(ready, self.release_digest) != identity:
                raise QualificationError("status identity differs from concurrent start receipts")
            receipt["cold_start"] = {"before": before, "identity": identity,
                                     "after": ready, "concurrent_start_count": 2}

            question = self._run([str(self.cli), "assistance", "ask", self.question,
                                  "--project", self.project], "source_grounded_question", timeout=300)
            receipt["inquiries"].append(self._inquiry_record("ask", question))
            after_ask = self._status()
            if _status_identity(after_ask, self.release_digest) != identity:
                raise QualificationError("supervisor identity changed during question")

            skill = self._skill()
            receipt["inquiries"].append(self._inquiry_record("skill", skill))
            if len(receipt["inquiries"]) > MAX_INQUIRY_SUBMISSIONS:
                raise QualificationError("inquiry submission budget exceeded")
            after_skill = self._status()
            if _status_identity(after_skill, self.release_digest) != identity:
                raise QualificationError("supervisor identity changed during skill call")
            roles = self._direct_roles()
            receipt["trusted_role_smoke"] = roles
            after_roles = self._status()
            if _status_identity(after_roles, self.release_digest) != identity:
                raise QualificationError("supervisor identity changed during trusted-role calls")
            receipt["identity_stable_through_calls"] = True
            receipt["inquiry_results_usable"] = all(
                item.get("status") in {"completed", "partial", "ok"}
                and item.get("visible_output_present") is True
                for item in receipt["inquiries"])
            receipt["trusted_role_results_available"] = (
                roles.get("status") == "available"
                and roles.get("distinct_layer_pair_assignment_proven") is True
                and len(roles.get("turns", [])) == MAX_DIRECT_ROLE_TURNS
                and {item.get("policy_id") for item in roles.get("turns", [])}
                    == {"gather-v1", "synthesis-v1"}
                and all(item.get("status") == "available"
                    and item.get("response_schema_valid") is True
                    and item.get("source_citation_valid") is True
                    and isinstance(item.get("turn_policy"), dict)
                    and item["turn_policy"].get("policy_id") == item.get("policy_id")
                    for item in roles.get("turns", [])))
            receipt["quality_review_required"] = True
        except BaseException as error:
            receipt["failure"] = {"type": type(error).__name__, "message": str(error)[:400]}
            raise
        finally:
            if self.started_by_run:
                try:
                    stop = self._run([str(self.cli), "assistance", "stop"], "verified_stop", timeout=120)
                    after_stop = self._status()
                    runtime = after_stop.get("runtime") if isinstance(after_stop, dict) else None
                    receipt["stop"] = {"response": stop, "after": after_stop,
                                       "stopped": stop.get("status") == "stopped"
                                       and isinstance(runtime, dict)
                                       and runtime.get("readiness") == "inactive"
                                       and runtime.get("active_state") in {"inactive", None}
                                       and _work_is_idle(after_stop)}
                except BaseException as error:
                    receipt["stop"] = {"stopped": False,
                                        "failure": {"type": type(error).__name__,
                                                    "message": str(error)[:300]}}
            receipt["events"] = self.events
            receipt["status"] = ("bounded_question_and_skill_smoke"
                                 if receipt.get("stop", {}).get("stopped") is True
                                 and receipt.get("identity_stable_through_calls") is True
                                 and receipt.get("inquiry_results_usable") is True
                                 and receipt.get("trusted_role_results_available") is True
                                 else "incomplete")
            receipt["finished_at_epoch"] = time.time()
            _atomic_json(self.output_dir / "receipt.json", receipt)
        return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-live", action="store_true",
                        help="authorize the bounded installed-runtime qualification")
    parser.add_argument("--project", required=True, help="registered fixture project id")
    parser.add_argument("--question", required=True, help="source-grounded read-only question")
    parser.add_argument("--skill", required=True, help="registered installed skill id")
    parser.add_argument("--skill-question", required=True, help="read-only question for that skill")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="new private directory; existing paths are refused")
    parser.add_argument("--cli", type=Path, help="candidate CLI; defaults to selected release bin")
    parser.add_argument("--wall-seconds", type=int, default=MAX_WALL_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute_live:
        print(json.dumps({"status": "inert", "required_flag": "--execute-live"}, sort_keys=True))
        return 2
    if not 1 <= args.wall_seconds <= MAX_WALL_SECONDS:
        raise QualificationError("wall budget must be between 1 and 600 seconds")
    if not args.project or not args.question.strip() or not args.skill or not args.skill_question.strip():
        raise QualificationError("project, question, registered skill, and skill question are required")
    manifest_text = os.environ.get("PROJECT_CONTROL_RELEASE_MANIFEST", "")
    if not manifest_text:
        raise QualificationError("selected release pins are missing")
    root = Path(manifest_text).expanduser().resolve(strict=True).parent
    cli = args.cli or root / "bin" / "project-control"
    driver = InstalledQualification(cli=cli, project=args.project, question=args.question,
        skill=args.skill, skill_question=args.skill_question, output_dir=args.out_dir,
        wall_seconds=args.wall_seconds)
    receipt = driver.run()
    print(json.dumps({"status": receipt["status"], "receipt": str(driver.output_dir / "receipt.json")},
                     sort_keys=True))
    return 0 if receipt["status"] == "bounded_question_and_skill_smoke" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1)
