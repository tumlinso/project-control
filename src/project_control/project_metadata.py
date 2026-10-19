"""PC-owned, append-only project metadata amendments.

Todo is deliberately not involved: these records describe Project Control's
project registrations, orientation and skill/provider metadata.  The small
SQLite store is private host state and mutations use per-project CAS plus
operation-id replay protection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Mapping


ACTIONS = {
    "register_identity": "identity",
    "register_relation": "relation",
    "register_generation": "generation",
    "configure_provider": "provider",
    "remove_registration": None,
    "record_skill_use": "skill_use",
    "update_orientation": "orientation",
}


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_encode(value).encode("utf-8")).hexdigest()


def metadata_state_root() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "project-control"


class ProjectMetadataStore:
    """Transactional PC metadata log; no Todo database paths are accepted."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory is not None else metadata_state_root()
        self.path = self.directory / "project-metadata.sqlite3"
    def _initialize(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata_projects(
                    project TEXT PRIMARY KEY, scope_digest TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata_declarations(
                    project TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    version INTEGER NOT NULL, payload_json TEXT NOT NULL, retired INTEGER NOT NULL,
                    origin TEXT NOT NULL, created_at REAL NOT NULL, revision INTEGER NOT NULL,
                    PRIMARY KEY(project, kind, id, version));
                CREATE INDEX IF NOT EXISTS metadata_declarations_latest
                    ON metadata_declarations(project, kind, id, version DESC);
                CREATE TABLE IF NOT EXISTS metadata_operations(
                    project TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    request_json TEXT NOT NULL, receipt_json TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(project, operation_id));
                CREATE TABLE IF NOT EXISTS metadata_previews(
                    project TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL, review_json TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(project, operation_id));
                CREATE TABLE IF NOT EXISTS metadata_invalidations(
                    project TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL, action TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(project, revision, kind, entity_id));
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @contextmanager
    def _connection(self):
        db = self._db()
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _latest(db, project: str) -> list[dict[str, Any]]:
        rows = db.execute("""
            SELECT d.* FROM metadata_declarations d
            WHERE d.project=? AND d.version=(SELECT MAX(x.version) FROM metadata_declarations x
                WHERE x.project=d.project AND x.kind=d.kind AND x.id=d.id)
            ORDER BY d.kind,d.id
        """, (project,)).fetchall()
        output = []
        for row in rows:
            output.append({"id": row["id"], "kind": row["kind"], "version": row["version"],
                           "retired": bool(row["retired"]), "payload": json.loads(row["payload_json"]),
                           "origin": row["origin"], "revision": row["revision"],
                           "created_at": row["created_at"]})
        return output

    def read_context(self, project: str, scope_digest: str) -> dict[str, Any]:
        if not self.path.exists():
            return {"revision": 0, "all_declarations": [], "declarations": [], "orientation": [],
                    "skill_uses": [], "registrations": [], "invalidations": [],
                    "scope_matches": True}
        with self._connection() as db:
            row = db.execute("SELECT scope_digest,revision FROM metadata_projects WHERE project=?",
                             (project,)).fetchone()
            if row is not None and row["scope_digest"] != scope_digest:
                # Existing records remain readable if a root alias is moved;
                # their source freshness will be reported by the caller.
                pass
            declarations = self._latest(db, project)
            revision = row["revision"] if row is not None else 0
            scope_matches = row is None or row["scope_digest"] == scope_digest
            invalidations = [dict(item) for item in db.execute(
                "SELECT revision,kind,entity_id,action,created_at FROM metadata_invalidations "
                "WHERE project=? ORDER BY revision,kind,entity_id", (project,)).fetchall()]
        active = [item for item in declarations if not item["retired"]]
        return {
            "revision": revision,
            "all_declarations": declarations,
            "declarations": [item for item in active if item["kind"] not in {"orientation", "skill_use"}],
            "orientation": [item for item in active if item["kind"] == "orientation"],
            "skill_uses": [item for item in active if item["kind"] == "skill_use"],
            "registrations": [item for item in declarations if item["retired"] or item["kind"] != "orientation"],
            "invalidations": invalidations,
            "scope_matches": scope_matches,
        }

    def amend(self, request: Mapping[str, Any], *, scope_digest: str,
              validate_sources: Callable[[dict[str, Any]], list[dict[str, Any]]],
              provider_allowlist: Mapping[str, Any],
              project_identities: set[str]) -> dict[str, Any]:
        value = dict(request)
        mode = value.get("mode", "apply")
        payload = json.loads(_encode(value["payload"]))
        value["payload"] = payload
        operation_id = value["operation_id"]
        request_hash = _digest({key: item for key, item in value.items()
                                if key not in {"mode", "expected_revision"}})
        project, action = value["project"], value["action"]
        kind = payload.get("kind") if action == "remove_registration" else ACTIONS[action]
        trust_digest = _digest(provider_allowlist) if action == "configure_provider" else _digest({})
        self._initialize()
        db = self._db()
        try:
            db.execute("BEGIN IMMEDIATE")
            project_row = db.execute("SELECT * FROM metadata_projects WHERE project=?", (project,)).fetchone()
            revision = project_row["revision"] if project_row is not None else 0
            prior = db.execute("SELECT * FROM metadata_operations WHERE project=? AND operation_id=?",
                               (project, operation_id)).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise ValueError("operation_id_conflict")
                saved_request = json.loads(prior["request_json"])
                if saved_request.get("expected_revision") != value["expected_revision"]:
                    raise ValueError("project_metadata_revision_stale")
                receipt = json.loads(prior["receipt_json"])
                db.rollback()
                return {**receipt, "status": "replayed" if receipt.get("status") == "applied" else receipt.get("status"),
                        "receipt": receipt}
            prior_preview = db.execute("SELECT * FROM metadata_previews WHERE project=? AND operation_id=?",
                                       (project, operation_id)).fetchone()
            if prior_preview is not None:
                if prior_preview["request_hash"] != request_hash:
                    raise ValueError("operation_id_conflict")
                if prior_preview["expected_revision"] != value["expected_revision"]:
                    raise ValueError("project_metadata_revision_stale")
            if revision != value["expected_revision"]:
                raise ValueError("project_metadata_revision_stale")

            sources = validate_sources(payload)
            latest = self._latest(db, project)
            current_by_key = {(row["kind"], row["id"]): row for row in latest}
            current = current_by_key.get((kind, payload["id"]))
            if action == "remove_registration":
                if (current is None or current["retired"] or current["version"] != payload["version"]):
                    raise ValueError("registration_stale")
                related = []
                for row in latest:
                    if row["retired"] or row["kind"] != "relation":
                        continue
                    content = row["payload"]
                    if any(isinstance(content.get(key), Mapping)
                           and content[key].get("kind") == kind and content[key].get("id") == payload["id"]
                           and content[key].get("project_uuid") in project_identities
                           and (current["payload"].get("repository") is None
                                or content[key].get("repository") == current["payload"].get("repository"))
                           for key in ("source", "target")):
                        related.append(row)
                affected_rows = [current, *related]
            else:
                affected_rows = [current] if current is not None else []
            noop = (action != "remove_registration" and current is not None
                    and not current["retired"] and current["origin"] == "project_declared"
                    and current["payload"] == payload)
            affected = [{"kind": row["kind"], "id": row["id"], "version": row["version"]}
                        for row in affected_rows]
            entities = {payload["id"]}
            for item in [payload, *(row["payload"] for row in affected_rows)]:
                for locator in [*item.get("anchors", []), *item.get("inputs", []),
                                 *(anchor for group in item.get("field_anchors", {}).values()
                                   for anchor in group)]:
                    if isinstance(locator, Mapping) and isinstance(locator.get("path"), str):
                        entities.add(locator["path"])
            invalidations = ([] if noop else [
                {"kind": invalidation_kind, "entity_id": identity}
                for identity in sorted(entities) for invalidation_kind in ("graph", "search", "orientation")
            ])
            review_digest = _digest({"affected": affected, "sources": sources,
                                     "trust_digest": trust_digest, "payload": payload,
                                     "invalidations": invalidations})
            if mode == "preview":
                review = {"review_digest": review_digest, "affected": affected,
                          "sources": sources, "invalidations": invalidations,
                          "trust_digest": trust_digest}
                if prior_preview is not None and json.loads(prior_preview["review_json"]) != review:
                    raise ValueError("project_metadata_review_stale")
                if prior_preview is None:
                    db.execute("INSERT INTO metadata_previews VALUES(?,?,?,?,?,?)",
                               (project, operation_id, request_hash, revision, _encode(review), time.time()))
                db.commit()
                return {"status": "preview", "operation_id": operation_id,
                        "expected_revision": revision, **review,
                        "changes": [] if noop else [{"action": action, "kind": kind, "id": payload["id"]}]}
            if prior_preview is not None:
                saved_review = json.loads(prior_preview["review_json"])
                current_review = {"review_digest": review_digest, "affected": affected,
                                  "sources": sources, "invalidations": invalidations,
                                  "trust_digest": trust_digest}
                if saved_review != current_review:
                    raise ValueError("project_metadata_review_stale")
            if noop:
                receipt = {"status": "noop", "operation_id": operation_id, "revision": revision,
                           "affected": affected, "invalidations": []}
                if project_row is not None:
                    db.execute("UPDATE metadata_projects SET scope_digest=? WHERE project=?",
                               (scope_digest, project))
                db.execute("INSERT INTO metadata_operations VALUES(?,?,?,?,?,?)",
                           (project, operation_id, request_hash, _encode(value), _encode(receipt), time.time()))
                db.commit()
                return {**receipt, "receipt": receipt}

            next_revision = revision + 1
            db.execute("INSERT INTO metadata_projects(project,scope_digest,revision) VALUES(?,?,?) "
                       "ON CONFLICT(project) DO UPDATE SET scope_digest=excluded.scope_digest,revision=excluded.revision",
                       (project, scope_digest, next_revision))
            targets = affected_rows if action == "remove_registration" else [{"kind": kind, "id": payload["id"]}]
            for target in targets:
                previous = current_by_key.get((target["kind"], target["id"]))
                version = previous["version"] + 1 if previous else 1
                retired = action == "remove_registration"
                content = previous["payload"] if retired and previous else payload
                db.execute("INSERT INTO metadata_declarations VALUES(?,?,?,?,?,?,?,?,?)",
                           (project, target["kind"], target["id"], version, _encode(content), int(retired),
                            "project_declared", time.time(), next_revision))
            for item in invalidations:
                db.execute("INSERT OR IGNORE INTO metadata_invalidations VALUES(?,?,?,?,?,?)",
                           (project, next_revision, item["kind"], item["entity_id"], action, time.time()))
            receipt = {"status": "applied", "operation_id": operation_id, "revision": next_revision,
                       "affected": affected,
                       "changes": [{"action": action, "kind": kind, "id": payload["id"]}],
                       "invalidations": invalidations}
            db.execute("INSERT INTO metadata_operations VALUES(?,?,?,?,?,?)",
                       (project, operation_id, request_hash, _encode(value), _encode(receipt), time.time()))
            db.commit()
            return {**receipt, "receipt": receipt}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
