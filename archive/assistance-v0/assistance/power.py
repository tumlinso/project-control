"""Durable demand, quiet and inference-release policy for observer frames.

The policy shares the controller's private SQLite connection.  It owns no
database, thread, scheduler or backend.  Mutations are short SQL writes that
the caller commits with its normal JobService transaction; release requests
are returned as intents and must be delivered only after that transaction has
committed.  ``physical_state`` remains ``pending`` until a separate owner has
verified process cleanup and device accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import secrets
import sqlite3
from typing import Any, Mapping


_CONTROL_SEAL = object()
_UNCHANGED = object()
_ACTIONS = frozenset({
    "set_quiet", "set_automatic", "request_release", "resume",
    "set_focus_window", "dispatch_during_quiet", "dispatch_descendants",
    "dispatch_automatic",
})
_FRAME_CLASSES = frozenset({
    "public_root", "foreground", "private_child", "automatic_root", "opportunistic",
})
MAX_FOCUS_WINDOW_SECONDS = 24 * 60 * 60
MIN_FOCUS_WINDOW_SECONDS = 1.0


class PowerPolicyError(ValueError):
    """A power-policy request is malformed or lacks trusted authority."""


def _finite_timestamp(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


class OperatorPermission:
    """Opaque authority minted by the trusted local operator control path."""

    __slots__ = ("_seal", "action")

    def __init__(self, seal: object, action: str):
        if seal is not _CONTROL_SEAL or action not in _ACTIONS:
            raise PowerPolicyError("trusted_operator_permission_required")
        self._seal = seal
        self.action = action


class TrustedOperatorControl:
    """Typed, process-local control capability; text and roles cannot create it."""

    __slots__ = ("_seal",)

    def __init__(self, seal: object):
        if seal is not _CONTROL_SEAL:
            raise PowerPolicyError("trusted_operator_control_required")
        self._seal = seal

    def permission(self, action: str) -> OperatorPermission:
        if action not in _ACTIONS:
            raise PowerPolicyError("operator_permission_unknown")
        return OperatorPermission(_CONTROL_SEAL, action)


class DemandOrigin:
    """Trusted classification decoded from a persisted frame root record."""

    __slots__ = ("root_id", "kind", "_seal")

    def __init__(self, root_id: str, kind: str, seal: object):
        if seal is not _CONTROL_SEAL or kind not in {"public_demand", "automatic"}:
            raise PowerPolicyError("persisted_demand_origin_required")
        self.root_id = root_id
        self.kind = kind
        self._seal = seal


def trusted_operator_control() -> TrustedOperatorControl:
    """Mint a control capability for trusted application wiring only.

    Public request text, model output, user role names and source documents are
    never passed here.  The caller should keep this object in the operator
    control adapter, separate from inquiry/source data.
    """
    return TrustedOperatorControl(_CONTROL_SEAL)


@dataclass(frozen=True)
class ReleaseIntent:
    request_id: str
    created_at: float
    declared_end: float | None
    reason: str


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    reason: str
    priority: str
    automatic_enabled: bool
    quiet_active: bool
    release_veto_active: bool


class PowerPolicy:
    """Read and mutate one durable power-policy row on the supplied DB handle."""

    def __init__(self, db: sqlite3.Connection, *, clock=lambda: 0.0):
        self.db = db
        self.clock = clock
        self.db.row_factory = sqlite3.Row
        self.initialize(self.db)

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        """Create the additive singleton table beside JobService tables."""
        db.execute("""CREATE TABLE IF NOT EXISTS pa1_power_state(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            automatic_enabled INTEGER NOT NULL DEFAULT 0,
            automatic_focus TEXT,
            automatic_project TEXT,
            automatic_starts REAL,
            automatic_until REAL,
            quiet_until REAL,
            quiet_focus TEXT,
            quiet_reason TEXT,
            release_veto INTEGER NOT NULL DEFAULT 0,
            release_until REAL,
            release_request_id TEXT,
            release_created_at REAL,
            release_reason TEXT,
            physical_state TEXT NOT NULL DEFAULT 'not_requested',
            release_verified_at REAL,
            release_verified_sessions INTEGER NOT NULL DEFAULT 0,
            release_verified_proof_digest TEXT,
            release_verified_targets_digest TEXT,
            updated REAL NOT NULL DEFAULT 0)""")
        db.execute("INSERT OR IGNORE INTO pa1_power_state(singleton) VALUES(1)")
        # Additive migration for early source-mode databases created before the
        # focus-window contract was finalized.
        columns = {row[1] for row in db.execute("PRAGMA table_info(pa1_power_state)")}
        for name, declaration in (
            ("automatic_focus", "TEXT"), ("automatic_project", "TEXT"),
            ("automatic_starts", "REAL"), ("automatic_until", "REAL"),
            ("release_verified_at", "REAL"), ("release_verified_sessions", "INTEGER NOT NULL DEFAULT 0"),
            ("release_verified_proof_digest", "TEXT"),
            ("release_verified_targets_digest", "TEXT"),
            ("release_created_at", "REAL"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE pa1_power_state ADD COLUMN {name} {declaration}")

    def _row(self) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM pa1_power_state WHERE singleton=1").fetchone()
        if row is None:
            raise RuntimeError("power_policy_state_missing")
        return row

    def root_demand_origin(self, root_id: str) -> DemandOrigin:
        """Decode origin only from the broker's persisted root admission row."""
        if not isinstance(root_id, str) or not root_id:
            raise PowerPolicyError("root_identity_invalid")
        try:
            row = self.db.execute(
                "SELECT origin_class FROM pa1_frames WHERE job_id=? AND parent_id IS NULL",
                (root_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            raise PowerPolicyError("persisted_root_origin_unavailable") from exc
        if row is None or row["origin_class"] not in {"public_demand", "automatic"}:
            raise PowerPolicyError("persisted_root_origin_unavailable")
        return DemandOrigin(root_id, row["origin_class"], _CONTROL_SEAL)

    @staticmethod
    def _authorized(control: object, action: str) -> None:
        if (not isinstance(control, TrustedOperatorControl)
                or control._seal is not _CONTROL_SEAL):
            raise PowerPolicyError("trusted_operator_control_required")
        # Mutating controls are deliberately distinct typed capabilities. This
        # prevents source strings and role labels from being interpreted as grants.
        permission = control.permission(action)
        if not isinstance(permission, OperatorPermission) or permission.action != action:
            raise PowerPolicyError("trusted_operator_permission_required")

    def _attach_owned_release_verifier(self, verifier) -> None:
        """Bind the ResourceController verifier over this exact SQLite handle."""
        if not callable(verifier):
            raise PowerPolicyError("owned_release_verifier_invalid")
        owner = getattr(verifier, "__self__", None)
        if owner is None or getattr(owner, "db", None) is not self.db:
            raise PowerPolicyError("owned_release_verifier_database_mismatch")
        current = getattr(self, "_owned_release_verifier", None)
        if current is not None and current != verifier:
            raise PowerPolicyError("owned_release_verifier_already_bound")
        self._owned_release_verifier = verifier

    def mark_release_verified(self, intent: ReleaseIntent) -> int:
        """Persist verified physical cleanup from the bound ResourceController.

        The ResourceController provider reads its committed session table and
        validates every exact session proof. PowerPolicy accepts only its sealed
        typed acknowledgement, never booleans or caller-supplied receipt maps.
        """
        if self.db.in_transaction:
            raise PowerPolicyError("release_verification_requires_committed_state")
        if not isinstance(intent, ReleaseIntent):
            raise PowerPolicyError("release_intent_invalid")
        verifier = getattr(self, "_owned_release_verifier", None)
        if verifier is None:
            raise PowerPolicyError("owned_release_verifier_unavailable")
        owner = getattr(verifier, "__self__", None)
        locked_verifier = getattr(owner, "_verified_release_ack_under_lock", None)
        if not callable(locked_verifier):
            raise PowerPolicyError("owned_release_verifier_lock_unavailable")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            row = self._row()
            if (row["release_request_id"] != intent.request_id
                    or row["physical_state"] not in {"pending", "released_verified"}):
                raise PowerPolicyError("release_intent_stale")
            acknowledgment = locked_verifier(intent)
            # Lazy import avoids a module cycle: resources.py imports PowerPolicy.
            from .resources import _is_verified_owned_release_ack
            if not _is_verified_owned_release_ack(acknowledgment, intent.request_id):
                raise PowerPolicyError("owned_release_proof_invalid")
            targets = acknowledgment.target_session_ids
            if not isinstance(targets, tuple) or not targets:
                raise PowerPolicyError("owned_release_targets_empty")
            proof_digest = acknowledgment.proof_digest
            if not isinstance(proof_digest, str) or len(proof_digest) != 64:
                raise PowerPolicyError("owned_release_proof_digest_invalid")
            target_digest = hashlib.sha256(
                json.dumps(sorted(targets), separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            current = self._row()
            if current["release_request_id"] != intent.request_id:
                raise PowerPolicyError("release_intent_replaced")
            self.db.execute("""UPDATE pa1_power_state SET physical_state='released_verified',
                release_verified_at=?,release_verified_sessions=?,release_verified_proof_digest=?,
                release_verified_targets_digest=?,updated=?
                WHERE singleton=1 AND release_request_id=?""",
                (float(self.clock()), len(targets), proof_digest, target_digest,
                 float(self.clock()), intent.request_id))
            if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                raise PowerPolicyError("release_intent_replaced")
            return len(targets)
        except Exception:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def _invalidate_verified_release_for_new_target(self) -> bool:
        """Clear prior physical proof when trusted ownership records a new session."""
        row = self._row()
        if row["physical_state"] != "released_verified":
            return False
        self.db.execute("""UPDATE pa1_power_state SET physical_state='pending',
            release_verified_at=NULL,release_verified_sessions=0,
            release_verified_proof_digest=NULL,release_verified_targets_digest=NULL,updated=?
            WHERE singleton=1 AND physical_state='released_verified'""",
            (float(self.clock()),))
        return self.db.execute("SELECT changes()").fetchone()[0] == 1

    def set_automatic(self, control: TrustedOperatorControl, enabled: bool) -> None:
        self._authorized(control, "set_automatic")
        if type(enabled) is not bool:
            raise PowerPolicyError("automatic_setting_must_be_boolean")
        self.db.execute("UPDATE pa1_power_state SET automatic_enabled=?,updated=? WHERE singleton=1",
                        (int(enabled), float(self.clock())))

    def set_focus_window(self, control: TrustedOperatorControl, *, focus_id: str,
                         project: str, starts_at: float, expires_at: float) -> None:
        """Opt in to bounded automatic work for one trusted focus and project."""
        self._authorized(control, "set_focus_window")
        now = float(self.clock())
        if (not all(isinstance(value, str) and value and len(value) <= 256
                    for value in (focus_id, project))
                or not _finite_timestamp(starts_at)
                or not _finite_timestamp(expires_at)
                or starts_at < now or expires_at <= starts_at
                or expires_at - starts_at < MIN_FOCUS_WINDOW_SECONDS
                or expires_at - starts_at > MAX_FOCUS_WINDOW_SECONDS):
            raise PowerPolicyError("focus_window_invalid")
        self.db.execute("""UPDATE pa1_power_state SET automatic_focus=?,automatic_project=?,
            automatic_starts=?,automatic_until=?,updated=? WHERE singleton=1""",
            (focus_id, project, float(starts_at), float(expires_at), now))

    def set_quiet(self, control: TrustedOperatorControl, *, expires_at: float | None = None,
                  reason: str = "operator-requested", focus_id: str | None = None) -> None:
        """Persist global quiet; expiry is explicit and never extended by focus changes."""
        self._authorized(control, "set_quiet")
        now = float(self.clock())
        if expires_at is not None and (not _finite_timestamp(expires_at) or expires_at <= now):
            raise PowerPolicyError("quiet_expiry_must_be_in_future")
        if not isinstance(reason, str) or not reason or len(reason) > 256:
            raise PowerPolicyError("quiet_reason_invalid")
        if focus_id is not None and (not isinstance(focus_id, str) or len(focus_id) > 256):
            raise PowerPolicyError("quiet_focus_invalid")
        self.db.execute("""UPDATE pa1_power_state SET quiet_until=?,quiet_focus=?,quiet_reason=?,updated=?
            WHERE singleton=1""", (expires_at, focus_id, reason, now))

    def set_release(self, control: TrustedOperatorControl, *,
                    declared_end: float | None | object = _UNCHANGED,
                    reason: str | None = None) -> ReleaseIntent:
        """Durably veto rewarming and record pending physical release intent.

        Repeated requests during the same unexpired veto reuse its identity and
        retain any physical proof. A supplied end or reason updates only that
        metadata. An omitted/None end leaves an active intent's current end
        unchanged; after resume or expiry the next request creates a new intent.

        The caller must commit its transaction before delivering the returned
        intent to the backend owner.  A backend acknowledgement is not proof of
        process exit or reclaimed VRAM and does not clear this veto.
        """
        self._authorized(control, "request_release")
        now = float(self.clock())
        current = self._row()
        current_active = bool(current["release_veto"]) and (
            current["release_until"] is None or now < current["release_until"])
        if current_active:
            next_end = current["release_until"]
            if declared_end is not _UNCHANGED and declared_end is not None:
                if not _finite_timestamp(declared_end) or declared_end <= now:
                    raise PowerPolicyError("release_end_must_be_in_future")
                next_end = float(declared_end)
            next_reason = current["release_reason"] if reason is None else reason
            if not isinstance(next_reason, str) or not next_reason or len(next_reason) > 256:
                raise PowerPolicyError("release_reason_invalid")
            if next_end != current["release_until"] or next_reason != current["release_reason"]:
                self.db.execute("""UPDATE pa1_power_state SET release_until=?,release_reason=?,updated=?
                    WHERE singleton=1 AND release_request_id=?""",
                    (next_end, next_reason, now, current["release_request_id"]))
            created = current["release_created_at"]
            if created is None:
                created = current["updated"]
            return ReleaseIntent(current["release_request_id"], float(created), next_end, next_reason)
        next_end = None if declared_end is _UNCHANGED else declared_end
        if next_end is not None and (not _finite_timestamp(next_end) or next_end <= now):
            raise PowerPolicyError("release_end_must_be_in_future")
        next_reason = "operator-requested" if reason is None else reason
        if not isinstance(next_reason, str) or not next_reason or len(next_reason) > 256:
            raise PowerPolicyError("release_reason_invalid")
        request_id = secrets.token_hex(16)
        self.db.execute("""UPDATE pa1_power_state SET release_veto=1,release_until=?,
            release_request_id=?,release_created_at=?,release_reason=?,physical_state='pending',
            release_verified_at=NULL,release_verified_sessions=0,
            release_verified_proof_digest=NULL,release_verified_targets_digest=NULL,updated=?
            WHERE singleton=1""", (next_end, request_id, now, next_reason, now))
        return ReleaseIntent(request_id, now, next_end, next_reason)

    def resume(self, control: TrustedOperatorControl) -> None:
        """Explicitly clear quiet and release veto; physical accounting stays separate."""
        self._authorized(control, "resume")
        self.db.execute("""UPDATE pa1_power_state SET quiet_until=NULL,quiet_focus=NULL,
            quiet_reason=NULL,release_veto=0,release_until=NULL,updated=? WHERE singleton=1""",
            (float(self.clock()),))

    def deliver_release_intent(self, intent: ReleaseIntent, callback) -> str:
        """Deliver a committed release intent without holding a DB transaction.

        The callback receives the stable request ID and reason. It may request
        owner cleanup, but its return value is not physical-release evidence.
        Repeated delivery must be idempotent at the backend owner. Any callback
        error leaves the durable veto and pending occupancy state in place.
        """
        if self.db.in_transaction:
            raise PowerPolicyError("release_callback_requires_committed_state")
        if not isinstance(intent, ReleaseIntent):
            raise PowerPolicyError("release_intent_invalid")
        row = self._row()
        if row["release_request_id"] != intent.request_id or not row["release_veto"]:
            raise PowerPolicyError("release_intent_stale")
        if not callable(callback):
            raise PowerPolicyError("release_callback_invalid")
        try:
            callback(intent.request_id, intent.reason)
        except Exception:
            # Keep the request pending. A later owner retry may deliver the same
            # idempotency key; no error path implies that resources were freed.
            return "pending_after_callback_error"
        return "pending_owner_verification"

    def snapshot(self) -> dict[str, Any]:
        """Read-only effective and persisted state; it never calls a backend."""
        row = self._row()
        now = float(self.clock())
        quiet_active = row["quiet_reason"] is not None and (
            row["quiet_until"] is None or now < row["quiet_until"])
        release_active = bool(row["release_veto"]) and (
            row["release_until"] is None or now < row["release_until"])
        return {
            "automatic_enabled": bool(row["automatic_enabled"]),
            "automatic_focus": row["automatic_focus"],
            "automatic_project": row["automatic_project"],
            "automatic_starts": row["automatic_starts"],
            "automatic_until": row["automatic_until"],
            "quiet_active": quiet_active,
            "quiet_until": row["quiet_until"],
            "quiet_focus": row["quiet_focus"],
            "quiet_reason": row["quiet_reason"],
            "release_veto_active": release_active,
            "release_until": row["release_until"],
            "release_request_id": row["release_request_id"],
            "release_created_at": row["release_created_at"],
            "release_reason": row["release_reason"],
            "physical_state": row["physical_state"],
            "release_verified_at": row["release_verified_at"],
            "release_verified_sessions": row["release_verified_sessions"],
            "release_verified_proof_digest": row["release_verified_proof_digest"],
            "release_verified_targets_digest": row["release_verified_targets_digest"],
            "updated": row["updated"],
        }

    def check_permission(self, control: TrustedOperatorControl,
                         permission: OperatorPermission, action: str) -> bool:
        """Check a typed capability without accepting role names or source data."""
        self._authorized(control, action)
        return (isinstance(permission, OperatorPermission)
                and permission._seal is _CONTROL_SEAL and permission.action == action)

    def allow_dispatch(self, frame_class: str, demand: bool,
                       permission: OperatorPermission | None = None, *,
                       focus_id: str | None = None, project: str | None = None,
                       root_id: str | None = None,
                       demand_origin: DemandOrigin | None = None) -> DispatchDecision:
        """Apply persisted power policy before a root, child or opportunistic turn."""
        if frame_class not in _FRAME_CLASSES:
            raise PowerPolicyError("frame_class_unknown")
        if type(demand) is not bool:
            raise PowerPolicyError("demand_must_be_boolean")
        self._persist_expiries(float(self.clock()))
        state = self.snapshot()
        automatic = frame_class in {"automatic_root", "opportunistic"}
        foreground = frame_class in {"public_root", "foreground"}
        priority = "foreground" if foreground else "opportunistic"
        if state["release_veto_active"]:
            return DispatchDecision(False, "inference_release_veto", priority, state["automatic_enabled"],
                                    state["quiet_active"], True)
        window_active = (state["automatic_focus"] is not None
                         and state["automatic_starts"] <= float(self.clock()) < state["automatic_until"]
                         and focus_id == state["automatic_focus"]
                         and project == state["automatic_project"])
        automatic_permission = (isinstance(permission, OperatorPermission)
                                and permission._seal is _CONTROL_SEAL
                                and permission.action == "dispatch_automatic")
        inherited_public = (frame_class == "private_child"
                            and isinstance(demand_origin, DemandOrigin)
                            and demand_origin._seal is _CONTROL_SEAL
                            and demand_origin.root_id == root_id
                            and demand_origin.kind == "public_demand")
        inherited_automatic = (frame_class == "private_child"
                               and isinstance(demand_origin, DemandOrigin)
                               and demand_origin._seal is _CONTROL_SEAL
                               and demand_origin.root_id == root_id
                               and demand_origin.kind == "automatic")
        child_origin_valid = (frame_class != "private_child" or inherited_public or inherited_automatic)
        if not child_origin_valid:
            return DispatchDecision(False, "persisted_root_origin_required", priority,
                                    state["automatic_enabled"], state["quiet_active"], False)
        child_automatic = frame_class == "private_child" and inherited_automatic
        if (automatic or child_automatic) and (
                not state["automatic_enabled"] or not window_active or not automatic_permission):
            return DispatchDecision(False, "automatic_work_disabled", priority, False,
                                    state["quiet_active"], False)
        if (automatic or frame_class == "private_child") and not demand:
            return DispatchDecision(False, "demand_required", priority, state["automatic_enabled"],
                                    state["quiet_active"], False)
        if foreground and not demand:
            return DispatchDecision(False, "demand_required", priority, state["automatic_enabled"],
                                    state["quiet_active"], False)
        if state["quiet_active"]:
            quiet_override = (demand and isinstance(permission, OperatorPermission)
                              and permission._seal is _CONTROL_SEAL
                              and permission.action == "dispatch_during_quiet")
            automatic_child = frame_class == "private_child" and inherited_automatic
            if ((automatic or automatic_child) and not quiet_override):
                return DispatchDecision(False, "quiet_active", priority, state["automatic_enabled"],
                                        True, False)
        return DispatchDecision(True, "allowed", priority, state["automatic_enabled"],
                                state["quiet_active"], False)

    def _persist_expiries(self, now: float) -> None:
        """Make observed expiry durable so later focus or clock changes cannot revive it."""
        row = self._row()
        updates: list[str] = []
        if row["quiet_reason"] is not None and row["quiet_until"] is not None and now >= row["quiet_until"]:
            updates.extend(("quiet_reason=NULL", "quiet_focus=NULL"))
        if row["release_veto"] and row["release_until"] is not None and now >= row["release_until"]:
            # The declared logical end permits future work. Physical state stays
            # pending until independently verified owner cleanup.
            updates.append("release_veto=0")
        if updates:
            updates.append("updated=?")
            self.db.execute("UPDATE pa1_power_state SET " + ",".join(updates) + " WHERE singleton=1",
                            (now,))
        if row["automatic_until"] is not None and now >= row["automatic_until"]:
            self.db.execute("""UPDATE pa1_power_state SET automatic_focus=NULL,automatic_project=NULL,
                automatic_starts=NULL,automatic_until=NULL,updated=? WHERE singleton=1""", (now,))
