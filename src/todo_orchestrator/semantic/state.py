"""Effective tasks, checkpoints, gates, programs, and contradictions."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .lifecycle import normalize_task


def _ancestors(task_id: str, parents: dict[str, str | None]) -> list[str]:
    chain: list[str] = []
    seen = {task_id}
    current = parents.get(task_id)
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = parents.get(current)
    return chain


def _heuristic_program(task_id: str) -> str | None:
    match = re.match(r"^(.+?)-\d+(?:\D.*)?$", task_id)
    return match.group(1) if match else None


def _current_relations(conn, tasks: dict[str, dict[str, object]]) -> tuple[set[str], set[str], set[str]]:
    current = {task_id for task_id, task in tasks.items() if not task["terminal"]}
    checkpoints: set[str] = set()
    interfaces: set[str] = set()
    gates: set[str] = set()
    for row in conn.execute("SELECT * FROM task_dependencies ORDER BY id"):
        if row["task_id"] not in current:
            continue
        if row["checkpoint_id"]:
            checkpoints.add(str(row["checkpoint_id"]))
        if row["interface_id"]:
            interfaces.add(str(row["interface_id"]))
        if row["barrier_id"]:
            for requirement in conn.execute(
                "SELECT type,entity_id FROM barrier_requirements WHERE barrier_id=?", (row["barrier_id"],)
            ):
                if requirement["type"] == "checkpoint":
                    checkpoints.add(str(requirement["entity_id"]))
                elif requirement["type"] == "interface":
                    interfaces.add(str(requirement["entity_id"]))
                elif requirement["type"] == "gate":
                    gates.add(str(requirement["entity_id"]))
    for row in conn.execute("SELECT interface_id,task_id FROM interface_consumers"):
        if row["task_id"] in current:
            interfaces.add(str(row["interface_id"]))
    for row in conn.execute("SELECT checkpoint_id,interface_id FROM checkpoint_interfaces"):
        if row["interface_id"] in interfaces:
            checkpoints.add(str(row["checkpoint_id"]))
    for checkpoint_id in tuple(checkpoints):
        gates.update(
            str(row[0])
            for row in conn.execute("SELECT gate_id FROM checkpoint_gates WHERE checkpoint_id=?", (checkpoint_id,))
        )
    return checkpoints, interfaces, gates


def _checkpoint_record(row, owner: dict[str, object], current_refs: set[str]) -> dict[str, object]:
    raw = str(row["state"])
    referenced = str(row["id"]) in current_refs
    if raw == "reached":
        effective = "reached"
        attention = False
        reasons = ["raw_checkpoint_reached"]
    elif owner["terminal"] and referenced:
        effective = "inconsistent_current_dependency"
        attention = True
        reasons = ["terminal_owner", "required_by_current_task"]
    elif owner["terminal"]:
        effective = "historical_stale"
        attention = False
        reasons = ["terminal_owner", "no_current_consumer"]
    elif raw == "revoked":
        effective = "current_revoked"
        attention = True
        reasons = ["revoked_for_nonterminal_owner"]
    else:
        effective = "current_pending"
        attention = True
        reasons = ["pending_for_nonterminal_owner"]
    return {
        "id": row["id"], "task_id": row["task_id"], "title": row["title"],
        "raw_state": raw, "effective_state": effective, "attention_eligible": attention,
        "owner_task_effective_state": owner["effective_state"], "reason_codes": reasons,
        "reached_at": row["reached_at"], "revoked_at": row["revoked_at"],
    }


def _gate_record(row, owner: dict[str, object], current_refs: set[str], checkpoint_current: bool) -> dict[str, object]:
    raw_status = str(row["status"])
    raw_valid = bool(row["valid"])
    current_dependency = str(row["id"]) in current_refs or checkpoint_current
    terminal_owner = bool(owner["terminal"])
    if terminal_owner and current_dependency and not raw_valid:
        effective = "inconsistent_current_dependency"
        attention = True
        reasons = ["terminal_owner", "invalid_gate_required_by_current_task"]
    elif terminal_owner:
        effective = "historical_valid" if raw_valid else "historical_invalid"
        attention = False
        reasons = ["terminal_owner", "historical_gate_state"]
    elif raw_valid and raw_status in {"passed", "evaluated_not_promoted"}:
        effective = "current_valid"
        attention = False
        reasons = ["valid_for_nonterminal_owner"]
    elif raw_status in {"failed", "evaluated_not_promoted"}:
        effective = "current_failed"
        attention = True
        reasons = ["failed_for_nonterminal_owner"]
    else:
        effective = "current_pending"
        attention = True
        reasons = ["pending_for_nonterminal_owner"]
    return {
        "id": row["id"], "task_id": row["task_id"], "checkpoint_id": row["checkpoint_id"],
        "type": row["type"], "required": bool(row["required"]),
        "raw_status": raw_status, "raw_valid": raw_valid, "effective_state": effective,
        "attention_eligible": attention, "owner_task_effective_state": owner["effective_state"],
        "reason_codes": reasons, "last_run_at": row["last_run_at"],
    }


def semantic_state(
    conn, project: dict[str, object], *, task_id: str | None = None, prefix: str | None = None,
    program: str | None = None, current_only: bool = False,
) -> dict[str, object]:
    tasks = {str(row["id"]): normalize_task(conn, row) for row in conn.execute("SELECT * FROM tasks ORDER BY id")}
    parents = {task_id: task["parent_id"] for task_id, task in tasks.items()}
    parent_ids = {str(value) for value in parents.values() if value}
    for current_id, task in tasks.items():
        chain = _ancestors(current_id, parents)
        task["ancestor_chain"] = chain
        task["program_root_id"] = chain[-1] if chain else current_id
        explicit_root = current_id in parent_ids
        task["heuristic_program_id"] = None if chain or explicit_root else _heuristic_program(current_id)
        task["program_basis"] = "parent_hierarchy" if chain or explicit_root else ("task_id_prefix_heuristic" if task["heuristic_program_id"] else "self")
        task["dependencies"] = [
            {key: row[key] for key in ("type", "prerequisite_task_id", "checkpoint_id", "interface_id", "barrier_id", "decision_id") if row[key] is not None}
            for row in conn.execute("SELECT * FROM task_dependencies WHERE task_id=? ORDER BY id", (current_id,))
        ]
        task["scopes"] = [dict(row) for row in conn.execute("SELECT mode,path FROM ownership_scopes WHERE task_id=? ORDER BY mode,path", (current_id,))]
        task["artifacts"] = [dict(row) for row in conn.execute("SELECT kind,path,content_hash FROM task_artifacts WHERE task_id=? ORDER BY kind,path", (current_id,))]

    current_checkpoint_refs, _, current_gate_refs = _current_relations(conn, tasks)
    checkpoint_records: dict[str, dict[str, object]] = {}
    for row in conn.execute("SELECT * FROM checkpoints ORDER BY id"):
        owner = tasks[str(row["task_id"])]
        checkpoint_records[str(row["id"])] = _checkpoint_record(row, owner, current_checkpoint_refs)
    gate_records: dict[str, dict[str, object]] = {}
    for row in conn.execute("SELECT * FROM gates ORDER BY id"):
        owner_id = row["task_id"]
        if owner_id is None and row["checkpoint_id"] in checkpoint_records:
            owner_id = checkpoint_records[str(row["checkpoint_id"])]["task_id"]
        owner = tasks[str(owner_id)] if owner_id in tasks else {
            "terminal": False, "effective_state": "planned",
        }
        checkpoint_current = bool(
            row["checkpoint_id"] and checkpoint_records.get(str(row["checkpoint_id"]), {}).get("attention_eligible")
        )
        gate_records[str(row["id"])] = _gate_record(row, owner, current_gate_refs, checkpoint_current)

    contradictions: list[dict[str, object]] = []
    for item in tasks.values():
        if item["terminal"] and item["active_claim"] and item["active_claim"]["state"] == "active":
            contradictions.append({"code": "active_claim_on_terminal_task", "task_id": item["id"]})
        if item["terminal"] and item["authoritative_ready"]:
            contradictions.append({"code": "ready_entry_for_frontier_ineligible_task", "task_id": item["id"]})
        if not item["terminal"]:
            for dependency in item["dependencies"]:
                prerequisite = dependency.get("prerequisite_task_id")
                if prerequisite and tasks.get(str(prerequisite), {}).get("effective_state") == "superseded":
                    contradictions.append({"code": "current_dependency_on_superseded_task", "task_id": item["id"], "dependency_id": prerequisite})
    for item in checkpoint_records.values():
        if item["effective_state"] == "inconsistent_current_dependency":
            contradictions.append({"code": "current_dependency_on_stale_checkpoint", "checkpoint_id": item["id"], "owner_task_id": item["task_id"]})
    for item in gate_records.values():
        if item["effective_state"] == "inconsistent_current_dependency":
            contradictions.append({"code": "current_dependency_on_invalid_historical_gate", "gate_id": item["id"], "owner_task_id": item["task_id"]})

    program_members: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in tasks.values():
        key = str(item["program_root_id"] if item["program_basis"] == "parent_hierarchy" else (item["heuristic_program_id"] or item["program_root_id"]))
        program_members[key].append(item)
    programs = []
    for key, members in sorted(program_members.items()):
        counts = Counter(str(item["effective_state"]) for item in members)
        programs.append({
            "id": key,
            "basis": "parent_hierarchy" if any(item["program_basis"] == "parent_hierarchy" for item in members) else members[0]["program_basis"],
            "task_ids": sorted(str(item["id"]) for item in members),
            "effective_state_counts": dict(sorted(counts.items())),
            "has_current_work": any(not item["terminal"] for item in members),
            "complete": bool(members) and all(item["terminal"] for item in members) and not any(item["effective_state"] in {"failed", "canceled"} for item in members),
        })

    def program_key(item: dict[str, object]) -> str:
        return str(item["program_root_id"] if item["program_basis"] == "parent_hierarchy" else (item["heuristic_program_id"] or item["program_root_id"]))

    selected_ids = set(tasks)
    if current_only:
        current_ids = {item_id for item_id, item in tasks.items() if not item["terminal"]}
        current_programs = {program_key(tasks[item_id]) for item_id in current_ids}
        if not current_programs:
            completed = [item for item in tasks.values() if item["effective_state"] == "done" and item["current_program_eligible"]]
            if completed:
                # Revision is the append-only semantic chronology. Wall-clock
                # timestamps can move backwards, be imported, or be repaired,
                # so they are only a deterministic tie-breaker.
                latest = max(
                    completed,
                    key=lambda item: (
                        int(item.get("completion_revision") or item.get("revision", 0)),
                        str(item.get("updated_at", "")),
                        str(item["id"]),
                    ),
                )
                current_programs.add(program_key(latest))
        selected_ids = {
            item_id for item_id, item in tasks.items()
            if program_key(item) in current_programs and item["current_program_eligible"]
        }
    if task_id:
        selected_ids &= {task_id}
    if prefix:
        selected_ids = {item for item in selected_ids if item.startswith(prefix)}
    if program:
        selected_ids = {
            item for item in selected_ids
            if tasks[item]["program_root_id"] == program or tasks[item]["heuristic_program_id"] == program
        }
    selected_checkpoints = {
        item_id: item for item_id, item in checkpoint_records.items()
        if item["task_id"] in selected_ids or item_id in current_checkpoint_refs
    }
    selected_gates = {
        item_id: item for item_id, item in gate_records.items()
        if item.get("task_id") in selected_ids or item_id in current_gate_refs or item.get("checkpoint_id") in selected_checkpoints
    }
    selected_program_keys = {program_key(tasks[item_id]) for item_id in selected_ids}
    selected_programs = [item for item in programs if str(item["id"]) in selected_program_keys]
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0])
    return {
        "revision": revision,
        "project_uuid": project["project_uuid"],
        "tasks": [tasks[item] for item in sorted(selected_ids)],
        "checkpoints": [selected_checkpoints[item] for item in sorted(selected_checkpoints)],
        "gates": [selected_gates[item] for item in sorted(selected_gates)],
        "programs": selected_programs,
        "contradictions": contradictions,
        "historical_counts": {
            "tasks": sum(1 for item in tasks.values() if item["current_relevance"] in {"historical", "superseded"}),
            "checkpoints": sum(1 for item in checkpoint_records.values() if item["effective_state"] == "historical_stale"),
            "gates": sum(1 for item in gate_records.values() if str(item["effective_state"]).startswith("historical_")),
        },
        "filters": {"task": task_id, "prefix": prefix, "program": program, "current_only": current_only},
    }
