"""Project-Control-owned retrieval and packet routing for advisory skill corpora.

Skills contribute inert semantic metadata, never execution policy. Broad queries
are recognized by the bounded lexical terms overview/compare/synthesis/summarize.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from .security import redact_output_text, redact_text
from .source_index import SourceLexicalIndex

PARSER_VERSION = "skill-sections-v1"
LABELS = {"origin": "agent_skill", "authority": "advisory_instruction", "mutation_authority": False}
SUPPORTED = {".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".tsv"}
BROAD = re.compile(r"\b(overview|compare|comparison|synthesis|synthesize|summari[sz]e|across|comprehensive)\b", re.I)


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


@dataclass(frozen=True)
class Section:
    path: str
    start: int
    end: int
    title: str
    text: str
    identity: str

    @property
    def id(self) -> str:
        return "e-" + _digest([self.path, self.start, self.end, self.identity])[:24]

    def evidence(self, *, excerpt: bool = False) -> dict[str, Any]:
        return {"id": self.id, "resource": self.path, "line_start": self.start,
                "line_end": self.end, "identity": self.identity, "title": self.title,
                "content": self.text[:320] if excerpt else self.text, **LABELS}


class SkillContext:
    def __init__(self, registry: Any, analyze_packet: Callable[[dict[str, Any]], dict[str, Any]] | None = None):
        self.registry = registry
        self.analyze_packet = analyze_packet
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[str, list[Section], SourceLexicalIndex, dict[str, Any]]] = {}

    def _inventory(self, skill: str) -> list[dict[str, Any]]:
        rows = self.registry.resources(skill)
        if isinstance(rows, dict):
            rows = rows.get("resources", [])
        if len(rows) > 4096:
            raise ValueError("skill_inventory_limit")
        if sum(int(row.get("bytes", 0)) for row in rows) > 32 * 1024 * 1024:
            raise ValueError("skill_corpus_size_limit")
        return rows

    def _snapshot(self, skill: str) -> tuple[str, list[Section], SourceLexicalIndex, dict[str, Any]]:
        with self._lock:
            inventory = self._inventory(skill)
            fingerprint = _digest([PARSER_VERSION, inventory])
            cached = self._cache.get(skill)
            if cached and cached[0] == fingerprint:
                return cached
            sections: list[Section] = []
            manifests: list[dict[str, Any]] = []
            rows: list[tuple[str, int, str]] = []
            content_ids: list[tuple[str, str]] = []
            documents: dict[str, dict[str, Any]] = {}
            for resource in inventory:
                path = resource["path"]
                if len(PurePosixPath(path).parts) > 8:
                    continue
                is_manifest = PurePosixPath(path).name == ".project-control-corpus.json"
                if not is_manifest and PurePosixPath(path).suffix.lower() not in SUPPORTED:
                    continue
                document = self.registry.read_text(skill, path)
                documents[path] = document
                text, identity = document["content"], document["identity"]
                content_ids.append((path, str(identity)))
                if is_manifest:
                    manifest = json.loads(text)
                    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                        raise ValueError("skill_manifest_invalid")
                    if len(manifest.get("resources", [])) > 4096 or len(manifest.get("relationships", [])) > 16384:
                        raise ValueError("skill_manifest_limit")
                    manifests.append(manifest)
                    continue
                lines = text.splitlines()
                boundaries = [0]
                for number, line in enumerate(lines):
                    if number and (line.startswith("#") or number - boundaries[-1] >= 60):
                        boundaries.append(number)
                boundaries.append(len(lines))
                for start, end in zip(boundaries, boundaries[1:]):
                    body = "\n".join(lines[start:end])
                    if not body.strip():
                        continue
                    title = lines[start].lstrip("# ")[:160]
                    sections.append(Section(path, start + 1, end, title, body, str(identity)))
                rows.extend((path, n, line) for n, line in enumerate(lines, 1) if line.strip())
            if self._inventory(skill) != inventory:
                raise ValueError("skill_corpus_changed_during_index")
            semantic: dict[str, Any] = {"resources": {}, "edges": {}, "unresolved": []}
            known = {section.path for section in sections}
            for manifest in manifests:
                source = manifest.get("source", {})
                if source:
                    _, archive_hash = self.registry.read_bytes(skill, source["archive"], max_bytes=64 * 1024 * 1024)
                    if archive_hash != source.get("sha256"):
                        raise ValueError("skill_ingestion_stale")
                for item in manifest.get("resources", []):
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item.get("path") not in known:
                        raise ValueError("skill_manifest_resource_invalid")
                    key = item["id"]
                    if len(key) > 160 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key):
                        raise ValueError("skill_manifest_id_invalid")
                    if item.get("sha256") and str(dict(content_ids).get(item["path"])) != item["sha256"]:
                        raise ValueError("skill_manifest_content_stale")
                    if key in semantic["resources"]:
                        raise ValueError("skill_manifest_duplicate_id")
                    semantic["resources"][key] = {k: item[k] for k in ("path", "title", "summary", "tags", "aliases", "line_start", "line_end") if k in item}
                    hint = " ".join(str(item.get(k, "")) for k in ("id", "title", "summary", "tags", "aliases"))
                    hint_line = item.get("line_start", 1)
                    if "line_start" in item or "line_end" in item:
                        start, end = item.get("line_start"), item.get("line_end")
                        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                            raise ValueError("skill_manifest_span_invalid")
                        document = documents[item["path"]]
                        lines = document["content"].splitlines()
                        if end > len(lines):
                            raise ValueError("skill_manifest_span_invalid")
                        section = Section(item["path"], start, end, redact_text(str(item.get("title", key)))[:160], "\n".join(lines[start-1:end]), str(document["identity"]))
                        if section.id not in {s.id for s in sections}:
                            sections.append(section)
                    rows.append((item["path"], hint_line, hint))
                for edge in manifest.get("relationships", []):
                    if not isinstance(edge, dict) or not all(isinstance(edge.get(k), str) for k in ("from", "to", "type")):
                        raise ValueError("skill_manifest_relationship_invalid")
                    if not all(len(edge[k]) <= 160 and re.fullmatch(r"[A-Za-z0-9_.:-]+", edge[k]) for k in ("from", "to", "type")):
                        raise ValueError("skill_manifest_relationship_invalid")
                    semantic["edges"].setdefault(edge["from"], []).append((edge["to"], edge["type"]))
            for origin, edges in semantic["edges"].items():
                for target, relation in edges:
                    if origin not in semantic["resources"] or target not in semantic["resources"]:
                        semantic["unresolved"].append({"from": origin, "to": target, "type": relation})
            identity = _digest([PARSER_VERSION, content_ids, semantic])
            index = SourceLexicalIndex("agent-skill:" + skill, identity)
            if not index.is_complete():
                index.build_rows(rows)
            semantic["identity"] = identity
            result = (fingerprint, sections, index, semantic)
            self._cache[skill] = result
            # Keep only a small number of disposable in-memory section maps.
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
            return result

    def _retrieve(self, query: str, sections: list[Section], index: SourceLexicalIndex, semantic: dict[str, Any]) -> tuple[list[Section], list[dict[str, str]]]:
        hits = index.search(query, limit=200, prefer_current=False)
        selected: dict[str, Section] = {}
        for hit in hits:
            matching = [s for s in sections if s.path == hit.path and s.start <= hit.line <= s.end]
            section = min(matching, key=lambda s: s.end - s.start) if matching else None
            if section:
                selected.setdefault(section.id, section)
        # Broad queries need coverage, rather than treating a generic word as
        # evidence that the corpus contains no relevant material.
        if BROAD.search(query) and not selected:
            selected = {s.id: s for s in sections}
        selected_paths = {s.path for s in selected.values()}
        seeds = [key for key, resource in semantic["resources"].items()
                 if resource["path"] in selected_paths and (
                     "line_start" not in resource or any(
                         s.path == resource["path"] and s.start == resource["line_start"] and s.end == resource["line_end"]
                         for s in selected.values()))]
        visited = set(seeds[:64])
        frontier = list(visited)
        expanded: list[dict[str, str]] = []
        for _ in range(2):
            next_frontier = []
            for origin in frontier:
                for target, relation in semantic["edges"].get(origin, []):
                    if target in visited or target not in semantic["resources"] or len(visited) >= 64:
                        continue
                    visited.add(target)
                    next_frontier.append(target)
                    path = semantic["resources"][target]["path"]
                    expanded.append({"from": origin, "to": target, "type": relation})
                    for section in sections:
                        node = semantic["resources"][target]
                        if section.path == path and ("line_start" not in node or (section.start == node["line_start"] and section.end == node["line_end"])):
                            selected.setdefault(section.id, section)
            frontier = next_frontier
        return list(selected.values()), expanded

    def context(self, query: str, skill: str = "auto", budget_bytes: int = 16384, continuation_cursor: str | None = None) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > 4096:
            raise ValueError("skill_query_invalid")
        if not isinstance(budget_bytes, int) or not 2048 <= budget_bytes <= 65536:
            raise ValueError("skill_context_budget_invalid")
        selection_warnings = []
        if skill == "auto":
            # Catalog metadata is cheap; natural-language questions must not be
            # passed to the registry's literal conjunctive list filter.
            candidates = []
            cursor = None
            for _ in range(128):
                listing = self.registry.list(max_items=128, continuation_cursor=cursor)
                candidates.extend(listing.get("skills", []))
                selection_warnings.extend(listing.get("warnings", []))
                cursor = listing.get("continuation_cursor")
                if not cursor or len(candidates) >= 128:
                    break
            stopwords = {"how", "should", "could", "would", "what", "which", "when", "where", "the", "and", "for", "with", "from", "this", "that", "review", "overview", "summarize", "synthesize", "compare", "comparison", "across", "please", "references", "skill"}
            terms = {t.casefold() for t in re.findall(r"[A-Za-z0-9_]{2,128}", query)} - stopwords
            ranked = []
            for candidate in candidates[:128]:
                name = candidate["name"].casefold()
                description = candidate["description"].casefold()
                try:
                    paths = " ".join(row["path"] for row in self._inventory(candidate["id"])).casefold()
                except ValueError:
                    selection_warnings.append({"skill": candidate["id"], "reason": "inventory_unavailable"})
                    continue
                score = sum(10 * (t in name) + 3 * (t in description) + (t in paths) for t in terms)
                if score:
                    ranked.append((score, candidate["id"]))
            if not ranked:
                return {**LABELS, "status": "no_matching_skill", "evidence": [], "selection_warnings": selection_warnings[:128]}
            skill = sorted(ranked, key=lambda item: (-item[0], item[1]))[0][1]
        self.registry.get(skill)
        _, sections, index, semantic = self._snapshot(skill)
        selected, expanded = self._retrieve(query, sections, index, semantic)
        identity = semantic["identity"]
        offset = 0
        if continuation_cursor:
            if not isinstance(continuation_cursor, str) or len(continuation_cursor.encode()) > 2048:
                raise ValueError("skill_continuation_invalid")
            try:
                cursor = json.loads(continuation_cursor)
                if cursor["identity"] != identity or cursor["skill"] != skill or cursor["query"] != _digest(query):
                    raise ValueError("skill_continuation_stale")
                offset = cursor["offset"]
                if not isinstance(offset, int) or offset < 0 or offset > len(selected):
                    raise ValueError("skill_continuation_invalid")
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("skill_continuation_invalid") from exc
        resources = len({s.path for s in selected})
        relevant_bytes = sum(len(s.text.encode()) for s in selected)
        route = "direct" if resources <= 2 and relevant_bytes <= 8192 else "indexed"
        if resources > 8 or relevant_bytes > 32768 or (BROAD.search(query) and resources >= 4):
            route = "machine"
        result: dict[str, Any] = {**LABELS, "status": "available", "skill": skill, "corpus_identity": identity,
            "route": route, "route_reason": {"relevant_resources": resources, "relevant_bytes": relevant_bytes, "broad_query": bool(BROAD.search(query))},
            "evidence": [], "summaries": [], "coverage": {"total_sections": len(selected), "returned_sections": 0},
            "relationships": expanded[:16], "unresolved_relationships": semantic["unresolved"][:16], "continuation_cursor": None}
        consumed = offset
        attempted_paths: set[str] = set()
        result["selection_warnings"] = selection_warnings
        if route == "machine":
            for _ in range(4):
                packet_start = consumed
                packet = {**LABELS, "query": query, "source_identity": {"skill_id": skill, "corpus_identity": identity}, "evidence": []}
                while consumed < len(selected) and len(packet["evidence"]) < 64:
                    item = selected[consumed].evidence()
                    trial = {**packet, "evidence": packet["evidence"] + [item]}
                    if len(_encoded(trial)) > 48 * 1024:
                        if not packet["evidence"]:
                            # Very long single lines remain evidence with exact
                            # line provenance, explicitly bounded for transport.
                            item["content"] = item["content"][:6000]
                            item["truncated"] = True
                        else:
                            break
                    packet["evidence"].append(item)
                    consumed += 1
                if not packet["evidence"]:
                    break
                attempted_paths.update(item["resource"] for item in packet["evidence"])
                try:
                    response = self.analyze_packet(json.loads(_encoded(packet))) if self.analyze_packet else {"status": "unavailable"}
                except Exception:
                    response = {"status": "unavailable"}
                valid_ids = {item["id"] for item in packet["evidence"]}
                citations = response.get("evidence_ids", []) if isinstance(response, dict) else []
                if not isinstance(citations, list) or any(c not in valid_ids for c in citations):
                    response = {"status": "unavailable"}
                    citations = []
                if not isinstance(response, dict):
                    response = {"status": "unavailable"}
                if response.get("status") in {"available", "completed", "ok"} and (not citations or not isinstance(response.get("summary"), str)):
                    response = {"status": "unavailable"}
                summary = {"status": response.get("status", "unavailable"), "summary": redact_text(str(response.get("summary", "")))[:2000], "evidence_ids": citations[:64]}
                if isinstance(response.get("uncertainty"), str):
                    summary["uncertainty"] = redact_text(response["uncertainty"])[:500]
                result["summaries"].append(summary)
                if summary["status"] not in {"available", "completed", "ok"}:
                    result["status"] = "unavailable"
                    reason = redact_output_text(str(response.get("reason", "observer_analysis_unavailable")))[:500]
                    summary["reason"] = reason
                    result["reason"] = reason
                    if not result["evidence"]:
                        result["evidence"] = [s.evidence(excerpt=True) for s in selected[packet_start:consumed][:2]]
                    consumed = packet_start
                    break
                for section in selected[offset:consumed]:
                    if section.id in citations and len(result["evidence"]) < 8:
                        result["evidence"].append(section.evidence(excerpt=True))
                if consumed >= len(selected) or len(_encoded(result)) > budget_bytes - 1024:
                    break
        else:
            for section in selected[offset:]:
                item = section.evidence()
                trial = {**result, "evidence": result["evidence"] + [item]}
                if len(_encoded(trial)) > budget_bytes - 768:
                    if not result["evidence"]:
                        item["content"] = item["content"][:max(256, (budget_bytes - 1800) // 4)]
                        item["truncated"] = True
                        result["evidence"].append(item)
                        consumed += 1
                    break
                result["evidence"].append(item)
                consumed += 1
        # Revalidate all selected resource identities after backend latency.
        revalidate_paths = attempted_paths | {s.path for s in selected[offset:consumed]} | {item["resource"] for item in result["evidence"]}
        for path in revalidate_paths:
            expected = next(s.identity for s in selected if s.path == path)
            if str(self.registry.read_text(skill, path)["identity"]) != expected:
                raise ValueError("skill_resource_changed_during_context")
        result["coverage"]["returned_sections"] = len(result["evidence"])
        result["coverage"]["analyzed_sections"] = consumed - offset if route == "machine" else 0
        if consumed < len(selected):
            result["continuation_cursor"] = json.dumps({"identity": identity, "skill": skill, "query": _digest(query), "offset": consumed}, separators=(",", ":"))
        while len(_encoded(result)) > budget_bytes and result["evidence"]:
            result["evidence"].pop()
        while len(_encoded(result)) > budget_bytes and result["summaries"]:
            result["summaries"].pop()
        if len(_encoded(result)) > budget_bytes:
            result["relationships"] = []
            result["unresolved_relationships"] = []
        return result
