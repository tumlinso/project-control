"""Machine-readable v2 plan validation, diffing, scaffolding, and transactional upsert."""

from __future__ import annotations

import json
import copy
import sqlite3
from collections import deque
from pathlib import Path
from typing import Any

from . import PLAN_SCHEMA_VERSION, SCHEMA_VERSION
from .db import Unchanged
from .config import utc_now
from .gates import validate_gate_spec
from .git_state import canonical_relative
from .graph import validate_acyclic
from .models import TodoError

TASK_KINDS = {"epic", "workstream", "task", "integration", "integration_task", "validation", "validation_task"}
PARALLEL_POLICIES = {"parallel_safe", "serial", "project_exclusive", "integration_exclusive"}
DEPENDENCY_TYPES = {"task", "checkpoint", "interface", "barrier", "decision"}
DISPOSITIONS = {"implemented", "validated", "evaluated_not_promoted", "no_change_required", "superseded", "failed"}


def load_plan(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TodoError("invalid_plan_json", str(exc)) from exc
    if not isinstance(data, dict):
        raise TodoError("invalid_plan", "Plan root must be a JSON object")
    return data


def validate_plan(data: dict[str, Any], repo_root: Path | None = None) -> dict[str, object]:
    errors: list[str] = []

    def validate_path(value: object, label: str, *, allow_root: bool = False) -> None:
        try:
            if allow_root and str(value) == ".":
                return
            if repo_root:
                canonical_relative(repo_root, str(value))
            elif Path(str(value)).is_absolute() or ".." in Path(str(value)).parts:
                raise ValueError(value)
        except Exception:
            errors.append(f"{label} has unsafe repository path: {value}")
    plan_version = int(data.get("schema_version", 0))
    if plan_version not in {SCHEMA_VERSION, PLAN_SCHEMA_VERSION}:
        errors.append(f"schema_version must be {SCHEMA_VERSION} or {PLAN_SCHEMA_VERSION}")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        errors.append("tasks must be an array")
        tasks = []
    ids: list[str] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not task.get("id") or not task.get("title"):
            errors.append(f"tasks[{index}] requires id and title")
            continue
        task_id = str(task["id"])
        ids.append(task_id)
        if task.get("kind", "task") not in TASK_KINDS:
            errors.append(f"task {task_id} has unsupported kind")
        if task.get("parallel_policy", "serial") not in PARALLEL_POLICIES:
            errors.append(f"task {task_id} has unsupported parallel_policy")
        scope = task.get("scope", {})
        for field in ("exclusive_paths", "read_paths", "forbidden_paths"):
            for value in scope.get(field, []):
                validate_path(value, f"task {task_id} {field}")
        for dependency in task.get("depends_on", []):
            if dependency.get("type") not in DEPENDENCY_TYPES:
                errors.append(f"task {task_id} has unsupported dependency type {dependency.get('type')}")
            if dependency.get("type") == "decision" and dependency.get("operator", "equals") not in {"equals", "in"}:
                errors.append(f"task {task_id} has unsafe decision operator")
        allowed = task.get("result_policy", {}).get("allowed_dispositions", [])
        invalid = set(allowed) - DISPOSITIONS
        if invalid:
            errors.append(f"task {task_id} has invalid dispositions: {sorted(invalid)}")
    duplicates = sorted({task_id for task_id in ids if ids.count(task_id) > 1})
    if duplicates:
        errors.append(f"duplicate task IDs: {duplicates}")
    task_ids = set(ids)
    interfaces = data.get("interfaces", []) if isinstance(data.get("interfaces", []), list) else []
    barriers = data.get("barriers", []) if isinstance(data.get("barriers", []), list) else []
    decisions = data.get("decisions", []) if isinstance(data.get("decisions", []), list) else []
    invariants = data.get("invariants", []) if isinstance(data.get("invariants", []), list) else []
    locks = data.get("locks", []) if isinstance(data.get("locks", []), list) else []
    resource_classes = data.get("resource_classes", []) if isinstance(data.get("resource_classes", []), list) else []
    checkpoint_ids = [str(item["id"]) for task in tasks if isinstance(task, dict) for item in task.get("checkpoints", []) if isinstance(item, dict) and item.get("id")]
    gate_ids = [str(item["id"]) for task in tasks if isinstance(task, dict) for item in task.get("gates", []) if isinstance(item, dict) and item.get("id")]
    interface_ids = [str(item["id"]) for item in interfaces if isinstance(item, dict) and item.get("id")]
    barrier_ids = [str(item["id"]) for item in barriers if isinstance(item, dict) and item.get("id")]
    decision_ids = [str(item["id"]) for item in decisions if isinstance(item, dict) and item.get("id")]
    invariant_ids = [str(item["id"]) for item in invariants if isinstance(item, dict) and item.get("id")]
    lock_names = [str(item["name"]) for item in locks if isinstance(item, dict) and item.get("name")]
    resource_class_ids = [str(item["id"]) for item in resource_classes if isinstance(item, dict) and item.get("id")]
    resource_instance_ids = [str(instance["id"]) for item in resource_classes if isinstance(item, dict) for instance in item.get("instances", []) if isinstance(instance, dict) and instance.get("id")]
    runs = data.get("runs", []) if isinstance(data.get("runs", []), list) else []
    if plan_version == SCHEMA_VERSION and runs:
        errors.append("schema v2 plans cannot declare first-class runs")
    run_ids: set[str] = set()
    lane_ids: set[str] = set()
    assigned_tasks: set[tuple[str, str]] = set()
    for run in runs:
        if not isinstance(run, dict) or not run.get("id") or not isinstance(run.get("charter"), dict):
            errors.append("schema v3 runs require id and charter")
            continue
        run_id = str(run["id"])
        if run_id in run_ids:
            errors.append(f"duplicate run ID: {run_id}")
        run_ids.add(run_id)
        if run.get("root_task_id") and run["root_task_id"] not in task_ids:
            errors.append(f"run {run_id} has unknown root task {run.get('root_task_id')}")
        local_lanes: set[str] = set()
        for lane in run.get("lanes", []):
            if not isinstance(lane, dict) or not lane.get("id"):
                errors.append(f"run {run_id} has a lane without id")
                continue
            lane_id = str(lane["id"])
            if lane_id in lane_ids:
                errors.append(f"duplicate lane ID: {lane_id}")
            lane_ids.add(lane_id)
            local_lanes.add(lane_id)
            if lane.get("role") not in {"coordinator", "implementer", "validator", "integrator", "specialist"}:
                errors.append(f"lane {lane_id} has unsupported role")
            workspace = lane.get("workspace", {})
            if workspace and workspace.get("mode", "exclusive") not in {"exclusive", "read_shared", "isolated_merge", "contract_split"}:
                errors.append(f"lane {lane_id} has unsupported workspace mode")
            if workspace.get("integration_task_id") and workspace["integration_task_id"] not in task_ids:
                errors.append(f"lane {lane_id} has unknown integration task")
            for task in lane.get("tasks", []):
                if task not in task_ids:
                    errors.append(f"lane {lane_id} has unknown task {task}")
                key = (run_id, str(task))
                if key in assigned_tasks:
                    errors.append(f"task {task} is assigned to multiple lanes in run {run_id}")
                assigned_tasks.add(key)
                if any(interface.get("owner_task_id") == task for interface in interfaces if isinstance(interface, dict)):
                    from .workflow.roles import ROLE_ACTIONS
                    if "publish_interface" not in ROLE_ACTIONS.get(lane.get("role"), ()):
                        errors.append(f"lane {lane_id} role cannot publish interfaces owned by task {task}")
        roots = [lane for lane in run.get("lanes", []) if isinstance(lane, dict) and not lane.get("parent_lane_id")]
        if len(roots) != 1:
            errors.append(f"run {run_id} must have exactly one root lane")
        for lane in run.get("lanes", []):
            if isinstance(lane, dict) and lane.get("parent_lane_id") and lane["parent_lane_id"] not in local_lanes:
                errors.append(f"lane {lane.get('id')} has unknown parent {lane.get('parent_lane_id')}")
        for rendezvous in run.get("rendezvous", []):
            if rendezvous.get("join_task_id") not in task_ids or rendezvous.get("barrier_id") not in barrier_ids:
                errors.append(f"run {run_id} rendezvous {rendezvous.get('id')} has unknown join task or barrier")
            participants = set(rendezvous.get("participants", []))
            if not participants or not participants <= local_lanes:
                errors.append(f"run {run_id} rendezvous {rendezvous.get('id')} has invalid participants")

    for label, values in (
        ("checkpoint", checkpoint_ids), ("gate", gate_ids), ("interface", interface_ids),
        ("barrier", barrier_ids), ("decision", decision_ids), ("invariant", invariant_ids),
        ("lock", lock_names), ("resource class", resource_class_ids), ("resource instance", resource_instance_ids),
    ):
        repeated = sorted({value for value in values if values.count(value) > 1})
        if repeated:
            errors.append(f"duplicate {label} IDs: {repeated}")

    known_checkpoints, known_interfaces = set(checkpoint_ids), set(interface_ids)
    known_barriers, known_decisions = set(barrier_ids), set(decision_ids)
    known_invariants, known_locks = set(invariant_ids), set(lock_names)
    known_resource_classes, known_resource_instances = set(resource_class_ids), set(resource_instance_ids)

    def selector_known(selector: object) -> bool:
        value = str(selector)
        return (value.endswith(":any") and value[:-4] in known_resource_classes) or value in known_resource_instances

    for interface in interfaces:
        if not isinstance(interface, dict) or not interface.get("id") or interface.get("owner_task_id") not in task_ids:
            errors.append(f"interface {interface.get('id') if isinstance(interface, dict) else '?'} requires a known owner_task_id")
        if isinstance(interface, dict):
            for value in interface.get("contract_paths", []):
                validate_path(value, f"interface {interface.get('id')} contract")
    for task in tasks:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        task_id = str(task["id"])
        for invariant_id in task.get("invariants", []):
            if invariant_id not in known_invariants:
                errors.append(f"task {task_id} references unknown invariant {invariant_id}")
        for lock_name in task.get("scope", {}).get("shared_locks", []):
            if lock_name not in known_locks:
                # Shared locks may be declared inline and are created on apply.
                known_locks.add(str(lock_name))
        for dependency in task.get("depends_on", []):
            kind = dependency.get("type")
            reference = {
                "task": (dependency.get("task_id"), task_ids),
                "checkpoint": (dependency.get("checkpoint_id"), known_checkpoints),
                "interface": (dependency.get("interface_id"), known_interfaces),
                "barrier": (dependency.get("barrier_id"), known_barriers),
                "decision": (dependency.get("decision_id"), known_decisions),
            }.get(kind)
            if reference and (not reference[0] or reference[0] not in reference[1]):
                errors.append(f"task {task_id} has unknown {kind} dependency {reference[0]}")
        for consumed in task.get("consumes_interfaces", []):
            if consumed.get("id") not in known_interfaces:
                errors.append(f"task {task_id} consumes unknown interface {consumed.get('id')}")
        for checkpoint in task.get("checkpoints", []):
            for published in checkpoint.get("publishes_interfaces", []):
                if published.get("id") not in known_interfaces:
                    errors.append(f"checkpoint {checkpoint.get('id')} publishes unknown interface {published.get('id')}")
        for gate in task.get("gates", []):
            errors.extend(validate_gate_spec(
                gate, repo_root, known_checkpoint_ids=known_checkpoints,
                known_resources=known_resource_classes | known_resource_instances,
            ))
        for request in task.get("resource_requests", []):
            if not selector_known(request.get("selector")):
                errors.append(f"task {task_id} references unknown resource selector {request.get('selector')}")
        for artifact in task.get("produced_artifacts", []):
            if artifact.get("path"):
                validate_path(artifact["path"], f"task {task_id} artifact")
    valid_requirement_types = {"task", "validation_task", "checkpoint", "interface", "gate"}
    requirement_sets = {"task": task_ids, "validation_task": task_ids, "checkpoint": known_checkpoints, "interface": known_interfaces, "gate": set(gate_ids)}
    for barrier in barriers:
        if not isinstance(barrier, dict) or not barrier.get("id"):
            errors.append("barriers require an id")
            continue
        if barrier.get("mode", "all") not in {"all", "quorum"}:
            errors.append(f"barrier {barrier['id']} has unsupported mode")
        requirements = barrier.get("requirements", [])
        if not requirements:
            errors.append(f"barrier {barrier['id']} requires at least one requirement")
        if barrier.get("mode") == "quorum" and not (isinstance(barrier.get("quorum"), int) and 1 <= barrier["quorum"] <= len(requirements)):
            errors.append(f"barrier {barrier['id']} has invalid quorum")
        for requirement in requirements:
            kind, entity_id = requirement.get("type"), requirement.get("id")
            if kind not in valid_requirement_types or entity_id not in requirement_sets.get(kind, set()):
                errors.append(f"barrier {barrier['id']} has unknown {kind} requirement {entity_id}")
    if not errors:
        try:
            validate_acyclic(tasks)
        except TodoError as exc:
            errors.append(exc.message)
    if errors:
        raise TodoError("plan_validation_failed", "Plan validation failed", details={"errors": errors})
    return {
        "valid": True,
        "task_count": len(tasks),
        "checkpoint_count": sum(len(task.get("checkpoints", [])) for task in tasks),
        "gate_count": sum(len(task.get("gates", [])) for task in tasks),
        "barrier_count": len(data.get("barriers", [])),
        "interface_count": len(data.get("interfaces", [])),
    }


def _task_order(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(task["id"]): task for task in tasks}
    pending = deque(sorted(by_id))
    ordered: list[dict[str, Any]] = []
    inserted: set[str] = set()
    while pending:
        task_id = pending.popleft()
        task = by_id[task_id]
        parent = task.get("parent_id")
        if parent and str(parent) not in inserted:
            pending.append(task_id)
            continue
        ordered.append(task)
        inserted.add(task_id)
    return ordered


def _clear_task_details(conn: sqlite3.Connection, task_id: str) -> None:
    for table in ("task_dependencies", "ownership_scopes", "task_locks", "task_invariants", "task_artifacts", "resource_requests"):
        conn.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))


