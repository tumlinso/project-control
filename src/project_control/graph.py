"""Deterministic request-local project graph and subject resolver."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from typing import Any

from .models import ProjectSnapshot
from .reconcile import ReconciledProject


STOP_WORDS = {
    "a", "an", "and", "architecture", "current", "for", "from", "in", "of", "on",
    "performance", "project", "relevant", "the", "to", "work",
}


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def tokens(value: str) -> set[str]:
    return {item for item in normalize(value).split() if item not in STOP_WORDS}


class ProjectGraph:
    def __init__(self, snapshot: ProjectSnapshot, reconciled: ReconciledProject):
        self.snapshot = snapshot
        self.reconciled = reconciled
        self.entities: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._build()

    @staticmethod
    def key(kind: str, entity_id: str) -> str:
        return f"{kind}:{entity_id}"

    def _add_entity(self, kind: str, entity_id: object, record: dict[str, Any], *, title: str | None = None, aliases: list[str] | None = None) -> str:
        value = str(entity_id)
        key = self.key(kind, value)
        names = [value, title or "", *(aliases or [])]
        for field in ("title", "name", "objective", "path", "symbol", "rule", "summary"):
            if isinstance(record.get(field), str):
                names.append(str(record[field]))
        self.entities[key] = {
            "key": key,
            "type": kind,
            "id": value,
            "title": title or record.get("title") or record.get("name") or value,
            "aliases": list(dict.fromkeys(item for item in names if item)),
            "record": record,
            "relevance": record.get("relevance") or record.get("current_relevance") or "unknown",
        }
        return key

    def _edge(self, source: str, target: str, relation: str, basis: str, confidence: str = "high") -> None:
        if source not in self.entities or target not in self.entities:
            return
        edge = {"source": source, "target": target, "relation": relation, "basis": basis, "confidence": confidence}
        self.edges.append(edge)
        self.adjacency[source].append(edge)
        reverse = {**edge, "source": target, "target": source, "relation": f"reverse:{relation}"}
        self.adjacency[target].append(reverse)

    def _build(self) -> None:
        for task_id, item in self.reconciled.tasks.items():
            self._add_entity("task", task_id, item, title=str(item.get("title") or task_id))
        for item in self.reconciled.programs:
            key = self._add_entity("program", item.get("id"), item)
            for task_id in item.get("task_ids", []):
                self._edge(key, self.key("task", str(task_id)), "contains", "todo program membership")

        raw_tables = self.snapshot.todo_tables
        table_types = {
            "interfaces": "interface", "decisions": "decision", "invariants": "invariant",
            "handoffs": "handoff", "child_executions": "child_execution", "evidence": "evidence",
        }
        for table, kind in table_types.items():
            for index, item in enumerate(raw_tables.get(table, [])):
                entity_id = item.get("id") or item.get("interface_id") or item.get("decision_id") or f"{table}-{index}"
                self._add_entity(kind, entity_id, item)
        for item in self.reconciled.checkpoints:
            self._add_entity("checkpoint", item.get("id"), item)
        for item in self.reconciled.gates:
            self._add_entity("gate", item.get("id"), item)

        for item in raw_tables.get("task_artifacts", []):
            artifact_id = f"{item.get('task_id')}:{item.get('path')}"
            artifact = self._add_entity("artifact", artifact_id, item, title=str(item.get("path") or artifact_id))
            path = self._add_entity("path", item.get("path"), {"path": item.get("path"), "relevance": "reference"})
            self._edge(artifact, path, "located_at", "registered todo artifact")
            self._edge(self.key("task", str(item.get("task_id"))), artifact, "produces", "registered todo artifact")
        for item in raw_tables.get("ownership_scopes", []):
            path = self._add_entity("path", item.get("path"), {"path": item.get("path"), "mode": item.get("mode"), "relevance": "reference"})
            self._edge(self.key("task", str(item.get("task_id"))), path, f"scope:{item.get('mode')}", "todo ownership scope")

        for task_id, item in self.reconciled.tasks.items():
            if item.get("parent_id"):
                self._edge(self.key("task", task_id), self.key("task", str(item["parent_id"])), "child_of", "todo parent_id")
        for item in raw_tables.get("task_dependencies", []):
            source = self.key("task", str(item.get("task_id")))
            for field, kind in (
                ("prerequisite_task_id", "task"), ("checkpoint_id", "checkpoint"),
                ("interface_id", "interface"), ("decision_id", "decision"),
            ):
                if item.get(field):
                    self._edge(source, self.key(kind, str(item[field])), "depends_on", f"todo task_dependencies.{field}")
        for item in raw_tables.get("interfaces", []):
            self._edge(self.key("task", str(item.get("owner_task_id"))), self.key("interface", str(item.get("id"))), "owns", "todo interface owner")
            contract_paths = item.get("contract_paths", item.get("contract_paths_json", []))
            if isinstance(contract_paths, str):
                try:
                    contract_paths = json.loads(contract_paths)
                except json.JSONDecodeError:
                    contract_paths = []
            for path_value in contract_paths if isinstance(contract_paths, list) else []:
                path = self._add_entity("path", path_value, {"path": path_value, "relevance": "reference"})
                self._edge(self.key("interface", str(item.get("id"))), path, "contract_path", "todo interface contract")
        for item in raw_tables.get("interface_consumers", []):
            self._edge(self.key("task", str(item.get("task_id"))), self.key("interface", str(item.get("interface_id"))), "consumes", "todo interface consumer")
        for item in self.reconciled.checkpoints:
            self._edge(self.key("task", str(item.get("task_id"))), self.key("checkpoint", str(item.get("id"))), "reaches", "todo checkpoint owner")
        for item in self.reconciled.gates:
            self._edge(self.key("task", str(item.get("task_id"))), self.key("gate", str(item.get("id"))), "validated_by", "todo gate owner")
            if item.get("checkpoint_id"):
                self._edge(self.key("checkpoint", str(item.get("checkpoint_id"))), self.key("gate", str(item.get("id"))), "validated_by", "todo checkpoint gate")
        for item in raw_tables.get("task_invariants", []):
            self._edge(self.key("task", str(item.get("task_id"))), self.key("invariant", str(item.get("invariant_id"))), "constrained_by", "todo task invariant")
        for kind in ("handoff", "child_execution"):
            for entity in [value for value in self.entities.values() if value["type"] == kind]:
                task_id = entity["record"].get("task_id") or entity["record"].get("parent_task_id")
                if task_id:
                    self._edge(self.key("task", str(task_id)), entity["key"], "has_worker_result", f"todo {kind}")

        for item in self.reconciled.performance["campaigns"]:
            key = self._add_entity("cuda_campaign", item.get("id") or item.get("campaign_id"), item)
            for task_id in item.get("task_ids", []):
                self._edge(key, self.key("task", str(task_id)), "linked_task", "CUDA campaign task_ids")
            for path_value in item.get("paths", []):
                path = self._add_entity("path", path_value, {"path": path_value, "relevance": item.get("relevance")})
                self._edge(key, path, "measures_path", "CUDA campaign path")
            for symbol in item.get("symbols", []):
                symbol_key = self._add_entity("symbol", symbol, {"symbol": symbol, "relevance": item.get("relevance")})
                self._edge(key, symbol_key, "measures_symbol", "CUDA campaign symbol")
        for item in [*self.reconciled.performance["current_evidence"], *self.reconciled.performance["historical_evidence"]]:
            entity_id = item.get("id") or item.get("fact_id") or item.get("job_id")
            key = self._add_entity("cuda_result", entity_id, item)
            campaign_id = item.get("campaign_id")
            if campaign_id:
                self._edge(key, self.key("cuda_campaign", str(campaign_id)), "result_of", "CUDA result campaign")
            for task_id in item.get("linked_task_ids", []):
                self._edge(key, self.key("task", str(task_id)), "linked_task", "CUDA result task")
        for alias, identity in self.snapshot.repositories.items():
            self._add_entity("git_commit", f"{alias}:{identity.commit}", {
                "repository": alias,
                "commit": identity.commit,
                "dirty": identity.dirty,
                "working_tree_fingerprint": identity.working_tree_fingerprint,
                "relevance": "current",
            })

    def resolve(self, subject: str, *, expected_types: set[str] | None = None) -> dict[str, Any]:
        pool = [item for item in self.entities.values() if not expected_types or item["type"] in expected_types]
        wanted = normalize(subject)
        exact_id = [item for item in pool if normalize(item["id"]) == wanted]
        if len(exact_id) == 1:
            return {"status": "resolved", "entity": exact_id[0], "reason": "exact_entity_id", "candidates": []}
        exact_name = [item for item in pool if any(normalize(alias) == wanted for alias in item["aliases"])]
        if len(exact_name) == 1:
            return {"status": "resolved", "entity": exact_name[0], "reason": "exact_normalized_name", "candidates": []}
        if len(exact_id) > 1 or len(exact_name) > 1:
            choices = exact_id or exact_name
            return {"status": "ambiguous", "entity": None, "reason": "multiple_exact_matches", "candidates": [self._candidate(item, 1.0) for item in choices[:10]]}

        wanted_tokens = tokens(subject)
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in pool:
            item_tokens = set().union(*(tokens(alias) for alias in item["aliases"]))
            if not wanted_tokens or not item_tokens:
                continue
            common = wanted_tokens & item_tokens
            if not common:
                continue
            score = len(common) / min(len(wanted_tokens), len(item_tokens))
            if item["type"] == "program":
                score += 0.08
            if score >= 0.5 or len(common) >= 2:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["type"], pair[1]["id"]))
        if not scored:
            return {"status": "not_found", "entity": None, "reason": "no_deterministic_subject_match", "candidates": []}
        top = scored[0][0]
        leaders = [item for score, item in scored if abs(score - top) < 1e-9]
        candidates = [self._candidate(item, score) for score, item in scored[:10]]
        if len(leaders) != 1:
            return {"status": "ambiguous", "entity": None, "reason": "equal_bounded_token_overlap", "candidates": candidates}
        return {"status": "resolved", "entity": scored[0][1], "reason": "bounded_normalized_token_overlap", "candidates": candidates[1:]}

    @staticmethod
    def _candidate(item: dict[str, Any], score: float) -> dict[str, Any]:
        return {"type": item["type"], "id": item["id"], "title": item["title"], "score": round(score, 3)}

    def related(self, key: str, *, max_hops: int = 2, max_items: int = 60) -> list[dict[str, Any]]:
        queue = deque([(key, 0)])
        seen = {key}
        result = []
        while queue and len(result) < max_items:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for edge in sorted(self.adjacency.get(current, []), key=lambda item: (item["relation"], item["target"])):
                target = edge["target"]
                if target in seen:
                    continue
                seen.add(target)
                entity = self.entities[target]
                result.append({
                    "type": entity["type"], "id": entity["id"], "title": entity["title"],
                    "relevance": entity["relevance"], "relation": edge["relation"],
                    "basis": edge["basis"], "confidence": edge["confidence"], "record": entity["record"],
                })
                queue.append((target, depth + 1))
                if len(result) >= max_items:
                    break
        return result
