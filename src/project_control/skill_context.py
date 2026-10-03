"""Project-Control-owned retrieval and packet routing for advisory skill corpora.

Skills contribute inert semantic metadata, never execution policy. Broad queries
are recognized by the bounded lexical terms overview/compare/synthesis/summarize.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Callable

from .security import redact_output_text, redact_text
from .source_index import SourceLexicalIndex

PARSER_VERSION = "skill-sections-v2"
LABELS = {"origin": "agent_skill", "authority": "advisory_instruction", "mutation_authority": False}
SUPPORTED = {".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".tsv"}
ROLES = {"canonical", "semantic_index", "deep_reference", "operational_guide", "derived_summary", "aggregate_view", "archive", "navigation", "generated", "legacy_router", "evidence", "experiment"}
CREATIVE = re.compile(r"\b(representation|state|unusual|unconventional|crazy|circuit|composition|compositions|compositional|substrate|creative)\b", re.I)
CONVENTIONAL = re.compile(r"\b(optimi[sz](?:e|ation)|profil(?:e|ing)|diagnos\w*|bottleneck|register pressure|occupancy)\b", re.I)
ARCHITECTURES = ({"volta", "v100", "sm_70"}, {"ampere", "a100", "sm_80"}, {"hopper", "h100", "sm_90"}, {"blackwell", "b200", "sm_100"})
QUERY_STOPWORDS = {"how", "should", "could", "would", "what", "which", "when", "where", "why", "the", "and", "for", "with", "from", "this", "that", "does", "can", "into", "use", "using", "are", "have", "its", "than", "then", "need", "overview", "compare", "comparison", "across", "comprehensive", "synthesis", "summarize", "synthesize"}
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

    semantic_ids: tuple[str, ...] = ()
    role: str | None = None
    lineage: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    alias_provenance: tuple[tuple[str, int, int], ...] = ()

    @property
    def id(self) -> str:
        return "e-" + _digest([self.path, self.start, self.end, self.identity])[:24]

    def evidence(self, *, excerpt: bool = False, metadata_budget_bytes: int = 1024) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if self.semantic_ids:
            metadata["semantic_ids"] = list(self.semantic_ids)
        if self.role:
            metadata["role"] = self.role
        if self.lineage:
            metadata["lineage"] = list(self.lineage)
        if self.aliases:
            metadata["aliases"] = list(self.aliases)
        if self.alias_provenance:
            metadata["alias_provenance"] = [{"resource": p, "line_start": a, "line_end": b} for p, a, b in self.alias_provenance]
        for field in ("aliases", "alias_provenance", "lineage", "semantic_ids"):
            while len(_encoded(metadata)) > metadata_budget_bytes and metadata.get(field):
                if field == "semantic_ids" and len(metadata[field]) == 1:
                    break
                metadata[field].pop()
                metadata["metadata_truncated"] = True
            if field in metadata and not metadata[field]:
                del metadata[field]
        return {"id": self.id, "resource": self.path, "line_start": self.start,
                "line_end": self.end, "identity": self.identity, "title": self.title,
                "content": self.text[:320] if excerpt else self.text, **LABELS, **metadata}


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
                    if not isinstance(manifest.get("resources", []), list) or not isinstance(manifest.get("relationships", []), list):
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
            known = set(documents) - {p for p in documents if PurePosixPath(p).name == ".project-control-corpus.json"}
            for manifest in manifests:
                source = manifest.get("source", {})
                if source:
                    _, archive_hash = self.registry.read_bytes(skill, source["archive"], max_bytes=64 * 1024 * 1024)
                    if archive_hash != source.get("sha256"):
                        raise ValueError("skill_ingestion_stale")
                for item in manifest.get("resources", []):
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("path"), str) or item.get("path") not in known:
                        raise ValueError("skill_manifest_resource_invalid")
                    key = item["id"]
                    if len(key) > 160 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", key):
                        raise ValueError("skill_manifest_id_invalid")
                    if item.get("sha256") and str(dict(content_ids).get(item["path"])) != item["sha256"]:
                        raise ValueError("skill_manifest_content_stale")
                    if key in semantic["resources"]:
                        raise ValueError("skill_manifest_duplicate_id")
                    if "role" in item and (not isinstance(item["role"], str) or item["role"] not in ROLES):
                        raise ValueError("skill_manifest_role_invalid")
                    if "index_excluded" in item and not isinstance(item["index_excluded"], bool):
                        raise ValueError("skill_manifest_exclusion_invalid")
                    for field, maximum in (("title", 512), ("summary", 4096)):
                        if field in item and (not isinstance(item[field], str) or len(item[field]) > maximum):
                            raise ValueError("skill_manifest_metadata_invalid")
                    for field, maximum in (("lineage", 160), ("tags", 160), ("aliases", 512)):
                        values = item.get(field, [])
                        if not isinstance(values, list) or len(values) > 64 or any(not isinstance(v, str) or not v or len(v) > maximum for v in values):
                            raise ValueError("skill_manifest_metadata_invalid")
                        if field == "lineage" and any(not re.fullmatch(r"[A-Za-z0-9_.:-]+", v) for v in values):
                            raise ValueError("skill_manifest_lineage_invalid")
                    semantic["resources"][key] = {k: item[k] for k in ("path", "title", "summary", "tags", "aliases", "line_start", "line_end", "role", "index_excluded", "lineage") if k in item}
                    hint = " ".join(str(item.get(k, "")) for k in ("id", "title", "summary", "tags", "aliases"))
                    hint_line = item.get("line_start", 1)
                    if "line_start" in item or "line_end" in item:
                        start, end = item.get("line_start"), item.get("line_end")
                        if type(start) is not int or type(end) is not int or start < 1 or end < start:
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
            excluded = {node["path"] for node in semantic["resources"].values() if node.get("index_excluded", False)}
            sections = [section for section in sections if section.path not in excluded]
            rows = [row for row in rows if row[0] not in excluded]
            nodes_by_path: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for key, node in semantic["resources"].items():
                nodes_by_path.setdefault(node["path"], []).append((key, node))
            annotated = []
            for section in sections:
                nodes = [(key, node) for key, node in nodes_by_path.get(section.path, []) if
                         ("line_start" not in node or (node["line_start"] <= section.start and section.end <= node["line_end"]))]
                nodes.sort(key=lambda pair: (pair[1].get("role") != "canonical", pair[0]))
                if nodes:
                    section = replace(section, semantic_ids=tuple(key for key, _ in nodes)[:64],
                        role=next((node["role"] for _, node in nodes if "role" in node), None),
                        lineage=tuple(sorted({v for _, node in nodes for v in node.get("lineage", [])}))[:64],
                        tags=tuple(sorted({v for _, node in nodes for v in node.get("tags", [])}))[:64],
                        aliases=tuple(sorted({v for _, node in nodes for v in node.get("aliases", [])}))[:64])
                annotated.append(section)
            sections = annotated
            semantic["excluded_paths"] = sorted(excluded)
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

    def _retrieve(self, query: str, sections: list[Section], index: SourceLexicalIndex, semantic: dict[str, Any], context_terms: tuple[str, ...] = ()) -> tuple[list[Section], list[dict[str, str]]]:
        original_terms = re.findall(r"[A-Za-z0-9_]{2,128}", query)
        ignored = QUERY_STOPWORDS | {v for group in ARCHITECTURES for v in group} | {v.casefold() for v in context_terms}
        meaningful = [v for v in original_terms if v.casefold() not in ignored]
        lexical_query = " ".join(meaningful) if meaningful else query
        hits = index.search(lexical_query, limit=200, prefer_current=False)
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
        broad = bool(BROAD.search(query))
        query_terms = {v.casefold() for v in meaningful or original_terms}
        query_arch = [group for group in ARCHITECTURES if group & {v.casefold() for v in original_terms}]

        def relevance(section: Section) -> int:
            metadata = " ".join(section.aliases)
            for key in section.semantic_ids:
                node = semantic["resources"][key]
                metadata += " " + str(node.get("title", "")) + " " + str(node.get("summary", ""))
            text_terms = {v.casefold() for v in re.findall(r"[A-Za-z0-9_]{2,128}", section.text + " " + metadata)}
            return len(query_terms & text_terms)

        # Compositional questions need narrow mechanisms covering different
        # parts of the query; an omnibus section must not set their cutoff.
        if not broad and not CREATIVE.search(query) and selected:
            strongest = max(relevance(s) for s in selected.values())
            if strongest >= 3:
                selected = {key: s for key, s in selected.items() if relevance(s) >= (strongest + 1) // 2}

        def priority(section: Section) -> tuple[int, int, str, int, str]:
            boost = 0
            if CREATIVE.search(query) and section.role in {"canonical", "deep_reference"}:
                boost += 2
            elif not CREATIVE.search(query) and CONVENTIONAL.search(query) and section.role == "operational_guide":
                boost += 2
            tags = {v.casefold() for tag in section.tags for v in re.findall(r"[A-Za-z0-9_]{2,128}", tag)}
            if any(group & tags for group in query_arch):
                boost += 2
            return (-relevance(section) - boost, section.role != "canonical", section.path, section.start, section.id)

        ordered = sorted(selected.values(), key=priority)
        seeds = []
        for section in ordered:
            for key in section.semantic_ids:
                if key not in seeds:
                    seeds.append(key)
        seeds = seeds[:64 if broad else 8]
        visited = set(seeds)
        frontier = seeds
        expanded: list[dict[str, str]] = []
        added = 0
        for _ in range(2):
            next_frontier = []
            for origin in frontier:
                for target, relation in semantic["edges"].get(origin, []):
                    node = semantic["resources"].get(target)
                    if target in visited or not node or node["path"] in semantic.get("excluded_paths", []) or len(visited) >= (64 if broad else 16):
                        continue
                    visited.add(target)
                    next_frontier.append(target)
                    expanded.append({"from": origin, "to": target, "type": relation})
                    candidates = sorted((s for s in sections if target in s.semantic_ids), key=priority)
                    for section in candidates:
                        if not broad and added >= 16:
                            break
                        if section.id not in selected:
                            selected[section.id] = section
                            added += 1
            frontier = next_frontier
        # Exact text only: unique sections survive, canonical provenance wins.
        grouped: dict[str, list[Section]] = {}
        for section in selected.values():
            grouped.setdefault(section.text, []).append(section)
        selected_ids = set(selected)
        for section in sections:
            if section.text in grouped and section.id not in selected_ids:
                grouped[section.text].append(section)
        deduplicated = []
        for duplicates in grouped.values():
            duplicates.sort(key=lambda s: (s.role != "canonical", priority(s)))
            winner = duplicates[0]
            if len(duplicates) > 1:
                winner = replace(winner,
                    semantic_ids=tuple(sorted({v for s in duplicates for v in s.semantic_ids}))[:64],
                    lineage=tuple(sorted({v for s in duplicates for v in s.lineage}))[:64],
                    aliases=tuple(sorted({v for s in duplicates for v in s.aliases}))[:64],
                    alias_provenance=tuple((s.path, s.start, s.end) for s in duplicates[1:])[:8])
            deduplicated.append(winner)
        return sorted(deduplicated, key=priority), expanded

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
        registered_skill = self.registry.get(skill)
        fingerprint, sections, index, semantic = self._snapshot(skill)
        selected, expanded = self._retrieve(query, sections, index, semantic, tuple(re.findall(r"[A-Za-z0-9_]{2,128}", registered_skill.name)))
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
        summary_ranges: list[tuple[int, int]] = []
        metadata_budget = min(1024, budget_bytes // 4)
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
                summary_ranges.append((packet_start, consumed))
                if summary["status"] not in {"available", "completed", "ok"}:
                    result["status"] = "unavailable"
                    reason = redact_output_text(str(response.get("reason", "observer_analysis_unavailable")))[:500]
                    summary["reason"] = reason
                    result["reason"] = reason
                    if not result["evidence"]:
                        result["evidence"] = [s.evidence(excerpt=True, metadata_budget_bytes=metadata_budget) for s in selected[packet_start:consumed][:2]]
                    consumed = packet_start
                    break
                for section in selected[offset:consumed]:
                    if section.id in citations and len(result["evidence"]) < 8:
                        result["evidence"].append(section.evidence(excerpt=True, metadata_budget_bytes=metadata_budget))
                if consumed >= len(selected) or len(_encoded(result)) > budget_bytes - 1024:
                    break
        else:
            for section in selected[offset:]:
                item = section.evidence(metadata_budget_bytes=metadata_budget)
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
        if _digest([PARSER_VERSION, self._inventory(skill)]) != fingerprint:
            raise ValueError("skill_corpus_changed_during_context")
        # Trim transport metadata before evidence. A pruned packet/section must
        # remain reachable through continuation rather than silently consumed.
        def update_progress() -> None:
            result["coverage"]["returned_sections"] = len(result["evidence"])
            result["coverage"]["analyzed_sections"] = consumed - offset if route == "machine" else 0
            result["continuation_cursor"] = None
            if consumed < len(selected):
                result["continuation_cursor"] = json.dumps({"identity": identity, "skill": skill, "query": _digest(query), "offset": consumed}, separators=(",", ":"))

        update_progress()
        if len(_encoded(result)) > budget_bytes:
            result["relationships"] = []
            result["unresolved_relationships"] = []
        while len(_encoded(result)) > budget_bytes and result["selection_warnings"]:
            result["selection_warnings"].pop()
        while len(_encoded(result)) > budget_bytes and result["summaries"]:
            result["summaries"].pop()
            start, _ = summary_ranges.pop()
            consumed = min(consumed, start)
            update_progress()
        while len(_encoded(result)) > budget_bytes and result["evidence"]:
            result["evidence"].pop()
            if route != "machine":
                consumed -= 1
            update_progress()
        return result
