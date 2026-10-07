from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task

from todo_orchestrator.workflow.capabilities import WorkflowCapabilityLocator
from todo_orchestrator.interfaces import interface_hash
from todo_orchestrator.workflow.protocol import WorkflowProtocol
from todo_orchestrator.workflow.service import WorkflowKernel
from todo_orchestrator.workflow.workspaces import WorkspaceService


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class WorkflowIsolatedClaimTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()
        git(self.repo.root, "config", "user.email", "workflow@example.invalid")
        git(self.repo.root, "config", "user.name", "Workflow Test")
        (self.repo.root / "shared.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        git(self.repo.root, "add", "shared.txt")
        git(self.repo.root, "commit", "-qm", "base")
        self.base = git(self.repo.root, "rev-parse", "HEAD")
        plan = base_plan([
            {"id": "ROOT", "kind": "epic", "title": "root", "objective": "root"},
            safe_task("A", "shared.txt", priority=20),
            safe_task("B", "shared.txt", priority=20),
            safe_task("INT", "integration", priority=1),
        ])
        plan["schema_version"] = 3
        plan["interfaces"] = [{
            "id": "IFACE-A",
            "owner_task_id": "A",
            "state": "draft",
            "version": "1",
            "contract_paths": ["src/a/interface.hh"],
        }]
        plan["runs"] = [{
            "id": "RUN", "root_task_id": "ROOT", "charter": {"objective": "isolated overlap"},
            "lanes": [
                {"id": "COORD", "role": "coordinator", "tasks": []},
                {"id": "A-LANE", "parent_lane_id": "COORD", "role": "implementer", "tasks": ["A"], "workspace": {"mode": "isolated_merge"}},
                {"id": "B-LANE", "parent_lane_id": "COORD", "role": "implementer", "tasks": ["B"], "workspace": {"mode": "isolated_merge"}},
                {"id": "INT-LANE", "parent_lane_id": "COORD", "role": "integrator", "tasks": ["INT"], "workspace": {"mode": "exclusive"}},
            ],
        }]
        self.repo.apply(plan)
        self.managed_temp = tempfile.TemporaryDirectory()
        self.managed = Path(self.managed_temp.name)
        self.workspaces = WorkspaceService(
            self.repo.service.db,
            managed_root=self.managed,
            repository_identity_resolver=lambda root: "repo",
        )
        self.locator_temp = tempfile.TemporaryDirectory()
        self.locator = WorkflowCapabilityLocator(Path(self.locator_temp.name))
        self.protocol = WorkflowProtocol(WorkflowKernel(locator=self.locator), self.locator)
        self.old_thread = os.environ.get("CODEX_THREAD_ID")

    def tearDown(self) -> None:
        if self.old_thread is None:
            os.environ.pop("CODEX_THREAD_ID", None)
        else:
            os.environ["CODEX_THREAD_ID"] = self.old_thread
        self.locator_temp.cleanup()
        self.managed_temp.cleanup()
        self.repo.close()

    def workspace(self, lane: str, mode: str) -> dict[str, object]:
        return self.workspaces.create_workspace(
            repository_root=self.repo.root,
            repository_identity="repo",
            run_id="RUN",
            lane_id=lane,
            mode=mode,
            base_commit=self.base,
            worktree_path=self.managed / lane.lower(),
            branch=f"test-{lane.lower()}",
            integration_task_id="INT",
        )

    def claim(self, thread: str, task: str) -> dict[str, object]:
        os.environ["CODEX_THREAD_ID"] = thread
        return self.protocol.next_task(repo_root=str(self.repo.root), task_id=task)

    def claim_from(self, thread: str, task: str, root: Path) -> dict[str, object]:
        os.environ["CODEX_THREAD_ID"] = thread
        return self.protocol.next_task(repo_root=str(root), task_id=task)

    def test_complete_managed_contract_allows_overlapping_first_class_claims(self) -> None:
        self.workspace("A-LANE", "isolated_merge")
        self.workspace("B-LANE", "isolated_merge")
        self.workspace("INT-LANE", "exclusive")
        first = self.claim("thread-a", "A")
        second = self.claim("thread-b", "B")
        self.assertEqual((first["status"], second["status"]), ("claimed", "claimed"))
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM claims WHERE state='active'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_dispatches WHERE state='active'").fetchone()[0], 2)

    def test_missing_integrator_destination_keeps_overlap_blocked(self) -> None:
        self.workspace("A-LANE", "isolated_merge")
        self.workspace("B-LANE", "isolated_merge")
        first = self.claim("thread-a", "A")
        second = self.claim("thread-b", "B")
        self.assertEqual(first["status"], "claimed")
        self.assertEqual(second["status"], "idle")
        with self.repo.service.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM claims WHERE state='active'").fetchone()[0], 1)

    def test_capability_resolves_its_exact_dispatch_worktree(self) -> None:
        first_workspace = self.workspace("A-LANE", "isolated_merge")
        second_workspace = self.workspace("B-LANE", "isolated_merge")
        self.workspace("INT-LANE", "exclusive")
        first_root = Path(str(first_workspace["worktree_path"])).resolve()
        second_root = Path(str(second_workspace["worktree_path"])).resolve()
        for root in (first_root, second_root):
            control = root / ".todo-orchestrator"
            control.mkdir(exist_ok=True)
            (control / "project.json").write_text(
                (self.repo.root / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        first = self.claim_from("thread-a", "A", first_root)
        second = self.claim_from("thread-b", "B", second_root)

        first_capability = self.locator.resolve(
            str(first["workflow_handle"]), required_operation="inspect_task"
        )
        second_capability = self.locator.resolve(
            str(second["workflow_handle"]), required_operation="inspect_task"
        )
        self.assertEqual(self.protocol.port._resolve_service(first_capability).paths.repo_root, first_root)
        self.assertEqual(self.protocol.port._resolve_service(second_capability).paths.repo_root, second_root)

    def test_shared_worktree_locator_hint_never_opens_the_hint_writable(self) -> None:
        first_workspace = self.workspace("A-LANE", "isolated_merge")
        second_workspace = self.workspace("B-LANE", "isolated_merge")
        self.workspace("INT-LANE", "exclusive")
        first_root = Path(str(first_workspace["worktree_path"])).resolve()
        sibling_root = Path(str(second_workspace["worktree_path"])).resolve()
        for root in (first_root, sibling_root):
            control = root / ".todo-orchestrator"
            control.mkdir(exist_ok=True)
            (control / "project.json").write_text(
                (self.repo.root / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        claimed = self.claim_from("thread-a", "A", first_root)
        self.locator.register(str(claimed["workflow_handle"]), sibling_root)
        capability = self.locator.resolve(str(claimed["workflow_handle"]), required_operation="inspect_task")
        opened = []
        original = self.protocol.port.service_factory
        def recording(root):
            opened.append(Path(root).resolve())
            return original(root)
        self.protocol.port.service_factory = recording
        self.assertEqual(self.protocol.port._resolve_service(capability).paths.repo_root, first_root)
        self.assertTrue(opened)
        self.assertEqual(set(opened), {first_root})

    def test_interface_publication_hashes_exact_dispatch_worktree(self) -> None:
        producer = self.workspace("A-LANE", "isolated_merge")
        self.workspace("B-LANE", "isolated_merge")
        self.workspace("INT-LANE", "exclusive")
        producer_root = Path(str(producer["worktree_path"])).resolve()
        contract = producer_root / "src" / "a" / "interface.hh"
        contract.parent.mkdir(parents=True, exist_ok=True)
        contract.write_text("#pragma once\n", encoding="utf-8")
        digest, _ = interface_hash(producer_root, ["src/a/interface.hh"])

        claimed = self.claim("thread-a", "A")
        published = self.protocol.coordinate_task(
            workflow_handle=str(claimed["workflow_handle"]),
            action="publish_interface",
            payload={"interface_id": "IFACE-A", "version": "1", "content_hash": digest},
        )

        self.assertEqual(published["interface_id"], "IFACE-A")
        self.assertEqual(published["content_hash"], digest)
        with self.repo.service.db.read() as conn:
            row = conn.execute("SELECT state,content_hash FROM interfaces WHERE id='IFACE-A'").fetchone()
        self.assertEqual((row["state"], row["content_hash"]), ("frozen", digest))

    def test_validator_lane_can_own_integration_destination(self) -> None:
        producer = self.workspace("B-LANE", "isolated_merge")
        self.workspace("INT-LANE", "exclusive")
        with self.repo.service.db.connect() as conn:
            conn.execute("UPDATE workflow_lanes SET role='validator' WHERE id='INT-LANE'")
        claimed = self.claim("thread-b", "B")
        producer_root = Path(str(producer["worktree_path"])).resolve()
        (producer_root / "shared.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        git(producer_root, "add", "shared.txt")
        git(producer_root, "commit", "-qm", "producer change")

        completed = self.protocol.finish_task(
            workflow_handle=str(claimed["workflow_handle"]),
            action="complete",
            disposition="implemented",
            note="validator-owned integration destination",
        )

        self.assertEqual(completed["status"], "idle")
        self.assertEqual(completed["integrator_lane_id"], "INT-LANE")


if __name__ == "__main__":
    unittest.main()