def apply_plan(conn: sqlite3.Connection, data: dict[str, Any], repo_root: Path, revision: int, *, _comparison=False) -> dict[str, object]:
    validate_plan(data, repo_root)
    data = _preserve_omitted_task_fields(conn, data)
    if not _comparison and not _plan_changes(conn, data, repo_root, revision):
        return Unchanged({"status": "noop", "tasks_upserted": 0, "barriers": [], "workflow": {"runs": []}})
    now = utc_now()
    for lock in data.get("locks", []):
        conn.execute(
            "INSERT INTO named_locks(name,capacity,metadata_json) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET capacity=excluded.capacity,metadata_json=excluded.metadata_json",
            (lock["name"], int(lock.get("capacity", 1)), json.dumps(lock.get("metadata", {}), sort_keys=True)),
        )
    for decision in data.get("decisions", []):
        conn.execute(
            "INSERT INTO decisions(id,title,value_json,allowed_json,updated_at,revision) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,value_json=excluded.value_json,allowed_json=excluded.allowed_json,updated_at=excluded.updated_at,revision=excluded.revision",
            (
                decision["id"],
                decision.get("title", decision["id"]),
                json.dumps(decision.get("value")) if "value" in decision else None,
                json.dumps(decision.get("allowed", []), sort_keys=True),
                now,
                revision,
            ),
        )
    for invariant in data.get("invariants", []):
        conn.execute(
            "INSERT INTO invariants(id,rule,scope_json,severity,enforcement) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET rule=excluded.rule,scope_json=excluded.scope_json,severity=excluded.severity,enforcement=excluded.enforcement",
            (invariant["id"], invariant["rule"], json.dumps(invariant.get("scope", {}), sort_keys=True), invariant.get("severity", "error"), invariant.get("enforcement")),
        )
    tasks = _task_order(data.get("tasks", []))
    for task in tasks:
        task_id = str(task["id"])
        existing = conn.execute("SELECT created_at,status,result FROM tasks WHERE id=?", (task_id,)).fetchone()
        status = task.get("status", existing["status"] if existing else "planned")
        result = task.get("result", existing["result"] if existing else None)
        conn.execute(
            "INSERT INTO tasks(id,parent_id,kind,title,objective,status,priority,tags_json,parallel_policy,result,next_action,result_policy_json,notes,created_at,updated_at,version,revision) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?) "
            "ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id,kind=excluded.kind,title=excluded.title,objective=excluded.objective,status=excluded.status,priority=excluded.priority,tags_json=excluded.tags_json,parallel_policy=excluded.parallel_policy,result=excluded.result,next_action=excluded.next_action,result_policy_json=excluded.result_policy_json,notes=excluded.notes,updated_at=excluded.updated_at,version=tasks.version+1,revision=excluded.revision",
            (
                task_id,
                task.get("parent_id"),
                task.get("kind", "task"),
                task["title"],
                task.get("objective", ""),
                status,
                int(task.get("priority", 0)),
                json.dumps(task.get("tags", []), sort_keys=True),
                task.get("parallel_policy", "serial"),
                result,
                task.get("next_action", ""),
                json.dumps(task.get("result_policy", {}), sort_keys=True),
                task.get("notes", ""),
                existing["created_at"] if existing else now,
                now,
                revision,
            ),
        )
        _clear_task_details(conn, task_id)
        scope = task.get("scope", {})
        mapping = {"exclusive_paths": "exclusive", "read_paths": "read", "forbidden_paths": "forbidden"}
        for field, mode in mapping.items():
            for value in scope.get(field, []):
                conn.execute("INSERT INTO ownership_scopes(task_id,mode,path) VALUES(?,?,?)", (task_id, mode, canonical_relative(repo_root, str(value))))
        for lock_name in scope.get("shared_locks", []):
            conn.execute("INSERT OR IGNORE INTO named_locks(name,capacity,metadata_json) VALUES(?,1,'{}')", (lock_name,))
            conn.execute("INSERT INTO task_locks(task_id,lock_name,phase) VALUES(?,?,?)", (task_id, lock_name, "manual"))
        for lock in task.get("claim_locks", []):
            conn.execute("INSERT OR IGNORE INTO named_locks(name,capacity,metadata_json) VALUES(?,1,'{}')", (lock,))
            conn.execute("INSERT INTO task_locks(task_id,lock_name,phase) VALUES(?,?,?)", (task_id, lock, "claim"))
        for invariant_id in task.get("invariants", []):
            conn.execute("INSERT INTO task_invariants(task_id,invariant_id) VALUES(?,?)", (task_id, invariant_id))
        for artifact in task.get("produced_artifacts", []):
            conn.execute("INSERT INTO task_artifacts(task_id,kind,path) VALUES(?,?,?)", (task_id, artifact.get("kind", "artifact"), canonical_relative(repo_root, artifact["path"])))

    for interface in data.get("interfaces", []):
        paths = [canonical_relative(repo_root, value) for value in interface.get("contract_paths", [])]
        existing_interface = conn.execute("SELECT * FROM interfaces WHERE id=?", (interface["id"],)).fetchone()
        requested_state = str(interface.get("state", existing_interface["state"] if existing_interface else "draft"))
        # Plan reapplication is not an interface lifecycle operation. Once a
        # contract is frozen or revised, a historical/default draft declaration
        # must not silently revoke that authority.
        if existing_interface and existing_interface["state"] in {"frozen", "revised"} and requested_state == "draft":
            requested_state = str(existing_interface["state"])
        requested_version = str(interface.get("version", existing_interface["version"] if existing_interface else "0"))
        conn.execute(
            "INSERT INTO interfaces(id,owner_task_id,state,version,contract_paths_json,content_hash,frozen_at,revised_at,revision) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET owner_task_id=excluded.owner_task_id,state=excluded.state,version=excluded.version,contract_paths_json=excluded.contract_paths_json,revision=excluded.revision",
            (
                interface["id"], interface["owner_task_id"], requested_state, requested_version,
                json.dumps(paths), interface.get("content_hash", existing_interface["content_hash"] if existing_interface else None),
                interface.get("frozen_at", existing_interface["frozen_at"] if existing_interface else None),
                interface.get("revised_at", existing_interface["revised_at"] if existing_interface else None), revision,
            ),
        )
        conn.execute("DELETE FROM interface_consumers WHERE interface_id=?", (interface["id"],))

    for task in tasks:
        task_id = str(task["id"])
        for dependency in task.get("depends_on", []):
            kind = dependency["type"]
            condition = {key: value for key, value in dependency.items() if key in {"operator", "value", "state", "version", "dispositions"}}
            conn.execute(
                "INSERT INTO task_dependencies(task_id,type,prerequisite_task_id,checkpoint_id,interface_id,barrier_id,decision_id,condition_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    kind,
                    dependency.get("task_id") if kind == "task" else None,
                    dependency.get("checkpoint_id") if kind == "checkpoint" else None,
                    dependency.get("interface_id") if kind == "interface" else None,
                    dependency.get("barrier_id") if kind == "barrier" else None,
                    dependency.get("decision_id") if kind == "decision" else None,
                    json.dumps(condition, sort_keys=True),
                ),
            )
        for consumed in task.get("consumes_interfaces", []):
            conn.execute(
                "INSERT OR REPLACE INTO interface_consumers(interface_id,task_id,required_state,required_version) VALUES(?,?,?,?)",
                (consumed["id"], task_id, consumed.get("required_state", "frozen"), consumed.get("required_version")),
            )
        for checkpoint in task.get("checkpoints", []):
            existing_checkpoint = conn.execute(
                "SELECT state,reached_at,revoked_at FROM checkpoints WHERE id=?", (checkpoint["id"],)
            ).fetchone()
            checkpoint_state = checkpoint.get(
                "state", existing_checkpoint["state"] if existing_checkpoint else "pending"
            )
            reached_at = existing_checkpoint["reached_at"] if existing_checkpoint else None
            revoked_at = existing_checkpoint["revoked_at"] if existing_checkpoint else None
            if not existing_checkpoint or checkpoint_state != existing_checkpoint["state"]:
                if checkpoint_state == "reached":
                    reached_at, revoked_at = now, None
                elif checkpoint_state == "revoked":
                    revoked_at = now
                elif checkpoint_state == "pending":
                    reached_at, revoked_at = None, None
            conn.execute(
                "INSERT INTO checkpoints(id,task_id,title,state,metadata_json,reached_at,revoked_at,revision) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id,title=excluded.title,state=excluded.state,metadata_json=excluded.metadata_json,reached_at=excluded.reached_at,revoked_at=excluded.revoked_at,revision=excluded.revision",
                (checkpoint["id"], task_id, checkpoint.get("title", checkpoint["id"]), checkpoint_state, json.dumps(checkpoint.get("metadata", {}), sort_keys=True), reached_at, revoked_at, revision),
            )
            conn.execute("DELETE FROM checkpoint_interfaces WHERE checkpoint_id=?", (checkpoint["id"],))
            for published in checkpoint.get("publishes_interfaces", []):
                conn.execute("INSERT INTO checkpoint_interfaces(checkpoint_id,interface_id,version) VALUES(?,?,?)", (checkpoint["id"], published["id"], published.get("version")))
        for gate in task.get("gates", []):
            conn.execute(
                "INSERT INTO gates(id,task_id,checkpoint_id,type,config_json,required,status,valid,revision) VALUES(?,?,?,?,?,?,COALESCE((SELECT status FROM gates WHERE id=?),'pending'),COALESCE((SELECT valid FROM gates WHERE id=?),0),?) "
                "ON CONFLICT(id) DO UPDATE SET task_id=excluded.task_id,checkpoint_id=excluded.checkpoint_id,type=excluded.type,config_json=excluded.config_json,required=excluded.required,revision=excluded.revision",
                (gate["id"], task_id, gate.get("checkpoint_id"), gate["type"], json.dumps({key: value for key, value in gate.items() if key not in {"id", "type", "required", "checkpoint_id"}}, sort_keys=True), int(gate.get("required", True)), gate["id"], gate["id"], revision),
            )
            if gate.get("checkpoint_id"):
                conn.execute("INSERT OR REPLACE INTO checkpoint_gates(checkpoint_id,gate_id) VALUES(?,?)", (gate["checkpoint_id"], gate["id"]))
        for index, request in enumerate(task.get("resource_requests", [])):
            conn.execute(
                "INSERT INTO resource_requests(id,task_id,phase,selector,amount,mode,required) VALUES(?,?,?,?,?,?,?)",
                (request.get("id", f"{task_id}-resource-{index}"), task_id, request.get("phase", "manual"), request["selector"], int(request.get("amount", 1)), request.get("mode", "exclusive"), int(request.get("required", True))),
            )

    for barrier in data.get("barriers", []):
        conn.execute(
            "INSERT INTO barriers(id,title,mode,quorum,state,explanation,revision) VALUES(?,?,?,?,COALESCE((SELECT state FROM barriers WHERE id=?),'closed'),'pending reevaluation',?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,mode=excluded.mode,quorum=excluded.quorum,revision=excluded.revision",
            (barrier["id"], barrier.get("title", barrier["id"]), barrier.get("mode", "all"), barrier.get("quorum"), barrier["id"], revision),
        )
        conn.execute("DELETE FROM barrier_requirements WHERE barrier_id=?", (barrier["id"],))
        for requirement in barrier.get("requirements", []):
            conn.execute(
                "INSERT INTO barrier_requirements(barrier_id,type,entity_id,required_state,dispositions_json) VALUES(?,?,?,?,?)",
                (barrier["id"], requirement["type"], requirement["id"], requirement.get("state", "done"), json.dumps(requirement.get("dispositions", []), sort_keys=True)),
            )
    for resource_class in data.get("resource_classes", []):
        conn.execute(
            "INSERT INTO resource_classes(id,mode,metadata_json) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET mode=excluded.mode,metadata_json=excluded.metadata_json",
            (resource_class["id"], resource_class.get("mode", "exclusive"), json.dumps(resource_class.get("metadata", {}), sort_keys=True)),
        )
        for instance in resource_class.get("instances", []):
            conn.execute(
                "INSERT INTO resource_instances(id,class_id,capacity,hostname,metadata_json,enabled) VALUES(?,?,?,?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET capacity=excluded.capacity,hostname=excluded.hostname,metadata_json=excluded.metadata_json,enabled=1",
                (instance["id"], resource_class["id"], int(instance.get("capacity", 1)), instance.get("hostname"), json.dumps(instance.get("metadata", {}), sort_keys=True)),
            )
    from .graph import reevaluate_barriers

    barrier_changes = reevaluate_barriers(conn, revision)
    workflow = _apply_workflow_plan(conn, data, revision)
    barrier_changes.extend(reevaluate_barriers(conn, revision))
    return {"tasks_upserted": len(tasks), "barriers": barrier_changes, "workflow": workflow}


