"""Durable, scoped autonomous LAB sessions built on the contained runner.

Preview records authority without inference. Authorization starts one finite
deadline. A run may then plan and execute several experiments inside that
frozen grant; every effect is journaled before the runner is called.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from ..adapters.git import GitReadAdapter
from . import lab_runner
from .lab import (LabError, LabOperator, LabSelection, LabService, _dump,
                  _sha256_file, _verify_snapshot)
from .lab_proposals import (LabProposal, ProposalError, parse_proposal,
                            validate_proposal, verify_artifacts, write_artifacts)
from .lab_snapshot import Snapshot, SnapshotError


DEFAULT_WALL_SECONDS = 600
DEFAULT_MAX_EXPERIMENTS = 6
MAX_WALL_SECONDS = 600
MAX_EXPERIMENTS = 6
MAX_PLANNER_SOURCE_BYTES = 400 * 1024
MAX_PLANNER_PAYLOAD_BYTES = 600 * 1024
_SCOPE_SEAL = object()


@dataclass(frozen=True, slots=True)
class LabScope:
    project: str
    source_paths: tuple[str, ...]
    goal: str
    tools: tuple[str, ...] = ("python3",)
    wall_seconds: int = DEFAULT_WALL_SECONDS
    max_experiments: int = DEFAULT_MAX_EXPERIMENTS
    gpu_uuids: tuple[str, ...] = ()
    toolchain_root: str | None = None


@dataclass(frozen=True, slots=True)
class _Capture:
    root: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    identity: dict[str, str]
    sources: tuple[dict[str, Any], ...]


class LabSessionService:
    """Session state machine over a trusted, cold ``LabService`` registry.

    ``planner`` is a caller-supplied model adapter. GPU effects are deliberately
    delegated through ``gpu_executor``; this service never manufactures GPU
    admission or cleanup evidence.
    """

    def __init__(self, lab_service: LabService, planner: Callable[[dict[str, Any]], Any], *,
                 gpu_executor: Callable[..., Mapping[str, Any]] | None = None,
                 lifecycle: Any = None,
                 clock: Callable[[], float] = time.time):
        if not isinstance(lab_service, LabService) or not callable(planner):
            raise LabError("trusted LabService and planner callable are required")
        self.lab = lab_service
        self.planner = planner
        self.gpu_executor = gpu_executor
        self.lifecycle = lifecycle
        self.clock = clock

    def preview(self, operator: LabOperator, scope: LabScope) -> dict[str, Any]:
        """Validate trusted registration and capture source identity without inference."""
        self.lab._require_operator(operator)
        self._validate_scope(scope)
        project = self.lab.projects.get(scope.project)
        if project is None:
            raise PermissionError("project_not_registered")
        session_id = "lab_session_" + uuid.uuid4().hex
        db = self.lab._open(create=True)
        assert db is not None
        db.close()
        sessions_root = self.lab.state_root / "sessions"
        sessions_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(sessions_root, 0o700)
        session_root = self.lab.state_root / "sessions" / session_id
        try:
            session_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            capture = self._capture(scope, session_root / "preview-source")
        except SnapshotError as exc:
            shutil.rmtree(session_root, ignore_errors=True)
            raise LabError(str(exc)) from exc
        except BaseException:
            shutil.rmtree(session_root, ignore_errors=True)
            raise
        now = float(self.clock())
        db = self.lab._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO lab_sessions(
                session_id,project,source_root,repository,scope_json,state,preview_snapshot,
                preview_manifest_sha256,preview_identity,created_at,updated_at,experiment_count,
                planning_count,cancel_requested,run_owner,run_pid,run_start_ticks)
                VALUES(?,?,?,?,?,'preview',?,?,?,?,?,0,0,0,NULL,NULL,NULL)""",
                (session_id, scope.project, str(Path(project.root).resolve(strict=True)),
                 project.repository, _dump(asdict(scope)), str(capture.root), capture.manifest_sha256,
                 _dump(capture.identity), now, now))
            db.commit()
        except BaseException:
            db.rollback()
            shutil.rmtree(session_root, ignore_errors=True)
            raise
        finally:
            db.close()
        return {"session_id": session_id, "state": "preview", "project": scope.project,
                "repository": project.repository, "goal": scope.goal,
                "source_identity": capture.identity,
                "source_files": len(capture.manifest["files"]),
                "tools": list(scope.tools), "gpu_uuids": list(scope.gpu_uuids),
                "wall_seconds": scope.wall_seconds, "max_experiments": scope.max_experiments,
                "authorized": False, "inference_started": False}

    def authorize(self, operator: LabOperator, session_id: str) -> dict[str, Any]:
        """Authorize one preview exactly once, starting its finite deadline."""
        self.lab._require_operator(operator)
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise LabError("LAB session does not exist")
            self._assert_frozen_registration(row)
            if row["state"] != "preview":
                raise LabError("LAB preview has already been authorized or consumed")
            scope = _scope_from_json(row["scope_json"])
            deadline = now + scope.wall_seconds
            db.execute("UPDATE lab_sessions SET state='authorized',authorized_at=?,deadline=?,updated_at=? WHERE session_id=?",
                       (now, deadline, now, session_id))
            self._event(db, session_id, "authorized", {"deadline": deadline}, now)
            db.commit()
            return {"session_id": session_id, "state": "authorized", "deadline": deadline,
                    "remaining_seconds": scope.wall_seconds,
                    "remaining_experiments": scope.max_experiments,
                    "inference_started": False}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def resolve_project(self, session_id: str) -> str:
        """Resolve an opaque scope ID to its registered project for cold CLI setup."""
        db = self.lab._open(create=False)
        if db is None:
            raise LabError("LAB session does not exist")
        try:
            row = db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise LabError("LAB session does not map to a trusted registered project")
            self._assert_frozen_registration(row)
            return str(row["project"])
        finally:
            db.close()

    def run(self, operator: LabOperator, session_id: str) -> dict[str, Any]:
        """Autonomously plan/execute until done, cancelled, or a frozen cap is met."""
        return self._drive(operator, session_id, resume=False)

    def resume(self, operator: LabOperator, session_id: str) -> dict[str, Any]:
        """Resume only a safe paused session; ambiguous effects are never replayed."""
        return self._drive(operator, session_id, resume=True)

    def _drive(self, operator: LabOperator, session_id: str, *, resume: bool) -> dict[str, Any]:
        self.lab._require_operator(operator)
        owner = uuid.uuid4().hex
        row = self._claim(operator, session_id, owner, resume=resume)
        scope = _scope_from_json(row["scope_json"])
        deadline = float(row["deadline"])
        try:
            while True:
                current = self._session_row(session_id)
                if current is None:
                    raise LabError("LAB session disappeared")
                remaining = deadline - float(self.clock())
                if current["cancel_requested"]:
                    return self._finish_session(session_id, owner, "cancelled", "operator_cancelled")
                if remaining <= 0:
                    return self._finish_session(session_id, owner, "budget_exhausted", "wall_budget_exhausted")
                used = int(current["experiment_count"])
                if used >= scope.max_experiments:
                    return self._finish_session(session_id, owner, "budget_exhausted", "experiment_budget_exhausted")

                capture = self._capture(scope, self._new_snapshot_path(session_id), current)
                planner_input = self._planner_input(session_id, scope, capture, deadline, current)
                self._increment_planning(session_id, owner)
                raw = self.planner(planner_input)
                self._assert_running(session_id, owner)
                if self._cancel_requested(session_id):
                    return self._finish_session(session_id, owner, "cancelled", "operator_cancelled")
                if float(self.clock()) >= deadline:
                    return self._finish_session(session_id, owner, "budget_exhausted", "planner_exhausted_wall_budget")
                proposal = parse_proposal(raw)
                validate_proposal(proposal, source_root=capture.root,
                                   source_manifest=capture.manifest, allowed_tools=scope.tools)
                if proposal.argv and proposal.argv[0] == "cuda" and not scope.gpu_uuids:
                    raise ProposalError("CUDA proposal requires explicitly approved GPU UUIDs")
                after_plan = self._capture(scope, self._new_snapshot_path(session_id), current)
                if after_plan.identity != capture.identity:
                    self._record_event(session_id, "source_changed_replan", {
                        "planned_identity": capture.identity, "current_identity": after_plan.identity})
                    capture = after_plan
                    continue
                proposal_id, proposal_root = self._persist_proposal(
                    session_id, owner, proposal, capture, scope)
                if proposal.done:
                    return self._finish_session(session_id, owner, "completed", "planner_done",
                                                final_proposal_id=proposal_id)
                if current["cancel_requested"]:
                    return self._finish_session(session_id, owner, "cancelled", "operator_cancelled")
                remaining = deadline - float(self.clock())
                if remaining <= 0:
                    return self._finish_session(session_id, owner, "budget_exhausted", "wall_budget_before_effect")
                receipt = self._execute(operator, session_id, owner, scope, proposal,
                                        proposal_id, capture, proposal_root, deadline)
                if receipt.get("cleanup_verified") is not True:
                    return self._finish_session(session_id, owner, "unknown", "effect_cleanup_unverified",
                                                receipt=receipt)
                self._store_receipt(session_id, proposal_id, receipt)
                if self._cancel_requested(session_id):
                    return self._finish_session(session_id, owner, "cancelled", "operator_cancelled",
                                                receipt=receipt)
                if receipt.get("continuation_ready") is False:
                    return self._finish_session(session_id, owner, "paused", "gpu_inference_rewarm_incomplete",
                                                receipt=receipt)
        except (ProposalError, SnapshotError, LabError) as exc:
            if self._cancel_requested(session_id):
                return self._finish_session(session_id, owner, "cancelled", "operator_cancelled")
            self._record_event(session_id, "session_error", {"error": str(exc)[:1024]})
            return self._finish_session(session_id, owner, "paused", "proposal_or_source_error",
                                        error=str(exc)[:1024])
        except BaseException as exc:
            self._mark_unknown_or_paused(session_id, owner, exc)
            raise

    def _execute(self, operator: LabOperator, session_id: str, owner: str,
                 scope: LabScope, proposal: LabProposal, proposal_id: str,
                 capture: _Capture, proposal_root: Path, deadline: float) -> dict[str, Any]:
        _verify_snapshot(capture.root, capture.manifest_sha256)
        verify_artifacts(proposal, proposal_root)
        public_json = _dump(proposal.public())
        public_digest = hashlib.sha256(public_json.encode("utf-8")).hexdigest()
        effect_id = "lab_effect_" + uuid.uuid4().hex
        ordinal = int(self._session_row(session_id)["experiment_count"]) + 1
        now = float(self.clock())
        db = self.lab._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None or row["state"] != "running" or row["run_owner"] != owner:
                raise LabError("LAB run ownership was lost before effect intent")
            self._assert_frozen_registration(row)
            if row["cancel_requested"] or now >= deadline:
                raise LabError("LAB scope expired or was cancelled before effect")
            persisted = db.execute("SELECT * FROM lab_session_proposals WHERE proposal_id=?",
                                   (proposal_id,)).fetchone()
            self._assert_persisted_proposal(persisted, session_id, proposal_id,
                                            public_json, public_digest, capture, proposal_root)
            db.execute("""INSERT INTO lab_session_effects(
                effect_id,session_id,proposal_id,ordinal,state,intent_at,snapshot_root,
                proposal_root,argv,receipt_json,error,process_identity)
                VALUES(?,?,?,?,'intent',?,?,?,?,NULL,NULL,NULL)""",
                (effect_id, session_id, proposal_id, ordinal, now, str(capture.root),
                 str(proposal_root), _dump(list(proposal.argv))))
            db.execute("UPDATE lab_sessions SET experiment_count=experiment_count+1,updated_at=? WHERE session_id=?",
                       (now, session_id))
            self._event(db, session_id, "effect_intent", {"effect_id": effect_id,
                "proposal_id": proposal_id, "ordinal": ordinal, "source_identity": capture.identity}, now)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        if self._cancel_requested(session_id) or float(self.clock()) >= deadline:
            reason = "cancelled_before_effect" if self._cancel_requested(session_id) else "deadline_before_effect"
            receipt = {"effect_id": effect_id, "proposal_id": proposal_id,
                       "status": "not_started", "cleanup_verified": True,
                       "elapsed_ms": 0, "reason": reason}
            self._finish_effect(session_id, effect_id, "completed", receipt)
            return receipt
        # Registration is checked again immediately before any external effect.
        # A repository replacement after planning must fail closed.
        frozen = self._session_row(session_id)
        if frozen is None:
            raise LabError("LAB session disappeared before effect")
        self._assert_frozen_registration(frozen)
        verify_artifacts(proposal, proposal_root)
        self._assert_proposal_still_persisted(session_id, proposal_id, public_json,
                                              public_digest, capture, proposal_root)
        if proposal.argv and proposal.argv[0] == "cuda":
            if self.gpu_executor is None:
                receipt = {"effect_id": effect_id, "status": "unavailable",
                           "cleanup_verified": False, "error": "GPU executor is unavailable"}
                self._finish_effect(session_id, effect_id, "unknown", receipt)
                return receipt
            try:
                receipt = self.gpu_executor(scope, proposal, capture.root, proposal_root,
                    session_id=session_id, effect_id=effect_id, deadline=deadline,
                    cancelled=lambda: self._cancel_requested(session_id))
                if not isinstance(receipt, Mapping) or receipt.get("cleanup_verified") is not True:
                    raise LabError("GPU executor did not provide verified cleanup evidence")
                final = dict(receipt)
                final.setdefault("effect_id", effect_id)
                final.setdefault("proposal_id", proposal_id)
                self._validate_gpu_receipt(final, scope, effect_id, proposal)
            except BaseException as exc:
                final = {"effect_id": effect_id, "proposal_id": proposal_id,
                         "status": "unknown", "cleanup_verified": False,
                         "error": str(exc)[:1024]}
            self._finish_effect(session_id, effect_id,
                                "completed" if final.get("cleanup_verified") is True else "unknown", final)
            return final

        attempt_root = self.lab.state_root / "sessions" / session_id / "effects" / effect_id
        attempt_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(attempt_root.parent, 0o700)
        attempt_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        remaining = deadline - float(self.clock())
        if remaining <= 0:
            receipt = {"effect_id": effect_id, "proposal_id": proposal_id,
                       "status": "not_started", "cleanup_verified": True,
                       "elapsed_ms": 0, "reason": "wall_budget_before_runner"}
            self._finish_effect(session_id, effect_id, "completed", receipt)
            return receipt

        grant = lab_runner.ExecutionGrant(timeout_seconds=min(lab_runner.MAX_WALL_SECONDS, remaining))
        started = float(self.clock())
        try:
            result = lab_runner.run_cpu(capture.root, proposal.argv, grant,
                attempt_root=attempt_root, effect_id=effect_id, proposal_root=proposal_root,
                should_cancel=lambda: self._cancel_requested(session_id),
                on_started=lambda identity: self._mark_effect_started(session_id, effect_id, identity))
            receipt = {
                "effect_id": effect_id, "proposal_id": proposal_id, "status": result.status,
                "returncode": result.returncode, "elapsed_ms": result.elapsed_ms,
                "cleanup_verified": result.cleanup_verified,
                "containment_backend": result.containment_backend,
                "source_identity": capture.identity,
                "proposal_sha256": public_digest,
                "argv": list(proposal.argv), "measurements": list(proposal.measurements),
                "stdout": _bounded_text(result.stdout), "stderr": _bounded_text(result.stderr),
                "artifact": ({"path": str(result.artifact_path), "bytes": result.artifact_bytes,
                              "sha256": result.artifact_sha256} if result.artifact_path else None),
                "resource_grant": asdict(grant), "started_at": started,
                "finished_at": float(self.clock()),
            }
            self._finish_effect(session_id, effect_id,
                                "completed" if result.cleanup_verified else "unknown", receipt)
            return receipt
        except lab_runner.EffectCleanupUnverified as exc:
            self._mark_effect_started(session_id, effect_id, exc.identity)
            receipt = {"effect_id": effect_id, "proposal_id": proposal_id,
                       "status": "cleanup_unverified", "cleanup_verified": False,
                       "error": str(exc)[:1024]}
            self._finish_effect(session_id, effect_id, "unknown", receipt)
            return receipt
        except BaseException as exc:
            receipt = {"effect_id": effect_id, "proposal_id": proposal_id,
                       "status": "unknown", "cleanup_verified": False,
                       "error": str(exc)[:1024]}
            self._finish_effect(session_id, effect_id, "unknown", receipt)
            return receipt

    @staticmethod
    def _validate_gpu_receipt(receipt: dict[str, Any], scope: LabScope,
                              effect_id: str, proposal: LabProposal) -> None:
        """Require the typed adapter's execution, native release, and resume proof."""
        terminal = receipt.get("foreground_terminal")
        probe = receipt.get("probe")
        build = receipt.get("build")
        run = receipt.get("run")
        resume = receipt.get("resume")
        expected_artifacts = [{"path": item.path,
            "sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
            "bytes": len(item.content.encode("utf-8"))} for item in proposal.artifacts]
        expected_resources = sorted(f"accelerator:{value}" for value in scope.gpu_uuids)
        resume_bound = (isinstance(resume, Mapping)
            and resume.get("request_id") == effect_id
            and isinstance(resume.get("continuation_id"), str)
            and bool(resume.get("continuation_id"))
            and sorted(resume.get("resource_ids", [])) == expected_resources
            and resume.get("status") in {"resumed", "partial", "pending", "vetoed"})
        probe_safe = (isinstance(probe, Mapping) and probe.get("cleanup_verified") is True
                      and probe.get("cgroup_removed") is True)
        successful_run = (isinstance(build, Mapping) and build.get("status") == "ok"
                          and build.get("cleanup_verified") is True
                          and build.get("cgroup_removed") is True
                          and isinstance(run, Mapping) and run.get("status") == "ok"
                          and run.get("cleanup_verified") is True
                          and run.get("cgroup_removed") is True)
        failed_build = (receipt.get("status") == "experiment_failed"
                        and isinstance(build, Mapping)
                        and build.get("status") not in {None, "ok"}
                        and build.get("cleanup_verified") is True
                        and build.get("cgroup_removed") is True
                        and run is None)
        controller = receipt.get("controller")
        no_effect_receipt = receipt.get("no_effect_receipt")
        no_foreground_proof = (
            receipt.get("payload_started") is False
            and receipt.get("no_foreground_effect") is True
            and isinstance(no_effect_receipt, Mapping)
            and no_effect_receipt.get("format") == "PC-GPU-LAB-NO-FOREGROUND-EFFECT/1"
            and no_effect_receipt.get("effect_id") == effect_id
            and no_effect_receipt.get("controller_returned") is True
            and no_effect_receipt.get("payload_started") is False
            and no_effect_receipt.get("lease_receipt") is None
            and no_effect_receipt.get("native_owner_claimed") is False
            and receipt.get("foreground_terminal") is None
            and run is None and probe in (None, {}))
        pre_admission_build_failure = (
            receipt.get("status") == "not_started"
            and receipt.get("phase") == "build"
            and isinstance(build, Mapping) and build.get("phase") == "build"
            and build.get("status") not in {None, "ok"}
            and build.get("cleanup_verified") is True and build.get("cgroup_removed") is True
            and isinstance(controller, Mapping)
            and controller.get("code") == "foreground_build_failed"
            and controller.get("returned") is True
            and controller.get("admission_started") is False
            and isinstance(no_effect_receipt, Mapping)
            and no_effect_receipt.get("controller_code") == "foreground_build_failed"
            and no_foreground_proof)
        no_payload_failure_codes = {
            "foreground_resource_contention", "foreign_gpu_activity", "gpu_not_quiescent",
        }
        pre_payload_admission_failure = (
            receipt.get("status") == "not_started"
            and receipt.get("phase") == "admission"
            and isinstance(build, Mapping) and build.get("phase") == "build"
            and build.get("status") == "ok"
            and build.get("cleanup_verified") is True and build.get("cgroup_removed") is True
            and isinstance(controller, Mapping)
            and controller.get("code") in no_payload_failure_codes
            and controller.get("returned") is True
            and controller.get("admission_started") is True
            and isinstance(no_effect_receipt, Mapping)
            and no_effect_receipt.get("controller_code") == controller.get("code")
            and no_foreground_proof)
        terminal_safe = (isinstance(terminal, Mapping) and terminal.get("verified") is True
            and terminal.get("state") == "released" and terminal.get("resources") == []
            and terminal.get("gpu_uuids") == list(scope.gpu_uuids))
        physical_outcome_safe = (
            ((receipt.get("status") == "completed" and successful_run) or failed_build)
            and probe_safe and terminal_safe
        ) or pre_admission_build_failure or pre_payload_admission_failure
        valid = (
            receipt.get("format") == "PC-GPU-LAB-RESULT/1"
            and physical_outcome_safe
            and receipt.get("effect_id") == effect_id
            and receipt.get("scope_project") == scope.project
            and receipt.get("gpu_uuids") == list(scope.gpu_uuids)
            and receipt.get("cleanup_verified") is True
            and resume_bound
            and receipt.get("proposal_artifacts") == expected_artifacts
        )
        if not valid:
            raise LabError("GPU executor receipt lacks matching foreground cleanup or artifact proof")
        receipt["continuation_ready"] = resume.get("status") == "resumed"

    def cancel(self, operator: LabOperator, session_id: str) -> dict[str, Any]:
        """Request cooperative cancellation; the contained runner owns cleanup."""
        self.lab._require_operator(operator)
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,run_owner FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise LabError("LAB session does not exist")
            if row["state"] in {"completed", "cancelled", "budget_exhausted", "unknown"}:
                return {"session_id": session_id, "state": row["state"], "cancel_requested": False}
            next_state = "cancelled" if row["state"] != "running" else "running"
            db.execute("UPDATE lab_sessions SET cancel_requested=1,state=?,updated_at=? WHERE session_id=?",
                       (next_state, now, session_id))
            self._event(db, session_id, "cancel_requested", {}, now)
            db.commit()
            return {"session_id": session_id, "state": next_state,
                    "cancel_requested": True, "cleanup_pending": bool(row["run_owner"])}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def status(self, operator: LabOperator, session_id: str | None = None) -> dict[str, Any]:
        self.lab._require_operator(operator)
        db = self.lab._open(create=False)
        if db is None:
            return {"sessions": []}
        try:
            rows = (db.execute("SELECT * FROM lab_sessions ORDER BY created_at,session_id").fetchall()
                    if session_id is None else
                    db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchall())
            sessions = []
            for row in rows:
                scope = _scope_from_json(row["scope_json"])
                effects = db.execute("SELECT * FROM lab_session_effects WHERE session_id=? ORDER BY ordinal",
                                     (row["session_id"],)).fetchall()
                proposals = db.execute("SELECT proposal_id,ordinal,source_identity,proposal_sha256,created_at,done "
                    "FROM lab_session_proposals WHERE session_id=? ORDER BY ordinal",
                    (row["session_id"],)).fetchall()
                sessions.append({"session_id": row["session_id"], "project": row["project"],
                    "repository": row["repository"], "goal": scope.goal, "state": row["state"],
                    "deadline": row["deadline"], "remaining_seconds": (max(0, row["deadline"] - self.clock())
                        if row["deadline"] is not None else None),
                    "experiment_count": row["experiment_count"], "max_experiments": scope.max_experiments,
                    "planning_count": row["planning_count"], "cancel_requested": bool(row["cancel_requested"]),
                    "source_identity": json.loads(row["preview_identity"]),
                    "proposals": [dict(item) | {"source_identity": json.loads(item["source_identity"])} for item in proposals],
                    "effects": [dict(item) | {"receipt": json.loads(item["receipt_json"])
                        if item["receipt_json"] else None,
                        "argv": json.loads(item["argv"]),
                        "process_identity": json.loads(item["process_identity"])
                        if item["process_identity"] else None} for item in effects]})
            return {"sessions": sessions}
        finally:
            db.close()

    def _claim(self, operator: LabOperator, session_id: str, owner: str, *, resume: bool) -> sqlite3.Row:
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise LabError("LAB session does not exist")
            self._assert_frozen_registration(row)
            state = row["state"]
            if state == "running":
                if self._run_owner_alive(row):
                    raise LabError("LAB session is already running")
                pending = db.execute("SELECT effect_id FROM lab_session_effects WHERE session_id=? "
                                     "AND state IN ('intent','running')", (session_id,)).fetchone()
                if pending:
                    db.execute("UPDATE lab_session_effects SET state='unknown',error='interrupted_effect_not_replayed' "
                               "WHERE effect_id=?", (pending["effect_id"],))
                    db.execute("UPDATE lab_sessions SET state='unknown',run_owner=NULL,run_pid=NULL,run_start_ticks=NULL,updated_at=? "
                               "WHERE session_id=?", (now, session_id))
                    db.commit()
                    raise LabError("previous LAB effect is ambiguous and will not be replayed")
                state = "paused"
                db.execute("UPDATE lab_sessions SET state='paused',run_owner=NULL,run_pid=NULL,run_start_ticks=NULL "
                           "WHERE session_id=?", (session_id,))
            if resume:
                if state not in {"paused", "authorized"}:
                    raise LabError("only an authorized or safely paused LAB session can resume")
            elif state != "authorized":
                raise LabError("LAB session must be authorized before run")
            if row["cancel_requested"]:
                raise LabError("cancelled LAB session cannot resume")
            if row["deadline"] is None or now >= row["deadline"]:
                db.execute("UPDATE lab_sessions SET state='budget_exhausted',updated_at=? WHERE session_id=?",
                           (now, session_id))
                db.commit()
                raise LabError("LAB authorization deadline has expired")
            start_ticks = lab_runner._process_start_time(os.getpid()) or 0
            db.execute("UPDATE lab_sessions SET state='running',run_owner=?,run_pid=?,run_start_ticks=?,updated_at=? "
                       "WHERE session_id=?", (owner, os.getpid(), start_ticks, now, session_id))
            self._event(db, session_id, "run_started", {"owner": owner, "resume": resume}, now)
            claimed = db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
            db.commit()
            return claimed
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _capture(self, scope: LabScope, destination: Path,
                 frozen_row: sqlite3.Row | None = None) -> _Capture:
        project = self.lab.projects[scope.project]
        if frozen_row is not None:
            self._assert_frozen_registration(frozen_row)
        try:
            snapshot = Snapshot.capture(Path(project.root).resolve(strict=True), scope.source_paths,
                                        destination, base_revision="HEAD")
        except SnapshotError:
            raise
        manifest_path = snapshot.destination / ".lab-snapshot.json"
        manifest_sha = _sha256_file(manifest_path)
        manifest = _verify_snapshot(snapshot.destination, manifest_sha)
        source_values = []
        total = 0
        for entry in manifest["files"]:
            path = snapshot.destination.joinpath(*PurePosixPath(entry["path"]).parts)
            raw = path.read_bytes()
            total += len(raw)
            if total > MAX_PLANNER_SOURCE_BYTES:
                raise LabError("selected text for planner exceeds the 400 KiB context bound")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            source_values.append({"path": entry["path"], "sha256": entry["sha256"],
                                  "size": entry["size"], "text": text})
        identity = {"head_commit": snapshot.head_commit,
                    "status_fingerprint": snapshot.status_fingerprint,
                    "manifest_sha256": manifest_sha}
        return _Capture(snapshot.destination, manifest, manifest_sha, identity, tuple(source_values))

    def _assert_frozen_registration(self, row: sqlite3.Row) -> None:
        """Fail closed if the trusted project mapping changed after preview."""
        project = self.lab.projects.get(str(row["project"]))
        if project is None:
            raise LabError("frozen LAB project registration is no longer available")
        try:
            current_root = str(Path(project.root).resolve(strict=True))
        except OSError as exc:
            raise LabError("frozen LAB project root is no longer available") from exc
        if current_root != str(row["source_root"]) or project.repository != str(row["repository"]):
            raise LabError("LAB project registration changed after preview; refusing to continue")

    @staticmethod
    def _assert_persisted_proposal(persisted: sqlite3.Row | None, session_id: str,
                                   proposal_id: str, public_json: str, public_digest: str,
                                   capture: _Capture, proposal_root: Path) -> None:
        if persisted is None:
            raise LabError("durable LAB proposal is missing before effect intent")
        stored_json = str(persisted["proposal_json"])
        try:
            canonical_stored = _dump(json.loads(stored_json))
            stored_identity = _dump(json.loads(str(persisted["source_identity"])))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LabError("durable LAB proposal record is malformed") from exc
        stored_digest = hashlib.sha256(canonical_stored.encode("utf-8")).hexdigest()
        if (str(persisted["session_id"]) != session_id
                or str(persisted["proposal_id"]) != proposal_id
                or canonical_stored != public_json
                or stored_identity != _dump(capture.identity)
                or str(persisted["proposal_sha256"]) != stored_digest
                or stored_digest != public_digest
                or str(persisted["proposal_root"]) != str(proposal_root)):
            raise LabError("durable LAB proposal changed after validation; refusing effect")

    def _assert_proposal_still_persisted(self, session_id: str, proposal_id: str,
                                         public_json: str, public_digest: str,
                                         capture: _Capture, proposal_root: Path) -> None:
        db = self.lab._open(create=False)
        if db is None:
            raise LabError("durable LAB proposal journal disappeared before effect")
        try:
            row = db.execute("SELECT * FROM lab_session_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
            self._assert_persisted_proposal(row, session_id, proposal_id, public_json,
                                            public_digest, capture, proposal_root)
        finally:
            db.close()

    def _planner_input(self, session_id: str, scope: LabScope, capture: _Capture,
                       deadline: float, row: sqlite3.Row) -> dict[str, Any]:
        db = self.lab._open(create=False)
        assert db is not None
        try:
            proposals = db.execute("SELECT proposal_id,proposal_json,proposal_sha256 "
                "FROM lab_session_proposals WHERE session_id=? ORDER BY ordinal",
                (session_id,)).fetchall()
            receipts = db.execute("SELECT effect_id,receipt_json FROM lab_session_effects "
                "WHERE session_id=? AND receipt_json IS NOT NULL ORDER BY ordinal",
                (session_id,)).fetchall()
            compact_proposals = []
            for item in proposals:
                proposal = json.loads(item["proposal_json"])
                proposal["artifacts"] = [{"path": artifact["path"],
                    "sha256": hashlib.sha256(artifact["content"].encode("utf-8")).hexdigest(),
                    "bytes": len(artifact["content"].encode("utf-8"))}
                    for artifact in proposal.get("artifacts", [])]
                compact_proposals.append({"proposal_id": item["proposal_id"],
                    "proposal": proposal, "sha256": item["proposal_sha256"]})
            compact_receipts = []
            for item in receipts:
                receipt = json.loads(item["receipt_json"])
                for key in ("stdout", "stderr"):
                    if isinstance(receipt.get(key), str):
                        output = receipt.pop(key)
                        encoded = output.encode("utf-8")
                        receipt[key + "_excerpt"] = _bounded_text(encoded, 4096)
                        receipt[key + "_bytes"] = len(encoded)
                        receipt[key + "_sha256"] = hashlib.sha256(encoded).hexdigest()
                compact_receipts.append({"effect_id": item["effect_id"], "receipt": receipt})
            request = {"session_id": session_id, "goal": scope.goal,
                "tools": list(scope.tools), "gpu_uuids": list(scope.gpu_uuids),
                "toolchain_root": scope.toolchain_root, "artifact_mount": "/proposal",
                "source_identity": capture.identity,
                "sources": list(capture.sources),
                "prior_proposals": compact_proposals,
                "receipts": compact_receipts,
                "remaining_seconds": max(0.0, deadline - float(self.clock())),
                "deadline_epoch": deadline,
                "remaining_experiments": max(0, scope.max_experiments-int(row["experiment_count"]))}
            if len(_dump(request).encode("utf-8")) > MAX_PLANNER_PAYLOAD_BYTES:
                raise LabError("planner request exceeds the 600 KiB context bound")
            # Internal cooperative control; adapters must remove it before
            # serializing the request into model-visible prompt text.
            request["cancelled"] = lambda: self._cancel_requested(session_id)
            return request
        finally:
            db.close()

    def _persist_proposal(self, session_id: str, owner: str, proposal: LabProposal,
                          capture: _Capture, scope: LabScope) -> tuple[str, Path]:
        proposal_id = "lab_proposal_" + uuid.uuid4().hex
        root = self.lab.state_root / "sessions" / session_id / "proposals" / proposal_id
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root.parent, 0o700)
        proposal_root = write_artifacts(proposal, root)
        public = proposal.public()
        digest = hashlib.sha256(_dump(public).encode("utf-8")).hexdigest()
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,run_owner,planning_count FROM lab_sessions WHERE session_id=?",
                             (session_id,)).fetchone()
            if row is None or row["state"] != "running" or row["run_owner"] != owner:
                raise LabError("LAB session ownership lost while recording proposal")
            ordinal = int(row["planning_count"])
            db.execute("INSERT INTO lab_session_proposals(proposal_id,session_id,ordinal,proposal_json,proposal_sha256," 
                       "source_identity,proposal_root,done,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (proposal_id, session_id, ordinal, _dump(public), digest,
                        _dump(capture.identity), str(proposal_root), int(proposal.done), now))
            self._event(db, session_id, "proposal_recorded", {"proposal_id": proposal_id,
                "proposal_sha256": digest, "source_identity": capture.identity,
                "done": proposal.done, "argv": list(proposal.argv)}, now)
            db.commit()
        except BaseException:
            db.rollback()
            os.chmod(proposal_root, 0o700)
            shutil.rmtree(proposal_root, ignore_errors=True)
            raise
        finally:
            db.close()
        return proposal_id, proposal_root

    def _increment_planning(self, session_id: str, owner: str) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE lab_sessions SET planning_count=planning_count+1,updated_at=? "
                       "WHERE session_id=? AND run_owner=? AND state='running'", (now, session_id, owner))
            if db.total_changes == 0:
                raise LabError("LAB run ownership lost before planning")
            self._event(db, session_id, "planning_intent", {}, now)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _store_receipt(self, session_id: str, proposal_id: str, receipt: Mapping[str, Any]) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT effect_id FROM lab_session_effects WHERE session_id=? AND proposal_id=? "
                             "ORDER BY ordinal DESC LIMIT 1", (session_id, proposal_id)).fetchone()
            if row is None:
                raise LabError("proposal effect intent is missing")
            db.execute("UPDATE lab_session_effects SET receipt_json=?,finished_at=? WHERE effect_id=?",
                       (_dump(dict(receipt)), now, row["effect_id"]))
            db.commit()
        finally:
            db.close()

    def _finish_effect(self, session_id: str, effect_id: str, state: str,
                       receipt: Mapping[str, Any]) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE lab_session_effects SET state=?,receipt_json=?,finished_at=?,error=? "
                       "WHERE effect_id=? AND session_id=?",
                       (state, _dump(dict(receipt)), now,
                        str(receipt.get("error", ""))[:1024] or None, effect_id, session_id))
            db.commit()
        finally:
            db.close()

    def _mark_effect_started(self, session_id: str, effect_id: str, identity: Any) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM lab_session_effects WHERE effect_id=? AND session_id=?",
                             (effect_id, session_id)).fetchone()
            if row is None or row["state"] != "intent":
                raise LabError("effect intent is unavailable for process ownership")
            identity_value = asdict(identity) if hasattr(identity, "__dataclass_fields__") else dict(identity)
            db.execute("UPDATE lab_session_effects SET state='running',started_at=?,process_identity=? "
                       "WHERE effect_id=?", (float(self.clock()), _dump(identity_value), effect_id))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _finish_session(self, session_id: str, owner: str, state: str, reason: str,
                        **extra: Any) -> dict[str, Any]:
        db = self.lab._open(create=True)
        assert db is not None
        now = float(self.clock())
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE lab_sessions SET state=?,run_owner=NULL,run_pid=NULL,run_start_ticks=NULL,updated_at=? "
                       "WHERE session_id=? AND run_owner=?", (state, now, session_id, owner))
            self._event(db, session_id, "session_finished", {"state": state, "reason": reason,
                **{key: value for key, value in extra.items() if key != "receipt"}}, now)
            db.commit()
        finally:
            db.close()
        result = {"session_id": session_id, "state": state, "reason": reason,
                  "cleanup_verified": self._all_cleanup_verified(session_id), **extra}
        result.update(self._remaining(session_id))
        return result

    def _remaining(self, session_id: str) -> dict[str, Any]:
        row = self._session_row(session_id)
        if row is None:
            return {}
        scope = _scope_from_json(row["scope_json"])
        return {"deadline": row["deadline"],
                "remaining_seconds": max(0.0, row["deadline"] - float(self.clock()))
                    if row["deadline"] is not None else None,
                "experiment_count": row["experiment_count"],
                "remaining_experiments": max(0, scope.max_experiments-int(row["experiment_count"]))}

    def _all_cleanup_verified(self, session_id: str) -> bool:
        db = self.lab._open(create=False)
        if db is None:
            return True
        try:
            row = db.execute("SELECT COUNT(*) FROM lab_session_effects WHERE session_id=? "
                             "AND state NOT IN ('completed')", (session_id,)).fetchone()
            return int(row[0]) == 0
        finally:
            db.close()

    def _mark_unknown_or_paused(self, session_id: str, owner: str, error: BaseException) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute("SELECT effect_id FROM lab_session_effects WHERE session_id=? "
                                 "AND state IN ('intent','running')", (session_id,)).fetchone()
            state = "unknown" if pending else "paused"
            if pending:
                db.execute("UPDATE lab_session_effects SET state='unknown',error=? WHERE effect_id=?",
                           (str(error)[:1024], pending["effect_id"]))
            db.execute("UPDATE lab_sessions SET state=?,run_owner=NULL,run_pid=NULL,run_start_ticks=NULL,updated_at=? "
                       "WHERE session_id=? AND run_owner=?",
                       (state, float(self.clock()), session_id, owner))
            self._event(db, session_id, "run_interrupted", {"state": state, "error": str(error)[:1024]},
                        float(self.clock()))
            db.commit()
        finally:
            db.close()

    def _assert_running(self, session_id: str, owner: str) -> None:
        row = self._session_row(session_id)
        if row is None or row["state"] != "running" or row["run_owner"] != owner:
            raise LabError("LAB run was cancelled or ownership was lost")

    def _cancel_requested(self, session_id: str) -> bool:
        row = self._session_row(session_id)
        return row is None or bool(row["cancel_requested"])

    def _session_row(self, session_id: str) -> sqlite3.Row | None:
        db = self.lab._open(create=False)
        if db is None:
            return None
        try:
            return db.execute("SELECT * FROM lab_sessions WHERE session_id=?", (session_id,)).fetchone()
        finally:
            db.close()

    @staticmethod
    def _run_owner_alive(row: sqlite3.Row) -> bool:
        pid = row["run_pid"]
        start_ticks = row["run_start_ticks"]
        if not pid or not start_ticks:
            return False
        try:
            return lab_runner._process_start_time(int(pid)) == int(start_ticks)
        except (OSError, TypeError, ValueError):
            return False

    def _new_snapshot_path(self, session_id: str) -> Path:
        path = self.lab.state_root / "sessions" / session_id / "snapshots" / uuid.uuid4().hex
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _proposal_hash(self, proposal_id: str) -> str | None:
        db = self.lab._open(create=False)
        if db is None:
            return None
        try:
            row = db.execute("SELECT proposal_sha256 FROM lab_session_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
            return str(row[0]) if row else None
        finally:
            db.close()

    def _record_event(self, session_id: str, kind: str, payload: Mapping[str, Any]) -> None:
        db = self.lab._open(create=True)
        assert db is not None
        try:
            self._event(db, session_id, kind, payload, float(self.clock()))
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, session_id: str, kind: str,
               payload: Mapping[str, Any], now: float) -> None:
        db.execute("INSERT INTO lab_session_events(event_id,session_id,kind,payload,created_at) VALUES(?,?,?,?,?)",
                   ("lab_event_" + uuid.uuid4().hex, session_id, kind, _dump(dict(payload)), now))

    @staticmethod
    def _validate_scope(scope: LabScope) -> None:
        if not isinstance(scope, LabScope):
            raise LabError("typed LAB scope is required")
        if (not isinstance(scope.project, str) or not scope.project.strip()
                or not isinstance(scope.goal, str) or not scope.goal.strip()
                or len(scope.goal.encode("utf-8")) > 8192 or "\0" in scope.goal):
            raise LabError("scope project and goal must be bounded non-empty text")
        if not isinstance(scope.source_paths, tuple) or not scope.source_paths:
            raise LabError("scope requires explicit source paths")
        if not isinstance(scope.tools, tuple) or not scope.tools or len(scope.tools) > 16:
            raise LabError("scope requires a bounded explicit tool allowlist")
        if any(not isinstance(tool, str) or not tool or "/" in tool or "\\" in tool or "\0" in tool
               for tool in scope.tools) or len(set(scope.tools)) != len(scope.tools):
            raise LabError("scope tools must be unique executable names")
        if (isinstance(scope.wall_seconds, bool) or not isinstance(scope.wall_seconds, int)
                or not 1 <= scope.wall_seconds <= MAX_WALL_SECONDS):
            raise LabError("scope wall budget must be between 1 and 600 seconds")
        if (isinstance(scope.max_experiments, bool) or not isinstance(scope.max_experiments, int)
                or not 1 <= scope.max_experiments <= MAX_EXPERIMENTS):
            raise LabError("scope experiment budget must be between 1 and 6")
        if not isinstance(scope.gpu_uuids, tuple) or len(set(scope.gpu_uuids)) != len(scope.gpu_uuids):
            raise LabError("GPU UUIDs must be a unique tuple")
        if any(not isinstance(value, str) or not re.fullmatch(r"GPU-[0-9a-fA-F-]{16,64}", value)
               for value in scope.gpu_uuids):
            raise LabError("GPU access requires exact GPU UUIDs")
        if (bool(scope.gpu_uuids) != ("cuda" in scope.tools)):
            raise LabError("GPU scopes must include the cuda tool and CPU scopes must omit it")
        if scope.toolchain_root is not None:
            if not isinstance(scope.toolchain_root, str):
                raise LabError("toolchain_root must be a registered absolute path string")
            path = Path(scope.toolchain_root)
            if path.is_symlink() or not path.is_absolute() or not path.is_dir():
                raise LabError("toolchain_root must be an existing absolute directory")
        # Reuse the one-shot selector's path and argv validators without creating
        # any record or capture during preview validation.
        probe = LabSelection(scope.project, scope.source_paths, "scope goal", ("python3",),
                             "preview", "source identity", "finite scope")
        LabService._validate_selection(probe)


def _scope_from_json(value: str) -> LabScope:
    data = json.loads(value)
    data["source_paths"] = tuple(data["source_paths"])
    data["tools"] = tuple(data["tools"])
    data["gpu_uuids"] = tuple(data["gpu_uuids"])
    return LabScope(**data)


def _bounded_text(value: bytes, limit: int = 64 * 1024) -> str:
    return value[:limit].decode("utf-8", errors="replace")
