from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..adapters.ctxpp import CtxppReadAdapter
from ..adapters.git import GitReadAdapter
from ..config import DEFAULT_DENY_PATTERNS, ProjectControlConfig
from ..models import ProjectSnapshot, SourceContextInput, ToolEnvelope, envelope
from ..registry import WorkspaceRegistry
from ..security import SecurityError, is_allowlisted_text_path, is_denied, redact, redact_text, resolve_registered_path
from ..source_index import SourceLexicalIndex
from ..subprocesses import CommandError
from ..worktrees import WorktreeCatalog, WorktreeSelectionError
from ..retrieval import economical_record


def _cursor(identity: str, offset: int) -> str:
    raw = json.dumps({"v": 1, "source": identity, "offset": offset}, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None, identity: str) -> int:
    if not value:
        return 0
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        item = json.loads(raw)
        if item.get("v") != 1 or item.get("source") != identity:
            raise ValueError
        return max(0, int(item["offset"]))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid_or_stale_continuation_cursor") from None


def _file_identity(path: Path) -> tuple[int, int, int, str]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, str(stat.st_mtime_ns)


def _range_read(root: Path, relative: str, start: int | None, end: int | None, deny: list[str], budget: int) -> dict[str, Any]:
    path = resolve_registered_path(root, relative, deny_patterns=deny)
    if not is_allowlisted_text_path(Path(relative)):
        raise SecurityError("file type is not allowlisted text")
    before = _file_identity(path)
    first = start or 1
    last = end or max(first, first + 399)
    chunks: list[str] = []
    used = 0
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            if number < first:
                continue
            if number > last:
                break
            if b"\0" in raw:
                raise SecurityError("binary file rejected")
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SecurityError("non-UTF-8 file rejected") from exc
            encoded = line.encode()
            if used + len(encoded) > budget:
                break
            chunks.append(line.rstrip("\r\n"))
            used += len(encoded)
    after = _file_identity(path)
    return {
        "path": relative,
        "line_start": first,
        "line_end": first + len(chunks) - 1,
        "excerpt": redact_text("\n".join(chunks)),
        "file_identity": hashlib.sha256("\0".join(map(str, after)).encode()).hexdigest(),
        "racy": before != after,
    }


def _workflow_mapping(snapshot: ProjectSnapshot, root: Path) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for run in snapshot.todo_workflow.get("runs", []):
        if not isinstance(run, dict):
            continue
        for lane in run.get("lanes", []):
            if not isinstance(lane, dict) or not isinstance(lane.get("workspace"), dict):
                continue
            workspace = lane["workspace"]
            raw_path = workspace.get("worktree_path")
            try:
                matches = isinstance(raw_path, str) and Path(raw_path).resolve(strict=True) == root
            except OSError:
                matches = False
            if matches:
                queue = lane.get("queue", [])
                head = next((item for item in queue if isinstance(item, dict)), {})
                mappings.append({
                    "run_id": run.get("id"), "lane_id": lane.get("id"),
                    "task_id": head.get("task_id"), "workspace_id": workspace.get("id"),
                    "workspace_mode": workspace.get("mode") or lane.get("workspace_mode"),
                })
    return mappings


