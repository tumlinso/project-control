"""Opaque workflow capabilities backed by authoritative todo SQLite state."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path

from ..config import utc_now
from ..models import TodoError
from .foundation import CapabilityLineage, RUN_LEVEL_ACTIONS, WorkflowDatabase


FIRST_CLASS_PREFIX = "wfc_"
CHILD_PREFIX = "wcc_"
DEFAULT_TTL_SECONDS = 24 * 60 * 60
CHILD_TTL_SECONDS = 6 * 60 * 60


def capability_hash(handle: str) -> str:
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()


def _new_handle(capability_class: str) -> str:
    prefix = FIRST_CLASS_PREFIX if capability_class == "first_class" else CHILD_PREFIX
    return prefix + secrets.token_urlsafe(32)


def _expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AuthorizedCapability:
    id: str
    lineage: CapabilityLineage
    expires_at: str
    repository_root: Path | None = None

    @property
    def capability_class(self) -> str:
        return self.lineage.capability_class


class WorkflowCapabilityStore:
    """Hash-only capability records; the project database is the sole authority."""

    def __init__(self, db: WorkflowDatabase):
        self.db = db

    def issue_first_class(
        self,
        lineage: CapabilityLineage,
        *,
        actor_session_id: str | None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        retire_prior: bool = True,
    ) -> tuple[str, AuthorizedCapability, int]:
        lineage.validate()
        if lineage.capability_class != "first_class":
            raise TodoError("invalid_first_class_capability", "First-class issue requires first-class lineage")
        return self._issue(lineage, actor_session_id, ttl_seconds, retire_prior)

    def issue_child(
        self,
        lineage: CapabilityLineage,
        *,
        parent_handle: str,
        actor_session_id: str | None,
        ttl_seconds: int = CHILD_TTL_SECONDS,
    ) -> tuple[str, AuthorizedCapability, int]:
        parent = self.resolve(parent_handle, required_operation="delegate_task", expected_class="first_class")
        lineage.validate(parent.lineage)
        if lineage.capability_class != "child" or lineage.parent_capability_id != parent.id:
            raise TodoError("invalid_child_capability", "Child must derive from the resolved parent capability")
        return self._issue(lineage, actor_session_id, ttl_seconds, False)

    def _issue(
        self,
        lineage: CapabilityLineage,
        actor_session_id: str | None,
        ttl_seconds: int,
        retire_prior: bool,
    ) -> tuple[str, AuthorizedCapability, int]:
        if ttl_seconds < 1:
            raise TodoError("invalid_capability_expiry", "Capability expiry must be positive")
        handle = _new_handle(lineage.capability_class)
        capability_id = str(uuid.uuid4())
        expires_at = _expiry(ttl_seconds)

        def operation(conn: Any, revision: int) -> AuthorizedCapability:
            return self._insert_in_transaction(
                conn,
                lineage=lineage,
                handle=handle,
                capability_id=capability_id,
                expires_at=expires_at,
                retire_prior=retire_prior,
            )

        authorized, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_capability",
            entity_id=capability_id,
            event_type="workflow_capability_issued",
            payload={"capability_class": lineage.capability_class, "task_id": lineage.task_id},
            operation=operation,
        )
        return handle, authorized, revision

    def stage_first_class(
        self,
        conn: Any,
        *,
        lineage: CapabilityLineage,
        revision: int,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        retire_prior: bool = True,
    ) -> tuple[str, AuthorizedCapability]:
        """Insert during the caller's claim/dispatch transaction."""
        lineage.validate()
        if lineage.capability_class != "first_class" or ttl_seconds < 1:
            raise TodoError("invalid_first_class_capability", "First-class lineage or expiry is invalid")
        handle = _new_handle("first_class")
        authorized = self._insert_in_transaction(
            conn,
            lineage=lineage,
            handle=handle,
            capability_id=str(uuid.uuid4()),
            expires_at=_expiry(ttl_seconds),
            retire_prior=retire_prior,
        )
        return handle, authorized

    def stage_child(
        self,
        conn: Any,
        *,
        lineage: CapabilityLineage,
        parent: AuthorizedCapability,
        revision: int,
        ttl_seconds: int = CHILD_TTL_SECONDS,
    ) -> tuple[str, AuthorizedCapability]:
        """Insert during the caller's child-execution creation transaction."""
        lineage.validate(parent.lineage)
        if lineage.capability_class != "child" or lineage.parent_capability_id != parent.id or ttl_seconds < 1:
            raise TodoError("invalid_child_capability", "Child lineage or expiry is invalid")
        handle = _new_handle("child")
        authorized = self._insert_in_transaction(
            conn,
            lineage=lineage,
            handle=handle,
            capability_id=str(uuid.uuid4()),
            expires_at=_expiry(ttl_seconds),
            retire_prior=False,
        )
        return handle, authorized

    def _insert_in_transaction(
        self,
        conn: Any,
        *,
        lineage: CapabilityLineage,
        handle: str,
        capability_id: str,
        expires_at: str,
        retire_prior: bool,
    ) -> AuthorizedCapability:
        self._validate_lineage_rows(conn, lineage)
        incarnation = lineage.incarnation
        if retire_prior and lineage.capability_class == "first_class":
            row = conn.execute(
                "SELECT COALESCE(MAX(incarnation),0) AS incarnation FROM workflow_capabilities "
                "WHERE project_uuid=? AND repository_identity=? AND session_id=? AND claim_id=? "
                "AND run_id=? AND lane_id=? AND task_id=?",
                (
                    lineage.project_uuid, lineage.repository_identity, lineage.session_id,
                    lineage.claim_id, lineage.run_id, lineage.lane_id, lineage.task_id,
                ),
            ).fetchone()
            incarnation = max(incarnation, int(row["incarnation"]) + 1)
            conn.execute(
                "UPDATE workflow_capabilities SET state='retired',revoked_at=? WHERE "
                "project_uuid=? AND repository_identity=? AND session_id=? AND claim_id=? "
                "AND run_id=? AND lane_id=? AND task_id=? AND state='active'",
                (
                    utc_now(), lineage.project_uuid, lineage.repository_identity,
                    lineage.session_id, lineage.claim_id, lineage.run_id,
                    lineage.lane_id, lineage.task_id,
                ),
            )
        stored = CapabilityLineage(**{**lineage.__dict__, "incarnation": incarnation})
        conn.execute(
            "INSERT INTO workflow_capabilities("
            "id,token_hash,capability_class,project_uuid,repository_identity,session_id,claim_id,"
            "run_id,lane_id,role,task_id,parent_capability_id,child_execution_id,allowed_operations_json,"
            "incarnation,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                capability_id, capability_hash(handle), stored.capability_class,
                stored.project_uuid, stored.repository_identity, stored.session_id,
                stored.claim_id, stored.run_id, stored.lane_id, stored.role,
                stored.task_id, stored.parent_capability_id, stored.child_execution_id,
                json.dumps(sorted(stored.allowed_operations), separators=(",", ":")),
                stored.incarnation, "active", utc_now(), expires_at,
            ),
        )
        return AuthorizedCapability(capability_id, stored, expires_at)

    def resolve(
        self,
        handle: str,
        *,
        required_operation: str,
        expected_class: str | None = None,
    ) -> AuthorizedCapability:
        if not isinstance(handle, str) or not handle.startswith((FIRST_CLASS_PREFIX, CHILD_PREFIX)):
            raise TodoError("invalid_workflow_capability", "Capability is unknown, expired, or inactive")
        digest = capability_hash(handle)
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_capabilities WHERE token_hash=?", (digest,)
            ).fetchone()
            if row is None or row["state"] != "active" or str(row["expires_at"]) <= utc_now():
                raise TodoError("invalid_workflow_capability", "Capability is unknown, expired, or inactive")
            lineage = self._lineage(row)
            if expected_class is not None and lineage.capability_class != expected_class:
                raise TodoError("capability_class_forbidden", "Capability class cannot perform this operation")
            if required_operation not in lineage.allowed_operations:
                raise TodoError("capability_operation_forbidden", "Capability does not authorize this operation")
            if lineage.capability_class == "child" and (
                required_operation in RUN_LEVEL_ACTIONS
                or required_operation in {"next_task", "inspect_task", "finish_task", "claim_task"}
                or required_operation.startswith("coordinate:")
            ):
                raise TodoError("child_run_authority_forbidden", "Child capability has no first-class authority")
            self._validate_lineage_rows(conn, lineage)
            if lineage.capability_class == "child":
                parent_row = conn.execute(
                    "SELECT * FROM workflow_capabilities WHERE id=?", (lineage.parent_capability_id,)
                ).fetchone()
                if parent_row is not None and parent_row["state"] == "retired":
                    parent_row = conn.execute(
                        "SELECT * FROM workflow_capabilities WHERE capability_class='first_class' "
                        "AND project_uuid=? AND repository_identity=? AND session_id=? AND claim_id=? "
                        "AND task_id=? AND state='active' AND expires_at>? ORDER BY incarnation DESC LIMIT 1",
                        (
                            lineage.project_uuid,
                            lineage.repository_identity,
                            lineage.session_id,
                            lineage.claim_id,
                            lineage.task_id,
                            utc_now(),
                        ),
                    ).fetchone()
                if parent_row is None or parent_row["state"] != "active" or str(parent_row["expires_at"]) <= utc_now():
                    raise TodoError("invalid_parent_capability", "Parent capability is not active")
                parent = self._lineage(parent_row)
                lineage.validate(parent)
                self._validate_lineage_rows(conn, parent)
        return AuthorizedCapability(str(row["id"]), lineage, str(row["expires_at"]))

    def revoke(self, handle: str, *, actor_session_id: str | None, family: bool = False) -> int:
        digest = capability_hash(handle)

        def operation(conn: Any, revision: int) -> str:
            row = conn.execute("SELECT * FROM workflow_capabilities WHERE token_hash=?", (digest,)).fetchone()
            if row is None:
                return "already_absent"
            self.stage_revoke(conn, capability_id=str(row["id"]), family=family)
            return str(row["id"])

        _, revision = self.db.mutate(
            actor_session_id=actor_session_id,
            entity_type="workflow_capability",
            entity_id=None,
            event_type="workflow_capability_revoked",
            payload={"family": family},
            operation=operation,
        )
        return revision

    def stage_revoke(self, conn: Any, *, capability_id: str, family: bool = False) -> None:
        """Revoke during the caller's completion/release transaction."""
        row = conn.execute("SELECT * FROM workflow_capabilities WHERE id=?", (capability_id,)).fetchone()
        if row is None:
            return
        now = utc_now()
        conn.execute(
            "UPDATE workflow_capabilities SET state='revoked',revoked_at=? WHERE id=? AND state IN ('active','retired')",
            (now, row["id"]),
        )
        if family and row["capability_class"] == "first_class":
            family_rows = conn.execute(
                "SELECT id FROM workflow_capabilities WHERE capability_class='first_class' "
                "AND project_uuid=? AND repository_identity=? AND session_id=? AND claim_id=? "
                "AND run_id=? AND lane_id=? AND task_id=?",
                (
                    row["project_uuid"], row["repository_identity"], row["session_id"],
                    row["claim_id"], row["run_id"], row["lane_id"], row["task_id"],
                ),
            ).fetchall()
            family_ids = [str(item["id"]) for item in family_rows]
            placeholders = ",".join("?" for _ in family_ids)
            conn.execute(
                f"UPDATE workflow_capabilities SET state='revoked',revoked_at=? WHERE "
                f"(id IN ({placeholders}) OR parent_capability_id IN ({placeholders})) "
                "AND state IN ('active','retired')",
                [now, *family_ids, *family_ids],
            )

    @staticmethod
    def _lineage(row: Any) -> CapabilityLineage:
        return CapabilityLineage(
            capability_class=str(row["capability_class"]),
            project_uuid=str(row["project_uuid"]),
            repository_identity=str(row["repository_identity"]),
            session_id=row["session_id"],
            claim_id=row["claim_id"],
            run_id=row["run_id"],
            lane_id=row["lane_id"],
            role=row["role"],
            task_id=str(row["task_id"]),
            allowed_operations=frozenset(json.loads(row["allowed_operations_json"])),
            incarnation=int(row["incarnation"]),
            parent_capability_id=row["parent_capability_id"],
            child_execution_id=row["child_execution_id"],
        )

    @staticmethod
    def _validate_lineage_rows(conn: Any, lineage: CapabilityLineage) -> None:
        project = conn.execute("SELECT value FROM meta WHERE key='project_uuid'").fetchone()
        if project is None or project["value"] != lineage.project_uuid:
            raise TodoError("capability_project_mismatch", "Capability project lineage is stale")
        claim = conn.execute(
            "SELECT task_id,session_id,state,expires_at FROM claims WHERE id=?", (lineage.claim_id,)
        ).fetchone()
        if claim is None or claim["state"] != "active" or str(claim["expires_at"]) <= utc_now():
            raise TodoError("capability_claim_inactive", "Capability claim is no longer active")
        if claim["task_id"] != lineage.task_id or claim["session_id"] != lineage.session_id:
            raise TodoError("capability_claim_mismatch", "Capability claim lineage changed")
        if lineage.capability_class == "first_class":
            lineage.validate()
            session = conn.execute("SELECT state FROM sessions WHERE id=?", (lineage.session_id,)).fetchone()
            lane = conn.execute(
                "SELECT run_id,role,state FROM workflow_lanes WHERE id=?", (lineage.lane_id,)
            ).fetchone()
            run = conn.execute("SELECT status FROM workflow_runs WHERE id=?", (lineage.run_id,)).fetchone()
            dispatch = conn.execute(
                "SELECT state FROM workflow_dispatches WHERE lane_id=? AND session_id=? AND claim_id=?",
                (lineage.lane_id, lineage.session_id, lineage.claim_id),
            ).fetchone()
            if session is None or session["state"] != "active":
                raise TodoError("capability_session_inactive", "Capability session is not active")
            if lane is None or lane["run_id"] != lineage.run_id or lane["role"] != lineage.role:
                raise TodoError("capability_lane_mismatch", "Capability lane or role lineage changed")
            if run is None or run["status"] != "active":
                raise TodoError("capability_run_inactive", "Capability run is not active")
            if dispatch is None or dispatch["state"] != "active":
                raise TodoError("capability_dispatch_inactive", "Capability dispatch is not active")
            return
        child = conn.execute(
            "SELECT parent_claim_id,task_id,state FROM child_executions WHERE id=?",
            (lineage.child_execution_id,),
        ).fetchone()
        if child is None or child["parent_claim_id"] != lineage.claim_id or child["task_id"] != lineage.task_id:
            raise TodoError("child_parent_claim_mismatch", "Child execution lineage changed")
        if child["state"] in {"failed", "cancelled", "rejected", "stale"}:
            raise TodoError("child_execution_inactive", "Child execution is no longer collectable")


