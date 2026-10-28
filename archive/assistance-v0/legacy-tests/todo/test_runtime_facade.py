from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from todo_orchestrator.background.host import HostCoordinator
from todo_orchestrator.background.store import BackgroundStore
from todo_orchestrator.runtime import (
    ContractError,
    RuntimeFacade,
    capture_source_identity,
    classify_local_worker_host_topology,
    normalize_artifact_ref,
    normalize_command_spec,
    normalize_evidence_summary,
    normalize_resource_request,
    normalize_source_identity,
    discover_gpu_topology,
)


class RuntimeContractTests(unittest.TestCase):
    def test_schema_documents_and_dependency_free_normalizers(self) -> None:
        names = [
            "command-spec-v1.schema.json",
            "source-identity-v1.schema.json",
            "resource-request-v1.schema.json",
            "artifact-ref-v1.schema.json",
            "evidence-summary-v1.schema.json",
        ]
        documents = {
            name: json.loads((ROOT / "schemas" / "runtime" / name).read_text(encoding="utf-8"))
            for name in names
        }
        self.assertTrue(all(item["$schema"].endswith("2020-12/schema") for item in documents.values()))
        self.assertTrue(all(item["additionalProperties"] is False for item in documents.values()))

        command = normalize_command_spec({"schema_version": 1, "argv": ["python", "-V"], "cwd": "."})
        self.assertEqual(command["env"], {})
        self.assertEqual(command["timeout_seconds"], 3600.0)
        with self.assertRaises(ContractError):
            normalize_command_spec("python -V")
        with self.assertRaises(ContractError):
            normalize_command_spec({"schema_version": 1, "argv": ["true"], "cwd": ".", "shell": True})

        request = normalize_resource_request({"schema_version": 1, "ids": ["accelerator:GPU-a"], "cpu_threads": 2})
        self.assertEqual(request["ids"], ["accelerator:GPU-a"])
        with self.assertRaises(ContractError):
            normalize_resource_request({"schema_version": 1, "ids": ["accelerator:GPU-a"], "count": 1})

        identity = normalize_source_identity({
            "schema_version": 1,
            "repo_root": "/tmp/repo",
            "git_head": "a" * 40,
            "dirty_paths": ["z.cpp", "a.cpp"],
            "fingerprint": "b" * 64,
        })
        self.assertEqual(identity["dirty_paths"], ["a.cpp", "z.cpp"])
        artifact = normalize_artifact_ref({
            "schema_version": 1, "kind": "report", "path": "out.json", "content_hash": None, "complete": True,
        })
        evidence = normalize_evidence_summary({
            "schema_version": 1,
            "status": "needs_codex",
            "valid": True,
            "contaminated": False,
            "severity": 1,
            "summary": {"reason": "architectural decision"},
            "artifacts": [artifact],
            "source_identity": identity,
        })
        self.assertEqual(evidence["status"], "needs_codex")

    def test_source_identity_is_content_sensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "runtime@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=root, check=True)
            source = root / "source.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            subprocess.run(["git", "add", "source.cpp"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
            clean = capture_source_identity(root)
            self.assertEqual(clean["dirty_paths"], [])
            source.write_text("int value = 2;\n", encoding="utf-8")
            dirty = capture_source_identity(root)
            self.assertEqual(dirty["dirty_paths"], ["source.cpp"])
            self.assertNotEqual(clean["fingerprint"], dirty["fingerprint"])

    def test_source_identity_covers_renamed_destination_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "runtime@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=root, check=True)
            original = root / "old.cpp"
            original.write_text("".join(f"int value_{index} = {index};\n" for index in range(40)), encoding="utf-8")
            subprocess.run(["git", "add", "old.cpp"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
            renamed = root / "new.cpp"
            original.rename(renamed)
            renamed.write_text("".join(f"int value_{index} = {index + (1 if index == 20 else 0)};\n" for index in range(40)), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            first = capture_source_identity(root)
            self.assertEqual(first["dirty_paths"], ["new.cpp"])
            renamed.write_text("".join(f"int value_{index} = {index + (2 if index == 20 else 0)};\n" for index in range(40)), encoding="utf-8")
            second = capture_source_identity(root)
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])


class RuntimeFacadeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.host_runtime = tempfile.TemporaryDirectory()
        self.environment = mock.patch.dict(os.environ, {"TODO_BACKGROUND_HOST_RUNTIME_DIR": self.host_runtime.name})
        self.environment.start()
        self.root = Path(self.temporary.name)
        self.store = BackgroundStore(self.root)
        self.host = HostCoordinator()
        self.facade = RuntimeFacade(self.root, store=self.store, host=self.host)
        self.identity = {
            "schema_version": 1,
            "repo_root": str(self.root),
            "git_head": None,
            "dirty_paths": [],
            "fingerprint": "0" * 64,
        }
        self.command = {"schema_version": 1, "argv": ["true"], "cwd": str(self.root), "timeout_seconds": 5}

    def tearDown(self) -> None:
        self.environment.stop()
        self.host_runtime.cleanup()
        self.temporary.cleanup()

    def test_jobs_artifacts_and_external_evidence_use_stable_contracts(self) -> None:
        first = self.facade.jobs.enqueue(kind="contract-test", command=self.command, source_identity=self.identity)
        duplicate = self.facade.jobs.enqueue(kind="contract-test", command=self.command, source_identity=self.identity)
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(first["job_id"], duplicate["job_id"])
        artifact_id = self.facade.artifacts.record(
            job_id=str(first["job_id"]),
            artifact={"schema_version": 1, "kind": "log", "path": "log.txt", "content_hash": None, "complete": True},
        )
        self.assertTrue(artifact_id)

        recorded = self.facade.jobs.record_external(
            kind="external-contract-test",
            command=self.command,
            source_identity=self.identity,
            evidence={
                "schema_version": 1,
                "status": "succeeded",
                "valid": True,
                "contaminated": False,
                "severity": 0,
                "summary": {"message": "bounded"},
                "artifacts": [
                    {"schema_version": 1, "kind": "report", "path": "report.json", "content_hash": None, "complete": True}
                ],
            },
        )
        result = self.facade.jobs.result(recorded["result_id"])
        self.assertIsNotNone(result)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["source_identity"]["fingerprint"], "0" * 64)
        self.assertEqual(result["summary"], {"message": "bounded"})
        self.assertEqual(result["artifacts"][0]["kind"], "report")

    def test_host_facade_wraps_existing_atomic_coordinator(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {"island": "one"}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {"island": "two"}},
        ])
        self.assertEqual([item["id"] for item in self.facade.host.list(kind="accelerator")],
                         ["accelerator:GPU-a", "accelerator:GPU-b"])
        request = {"schema_version": 1, "ids": ["accelerator:GPU-a"]}
        reservation = self.facade.host.reserve_background(
            project_root=self.root, job_id="job-a", attempt_id="attempt-a", resource_request=request,
        )
        self.assertIsNotNone(reservation)
        blocked = self.facade.host.reserve_background(
            project_root=self.root / "other", job_id="job-b", attempt_id="attempt-b", resource_request=request,
        )
        self.assertIsNone(blocked)
        self.facade.host.release(str(reservation["owner_id"]))
        available = self.facade.host.reserve_background(
            project_root=self.root / "other", job_id="job-b", attempt_id="attempt-b", resource_request=request,
        )
        self.assertIsNotNone(available)
        self.facade.host.release(str(available["owner_id"]))

    def test_service_priority_drain_quiescence_and_runtime_bundles(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:GPU-a", "kind": "accelerator",
             "tags": {"nvlink_domain": "island-a", "pcie_root": "root-a"}},
            {"id": "accelerator:GPU-b", "kind": "accelerator",
             "tags": {"nvlink_domain": "island-a", "pcie_root": "root-a"}},
            {"id": "accelerator:GPU-c", "kind": "accelerator",
             "tags": {"nvlink_domain": "island-b", "pcie_root": "root-b"}},
        ])
        bundles = self.facade.host.compound_gpu_bundles(2)
        self.assertEqual(bundles[0]["resource_ids"], ["accelerator:GPU-a", "accelerator:GPU-b"])
        service = self.facade.host.reserve_service(
            project_root=self.root,
            service_id="llama",
            resource_request={"schema_version": 1, "ids": bundles[0]["resource_ids"],
                              "exclusive_resources": bundles[0]["exclusive_resources"]},
        )
        self.assertIsNotNone(service)
        owner_id = str(service["owner_id"])
        self.assertTrue(self.facade.host.set_priority(owner_id, "active_local_delegation"))
        drained: list[str] = []
        self.facade.host.register_drain_callback(owner_id, lambda: drained.append(owner_id) or True)
        self.assertTrue(self.facade.host.request_preemption(owner_id))
        self.assertTrue(self.facade.host.drain_if_requested(owner_id))
        self.assertEqual(drained, [owner_id])
        self.assertTrue(self.facade.host.wait_for_quiescence(bundles[0]["resource_ids"], timeout_seconds=0.1))

    def test_discovery_refresh_retires_stale_accelerator_ids(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:synthetic", "kind": "accelerator", "tags": {"nvlink_domain": "ghost"}},
        ])
        live = [
            {"id": "accelerator:GPU-live", "kind": "accelerator", "tags": {"nvlink_domain": "live"}},
        ]
        with mock.patch("todo_orchestrator.runtime.facade.discover_gpu_topology", return_value=live):
            self.assertEqual(self.facade.host.discover_gpus(), live)
        resources = {item["id"]: item for item in self.facade.host.list(kind="accelerator")}
        self.assertFalse(resources["accelerator:synthetic"]["enabled"])
        self.assertTrue(resources["accelerator:GPU-live"]["enabled"])
        bundles = self.facade.host.compound_gpu_bundles(1)
        self.assertEqual([item["resource_ids"] for item in bundles], [["accelerator:GPU-live"]])

    def test_two_nvlink_pairs_share_pcie_roots_but_reserve_concurrently(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:GPU-0", "kind": "accelerator", "tags": {"nvlink_domain": "0-2", "pcie_root": "root-0"}},
            {"id": "accelerator:GPU-1", "kind": "accelerator", "tags": {"nvlink_domain": "1-3", "pcie_root": "root-0"}},
            {"id": "accelerator:GPU-2", "kind": "accelerator", "tags": {"nvlink_domain": "0-2", "pcie_root": "root-1"}},
            {"id": "accelerator:GPU-3", "kind": "accelerator", "tags": {"nvlink_domain": "1-3", "pcie_root": "root-1"}},
        ])
        bundles = self.facade.host.compound_gpu_bundles(2)
        self.assertEqual(
            {tuple(item["resource_ids"]) for item in bundles},
            {("accelerator:GPU-0", "accelerator:GPU-2"), ("accelerator:GPU-1", "accelerator:GPU-3")},
        )
        first, second = bundles
        self.assertTrue(all(not item.startswith("interference:pcie:") for item in first["exclusive_resources"]))
        left = self.facade.host.reserve_service(
            project_root=self.root, service_id="pair-left",
            resource_request={"schema_version": 1, "ids": first["resource_ids"], "exclusive_resources": first["exclusive_resources"]},
        )
        right = self.facade.host.reserve_service(
            project_root=self.root, service_id="pair-right",
            resource_request={"schema_version": 1, "ids": second["resource_ids"], "exclusive_resources": second["exclusive_resources"]},
        )
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        self.assertIsNone(self.facade.host.reserve_service(
            project_root=self.root, service_id="pair-overlap",
            resource_request={"schema_version": 1, "ids": first["resource_ids"], "exclusive_resources": first["exclusive_resources"]},
        ))

    def test_singleton_disjoint_gpus_on_same_pcie_root_coexist(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {"nvlink_domain": "synthetic-a", "pcie_root": "root-0"}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {"nvlink_domain": "synthetic-b", "pcie_root": "root-0"}},
        ])
        bundles = self.facade.host.compound_gpu_bundles(1)
        self.assertTrue(all(
            not resource.startswith("interference:pcie:")
            for bundle in bundles for resource in bundle["exclusive_resources"]
        ))
        first, second = bundles
        left = self.facade.host.reserve_service(
            project_root=self.root, service_id="singleton-left",
            resource_request={"schema_version": 1, "ids": first["resource_ids"], "exclusive_resources": first["exclusive_resources"]},
        )
        self.assertIsNotNone(left)
        self.assertIsNotNone(self.facade.host.reserve_service(
            project_root=self.root, service_id="singleton-right",
            resource_request={"schema_version": 1, "ids": second["resource_ids"], "exclusive_resources": second["exclusive_resources"]},
        ))

    def test_disjoint_gpus_in_same_nvlink_domain_conflict(self) -> None:
        self.facade.host.upsert([
            {"id": "accelerator:GPU-a", "kind": "accelerator", "tags": {"nvlink_domain": "shared", "pcie_root": "root-0"}},
            {"id": "accelerator:GPU-b", "kind": "accelerator", "tags": {"nvlink_domain": "shared", "pcie_root": "root-1"}},
        ])
        bundles = self.facade.host.compound_gpu_bundles(1)
        first, second = bundles
        left = self.facade.host.reserve_service(
            project_root=self.root, service_id="shared-left",
            resource_request={"schema_version": 1, "ids": first["resource_ids"], "exclusive_resources": first["exclusive_resources"]},
        )
        self.assertIsNotNone(left)
        self.assertIsNone(self.facade.host.reserve_service(
            project_root=self.root, service_id="shared-right",
            resource_request={"schema_version": 1, "ids": second["resource_ids"], "exclusive_resources": second["exclusive_resources"]},
        ))

    def test_topology_discovery_derives_islands_from_runtime_output(self) -> None:
        outputs = iter([
            "0, GPU-a, 00000000:07:00.0\n1, GPU-b, 00000000:08:00.0\n2, GPU-c, 00000000:80:00.0\n",
            "\t\x1b[4mGPU0\tGPU1\tGPU2\tCPU Affinity\tNUMA Affinity\tGPU NUMA ID\x1b[0m\n"
            "GPU0\tX\tNV2\tSYS\t0-31\t0\tN/A\n"
            "GPU1\tNV2\tX\tSYS\t0-31\t0\tN/A\n"
            "GPU2\tSYS\tSYS\tX\t32-63\t1\tN/A\n",
        ])
        resources = discover_gpu_topology(lambda argv: next(outputs))
        tags = {item["tags"]["uuid"]: item["tags"] for item in resources}
        self.assertEqual(tags["GPU-a"]["nvlink_domain"], tags["GPU-b"]["nvlink_domain"])
        self.assertNotEqual(tags["GPU-a"]["nvlink_domain"], tags["GPU-c"]["nvlink_domain"])
        self.assertEqual((tags["GPU-a"]["numa_node"], tags["GPU-c"]["numa_node"]), ("0", "1"))

    def test_local_worker_topology_classifies_two_nvlink_pairs_as_normal(self) -> None:
        topology = (
            "\tGPU0\tGPU1\tGPU2\tGPU3\tCPU Affinity\tNUMA Affinity\n"
            "GPU0\tX\tNV1\tSYS\tSYS\t0-19\t0\n"
            "GPU1\tNV1\tX\tSYS\tSYS\t0-19\t0\n"
            "GPU2\tSYS\tSYS\tX\tNV4\t20-39\t1\n"
            "GPU3\tSYS\tSYS\tNV4\tX\t20-39\t1\n"
        )
        result = classify_local_worker_host_topology(lambda argv: topology)
        self.assertEqual((result.mode, result.gpu_count, result.status), ("normal", 4, "available"))
        self.assertEqual(result.nvlink_components, ((0, 1), (2, 3)))

    def test_local_worker_topology_classifies_transitive_four_gpu_graph_as_x_mode(self) -> None:
        topology = (
            "\tGPU0\tGPU1\tGPU2\tGPU3\n"
            "GPU0\tX\tNV2\tSYS\tSYS\n"
            "GPU1\tNV2\tX\tNV1\tSYS\n"
            "GPU2\tSYS\tNV1\tX\tNV4\n"
            "GPU3\tSYS\tSYS\tNV4\tX\n"
        )
        result = classify_local_worker_host_topology(lambda argv: topology)
        self.assertEqual((result.mode, result.nvlink_components), ("x_mode", ((0, 1, 2, 3),)))

    def test_local_worker_topology_fails_closed_for_unsupported_and_unavailable_graphs(self) -> None:
        matrices = [
            "\tGPU0\tGPU1\tGPU2\tGPU3\nGPU0\tX\tSYS\tSYS\tSYS\nGPU1\tSYS\tX\tSYS\tSYS\nGPU2\tSYS\tSYS\tX\tSYS\nGPU3\tSYS\tSYS\tSYS\tX\n",
            "\tGPU0\tGPU1\tGPU2\tGPU3\nGPU0\tX\tNV1\tSYS\tSYS\nGPU1\tNV1\tX\tNV2\tSYS\nGPU2\tSYS\tNV2\tX\tSYS\nGPU3\tSYS\tSYS\tSYS\tX\n",
            "\tGPU0\tGPU1\tGPU2\tGPU3\nGPU0\tX\tNV4\tSYS\tSYS\nGPU1\tNV4\tX\tSYS\tSYS\nGPU2\tSYS\tSYS\tX\tSYS\nGPU3\tSYS\tSYS\tSYS\tX\n",
            "\tGPU0\tGPU1\nGPU0\tX\tNV1\nGPU1\tNV1\tX\n",
        ]
        for topology in matrices:
            with self.subTest(topology=topology):
                result = classify_local_worker_host_topology(lambda argv, value=topology: value)
                self.assertEqual((result.mode, result.status), ("unknown", "unsupported"))
        malformed = classify_local_worker_host_topology(lambda argv: "not a topology matrix")
        self.assertEqual((malformed.mode, malformed.status), ("unknown", "unavailable"))
        failed = classify_local_worker_host_topology(
            lambda argv: (_ for _ in ()).throw(OSError("nvidia unavailable"))
        )
        self.assertEqual((failed.mode, failed.status), ("unknown", "unavailable"))


if __name__ == "__main__":
    unittest.main()
