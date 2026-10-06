"""Bounded retrieval of advisory, evidence-linked assistance notes.

The provider has no write or publication path. Records remain attributed to
their original producer and source witnesses; returning a note never creates a
new witness or changes canonical Todo state.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from collections.abc import Callable, Mapping
from typing import Any


_WORD = re.compile(r"[\w]+", re.UNICODE)
_MAX_BUDGET = 32 * 1024
_MAX_LIMIT = 32


def _wire(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)


def _tokens(value: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(value)}


def _source_dict(source: Any) -> dict[str, Any]:
    if hasattr(source, "model_dump"):
        return source.model_dump(mode="json")
    return dict(source) if isinstance(source, Mapping) else {}


class NotebookProvider:
    """Read trusted project notes while checking their material dependencies.

    ``resolve_dependency(project, key)`` must be a trusted host callback. It
    returns a current digest string, ``{"status": "current", "digest": ...}``,
    or an explicit unavailable mapping such as ``{"status": "missing"}``.
    Repository HEAD is deliberately not consulted: each note carries the
    determinants that matter to its own claim.
    """

    def __init__(self, store: Any, *, resolve_dependency: Callable[[str, str], Any],
                 trusted_projects: Any, clock: Callable[[], float] = time.time):
        if not callable(resolve_dependency):
            raise TypeError("trusted dependency resolver required")
        self.store = store
        self.resolve_dependency = resolve_dependency
        self.trusted_projects = trusted_projects
        self.clock = clock

    def _trusted_project(self, project: str) -> bool:
        trusted = self.trusted_projects
        if isinstance(trusted, Mapping):
            return project in trusted
        try:
            return project in trusted
        except TypeError:
            return False

    def _freshness(self, record: Mapping[str, Any], project: str) -> tuple[str, list[dict[str, str]]]:
        expected: dict[tuple[str, str], str] = {}
        issues: list[dict[str, str]] = []
        dependencies = record.get("dependencies", {})
        if not isinstance(dependencies, Mapping):
            issues.append({"reason": "invalid_dependency_manifest"})
            dependencies = {}
        for key, digest in dependencies.items():
            if not isinstance(key, str) or not isinstance(digest, str):
                issues.append({"reason": "invalid_dependency_manifest"})
                continue
            expected[(project, key)] = digest
        for source in record.get("sources", ()) or ():
            locator = _source_dict(source)
            repository, path, digest = (locator.get("repository"), locator.get("path"),
                                        locator.get("content_sha256"))
            if not (isinstance(repository, str) and isinstance(path, str)
                    and isinstance(digest, str)):
                issues.append({"reason": "invalid_source_locator"})
                continue
            source_project = locator.get("project")
            if not isinstance(source_project, str) or not source_project:
                issues.append({"reason": "invalid_source_locator"})
                continue
            if not self._trusted_project(source_project):
                issues.append({"project": source_project, "reason": "forbidden_project"})
                continue
            key = f"file:{repository}/{path}"
            identity = (source_project, key)
            if identity in expected and expected[identity] != digest:
                issues.append({"project": source_project, "dependency": key,
                               "reason": "conflicting_dependency"})
            else:
                expected[identity] = digest

        if not expected and not (record.get("kind") == "goal" and record.get("provenance") == "user"):
            issues.append({"reason": "material_dependencies_missing"})
        for (dependency_project, key), wanted in sorted(expected.items()):
            try:
                current = self.resolve_dependency(dependency_project, key)
            except Exception:
                current = {"status": "unavailable", "reason": "resolver_error"}
            if isinstance(current, str):
                actual, state = current, "current"
            elif isinstance(current, Mapping):
                state = str(current.get("status", current.get("state", "unavailable")))
                actual = current.get("digest", current.get("sha256")) if state == "current" else None
            else:
                actual, state = None, "unavailable"
            if state != "current" or not isinstance(actual, str):
                issues.append({"project": dependency_project, "dependency": key,
                               "reason": str(current.get("reason", state))[:96]
                               if isinstance(current, Mapping) else "unavailable"})
            elif actual != wanted:
                issues.append({"project": dependency_project, "dependency": key,
                               "reason": "changed"})
        return ("current" if not issues else "stale"), issues

    @staticmethod
    def _record_text(record: Mapping[str, Any]) -> str:
        fields = ("kind", "claim", "reason_matters", "coverage", "uncertainty",
                  "next_action", "contradicts", "supersedes")
        return " ".join(str(record.get(field, "")) for field in fields)

    @staticmethod
    def _safe_record(record: Mapping[str, Any], freshness: str,
                     dependency_omissions: list[dict[str, str]]) -> dict[str, Any]:
        # Keep the complete provenance contract and add only retrieval metadata.
        item = dict(record)
        item["freshness"] = freshness
        item["freshness_omissions"] = list(dependency_omissions)
        item["independent_proof"] = False
        return item

    def retrieve(self, query: str, *, project: str, access_scope: Mapping[str, Any],
                 budget_bytes: int = 4096, limit: int = 5) -> dict[str, Any]:
        if not isinstance(query, str):
            raise TypeError("query must be plain text")
        if not isinstance(access_scope, Mapping) or not access_scope:
            raise ValueError("trusted host access scope required")
        if not 1 <= int(limit) <= _MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}")
        if not 256 <= int(budget_bytes) <= _MAX_BUDGET:
            raise ValueError(f"budget_bytes must be between 256 and {_MAX_BUDGET}")

        result: dict[str, Any] = {
            "notes": [], "goals": [], "omissions": [],
            "coverage": {"project": project, "examined": 0, "matched": 0,
                         "returned": 0, "partial": False},
            "authoritative": False,
        }
        if (not isinstance(project, str) or not self._trusted_project(project)
                or access_scope.get("project") != project):
            result["omissions"].append({"project": project, "reason": "forbidden_project"})
            result["coverage"]["partial"] = True
            return result

        rows = self.store.list_notes(access_scope=access_scope, project=project, limit=128)
        tokens = _tokens(query)
        ranked: list[tuple[int, int, str, dict[str, Any], bool]] = []
        now = self.clock()
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                result["omissions"].append({"reason": "invalid_record"})
                continue
            record = dict(raw)
            result["coverage"]["examined"] += 1
            # Defensive recheck even when the store enforces this boundary.
            if record.get("project") != project:
                result["omissions"].append({"note_id": record.get("note_id"),
                                            "reason": "forbidden_project"})
                continue
            relevance = len(tokens & _tokens(self._record_text(record)))
            is_goal = record.get("kind") == "goal" and record.get("provenance") == "user"
            # User goals remain available as user-authored context even when the
            # current query uses different words; inferred notes need a match.
            if relevance == 0 and not is_goal:
                continue
            freshness, dependency_omissions = self._freshness(record, project)
            stamp = str(record.get("created_at", ""))
            if record.get("expires_at") is not None:
                try:
                    expiry = record["expires_at"]
                    if isinstance(expiry, str):
                        expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp()
                    if float(expiry) <= now:
                        freshness = "stale"
                        dependency_omissions.append({"reason": "proof_expired"})
                except (TypeError, ValueError):
                    freshness = "stale"
                    dependency_omissions.append({"reason": "invalid_expiry"})
            item = self._safe_record(record, freshness, dependency_omissions)
            ranked.append((0 if is_goal else 1, -relevance, stamp + f"/{index:04d}", item, is_goal))
        ranked.sort(key=lambda row: row[:3])
        result["coverage"]["matched"] = len(ranked)

        for _, _, _, item, is_goal in ranked:
            if result["coverage"]["returned"] >= int(limit):
                result["omissions"].append({"reason": "limit", "note_id": item.get("note_id")})
                result["coverage"]["partial"] = True
                continue
            bucket = "goals" if is_goal else "notes"
            candidate = dict(result)
            candidate[bucket] = [*result[bucket], item]
            candidate["coverage"] = dict(result["coverage"])
            candidate["coverage"]["returned"] += 1
            if item["freshness_omissions"]:
                candidate["omissions"] = [*candidate["omissions"],
                    {"note_id": item.get("note_id"), "reason": "stale",
                     "details": item["freshness_omissions"]}]
                candidate["coverage"]["partial"] = True
            encoded = _wire(candidate).encode("utf-8")
            if len(encoded) > int(budget_bytes):
                result["omissions"].append({"reason": "budget", "note_id": item.get("note_id")})
                result["coverage"]["partial"] = True
                continue
            result = candidate
        # Limit omitted-record detail so adversarial or crowded notebooks cannot
        # grow the response after the per-record budget check.
        if len(result["omissions"]) > 32:
            result["omissions"] = result["omissions"][:31] + [{"reason": "omissions_truncated"}]
            result["coverage"]["partial"] = True
        while len(_wire(result).encode("utf-8")) > int(budget_bytes):
            if result["omissions"]:
                result["omissions"].pop()
            elif result["notes"]:
                result["notes"].pop()
                result["coverage"]["returned"] -= 1
                result["coverage"]["partial"] = True
            elif result["goals"]:
                result["goals"].pop()
                result["coverage"]["returned"] -= 1
                result["coverage"]["partial"] = True
            else:
                break
        return result
