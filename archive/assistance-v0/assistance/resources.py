"""Private durable records for PA1-owned observer residency.

This component shares the caller's JobService SQLite connection.  It never
opens a database, starts a thread, starts a model, or chooses a GPU.  Session
receipts and release capabilities are private resource-control metadata; they
must never be added to inquiry packets, model messages, or public logs.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .power import PowerPolicy, PowerPolicyError, ReleaseIntent


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_OWNED_RELEASE_INPUT_BATCH = 64
_HEX_32 = re.compile(r"^[0-9a-f]{32}$")
_UUID_TEXT = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SESSION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_STATES = frozenset({"active", "idle_owned", "release_pending", "released_verified", "stale", "superseded"})
_ACK_SEAL = object()
_TERMINAL_JOB_STATES = frozenset({"completed", "partial", "failed", "cancelled"})


def _process_start_time(pid: int) -> str | None:
    """Read one exact Linux process generation; unreadable is not evidence of death."""
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ResourceControllerError("orphan_execution_slot_owner_identity_unavailable") from error
    close = raw.rfind(")")
    if close < 0:
        raise ResourceControllerError("orphan_execution_slot_owner_identity_invalid")
    fields = raw[close + 1:].split()
    if len(fields) <= 19 or not fields[19].isdigit():
        raise ResourceControllerError("orphan_execution_slot_owner_identity_invalid")
    return fields[19]


def _require_process_absent(pid: int, start: str, *, error_prefix: str) -> None:
    """Require exact kernel-confirmed absence; a reused PID is not absence."""
    current = _process_start_time(pid)
    if current is not None:
        raise ResourceControllerError(f"{error_prefix}_present_or_reused")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except (PermissionError, OSError) as error:
        raise ResourceControllerError(f"{error_prefix}_absence_unverifiable") from error
    raise ResourceControllerError(f"{error_prefix}_absence_unverifiable")


def _validate_orphan_execution_slots(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 16:
        raise ResourceControllerError("orphan_execution_slot_attestation_invalid")
    accepted = []
    seen = set()
    for slot in value:
        fields = {"job_id", "attempt", "cleanup_pending", "owner_pid", "owner_process_start"}
        if not isinstance(slot, Mapping) or set(slot) != fields:
            raise ResourceControllerError("orphan_execution_slot_attestation_invalid")
        job_id, attempt = slot.get("job_id"), slot.get("attempt")
        owner_pid, owner_start = slot.get("owner_pid"), slot.get("owner_process_start")
        if (not isinstance(job_id, str) or not job_id or len(job_id) > 256 or
                type(attempt) is not int or attempt <= 0 or slot.get("cleanup_pending") is not True or
                type(owner_pid) is not int or owner_pid <= 0 or
                not isinstance(owner_start, str) or not owner_start.isdigit() or len(owner_start) > 128 or
                (job_id, attempt) in seen):
            raise ResourceControllerError("orphan_execution_slot_attestation_invalid")
        seen.add((job_id, attempt))
        accepted.append({"job_id": job_id, "attempt": attempt, "cleanup_pending": True,
            "owner_pid": owner_pid, "owner_process_start": owner_start})
    return accepted


def _physical_cleanup_identities(value: object) -> tuple[tuple[Any, ...], ...] | None:
    if not isinstance(value, list):
        return None
    identities = []
    for item in value:
        if (not isinstance(item, Mapping) or not isinstance(item.get("owner_id"), str) or
                type(item.get("owned_pid")) is not int or
                not isinstance(item.get("server_process_start"), str) or
                not isinstance(item.get("generation"), str) or
                not isinstance(item.get("gpu_uuids"), list) or
                any(not isinstance(uuid, str) for uuid in item["gpu_uuids"])):
            return None
        identities.append((item["owner_id"], item["owned_pid"],
            item["server_process_start"], item["generation"], tuple(sorted(item["gpu_uuids"]))))
    return tuple(sorted(identities))


def _orphan_audit_id(value: object) -> bool:
    """Accept current hex UUIDs and legacy canonical dashed UUID receipts."""
    return isinstance(value, str) and (
        _HEX_32.fullmatch(value) is not None or _UUID_TEXT.fullmatch(value) is not None)


class ResourceControllerError(ValueError):
    """Malformed or inconsistent private resource ownership metadata."""


class _VerifiedOwnedReleaseAck:
    """Opaque proof token minted only after committed owner-row verification."""

    __slots__ = ("request_id", "target_session_ids", "proof_digest", "_seal")

    def __init__(self, seal: object, request_id: str, target_session_ids: Sequence[str], proof_digest: str):
        if seal is not _ACK_SEAL:
            raise ResourceControllerError("verified_release_ack_is_private")
        self.request_id = request_id
        self.target_session_ids = tuple(target_session_ids)
        self.proof_digest = proof_digest
        self._seal = seal


def _is_verified_owned_release_ack(value: object, request_id: str) -> bool:
    return (isinstance(value, _VerifiedOwnedReleaseAck) and value._seal is _ACK_SEAL and
            value.request_id == request_id and bool(value.target_session_ids) and
            len(set(value.target_session_ids)) == len(value.target_session_ids) and
            bool(_HEX_64.fullmatch(value.proof_digest)))


def _json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ResourceControllerError("resource_metadata_must_be_json") from error


def _epoch(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResourceControllerError("supervisor_epoch_required")
    allowed = {"daemon_epoch", "supervisor_pid", "supervisor_process_start", "runtime_fingerprint"}
    if set(value) != allowed:
        raise ResourceControllerError("supervisor_epoch_fields_invalid")
    if (not isinstance(value["daemon_epoch"], str) or not _HEX_64.fullmatch(value["daemon_epoch"]) or
            type(value["supervisor_pid"]) is not int or value["supervisor_pid"] <= 0 or
            not isinstance(value["supervisor_process_start"], str) or
            not 1 <= len(value["supervisor_process_start"]) <= 128 or
            not isinstance(value["runtime_fingerprint"], str) or
            not _HEX_64.fullmatch(value["runtime_fingerprint"])):
        raise ResourceControllerError("supervisor_epoch_invalid")
    return dict(value)


def _receipt(session_id: str, value: Mapping[str, Any], supervisor_epoch: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ResourceControllerError("close_receipt_required")
    fields = {"format", "session_id", "daemon_epoch", "slot_id", "owner_id", "host_lease_id",
              "residency_generation", "server_pid", "server_process_start", "gpu_uuids", "capability"}
    if set(value) != fields or value.get("format") != "PA1-OWNED-RESOURCE/1":
        raise ResourceControllerError("close_receipt_fields_invalid")
    if (value.get("session_id") != session_id or value.get("daemon_epoch") != supervisor_epoch["daemon_epoch"] or
            not isinstance(value.get("slot_id"), str) or not 1 <= len(value["slot_id"]) <= 128 or
            not isinstance(value.get("owner_id"), str) or not 1 <= len(value["owner_id"]) <= 256 or
            value.get("host_lease_id") != value.get("owner_id") or
            not isinstance(value.get("residency_generation"), str) or
            not 1 <= len(value["residency_generation"]) <= 256 or
            type(value.get("server_pid")) is not int or value["server_pid"] <= 0 or
            not isinstance(value.get("server_process_start"), str) or
            not 1 <= len(value["server_process_start"]) <= 128 or
            not isinstance(value.get("capability"), str) or not _HEX_64.fullmatch(value["capability"])):
        raise ResourceControllerError("close_receipt_identity_invalid")
    gpu_uuids = value.get("gpu_uuids")
    if (not isinstance(gpu_uuids, list) or not 1 <= len(gpu_uuids) <= 4 or
            any(not isinstance(item, str) or not item.startswith("GPU-") or len(item) > 128 for item in gpu_uuids) or
            len(set(gpu_uuids)) != len(gpu_uuids)):
        raise ResourceControllerError("close_receipt_gpu_scope_invalid")
    return dict(value)


class ResourceController:
    """Durable exact-session receipts and committed release-intent delivery."""

    def __init__(self, db: sqlite3.Connection, *, power_policy: PowerPolicy | None = None,
                 clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock
        self.power_policy = power_policy or PowerPolicy(db, clock=clock)
        self.initialize(db)
        attach = getattr(self.power_policy, "_attach_owned_release_verifier", None)
        if callable(attach):
            attach(self.verified_release_ack)

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        db.execute("""CREATE TABLE IF NOT EXISTS pa1_owned_resource_sessions(
            session_id TEXT PRIMARY KEY,
            supervisor_epoch TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('active','idle_owned','release_pending','released_verified','stale','superseded')),
            close_receipt TEXT,
            release_request_id TEXT,
            release_proof TEXT,
            updated REAL NOT NULL,
            CHECK((state='active' AND close_receipt IS NULL) OR
                  (state<>'active' AND close_receipt IS NOT NULL))
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pa1_orphan_resource_reconciliation_audit(
            receipt_id TEXT PRIMARY KEY,
            release_request_id TEXT NOT NULL,
            daemon_epoch TEXT NOT NULL,
            session_ids TEXT NOT NULL,
            proof TEXT NOT NULL,
            proof_sha256 TEXT NOT NULL,
            controller_host TEXT NOT NULL,
            reconciled_at REAL NOT NULL,
            UNIQUE(release_request_id, daemon_epoch)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pa1_stale_resource_reconciliation_audit(
            receipt_id TEXT PRIMARY KEY,
            release_request_id TEXT NOT NULL,
            daemon_epoch TEXT NOT NULL,
            session_ids TEXT NOT NULL,
            proof TEXT NOT NULL,
            proof_sha256 TEXT NOT NULL,
            controller_host TEXT NOT NULL,
            reconciled_at REAL NOT NULL,
            UNIQUE(release_request_id, daemon_epoch)
        )""")

    def record_active_session(self, session_id: str, supervisor_epoch: Mapping[str, Any]) -> None:
        """Record a trusted observer-open before the broker exposes session work."""
        if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
            raise ResourceControllerError("session_id_invalid")
        epoch = _epoch(supervisor_epoch)
        existing = self.db.execute("SELECT supervisor_epoch,state FROM pa1_owned_resource_sessions WHERE session_id=?",
                                   (session_id,)).fetchone()
        encoded = _json(epoch)
        if existing:
            prior_epoch = existing["supervisor_epoch"] if isinstance(existing, sqlite3.Row) else existing[0]
            prior_state = existing["state"] if isinstance(existing, sqlite3.Row) else existing[1]
            if prior_epoch == encoded and prior_state == "active":
                return
            raise ResourceControllerError("session_id_already_recorded")
        self.db.execute("""INSERT INTO pa1_owned_resource_sessions
            (session_id,supervisor_epoch,state,close_receipt,updated) VALUES(?,?,'active',NULL,?)""",
            (session_id, encoded, float(self.clock())))
        power_state = self.power_policy.snapshot()
        if power_state["release_veto_active"] and power_state["physical_state"] == "released_verified":
            invalidate = getattr(self.power_policy, "_invalidate_verified_release_for_new_target", None)
            if not callable(invalidate) or invalidate() is not True:
                raise ResourceControllerError("prior_release_proof_invalidation_unavailable")

    def record_session(self, session_id: str, close_receipt: Mapping[str, Any]) -> None:
        """Attach the daemon-minted close capability after ordinary idle close."""
        if not isinstance(session_id, str) or not _SESSION.fullmatch(session_id):
            raise ResourceControllerError("session_id_invalid")
        row = self.db.execute("SELECT supervisor_epoch,state,close_receipt,release_request_id FROM pa1_owned_resource_sessions WHERE session_id=?",
                              (session_id,)).fetchone()
        if row is None:
            raise ResourceControllerError("active_session_record_required")
        epoch_text = row["supervisor_epoch"] if isinstance(row, sqlite3.Row) else row[0]
        state = row["state"] if isinstance(row, sqlite3.Row) else row[1]
        prior = row["close_receipt"] if isinstance(row, sqlite3.Row) else row[2]
        release_id = row["release_request_id"] if isinstance(row, sqlite3.Row) else row[3]
        epoch = json.loads(epoch_text)
        checked = _receipt(session_id, close_receipt, epoch)
        encoded = _json(checked)
        if state == "idle_owned" and prior == encoded:
            return
        if state not in {"active", "release_pending"}:
            raise ResourceControllerError("session_not_active")
        next_state = "release_pending" if release_id else "idle_owned"
        # One physical backend slot can carry multiple sequential session IDs.
        # A later trusted closecap for that exact daemon slot/host owner
        # supersedes the earlier idle cap, which the server has invalidated on
        # reuse. The replacement receipt is the sole current authority.
        self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='superseded',
            release_request_id=NULL,release_proof=NULL,updated=?
            WHERE session_id<>? AND state IN ('idle_owned','release_pending')
            AND json_extract(close_receipt,'$.daemon_epoch')=?
            AND json_extract(close_receipt,'$.slot_id')=?
            AND json_extract(close_receipt,'$.owner_id')=?""",
            (float(self.clock()), session_id, checked["daemon_epoch"], checked["slot_id"], checked["owner_id"]))
        self.db.execute("""UPDATE pa1_owned_resource_sessions SET state=?,close_receipt=?,updated=?
            WHERE session_id=? AND state IN ('active','release_pending')""",
            (next_state, encoded, float(self.clock()), session_id))

    def record_orphaned_sessions_after_physical_release(self, control: object, intent: ReleaseIntent,
                                                        proof: object) -> list[str]:
        """Reconcile active rows only after an authenticated whole-epoch stop.

        This path is for the narrow failure where a frontend died before it
        could persist its daemon-minted close receipt. It records an aggregate
        physical-stop attestation in a separate format; it never manufactures
        a per-session daemon capability.
        """
        from ..observer_analysis import _is_verified_physical_release

        if not _is_verified_physical_release(proof):
            raise ResourceControllerError("verified_physical_release_required")
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        PowerPolicy._authorized(control, "request_release")
        state = self.power_policy.snapshot()
        if (state.get("release_veto_active") is not True or state.get("release_until") is not None or
                state.get("release_request_id") != intent.request_id or
                state.get("physical_state") != "pending"):
            raise ResourceControllerError("permanent_release_veto_required")
        now = float(self.clock())
        captured = proof.get("captured_at")
        if (isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                now < float(captured) or now - float(captured) > 300):
            raise ResourceControllerError("physical_release_proof_expired")
        required = ("daemon_epoch", "supervisor_pid", "supervisor_process_start",
                    "runtime_fingerprint", "gpu_uuids", "cleanup_receipts")
        if (proof.get("format") != "PA1-PHYSICAL-RELEASE/1" or proof.get("stopped") is not True or
                proof.get("quiescent") is not True or any(key not in proof for key in required) or
                not isinstance(proof.get("daemon_epoch"), str) or
                not _HEX_64.fullmatch(proof["daemon_epoch"]) or
                type(proof.get("supervisor_pid")) is not int or proof["supervisor_pid"] <= 0 or
                not isinstance(proof.get("supervisor_process_start"), str) or
                not isinstance(proof.get("runtime_fingerprint"), str) or
                not _HEX_64.fullmatch(proof["runtime_fingerprint"]) or
                not isinstance(proof.get("gpu_uuids"), list) or not proof["gpu_uuids"] or
                not isinstance(proof.get("cleanup_receipts"), list) or not proof["cleanup_receipts"] or
                not isinstance(getattr(proof, "host", None), str) or not proof.host):
            raise ResourceControllerError("physical_release_proof_invalid")
        # Recheck the broker's authority in the same database as these rows.
        attested_slots = _validate_orphan_execution_slots(proof.get("orphan_execution_slots", []))
        try:
            active_jobs = self.db.execute("""SELECT count(*) FROM jobs
                WHERE COALESCE(json_extract(record,'$.status'),'') NOT IN ('completed','partial','failed','cancelled')""").fetchone()[0]
            slot_count = self.db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("controller_quiescence_unavailable") from error
        if active_jobs != 0:
            raise ResourceControllerError("controller_work_not_quiescent")
        self._validate_recoverable_execution_slots(attested_slots, expected_count=slot_count)
        rows = self._rows()
        active_rows = []
        for row in rows:
            session_id, epoch_text, row_state = row[0], row[1], row[2]
            if row_state == "active":
                epoch = json.loads(epoch_text)
                if (epoch["daemon_epoch"] != proof["daemon_epoch"] or
                        epoch["supervisor_pid"] != proof["supervisor_pid"] or
                        epoch["supervisor_process_start"] != proof["supervisor_process_start"] or
                        epoch["runtime_fingerprint"] != proof["runtime_fingerprint"]):
                    raise ResourceControllerError("active_session_epoch_mismatch")
                active_rows.append(session_id)
        if not active_rows:
            raise ResourceControllerError("orphaned_active_sessions_missing")
        if any(row[2] in {"idle_owned", "release_pending"} and
               json.loads(row[1]).get("daemon_epoch") == proof["daemon_epoch"] for row in rows):
            raise ResourceControllerError("owned_close_receipts_require_normal_release")
        session_ids = sorted(active_rows)
        audit_id = str(uuid.uuid4())
        receipt = {"format": "PA1-ORPHANED-SESSION-RELEASE/1", "receipt_id": audit_id,
            "release_request_id": intent.request_id,
            "daemon_epoch": proof["daemon_epoch"], "session_ids": session_ids,
            "physical_release_proof_sha256": hashlib.sha256(_json(proof).encode("utf-8")).hexdigest()}
        encoded_receipt = _json(receipt)
        encoded_proof = _json(proof)
        proof_digest = hashlib.sha256(encoded_proof.encode("utf-8")).hexdigest()
        self.db.execute("SAVEPOINT pa1_orphan_reconcile")
        try:
            # Re-read the broker rows after the savepoint so that a concurrent
            # claim, cancellation, or cleanup cannot widen the captured set.
            try:
                active_jobs_now = self.db.execute("""SELECT count(*) FROM jobs
                    WHERE COALESCE(json_extract(record,'$.status'),'') NOT IN ('completed','partial','failed','cancelled')""").fetchone()[0]
                current_slot_count = self.db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
            except sqlite3.OperationalError as error:
                raise ResourceControllerError("controller_quiescence_unavailable") from error
            if active_jobs_now != 0:
                raise ResourceControllerError("controller_work_not_quiescent")
            self._validate_recoverable_execution_slots(attested_slots, expected_count=current_slot_count)
            for slot in attested_slots:
                if _process_start_time(slot["owner_pid"]) == slot["owner_process_start"]:
                    raise ResourceControllerError("orphan_execution_slot_owner_still_running")
            self.db.execute("""INSERT INTO pa1_orphan_resource_reconciliation_audit
                (receipt_id,release_request_id,daemon_epoch,session_ids,proof,proof_sha256,controller_host,reconciled_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (audit_id, intent.request_id, proof["daemon_epoch"], _json({"session_ids": session_ids}),
                 encoded_proof, proof_digest, proof.host, now))
            for session_id in session_ids:
                updated = self.db.execute("""UPDATE pa1_owned_resource_sessions
                    SET state='released_verified',close_receipt=?,release_request_id=?,release_proof=?,updated=?
                    WHERE session_id=? AND state='active'""",
                    (encoded_receipt, intent.request_id, encoded_proof, now, session_id)).rowcount
                if updated != 1:
                    raise ResourceControllerError("orphaned_session_changed_during_reconcile")
            self.db.execute("RELEASE SAVEPOINT pa1_orphan_reconcile")
        except Exception:
            self.db.execute("ROLLBACK TO SAVEPOINT pa1_orphan_reconcile")
            self.db.execute("RELEASE SAVEPOINT pa1_orphan_reconcile")
            raise
        return session_ids

    def record_archived_epoch_recovery_after_physical_release(
            self, control: object, intent: ReleaseIntent, proof: object) -> list[str]:
        """Reconcile mixed orphan/closed rows from a freshly revalidated old epoch.

        This is a source-mode operator recovery for the specific state where
        receiptless active sessions and receipt-bearing release-pending sessions
        coexist with terminal failed execution slots.  The private proof is
        minted only after fresh host checks; this method additionally matches
        every durable row, process generation, GPU owner, source identity, and
        failed slot before updating any resource state.
        """
        from ..observer_analysis import _is_verified_physical_release, _source_identity_snapshot

        if not _is_verified_physical_release(proof):
            raise ResourceControllerError("verified_physical_release_required")
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        PowerPolicy._authorized(control, "request_release")
        if proof.get("recovery_kind") != "source_mode_archived_whole_epoch":
            raise ResourceControllerError("archived_physical_release_proof_required")
        try:
            current_source = _source_identity_snapshot()
        except Exception as error:
            raise ResourceControllerError("physical_release_source_identity_unavailable") from error
        if proof.get("source_identity") != current_source:
            raise ResourceControllerError("physical_release_source_identity_mismatch")

        state = self.power_policy.snapshot()
        if (state.get("release_veto_active") is not True or state.get("release_until") is not None or
                state.get("release_request_id") != intent.request_id or
                state.get("physical_state") != "pending"):
            raise ResourceControllerError("permanent_release_veto_required")
        now = float(self.clock())
        captured = proof.get("captured_at")
        if (isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                not math.isfinite(float(captured)) or now < float(captured) or now - float(captured) > 300):
            raise ResourceControllerError("physical_release_proof_expired")
        required = ("daemon_epoch", "supervisor_pid", "supervisor_process_start",
                    "runtime_fingerprint", "gpu_uuids", "cleanup_receipts",
                    "orphan_execution_slots", "fresh_observations")
        if (proof.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                proof.get("status") != "released_verified" or proof.get("released_verified") is not True or
                proof.get("stopped") is not True or proof.get("evicted") is not True or
                proof.get("quiescent") is not True or any(key not in proof for key in required) or
                not isinstance(proof.get("daemon_epoch"), str) or
                not _HEX_64.fullmatch(proof["daemon_epoch"]) or
                type(proof.get("supervisor_pid")) is not int or proof["supervisor_pid"] <= 0 or
                not isinstance(proof.get("supervisor_process_start"), str) or
                not proof["supervisor_process_start"].isdigit() or
                not isinstance(proof.get("runtime_fingerprint"), str) or
                not _HEX_64.fullmatch(proof["runtime_fingerprint"]) or
                not isinstance(proof.get("gpu_uuids"), list) or not proof["gpu_uuids"] or
                not isinstance(proof.get("cleanup_receipts"), list) or not proof["cleanup_receipts"] or
                not isinstance(getattr(proof, "host", None), str) or not proof.host):
            raise ResourceControllerError("physical_release_proof_invalid")
        fresh = proof.get("fresh_observations")
        units = fresh.get("service_units") if isinstance(fresh, Mapping) else None
        gpu = fresh.get("gpu") if isinstance(fresh, Mapping) else None
        host = fresh.get("host") if isinstance(fresh, Mapping) else None
        expected_units = {"project-control.service", "project-control-inference.service"}
        gpu_devices = gpu.get("devices") if isinstance(gpu, Mapping) else None
        gpu_ids = ({item.get("uuid") for item in gpu_devices if isinstance(item, Mapping)}
                   if isinstance(gpu_devices, list) else set())
        if (not isinstance(units, list) or len(units) != 2 or
                {item.get("unit") for item in units if isinstance(item, Mapping)} != expected_units or
                any(item.get("active_state") != "inactive" or item.get("main_pid") != 0
                    for item in units if isinstance(item, Mapping)) or
                not isinstance(gpu, Mapping) or not isinstance(gpu_devices, list) or
                gpu_ids != set(proof.get("gpu_uuids", [])) or len(gpu_devices) != len(gpu_ids) or
                gpu.get("processes") != [] or
                any(not isinstance(item, Mapping) or
                    isinstance(item.get("memory_used_mib"), bool) or
                    not isinstance(item.get("memory_used_mib"), (int, float)) or
                    item.get("memory_used_mib") != 0 for item in gpu_devices) or
                not isinstance(host, Mapping) or type(host.get("active_owners")) is not int or
                host.get("active_owners") != 0 or type(host.get("active_reservations")) is not int or
                host.get("active_reservations") != 0 or
                type(host.get("active_foreground_intents")) is not int or
                host.get("active_foreground_intents") != 0):
            raise ResourceControllerError("physical_release_fresh_observation_invalid")

        attested_slots = _validate_orphan_execution_slots(proof["orphan_execution_slots"])
        try:
            active_jobs = self.db.execute("""SELECT count(*) FROM jobs
                WHERE COALESCE(json_extract(record,'$.status'),'') NOT IN
                ('completed','partial','failed','cancelled')""").fetchone()[0]
            slot_count = self.db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("controller_quiescence_unavailable") from error
        if active_jobs != 0:
            raise ResourceControllerError("controller_work_not_quiescent")
        self._validate_recoverable_execution_slots(attested_slots, expected_count=slot_count)
        for slot in attested_slots:
            _require_process_absent(slot["owner_pid"], slot["owner_process_start"],
                                    error_prefix="orphan_execution_slot_owner")
        _require_process_absent(proof["supervisor_pid"], proof["supervisor_process_start"],
                                error_prefix="physical_release_supervisor")

        epoch_identity = {key: proof[key] for key in
            ("daemon_epoch", "supervisor_pid", "supervisor_process_start", "runtime_fingerprint")}
        rows = self._rows()
        unresolved = [tuple(row) for row in rows if tuple(row)[2] in
                      {"active", "idle_owned", "release_pending", "stale"}]
        if not unresolved:
            return self._existing_archived_epoch_recovery(intent, proof, attested_slots)
        if state.get("physical_state") != "pending":
            raise ResourceControllerError("permanent_release_veto_required")
        targets: list[str] = []
        preserved_receipts: dict[str, Any] = {}
        receipt_identities = set()
        for row in unresolved:
            session_id, epoch_text, row_state, receipt_text, request_id = row
            try:
                epoch = _epoch(json.loads(epoch_text))
            except (TypeError, ValueError, json.JSONDecodeError, ResourceControllerError) as error:
                raise ResourceControllerError("orphaned_epoch_session_identity_invalid") from error
            if (epoch != epoch_identity or request_id != intent.request_id or
                    row_state not in {"active", "release_pending"} or
                    (row_state == "active" and receipt_text is not None) or
                    (row_state == "release_pending" and not receipt_text)):
                raise ResourceControllerError("orphaned_epoch_session_set_incomplete")
            prior_receipt = None
            if receipt_text is not None:
                try:
                    prior_receipt = _receipt(session_id, json.loads(receipt_text), epoch)
                except (TypeError, ValueError, json.JSONDecodeError, ResourceControllerError) as error:
                    raise ResourceControllerError("orphaned_epoch_close_receipt_invalid") from error
                identity = (prior_receipt["owner_id"], prior_receipt["server_pid"],
                    prior_receipt["server_process_start"], prior_receipt["residency_generation"],
                    tuple(sorted(prior_receipt["gpu_uuids"])))
                if identity in receipt_identities:
                    raise ResourceControllerError("orphaned_epoch_close_receipt_duplicate")
                receipt_identities.add(identity)
                _require_process_absent(prior_receipt["server_pid"],
                    prior_receipt["server_process_start"], error_prefix="orphan_owned_server")
            preserved_receipts[session_id] = prior_receipt
            targets.append(session_id)

        cleanup_identities = set()
        observed_gpu_uuids = set()
        for item in proof["cleanup_receipts"]:
            if not isinstance(item, Mapping):
                raise ResourceControllerError("physical_release_proof_invalid")
            uuids = item.get("gpu_uuids")
            key = (item.get("owner_id"), item.get("owned_pid"),
                item.get("server_process_start"), item.get("generation"),
                tuple(sorted(uuids)) if isinstance(uuids, list) else ())
            if (not isinstance(item.get("owner_id"), str) or type(item.get("owned_pid")) is not int or
                    not isinstance(item.get("server_process_start"), str) or
                    not isinstance(item.get("generation"), str) or not isinstance(uuids, list) or
                    not uuids or any(not isinstance(uuid, str) or not uuid.startswith("GPU-") for uuid in uuids) or
                    item.get("released") is not True or item.get("process_released") is not True or
                    item.get("memory_released") is not True or key in cleanup_identities):
                raise ResourceControllerError("physical_release_proof_invalid")
            cleanup_identities.add(key)
            observed_gpu_uuids.update(uuids)
            _require_process_absent(item["owned_pid"], item["server_process_start"],
                                    error_prefix="orphan_owned_server")
        if (cleanup_identities != receipt_identities or
                sorted(observed_gpu_uuids) != sorted(set(proof["gpu_uuids"]))):
            raise ResourceControllerError("orphaned_epoch_physical_identity_mismatch")

        session_ids = sorted(targets)
        proof_value = dict(proof)
        proof_value["recovery_context"] = {
            "format": "PA1-MIXED-EPOCH-RECOVERY/1",
            "request_id": intent.request_id,
            "session_ids": session_ids,
            "preserved_close_receipts": preserved_receipts,
        }
        encoded_proof = _json(proof_value)
        proof_digest = hashlib.sha256(encoded_proof.encode("utf-8")).hexdigest()
        audit_id = uuid.uuid4().hex
        encoded_receipt = _json({"format": "PA1-ORPHANED-SESSION-RELEASE/1",
            "receipt_id": audit_id, "release_request_id": intent.request_id,
            "daemon_epoch": proof["daemon_epoch"], "session_ids": session_ids,
            "physical_release_proof_sha256": proof_digest,
            "preserved_close_receipts_sha256": hashlib.sha256(
                _json(preserved_receipts).encode("utf-8")).hexdigest()})

        if self.db.in_transaction:
            raise ResourceControllerError("physical_release_requires_committed_state")
        self.db.execute("SAVEPOINT pa1_archived_epoch_recovery")
        try:
            current_state = self.power_policy.snapshot()
            if (current_state.get("release_veto_active") is not True or
                    current_state.get("release_until") is not None or
                    current_state.get("release_request_id") != intent.request_id or
                    current_state.get("physical_state") != "pending"):
                raise ResourceControllerError("permanent_release_veto_required")
            try:
                active_jobs_now = self.db.execute("""SELECT count(*) FROM jobs
                    WHERE COALESCE(json_extract(record,'$.status'),'') NOT IN
                    ('completed','partial','failed','cancelled')""").fetchone()[0]
                current_slot_count = self.db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
                current_rows = self._rows()
            except sqlite3.OperationalError as error:
                raise ResourceControllerError("controller_quiescence_unavailable") from error
            current_unresolved = [tuple(row) for row in current_rows if tuple(row)[2] in
                                  {"active", "idle_owned", "release_pending", "stale"}]
            if (active_jobs_now != 0 or current_unresolved != unresolved or
                    len(current_unresolved) != len(session_ids)):
                raise ResourceControllerError("orphaned_epoch_session_set_changed")
            self._validate_recoverable_execution_slots(attested_slots, expected_count=current_slot_count)
            for slot in attested_slots:
                _require_process_absent(slot["owner_pid"], slot["owner_process_start"],
                                        error_prefix="orphan_execution_slot_owner")
            self.db.execute("""INSERT INTO pa1_orphan_resource_reconciliation_audit
                (receipt_id,release_request_id,daemon_epoch,session_ids,proof,proof_sha256,controller_host,reconciled_at)
                VALUES(?,?,?,?,?,?,?,?)""", (audit_id, intent.request_id, proof["daemon_epoch"],
                _json({"session_ids": session_ids}), encoded_proof, proof_digest, proof.host, now))
            for session_id in session_ids:
                changed = self.db.execute("""UPDATE pa1_owned_resource_sessions SET
                    state='released_verified',close_receipt=?,release_request_id=?,release_proof=?,updated=?
                    WHERE session_id=? AND state IN ('active','release_pending') AND release_request_id=?""",
                    (encoded_receipt, intent.request_id, encoded_proof, now,
                     session_id, intent.request_id)).rowcount
                if changed != 1:
                    raise ResourceControllerError("orphaned_epoch_session_changed_during_recovery")
            self.db.execute("RELEASE SAVEPOINT pa1_archived_epoch_recovery")
        except Exception:
            self.db.execute("ROLLBACK TO SAVEPOINT pa1_archived_epoch_recovery")
            self.db.execute("RELEASE SAVEPOINT pa1_archived_epoch_recovery")
            raise
        return session_ids

    def _existing_archived_epoch_recovery(self, intent: ReleaseIntent, proof: Mapping[str, Any],
                                          attested_slots: list[dict[str, Any]]) -> list[str]:
        """Resume only this same audited recovery after an interruption before ACK."""
        acknowledgment = self.verified_release_ack(intent)
        try:
            audits = self.db.execute("""SELECT daemon_epoch,session_ids,proof
                FROM pa1_orphan_resource_reconciliation_audit WHERE release_request_id=?
                AND daemon_epoch=?""", (intent.request_id, proof.get("daemon_epoch"))).fetchall()
            if len(audits) != 1:
                raise ResourceControllerError("orphaned_epoch_recovery_audit_missing")
            audit_epoch, session_text, proof_text = tuple(audits[0])
            audited = json.loads(proof_text)
            session_ids = json.loads(session_text).get("session_ids")
            context = audited.get("recovery_context") if isinstance(audited, Mapping) else None
            if (audit_epoch != proof.get("daemon_epoch") or not isinstance(session_ids, list) or
                    session_ids != sorted(acknowledgment.target_session_ids) or
                    not isinstance(context, Mapping) or
                    context.get("format") != "PA1-MIXED-EPOCH-RECOVERY/1" or
                    context.get("request_id") != intent.request_id or
                    context.get("session_ids") != session_ids or
                    audited.get("source_identity") != proof.get("source_identity") or
                    any(audited.get(key) != proof.get(key) for key in
                        ("supervisor_pid", "supervisor_process_start", "runtime_fingerprint")) or
                    audited.get("orphan_execution_slots") != attested_slots or
                    _physical_cleanup_identities(audited.get("cleanup_receipts")) !=
                        _physical_cleanup_identities(proof.get("cleanup_receipts"))):
                raise ResourceControllerError("orphaned_epoch_recovery_audit_mismatch")
        except ResourceControllerError:
            raise
        except Exception as error:
            raise ResourceControllerError("orphaned_epoch_recovery_audit_unavailable") from error
        return session_ids

    def _validate_recoverable_execution_slots(self, attested: list[dict[str, Any]], *,
                                              expected_count: int) -> list[dict[str, Any]]:
        """Match every remaining execution row to a terminal failed generation."""
        if expected_count != len(attested):
            raise ResourceControllerError("controller_work_not_quiescent")
        if not attested:
            return []
        try:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(execution_slots)")}
            required = {"job", "attempt", "owner_pid", "owner_start", "cleanup_failed"}
            if not required.issubset(columns):
                raise ResourceControllerError("controller_quiescence_unavailable")
            rows = self.db.execute("""SELECT job,attempt,owner_pid,owner_start,cleanup_failed
                FROM execution_slots ORDER BY job""").fetchall()
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("controller_quiescence_unavailable") from error
        captured_by_key = {(item["job_id"], item["attempt"]): item for item in attested}
        observed = []
        for row in rows:
            job_id, attempt, owner_pid, owner_start, cleanup_failed = tuple(row)
            captured = captured_by_key.get((job_id, attempt))
            if (captured is None or type(owner_pid) is not int or type(cleanup_failed) is not int or
                    cleanup_failed != 1 or owner_pid != captured["owner_pid"] or
                    owner_start != captured["owner_process_start"]):
                raise ResourceControllerError("orphan_execution_slot_state_invalid")
            try:
                job_row = self.db.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone()
                job = json.loads(job_row[0]) if job_row is not None else None
            except (sqlite3.OperationalError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ResourceControllerError("orphan_execution_slot_job_state_unavailable") from error
            if (not isinstance(job, dict) or job.get("status") not in _TERMINAL_JOB_STATES or
                    type(job.get("attempt")) is not int or job["attempt"] < attempt):
                raise ResourceControllerError("orphan_execution_slot_job_not_terminal")
            observed.append(captured)
        if len(observed) != len(attested):
            raise ResourceControllerError("orphan_execution_slot_state_invalid")
        for slot in observed:
            if _process_start_time(slot["owner_pid"]) == slot["owner_process_start"]:
                raise ResourceControllerError("orphan_execution_slot_owner_still_running")
        return observed

    def _rows(self):
        return self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()

    def reclaimable_session_ids(self, release_request_id: str) -> list[str]:
        """Select only receiptless sessions attached to this exact release intent.

        An interrupted broker can leave a row in ``release_pending`` before it
        persists the daemon-minted close receipt. The public snapshot omits
        receipt data, so orphan recovery must use this private, exact-intent
        query instead of inferring eligibility from the snapshot state alone.
        """
        if not isinstance(release_request_id, str) or not _HEX_32.fullmatch(release_request_id):
            raise ResourceControllerError("release_request_id_invalid")
        rows = self.db.execute("""SELECT session_id FROM pa1_owned_resource_sessions
            WHERE state IN ('active','release_pending') AND close_receipt IS NULL
            AND release_request_id=? ORDER BY session_id""", (release_request_id,)).fetchall()
        return [row[0] for row in rows]

    def stale_sessions_matching_idle_owner(self, intent: ReleaseIntent,
                                           status: Mapping[str, Any]) -> list[str]:
        """Return exact stale targets only when current idle slots match their receipts."""
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        power = self.power_policy.snapshot()
        if (power.get("release_veto_active") is not True or power.get("release_until") is not None or
                power.get("physical_state") != "pending" or
                power.get("release_request_id") != intent.request_id):
            raise ResourceControllerError("permanent_release_veto_required")
        slots = status.get("slots") if isinstance(status, Mapping) else None
        if (not isinstance(slots, list) or not slots or
                status.get("active_leases") != 0 or status.get("active_admissions") != 0):
            return []
        rows = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        unresolved = [row for row in rows if tuple(row)[2] in
                      {"active", "idle_owned", "release_pending", "stale"}]
        if not unresolved or any(tuple(row)[2] != "stale" or tuple(row)[4] != intent.request_id
                                 for row in unresolved):
            return []
        epoch = status.get("daemon_epoch")
        if (not isinstance(epoch, str) or not _HEX_64.fullmatch(epoch) or
                type(status.get("supervisor_pid")) is not int or
                not isinstance(status.get("supervisor_process_start"), str) or
                not isinstance(status.get("runtime_fingerprint"), str)):
            return []
        expected = {}
        for row in unresolved:
            session_id, epoch_text, _state, receipt_text, _request_id = tuple(row)
            try:
                row_epoch = json.loads(epoch_text)
                receipt = _receipt(session_id, json.loads(receipt_text), row_epoch)
            except (TypeError, ValueError, json.JSONDecodeError, ResourceControllerError):
                return []
            if (row_epoch.get("daemon_epoch") != epoch or
                    row_epoch.get("supervisor_pid") != status.get("supervisor_pid") or
                    row_epoch.get("supervisor_process_start") != status.get("supervisor_process_start") or
                    row_epoch.get("runtime_fingerprint") != status.get("runtime_fingerprint") or
                    _process_start_time(receipt["server_pid"]) != receipt["server_process_start"]):
                return []
            key = (receipt["slot_id"], receipt["owner_id"], receipt["server_pid"],
                   tuple(sorted(receipt["gpu_uuids"])))
            expected.setdefault(key, []).append((session_id, receipt))
        observed = set()
        for slot in slots:
            if (not isinstance(slot, Mapping) or slot.get("state") != "idle" or
                    slot.get("leased") is not False or not isinstance(slot.get("gpu_uuids"), list) or
                    any(not isinstance(item, str) or not item.startswith("GPU-")
                        for item in slot.get("gpu_uuids", []))):
                return []
            key = (slot.get("slot_id"), slot.get("owner_id"), slot.get("server_pid"),
                   tuple(sorted(slot.get("gpu_uuids", []))))
            if key not in expected or key in observed:
                return []
            observed.add(key)
        if observed != set(expected):
            return []
        return sorted(tuple(row)[0] for row in unresolved)

    def record_stale_sessions_after_physical_release(self, control: object, intent: ReleaseIntent,
                                                     proof: object, session_ids: Sequence[str]) -> list[str]:
        """Reconcile stale close receipts only against their sealed whole-epoch stop proof."""
        from ..observer_analysis import _is_verified_physical_release

        if not _is_verified_physical_release(proof):
            raise ResourceControllerError("verified_physical_release_required")
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        PowerPolicy._authorized(control, "request_release")
        targets = sorted(set(session_ids)) if isinstance(session_ids, Sequence) else []
        if not targets or targets != list(session_ids) or len(targets) != len(session_ids):
            raise ResourceControllerError("stale_session_target_set_invalid")
        captured = proof.get("captured_at")
        now = float(self.clock())
        if (isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                not math.isfinite(float(captured)) or now < float(captured) or now - float(captured) > 300):
            raise ResourceControllerError("physical_release_proof_expired")
        if (proof.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                proof.get("status") != "released_verified" or proof.get("released_verified") is not True or
                proof.get("stopped") is not True or proof.get("evicted") is not True or
                proof.get("quiescent") is not True or not isinstance(proof.get("daemon_epoch"), str) or
                not _HEX_64.fullmatch(proof["daemon_epoch"]) or
                type(proof.get("supervisor_pid")) is not int or proof["supervisor_pid"] <= 0 or
                not isinstance(proof.get("supervisor_process_start"), str) or
                not isinstance(proof.get("runtime_fingerprint"), str) or
                not _HEX_64.fullmatch(proof["runtime_fingerprint"]) or
                not isinstance(proof.get("cleanup_receipts"), list) or
                not isinstance(proof.get("gpu_uuids"), list) or not proof["gpu_uuids"] or
                any(not isinstance(item, str) or not item.startswith("GPU-") for item in proof["gpu_uuids"]) or
                not isinstance(getattr(proof, "host", None), str) or not proof.host):
            raise ResourceControllerError("physical_release_proof_invalid")
        power = self.power_policy.snapshot()
        if (power.get("release_veto_active") is not True or power.get("release_until") is not None or
                power.get("physical_state") != "pending" or power.get("release_request_id") != intent.request_id):
            raise ResourceControllerError("permanent_release_veto_required")
        rows = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        unresolved = [row for row in rows if tuple(row)[2] in
                      {"active", "idle_owned", "release_pending", "stale"}]
        if (not unresolved or {tuple(row)[0] for row in unresolved} != set(targets) or
                any(tuple(row)[2] != "stale" or tuple(row)[4] != intent.request_id for row in unresolved)):
            raise ResourceControllerError("stale_session_target_set_incomplete")
        expected_epochs = {tuple(row)[0]: tuple(row)[1] for row in unresolved}
        proof_epoch = proof.get("daemon_epoch")
        cleanup = proof["cleanup_receipts"]
        cleanup_by_identity = {}
        for item in cleanup:
            if not isinstance(item, Mapping):
                raise ResourceControllerError("physical_release_proof_invalid")
            gpu_uuids = item.get("gpu_uuids")
            if (item.get("released") is not True or item.get("process_released") is not True or
                    item.get("memory_released") is not True or type(item.get("owned_pid")) is not int or
                    not isinstance(item.get("owner_id"), str) or
                    not isinstance(item.get("server_process_start"), str) or
                    not isinstance(item.get("generation"), str) or not isinstance(gpu_uuids, list) or
                    not gpu_uuids or any(not isinstance(uuid, str) or not uuid.startswith("GPU-")
                        for uuid in gpu_uuids) or len(set(gpu_uuids)) != len(gpu_uuids)):
                raise ResourceControllerError("physical_release_proof_invalid")
            key = (item["owner_id"], item["owned_pid"], item["server_process_start"],
                   item["generation"], tuple(sorted(gpu_uuids)))
            if key in cleanup_by_identity:
                raise ResourceControllerError("physical_release_proof_invalid")
            cleanup_by_identity[key] = item
        matched = []
        expected_cleanup = set()
        for row in unresolved:
            session_id, epoch_text, _state, receipt_text, _request_id = tuple(row)
            try:
                epoch = json.loads(epoch_text)
                receipt = _receipt(session_id, json.loads(receipt_text), epoch)
            except (TypeError, ValueError, json.JSONDecodeError, ResourceControllerError) as error:
                raise ResourceControllerError("stale_session_close_receipt_invalid") from error
            if (epoch.get("daemon_epoch") != proof_epoch or
                    epoch.get("supervisor_pid") != proof.get("supervisor_pid") or
                    epoch.get("supervisor_process_start") != proof.get("supervisor_process_start") or
                    epoch.get("runtime_fingerprint") != proof.get("runtime_fingerprint")):
                raise ResourceControllerError("stale_session_epoch_mismatch")
            key = (receipt["owner_id"], receipt["server_pid"], receipt["server_process_start"],
                   receipt["residency_generation"], tuple(sorted(receipt["gpu_uuids"])))
            expected_cleanup.add(key)
            item = cleanup_by_identity.get(key)
            if item is None:
                raise ResourceControllerError("stale_session_physical_identity_mismatch")
            matched.append((session_id, receipt, item))
        if set(cleanup_by_identity) != expected_cleanup:
            raise ResourceControllerError("stale_session_physical_target_set_mismatch")
        observed_gpu_uuids = sorted({uuid for item in cleanup for uuid in item["gpu_uuids"]})
        if observed_gpu_uuids != sorted(set(proof["gpu_uuids"])):
            raise ResourceControllerError("physical_release_proof_gpu_scope_invalid")

        if self.db.in_transaction:
            raise ResourceControllerError("physical_release_requires_committed_state")
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("controller_quiescence_unavailable") from error
        # Fence admissions and recheck every proof precondition after taking
        # the write lock, before any row can become released_verified.
        power = self.power_policy.snapshot()
        if (power.get("release_veto_active") is not True or power.get("release_until") is not None or
                power.get("physical_state") != "pending" or power.get("release_request_id") != intent.request_id):
            self.db.rollback()
            raise ResourceControllerError("permanent_release_veto_required")
        try:
            active_jobs = self.db.execute("""SELECT count(*) FROM jobs
                WHERE COALESCE(json_extract(record,'$.status'),'') NOT IN
                    ('completed','partial','failed','cancelled')""").fetchone()[0]
            execution_slots = self.db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
        except sqlite3.OperationalError as error:
            self.db.rollback()
            raise ResourceControllerError("controller_quiescence_unavailable") from error
        if active_jobs != 0 or execution_slots != 0:
            self.db.rollback()
            raise ResourceControllerError("controller_work_not_quiescent")
        current_rows = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        current_unresolved = [row for row in current_rows if tuple(row)[2] in
                              {"active", "idle_owned", "release_pending", "stale"}]
        expected_receipts = {session_id: _json(receipt) for session_id, receipt, _item in matched}
        if (not current_unresolved or {tuple(row)[0] for row in current_unresolved} != set(targets) or
                any(tuple(row)[2] != "stale" or tuple(row)[4] != intent.request_id or
                    tuple(row)[3] != expected_receipts.get(tuple(row)[0]) or
                    tuple(row)[1] != expected_epochs.get(tuple(row)[0])
                    for row in current_unresolved)):
            self.db.rollback()
            raise ResourceControllerError("stale_session_target_set_incomplete")
        proof_text = _json(proof)
        proof_hash = hashlib.sha256(proof_text.encode("utf-8")).hexdigest()
        audit_id = uuid.uuid4().hex
        session_json = _json({"session_ids": targets})
        audit_proof = {"format": "PA1-STALE-SESSION-RELEASE-AUDIT/1",
            "request_id": intent.request_id, "daemon_epoch": proof_epoch,
            "physical_release_proof_sha256": proof_hash,
            "whole_epoch_proof": json.loads(proof_text),
            "matches": [{"session_id": session_id, "owner_id": receipt["owner_id"],
                "server_pid": receipt["server_pid"], "server_process_start": receipt["server_process_start"],
                "residency_generation": receipt["residency_generation"],
                "gpu_uuids": receipt["gpu_uuids"]} for session_id, receipt, _item in matched]}
        audit_text = _json(audit_proof)
        audit_hash = hashlib.sha256(audit_text.encode("utf-8")).hexdigest()
        self.db.execute("SAVEPOINT pa1_stale_reconcile")
        try:
            self.db.execute("""INSERT INTO pa1_stale_resource_reconciliation_audit
                (receipt_id,release_request_id,daemon_epoch,session_ids,proof,proof_sha256,controller_host,reconciled_at)
                VALUES(?,?,?,?,?,?,?,?)""", (audit_id, intent.request_id, proof_epoch, session_json,
                audit_text, audit_hash, proof.host, now))
            for session_id, receipt, _item in matched:
                rowproof = {"format": "PC-PA1-STALE-OWNED-RELEASE-PROOF/1",
                    "request_id": intent.request_id, "session_id": session_id,
                    "daemon_epoch": proof_epoch, "receipt_id": audit_id,
                    "physical_release_proof_sha256": proof_hash, "audit_sha256": audit_hash,
                    **{key: receipt[key] for key in ("slot_id", "owner_id", "host_lease_id",
                        "residency_generation", "server_pid", "server_process_start", "gpu_uuids")},
                    "process_released": True, "memory_released": True, "host_released": True}
                changed = self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='released_verified',
                    release_proof=?,updated=? WHERE session_id=? AND state='stale' AND release_request_id=?
                    AND close_receipt=?""",
                    (_json(rowproof), now, session_id, intent.request_id, _json(receipt))).rowcount
                if changed != 1:
                    raise ResourceControllerError("stale_session_changed_during_reconcile")
            self.db.execute("RELEASE SAVEPOINT pa1_stale_reconcile")
        except Exception:
            self.db.execute("ROLLBACK TO SAVEPOINT pa1_stale_reconcile")
            self.db.execute("RELEASE SAVEPOINT pa1_stale_reconcile")
            raise
        return targets

    def release_owned(self, intent: ReleaseIntent,
                      callback: Callable[[str, str, Sequence[Mapping[str, Any]]], Mapping[str, Any]]) -> str:
        """Deliver a committed intent to the exact owner; only verified receipts complete it.

        Caller commits the JobService transaction before entry.  An open session
        holds the global veto and causes a pending result; it is closed and
        recorded by the broker before a later reconciliation retries delivery.
        """
        if self.db.in_transaction:
            raise PowerPolicyError("release_callback_requires_committed_state")
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        if not callable(callback):
            raise ResourceControllerError("release_callback_invalid")
        all_rows = self._rows()
        if not all_rows:
            # Validate that the intent is committed without issuing an empty
            # owner operation that could be mistaken for physical proof.
            observed: dict[str, Any] = {}
            result = self.power_policy.deliver_release_intent(intent, lambda rid, reason: observed.update(
                request_id=rid, reason=reason))
            return "pending_no_owned_resources" if result == "pending_owner_verification" else result

        non_superseded = [row for row in all_rows if tuple(row)[2] != "superseded"]
        if (non_superseded and all(tuple(row)[2] == "released_verified" and
                                   tuple(row)[4] == intent.request_id for row in non_superseded)):
            self.power_policy.deliver_release_intent(intent, lambda _rid, _reason: None)
            return "released_verified"
        # A fresh intent may follow resume/expiry after all current resources
        # were already verified under an older intent. Carry that state forward
        # only when there are no unresolved rows; acknowledgment revalidates
        # every stored proof before updating PowerPolicy.
        if (non_superseded and all(tuple(row)[2] == "released_verified" for row in non_superseded)):
            self.power_policy.deliver_release_intent(intent, lambda _rid, _reason: None)
            return "released_verified"
        tracked_rows = [row for row in all_rows if tuple(row)[2] not in {"released_verified", "superseded"}]
        rows = [row for row in tracked_rows if tuple(row)[2] != "released_verified"]

        decoded = []
        active = False
        stale_ids = []
        for row in rows:
            session_id, epoch_text, state, receipt_text, _ = tuple(row)
            if state == "active" or receipt_text is None:
                active = True
                continue
            if state == "stale":
                stale_ids.append(session_id)
                continue
            receipt = json.loads(receipt_text)
            decoded.append(receipt)
        if active:
            # Do not begin a write transaction before PowerPolicy validates the
            # committed release intent and the external callback boundary.
            self.power_policy.deliver_release_intent(intent, lambda _rid, _reason: None)
            self.db.execute("""UPDATE pa1_owned_resource_sessions SET
                state=CASE WHEN state='idle_owned' THEN 'release_pending' ELSE state END,
                release_request_id=?,updated=? WHERE state IN ('active','idle_owned','release_pending','stale')""",
                (intent.request_id, float(self.clock())))
            return "pending_sessions_open"
        if not decoded:
            self.power_policy.deliver_release_intent(intent, lambda _rid, _reason: None)
            if stale_ids:
                self.db.execute("""UPDATE pa1_owned_resource_sessions SET release_request_id=?,updated=?
                    WHERE state='stale'""", (intent.request_id, float(self.clock())))
            return "pending_reconciliation_required" if stale_ids else "pending_no_owned_resources"

        records = tuple(sorted(decoded, key=lambda item: item["session_id"]))
        response: dict[str, Any] = {}

        def deliver(request_id: str, reason: str) -> None:
            # SupervisorClient accepts at most 64 durable receipts per call and
            # the daemon keeps a bounded set of close capabilities. Process a
            # stable bounded prefix at a time so older rows cannot prevent
            # current owned targets from reaching the owner. Keep every row's
            # result for the exact-set proof check below; a failed/missing row
            # therefore remains pending instead of being treated as released.
            outcomes: list[Mapping[str, Any]] = []
            batch_statuses: list[str] = []
            for start in range(0, len(records), _OWNED_RELEASE_INPUT_BATCH):
                batch = records[start:start + _OWNED_RELEASE_INPUT_BATCH]
                result = callback(request_id, reason, batch)
                if not isinstance(result, Mapping):
                    continue
                sessions = result.get("sessions")
                if isinstance(sessions, list):
                    outcomes.extend(item for item in sessions if isinstance(item, Mapping))
                status = result.get("status")
                if isinstance(status, str):
                    batch_statuses.append(status)
            response["sessions"] = outcomes
            response["status"] = ("released" if batch_statuses and all(
                status == "released" for status in batch_statuses) else
                "stale" if "stale" in batch_statuses else "pending")

        self.power_policy.deliver_release_intent(intent, deliver)
        # The owner callback boundary requires a committed intent and no open
        # SQLite transaction. Project stale rows only after that validated
        # callback has returned; they are not owner-releasable receipts.
        if stale_ids:
            self.db.execute("""UPDATE pa1_owned_resource_sessions SET release_request_id=?,updated=?
                WHERE state='stale'""", (intent.request_id, float(self.clock())))
        requested = {item["session_id"] for item in records}
        outcomes = response.get("sessions")
        if isinstance(outcomes, list):
            by_id = {item.get("session_id"): item for item in outcomes if isinstance(item, Mapping)}
            receipts = {item["session_id"]: item for item in records}
            proofs = {session_id: self._verified_proof(intent, receipts[session_id], by_id[session_id])
                      for session_id in requested if session_id in by_id and session_id in receipts}
            if set(by_id) == requested:
                complete = True
                for session_id in sorted(requested):
                    proof = proofs.get(session_id)
                    if proof is not None:
                        self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='released_verified',
                            release_request_id=?,release_proof=?,updated=? WHERE session_id=?
                            AND state IN ('idle_owned','release_pending')""",
                            (intent.request_id, _json(proof), float(self.clock()), session_id))
                    elif by_id[session_id].get("stale") is True:
                        complete = False
                        self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='stale',
                            release_request_id=?,updated=? WHERE session_id=?""",
                            (intent.request_id, float(self.clock()), session_id))
                    else:
                        complete = False
                        self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='release_pending',
                            release_request_id=?,updated=? WHERE session_id=?
                            AND state IN ('idle_owned','release_pending')""",
                            (intent.request_id, float(self.clock()), session_id))
                if complete and not stale_ids:
                    return "released_verified"
                if stale_ids or any(by_id[session_id].get("stale") is True for session_id in requested):
                    return "pending_reconciliation_required"
                return "pending_owner_verification"
        if stale_ids:
            for session_id in stale_ids:
                self.db.execute("""UPDATE pa1_owned_resource_sessions SET release_request_id=?,updated=?
                    WHERE session_id=? AND state='stale'""", (intent.request_id, float(self.clock()), session_id))
            return "pending_reconciliation_required"
        for session_id in requested:
            self.db.execute("""UPDATE pa1_owned_resource_sessions SET state='release_pending',
                release_request_id=?,updated=? WHERE session_id=? AND state='idle_owned'""",
                (intent.request_id, float(self.clock()), session_id))
        return "pending_owner_verification"

    def _verified_proof(self, intent: ReleaseIntent, receipt: Mapping[str, Any],
                        outcome: Mapping[str, Any]) -> dict[str, Any] | None:
        """Join daemon cleanup evidence to its persisted close capability identity."""
        identity_fields = ("daemon_epoch", "session_id", "slot_id", "owner_id", "host_lease_id",
                           "residency_generation", "server_pid", "server_process_start", "gpu_uuids")
        if (outcome.get("released") is not True or outcome.get("process_released") is not True or
                outcome.get("memory_released") is not True or outcome.get("host_released") is not True or
                any(outcome.get(key) != receipt.get(key) for key in identity_fields) or
                outcome.get("session_id") != receipt.get("session_id")):
            return None
        observation = outcome.get("observation")
        observed = observation.get("observed_unix") if isinstance(observation, Mapping) else None
        if (not isinstance(observed, (int, float)) or isinstance(observed, bool) or
                not math.isfinite(float(observed)) or observed <= 0 or observed > float(self.clock()) + 5):
            return None
        processes = observation.get("processes")
        if (not isinstance(processes, list) or
                any(isinstance(row, Mapping) and row.get("pid") == receipt["server_pid"] for row in processes)):
            return None
        proof = {"format": "PC-PA1-OWNED-RELEASE-PROOF/1", "request_id": intent.request_id,
                 **{key: receipt[key] for key in identity_fields},
                 "process_released": True, "memory_released": True, "host_released": True,
                 "observation": dict(observation)}
        return proof

    def verified_release_ack(self, intent: ReleaseIntent) -> _VerifiedOwnedReleaseAck:
        """Verify committed owner rows and mint PowerPolicy's opaque acknowledgement."""
        if self.db.in_transaction:
            raise ResourceControllerError("verified_release_ack_requires_committed_rows")
        return self._verify_release_ack(intent)

    def _verified_release_ack_under_lock(self, intent: ReleaseIntent) -> _VerifiedOwnedReleaseAck:
        """Revalidate rows while PowerPolicy holds BEGIN IMMEDIATE through its update."""
        if not self.db.in_transaction:
            raise ResourceControllerError("verified_release_ack_write_lock_required")
        return self._verify_release_ack(intent)

    def _verify_release_ack(self, intent: ReleaseIntent) -> _VerifiedOwnedReleaseAck:
        before = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,
            release_request_id,release_proof FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        acknowledgment = self._verify_release_ack_rows(intent)
        after = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,
            release_request_id,release_proof FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        if [tuple(row) for row in before] != [tuple(row) for row in after]:
            raise ResourceControllerError("verified_release_target_set_changed")
        return acknowledgment

    def _load_orphan_release_audit(self, receipt_id: str) -> dict[str, Any]:
        """Load an immutable initial audit, including pre-existing dashed UUID IDs."""
        if not _orphan_audit_id(receipt_id):
            raise ResourceControllerError("verified_release_audit_mismatch")
        try:
            base = self.db.execute("""SELECT release_request_id,daemon_epoch,session_ids,proof,
                proof_sha256,controller_host FROM pa1_orphan_resource_reconciliation_audit
                WHERE receipt_id=?""", (receipt_id,)).fetchone()
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("verified_release_audit_unavailable") from error
        if base is None:
            raise ResourceControllerError("verified_release_audit_missing")
        request_id, epoch, sessions_text, proof_text, digest, host = tuple(base)
        try:
            sessions = json.loads(sessions_text).get("session_ids")
            proof = json.loads(proof_text)
            actual_digest = hashlib.sha256(proof_text.encode("utf-8")).hexdigest()
            canonical_digest = hashlib.sha256(_json(proof).encode("utf-8")).hexdigest()
        except (TypeError, ValueError, AttributeError, json.JSONDecodeError) as error:
            raise ResourceControllerError("verified_release_audit_mismatch") from error
        if (not isinstance(sessions, list) or sessions != sorted(sessions) or
                len(sessions) != len(set(sessions)) or actual_digest != digest or
                canonical_digest != digest or not isinstance(host, str) or not host or
                not isinstance(proof, Mapping)):
            raise ResourceControllerError("verified_release_audit_mismatch")
        return {"receipt_id": receipt_id, "release_request_id": request_id,
            "daemon_epoch": epoch, "session_ids": sessions, "proof": proof,
            "proof_text": proof_text, "proof_sha256": digest, "controller_host": host}

    def _verify_release_ack_rows(self, intent: ReleaseIntent) -> _VerifiedOwnedReleaseAck:
        if not isinstance(intent, ReleaseIntent):
            raise ResourceControllerError("release_intent_invalid")
        state = self.power_policy.snapshot()
        if state["release_request_id"] != intent.request_id:
            raise ResourceControllerError("release_intent_stale")
        rows = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id,release_proof
            FROM pa1_owned_resource_sessions WHERE release_request_id=? ORDER BY session_id""",
            (intent.request_id,)).fetchall()
        if not rows:
            return self._verified_historical_release_ack(intent)
        session_ids = []
        proofs = []
        aggregate_rows = []
        stale_rows = []
        for row in rows:
            session_id, epoch_text, status, receipt_text, request_id, proof_text = tuple(row)
            if status != "released_verified" or not receipt_text or request_id != intent.request_id or not proof_text:
                raise ResourceControllerError("verified_release_target_incomplete")
            receipt = json.loads(receipt_text)
            proof = json.loads(proof_text)
            if receipt.get("format") == "PA1-ORPHANED-SESSION-RELEASE/1":
                aggregate_rows.append((session_id, receipt, proof))
                session_ids.append(session_id)
                continue
            if proof.get("format") == "PC-PA1-STALE-OWNED-RELEASE-PROOF/1":
                if (proof.get("request_id") != intent.request_id or proof.get("session_id") != session_id or
                        any(proof.get(key) != receipt.get(key) for key in
                            ("daemon_epoch", "session_id", "slot_id", "owner_id", "host_lease_id",
                             "residency_generation", "server_pid", "server_process_start", "gpu_uuids")) or
                        any(proof.get(key) is not True for key in
                            ("process_released", "memory_released", "host_released"))):
                    raise ResourceControllerError("verified_release_proof_mismatch")
                stale_rows.append((session_id, receipt, proof, json.loads(epoch_text)))
                session_ids.append(session_id)
                continue
            if (proof.get("format") != "PC-PA1-OWNED-RELEASE-PROOF/1" or
                    proof.get("request_id") != intent.request_id or
                    proof.get("session_id") != session_id or
                    any(proof.get(key) != receipt.get(key) for key in
                        ("daemon_epoch", "session_id", "slot_id", "owner_id", "host_lease_id",
                         "residency_generation", "server_pid", "server_process_start", "gpu_uuids")) or
                    any(proof.get(key) is not True for key in
                        ("process_released", "memory_released", "host_released"))):
                raise ResourceControllerError("verified_release_proof_mismatch")
            session_ids.append(session_id)
            proofs.append(proof)
        if aggregate_rows:
            if stale_rows or len(aggregate_rows) != len(rows):
                raise ResourceControllerError("verified_release_proof_mismatch")
            _, first_receipt, first_proof = aggregate_rows[0]
            audited = self._load_orphan_release_audit(first_receipt["receipt_id"])
            audit_request, audit_epoch = audited["release_request_id"], audited["daemon_epoch"]
            audit_sessions, aggregate_proof = audited["session_ids"], audited["proof"]
            audit_proof, audit_hash = audited["proof_text"], audited["proof_sha256"]
            audit_host = audited["controller_host"]
            target_ids = sorted(session_ids)
            if (audit_request != intent.request_id or audit_epoch != first_receipt.get("daemon_epoch") or
                    not isinstance(audit_host, str) or not audit_host or
                    audit_sessions != target_ids or
                    first_proof != aggregate_proof or aggregate_proof.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                    aggregate_proof.get("daemon_epoch") != audit_epoch or
                    aggregate_proof.get("stopped") is not True or aggregate_proof.get("quiescent") is not True or
                    self.clock() < float(aggregate_proof.get("captured_at", 0)) or
                    self.clock() - float(aggregate_proof.get("captured_at", 0)) > 300 or
                    first_receipt.get("session_ids") != target_ids or
                    first_receipt.get("physical_release_proof_sha256") != audit_hash or
                    any(receipt != first_receipt or proof != first_proof for _, receipt, proof in aggregate_rows)):
                raise ResourceControllerError("verified_release_audit_mismatch")
            outstanding = self.db.execute("""SELECT session_id,state,release_request_id
                FROM pa1_owned_resource_sessions WHERE state<>'superseded'
                AND (state<>'released_verified' OR release_request_id=?) ORDER BY session_id""",
                (intent.request_id,)).fetchall()
            if (not outstanding or any(tuple(item)[2] != intent.request_id for item in outstanding) or
                    {tuple(item)[0] for item in outstanding} != set(target_ids)):
                raise ResourceControllerError("verified_release_target_set_incomplete")
            digest = hashlib.sha256(_json({"request_id": intent.request_id,
                "receipt_id": first_receipt["receipt_id"], "proof_sha256": audit_hash,
                "target_session_ids": target_ids}).encode("utf-8")).hexdigest()
            return _VerifiedOwnedReleaseAck(_ACK_SEAL, intent.request_id, target_ids, digest)
        if stale_rows:
            if aggregate_rows:
                raise ResourceControllerError("verified_release_proof_mismatch")
            audit_ids = {proof.get("receipt_id") for _, _, proof, _ in stale_rows}
            if len(audit_ids) != 1:
                raise ResourceControllerError("verified_release_audit_mismatch")
            audit_id = next(iter(audit_ids))
            audit_info = self._verify_historical_stale_audit(intent.request_id, audit_id, stale_rows)
            target_ids = sorted(session_ids)
            outstanding = self.db.execute("""SELECT session_id,state,release_request_id
                FROM pa1_owned_resource_sessions WHERE state<>'superseded'
                AND (state<>'released_verified' OR release_request_id=?) ORDER BY session_id""",
                (intent.request_id,)).fetchall()
            if (not outstanding or any(tuple(item)[2] != intent.request_id for item in outstanding) or
                    {tuple(item)[0] for item in outstanding} != set(target_ids)):
                raise ResourceControllerError("verified_release_target_set_incomplete")
            digest = hashlib.sha256(_json({"request_id": intent.request_id,
                "audit_id": audit_id, "audit_sha256": audit_info["audit_sha256"],
                "target_session_ids": target_ids,
                "ordinary_proofs": proofs}).encode("utf-8")).hexdigest()
            return _VerifiedOwnedReleaseAck(_ACK_SEAL, intent.request_id, target_ids, digest)
        outstanding = self.db.execute("""SELECT session_id,state,release_request_id
            FROM pa1_owned_resource_sessions WHERE state<>'superseded'
            AND (state<>'released_verified' OR release_request_id=?) ORDER BY session_id""",
            (intent.request_id,)).fetchall()
        if (not outstanding or any(tuple(row)[2] != intent.request_id for row in outstanding) or
                {tuple(row)[0] for row in outstanding} != set(session_ids)):
            raise ResourceControllerError("verified_release_target_set_incomplete")
        digest = hashlib.sha256(_json({"request_id": intent.request_id,
            "proofs": proofs}).encode("utf-8")).hexdigest()
        return _VerifiedOwnedReleaseAck(_ACK_SEAL, intent.request_id, session_ids, digest)

    def _verified_historical_release_ack(self, intent: ReleaseIntent) -> _VerifiedOwnedReleaseAck:
        """Carry forward complete ordinary and audited stale proofs with no current targets."""
        all_rows = self.db.execute("""SELECT session_id,supervisor_epoch,state,close_receipt,release_request_id,release_proof
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        current = [tuple(row) for row in all_rows if tuple(row)[2] != "superseded"]
        if not current or any(row[2] != "released_verified" for row in current):
            raise ResourceControllerError("verified_release_target_set_empty")
        session_ids = []
        historical = []
        stale_groups: dict[tuple[str, str], list[tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]] = {}
        aggregate_groups: dict[str, list[tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]] = {}
        proof_kinds: dict[str, set[str]] = {}
        for session_id, epoch_text, status, receipt_text, request_id, proof_text in current:
            if (status != "released_verified" or not receipt_text or not proof_text or
                    not isinstance(request_id, str) or not _HEX_32.fullmatch(request_id)):
                raise ResourceControllerError("verified_release_target_incomplete")
            try:
                raw_receipt = json.loads(receipt_text)
                proof = json.loads(proof_text)
            except (TypeError, ValueError, json.JSONDecodeError, ResourceControllerError) as error:
                raise ResourceControllerError("verified_release_proof_mismatch") from error
            try:
                epoch = json.loads(epoch_text)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ResourceControllerError("verified_release_proof_mismatch") from error
            if isinstance(raw_receipt, Mapping) and raw_receipt.get("format") == "PA1-ORPHANED-SESSION-RELEASE/1":
                if (not isinstance(proof, Mapping) or proof.get("request_id") != request_id or
                        raw_receipt.get("release_request_id") != request_id or
                        not _orphan_audit_id(raw_receipt.get("receipt_id"))):
                    raise ResourceControllerError("verified_release_proof_mismatch")
                aggregate_groups.setdefault(request_id, []).append(
                    (session_id, raw_receipt, proof, epoch))
                proof_kinds.setdefault(request_id, set()).add("aggregate")
                session_ids.append(session_id)
                historical.append({"session_id": session_id, "request_id": request_id,
                    "proof_sha256": hashlib.sha256(_json(proof).encode("utf-8")).hexdigest()})
                continue
            try:
                receipt = _receipt(session_id, raw_receipt, epoch)
            except ResourceControllerError as error:
                raise ResourceControllerError("verified_release_proof_mismatch") from error
            identity = ("daemon_epoch", "session_id", "slot_id", "owner_id", "host_lease_id",
                        "residency_generation", "server_pid", "server_process_start", "gpu_uuids")
            if (not isinstance(proof, Mapping) or proof.get("request_id") != request_id or
                    any(proof.get(key) != receipt.get(key) for key in identity)):
                raise ResourceControllerError("verified_release_proof_mismatch")
            if proof.get("format") == "PC-PA1-OWNED-RELEASE-PROOF/1":
                proof_kinds.setdefault(request_id, set()).add("ordinary")
                if any(proof.get(key) is not True for key in
                       ("process_released", "memory_released", "host_released")):
                    raise ResourceControllerError("verified_release_proof_mismatch")
                observation = proof.get("observation")
                observed = observation.get("observed_unix") if isinstance(observation, Mapping) else None
                processes = observation.get("processes") if isinstance(observation, Mapping) else None
                if (isinstance(observed, bool) or not isinstance(observed, (int, float)) or
                        not math.isfinite(float(observed)) or observed <= 0 or
                        observed > float(self.clock()) + 5 or not isinstance(processes, list) or
                        any(isinstance(item, Mapping) and item.get("pid") == receipt["server_pid"]
                            for item in processes)):
                    raise ResourceControllerError("verified_release_proof_mismatch")
            elif proof.get("format") == "PC-PA1-STALE-OWNED-RELEASE-PROOF/1":
                proof_kinds.setdefault(request_id, set()).add("stale")
                if (any(proof.get(key) is not True for key in
                        ("process_released", "memory_released", "host_released")) or
                        not isinstance(proof.get("receipt_id"), str) or
                        not _HEX_32.fullmatch(proof["receipt_id"]) or
                        not isinstance(proof.get("audit_sha256"), str) or
                        not _HEX_64.fullmatch(proof["audit_sha256"]) or
                        not isinstance(proof.get("physical_release_proof_sha256"), str) or
                        not _HEX_64.fullmatch(proof["physical_release_proof_sha256"])):
                    raise ResourceControllerError("verified_release_proof_mismatch")
                stale_groups.setdefault((request_id, proof["receipt_id"]), []).append(
                    (session_id, receipt, proof, epoch))
            else:
                raise ResourceControllerError("verified_release_proof_mismatch")
            session_ids.append(session_id)
            historical.append({"session_id": session_id, "request_id": request_id,
                "proof_sha256": hashlib.sha256(_json(proof).encode("utf-8")).hexdigest()})
        audit_digests = []
        if any("aggregate" in kinds and len(kinds) != 1 for kinds in proof_kinds.values()):
            raise ResourceControllerError("verified_release_proof_mismatch")
        audit_ids_by_request: dict[str, set[str]] = {}
        for request_id, audit_id in stale_groups:
            audit_ids_by_request.setdefault(request_id, set()).add(audit_id)
        if any(len(audit_ids) != 1 for audit_ids in audit_ids_by_request.values()):
            raise ResourceControllerError("verified_release_audit_mismatch")
        for (request_id, audit_id), stale_rows in sorted(stale_groups.items()):
            audit_digests.append(self._verify_historical_stale_audit(request_id, audit_id, stale_rows))
        for request_id, aggregate_rows in sorted(aggregate_groups.items()):
            audit_digests.append(self._verify_historical_aggregate(request_id, aggregate_rows))
        digest = hashlib.sha256(_json({"request_id": intent.request_id,
            "historical_verified_sessions": historical,
            "historical_stale_audits": audit_digests}).encode("utf-8")).hexdigest()
        return _VerifiedOwnedReleaseAck(_ACK_SEAL, intent.request_id, session_ids, digest)

    def _verify_historical_stale_audit(self, request_id: str, audit_id: str,
                                       stale_rows: Sequence[tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
        """Revalidate a stored stale-session audit against its exact whole-epoch proof."""
        audit_hashes = {proof.get("audit_sha256") for _, _, proof, _ in stale_rows}
        physical_hashes = {proof.get("physical_release_proof_sha256") for _, _, proof, _ in stale_rows}
        if len(audit_hashes) != 1 or len(physical_hashes) != 1:
            raise ResourceControllerError("verified_release_audit_mismatch")
        try:
            audited = self.db.execute("""SELECT release_request_id,daemon_epoch,session_ids,proof,
                proof_sha256,controller_host FROM pa1_stale_resource_reconciliation_audit
                WHERE receipt_id=?""", (audit_id,)).fetchone()
        except sqlite3.OperationalError as error:
            raise ResourceControllerError("verified_release_audit_unavailable") from error
        if audited is None:
            raise ResourceControllerError("verified_release_audit_missing")
        audit_request, audit_epoch, audit_sessions, audit_text, audit_digest, audit_host = tuple(audited)
        try:
            audit = json.loads(audit_text)
            whole_epoch = audit["whole_epoch_proof"]
            physical_digest = hashlib.sha256(_json(whole_epoch).encode("utf-8")).hexdigest()
            session_set = json.loads(audit_sessions).get("session_ids")
        except (TypeError, ValueError, KeyError, AttributeError, json.JSONDecodeError) as error:
            raise ResourceControllerError("verified_release_audit_mismatch") from error
        target_ids = sorted(session_id for session_id, _receipt, _proof, _epoch in stale_rows)
        expected_matches = [{"session_id": session_id, "owner_id": receipt["owner_id"],
            "server_pid": receipt["server_pid"], "server_process_start": receipt["server_process_start"],
            "residency_generation": receipt["residency_generation"], "gpu_uuids": receipt["gpu_uuids"]}
            for session_id, receipt, _proof, _epoch in stale_rows]
        cleanup = whole_epoch.get("cleanup_receipts") if isinstance(whole_epoch, Mapping) else None
        gpu_uuids = whole_epoch.get("gpu_uuids") if isinstance(whole_epoch, Mapping) else None
        cleanup_by_identity = {}
        if isinstance(cleanup, list):
            for item in cleanup:
                if not isinstance(item, Mapping):
                    raise ResourceControllerError("verified_release_audit_mismatch")
                item_uuids = item.get("gpu_uuids")
                if (item.get("released") is not True or item.get("process_released") is not True or
                        item.get("memory_released") is not True or type(item.get("owned_pid")) is not int or
                        not isinstance(item.get("owner_id"), str) or not item["owner_id"] or
                        not isinstance(item.get("server_process_start"), str) or not item["server_process_start"] or
                        not isinstance(item.get("generation"), str) or not item["generation"] or
                        not isinstance(item_uuids, list) or not item_uuids or
                        any(not isinstance(uuid, str) or not uuid.startswith("GPU-") for uuid in item_uuids) or
                        len(set(item_uuids)) != len(item_uuids)):
                    raise ResourceControllerError("verified_release_audit_mismatch")
                key = (item["owner_id"], item["owned_pid"], item["server_process_start"],
                       item["generation"], tuple(sorted(item_uuids)))
                if key in cleanup_by_identity:
                    raise ResourceControllerError("verified_release_audit_mismatch")
                cleanup_by_identity[key] = item
        expected_cleanup = set()
        for session_id, receipt, _proof, epoch in stale_rows:
            if (epoch.get("daemon_epoch") != whole_epoch.get("daemon_epoch") or
                    epoch.get("supervisor_pid") != whole_epoch.get("supervisor_pid") or
                    epoch.get("supervisor_process_start") != whole_epoch.get("supervisor_process_start") or
                    epoch.get("runtime_fingerprint") != whole_epoch.get("runtime_fingerprint")):
                raise ResourceControllerError("verified_release_audit_mismatch")
            expected_cleanup.add((receipt["owner_id"], receipt["server_pid"],
                receipt["server_process_start"], receipt["residency_generation"],
                tuple(sorted(receipt["gpu_uuids"]))))
        observed_gpu_uuids = sorted({uuid for item in cleanup or [] for uuid in item["gpu_uuids"]})
        captured_at = whole_epoch.get("captured_at") if isinstance(whole_epoch, Mapping) else None
        if (audit_request != request_id or audit.get("format") != "PA1-STALE-SESSION-RELEASE-AUDIT/1" or
                audit.get("request_id") != request_id or audit.get("daemon_epoch") != audit_epoch or
                not isinstance(audit_host, str) or not audit_host or session_set != target_ids or
                hashlib.sha256(audit_text.encode("utf-8")).hexdigest() != audit_digest or
                audit.get("physical_release_proof_sha256") != physical_digest or
                physical_digest not in physical_hashes or audit.get("matches") != expected_matches or
                not isinstance(whole_epoch, Mapping) or
                whole_epoch.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                whole_epoch.get("status") != "released_verified" or
                whole_epoch.get("released_verified") is not True or
                whole_epoch.get("stopped") is not True or whole_epoch.get("quiescent") is not True or
                whole_epoch.get("evicted") is not True or
                whole_epoch.get("daemon_epoch") != audit_epoch or
                type(whole_epoch.get("supervisor_pid")) is not int or whole_epoch["supervisor_pid"] <= 0 or
                not isinstance(whole_epoch.get("supervisor_process_start"), str) or
                not whole_epoch["supervisor_process_start"] or
                not isinstance(whole_epoch.get("runtime_fingerprint"), str) or
                not _HEX_64.fullmatch(whole_epoch["runtime_fingerprint"]) or
                isinstance(captured_at, bool) or not isinstance(captured_at, (int, float)) or
                not math.isfinite(float(captured_at)) or self.clock() < float(captured_at) or
                self.clock() - float(captured_at) > 300 or
                not isinstance(gpu_uuids, list) or not gpu_uuids or
                any(not isinstance(uuid, str) or not uuid.startswith("GPU-") for uuid in gpu_uuids) or
                not isinstance(cleanup, list) or not cleanup or cleanup_by_identity.keys() != expected_cleanup or
                sorted(set(gpu_uuids)) != observed_gpu_uuids or
                any(proof.get("audit_sha256") != audit_digest for _, _, proof, _ in stale_rows)):
            raise ResourceControllerError("verified_release_audit_mismatch")
        return {"request_id": request_id, "audit_id": audit_id,
                "audit_sha256": audit_digest, "target_session_ids": target_ids}

    def _verify_historical_aggregate(self, request_id: str,
            rows: Sequence[tuple[str, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]) -> dict[str, Any]:
        """Revalidate orphan aggregate receipt rows and their authenticated audit."""
        if not rows:
            raise ResourceControllerError("verified_release_audit_missing")
        session_ids = sorted(session_id for session_id, _receipt, _proof, _epoch in rows)
        first_receipt, first_proof = rows[0][1], rows[0][2]
        receipt_id = first_receipt.get("receipt_id")
        if not _orphan_audit_id(receipt_id):
            raise ResourceControllerError("verified_release_audit_mismatch")
        audited = self._load_orphan_release_audit(receipt_id)
        audit_request, audit_epoch = audited["release_request_id"], audited["daemon_epoch"]
        audit_targets, audit_proof = audited["session_ids"], audited["proof"]
        audit_text, audit_hash, audit_host = (audited["proof_text"], audited["proof_sha256"],
            audited["controller_host"])
        proof_hash = hashlib.sha256(_json(audit_proof).encode("utf-8")).hexdigest()
        captured = first_proof.get("captured_at") if isinstance(first_proof, Mapping) else None
        if (audit_request != request_id or audit_epoch != first_receipt.get("daemon_epoch") or
                not isinstance(audit_host, str) or not audit_host or audit_targets != session_ids or
                hashlib.sha256(audit_text.encode("utf-8")).hexdigest() != audit_hash or
                proof_hash != audit_hash or first_proof != audit_proof or
                first_receipt.get("release_request_id") != request_id or
                first_receipt.get("session_ids") != session_ids or
                first_receipt.get("physical_release_proof_sha256") != audit_hash or
                not isinstance(first_proof, Mapping) or
                first_proof.get("format") != "PA1-PHYSICAL-RELEASE/1" or
                first_proof.get("daemon_epoch") != audit_epoch or
                first_proof.get("stopped") is not True or first_proof.get("quiescent") is not True or
                isinstance(captured, bool) or not isinstance(captured, (int, float)) or
                not math.isfinite(float(captured)) or self.clock() < float(captured) or
                self.clock() - float(captured) > 300 or
                any(receipt != first_receipt or proof != first_proof
                    for _session, receipt, proof, _epoch in rows)):
            raise ResourceControllerError("verified_release_audit_mismatch")
        for _session, _receipt, _proof, epoch in rows:
            if (epoch.get("daemon_epoch") != audit_epoch or
                    epoch.get("supervisor_pid") != first_proof.get("supervisor_pid") or
                    epoch.get("supervisor_process_start") != first_proof.get("supervisor_process_start") or
                    epoch.get("runtime_fingerprint") != first_proof.get("runtime_fingerprint")):
                raise ResourceControllerError("verified_release_audit_mismatch")
        return {"request_id": request_id, "receipt_id": receipt_id,
                "proof_sha256": audit_hash, "target_session_ids": session_ids}

    def acknowledge_verified_release(self, intent: ReleaseIntent) -> None:
        """Update PowerPolicy only after owner proofs have been committed."""
        if self.db.in_transaction:
            raise ResourceControllerError("release_proofs_must_be_committed_first")
        mark = getattr(self.power_policy, "mark_release_verified", None)
        if not callable(mark):
            raise ResourceControllerError("power_release_verification_unavailable")
        mark(intent)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return nonsecret session state; close capabilities are never returned."""
        rows = self.db.execute("""SELECT session_id,state,release_request_id,updated
            FROM pa1_owned_resource_sessions ORDER BY session_id""").fetchall()
        return [{"session_id": row[0], "state": row[1], "release_request_id": row[2], "updated": row[3]}
                for row in rows]

    def summary(self) -> dict[str, Any]:
        """Return safe counts for operator readiness and stop diagnostics."""
        rows = self.db.execute("SELECT state,count(*) FROM pa1_owned_resource_sessions GROUP BY state").fetchall()
        counts = {state: int(count) for state, count in rows}
        return {"session_count": sum(counts.values()), "states": counts,
            "release_pending": bool(counts.get("active", 0) or counts.get("idle_owned", 0)
                                    or counts.get("release_pending", 0) or counts.get("stale", 0)),
            "release_verified_sessions": counts.get("released_verified", 0)}
