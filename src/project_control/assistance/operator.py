"""Cold operator controls for demand-only local assistance.

This module stores user intent and power policy beside the existing private
observer broker.  It does not start the broker, connect a model, or infer while
waiting for operator input.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from typing import Any, Callable, Iterable, Mapping

from ..observer_analysis import observer_analysis_state_root
from ..as1_packets import SQLitePacketStore
from .power import PowerPolicy, trusted_operator_control


class AssistanceOperator:
    """Trusted local command adapter; its methods are called only by the CLI."""

    def __init__(self, *, state_root: str | Path | None = None,
                 clock: Callable[[], float] = time.time):
        self.state_root = Path(state_root) if state_root is not None else observer_analysis_state_root(create=False) / "as1"
        self.db_path = self.state_root / "jobs-v2" / "jobs.sqlite3"
        self.clock = clock
        self.control = trusted_operator_control()

    def _open(self, *, create: bool, timeout: float = 10) -> sqlite3.Connection | None:
        if not create and not self.db_path.is_file():
            return None
        if create:
            self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                self.db_path.parent.chmod(0o700)
            except OSError:
                pass
            db = sqlite3.connect(self.db_path, timeout=timeout)
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
            db.row_factory = sqlite3.Row
            return db
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def admission_guard(self, deadline_epoch: float):
        """Serialize only an external runtime-spawn effect with release veto.

        The canonical broker database write lock gives start and release one
        ordering: the short spawn or request-admission effect completes before
        the veto commits, or the guard observes the veto and refuses it.
        Callers must leave the context before model warming, readiness polling,
        or execution waits; never hold it through inference.
        """
        if (isinstance(deadline_epoch, bool) or not isinstance(deadline_epoch, (int, float))
                or deadline_epoch <= self.clock()):
            raise ValueError("runtime_admission_deadline_invalid")
        remaining = min(10.0, float(deadline_epoch) - float(self.clock()))
        if remaining <= 0:
            raise TimeoutError("runtime_admission_deadline_expired")
        db = self._open(create=True, timeout=remaining)
        try:
            db.execute(f"PRAGMA busy_timeout={int(remaining * 1000)}")
            db.execute("BEGIN IMMEDIATE")
            policy = PowerPolicy(db, clock=self.clock)
            if policy.snapshot().get("release_veto_active"):
                raise PermissionError("assistance_release_veto_active")
            if deadline_epoch <= self.clock():
                raise TimeoutError("runtime_admission_deadline_expired")
            yield
            db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _default_status() -> dict[str, Any]:
        return {
            "status": "ok", "demand_only": True, "notifications": "off",
            "focus": None,
            "power": {"automatic_enabled": False, "automatic_focus": None,
                      "automatic_project": None, "automatic_until": None,
                      "quiet_active": False, "quiet_until": None,
                      "release_veto_active": False, "physical_state": "not_requested",
                      "release_request_id": None, "release_verified_sessions": 0},
            "owned_resources": {"status": "not_observed", "session_count": None,
                                "current_sessions": None, "release_pending": None},
            "dispatcher": "not_observed",
        }

    def status(self) -> dict[str, Any]:
        """Read persisted state without initializing tables or opening a service."""
        result = self._default_status()
        db = self._open(create=False)
        if db is None:
            return result
        try:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "pa1_owned_resource_sessions" in tables:
                counts = {row[0]: int(row[1]) for row in db.execute(
                    "SELECT state,count(*) FROM pa1_owned_resource_sessions GROUP BY state")}
                current = sum(counts.get(state, 0) for state in
                              ("active", "idle_owned", "release_pending", "stale"))
                result["owned_resources"] = {
                    "status": "pending" if current else "no_owned_resources",
                    "session_count": sum(counts.values()),
                    "current_sessions": current,
                    "release_pending": bool(current),
                    "states": counts,
                }
            if "pa1_attention_focus" in tables:
                row = db.execute("SELECT focus_id,project,source_paths,goal_card_id,state,"
                                 "starts_at,expires_at,permit_automatic,reserved_turns,active_seconds "
                                 "FROM pa1_attention_focus ORDER BY created DESC LIMIT 1").fetchone()
                if row is not None:
                    result["focus"] = dict(row)
                    result["focus"]["source_paths"] = json.loads(result["focus"]["source_paths"])
                    result["focus"]["permit_automatic"] = bool(result["focus"]["permit_automatic"])
            if "pa1_power_state" in tables:
                row = db.execute("SELECT * FROM pa1_power_state WHERE singleton=1").fetchone()
                if row is not None:
                    now = float(self.clock())
                    columns = set(row.keys())
                    def saved(name: str, default=None):
                        return row[name] if name in columns else default
                    automatic_until = saved("automatic_until")
                    automatic_focus = saved("automatic_focus")
                    release_veto = bool(saved("release_veto", 0))
                    release_until = saved("release_until")
                    quiet_reason = saved("quiet_reason")
                    quiet_until = saved("quiet_until")
                    result["power"] = {
                        "automatic_enabled": bool(saved("automatic_enabled", 0)),
                        "automatic_focus": automatic_focus,
                        "automatic_project": saved("automatic_project"),
                        "automatic_until": automatic_until,
                        "quiet_active": quiet_reason is not None and
                            (quiet_until is None or now < quiet_until),
                        "quiet_until": quiet_until,
                        "release_veto_active": release_veto and
                            (release_until is None or now < release_until),
                        "release_until": release_until,
                        "release_request_id": saved("release_request_id"),
                        "physical_state": saved("physical_state", "not_requested"),
                        "release_verified_sessions": saved("release_verified_sessions", 0),
                    }
                    result["demand_only"] = not result["power"]["automatic_enabled"]
            return result
        finally:
            db.close()

    def set_goal(self, *, project: str, text: str,
                 trusted_projects: Iterable[str], principal: str = "project-control-observer") -> dict[str, Any]:
        """Store directly stated intent as a user goal; it grants no runtime power."""
        self._validate_project(project, trusted_projects)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2048:
            raise ValueError("goal_text_invalid")
        note_id = "goal_" + hashlib.sha256(f"{principal}\0{project}".encode()).hexdigest()[:32]
        scope = {"principal": principal, "profile": "observer", "project": project}
        store = SQLitePacketStore(self.state_root)
        record = {
            "note_id": note_id, "project": project, "kind": "goal",
            "claim": text.strip(), "reason_matters": "User-stated project goal.",
            "sources": [], "evidence_packets": [], "dependencies": {},
            "coverage": {"basis": "direct_user_input"}, "uncertainty": [],
            "provenance": "user", "authoritative": False,
            "is_independent_evidence": False,
        }
        saved = store.put_user_goal(record, access_scope=scope)
        return {"status": "saved", "goal": saved, "power_grant": False}

    def set_focus(self, *, project: str, text: str, trusted_projects: Iterable[str],
                  trusted_root: str | Path, trusted_repository: str,
                  source_paths: Iterable[str] = (),
                  automatic_seconds: float | None = None,
                  principal: str = "project-control-observer") -> dict[str, Any]:
        from .attention import AttentionController, FocusConfig

        paths = tuple(source_paths)
        self._validate_project(project, trusted_projects)
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2048:
            raise ValueError("goal_text_invalid")
        if not isinstance(trusted_repository, str) or not trusted_repository:
            raise ValueError("repository_identity_required")
        trusted_root = Path(trusted_root).resolve(strict=True)
        if not trusted_root.is_dir():
            raise ValueError("trusted_repository_root_must_be_directory")
        if automatic_seconds is not None and not paths:
            raise ValueError("automatic_focus_requires_source_paths")
        if (automatic_seconds is not None and (isinstance(automatic_seconds, bool)
                or not isinstance(automatic_seconds, (int, float))
                or not 1 <= float(automatic_seconds) <= 86400
                or not float(automatic_seconds) < float("inf"))):
            raise ValueError("automatic_window_must_be_between_1_second_and_24_hours")
        if len(paths) > 64:
            raise ValueError("source_path_limit_exceeded")
        now = float(self.clock())
        focus_id = "focus_" + uuid.uuid4().hex
        expires = now + (float(automatic_seconds) if automatic_seconds is not None else 86400.0)
        focus_config = FocusConfig(focus_id=focus_id, project=project,
            source_paths=paths, starts_at=now, expires_at=expires,
            permit_automatic_inference=automatic_seconds is not None,
            goal_card_id="pending-goal-validation").validated()
        # Run path/scope validation against an in-memory controller first. Bad
        # paths or malformed focus config must not write a user goal or broker
        # policy state as a side effect.
        validation_db = sqlite3.connect(":memory:")
        try:
            validation_power = PowerPolicy(validation_db, clock=self.clock)
            validation_controller = AttentionController(validation_db,
                power_policy=validation_power, trusted_roots={project: trusted_root},
                repository_for=lambda item: trusted_repository if item == project else "",
                access_scope=lambda item: {"principal": principal, "profile": "observer", "project": item},
                broker=None, notebook=None, clock=self.clock)
            for path in focus_config.source_paths:
                validation_controller._digest(project, path)
        finally:
            validation_db.close()
        goal_result = self.set_goal(project=project, text=text,
                                    trusted_projects=trusted_projects, principal=principal)
        store = SQLitePacketStore(self.state_root)
        focus_config = FocusConfig(focus_id=focus_id, project=project,
            source_paths=paths, starts_at=now, expires_at=expires,
            permit_automatic_inference=automatic_seconds is not None,
            goal_card_id=goal_result["goal"]["note_id"])
        db = self._open(create=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            # The bounded grant starts at the timestamp captured before
            # validation and goal persistence. Use that same instant only for
            # the transaction that creates the window; all later policy reads
            # use the operator's live clock and therefore observe expiry.
            policy = PowerPolicy(db, clock=lambda: now)
            controller = AttentionController(db, power_policy=policy,
                trusted_roots={project: trusted_root},
                repository_for=lambda item: trusted_repository if item == project else "",
                access_scope=lambda item: {"principal": principal, "profile": "observer", "project": item},
                broker=None, notebook=store, clock=self.clock)
            controller.configure_focus(self.control, focus_config)
            policy.set_automatic(self.control, automatic_seconds is not None)
            if automatic_seconds is not None:
                policy.set_focus_window(self.control, focus_id=focus_id, project=project,
                                        starts_at=now, expires_at=expires)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return {"status": "focused", "focus_id": focus_id, "project": project,
                "goal_note_id": goal_result["goal"]["note_id"],
                "automatic": automatic_seconds is not None,
                "source_paths": list(paths), "expires_at": expires,
                "automatic_until": expires if automatic_seconds is not None else None}

    @staticmethod
    def _validate_project(project: str, trusted_projects: Iterable[str]) -> None:
        if not isinstance(project, str) or project not in set(trusted_projects):
            raise PermissionError("project_not_registered")

    def quiet(self, *, until: float | None = None, reason: str = "operator-requested") -> dict[str, Any]:
        db = self._open(create=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            policy = PowerPolicy(db, clock=self.clock)
            policy.set_quiet(self.control, expires_at=until, reason=reason)
            result = policy.snapshot()
            db.commit()
            return {"status": "quiet", "quiet_active": True, "quiet_until": result["quiet_until"],
                    "automatic_enabled": result["automatic_enabled"]}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def request_release(self, *, job_service=None, until: float | None = None,
                        reason: str = "operator-requested") -> dict[str, Any]:
        """Commit the durable inference veto before optional owner cleanup."""
        db = self._open(create=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            policy = PowerPolicy(db, clock=self.clock)
            from .resources import ResourceController
            ResourceController(db, power_policy=policy, clock=self.clock)
            intent = policy.set_release(self.control,
                                        declared_end=until if until is not None else None,
                                        reason=reason)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        outcome = "pending_owner_unavailable"
        if job_service is not None:
            # This reuses the same idempotent intent and the JobService's real
            # SupervisorClient release RPC plus ResourceController proof path.
            outcome = job_service._deliver_owned_release(intent)
        if outcome == "pending_no_owned_resources":
            # This is an ownership census result, not proof that a supervisor
            # process stopped. Keep PowerPolicy's physical state pending.
            outcome = "no_owned_resources"
        full_state = self.status()
        state = full_state["power"]
        return {"status": outcome, "release_veto_active": state["release_veto_active"],
                "physical_state": state["physical_state"],
                "release_request_id": intent.request_id,
                "verified_sessions": state["release_verified_sessions"],
                "owned_resources": full_state["owned_resources"]}

    def resume(self, *, quiet: bool = False, release: bool = False) -> dict[str, Any]:
        if not quiet and not release:
            raise ValueError("select_at_least_one_veto_to_resume")
        db = self._open(create=True)
        try:
            db.execute("BEGIN IMMEDIATE")
            policy = PowerPolicy(db, clock=self.clock)
            # Mint the trusted resume capability, then clear only the selected
            # vetoes. Physical release accounting is never rewritten here.
            policy._authorized(self.control, "resume")
            updates = []
            if quiet:
                updates.extend(("quiet_until=NULL", "quiet_focus=NULL", "quiet_reason=NULL"))
            if release:
                updates.extend(("release_veto=0", "release_until=NULL"))
            updates.append("updated=?")
            db.execute("UPDATE pa1_power_state SET " + ",".join(updates) +
                       " WHERE singleton=1", (float(self.clock()),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return {"status": "resumed", "quiet": quiet, "release": release,
                "power": self.status()["power"]}

    def dismiss(self, *, project: str, focus_id: str, fingerprint: str,
                trusted_projects: Iterable[str], trusted_root: str | Path,
                trusted_repository: str,
                reason: str = "operator-dismissed",
                principal: str = "project-control-observer") -> dict[str, Any]:
        self._validate_project(project, trusted_projects)
        from .attention import AttentionController

        scope = {"principal": principal, "profile": "observer", "project": project}
        store = SQLitePacketStore(self.state_root)
        db = self._open(create=True)
        try:
            policy = PowerPolicy(db, clock=self.clock)
            controller = AttentionController(db, power_policy=policy,
                trusted_roots={project: trusted_root},
                repository_for=lambda item: trusted_repository if item == project else "",
                access_scope=lambda item: scope,
                broker=None, notebook=store, clock=self.clock)
            controller.dismiss(project=project, focus_id=focus_id,
                               fingerprint=fingerprint, reason=reason)
            return {"status": "dismissed", "focus_id": focus_id,
                    "fingerprint": fingerprint}
        finally:
            db.close()

    def accept_suggestion(self, *, project: str, note_id: str,
                          trusted_projects: Iterable[str],
                          principal: str = "project-control-observer") -> dict[str, Any]:
        """Turn one named advisory suggestion into an explicit user goal card."""
        self._validate_project(project, trusted_projects)
        scope = {"principal": principal, "profile": "observer", "project": project}
        store = SQLitePacketStore(self.state_root)
        suggestion = store.get_note(note_id, access_scope=scope)
        if (suggestion is None or suggestion.get("project") != project
                or suggestion.get("provenance") != "model"
                or suggestion.get("kind") == "goal"):
            raise ValueError("suggestion_not_available_for_acceptance")
        proposed = suggestion.get("next_action")
        accepted_text = proposed if isinstance(proposed, str) and proposed.strip() else suggestion.get("claim")
        if not isinstance(accepted_text, str) or not accepted_text.strip():
            raise ValueError("suggestion_has_no_acceptable_text")
        result = self.set_goal(project=project,
            text=f"Accepted suggestion {note_id}: {accepted_text.strip()}",
            trusted_projects=trusted_projects, principal=principal)
        result["status"] = "accepted_as_user_goal"
        result["accepted_note_id"] = note_id
        return result

    def handoff(self, *, project: str, focus_id: str | None, trusted_projects: Iterable[str],
                trusted_root: str | Path, trusted_repository: str,
                principal: str = "project-control-observer") -> dict[str, Any]:
        self._validate_project(project, trusted_projects)
        from .attention import AttentionController

        scope = {"principal": principal, "profile": "observer", "project": project}
        store = SQLitePacketStore(self.state_root)
        notes = store.list_notes(access_scope=scope, project=project)
        goals = [item for item in notes if item.get("kind") == "goal" and item.get("provenance") == "user"]
        inferred = [item for item in notes if item.get("provenance") == "model"]
        db = self._open(create=True)
        try:
            policy = PowerPolicy(db, clock=self.clock)
            controller = AttentionController(db, power_policy=policy,
                trusted_roots={project: trusted_root},
                repository_for=lambda item: trusted_repository if item == project else "",
                access_scope=lambda item: scope,
                broker=None, notebook=store, clock=self.clock)
            if focus_id is None:
                row = db.execute("SELECT focus_id FROM pa1_attention_focus WHERE project=? "
                                 "ORDER BY created DESC LIMIT 1", (project,)).fetchone()
                focus_id = row[0] if row else ""
            active = db.execute("SELECT state,starts_at,expires_at FROM pa1_attention_focus "
                                "WHERE focus_id=? AND project=?", (focus_id, project)).fetchone()
            if (active is None or active["state"] != "active"
                    or not active["starts_at"] <= float(self.clock()) < active["expires_at"]):
                inferred = []
            else:
                inferred = [item for item in inferred
                            if (item.get("coverage") or {}).get("focus_id") == focus_id]
            return controller.handoff(project=project, focus_id=focus_id,
                                      user_goal_notes=goals, inferred_notes=inferred)
        finally:
            db.close()

    def attention(self, config, *, broker=None,
                  principal: str = "project-control-observer"):
        """Bind attention to registered authority roots without starting a service."""
        from .attention import AttentionController
        roots: dict[str, Path] = {}
        repositories: dict[str, str] = {}
        for project, workspace in config.workspaces.items():
            alias = workspace.authority_repository
            if alias is None and len(workspace.repositories) == 1:
                alias = next(iter(workspace.repositories))
            if alias is not None and alias in workspace.repositories:
                roots[project] = workspace.repositories[alias].root
                repositories[project] = alias

        def access_scope(project: str) -> dict[str, str]:
            if project not in roots:
                raise PermissionError("project_authority_root_unavailable")
            return {"principal": principal, "profile": "observer", "project": project}

        notebook = SQLitePacketStore(self.state_root)
        db = self._open(create=True)
        policy = PowerPolicy(db, clock=self.clock)
        try:
            controller = AttentionController(db, power_policy=policy,
                trusted_roots=roots, repository_for=lambda project: repositories[project],
                access_scope=access_scope,
                broker=broker, notebook=notebook, clock=self.clock)
            return db, controller
        except BaseException:
            db.close()
            raise


def parse_utc_timestamp(value: str) -> float:
    """Parse an ISO timestamp only when it carries an explicit timezone."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp_must_be_iso8601_with_timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_must_be_iso8601_with_timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def parse_duration(value: str) -> float:
    """Parse a finite, positive duration in seconds or with s/m/h/d suffix."""
    suffixes = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    text = value.strip().lower()
    factor = suffixes.get(text[-1:], 1)
    number = text[:-1] if text[-1:] in suffixes else text
    try:
        seconds = float(number) * factor
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_invalid") from exc
    if not (seconds > 0 and seconds < float("inf")):
        raise ValueError("duration_invalid")
    return seconds
