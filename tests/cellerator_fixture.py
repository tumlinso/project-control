from __future__ import annotations

from project_control.models import ProjectSnapshot, RepositoryIdentity


def cellerator_snapshot() -> ProjectSnapshot:
    tasks = [
        {
            "id": "CE-ARCH-00", "kind": "epic", "title": "CE-ARCH architecture program",
            "objective": "Complete the current architecture migration", "raw_status": "done",
            "raw_result": "validated", "effective_state": "done", "terminal": True,
            "frontier_eligible": False, "attention_eligible": False,
            "current_program_eligible": True, "current_relevance": "reference",
            "reason_codes": ["successful_terminal_result"], "priority": 100,
            "program_root_id": "CE-ARCH-00", "ancestor_chain": [], "dependencies": [],
        },
        {
            "id": "CE-ARCH-82", "parent_id": "CE-ARCH-00", "kind": "task",
            "title": "Execution Image v2", "objective": "Freeze the Execution Image v2 contract and tests",
            "raw_status": "done", "raw_result": "validated", "effective_state": "done",
            "terminal": True, "frontier_eligible": False, "attention_eligible": False,
            "current_program_eligible": True, "current_relevance": "reference",
            "reason_codes": ["successful_terminal_result"], "priority": 90,
            "program_root_id": "CE-ARCH-00", "ancestor_chain": ["CE-ARCH-00"], "dependencies": [],
        },
        {
            "id": "CE-ARCH-92", "parent_id": "CE-ARCH-00", "kind": "validation_task",
            "title": "Real-data architecture evidence", "objective": "Validate Execution Image v2 on PBMC3k, GSE147520, and adversarial traces",
            "raw_status": "done", "raw_result": "validated", "effective_state": "done",
            "terminal": True, "frontier_eligible": False, "attention_eligible": False,
            "current_program_eligible": True, "current_relevance": "reference",
            "reason_codes": ["successful_terminal_result"], "priority": 95,
            "program_root_id": "CE-ARCH-00", "ancestor_chain": ["CE-ARCH-00"], "dependencies": [],
        },
        {
            "id": "CP-MATH-17", "kind": "task", "title": "Old math runtime",
            "objective": "Superseded by CE-ARCH-60 after replacements validate.",
            "raw_status": "superseded", "raw_result": "superseded", "effective_state": "superseded",
            "terminal": True, "frontier_eligible": False, "attention_eligible": False,
            "current_program_eligible": False, "current_relevance": "superseded",
            "reason_codes": ["explicit_status_superseded"], "priority": 120,
            "program_root_id": "CP-MATH-17", "ancestor_chain": [], "dependencies": [],
        },
    ]
    raw_tasks = [
        {"id": item["id"], "parent_id": item.get("parent_id"), "kind": item["kind"], "title": item["title"],
         "objective": item["objective"], "status": item["raw_status"], "result": item["raw_result"],
         "priority": item["priority"], "updated_at": f"2026-08-{25 if item['id'].startswith('CE') else 10:02d}T00:00:00Z"}
        for item in tasks
    ]
    events = [
        {"revision": index + 10, "timestamp": "2026-08-25T00:00:00Z", "event_type": "claim.pulsed", "entity_type": "claim", "entity_id": "CE-ARCH-00"}
        for index in range(400)
    ]
    events.extend([
        {"revision": 450, "timestamp": "2026-08-25T12:00:00Z", "event_type": "interface.freeze", "entity_type": "interface", "entity_id": "EXECUTION-IMAGE-V2"},
        {"revision": 451, "timestamp": "2026-08-25T13:00:00Z", "event_type": "gate.completed", "entity_type": "gate", "entity_id": "CE-ARCH-92-GATE"},
        {"revision": 452, "timestamp": "2026-08-25T14:00:00Z", "event_type": "task.completed", "entity_type": "task", "entity_id": "CE-ARCH-92"},
    ])
    return ProjectSnapshot(
        workspace_id="cellerator",
        display_name="Cellerator",
        observed_at="2026-08-26T00:00:00Z",
        todo_revision=452,
        project_uuid="cellerator-fixture",
        repositories={
            "source": RepositoryIdentity(
                commit="a" * 40,
                dirty=False,
                working_tree_fingerprint="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
        },
        repository_fingerprints={"source": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        todo_status={"ready": [], "active_claims": []},
        todo_semantic={
            "revision": 452,
            "project_uuid": "cellerator-fixture",
            "tasks": tasks,
            "checkpoints": [
                {"id": "CE-ARCH-VALIDATED", "task_id": "CE-ARCH-92", "raw_state": "reached", "effective_state": "reached", "attention_eligible": False, "owner_task_effective_state": "done", "reason_codes": ["raw_checkpoint_reached"]},
                {"id": "CP-MATH-COMPLETE", "task_id": "CP-MATH-17", "raw_state": "pending", "effective_state": "historical_stale", "attention_eligible": False, "owner_task_effective_state": "superseded", "reason_codes": ["terminal_owner", "no_current_consumer"]},
                {"id": "CE_ARCH_FORENSIC_INVENTORY_VALIDATED", "task_id": "CE-ARCH-00", "raw_state": "pending", "effective_state": "historical_stale", "attention_eligible": False, "owner_task_effective_state": "done", "reason_codes": ["terminal_owner", "no_current_consumer"]},
            ],
            "gates": [
                {"id": "CE-ARCH-92-GATE", "task_id": "CE-ARCH-92", "type": "command", "required": True, "raw_status": "passed", "raw_valid": True, "effective_state": "historical_valid", "attention_eligible": False, "owner_task_effective_state": "done", "reason_codes": ["terminal_owner", "historical_gate_state"]},
                {"id": "CP-MATH-REMOVED-RUNTIME", "task_id": "CP-MATH-17", "type": "file_exists", "required": True, "raw_status": "failed", "raw_valid": False, "effective_state": "historical_invalid", "attention_eligible": False, "owner_task_effective_state": "superseded", "reason_codes": ["terminal_owner", "historical_gate_state"]},
            ],
            "programs": [{
                "id": "CE-ARCH-00", "basis": "parent_hierarchy",
                "task_ids": ["CE-ARCH-00", "CE-ARCH-82", "CE-ARCH-92"],
                "effective_state_counts": {"done": 3}, "has_current_work": False, "complete": True,
            }],
            "contradictions": [],
        },
        todo_tables={
            "tasks": raw_tasks,
            "task_dependencies": [],
            "ownership_scopes": [
                {"task_id": "CE-ARCH-82", "mode": "exclusive", "path": "src/compute/execution_image"},
                {"task_id": "CE-ARCH-92", "mode": "exclusive", "path": "bench/architecture_evidence"},
            ],
            "task_artifacts": [
                {"task_id": "CE-ARCH-82", "kind": "contract", "path": "src/compute/execution_image/execution_image_v2.hpp"},
                {"task_id": "CE-ARCH-82", "kind": "test", "path": "tests/architecture/execution_image_v2_test.cpp"},
                {"task_id": "CE-ARCH-92", "kind": "validation", "path": "bench/architecture_evidence/ce_arch_92_v100_summary.json"},
            ],
            "interfaces": [{"id": "EXECUTION-IMAGE-V2", "owner_task_id": "CE-ARCH-82", "state": "frozen", "version": "2", "contract_paths": ["src/compute/execution_image/execution_image_v2.hpp"]}],
            "interface_consumers": [],
            "decisions": [], "invariants": [], "task_invariants": [],
            "checkpoints": [
                {"id": "CP-MATH-COMPLETE", "task_id": "CP-MATH-17", "state": "pending"},
                {"id": "CE_ARCH_FORENSIC_INVENTORY_VALIDATED", "task_id": "CE-ARCH-00", "state": "pending"},
            ],
            "gates": [
                {"id": "CE-ARCH-92-GATE", "task_id": "CE-ARCH-92", "type": "command", "required": 1, "status": "passed", "valid": 1},
                {"id": "CP-MATH-REMOVED-RUNTIME", "task_id": "CP-MATH-17", "type": "file_exists", "required": 1, "status": "failed", "valid": 0},
            ],
            "events": events,
            "handoffs": [], "child_executions": [], "evidence": [],
        },
        cuda={
            "status": "ok", "warnings": [],
            "campaigns": [
                {"id": "cp-bp-history", "status": "armed", "task_ids": ["CP-MATH-17"], "paths": ["src/old"]},
                {"id": "ce-arch-92-evidence", "status": "closed", "task_ids": ["CE-ARCH-92"], "paths": ["bench/architecture_evidence"]},
            ],
            "facts": [],
            "results": [
                {"id": "old-regression", "campaign_id": "cp-bp-history", "task_id": "CP-MATH-17", "classification": "material-regression", "valid": True, "contaminated": False},
                {"id": "current-ce-result", "campaign_id": "ce-arch-92-evidence", "task_id": "CE-ARCH-92", "classification": "healthy", "valid": True, "contaminated": False, "measurement": {"metric": "amortized_total_ms"}},
            ],
        },
        local_worker={"status": "unavailable", "warnings": []},
        host={},
    )