def default_first_class_operations(role: str = "implementer") -> frozenset[str]:
    common = {
        "inspect_task",
        "finish_task",
        "collect_delegation",
        "coordinate:sync",
        "coordinate:message",
        "coordinate:answer",
        "coordinate:arrive",
        "coordinate:run_gates",
    }
    additions = {
        "coordinator": {
            "delegate_task", "coordinate:fork", "coordinate:publish_context",
            "coordinate:request_integration", "coordinate:accept_child", "coordinate:reject_child",
            "coordinate:bind_required_gates",
        },
        "implementer": {
            "delegate_task", "coordinate:publish_interface", "coordinate:request_integration",
            "coordinate:accept_child", "coordinate:reject_child", "coordinate:bind_required_gates",
        },
        "validator": set(),
        "integrator": {
            "coordinate:publish_interface", "coordinate:request_integration", "coordinate:accept_child",
            "coordinate:publish_context",
            "coordinate:reject_child",
        },
        "specialist": {
            "delegate_task", "coordinate:publish_interface", "coordinate:accept_child",
            "coordinate:reject_child",
        },
    }
    if role not in additions:
        raise TodoError("invalid_workflow_role", "Cannot derive operations for unknown role")
    if role in {"implementer", "specialist", "coordinator", "integrator"}:
        additions[role].add("coordinate:publish_project_context")
    return frozenset(common | additions[role])