def _apply_workflow_plan(conn: sqlite3.Connection, data: dict[str, Any], revision: int) -> dict[str, object]:
    """Normalize v2 or apply explicit v3 first-class run declarations."""
    from .workflow.foundation import canonical_json, content_hash
    from .workflow.lanes import (
        create_lane_in_transaction,
        enqueue_tasks_in_transaction,
        reconcile_lane_task_order_in_transaction,
    )
    from .workflow.runs import create_run_in_transaction

    now = utc_now()
    runs = list(data.get("runs", []))
    compatibility = int(data.get("schema_version", SCHEMA_VERSION)) == SCHEMA_VERSION
    if compatibility and not conn.execute("SELECT 1 FROM workflow_runs LIMIT 1").fetchone():
        ordered = [str(task["id"]) for task in _task_order(data.get("tasks", [])) if task.get("kind", "task") != "epic"]
        root = next((str(task["id"]) for task in data.get("tasks", []) if task.get("kind") == "epic"), ordered[0] if ordered else None)
        runs = [{
            "id": "compat-v2",
            "root_task_id": root,
            "charter": {
                "objective": str(data.get("project", {}).get("name", "Legacy v2 project")),
                "boundaries": ["Compatibility run normalized from plan schema v2"],
                "invariants": [],
                "acceptance_conditions": [],
                "glossary": {"lane": "single serial compatibility lane"},
            },
            "lanes": [{"id": "compat-v2-main", "role": "implementer", "tasks": ordered, "workspace": {"mode": "exclusive"}}],
            "rendezvous": [],
        }]
    applied_runs: list[str] = []
    for run in runs:
        run_id = str(run["id"])
        create_run_in_transaction(
            conn,
            revision,
            run_id=run_id,
            charter=dict(run["charter"]),
            root_task_id=run.get("root_task_id"),
        )
        lanes = list(run.get("lanes", []))
        pending = {str(lane["id"]): lane for lane in lanes}
        while pending:
            progressed = False
            for lane_id, lane in list(pending.items()):
                parent = lane.get("parent_lane_id")
                if parent and parent in pending:
                    continue
                workspace = dict(lane.get("workspace", {}))
                create_lane_in_transaction(
                    conn,
                    revision,
                    run_id=run_id,
                    lane_id=lane_id,
                    parent_lane_id=parent,
                    role=str(lane.get("role", "implementer")),
                    workspace_mode=str(workspace.get("mode", "exclusive")),
                    allow_workspace_mode_update=int(data.get("schema_version", SCHEMA_VERSION)) == 3,
                )
                declared_task_ids = [str(value) for value in lane.get("tasks", [])]
                enqueue_tasks_in_transaction(conn, revision, lane_id=lane_id, task_ids=declared_task_ids)
                reconcile_lane_task_order_in_transaction(
                    conn,
                    revision,
                    lane_id=lane_id,
                    task_ids=declared_task_ids,
                )
                conn.execute(
                    "UPDATE workflow_lane_tasks SET state=CASE "
                    "WHEN task_id IN (SELECT id FROM tasks WHERE status='done') THEN 'completed' "
                    "WHEN task_id IN (SELECT id FROM tasks WHERE status IN ('cancelled','superseded','stale')) THEN 'skipped' "
                    "ELSE state END,revision=? WHERE lane_id=?",
                    (revision, lane_id),
                )
                del pending[lane_id]
                progressed = True
            if not progressed:
                raise TodoError("workflow_lane_parent_cycle", f"Run {run_id} lane hierarchy contains a cycle")
        for rendezvous in run.get("rendezvous", []):
            rendezvous_id = str(rendezvous["id"])
            participants = [str(value) for value in rendezvous.get("participants", [])]
            required_roles = sorted({str(value) for value in rendezvous.get("required_roles", [])})
            conn.execute(
                "INSERT INTO workflow_rendezvous(id,run_id,barrier_id,mode,quorum,join_task_id,state,required_roles_json,created_at,revision) "
                "VALUES(?,?,?,?,?,?, 'open',?,?,?) ON CONFLICT(id) DO NOTHING",
                (rendezvous_id, run_id, rendezvous["barrier_id"], rendezvous.get("mode", "all"), rendezvous.get("quorum"), rendezvous["join_task_id"], canonical_json(required_roles), now, revision),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO workflow_rendezvous_participants(rendezvous_id,lane_id,producer,required) VALUES(?,?,?,1)",
                [(rendezvous_id, lane_id, int(lane_id in set(rendezvous.get("producers", [])))) for lane_id in participants],
            )
            conn.execute(
                "INSERT OR IGNORE INTO barrier_requirements(barrier_id,type,entity_id,required_state,dispositions_json) VALUES(?,'rendezvous',?,'satisfied','[]')",
                (rendezvous["barrier_id"], rendezvous_id),
            )
        declared_fragments = list(run.get("context_fragments", []))
        declared_kinds = {
            (str(fragment["kind"]), fragment.get("lane_id"), fragment.get("task_id"))
            for fragment in declared_fragments
        }
        if ("run_charter", None, None) not in declared_kinds:
            declared_fragments.append({"kind": "run_charter", "content": dict(run["charter"])})
        for lane in lanes:
            lane_id = str(lane["id"])
            if ("lane_brief", lane_id, None) not in declared_kinds:
                declared_fragments.append({
                    "kind": "lane_brief", "lane_id": lane_id,
                    "content": {
                        "role": str(lane.get("role", "implementer")),
                        "authority": "server_enforced_role_and_task_scope",
                        "ordered_tasks": [str(value) for value in lane.get("tasks", [])],
                        "interfaces": [], "rendezvous": [
                            str(item["id"]) for item in run.get("rendezvous", [])
                            if lane_id in item.get("participants", [])
                        ],
                        "workspace_mode": str(dict(lane.get("workspace", {})).get("mode", "exclusive")),
                        **{key: lane[key] for key in ("motivation", "desired_end_state", "conceptual_end_state", "rationale", "uncertainties", "risks", "delegated_choices", "delegated_judgment", "references") if key in lane},
                    },
                })
            for task_id in lane.get("tasks", []):
                task_id = str(task_id)
                if ("task_brief", lane_id, task_id) in declared_kinds:
                    continue
                task = next(item for item in data.get("tasks", []) if str(item["id"]) == task_id)
                task = dict(task)
                prior_brief = conn.execute(
                    "SELECT content_json FROM workflow_context_fragments WHERE run_id=? AND lane_id=? AND task_id=? AND kind='task_brief' ORDER BY version DESC LIMIT 1",
                    (run_id, lane_id, task_id),
                ).fetchone()
                prior_content = json.loads(prior_brief[0]) if prior_brief else {}
                for key in ('completion_contract', 'motivation', 'desired_end_state', 'conceptual_end_state', 'rationale', 'uncertainties', 'risks', 'delegated_choices', 'delegated_judgment', 'references'):
                    if key in prior_content:
                        task.setdefault(key, prior_content[key])
                prior_scope = prior_content.get('scope', {})
                current_scope = task.get('scope', {})
                scope_keys = set(prior_scope) | set(current_scope)
                if prior_brief and all(sorted(prior_scope.get(key, [])) == sorted(current_scope.get(key, [])) for key in scope_keys):
                    task['scope'] = prior_scope
                task_brief = {
                    "objective": str(task.get("objective", task.get("title", task_id))),
                    "next_action": str(task.get("next_action", task.get("objective", task.get("title", task_id)))),
                    "scope": dict(task.get("scope", {})),
                    "completion_contract": task.get("completion_contract"),
                    "tests": [gate.get("id") for gate in task.get("gates", [])],
                    "gates": [gate.get("id") for gate in task.get("gates", [])],
                    "forbidden_mutations": list(dict(task.get("scope", {})).get("forbidden_paths", [])),
                }
                if task.get("consumes_interfaces"):
                    task_brief["consumes_interfaces"] = [
                        dict(interface) for interface in task["consumes_interfaces"]
                    ]
                for key in ('tests', 'gates'):
                    if key in prior_content and sorted(prior_content[key]) == sorted(task_brief[key]):
                        task_brief[key] = prior_content[key]
                def consumer_meaning(values):
                    return sorted((str(value['id']), str(value.get('required_state', 'frozen')), value.get('required_version')) for value in values)
                if ('consumes_interfaces' in prior_content and consumer_meaning(prior_content['consumes_interfaces']) == consumer_meaning(task_brief.get('consumes_interfaces', []))):
                    task_brief['consumes_interfaces'] = prior_content['consumes_interfaces']
                task_brief.update({key: task[key] for key in ("motivation", "desired_end_state", "conceptual_end_state", "rationale", "uncertainties", "risks", "delegated_choices", "delegated_judgment", "references") if key in task})
                declared_fragments.append({
                    "kind": "task_brief", "lane_id": lane_id, "task_id": task_id,
                    "content": task_brief,
                })
        for fragment in declared_fragments:
            content = dict(fragment.get("content", {}))
            digest = content_hash(content)
            owner_lane = fragment.get("lane_id")
            owner_task = fragment.get("task_id")
            kind = str(fragment["kind"])
            owner_scope = {"run_id": run_id}
            if owner_lane:
                owner_scope["lane_id"] = str(owner_lane)
            if owner_task:
                owner_scope["task_id"] = str(owner_task)
            prior = conn.execute(
                "SELECT * FROM workflow_context_fragments WHERE run_id=? AND lane_id IS ? "
                "AND task_id IS ? AND kind=? ORDER BY version DESC LIMIT 1",
                (run_id, owner_lane, owner_task, kind),
            ).fetchone()
            if prior is not None and prior["content_hash"] == digest:
                continue
            requested_version = int(fragment.get("version", 1))
            version = max(requested_version, int(prior["version"]) + 1 if prior is not None else 1)
            default_identifier = f"{run_id}:{kind}:{owner_lane or '-'}:{owner_task or '-'}:{version}"
            identifier = str(fragment.get("id", default_identifier))
            if conn.execute(
                "SELECT 1 FROM workflow_context_fragments WHERE id=?", (identifier,)
            ).fetchone():
                identifier = default_identifier
            if conn.execute(
                "SELECT 1 FROM workflow_context_fragments WHERE id=?", (identifier,)
            ).fetchone():
                raise TodoError(
                    "context_fragment_identity_conflict",
                    f"Context fragment identity is already occupied: {identifier}",
                )
            conn.execute(
                "INSERT INTO workflow_context_fragments(id,run_id,lane_id,task_id,kind,owner_scope_json,version,content_json,content_hash,creation_revision,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (identifier, run_id, owner_lane, owner_task, kind, canonical_json(owner_scope), version, canonical_json(content), digest, revision, now),
            )
            conn.execute(
                "UPDATE workflow_context_fragments SET invalidated_at=?,invalidation_revision=?,superseded_by=? "
                "WHERE run_id=? AND lane_id IS ? AND task_id IS ? AND kind=? AND id<>? "
                "AND invalidated_at IS NULL",
                (now, revision, identifier, run_id, owner_lane, owner_task, kind, identifier),
            )
        applied_runs.append(run_id)
    return {"plan_schema_version": int(data.get("schema_version", SCHEMA_VERSION)), "compatibility": compatibility, "runs": applied_runs}


