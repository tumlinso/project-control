"""Canonical, read-only projection of generated briefs and authored context."""

from __future__ import annotations

import json
from typing import Any

from .coordination import parse_json
from .models import ProjectSnapshot


_TABLES = ("workflow_context_fragments", "run_context_fragments", "context_fragments")
_TERMINAL = {"invalidated", "superseded", "deleted", "retired"}
_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "does", "for", "how", "in", "is", "of", "on", "or", "the", "to", "what", "with", "work", "works",
}


def _object(value: object) -> dict[str, Any]:
    parsed = parse_json(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _list(value: object) -> list[Any]:
    parsed = parse_json(value, [])
    return parsed if isinstance(parsed, list) else []


def _first(row: dict[str, Any], *names: str) -> object:
    return next((row[name] for name in names if row.get(name) is not None), None)


def _anchors(row: dict[str, Any], owner_scope: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    raw = _first(row, "anchors", "anchor", "anchors_json", "anchor_json")
    value = _object(raw)
    envelope_anchors = envelope.get("anchors")
    if isinstance(envelope_anchors, list):
        # Canonical Todo notes use [{kind, value}]. Keep the list, while also
        # exposing familiar keyed fields for legacy consumers.
        value["items"] = [item for item in envelope_anchors if isinstance(item, dict)]
        for item in value["items"]:
            kind, anchor_value = item.get("kind"), item.get("value")
            if isinstance(kind, str) and anchor_value is not None:
                key = {"directory": "path", "file": "path"}.get(kind, kind)
                if key in value:
                    value[key] = [value[key], anchor_value] if not isinstance(value[key], list) else [*value[key], anchor_value]
                else:
                    value[key] = anchor_value
    # Older generated fragments used owner_scope as their only virtual anchor.
    for key in ("project", "repository", "path", "paths", "directory", "symbol", "symbols", "task_id", "run_id", "lane_id", "interface_id", "decision_id"):
        if key not in value and owner_scope.get(key) is not None:
            value[key] = owner_scope[key]
        if key not in value and row.get(key) is not None:
            value[key] = row[key]
    return value


def _source_identity(row: dict[str, Any], envelope: dict[str, Any]) -> object | None:
    raw = _first(row, "source_identity", "source_identity_json", "recorded_source_identity", "recorded_source_identity_json")
    value = parse_json(raw, raw) if raw is not None else envelope.get("source_identity")
    return value if isinstance(value, (str, dict)) else None


def _freshness(snapshot: ProjectSnapshot, identity: object | None, repository: object) -> str:
    if identity is None:
        return "unknown"
    alias = str(repository or "")
    current = snapshot.repositories.get(alias) if alias else (next(iter(snapshot.repositories.values())) if len(snapshot.repositories) == 1 else None)
    if current is None:
        return "unknown"
    if isinstance(identity, str):
        return "current" if identity == current.commit else "potentially_stale"
    commit = identity.get("commit") or identity.get("source_commit") or identity.get("head")
    fingerprint = identity.get("working_tree_fingerprint") or identity.get("fingerprint")
    if commit and commit != current.commit:
        return "potentially_stale"
    if fingerprint and current.working_tree_fingerprint and fingerprint != current.working_tree_fingerprint:
        return "potentially_stale"
    return "current" if commit or fingerprint else "unknown"


def canonical_context_fragments(snapshot: ProjectSnapshot) -> list[dict[str, Any]]:
    """Merge the three export aliases without making their storage authority public."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for table in _TABLES:
        for raw in snapshot.todo_tables.get(table, []):
            if not isinstance(raw, dict):
                continue
            identifier = str(_first(raw, "id", "fragment_id") or "")
            if not identifier:
                continue
            version = str(raw.get("version") or raw.get("context_version") or "")
            key = (identifier, version)
            prior = merged.setdefault(key, {})
            # The workflow table is richer, but aliases can contain fields it
            # lacks.  Fill missing values rather than exposing duplicate rows.
            for field, value in raw.items():
                if prior.get(field) is None and value is not None:
                    prior[field] = value
            prior.setdefault("_tables", []).append(table)
    result: list[dict[str, Any]] = []
    for (_, _), row in merged.items():
        owner_scope = _object(_first(row, "owner_scope", "owner_scope_json"))
        envelope = _object(_first(row, "content", "content_json", "payload", "payload_json"))
        content: object = envelope.get("content", envelope)
        if not isinstance(content, (dict, list, str)):
            content = {}
        kind = str(row.get("kind") or "context_brief")
        state = str(row.get("state") or ("invalidated" if row.get("invalidated_at") else "superseded" if row.get("superseded_by") else "current"))
        anchors = _anchors(row, owner_scope, envelope)
        source_identity = _source_identity(row, envelope)
        repository = anchors.get("repository") or row.get("repository") or (source_identity.get("repository") if isinstance(source_identity, dict) else None)
        result.append({
            "id": str(_first(row, "id", "fragment_id")), "kind": kind,
            "series_key": str(row.get("series_key") or row.get("fragment_key") or row.get("kind") or _first(row, "id", "fragment_id")),
            "version": row.get("version") or row.get("context_version"),
            "run_id": row.get("run_id"), "lane_id": row.get("lane_id"), "task_id": row.get("task_id"),
            "origin_owner": _object(_first(row, "origin_owner", "origin_owner_json")) or owner_scope,
            "owner_scope": owner_scope, "anchors": anchors, "classification": row.get("classification") or envelope.get("classification"),
            "content": content, "content_hash": row.get("content_hash"), "created_at": row.get("created_at"),
            "revision": row.get("revision") or row.get("creation_revision"), "state": state,
            "invalidated_at": row.get("invalidated_at"), "invalidation_revision": row.get("invalidation_revision"),
            "superseded_by": row.get("superseded_by"), "source_identity": source_identity,
            "source_freshness": _freshness(snapshot, source_identity, repository),
            "authority": "non_authoritative_context" if kind == "context_note" else "durable_export_enrichment",
            "tables": row.get("_tables", []),
        })
    return sorted(result, key=lambda item: (str(item.get("id")), str(item.get("version") or "")))


def active(fragment: dict[str, Any]) -> bool:
    return str(fragment.get("state") or "current").casefold() not in _TERMINAL and not fragment.get("invalidated_at") and not fragment.get("superseded_by")


def matches_subject(fragment: dict[str, Any], subject: str) -> bool:
    wanted = subject.casefold()
    if wanted in {str(fragment.get(key) or "").casefold() for key in ("id", "task_id", "run_id", "lane_id", "series_key")}:
        return True
    blob = json.dumps({"anchors": fragment.get("anchors"), "content": fragment.get("content")}, sort_keys=True, default=str).casefold()
    return wanted in blob


def matches_question(fragment: dict[str, Any], question: str) -> bool:
    """Conservative note relevance for a prose architecture question."""
    import re

    tokens = {token.casefold() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", question) if token.casefold() not in _QUERY_STOPWORDS}
    if not tokens:
        return False
    anchors = fragment.get("anchors") if isinstance(fragment.get("anchors"), dict) else {}
    anchor_blob = json.dumps(anchors, sort_keys=True, default=str).casefold()
    # A named path/symbol/interface is a deliberate direct connection.
    if any(re.search(rf"\b{re.escape(token)}\b", anchor_blob) for token in tokens):
        return True
    content_blob = json.dumps(fragment.get("content"), sort_keys=True, default=str).casefold()
    return sum(bool(re.search(rf"\b{re.escape(token)}\b", content_blob)) for token in tokens) >= 2


def matches_scope(fragment: dict[str, Any], *, run_id: str | None = None, lane_id: str | None = None, task_id: str | None = None) -> bool:
    """Origin scope plus virtual anchors, without treating a project note as scoped."""
    anchors = fragment.get("anchors") if isinstance(fragment.get("anchors"), dict) else {}
    items = anchors.get("items") if isinstance(anchors.get("items"), list) else []
    def values(name: str) -> set[str]:
        found = {str(fragment.get(name) or ""), str(anchors.get(name) or "")}
        found.update(str(item.get("value") or "") for item in items if item.get("kind") in {name, name.removesuffix("_id")})
        return found
    return not ((run_id and run_id not in values("run_id")) or (lane_id and lane_id not in values("lane_id")) or (task_id and task_id not in values("task_id")))


def path_or_symbol_matches(fragment: dict[str, Any], target: str, kind: str) -> bool:
    anchors = fragment.get("anchors") if isinstance(fragment.get("anchors"), dict) else {}
    value = target.strip("/")
    items = anchors.get("items") if isinstance(anchors.get("items"), list) else []
    if kind in {"path", "subsystem"}:
        paths = [anchors.get("path"), anchors.get("directory"), *(_list(anchors.get("paths")))]
        paths.extend(item.get("value") for item in items if item.get("kind") in {"path", "directory", "file", "subsystem"})
        return any(isinstance(path, str) and (value == path.strip("/") or value.startswith(path.strip("/") + "/") or path.strip("/").startswith(value + "/")) for path in paths if path)
    symbols = [anchors.get("symbol"), *(_list(anchors.get("symbols")))]
    symbols.extend(item.get("value") for item in items if item.get("kind") == "symbol")
    if any(isinstance(symbol, str) and symbol.casefold() == target.casefold() for symbol in symbols):
        return True
    return target.casefold() in json.dumps({"anchors": anchors, "content": fragment.get("content")}, default=str).casefold()


def project_fragment(fragment: dict[str, Any], *, detail: str = "standard") -> dict[str, Any]:
    note = fragment.get("kind") == "context_note"
    fields = ("id", "kind", "series_key", "version", "run_id", "lane_id", "task_id", "origin_owner", "anchors", "classification", "state", "invalidated_at", "invalidation_revision", "superseded_by", "source_identity", "source_freshness", "content_hash", "created_at", "revision", "authority")
    result = {key: fragment.get(key) for key in fields if fragment.get(key) is not None}
    if detail == "expanded" or (note and detail == "standard"):
        result["content"] = fragment.get("content")
    return result
