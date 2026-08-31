from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILLS = Path("/home/tumlinson/.agents/skills")
TODO_PACKAGE = SKILLS / "todo-orchestrator"
if str(TODO_PACKAGE) not in sys.path:
    sys.path.insert(0, str(TODO_PACKAGE))

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProposalEnvelope, VersionedPrecondition
from project_control.mutation import MutationRejected, apply_proposal, validate_native_plan
from project_control.snapshot import SnapshotBuilder
from project_control.workflow_binding import reset_runtime_for_testing, todo_read_port_factory


def run(argv: list[str], root: Path) -> str:
    return subprocess.run(argv, cwd=root, check=True, text=True, capture_output=True).stdout


@unittest.skipUnless((TODO_PACKAGE / "todo_orchestrator" / "service.py").is_file(), "Todo runtime unavailable")
class PlanApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_runtime_for_testing()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        run(["git", "init", "-b", "main"], self.root)
        run(["git", "config", "user.email", "tests@example.invalid"], self.root)
        run(["git", "config", "user.name", "Tests"], self.root)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(
            ".todo-orchestrator/\ntodos/\ntodos.md\ntodo-status.md\n", encoding="utf-8"
        )
        run(["git", "add", "README.md", ".gitignore"], self.root)
        run(["git", "commit", "-m", "fixture"], self.root)
        from todo_orchestrator.service import Service

        Service.bootstrap(self.root, "Fixture")
        self.config = ProjectControlConfig(
            skills_root=SKILLS,
            workspaces={
                "demo": WorkspaceConfig(
                    authority_repository="source",
                    repositories={"source": RepositoryConfig(root=self.root)},
                )
            },
        )
        self.environment = dict(os.environ, PROJECT_CONTROL_SKILLS_ROOT=str(SKILLS))
        self.builder = SnapshotBuilder(
            self.config,
            todo_read_port_factory=todo_read_port_factory(self.environment),
        )

    def tearDown(self) -> None:
        reset_runtime_for_testing()
        self.temporary.cleanup()

    def plan(self, task_id: str = "TASK-1") -> dict[str, object]:
        return {
            "schema_version": 2,
            "project": {"name": "Fixture"},
            "invariants": [],
            "decisions": [],
            "locks": [],
            "interfaces": [],
            "barriers": [],
            "resource_classes": [],
            "tasks": [{"id": task_id, "title": "Apply fixture task", "objective": "Exercise plan apply"}],
        }

    def proposal(self, plan: dict[str, object] | None = None) -> ProposalEnvelope:
        snapshot = self.builder.build("demo")
        return ProposalEnvelope.create(
            intent="Apply a native Todo plan",
            proposed_change=plan if plan is not None else self.plan(),
            observation_preconditions=snapshot.observation_preconditions(),
            created_at=snapshot.observed_at,
        )

    def revision(self) -> int:
        from todo_orchestrator.service import Service

        return int(Service(self.root, read_only=True).status()["project_revision"])

    def test_valid_proposal_applies_and_changes_revision(self) -> None:
        proposal = self.proposal()
        before = self.revision()
        result = apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["before_revision"], before)
        self.assertGreater(result["after_revision"], before)
        self.assertEqual(result["applied_add"], ["TASK-1"])

    def test_invalid_proposal_digest_rejects(self) -> None:
        value = self.proposal().model_dump(mode="json")
        value["deterministic_digest"] = "0" * 64
        before = self.revision()
        with self.assertRaisesRegex(MutationRejected, "ProposalEnvelope is invalid"):
            apply_proposal(self.config, "demo", value, snapshot_builder=self.builder)
        self.assertEqual(self.revision(), before)

    def test_stale_todo_revision_rejects(self) -> None:
        proposal = self.proposal()
        stale = proposal.observation_preconditions.model_copy(
            update={"todo_revision": proposal.observation_preconditions.todo_revision - 1}
        )
        proposal = ProposalEnvelope.create(intent=proposal.intent, proposed_change=proposal.proposed_change, observation_preconditions=stale)
        with self.assertRaisesRegex(MutationRejected, "stale"):
            apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)

    def test_stale_repository_commit_rejects(self) -> None:
        proposal = self.proposal()
        stale = proposal.observation_preconditions.model_copy(update={"repository_commits": {"source": "f" * 40}})
        proposal = ProposalEnvelope.create(intent=proposal.intent, proposed_change=proposal.proposed_change, observation_preconditions=stale)
        with self.assertRaisesRegex(MutationRejected, "stale"):
            apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)

    def test_stale_worktree_fingerprint_rejects(self) -> None:
        proposal = self.proposal()
        worktrees = {
            key: value.model_copy(update={"working_tree_fingerprint": "stale"})
            for key, value in proposal.observation_preconditions.worktrees.items()
        }
        self.assertTrue(worktrees)
        stale = proposal.observation_preconditions.model_copy(update={"worktrees": worktrees})
        proposal = ProposalEnvelope.create(intent=proposal.intent, proposed_change=proposal.proposed_change, observation_preconditions=stale)
        with self.assertRaisesRegex(MutationRejected, "stale"):
            apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)

    def test_stale_interface_and_context_preconditions_reject(self) -> None:
        proposal = self.proposal()
        stale = proposal.observation_preconditions.model_copy(
            update={
                "interfaces": {"api": VersionedPrecondition(version=1, content_hash="old")},
                "context_fragments": {"brief": VersionedPrecondition(version=1, content_hash="old")},
            }
        )
        proposal = ProposalEnvelope.create(intent=proposal.intent, proposed_change=proposal.proposed_change, observation_preconditions=stale)
        with self.assertRaises(MutationRejected) as caught:
            apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        fields = {item["field"] for item in caught.exception.details["mismatches"]}
        self.assertIn("interfaces.api", fields)
        self.assertIn("context_fragments.brief", fields)

    def test_invalid_todo_plan_rejects_without_mutation(self) -> None:
        proposal = self.proposal({"schema_version": 2, "tasks": [{"id": "BROKEN"}]})
        before = self.revision()
        with self.assertRaises(MutationRejected) as caught:
            apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        self.assertEqual(caught.exception.code, "plan_validation_failed")
        self.assertEqual(self.revision(), before)

    def test_noop_plan_does_not_enter_apply(self) -> None:
        proposal = self.proposal({**self.plan(), "tasks": []})
        before = self.revision()
        with patch("project_control.mutation._apply_once") as apply_once:
            result = apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        apply_once.assert_not_called()
        self.assertEqual(result["status"], "noop")
        self.assertEqual(self.revision(), before)

    def test_project_uuid_remains_unchanged(self) -> None:
        proposal = self.proposal()
        before = self.builder.build("demo").project_uuid
        result = apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        self.assertEqual(result["project_uuid"], before)
        self.assertEqual(self.builder.build("demo").project_uuid, before)

    def test_source_repository_files_remain_unchanged(self) -> None:
        readme = self.root / "README.md"
        before = hashlib.sha256(readme.read_bytes()).hexdigest()
        apply_proposal(self.config, "demo", self.proposal(), snapshot_builder=self.builder)
        self.assertEqual(hashlib.sha256(readme.read_bytes()).hexdigest(), before)

    def test_failure_does_not_retry(self) -> None:
        proposal = self.proposal()
        error = MutationRejected("transaction_failed", "failed once")
        with patch("project_control.mutation._apply_once", side_effect=error) as apply_once:
            with self.assertRaises(MutationRejected):
                apply_proposal(self.config, "demo", proposal, snapshot_builder=self.builder)
        apply_once.assert_called_once()

    def test_receipt_is_bounded_provenance(self) -> None:
        result = apply_proposal(self.config, "demo", self.proposal(), snapshot_builder=self.builder)
        self.assertLess(len(str(result)), 16_384)
        self.assertEqual(
            {
                "status", "proposal_digest", "plan_digest", "project_uuid", "before_revision",
                "after_revision", "would_add", "would_modify", "applied_add", "applied_modify",
                "warnings", "todo_result", "current_observation_preconditions",
            },
            set(result),
        )

    def test_validate_uses_same_todo_backend_without_mutation(self) -> None:
        before = self.revision()
        result = validate_native_plan(self.config, "demo", self.plan(), snapshot_builder=self.builder)
        self.assertTrue(result["valid"])
        self.assertEqual(result["would_add"], ["TASK-1"])
        self.assertEqual(self.revision(), before)


if __name__ == "__main__":
    unittest.main()