def _preserve_omitted_task_fields(conn, data):
    """Absent fields preserve existing declarations; explicit empties clear them."""
    data = copy.deepcopy(data)
    for task in data.get('tasks', []):
        row = conn.execute('SELECT * FROM tasks WHERE id=?', (str(task['id']),)).fetchone()
        if row is None:
            continue
        for key in ('parent_id', 'kind', 'objective', 'status', 'priority', 'parallel_policy', 'result', 'next_action', 'notes'):
            if key not in ('objective', 'next_action') or row[key]:
                task.setdefault(key, row[key])
        for key in ('tags', 'result_policy'):
            task.setdefault(key, json.loads(row[key + '_json']))
        existing_scope = {key: [r['path'] for r in conn.execute('SELECT path FROM ownership_scopes WHERE task_id=? AND mode=? ORDER BY path', (task['id'], mode))] for key, mode in (('exclusive_paths', 'exclusive'), ('read_paths', 'read'), ('forbidden_paths', 'forbidden'))}
        existing_scope['shared_locks'] = [r[0] for r in conn.execute("SELECT lock_name FROM task_locks WHERE task_id=? AND phase='manual' ORDER BY lock_name", (task['id'],))]
        task['scope'] = {**existing_scope, **task.get('scope', {})}
        task.setdefault('gates', [{**json.loads(r['config_json']), 'id': r['id'], 'type': r['type'], 'required': bool(r['required']), **({'checkpoint_id': r['checkpoint_id']} if r['checkpoint_id'] else {})} for r in conn.execute('SELECT * FROM gates WHERE task_id=? ORDER BY id', (task['id'],))])
        task.setdefault('consumes_interfaces', [{'id':r['interface_id'], 'required_state':r['required_state'], **({'required_version':r['required_version']} if r['required_version'] is not None else {})} for r in conn.execute('SELECT * FROM interface_consumers WHERE task_id=? ORDER BY interface_id', (task['id'],))])
        task.setdefault('claim_locks', [r[0] for r in conn.execute("SELECT lock_name FROM task_locks WHERE task_id=? AND phase='claim' ORDER BY lock_name", (task['id'],))])
        task.setdefault('invariants', [r[0] for r in conn.execute('SELECT invariant_id FROM task_invariants WHERE task_id=? ORDER BY invariant_id', (task['id'],))])
        task.setdefault('produced_artifacts', [dict(r) for r in conn.execute('SELECT kind,path FROM task_artifacts WHERE task_id=? ORDER BY kind,path', (task['id'],))])
        if 'depends_on' not in task:
            task['depends_on'] = []
            for dep in conn.execute('SELECT * FROM task_dependencies WHERE task_id=?', (task['id'],)):
                kind = dep['type']
                target = 'prerequisite_task_id' if kind == 'task' else kind + '_id'
                task['depends_on'].append({'type': kind, ('task_id' if kind == 'task' else target): dep[target], **json.loads(dep['condition_json'])})
        task.setdefault('resource_requests', [dict(r) for r in conn.execute('SELECT id,phase,selector,amount,mode,required FROM resource_requests WHERE task_id=? ORDER BY id', (task['id'],))])
    return data