def child_operations() -> frozenset[str]:
    return frozenset({"collect_delegation"})


class WorkflowCapabilityLocator:
    """Semantics-free XDG hint mapping a handle hash to its project database.

    Losing this cache is harmless: ``next_task`` can prove continuity from todo
    state and issue a replacement. Every lookup is revalidated by the
    authoritative per-project ``WorkflowCapabilityStore``.
    """

    def __init__(self, state_dir: str | Path | None = None):
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        self.root = Path(state_dir) if state_dir is not None else base / "coding-workflow" / "locators"

    def register(self, handle: str, repo_root: str | Path) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        target = self.root / capability_hash(handle)
        temporary = target.with_suffix(".tmp-" + secrets.token_hex(6))
        temporary.write_text(str(Path(repo_root).resolve()) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)

    def forget(self, handle: str) -> None:
        try:
            (self.root / capability_hash(handle)).unlink()
        except FileNotFoundError:
            pass

    def resolve(self, handle: str, *, required_operation: str, expected_class: str | None = None) -> AuthorizedCapability:
        if not isinstance(handle, str) or not handle.startswith((FIRST_CLASS_PREFIX, CHILD_PREFIX)):
            raise TodoError("invalid_workflow_capability", "Capability is unknown, expired, or inactive")
        try:
            repo = Path((self.root / capability_hash(handle)).read_text(encoding="utf-8").strip()).resolve()
        except (FileNotFoundError, OSError, ValueError):
            raise TodoError("invalid_workflow_capability", "Capability locator is absent; call next_task to resume") from None
        # Imports remain lazy so MCP startup does not open a repository or DB.
        from ..service import Service

        # A locator hint must never initialize or restore an unrelated
        # authority. Resolution is read-only; the kernel opens only this exact
        # proven authority after capability validation succeeds.
        service = Service(repo, read_only=True)
        capability = WorkflowCapabilityStore(service.db).resolve(
            handle, required_operation=required_operation, expected_class=expected_class
        )
        if capability.lineage.session_id is None:
            raise TodoError("invalid_workflow_capability", "Capability session provenance is absent")
        with service.db.read() as conn:
            session = conn.execute(
                "SELECT repo_root FROM sessions WHERE id=?", (capability.lineage.session_id,)
            ).fetchone()
        if session is None:
            raise TodoError("invalid_workflow_capability", "Capability session provenance is absent")
        exact_root = Path(str(session["repo_root"])).resolve()
        exact_service = Service(exact_root, read_only=True)
        # Import lazily to avoid the service/capability module cycle at startup.
        from .service import repository_identity
        if repository_identity(exact_service.paths.repo_root, str(exact_service.project["project_uuid"])) != capability.lineage.repository_identity:
            raise TodoError("repository_identity_mismatch", "Capability dispatch repository identity changed")
        return AuthorizedCapability(
            capability.id, capability.lineage, capability.expires_at, exact_service.paths.repo_root,
        )
