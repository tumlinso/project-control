from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from todo_orchestrator.db import Database
from todo_orchestrator.config import utc_now
from todo_orchestrator.claims import claim_best
from todo_orchestrator.gates import run_gate
from todo_orchestrator.models import ProjectPaths, TodoError
from todo_orchestrator.sessions import create_session
from todo_orchestrator.workflow.workspaces import WorkspaceService


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


class WorkflowWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "workflow@example.invalid")
        git(self.repo, "config", "user.name", "Workflow Test")
        (self.repo / "shared.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        git(self.repo, "add", "shared.txt")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")

        self.db = Database(self.root / "state.sqlite3")
        self.db.initialize({"project_uuid": "project-1", "project_name": "fixture"})

        def seed(conn, revision):
            now = "2026-08-27T00:00:00Z"
            for task_id in ("ROOT", "IMPL", "IMPL2", "INTEGRATE"):
                conn.execute(
                    "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) VALUES(?,?,?,?,?,?,?)",
                    (task_id, "workstream", task_id, "planned", now, now, revision),
                )
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,status,created_at,updated_at,revision) VALUES(?,?,?,?,?,?)",
                ("RUN", "ROOT", "active", now, now, revision),
            )
            lanes = (
                ("COORD", None, "coordinator", "exclusive"),
                ("PRODUCER", "COORD", "implementer", "isolated_merge"),
                ("PRODUCER2", "COORD", "implementer", "isolated_merge"),
                ("INTEGRATOR", "COORD", "integrator", "exclusive"),
                ("NOT_INTEGRATOR", "COORD", "implementer", "exclusive"),
                ("READER", "COORD", "validator", "read_shared"),
            )
            for lane_id, parent, role, mode in lanes:
                conn.execute(
                    """INSERT INTO workflow_lanes(
                         id,run_id,parent_lane_id,role,state,workspace_mode,created_at,updated_at,revision)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (lane_id, "RUN", parent, role, "ready", mode, now, now, revision),
                )
            conn.executemany(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES(?,?,?,'queued',?,?)",
                [
                    ("PRODUCER", 0, "IMPL", now, revision),
                    ("PRODUCER2", 0, "IMPL2", now, revision),
                    ("INTEGRATOR", 0, "INTEGRATE", now, revision),
                    ("NOT_INTEGRATOR", 0, "INTEGRATE", now, revision),
                ],
            )
            conn.execute(
                "INSERT INTO gates(id,task_id,type,config_json,required,status,valid,revision) "
                "VALUES('POST','INTEGRATE','manual','{}',1,'pending',0,?)",
                (revision,),
            )

        self.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="RUN",
            event_type="fixture_seeded",
            payload={},
            operation=seed,
        )
        self.managed = self.root / "managed"
        self.service = WorkspaceService(
            self.db,
            managed_root=self.managed,
            repository_identity_resolver=lambda root: "repo-identity",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_destination(self, *, lane: str = "INTEGRATOR") -> dict[str, object]:
        return self.service.create_workspace(
            repository_root=self.repo,
            repository_identity="repo-identity",
            run_id="RUN",
            lane_id=lane,
            mode="exclusive",
            base_commit=self.base,
            worktree_path=self.managed / lane.lower(),
            branch=f"test-{lane.lower()}",
            integration_task_id="INTEGRATE",
        )

    def create_producer(self, *, lane: str = "PRODUCER", base: str | None = None) -> dict[str, object]:
        return self.service.create_workspace(
            repository_root=self.repo,
            repository_identity="repo-identity",
            run_id="RUN",
            lane_id=lane,
            mode="isolated_merge",
            base_commit=base or self.base,
            worktree_path=self.managed / lane.lower(),
            branch=f"test-{lane.lower()}",
            integration_task_id="INTEGRATE",
        )

    def producer_commit(self, workspace: dict[str, object], content: str) -> str:
        path = Path(str(workspace["worktree_path"]))
        (path / "shared.txt").write_text(content, encoding="utf-8")
        git(path, "add", "shared.txt")
        git(path, "commit", "-qm", "producer change")
        return git(path, "rev-parse", "HEAD")

    def assert_code(self, expected: str, call) -> None:
        with self.assertRaises(TodoError) as caught:
            call()
        self.assertEqual(caught.exception.code, expected)

    def gate_evidence(self, status: str) -> str:
        evidence_id = f"POST-{status}-{self.db.revision()}"
        valid = int(status == "passed")
        with self.db.read() as conn:
            queue = conn.execute(
                "SELECT q.merge_result_json,d.worktree_path FROM workflow_integration_queue q "
                "JOIN workflow_workspaces d ON d.run_id=q.run_id AND d.lane_id=q.integrator_lane_id "
                "WHERE q.state='awaiting_gates'"
            ).fetchone()
        applied = json.loads(queue["merge_result_json"])
        fingerprint = f"fingerprint-{evidence_id}"
        metadata = json.dumps({
            "input_fingerprint": fingerprint,
            "started_revision": self.db.revision(),
            "workspace_path": str(Path(queue["worktree_path"]).resolve()),
            "source_identity": applied["source_identity"],
        }, sort_keys=True)
        def operation(conn, revision):
            conn.execute(
                "UPDATE gates SET status=?,valid=?,input_fingerprint=?,revision=? WHERE id='POST'",
                (status, valid, fingerprint, revision),
            )
            conn.execute(
                "INSERT INTO evidence(id,gate_id,kind,status,metadata_json,created_at,revision) "
                "VALUES(?,'POST','gate',?,?,?,?)",
                (evidence_id, status, metadata, utc_now(), revision),
            )
        self.db.mutate(actor_session_id=None, entity_type="gate", entity_id="POST", event_type="test_gate", payload={}, operation=operation)
        return evidence_id

    def wave_gate_evidence(self, *, source_identity: str, status: str = "passed") -> str:
        """Authoritative evidence fixture for the one cumulative wave source."""
        evidence_id = f"WAVE-{status}-{self.db.revision()}"
        fingerprint = f"fingerprint-{evidence_id}"
        with self.db.read() as conn:
            destination = conn.execute(
                "SELECT worktree_path FROM workflow_workspaces WHERE lane_id='INTEGRATOR'"
            ).fetchone()
        metadata = json.dumps({
            "input_fingerprint": fingerprint,
            "started_revision": self.db.revision(),
            "workspace_path": str(Path(destination["worktree_path"]).resolve()),
            "source_identity": source_identity,
        }, sort_keys=True)
        def operation(conn, revision):
            conn.execute("UPDATE gates SET status=?,valid=?,input_fingerprint=?,revision=? WHERE id='POST'",
                         (status, int(status == "passed"), fingerprint, revision))
            conn.execute("INSERT INTO evidence(id,gate_id,kind,status,metadata_json,created_at,revision) VALUES(?,'POST','gate',?,?,?,?)",
                         (evidence_id, status, metadata, utc_now(), revision))
        self.db.mutate(actor_session_id=None, entity_type="gate", entity_id="POST", event_type="wave_gate", payload={}, operation=operation)
        return evidence_id

    def test_declared_batch_wave_applies_and_finalizes_members_atomically(self) -> None:
        destination = self.create_destination()
        first, second = self.create_producer(), self.create_producer(lane="PRODUCER2")
        first_commit = self.producer_commit(first, "alpha first\nbeta\ngamma\n")
        second_commit = self.producer_commit(second, "alpha\nbeta\ngamma second\n")
        first_artifact = self.service.publish_artifact(workspace_id=str(first["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=first_commit)
        second_artifact = self.service.publish_artifact(workspace_id=str(second["workspace_id"]), task_id="IMPL2", kind="commit", artifact_ref=second_commit)
        q1 = self.service.enqueue_artifact(artifact_id=str(first_artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        q2 = self.service.enqueue_artifact(artifact_id=str(second_artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        wave = self.service.declare_integration_wave(queue_ids=[str(q1["queue_id"]), str(q2["queue_id"])])
        applied = self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        with self.db.read() as conn:
            states = [row[0] for row in conn.execute("SELECT state FROM workflow_integration_queue ORDER BY position")]
        self.assertEqual(states, ["applied_pending_wave", "applied_pending_wave"])
        accepted = self.service.record_integration_wave_gates(
            integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]),
            gate_results=[{"gate_id": "POST", "evidence_id": self.wave_gate_evidence(source_identity=str(applied["source_identity"]))}],
        )
        self.assertEqual(accepted["state"], "integrated")
        self.assertEqual(len(accepted["queue_ids"]), 2)
        self.assertEqual(git(Path(str(destination["worktree_path"])), "status", "--porcelain=v1"), "")

    def test_root_wave_receipt_preserves_verified_inherited_base(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "inherited base consumer\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        )
        inherited = {
            "task_id": "PREVIOUS-INTEGRATE", "completion_commit": self.base,
            "content_hash": "previous-wave-hash", "workspace_id": "PREVIOUS-DESTINATION",
        }
        wave = self.service.declare_and_adopt_integration_wave(
            queue_ids=[str(queued["queue_id"])], inherited_base=inherited,
        )
        self.assertEqual(wave["inherited_base"], inherited)
        with self.db.read() as conn:
            receipt = json.loads(conn.execute(
                "SELECT merge_result_json FROM workflow_integration_queue WHERE id=?", (queued["queue_id"],)
            ).fetchone()[0])
        self.assertEqual(receipt["inherited_base"], inherited)

    def test_root_wave_rejects_inherited_base_that_does_not_match_artifact_base(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "wrong inherited base\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        )
        self.assert_code("integration_inherited_base_mismatch", lambda: self.service.declare_and_adopt_integration_wave(
            queue_ids=[str(queued["queue_id"])],
            inherited_base={
                "task_id": "PREVIOUS-INTEGRATE", "completion_commit": "not-the-producer-base",
                "content_hash": "previous-wave-hash", "workspace_id": "PREVIOUS-DESTINATION",
            },
        ))

    def test_wave_rejects_late_member_and_audits_gate_failure_adoption(self) -> None:
        self.create_destination()
        first, second = self.create_producer(), self.create_producer(lane="PRODUCER2")
        first_commit = self.producer_commit(first, "alpha first\nbeta\ngamma\n")
        second_commit = self.producer_commit(second, "alpha\nbeta\ngamma second\n")
        a1 = self.service.publish_artifact(workspace_id=str(first["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=first_commit)
        a2 = self.service.publish_artifact(workspace_id=str(second["workspace_id"]), task_id="IMPL2", kind="commit", artifact_ref=second_commit)
        q1 = self.service.enqueue_artifact(artifact_id=str(a1["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        q2 = self.service.enqueue_artifact(artifact_id=str(a2["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        self.service.apply_next(queue_id=str(q1["queue_id"]))
        self.service.record_post_merge_gates(queue_id=str(q1["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("failed")}])
        # Simulate the pre-WF2 receipt shape: source identity existed but the
        # old finalizer discarded pre-apply/destination provenance.  The root
        # must attest it in the same transaction as declaration/adoption.
        def legacy_receipt(conn, revision):
            row = conn.execute("SELECT merge_result_json FROM workflow_integration_queue WHERE id=?", (q1["queue_id"],)).fetchone()
            data = json.loads(row[0]); data.pop("pre_apply_head", None); data.pop("destination_worktree", None)
            conn.execute("UPDATE workflow_integration_queue SET merge_result_json=? WHERE id=?", (json.dumps(data), q1["queue_id"]))
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="legacy_receipt", payload={}, operation=legacy_receipt)
        self.assert_code("integration_apply_provenance_missing", lambda: self.service.declare_and_adopt_integration_wave(queue_ids=[str(q1["queue_id"]), str(q2["queue_id"])]))
        wave = self.service.declare_and_adopt_integration_wave(
            queue_ids=[str(q1["queue_id"]), str(q2["queue_id"])], legacy_provenance_reason="root audited legacy I00 receipt",
        )
        self.assertTrue(wave["legacy_provenance_adopted"])
        # A newly injected nonmember invalidates the immutable declaration.
        def late(conn, revision):
            conn.execute("UPDATE workflow_integration_queue SET state='queued' WHERE id=?", (q2["queue_id"],))
            conn.execute("INSERT INTO workflow_integration_queue(id,run_id,integration_task_id,integrator_lane_id,patch_artifact_id,position,state,conflict_json,merge_result_json,created_at,updated_at) VALUES('LATE','RUN','INTEGRATE','INTEGRATOR',?,2,'queued','{}','{}',?,?)", (a2["artifact_id"], utc_now(), utc_now()))
        # Unique artifact forbids a real late row; simulate the immutable-set
        # check by changing the declared member list instead.
        def tamper(conn, revision):
            row = conn.execute("SELECT merge_result_json FROM workflow_integration_queue WHERE id=?", (q2["queue_id"],)).fetchone()
            data = json.loads(row[0]); data["wave_members"] = [str(q1["queue_id"])]
            conn.execute("UPDATE workflow_integration_queue SET merge_result_json=? WHERE id=?", (json.dumps(data), q2["queue_id"]))
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="wave_tamper", payload={}, operation=tamper)
        self.assert_code("integration_wave_changed", lambda: self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"])))

    def test_wave_finalization_failure_retries_without_git_rewrite(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha batch\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        queued = self.service.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        wave = self.service.declare_and_adopt_integration_wave(queue_ids=[str(queued["queue_id"])])
        applied = self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        before = git(Path(str(destination["worktree_path"])), "rev-parse", "HEAD")
        def fail_finalization(conn, revision):
            conn.execute("UPDATE workflow_integration_queue SET state='finalization_failed' WHERE id=?", (queued["queue_id"],))
            conn.execute("UPDATE workflow_workspaces SET state='finalization_failed' WHERE lane_id='INTEGRATOR'")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="wave_finalization_failed", payload={}, operation=fail_finalization)
        retried = self.service.retry_declared_integration_wave_finalization(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        self.assertEqual(retried["source_identity"], applied["source_identity"])
        self.assertEqual(git(Path(str(destination["worktree_path"])), "rev-parse", "HEAD"), before)

    def test_clean_wave_apply_failure_can_retry_without_git_recovery(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha retry\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        queued = self.service.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        wave = self.service.declare_and_adopt_integration_wave(queue_ids=[str(queued["queue_id"])])
        def fail_apply(conn, revision):
            conn.execute("UPDATE workflow_integration_queue SET state='apply_failed',conflict_json=? WHERE id=?", (json.dumps({"code": "integration_workspace_dirty", "preserved": True}), queued["queue_id"]))
            conn.execute("UPDATE workflow_workspaces SET state='apply_failed' WHERE lane_id='INTEGRATOR'")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="wave_apply_failed", payload={}, operation=fail_apply)
        retry = self.service.retry_declared_integration_wave_apply(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        self.assertEqual(retry["state"], "queued")
        applied = self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        self.assertEqual(applied["state"], "applied_pending_wave")

    def test_interrupted_wave_finalization_recovery_preserves_pre_and_post_freeze_source(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha recovery\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        queued = self.service.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        wave = self.service.declare_and_adopt_integration_wave(queue_ids=[str(queued["queue_id"])])
        applied = self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        target = Path(str(destination["worktree_path"]))
        def finalizing(conn, revision):
            conn.execute("UPDATE workflow_integration_queue SET state='finalizing' WHERE id=?", (queued["queue_id"],))
            conn.execute("UPDATE workflow_workspaces SET state='finalizing' WHERE lane_id='INTEGRATOR'")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="crash_pre_freeze", payload={}, operation=finalizing)
        recovered = self.service.recover_interrupted_integration_wave_finalization(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]), reason="root pre-freeze crash")
        self.assertEqual(recovered["state"], "finalization_failed")
        self.assert_code("integration_wave_not_finalizing", lambda: self.service.recover_interrupted_integration_wave_finalization(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]), reason="repeat"))
        # A frozen HEAD carries the identical material source and is recoverable
        # without any Git rewrite.
        frozen = self.service._freeze_integration_commit(target, base_commit=self.base, queue_id=str(wave["wave_id"]), source_identity=str(applied["source_identity"]))
        self.service._advance_integration_head(target, frozen)
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="crash_post_freeze", payload={}, operation=finalizing)
        recovered = self.service.recover_interrupted_integration_wave_finalization(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]), reason="root post-freeze crash")
        self.assertEqual(recovered["source_identity"], applied["source_identity"])

    def test_interrupted_wave_recovery_rejects_changed_source(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha changed\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        queued = self.service.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        wave = self.service.declare_and_adopt_integration_wave(queue_ids=[str(queued["queue_id"])])
        self.service.apply_declared_integration_wave(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]))
        def finalizing(conn, revision):
            conn.execute("UPDATE workflow_integration_queue SET state='finalizing' WHERE id=?", (queued["queue_id"],))
            conn.execute("UPDATE workflow_workspaces SET state='finalizing' WHERE lane_id='INTEGRATOR'")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="crash_source_changed", payload={}, operation=finalizing)
        (Path(str(destination["worktree_path"])) / "shared.txt").write_text("untrusted mutation\n", encoding="utf-8")
        self.assert_code("integration_source_changed", lambda: self.service.recover_interrupted_integration_wave_finalization(integration_task_id="INTEGRATE", wave_id=str(wave["wave_id"]), reason="must preserve mutation"))

    def test_modes_materialize_only_managed_first_class_workspaces(self) -> None:
        destination = self.create_destination()
        self.assertTrue(Path(str(destination["worktree_path"])).is_dir())
        shared = self.service.create_workspace(
            repository_root=self.repo,
            repository_identity="repo-identity",
            run_id="RUN",
            lane_id="READER",
            mode="read_shared",
            base_commit=self.base,
            worktree_path=None,
            branch=None,
            integration_task_id=None,
        )
        self.assertIsNone(shared["worktree_path"])
        self.assert_code(
            "local_child_workspace_forbidden",
            lambda: self.service.create_workspace(
                repository_root=self.repo,
                repository_identity="repo-identity",
                run_id="RUN",
                lane_id="child-execution-id",
                mode="isolated_merge",
                base_commit=self.base,
                worktree_path=self.managed / "child",
                branch="child",
                integration_task_id="INTEGRATE",
                worker_class="child",
            ),
        )
        self.assertFalse((self.managed / "child").exists())

    def test_unmanaged_paths_and_mode_mismatch_are_rejected_before_git(self) -> None:
        insecure = WorkspaceService(self.db, managed_root=self.managed)
        self.assert_code(
            "repository_identity_resolver_required",
            lambda: insecure.create_workspace(
                repository_root=self.repo, repository_identity="caller-asserted", run_id="RUN",
                lane_id="PRODUCER", mode="isolated_merge", base_commit=self.base,
                worktree_path=self.managed / "unverified", branch="unverified",
                integration_task_id="INTEGRATE",
            ),
        )
        self.assert_code(
            "workspace_path_unmanaged",
            lambda: self.service.create_workspace(
                repository_root=self.repo,
                repository_identity="repo-identity",
                run_id="RUN",
                lane_id="PRODUCER",
                mode="isolated_merge",
                base_commit=self.base,
                worktree_path=self.root / "outside",
                branch="outside",
                integration_task_id="INTEGRATE",
            ),
        )
        enforcing = WorkspaceService(
            self.db,
            managed_root=self.managed,
            repository_identity_resolver=lambda root: "authoritative-repo",
        )
        self.assert_code(
            "repository_identity_mismatch",
            lambda: enforcing.create_workspace(
                repository_root=self.repo, repository_identity="caller-asserted", run_id="RUN",
                lane_id="PRODUCER", mode="isolated_merge", base_commit=self.base,
                worktree_path=self.managed / "identity-mismatch", branch="identity-mismatch",
                integration_task_id="INTEGRATE",
            ),
        )
        self.assert_code(
            "lane_workspace_mode_mismatch",
            lambda: self.service.create_workspace(
                repository_root=self.repo,
                repository_identity="repo-identity",
                run_id="RUN",
                lane_id="PRODUCER",
                mode="exclusive",
                base_commit=self.base,
                worktree_path=self.managed / "wrong-mode",
                branch="wrong-mode",
                integration_task_id="INTEGRATE",
            ),
        )

    def test_exact_same_base_is_enforced_for_isolated_participants(self) -> None:
        self.create_producer()
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        git(self.repo, "add", "later.txt")
        git(self.repo, "commit", "-qm", "later base")
        later = git(self.repo, "rev-parse", "HEAD")
        self.assert_code("workspace_base_mismatch", lambda: self.create_producer(lane="PRODUCER2", base=later))
        self.assertFalse((self.managed / "producer2").exists())

    def test_clean_workspace_base_can_be_reconciled_after_prior_integration(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        (self.repo / "integrated.txt").write_text("prior wave\n", encoding="utf-8")
        git(self.repo, "add", "integrated.txt")
        git(self.repo, "commit", "-qm", "prior integration")
        integrated_base = git(self.repo, "rev-parse", "HEAD")
        git(Path(str(destination["worktree_path"])), "merge", "--ff-only", integrated_base)
        producer_path = Path(str(producer["worktree_path"]))
        git(producer_path, "merge", "--ff-only", integrated_base)
        commit = self.producer_commit(producer, "alpha\nbeta changed\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL",
            kind="commit", artifact_ref=commit,
        )

        def misorder_destination(conn, revision):
            now = "2026-08-27T01:00:00Z"
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                "VALUES('INTEGRATE2','workstream','INTEGRATE2','planned',?,?,?)",
                (now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('INTEGRATOR',1,'INTEGRATE2','queued',?,?)",
                (now, revision),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET integration_task_id='INTEGRATE2',state='active' WHERE id=?",
                (destination["workspace_id"],),
            )

        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="INTEGRATE2",
            event_type="fixture_misordered_destination", payload={}, operation=misorder_destination,
        )

        reconciled = self.service.reconcile_workspace_base(
            repository_root=self.repo,
            run_id="RUN",
            lane_id="PRODUCER",
            base_commit=integrated_base,
            reason="prior integration reached the canonical branch",
        )
        self.assertEqual(reconciled["base_commit"], integrated_base)
        self.assertEqual(len(reconciled["reconciled_workspaces"]), 1)
        self.assertEqual(reconciled["reconciled_workspaces"][0]["old_base_commit"], self.base)
        self.assertEqual(reconciled["repaired_integrator_destination"]["from_task_id"], "INTEGRATE2")
        self.assertEqual(reconciled["repaired_integrator_destination"]["integration_task_id"], "INTEGRATE")
        self.assertEqual(reconciled["repaired_integrator_destination"]["transition"], "restore_prior")
        self.assertEqual(reconciled["superseded_artifact_ids"], [artifact["artifact_id"]])
        with self.db.read() as conn:
            recorded = conn.execute(
                "SELECT base_commit,state,artifact_ref FROM workflow_workspaces WHERE id=?",
                (producer["workspace_id"],),
            ).fetchone()
            artifact_state = conn.execute(
                "SELECT state FROM workflow_patch_artifacts WHERE id=?",
                (artifact["artifact_id"],),
            ).fetchone()[0]
        self.assertEqual(recorded["base_commit"], integrated_base)
        self.assertEqual(recorded["state"], "active")
        self.assertIsNone(recorded["artifact_ref"])
        self.assertEqual(artifact_state, "superseded")

    def test_workspace_base_reconciliation_rejects_dirty_or_unrelated_history(self) -> None:
        producer = self.create_producer()
        producer_path = Path(str(producer["worktree_path"]))
        (producer_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        self.assert_code(
            "workspace_reconcile_dirty",
            lambda: self.service.reconcile_workspace_base(
                repository_root=self.repo, run_id="RUN", lane_id="PRODUCER",
                base_commit=self.base, reason="test dirty refusal",
            ),
        )

    def test_queued_artifact_retargets_after_identical_forward_base_merge(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        old_commit = self.producer_commit(producer, "alpha\nbeta retargeted\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL",
            kind="commit", artifact_ref=old_commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR",
            integration_task_id="INTEGRATE",
        )
        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="OLD-SUPERSEDED",
            event_type="fixture_old_artifact", payload={},
            operation=lambda conn, revision: conn.execute(
                "INSERT INTO workflow_patch_artifacts(id,workspace_id,task_id,kind,artifact_ref,"
                "content_hash,base_commit,created_at,state) VALUES('OLD-SUPERSEDED',?,"
                "'IMPL','commit',?,'old-hash',?,'2026-08-27T00:00:00Z','superseded')",
                (producer["workspace_id"], old_commit, self.base),
            ),
        )

        (self.repo / "upstream.txt").write_text("integrated base\n", encoding="utf-8")
        git(self.repo, "add", "upstream.txt")
        git(self.repo, "commit", "-qm", "integrated base")
        integrated_base = git(self.repo, "rev-parse", "HEAD")
        git(Path(str(destination["worktree_path"])), "merge", "--ff-only", integrated_base)
        producer_path = Path(str(producer["worktree_path"]))
        git(producer_path, "merge", "--no-edit", integrated_base)
        new_head = git(producer_path, "rev-parse", "HEAD")

        reconciled = self.service.reconcile_workspace_base(
            repository_root=self.repo, run_id="RUN", lane_id="PRODUCER",
            base_commit=integrated_base, reason="prior integration advanced the base",
        )
        self.assertEqual(reconciled["retargeted_artifacts"][0]["artifact_id"], artifact["artifact_id"])
        with self.db.read() as conn:
            stored_artifact = conn.execute(
                "SELECT artifact_ref,base_commit,state FROM workflow_patch_artifacts WHERE id=?",
                (artifact["artifact_id"],),
            ).fetchone()
            stored_workspace = conn.execute(
                "SELECT artifact_ref,base_commit,state FROM workflow_workspaces WHERE id=?",
                (producer["workspace_id"],),
            ).fetchone()
        self.assertEqual(dict(stored_artifact), {
            "artifact_ref": new_head, "base_commit": integrated_base, "state": "queued",
        })
        self.assertEqual(dict(stored_workspace), {
            "artifact_ref": new_head, "base_commit": integrated_base, "state": "queued",
        })
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(applied["state"], "awaiting_gates")

    def test_base_reconciliation_advances_completed_integrator_destination(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        old_commit = self.producer_commit(producer, "alpha\nbeta next wave\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL",
            kind="commit", artifact_ref=old_commit,
        )
        self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR",
            integration_task_id="INTEGRATE",
        )

        def seed_previous(conn, revision):
            now = "2026-08-27T01:00:00Z"
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                "VALUES('INTEGRATE0','workstream','INTEGRATE0','done',?,?,?)",
                (now, now, revision),
            )
            conn.execute("UPDATE workflow_lane_tasks SET position=1 WHERE lane_id='INTEGRATOR'")
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('INTEGRATOR',0,'INTEGRATE0','completed',?,?)",
                (now, revision),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET integration_task_id='INTEGRATE0',state='integrated' "
                "WHERE id=?",
                (destination["workspace_id"],),
            )

        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="INTEGRATE0",
            event_type="fixture_previous_integration", payload={}, operation=seed_previous,
        )
        (self.repo / "prior-wave.txt").write_text("complete\n", encoding="utf-8")
        git(self.repo, "add", "prior-wave.txt")
        git(self.repo, "commit", "-qm", "prior wave")
        integrated_base = git(self.repo, "rev-parse", "HEAD")
        git(Path(str(destination["worktree_path"])), "merge", "--ff-only", integrated_base)
        producer_path = Path(str(producer["worktree_path"]))
        git(producer_path, "merge", "--no-edit", integrated_base)

        reconciled = self.service.reconcile_workspace_base(
            repository_root=self.repo, run_id="RUN", lane_id="PRODUCER",
            base_commit=integrated_base, reason="advance after completed integration",
        )
        repaired = reconciled["repaired_integrator_destination"]
        self.assertEqual(repaired["from_task_id"], "INTEGRATE0")
        self.assertEqual(repaired["integration_task_id"], "INTEGRATE")
        self.assertEqual(repaired["transition"], "advance_next")
        with self.db.read() as conn:
            stored = conn.execute(
                "SELECT integration_task_id,base_commit,state FROM workflow_workspaces WHERE id=?",
                (destination["workspace_id"],),
            ).fetchone()
        self.assertEqual(dict(stored), {
            "integration_task_id": "INTEGRATE", "base_commit": integrated_base, "state": "active",
        })

    def test_clean_quarantined_participant_is_reactivated_by_group_reconciliation(self) -> None:
        producer = self.create_producer()

        def quarantine(conn, revision):
            conn.execute(
                "UPDATE workflow_workspaces SET state='quarantined' WHERE id=?",
                (producer["workspace_id"],),
            )

        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id=str(producer["workspace_id"]),
            event_type="fixture_workspace_quarantined", payload={}, operation=quarantine,
        )
        reconciled = self.service.reconcile_workspace_base(
            repository_root=self.repo, run_id="RUN", lane_id="PRODUCER",
            base_commit=self.base, reason="verified clean owner recovery",
        )
        self.assertEqual(reconciled["reconciled_workspaces"][0]["state"], "quarantined")
        with self.db.read() as conn:
            state = conn.execute(
                "SELECT state FROM workflow_workspaces WHERE id=?", (producer["workspace_id"],)
            ).fetchone()[0]
        self.assertEqual(state, "active")

    def test_commit_artifact_queue_merge_gates_and_explicit_cleanup_eligibility(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha changed\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        self.assert_code(
            "integrator_role_required",
            lambda: self.service.enqueue_artifact(
                artifact_id=str(artifact["artifact_id"]),
                integrator_lane_id="NOT_INTEGRATOR",
                integration_task_id="INTEGRATE",
            ),
        )
        self.assertEqual(len(str(artifact["diff_hash"])), 64)
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(applied["state"], "awaiting_gates")
        self.assertEqual((Path(str(destination["worktree_path"])) / "shared.txt").read_text(), "alpha changed\nbeta\ngamma\n")
        finished = self.service.record_post_merge_gates(
            queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}]
        )
        self.assertEqual(finished["state"], "integrated")
        eligible = self.service.mark_cleanup_eligible(workspace_id=str(producer["workspace_id"]))
        self.assertTrue(eligible["cleanup_eligible"])
        self.assertFalse(eligible["deleted"])
        self.assertTrue(Path(str(producer["worktree_path"])).exists())
        destination_eligible = self.service.mark_cleanup_eligible(workspace_id=str(destination["workspace_id"]))
        self.assertTrue(destination_eligible["cleanup_eligible"])
        self.assertTrue(Path(str(destination["worktree_path"])).exists())

    def test_integrated_producer_advances_wave_without_losing_history(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        source = Path(str(producer["worktree_path"]))
        first = self.producer_commit(producer, "alpha changed\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=first,
        )
        queue = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        )
        self.service.apply_next(queue_id=str(queue["queue_id"]))
        accepted = self.service.record_post_merge_gates(
            queue_id=str(queue["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}],
        )
        base = accepted["integrated_artifact"]["ref"]

        def add_wave(conn, revision):
            now = utc_now()
            for task in ("IMPL_NEXT", "INTEGRATE_NEXT"):
                conn.execute("INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) VALUES(?,?,?,'planned',?,?,?)",
                             (task, "workstream", task, now, now, revision))
            for lane, task in (("PRODUCER", "IMPL_NEXT"), ("INTEGRATOR", "INTEGRATE_NEXT")):
                conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES(?,1,?,'queued',?,?)",
                             (lane, task, now, revision))
            conn.execute("UPDATE tasks SET status='done' WHERE id IN ('IMPL','INTEGRATE')")
            conn.execute("UPDATE workflow_lane_tasks SET state='completed' WHERE task_id IN ('IMPL','INTEGRATE')")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="wave_seeded", payload={}, operation=add_wave)
        args = dict(repository_root=self.repo, workspace_id=str(producer["workspace_id"]),
                    base_commit=base, integration_task_id="INTEGRATE_NEXT", reason="next accepted program milestone")
        revision = self.db.revision()
        self.assert_code("workspace_wave_source_unready", lambda: self.service.advance_producer_wave(**args))
        self.assertEqual(self.db.revision(), revision)
        git(source, "merge", "--no-edit", base)
        head = git(source, "rev-parse", "HEAD")
        self.assertNotEqual(head, base)  # No reset needed after patch-based integration.
        self.assert_code("workspace_wave_base_unqualified", lambda: self.service.advance_producer_wave(**{**args, "base_commit": self.base}))
        self.assert_code("workspace_wave_order_invalid", lambda: self.service.advance_producer_wave(**{**args, "integration_task_id": "INTEGRATE"}))
        (source / "unpublished.txt").write_text("preserve me\n")
        self.assert_code("workspace_wave_source_unready", lambda: self.service.advance_producer_wave(**args))
        self.assertEqual((source / "unpublished.txt").read_text(), "preserve me\n")
        (source / "unpublished.txt").unlink()  # Test-owned negative fixture only.
        def mutate_fixture(sql, values=()):
            self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="negative_fixture",
                           payload={}, operation=lambda conn, rev: conn.execute(sql, values).rowcount)
        mutate_fixture("UPDATE workflow_lanes SET state='active' WHERE id='PRODUCER'")
        self.assert_code("workspace_wave_lane_busy", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE workflow_lanes SET state='ready' WHERE id='PRODUCER'")
        mutate_fixture("UPDATE workflow_runs SET status='paused' WHERE id='RUN'")
        self.assert_code("workspace_wave_run_inactive", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE workflow_runs SET status='active' WHERE id='RUN'")
        def orphan_claim(conn, revision):
            session, _ = create_session(conn, self.repo)
            now = utc_now()
            conn.execute("INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                         "VALUES('orphan','IMPL_NEXT',?,'fixture-only','active',?,?,?,?)", (session["agent_id"], now, now, now, revision))
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="orphan_claim_fixture", payload={}, operation=orphan_claim)
        self.assert_code("workspace_wave_lane_busy", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE claims SET state='released' WHERE id='orphan'")
        mutate_fixture("UPDATE tasks SET status='planned' WHERE id='INTEGRATE'")
        self.assert_code("workspace_wave_order_invalid", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE tasks SET status='done' WHERE id='INTEGRATE'")
        mutate_fixture("UPDATE workflow_lane_tasks SET state='completed' WHERE task_id='IMPL_NEXT'")
        self.assert_code("workspace_wave_no_remaining_task", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE workflow_lane_tasks SET state='queued' WHERE task_id='IMPL_NEXT'")
        mutate_fixture("UPDATE workflow_patch_artifacts SET state='pending' WHERE id=?", (artifact["artifact_id"],))
        self.assert_code("workspace_wave_pending_artifacts", lambda: self.service.advance_producer_wave(**args))
        mutate_fixture("UPDATE workflow_patch_artifacts SET state='integrated' WHERE id=?", (artifact["artifact_id"],))
        # A terminal artifact alone is insufficient while its queue is unresolved.
        mutate_fixture("UPDATE workflow_integration_queue SET state='gate_failed' WHERE id=?", (queue["queue_id"],))
        unchanged_revision = self.db.revision()
        self.assert_code("workspace_wave_pending_artifacts", lambda: self.service.advance_producer_wave(**args))
        self.assertEqual(self.db.revision(), unchanged_revision)
        self.assertEqual(git(source, "rev-parse", "HEAD"), head)
        mutate_fixture("UPDATE workflow_integration_queue SET state='integrated' WHERE id=?", (queue["queue_id"],))
        with self.db.read() as conn:
            old_artifact = dict(conn.execute("SELECT * FROM workflow_patch_artifacts WHERE id=?", (artifact["artifact_id"],)).fetchone())
            old_queue = dict(conn.execute("SELECT * FROM workflow_integration_queue WHERE id=?", (queue["queue_id"],)).fetchone())
        result = self.service.advance_producer_wave(**args)
        self.assertEqual(result["state"], "active")
        self.assertEqual(git(source, "rev-parse", "HEAD"), head)
        with self.db.read() as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM workflow_patch_artifacts WHERE id=?", (artifact["artifact_id"],)).fetchone()), old_artifact)
            self.assertEqual(dict(conn.execute("SELECT * FROM workflow_integration_queue WHERE id=?", (queue["queue_id"],)).fetchone()), old_queue)
        second = self.producer_commit(producer, "alpha changed\nbeta next\ngamma\n")
        next_artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL_NEXT", kind="commit", artifact_ref=second,
        )
        queued = self.service.enqueue_artifact(artifact_id=str(next_artifact["artifact_id"]),
                                              integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE_NEXT")
        self.assertEqual(queued["position"], 0)
        self.assertEqual(self.service.apply_next(queue_id=str(queued["queue_id"]))["state"], "awaiting_gates")

    def test_completed_wave_publication_is_atomic_and_terminal(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        source = Path(str(producer["worktree_path"]))
        commit = self.producer_commit(producer, "alpha wave\nbeta\ngamma\n")
        def terminal(conn, revision):
            now = utc_now()
            conn.execute("UPDATE tasks SET status='done' WHERE id='IMPL'")
            conn.execute("UPDATE workflow_lane_tasks SET state='completed' WHERE task_id='IMPL'")
            conn.execute("INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                         "VALUES('IMPL_NEXT','workstream','next','planned',?,?,?)", (now, now, revision))
            conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                         "VALUES('PRODUCER',1,'IMPL_NEXT','queued',?,?)", (now, revision))
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="IMPL",
                       event_type="task_completed", payload={}, operation=terminal)
        before = self.db.revision()
        result = self.service.publish_completed_wave(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", artifact_ref=commit,
            integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        )
        self.assertEqual(result["revision"], before + 1)
        with self.db.read() as conn:
            self.assertEqual(conn.execute(
                "SELECT state FROM workflow_patch_artifacts WHERE id=?", (result["artifact_id"],)
            ).fetchone()[0], "queued")
            self.assertEqual(conn.execute(
                "SELECT state FROM workflow_integration_queue WHERE id=?", (result["queue_id"],)
            ).fetchone()[0], "queued")
        self.assertEqual(git(source, "status", "--porcelain"), "")

    def test_completed_wave_rejects_dirty_or_missing_destination_without_partial_artifact(self) -> None:
        producer = self.create_producer()
        source = Path(str(producer["worktree_path"]))
        commit = self.producer_commit(producer, "alpha wave\nbeta\ngamma\n")
        def terminal(conn, revision):
            conn.execute("UPDATE tasks SET status='done' WHERE id='IMPL'")
            conn.execute("UPDATE workflow_lane_tasks SET state='completed' WHERE task_id='IMPL'")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="IMPL",
                       event_type="task_completed", payload={}, operation=terminal)
        (source / "dirty.txt").write_text("preserve\n", encoding="utf-8")
        self.assert_code("workspace_uncommitted_changes", lambda: self.service.publish_completed_wave(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", artifact_ref=commit,
            integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        ))
        (source / "dirty.txt").unlink()
        before = self.db.revision()
        self.assert_code("exclusive_integration_workspace_required", lambda: self.service.publish_completed_wave(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", artifact_ref=commit,
            integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE",
        ))
        self.assertEqual(self.db.revision(), before)
        with self.db.read() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workflow_patch_artifacts WHERE workspace_id=?",
                (producer["workspace_id"],),
            ).fetchone()[0], 0)

    def test_unintegrated_producer_cannot_advance_wave(self) -> None:
        producer = self.create_producer()
        self.assert_code("workspace_wave_not_integrated", lambda: self.service.advance_producer_wave(
            repository_root=self.repo, workspace_id=str(producer["workspace_id"]), base_commit=self.base,
            integration_task_id="INTEGRATE", reason="not qualified",
        ))

    def test_unconsumed_handoff_can_be_withdrawn_for_more_serial_work(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        source = Path(str(producer["worktree_path"]))
        commit = self.producer_commit(producer, "alpha changed\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        queue = self.service.enqueue_artifact(artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        args = dict(artifact_id=str(artifact["artifact_id"]), resume_producer=True, reason="finish the next serial task before handoff")
        # Queue fixtures begin with a remaining queued task. No file/HEAD may change.
        (source / "unknown.txt").write_text("preserve")
        rev = self.db.revision()
        self.assert_code("artifact_withdrawal_source_changed", lambda: self.service.reject_artifact(**args))
        self.assertEqual(self.db.revision(), rev)
        self.assertEqual((source / "unknown.txt").read_text(), "preserve")
        (source / "unknown.txt").unlink()
        self.assert_code("artifact_withdrawal_invalid", lambda: self.service.reject_artifact(**{**args, "reason": ""}))
        def fixture(sql, values=()):
            self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="withdrawal_fixture", payload={},
                           operation=lambda conn, revision: conn.execute(sql, values).rowcount)
        fixture("UPDATE workflow_runs SET status='paused' WHERE id='RUN'")
        self.assert_code("artifact_withdrawal_busy", lambda: self.service.reject_artifact(**args))
        fixture("UPDATE workflow_runs SET status='active' WHERE id='RUN'")
        fixture("UPDATE workflow_lanes SET state='active' WHERE id='PRODUCER'")
        self.assert_code("artifact_withdrawal_busy", lambda: self.service.reject_artifact(**args))
        fixture("UPDATE workflow_lanes SET state='ready' WHERE id='PRODUCER'")
        fixture("UPDATE workflow_lane_tasks SET state='completed' WHERE lane_id='PRODUCER'")
        self.assert_code("artifact_withdrawal_no_remaining_task", lambda: self.service.reject_artifact(**args))
        fixture("UPDATE workflow_lane_tasks SET state='queued' WHERE lane_id='PRODUCER'")
        def orphan_claim(conn, revision):
            session, _ = create_session(conn, self.repo)
            now = utc_now()
            conn.execute("INSERT INTO claims(id,task_id,session_id,token_hash,state,created_at,heartbeat_at,expires_at,baseline_revision) "
                         "VALUES('withdrawal-orphan','IMPL',?,'fixture-only','active',?,?,?,?)", (session["agent_id"], now, now, now, revision))
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="withdrawal_orphan", payload={}, operation=orphan_claim)
        revision = self.db.revision()
        self.assert_code("artifact_withdrawal_busy", lambda: self.service.reject_artifact(**args))
        self.assertEqual(self.db.revision(), revision)
        fixture("UPDATE claims SET state='released' WHERE id='withdrawal-orphan'")
        result = self.service.reject_artifact(**args)
        self.assertEqual(result["workspace_state"], "active")
        self.assertEqual(git(source, "rev-parse", "HEAD"), commit)
        with self.db.read() as conn:
            old = dict(conn.execute("SELECT * FROM workflow_patch_artifacts WHERE id=?", (artifact["artifact_id"],)).fetchone())
            self.assertEqual(old["state"], "rejected")
            self.assertEqual(old["artifact_ref"], commit)
            self.assertEqual(conn.execute("SELECT state FROM workflow_integration_queue WHERE id=?", (queue["queue_id"],)).fetchone()[0], "rejected")
        later = self.producer_commit(producer, "alpha changed\nbeta next\ngamma\n")
        new = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=later)
        queued = self.service.enqueue_artifact(artifact_id=str(new["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE")
        self.assertEqual(queued["position"], 1)
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        before = self.db.revision()
        self.assert_code("artifact_withdrawal_consumed", lambda: self.service.reject_artifact(**{**args, "artifact_id": str(new["artifact_id"])}))
        self.assertEqual(self.db.revision(), before)

    def test_withdrawal_preserves_clean_unpublished_commit(self) -> None:
        producer = self.create_producer()
        source = Path(str(producer["worktree_path"]))
        commit = self.producer_commit(producer, "published\n")
        artifact = self.service.publish_artifact(workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit)
        later = self.producer_commit(producer, "unpublished but committed\n")
        revision = self.db.revision()
        self.assert_code("artifact_withdrawal_source_changed", lambda: self.service.reject_artifact(
            artifact_id=str(artifact["artifact_id"]), resume_producer=True, reason="must not discard newer work"))
        self.assertEqual(self.db.revision(), revision)
        self.assertEqual(git(source, "rev-parse", "HEAD"), later)
        self.assertEqual((source / "shared.txt").read_text(), "unpublished but committed\n")

    def test_two_producer_artifacts_integrate_serially_into_one_destination(self) -> None:
        destination = self.create_destination()
        first = self.create_producer()
        second = self.create_producer(lane="PRODUCER2")
        for producer, revision in ((first, 1), (second, 2)):
            producer_path = Path(str(producer["worktree_path"]))
            (producer_path / ".todo-orchestrator").mkdir()
            (producer_path / ".todo-orchestrator" / "state.snapshot.json").write_text(
                json.dumps({"revision": revision}) + "\n",
                encoding="utf-8",
            )
            git(producer_path, "add", ".todo-orchestrator/state.snapshot.json")
            git(producer_path, "commit", "-qm", "producer authority projection")
        first_commit = self.producer_commit(first, "alpha changed\nbeta\ngamma\n")
        second_commit = self.producer_commit(second, "alpha\nbeta\ngamma changed\n")
        first_artifact = self.service.publish_artifact(
            workspace_id=str(first["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=first_commit
        )
        second_artifact = self.service.publish_artifact(
            workspace_id=str(second["workspace_id"]), task_id="IMPL2", kind="commit", artifact_ref=second_commit
        )
        first_queue = self.service.enqueue_artifact(
            artifact_id=str(first_artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        second_queue = self.service.enqueue_artifact(
            artifact_id=str(second_artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        self.service.apply_next(queue_id=str(first_queue["queue_id"]))
        first_result = self.service.record_post_merge_gates(
            queue_id=str(first_queue["queue_id"]),
            gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}],
        )
        destination_path = Path(str(destination["worktree_path"]))
        self.assertEqual(git(destination_path, "rev-parse", "HEAD"), first_result["integrated_artifact"]["ref"])
        self.assertEqual(git(destination_path, "status", "--porcelain=v1"), "")

        self.service.apply_next(queue_id=str(second_queue["queue_id"]))
        second_result = self.service.record_post_merge_gates(
            queue_id=str(second_queue["queue_id"]),
            gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}],
        )
        self.assertEqual(git(destination_path, "rev-parse", "HEAD"), second_result["integrated_artifact"]["ref"])
        self.assertEqual(git(destination_path, "status", "--porcelain=v1"), "")
        self.assertEqual(
            (destination_path / "shared.txt").read_text(encoding="utf-8"),
            "alpha changed\nbeta\ngamma changed\n",
        )

        intermediate = first_result["integrated_artifact"]["ref"]
        final = second_result["integrated_artifact"]["ref"]
        # Cumulative queue commits are squashed against the same wave base.
        self.assertEqual(git(self.repo, "rev-parse", intermediate + "^"), self.base)
        self.assertEqual(git(self.repo, "rev-parse", final + "^"), self.base)
        self.assertNotEqual(intermediate, final)
        ancestor = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", intermediate, final])
        self.assertEqual(ancestor.returncode, 1)

        def next_wave(conn, revision):
            now = utc_now()
            for lane, task in (("PRODUCER", "IMPL_NEXT"), ("INTEGRATOR", "INTEGRATE_NEXT")):
                conn.execute("INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) VALUES(?,?,?,'planned',?,?,?)",
                             (task, "workstream", task, now, now, revision))
                conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES(?,1,?,'queued',?,?)",
                             (lane, task, now, revision))
            conn.execute("UPDATE tasks SET status='done' WHERE id IN ('IMPL','IMPL2','INTEGRATE')")
            conn.execute("UPDATE workflow_lane_tasks SET state='completed' WHERE task_id IN ('IMPL','IMPL2','INTEGRATE')")
        self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="next_wave", payload={}, operation=next_wave)
        source = Path(str(first["worktree_path"]))
        git(source, "merge", "--no-edit", final)
        preserved_projection = (source / ".todo-orchestrator/state.snapshot.json").read_bytes()
        args = dict(repository_root=self.repo, workspace_id=str(first["workspace_id"]), base_commit=final,
                    integration_task_id="INTEGRATE_NEXT", reason="continue first producer after cumulative integration")
        self.assert_code("workspace_wave_base_unqualified", lambda: self.service.advance_producer_wave(**{**args, "base_commit": intermediate}))
        # The first producer's receipt is terminal, but another queue must also finish.
        def queue_state(state):
            self.db.mutate(actor_session_id=None, entity_type="fixture", entity_id="RUN", event_type="queue_state", payload={},
                           operation=lambda conn, revision: conn.execute("UPDATE workflow_integration_queue SET state=? WHERE id=?", (state, second_queue["queue_id"])).rowcount)
        queue_state("gate_failed")
        self.assert_code("workspace_wave_pending_artifacts", lambda: self.service.advance_producer_wave(**args))
        queue_state("integrated")
        self.producer_commit(first, "unpublished material\n")
        unpublished_head = git(source, "rev-parse", "HEAD")
        self.assert_code("workspace_wave_source_unready", lambda: self.service.advance_producer_wave(**args))
        self.assertEqual(git(source, "rev-parse", "HEAD"), unpublished_head)
        git(source, "revert", "--no-edit", unpublished_head)
        result = self.service.advance_producer_wave(**args)
        self.assertEqual(result["state"], "active")
        self.assertEqual(result["base_commit"], final)
        self.assertEqual((source / ".todo-orchestrator/state.snapshot.json").read_bytes(), preserved_projection)
        self.assertEqual((source / "shared.txt").read_text(), "alpha changed\nbeta\ngamma changed\n")

    def test_commit_artifact_applies_the_complete_base_to_tip_range(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        producer_path = Path(str(producer["worktree_path"]))
        (producer_path / "first.txt").write_text("first\n", encoding="utf-8")
        git(producer_path, "add", "first.txt")
        git(producer_path, "commit", "-qm", "first producer commit")
        (producer_path / "second.txt").write_text("second\n", encoding="utf-8")
        git(producer_path, "add", "second.txt")
        git(producer_path, "commit", "-qm", "second producer commit")
        tip = git(producer_path, "rev-parse", "HEAD")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=tip
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(applied["state"], "awaiting_gates")
        destination_path = Path(str(destination["worktree_path"]))
        self.assertEqual((destination_path / "first.txt").read_text(), "first\n")
        self.assertEqual((destination_path / "second.txt").read_text(), "second\n")

    def test_canonical_gate_runner_binds_destination_workspace_and_source(self) -> None:
        (self.repo / ".todo-orchestrator").mkdir()
        (self.repo / ".todo-orchestrator" / "state.snapshot.json").write_text("{}\n", encoding="utf-8")
        git(self.repo, "add", ".todo-orchestrator/state.snapshot.json")
        git(self.repo, "commit", "-qm", "tracked authority projection")
        self.base = git(self.repo, "rev-parse", "HEAD")
        destination = self.create_destination()
        destination_path = Path(str(destination["worktree_path"]))
        snapshot = destination_path / ".todo-orchestrator" / "state.snapshot.json"
        snapshot.write_text("{\"revision\": 1}\n", encoding="utf-8")
        git(destination_path, "add", ".todo-orchestrator/state.snapshot.json")
        git(destination_path, "commit", "-qm", "refresh authority projection")
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha changed\nbeta\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))

        def authorize(conn, revision):
            conn.execute(
                "UPDATE gates SET type='command',config_json=? WHERE id='POST'",
                (json.dumps({
                    "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('shared.txt').read_text().startswith('alpha changed')"],
                    "input_paths": ["shared.txt"],
                }, sort_keys=True),),
            )
            session, _ = create_session(conn, self.repo)
            _, claim_token = claim_best(
                conn, self.repo, str(session["agent_id"]), revision, 7200,
                requested_task_id="INTEGRATE", reconcile_expired=False,
            )
            return claim_token

        claim_token, _ = self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="INTEGRATE",
            event_type="fixture_gate_authorized", payload={}, operation=authorize,
        )
        evidence_dir = self.root / "evidence"
        evidence_dir.mkdir()
        paths = ProjectPaths(
            repo_root=self.repo,
            control_dir=self.root / "control",
            project_file=self.root / "project.json",
            snapshot_file=self.root / "snapshot.json",
            state_dir=self.root,
            db_file=self.root / "state.sqlite3",
            evidence_dir=evidence_dir,
        )
        gate, _ = run_gate(
            self.db,
            paths,
            {"configuration": {}},
            "POST",
            claim_token,
            execution_root=Path(str(destination["worktree_path"])),
            workspace_base_commit=self.base,
        )
        self.assertEqual(gate["status"], "passed")
        snapshot.write_text("{\"revision\": 2}\n", encoding="utf-8")
        gate, _ = run_gate(
            self.db,
            paths,
            {"configuration": {}},
            "POST",
            claim_token,
            execution_root=destination_path,
            workspace_base_commit=self.base,
        )
        self.assertEqual(gate["status"], "passed")
        finished = self.service.record_post_merge_gates(
            queue_id=str(queued["queue_id"]),
            gate_results=[{"gate_id": "POST", "evidence_id": gate["evidence_id"]}],
        )
        self.assertEqual(finished["state"], "integrated")
        self.assertEqual(finished["integrated_artifact"]["kind"], "commit")
        self.assertEqual(
            _sha := subprocess.run(
                ["git", "-C", str(destination["worktree_path"]), "rev-parse", f"{finished['integrated_artifact']['ref']}^{{commit}}"],
                capture_output=True, text=True, check=True,
            ).stdout.strip(),
            finished["integrated_artifact"]["ref"],
        )

    def test_integrator_role_and_exclusive_destination_are_authoritative(self) -> None:
        self.create_destination(lane="NOT_INTEGRATOR")
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta changed\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )

    def test_integrated_destination_rolls_forward_for_next_owned_integration_task(self) -> None:
        destination = self.create_destination()
        destination_path = Path(str(destination["worktree_path"]))
        (destination_path / "integrated.txt").write_text("milestone one\n", encoding="utf-8")
        git(destination_path, "add", "integrated.txt")
        git(destination_path, "commit", "-qm", "integrated milestone")
        next_base = git(destination_path, "rev-parse", "HEAD")

        def advance_lane(conn, revision):
            now = "2026-08-27T01:00:00Z"
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                "VALUES('INTEGRATE2','workstream','INTEGRATE2','planned',?,?,?)",
                (now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('INTEGRATOR',1,'INTEGRATE2','queued',?,?)",
                (now, revision),
            )
            conn.execute(
                "UPDATE workflow_lane_tasks SET state='completed',revision=? "
                "WHERE lane_id='INTEGRATOR' AND task_id='INTEGRATE'",
                (revision,),
            )
            conn.execute(
                "UPDATE workflow_workspaces SET state='integrated' WHERE id=?",
                (destination["workspace_id"],),
            )

        self.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="INTEGRATE2",
            event_type="fixture_integrator_advanced",
            payload={},
            operation=advance_lane,
        )
        producer = self.service.create_workspace(
            repository_root=self.repo,
            repository_identity="repo-identity",
            run_id="RUN",
            lane_id="PRODUCER2",
            mode="isolated_merge",
            base_commit=next_base,
            worktree_path=self.managed / "producer2",
            branch="test-producer2",
            integration_task_id="INTEGRATE2",
        )
        commit = self.producer_commit(producer, "alpha\nbeta\nnext wave\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]),
            task_id="IMPL2",
            kind="commit",
            artifact_ref=commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]),
            integrator_lane_id="INTEGRATOR",
            integration_task_id="INTEGRATE2",
        )
        self.assertEqual(queued["position"], 0)
        with self.db.read() as conn:
            rolled = conn.execute(
                "SELECT base_commit,integration_task_id,state,artifact_ref FROM workflow_workspaces "
                "WHERE id=?",
                (destination["workspace_id"],),
            ).fetchone()
        self.assertEqual(rolled["base_commit"], next_base)
        self.assertEqual(rolled["integration_task_id"], "INTEGRATE2")
        self.assertEqual(rolled["state"], "active")
        self.assertIsNone(rolled["artifact_ref"])

    def test_future_integration_artifact_queues_without_skipping_current_task(self) -> None:
        destination = self.create_destination()

        def add_future_task(conn, revision):
            now = "2026-08-27T01:00:00Z"
            conn.execute(
                "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                "VALUES('INTEGRATE2','workstream','INTEGRATE2','planned',?,?,?)",
                (now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES('INTEGRATOR',1,'INTEGRATE2','queued',?,?)",
                (now, revision),
            )

        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="INTEGRATE2",
            event_type="fixture_future_integration", payload={}, operation=add_future_task,
        )
        producer = self.service.create_workspace(
            repository_root=self.repo, repository_identity="repo-identity", run_id="RUN",
            lane_id="PRODUCER2", mode="isolated_merge", base_commit=self.base,
            worktree_path=self.managed / "producer2", branch="test-producer2",
            integration_task_id="INTEGRATE2",
        )
        commit = self.producer_commit(producer, "alpha\nbeta\nfuture wave\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL2",
            kind="commit", artifact_ref=commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR",
            integration_task_id="INTEGRATE2",
        )
        with self.db.read() as conn:
            destination_state = conn.execute(
                "SELECT integration_task_id,state FROM workflow_workspaces WHERE id=?",
                (destination["workspace_id"],),
            ).fetchone()
        self.assertEqual(destination_state["integration_task_id"], "INTEGRATE")
        self.assertEqual(destination_state["state"], "active")
        self.assert_code(
            "integration_task_out_of_order",
            lambda: self.service.apply_next(queue_id=str(queued["queue_id"])),
        )

    def test_active_integration_task_remains_first_in_lane_order(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta active\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL",
            kind="commit", artifact_ref=commit,
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR",
            integration_task_id="INTEGRATE",
        )

        def activate_current(conn, revision):
            conn.execute(
                "UPDATE workflow_lane_tasks SET state='active',revision=? "
                "WHERE lane_id='INTEGRATOR' AND task_id='INTEGRATE'",
                (revision,),
            )

        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="INTEGRATE",
            event_type="fixture_integrator_activated", payload={}, operation=activate_current,
        )
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(applied["state"], "awaiting_gates")

    def test_identical_pending_artifact_publish_is_idempotent(self) -> None:
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta changed\ngamma\n")
        first = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]),
            task_id="IMPL",
            kind="commit",
            artifact_ref=commit,
        )
        second = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]),
            task_id="IMPL",
            kind="commit",
            artifact_ref=commit,
        )
        self.assertEqual(second["artifact_id"], first["artifact_id"])
        with self.db.read() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM workflow_patch_artifacts WHERE workspace_id=?",
                (producer["workspace_id"],),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_real_merge_conflict_becomes_preserved_integration_work(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "producer\nbeta\ngamma\n")
        destination_path = Path(str(destination["worktree_path"]))
        (destination_path / "shared.txt").write_text("integrator\nbeta\ngamma\n", encoding="utf-8")
        git(destination_path, "add", "shared.txt")
        git(destination_path, "commit", "-qm", "integrator conflicting change")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        result = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(result["integration_task_id"], "INTEGRATE")
        self.assertIn("shared.txt", result["conflict"]["paths"])
        self.assertTrue(result["conflict"]["preserved"])
        self.assertTrue(destination_path.exists())
        self.assertIn("UU shared.txt", git(destination_path, "status", "--porcelain=v1"))
        pre_apply_head = git(destination_path, "rev-parse", "HEAD")
        cherry_pick_head = Path(git(destination_path, "rev-parse", "--git-path", "CHERRY_PICK_HEAD"))
        if cherry_pick_head.exists():
            cherry_pick_head.unlink()
        retried = self.service.retry_conflict(queue_id=str(queued["queue_id"]))
        self.assertEqual(retried["state"], "queued")
        self.assertEqual(retried["restored_head"], pre_apply_head)
        self.assertEqual(git(destination_path, "status", "--porcelain=v1"), "")
        self.assertEqual(git(destination_path, "rev-parse", "HEAD"), pre_apply_head)

    def test_patch_is_copied_to_immutable_managed_artifact(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        producer_path = Path(str(producer["worktree_path"]))
        (producer_path / "shared.txt").write_text("alpha\nbeta\npatch\n", encoding="utf-8")
        patch = self.managed / "immutable.patch"
        patch.write_bytes(subprocess.run(
            ["git", "-C", str(producer_path), "diff", "--binary"], capture_output=True, check=True
        ).stdout)
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="patch", artifact_ref=str(patch)
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        patch.write_text("changed after publication\n", encoding="utf-8")
        self.assertEqual(self.service.apply_next(queue_id=str(queued["queue_id"]))["state"], "awaiting_gates")

    def test_failed_post_merge_gate_preserves_destination_and_blocks_cleanup(self) -> None:
        (self.repo / ".todo-orchestrator").mkdir()
        (self.repo / ".todo-orchestrator" / "state.snapshot.json").write_text("{}\n", encoding="utf-8")
        git(self.repo, "add", ".todo-orchestrator/state.snapshot.json")
        git(self.repo, "commit", "-qm", "tracked authority projection")
        self.base = git(self.repo, "rev-parse", "HEAD")
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta\ngamma changed\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        result = self.service.record_post_merge_gates(
            queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("failed")}]
        )
        self.assertEqual(result["state"], "gate_failed")
        self.assertTrue(Path(str(destination["worktree_path"])).exists())
        destination_path = Path(str(destination["worktree_path"]))
        snapshot = destination_path / ".todo-orchestrator" / "state.snapshot.json"
        snapshot.write_text(snapshot.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        retried_projection_refresh = self.service.retry_failed_gates(queue_id=str(queued["queue_id"]))
        self.assertEqual(retried_projection_refresh["state"], "awaiting_gates")
        self.assertEqual(
            self.service.record_post_merge_gates(
                queue_id=str(queued["queue_id"]),
                gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("failed")}],
            )["state"],
            "gate_failed",
        )
        (destination_path / "resolution.txt").write_text("integrator resolution\n", encoding="utf-8")
        git(destination_path, "add", "resolution.txt")
        self.assert_code(
            "integration_source_changed",
            lambda: self.service.retry_failed_gates(queue_id=str(queued["queue_id"])),
        )
        retried = self.service.retry_failed_gates(
            queue_id=str(queued["queue_id"]), allow_source_resolution=True
        )
        self.assertEqual(retried["state"], "awaiting_gates")
        finalized = self.service.record_post_merge_gates(
            queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}]
        )
        self.assertEqual(finalized["state"], "integrated")
        self.assertTrue(self.service.mark_cleanup_eligible(workspace_id=str(producer["workspace_id"]))["cleanup_eligible"])

    def test_finalization_failure_can_return_to_authoritative_gates(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta\ngamma retry\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        with mock.patch.object(
            self.service,
            "_freeze_integration_commit",
            side_effect=TodoError("integration_tree_mismatch", "fixture finalization failure"),
        ):
            self.assert_code(
                "integration_tree_mismatch",
                lambda: self.service.record_post_merge_gates(
                    queue_id=str(queued["queue_id"]),
                    gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}],
                ),
            )
        with self.db.read() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT state FROM workflow_integration_queue WHERE id=?",
                    (queued["queue_id"],),
                ).fetchone()[0],
                "finalization_failed",
            )
        retried = self.service.retry_finalization_failed(queue_id=str(queued["queue_id"]))
        self.assertEqual(retried["state"], "awaiting_gates")
        finalized = self.service.record_post_merge_gates(
            queue_id=str(queued["queue_id"]),
            gate_results=[{"gate_id": "POST", "evidence_id": self.gate_evidence("passed")}],
        )
        self.assertEqual(finalized["state"], "integrated")

    def test_caller_asserted_gate_status_cannot_authorize_integration(self) -> None:
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta asserted\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.db.mutate(
            actor_session_id=None, entity_type="evidence", entity_id="STALE", event_type="test_timestamp_adjusted", payload={},
            operation=lambda conn, revision: conn.execute(
                "UPDATE evidence SET created_at='9999-12-31T23:59:59Z' WHERE id='STALE'"
            ),
        )
        self.assert_code(
            "integration_gate_provenance_invalid",
            lambda: self.service.record_post_merge_gates(
                queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "status": "passed"}]
            ),
        )

    def test_gate_started_before_apply_and_changed_destination_are_rejected(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta provenance\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        destination_path = Path(str(destination["worktree_path"])).resolve()
        started_revision = self.db.revision()
        stale_metadata = json.dumps({
            "input_fingerprint": "stale-fingerprint",
            "started_revision": started_revision,
            "workspace_path": str(destination_path),
            "source_identity": self.service._source_identity(destination_path, self.base),
        }, sort_keys=True)
        self.db.mutate(
            actor_session_id=None, entity_type="gate", entity_id="POST", event_type="stale_gate", payload={},
            operation=lambda conn, revision: (
                conn.execute("UPDATE gates SET status='passed',valid=1,input_fingerprint='stale-fingerprint',revision=? WHERE id='POST'", (revision,)),
                conn.execute(
                    "INSERT INTO evidence(id,gate_id,kind,status,metadata_json,created_at,revision) "
                    "VALUES('STALE','POST','gate','passed',?,?,?)",
                    (stale_metadata, utc_now(), revision),
                ),
            ),
        )
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assert_code(
            "integration_gate_workspace_mismatch",
            lambda: self.service.record_post_merge_gates(
                queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": "STALE"}]
            ),
        )

        evidence = self.gate_evidence("passed")
        (destination_path / "shared.txt").write_text("changed after gates\n", encoding="utf-8")
        self.assert_code(
            "integration_source_changed",
            lambda: self.service.record_post_merge_gates(
                queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": evidence}]
            ),
        )

    def test_all_required_gates_are_mandatory_and_apply_failure_is_recorded(self) -> None:
        destination = self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta coverage\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        destination_path = Path(str(destination["worktree_path"]))
        (destination_path / "dirty.txt").write_text("preserve\n", encoding="utf-8")
        self.assert_code("integration_workspace_dirty", lambda: self.service.apply_next(queue_id=str(queued["queue_id"])))
        with self.db.read() as conn:
            self.assertEqual(conn.execute("SELECT state FROM workflow_integration_queue WHERE id=?", (queued["queue_id"],)).fetchone()[0], "apply_failed")
            self.assertEqual(conn.execute("SELECT state FROM workflow_workspaces WHERE id=?", (destination["workspace_id"],)).fetchone()[0], "apply_failed")
        self.assert_code(
            "integration_workspace_dirty",
            lambda: self.service.retry_apply_failed(queue_id=str(queued["queue_id"])),
        )
        (destination_path / "dirty.txt").unlink()
        retried = self.service.retry_apply_failed(queue_id=str(queued["queue_id"]))
        self.assertEqual(retried["state"], "queued")
        self.assertEqual(retried["destination_state"], "active")
        applied = self.service.apply_next(queue_id=str(queued["queue_id"]))
        self.assertEqual(applied["state"], "awaiting_gates")

        # A fresh fixture flow proves omitting any required gate cannot authorize integration.
        self.tearDown()
        self.setUp()
        self.db.mutate(
            actor_session_id=None, entity_type="gate", entity_id="POST2", event_type="test_gate_added", payload={},
            operation=lambda conn, revision: conn.execute(
                "INSERT INTO gates(id,task_id,type,config_json,required,status,valid,revision) "
                "VALUES('POST2','INTEGRATE','manual','{}',1,'pending',0,?)", (revision,)
            ),
        )
        self.create_destination()
        producer = self.create_producer()
        commit = self.producer_commit(producer, "alpha\nbeta gates\ngamma\n")
        artifact = self.service.publish_artifact(
            workspace_id=str(producer["workspace_id"]), task_id="IMPL", kind="commit", artifact_ref=commit
        )
        queued = self.service.enqueue_artifact(
            artifact_id=str(artifact["artifact_id"]), integrator_lane_id="INTEGRATOR", integration_task_id="INTEGRATE"
        )
        self.service.apply_next(queue_id=str(queued["queue_id"]))
        evidence = self.gate_evidence("passed")
        self.assert_code(
            "integration_gate_coverage_incomplete",
            lambda: self.service.record_post_merge_gates(
                queue_id=str(queued["queue_id"]), gate_results=[{"gate_id": "POST", "evidence_id": evidence}]
            ),
        )


if __name__ == "__main__":
    unittest.main()