def _semantic_plan_state(conn):
    # Compare actual normalized native effects, excluding bookkeeping generated
    # by the upsert itself. No authority/receipt is persisted by this probe.
    ignored = {'revision', 'updated_at', 'created_at', 'creation_revision'}
    generated_ids = {'task_dependencies', 'ownership_scopes', 'task_locks', 'task_artifacts', 'barrier_requirements'}
    state = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")') if r[1] not in ignored and not (table == 'tasks' and r[1] == 'version') and not (table in generated_ids and r[1] == 'id')]
        if not columns:
            continue
        rows = []
        for row in conn.execute('SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + table + '"'):
            values = []
            for column, value in zip(columns, row):
                if column.endswith('_json') and isinstance(value, str):
                    value = json.loads(value)
                values.append(value)
            rows.append(json.dumps(values, sort_keys=True, default=str))
        state[table] = sorted(rows)
    return state


def _plan_changes(conn, data, repo_root, revision):
    before = _semantic_plan_state(conn)
    conn.execute('SAVEPOINT canonical_plan_comparison')
    try:
        apply_plan(conn, data, repo_root, revision, _comparison=True)
        after = _semantic_plan_state(conn)
    finally:
        conn.execute('ROLLBACK TO canonical_plan_comparison')
        conn.execute('RELEASE canonical_plan_comparison')
    return before != after


def _task_plan_state(conn, task_id):
    state = {}
    for table in ('tasks', 'ownership_scopes', 'task_locks', 'task_invariants', 'task_artifacts', 'task_dependencies', 'resource_requests', 'checkpoints', 'gates', 'interface_consumers'):
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")') if r[1] not in {'revision', 'updated_at', 'created_at', 'version', 'reached_at', 'revoked_at'} and not (table in {'ownership_scopes', 'task_locks', 'task_artifacts', 'task_dependencies'} and r[1] == 'id')]
        key = 'id' if table == 'tasks' else 'task_id'
        rows = []
        for row in conn.execute('SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + table + '" WHERE "' + key + '"=?', (task_id,)):
            rows.append(json.dumps([json.loads(v) if c.endswith('_json') and isinstance(v, str) else v for c, v in zip(columns, row)], sort_keys=True, default=str))
        state[table] = sorted(rows)
    return state


def plan_diff(conn: sqlite3.Connection, data: dict[str, Any], repo_root: Path | None = None, *, _probe=False) -> dict[str, object]:
    if not _probe:
        # Read ports retain read-only authority: project into an ephemeral native
        # SQLite copy rather than acquire a write lock on the source authority.
        probe = sqlite3.connect(':memory:', isolation_level=None)
        probe.row_factory = sqlite3.Row
        try:
            conn.backup(probe)
            probe.execute('PRAGMA foreign_keys=ON')
            return plan_diff(probe, data, repo_root, _probe=True)
        finally:
            probe.close()
    existing = {row[0] for row in conn.execute("SELECT id FROM tasks")}
    incoming = {str(task["id"]) for task in data.get("tasks", [])}
    revision = int(conn.execute("SELECT value FROM meta WHERE key='project_revision'").fetchone()[0]) + 1
    before = _semantic_plan_state(conn)
    task_before = {task_id: _task_plan_state(conn, task_id) for task_id in incoming & existing}
    conn.execute('SAVEPOINT canonical_plan_diff')
    try:
        apply_plan(conn, data, repo_root or Path.cwd(), revision, _comparison=True)
        changed = before != _semantic_plan_state(conn)
        updates = [task_id for task_id, state in task_before.items() if state != _task_plan_state(conn, task_id)]
    finally:
        conn.execute('ROLLBACK TO canonical_plan_diff')
        conn.execute('RELEASE canonical_plan_diff')
    return {'add': sorted(incoming - existing), 'update': sorted(updates),
            'unchanged': sorted((incoming & existing) - set(updates)),
            'unchanged_or_removed': sorted(existing - incoming),
            'status': 'changed' if changed else 'noop'}


def apply_selective_replan(conn: sqlite3.Connection, data: dict[str, Any], repo_root: Path, revision: int) -> dict[str, object]:
    """Atomically replace only fully idle planned entries, with exact authority binding."""
    if data.get("format") != "selective-replan-v1" or not isinstance(data.get("replacements"), list):
        raise TodoError("invalid_selective_replan", "Expected selective-replan-v1 replacements")
    authority = data.get("authority")
    project = conn.execute("SELECT value FROM meta WHERE key='project_uuid'").fetchone()
    if not isinstance(authority, dict) or set(authority) != {"project_uuid", "revision", "fingerprint"} or not isinstance(authority["fingerprint"], str) or not project or authority["project_uuid"] != project["value"] or authority["revision"] != revision - 1:
        raise TodoError("selective_replan_authority_mismatch", "Selective replan requires current exact UUID, revision, and fingerprint")
    selected = {str(x.get("task_id")) for x in data["replacements"] if isinstance(x, dict) and x.get("task_id")}
    if not selected or len(selected) != len(data["replacements"]):
        raise TodoError("invalid_selective_replan", "Replacement task IDs must be present and unique")
    for old in selected:
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (old,)).fetchone()
        queue = conn.execute("SELECT lane_id,state FROM workflow_lane_tasks WHERE task_id=?", (old,)).fetchone()
        if not task or task["status"] != "planned" or not queue or queue["state"] != "queued":
            raise TodoError("selective_replan_task_not_idle", f"Task is not planned and queued: {old}")
        if conn.execute("SELECT 1 FROM claims WHERE task_id=?", (old,)).fetchone():
            raise TodoError("selective_replan_claim_bound", f"Task has claim history: {old}")
        if conn.execute("SELECT 1 FROM workflow_lane_tasks lt JOIN workflow_workspaces w ON w.lane_id=lt.lane_id WHERE lt.task_id=?", (old,)).fetchone():
            raise TodoError("selective_replan_workspace_bound", f"Task lane has a workspace: {old}")
        if conn.execute("SELECT 1 FROM workflow_patch_artifacts WHERE task_id=?", (old,)).fetchone():
            raise TodoError("selective_replan_artifact_bound", f"Task has patch artifacts: {old}")
        if conn.execute("SELECT 1 FROM workflow_integration_queue WHERE integration_task_id=?", (old,)).fetchone():
            raise TodoError("selective_replan_integration_bound", f"Task is an integration destination: {old}")
    rewrites = data.get("downstream_rewrites", [])
    if not isinstance(rewrites, list):
        raise TodoError("invalid_selective_replan", "downstream_rewrites must be an array")
    declared = {(str(x.get("task_id")), str(x.get("old_task_id"))): str(x.get("new_task_id")) for x in rewrites if isinstance(x, dict)}
    if len(declared) != len(rewrites) or any(not a or not b or not c for (a, b), c in declared.items()):
        raise TodoError("invalid_selective_replan", "Each downstream rewrite requires unique task_id, old_task_id, and new_task_id")
    replacements: dict[str, dict[str, Any]] = {}
    required = {"id", "title", "scope", "invariants", "produced_artifacts", "checkpoints", "gates"}
    for item in data["replacements"]:
        if not isinstance(item, dict) or not isinstance(item.get("replacement"), dict):
            raise TodoError("invalid_selective_replan", "Each replacement must be an object")
        old, replacement = str(item["task_id"]), dict(item["replacement"])
        scope = replacement.get("scope")
        if not required.issubset(replacement) or not isinstance(scope, dict) or not {"exclusive_paths", "read_paths", "forbidden_paths"}.issubset(scope):
            raise TodoError("selective_replan_incomplete_replacement", "Replacement must explicitly preserve scope, invariants, artifacts, checkpoints, and gates")
        replacement.setdefault("status", "planned")
        replacement.setdefault("completion_contract", {"assertions": [], "source_task_id": old})
        replacements[old] = replacement
    new_ids = {str(value["id"]) for value in replacements.values()}
    if "" in new_ids or any(conn.execute("SELECT 1 FROM tasks WHERE id=?", (value,)).fetchone() for value in new_ids):
        raise TodoError("selective_replan_replacement_identity", "Replacement IDs must be new")
    unique: dict[str, dict[str, Any]] = {}
    for value in replacements.values():
        prior = unique.setdefault(str(value["id"]), value)
        if prior != value:
            raise TodoError("selective_replan_replacement_identity", "Many-old-to-one must use one identical replacement record")
    for new, value in unique.items():
        olds = [old for old, replacement in replacements.items() if replacement["id"] == new]
        parents = {conn.execute("SELECT parent_id FROM tasks WHERE id=?", (old,)).fetchone()[0] for old in olds}
        if "parent_id" not in value:
            if len(parents) != 1:
                raise TodoError("selective_replan_replacement_identity", "Consolidated replacement requires explicit parent_id")
            value["parent_id"] = next(iter(parents))
    for (downstream, old), new in declared.items():
        if old not in selected or new not in unique or new != replacements[old]["id"] or downstream in selected:
            raise TodoError("selective_replan_rewrite_target", "Rewrite must connect a selected old task to its mapped new task")
        downstream_task = conn.execute("SELECT status FROM tasks WHERE id=?", (downstream,)).fetchone()
        if not downstream_task or downstream_task["status"] != "planned":
            raise TodoError("selective_replan_rewrite_target", f"Downstream task is not safely planned: {downstream}")
        if conn.execute("SELECT 1 FROM claims WHERE task_id=?", (downstream,)).fetchone() or conn.execute("SELECT 1 FROM workflow_patch_artifacts WHERE task_id=?", (downstream,)).fetchone() or conn.execute("SELECT 1 FROM workflow_integration_queue WHERE integration_task_id=?", (downstream,)).fetchone():
            raise TodoError("selective_replan_rewrite_target", f"Downstream task has durable execution state: {downstream}")
        if conn.execute("SELECT 1 FROM workflow_lane_tasks lt JOIN workflow_workspaces w ON w.lane_id=lt.lane_id WHERE lt.task_id=?", (downstream,)).fetchone():
            raise TodoError("selective_replan_rewrite_target", f"Downstream lane has a workspace: {downstream}")
    interfaces = []
    queue_rows = {old: dict(conn.execute("SELECT lane_id,position FROM workflow_lane_tasks WHERE task_id=?", (old,)).fetchone()) for old in selected}
    for new in unique:
        lanes = {queue_rows[old]["lane_id"] for old, value in replacements.items() if value["id"] == new}
        if len(lanes) != 1:
            raise TodoError("selective_replan_replacement_identity", "Many-old-to-one cannot cross lanes")
    for old, replacement in replacements.items():
        for row in conn.execute("SELECT * FROM interfaces WHERE owner_task_id=?", (old,)):
            interfaces.append({"id": row["id"], "owner_task_id": replacement["id"], "state": row["state"], "version": row["version"], "contract_paths": json.loads(row["contract_paths_json"]), "content_hash": row["content_hash"], "frozen_at": row["frozen_at"], "revised_at": row["revised_at"]})
    # Insert every new task before redirecting dependency foreign keys.
    apply_plan(conn, {"schema_version": 3, "project": {}, "tasks": list(unique.values()), "interfaces": interfaces}, repo_root, revision)
    # Coalesce all old prerequisites of a downstream task to one new
    # milestone.  Metadata must match because one dependency row survives.
    consumed_rewrites: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], list[str]] = {}
    for (downstream, old), new in declared.items():
        grouped.setdefault((downstream, new), []).append(old)
    for (downstream, new), olds in grouped.items():
        rows = []
        for old in olds:
            row = conn.execute("SELECT id,condition_json FROM task_dependencies WHERE task_id=? AND type='task' AND prerequisite_task_id=?", (downstream, old)).fetchall()
            if len(row) != 1:
                raise TodoError("selective_replan_rewrite_missing", f"Declared dependency not found exactly once: {downstream}->{old}")
            rows.append(row[0])
        if len({row["condition_json"] for row in rows}) != 1:
            raise TodoError("selective_replan_rewrite_metadata", "Consolidated dependencies have incompatible metadata")
        conn.execute("UPDATE task_dependencies SET prerequisite_task_id=? WHERE id=?", (new, rows[0]["id"]))
        for row in rows[1:]:
            conn.execute("DELETE FROM task_dependencies WHERE id=?", (row["id"],))
        consumed_rewrites.update((downstream, old) for old in olds)
    for old, replacement in replacements.items():
        new = str(replacement["id"])
        conn.execute("UPDATE tasks SET status='superseded',result='superseded',updated_at=?,revision=? WHERE id=?", (utc_now(), revision, old))
        # In a same-lane consolidation preserve exactly the earliest queue
        # position for the new milestone and remove the redundant old queue rows.
        peers = [candidate for candidate, value in replacements.items() if value["id"] == new and queue_rows[candidate]["lane_id"] == queue_rows[old]["lane_id"]]
        keeper = min(peers, key=lambda candidate: queue_rows[candidate]["position"])
        if old == keeper:
            conn.execute("UPDATE workflow_lane_tasks SET task_id=?,revision=? WHERE task_id=?", (new, revision, old))
        else:
            conn.execute("DELETE FROM workflow_lane_tasks WHERE task_id=?", (old,))
        if conn.execute("SELECT 1 FROM task_dependencies WHERE prerequisite_task_id=?", (old,)).fetchone():
            raise TodoError("selective_replan_undeclared_downstream", f"Every downstream dependency must be declared: {old}")
    if consumed_rewrites != set(declared):
        raise TodoError("selective_replan_rewrite_missing", "Every declared downstream rewrite must be consumed exactly once")
    return {"superseded": sorted(selected), "replacements": sorted(unique), "consolidated": {new: sorted(old for old, value in replacements.items() if value["id"] == new) for new in sorted(unique)}}


