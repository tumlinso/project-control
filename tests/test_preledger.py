from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from project_control.preledger import PreledgerError, canonical_json, compile_preledger


class Package:
    def __init__(self, *, versioned: bool = False) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.versioned = versioned

    def close(self) -> None:
        self.temporary.cleanup()

    def write_json(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def write_tasks(self, tasks: list[dict[str, object]]) -> Path:
        return self.write_json("proposed_todos.json", {"tasks": tasks})

    def descriptor(self, **optional: str) -> Path:
        return self.write_json("preledger.json", {
            "format": "project-control-preledger",
            "schema_version": 1,
            "tasks": "proposed_todos.json",
            **optional,
        })


def task(task_id: str, repository: str = "Cellerator", **values: object) -> dict[str, object]:
    return {
        "id": task_id,
        "title": f"Title {task_id}",
        "repository": repository,
        "purpose": f"Purpose {task_id}",
        **values,
    }


class PreledgerCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = Package()

    def tearDown(self) -> None:
        self.package.close()

    def test_minimal_compatibility_package(self) -> None:
        self.package.write_tasks([task("CE-1")])

        result = compile_preledger(self.package.root, target_repository="Cellerator")

        self.assertEqual(result.selected_task_count, 1)
        self.assertEqual(result.excluded_task_count, 0)
        self.assertEqual(result.native_todo_plan["schema_version"], 2)
        self.assertEqual(result.native_todo_plan["project"], {"name": "Cellerator"})
        self.assertEqual(result.native_todo_plan["tasks"][0]["objective"], "Purpose CE-1")
        self.assertIn("proposed_todos.json", result.source_files)

    def test_versioned_package_and_deterministic_compilation(self) -> None:
        self.package.write_tasks([task("CE-2"), task("CE-1")])
        self.package.descriptor()

        first = compile_preledger(self.package.root, target_repository="Cellerator")
        second = compile_preledger(self.package.root, target_repository="Cellerator")

        self.assertEqual(first, second)
        self.assertEqual(first.canonical_plan_json(), second.canonical_plan_json())
        self.assertEqual(first.plan_digest, hashlib.sha256(first.canonical_plan_json().encode()).hexdigest())
        self.assertEqual([item["id"] for item in first.native_todo_plan["tasks"]], ["CE-1", "CE-2"])

    def test_repository_filtering_and_cross_authority_dependency(self) -> None:
        self.package.write_tasks([
            task("CE-1", prerequisite_tasks_or_checkpoints=["CS-1"]),
            task("CS-1", "CellShard"),
        ])

        result = compile_preledger(self.package.root, target_repository="Cellerator")

        self.assertEqual(result.selected_task_count, 1)
        self.assertEqual(result.excluded_task_count, 1)
        self.assertNotIn("depends_on", result.native_todo_plan["tasks"][0])
        self.assertEqual(result.external_dependencies, ({
            "task_id": "CE-1",
            "dependency_id": "CS-1",
            "dependency_repository": "CellShard",
            "source": ["prerequisite_tasks_or_checkpoints"],
        },))

    def test_same_authority_dependencies_and_scope_lowering(self) -> None:
        self.package.write_tasks([
            task("CE-1"),
            task(
                "CE-2",
                builds_on="CE-1",
                write_scope={"exclusive_paths": ["src/b", "src/a", "src/a"]},
                existing_source_paths=["include/z.h", "include/z.h"],
                proposed_source_paths=["must/not/be/inferred.cc"],
            ),
        ])

        result = compile_preledger(self.package.root, target_repository="Cellerator")
        lowered = {item["id"]: item for item in result.native_todo_plan["tasks"]}["CE-2"]

        self.assertEqual(lowered["depends_on"], [{"type": "task", "task_id": "CE-1"}])
        self.assertEqual(result.internal_dependency_count, 1)
        self.assertEqual(lowered["scope"], {
            "exclusive_paths": ["src/a", "src/b"],
            "read_paths": ["include/z.h"],
        })

    def test_rich_metadata_is_preserved_in_notes(self) -> None:
        self.package.write_tasks([task(
            "CE-RICH",
            workstream="sparse-runtime",
            biological_motivation="Preserve rare populations",
            compiler_architectural_reason="Keep generic compute below policy",
            implementation_mechanism="bounded CUDA primitive",
            invariants=["exact bytes"],
            forbidden_shortcuts=["host fallback"],
            validation=["unit", "integration"],
            performance_evidence="accepted benchmark",
            completion_condition="tests pass",
            experimental=True,
            negative_result_acceptable=False,
        )])

        result = compile_preledger(self.package.root, target_repository="Cellerator")
        prefix, encoded = result.native_todo_plan["tasks"][0]["notes"].split("=", 1)
        provenance = json.loads(encoded)

        self.assertEqual(prefix, "preledger_provenance")
        self.assertEqual(provenance["original_task_id"], "CE-RICH")
        self.assertEqual(provenance["preledger_package_digest"], result.package_digest)
        self.assertEqual(provenance["biological_motivation"], "Preserve rare populations")
        self.assertTrue(provenance["experimental"])
        self.assertFalse(provenance["negative_result_acceptable"])

    def test_interface_import_and_malformed_interface_reporting(self) -> None:
        self.package.write_tasks([task("CE-OWNER")])
        self.package.write_json("interface_catalog.json", {"interfaces": [
            {
                "id": "CE-IFACE",
                "owner_task_id": "CE-OWNER",
                "state": "frozen",
                "version": 3,
                "contract_paths": ["include/iface.h"],
                "content_hash": "abc123",
            },
            {"id": "INCOMPLETE", "owner_task_id": "CE-OWNER"},
        ]})

        result = compile_preledger(self.package.root, target_repository="Cellerator")

        self.assertEqual(result.interfaces_imported, 1)
        self.assertEqual(result.native_todo_plan["interfaces"], [{
            "id": "CE-IFACE",
            "owner_task_id": "CE-OWNER",
            "state": "frozen",
            "version": "3",
            "contract_paths": ["include/iface.h"],
            "content_hash": "abc123",
        }])
        self.assertIn("unsupported interface record: INCOMPLETE", result.warnings)

    def test_dependency_index_supplements_task_local_dependencies(self) -> None:
        self.package.write_tasks([task("CE-1"), task("CE-2")])
        (self.package.root / "dependency_edges.csv").write_text(
            "task_id,prerequisite_task_id\nCE-2,CE-1\n", encoding="utf-8"
        )

        result = compile_preledger(self.package.root, target_repository="Cellerator")

        lowered = {item["id"]: item for item in result.native_todo_plan["tasks"]}["CE-2"]
        self.assertEqual(lowered["depends_on"], [{"type": "task", "task_id": "CE-1"}])
        self.assertIn("dependency index supplemented task-local dependencies: CE-2 -> CE-1", result.warnings)

    def test_produced_checkpoint_dependency_is_native(self) -> None:
        self.package.write_tasks([
            task("CE-1", produces_checkpoint="CE-READY"),
            task("CE-2", prerequisite_tasks_or_checkpoints=["CE-READY"]),
        ])

        result = compile_preledger(self.package.root, target_repository="Cellerator")
        lowered = {item["id"]: item for item in result.native_todo_plan["tasks"]}

        self.assertEqual(lowered["CE-1"]["checkpoints"], [{"id": "CE-READY", "title": "CE-READY"}])
        self.assertEqual(lowered["CE-2"]["depends_on"], [{"type": "checkpoint", "checkpoint_id": "CE-READY"}])

    def test_malformed_task_id_is_rejected(self) -> None:
        self.package.write_tasks([task("bad task id")])

        with self.assertRaisesRegex(PreledgerError, "malformed id"):
            compile_preledger(self.package.root, target_repository="Cellerator")

    def test_missing_required_file_is_rejected(self) -> None:
        self.package.descriptor()

        with self.assertRaisesRegex(PreledgerError, "missing tasks file"):
            compile_preledger(self.package.root, target_repository="Cellerator")

    def test_manifest_is_validated_and_mismatch_rejected(self) -> None:
        tasks_path = self.package.write_tasks([task("CE-1")])
        digest = hashlib.sha256(tasks_path.read_bytes()).hexdigest()
        (self.package.root / "MANIFEST.sha256").write_text(
            f"{digest}  proposed_todos.json\n", encoding="utf-8"
        )
        compile_preledger(self.package.root, target_repository="Cellerator")
        (self.package.root / "MANIFEST.sha256").write_text(
            f"{'0' * 64}  proposed_todos.json\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(PreledgerError, "manifest hash mismatch"):
            compile_preledger(self.package.root, target_repository="Cellerator")

    def test_summary_is_not_semantic_authority(self) -> None:
        self.package.write_tasks([task("CE-1")])
        self.package.write_json("plan_summary.json", {"claimed_task_count": 999})
        self.package.descriptor(summary="plan_summary.json")
        first = compile_preledger(self.package.root, target_repository="Cellerator")
        self.package.write_json("plan_summary.json", {"claimed_task_count": 0, "different": True})
        second = compile_preledger(self.package.root, target_repository="Cellerator")

        self.assertEqual(first.package_digest, second.package_digest)
        self.assertEqual(first.native_todo_plan, second.native_todo_plan)
        self.assertEqual(first.plan_digest, second.plan_digest)

    def test_canonical_json_has_stable_compact_encoding(self) -> None:
        self.assertEqual(canonical_json({"b": 2, "a": 1}), '{"a":1,"b":2}\n')


if __name__ == "__main__":
    unittest.main()