def source_context(config: ProjectControlConfig, snapshot: ProjectSnapshot, request: SourceContextInput) -> ToolEnvelope:
    registry = WorkspaceRegistry(config)
    repository = registry.repository(request.project, request.repository)
    workspace = registry.workspace(request.project)
    deny = [*DEFAULT_DENY_PATTERNS, *workspace.deny_patterns]
    warnings: list[str] = []
    try:
        selected = WorktreeCatalog(repository.alias, repository.root).select(request.worktree_id)
    except WorktreeSelectionError as exc:
        return envelope("source_context", snapshot, {"error": str(exc), "targets": []}, warnings=[str(exc)])
    root = selected.selected.root
    git = GitReadAdapter(root)
    selector = request.source_selector
    if selector == "working_tree":
        revision = None
        source_identity = f"{selected.selected.head}:{selected.selected.working_tree_fingerprint}"
    elif selector == "HEAD":
        revision = selected.selected.head
        source_identity = revision
    else:
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", selector):
            return envelope("source_context", snapshot, {"error": "invalid_source_selector", "targets": []}, warnings=["invalid_source_selector"])
        try:
            revision = git.verify_revision(selector)
        except Exception:
            return envelope("source_context", snapshot, {"error": "invalid_source_selector", "targets": []}, warnings=["invalid_source_selector"])
        source_identity = revision
    query_identity = hashlib.sha256(json.dumps({
        "source": source_identity,
        "targets": [target.model_dump() for target in request.targets],
        "relations": request.requested_relations,
    }, sort_keys=True).encode()).hexdigest()
    offset = _decode_cursor(request.continuation_cursor, query_identity)
    results: list[dict[str, Any]] = []
    per_target = max(1024, request.budget_bytes // max(1, len(request.targets)))
    lexical: SourceLexicalIndex | None = None
    if selector == "working_tree" and any(target.kind in {"text", "subsystem"} for target in request.targets):
        lexical = SourceLexicalIndex(selected.common_id, source_identity)
        try:
            if not lexical.is_complete():
                lexical.build(root, [*git.tracked_files(), *selected.selected.dirty_paths], deny)
        except (OSError, sqlite3.Error):
            lexical = None
            warnings.append("source_index_unavailable")
    for target in request.targets:
        item: dict[str, Any] = {"kind": target.kind, "target": target.value}
        try:
            if target.kind == "path":
                if revision is None:
                    detail = _range_read(root, target.value, target.line_start, target.line_end, deny, per_target)
                    if detail.pop("racy"):
                        # Retry once from a newly opened descriptor; preserve an explicit race if it moves again.
                        detail = _range_read(root, target.value, target.line_start, target.line_end, deny, per_target)
                        if detail.pop("racy"):
                            warnings.append("racy_source_read")
                            detail["status"] = "raced"
                    item.update(detail)
                else:
                    if Path(target.value).is_absolute() or ".." in Path(target.value).parts or is_denied(Path(target.value), deny):
                        raise SecurityError("path must be repository relative")
                    text = git.show_text(revision, target.value, max_bytes=max(2 * 1024 * 1024, per_target * 8))
                    lines = text.splitlines()
                    first = target.line_start or 1
                    last = target.line_end or min(len(lines), first + 399)
                    item.update(path=target.value, line_start=first, line_end=last, excerpt=redact_text("\n".join(lines[first - 1:last]))[:per_target])
            elif target.kind == "symbol":
                semantic = CtxppReadAdapter(root, git, deny).inspect(target.value, max_items=30)
                item.update(semantic)
                warnings.extend(semantic.get("warnings", []))
            elif lexical is not None:
                matches = lexical.search(target.value, offset=offset, limit=50)
                item.update(source="private_lexical_index", matches=[match.__dict__ for match in matches])
            else:
                item.update(source="bounded_git_grep", matches=git.grep(target.value, max_items=50, deny_patterns=deny))
            if "recent_changes" in request.requested_relations and target.kind == "path":
                item["recent_changes"] = git.changed_path_commits(target.value, 12)
            relation_token = Path(target.value).stem if target.kind == "path" else target.value
            if target.kind == "path" and isinstance(item.get("excerpt"), str):
                symbols = re.findall(r"\b(?:def|class|struct|enum)\s+([A-Za-z_]\w*)", item["excerpt"])
                if symbols:
                    relation_token = symbols[0]
            if "tests" in request.requested_relations:
                item["tests"] = [match for match in git.grep(relation_token, max_items=30, deny_patterns=deny) if "test" in str(match["path"]).casefold()]
            if "documentation" in request.requested_relations:
                item["documentation"] = [match for match in git.grep(relation_token, max_items=30, deny_patterns=deny) if Path(str(match["path"])).suffix.casefold() in {".md", ".rst"}]
            if "build_config_references" in request.requested_relations:
                item["build_config_references"] = [match for match in git.grep(relation_token, max_items=30, deny_patterns=deny) if Path(str(match["path"])).name in {"CMakeLists.txt", "Makefile", "pyproject.toml"}]
            base_matches = item.get("matches", []) if isinstance(item.get("matches"), list) else []
            if "definitions" in request.requested_relations:
                item["definitions"] = [
                    match for match in base_matches
                    if str(match.get("kind", "")).casefold() in {"function", "class", "struct", "enum", "definition"}
                    or re.search(r"\b(def|class|struct|enum)\b", str(match.get("excerpt", "")))
                ][:30]
            if "references" in request.requested_relations:
                item["references"] = base_matches[:30]
            for relation in ("callers", "callees"):
                if relation in request.requested_relations:
                    semantic_relations = [
                        match for match in base_matches
                        if relation[:-1] in str(match.get("kind", "")).casefold()
                    ]
                    item[relation] = {
                        "status": "available" if semantic_relations else "unavailable",
                        "basis": "existing_ctxpp_index" if semantic_relations else "semantic_relation_unavailable",
                        "matches": semantic_relations[:30],
                    }
            table_relations = {
                "task_ownership": "tasks",
                "interfaces": "interfaces",
            }
            for relation, table in table_relations.items():
                if relation in request.requested_relations:
                    item[relation] = [
                        economical_record(record, expanded=request.detail == "expanded") for record in snapshot.todo_tables.get(table, [])
                        if relation_token.casefold() in json.dumps(record, sort_keys=True, default=str).casefold()
                    ][:30]
            if "performance_evidence" in request.requested_relations:
                evidence = [
                    record for name in ("facts", "results") for record in snapshot.cuda.get(name, [])
                    if isinstance(record, dict) and relation_token.casefold() in json.dumps(record, sort_keys=True, default=str).casefold()
                ]
                item["performance_evidence"] = evidence[:30]
        except (SecurityError, OSError, ValueError, CommandError, sqlite3.Error) as exc:
            item.update(status="unavailable", error=str(exc))
            warnings.append("source_target_unavailable")
        results.append(item)
    # Apply a deterministic source-specific section budget before adding fixed
    # provenance. This preserves target identity and continuation metadata.
    target_budget = max(512, request.budget_bytes - 2048)
    considered_items = sum(len(item.get("matches", [])) for item in results)
    while len(json.dumps(results, sort_keys=True, default=str).encode()) > target_budget:
        lists = [
            value for item in results for value in item.values()
            if isinstance(value, list) and value
        ]
        if lists:
            max(lists, key=len).pop()
            continue
        excerpts = [item for item in results if isinstance(item.get("excerpt"), str) and len(item["excerpt"]) > 128]
        if excerpts:
            item = max(excerpts, key=lambda value: len(value["excerpt"]))
            item["excerpt"] = item["excerpt"][: len(item["excerpt"]) // 2] + "…"
            continue
        break
    returned_items = sum(len(item.get("matches", [])) for item in results)
    has_more = returned_items < considered_items or any(len(item.get("matches", [])) >= 50 for item in results)
    data = {
        "repository": repository.alias,
        "worktree": selected.public(),
        "workflow_mapping": _workflow_mapping(snapshot, root),
        "source_selector": selector,
        "source_commit": revision or selected.selected.head,
        "source_freshness": "working_tree" if revision is None else "immutable_commit",
        "targets": results,
        "counts": {"considered": len(request.targets), "returned": len(results)},
        "result_items": {"considered": considered_items, "returned": returned_items},
        "continuation_cursor": _cursor(query_identity, offset + 50) if has_more else None,
        "preconditions": snapshot.observation_preconditions().model_dump(mode="json"),
    }
    # Last-line defense: adapters must never surface secrets or private paths.
    return envelope("source_context", snapshot, redact(data), warnings=list(dict.fromkeys(warnings)))