def scaffold(shape: str) -> dict[str, object]:
    base: dict[str, object] = {"schema_version": SCHEMA_VERSION, "project": {"name": "Project"}, "invariants": [], "decisions": [], "locks": [], "interfaces": [], "barriers": [], "resource_classes": [], "tasks": []}
    if shape == "fanout":
        base["tasks"] = [
            {"id": "EPIC-00", "kind": "epic", "title": "Parent", "parallel_policy": "serial"},
            *[{"id": f"TASK-{letter}", "parent_id": "EPIC-00", "kind": "task", "title": f"Child {letter}", "parallel_policy": "parallel_safe", "scope": {"exclusive_paths": [f"src/{letter.lower()}"]}} for letter in "ABC"],
            {"id": "INTEGRATE", "parent_id": "EPIC-00", "kind": "integration_task", "title": "Integrate", "parallel_policy": "integration_exclusive", "depends_on": [{"type": "barrier", "barrier_id": "FANIN"}]},
        ]
        base["barriers"] = [{"id": "FANIN", "mode": "all", "requirements": [{"type": "task", "id": f"TASK-{letter}", "state": "done"} for letter in "ABC"]}]
    elif shape == "producer-consumers":
        base["interfaces"] = [{"id": "contract", "owner_task_id": "PRODUCER", "contract_paths": ["include/contract.h"]}]
        base["tasks"] = [
            {"id": "PRODUCER", "kind": "workstream", "title": "Produce contract", "checkpoints": [{"id": "CONTRACT-FROZEN", "publishes_interfaces": [{"id": "contract", "version": "1"}]}]},
            {"id": "CONSUMER", "kind": "workstream", "title": "Consume contract", "depends_on": [{"type": "checkpoint", "checkpoint_id": "CONTRACT-FROZEN"}], "consumes_interfaces": [{"id": "contract", "required_state": "frozen"}]},
        ]
    elif shape == "benchmark":
        base["resource_classes"] = [{"id": "gpu", "instances": [{"id": "gpu:0"}, {"id": "gpu:1"}]}]
        base["tasks"] = [{"id": "BENCH", "kind": "workstream", "title": "Benchmark", "gates": [{"id": "BENCH-GATE", "type": "benchmark", "argv": ["python", "bench.py"], "resources": ["gpu:any"], "metric_path": "score", "operator": ">=", "threshold": 1.0}]}]
    elif shape == "integration-barrier":
        return scaffold("fanout")
    else:
        raise TodoError("unknown_scaffold", f"Unknown plan scaffold {shape}")
    return base
