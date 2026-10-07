from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


class V2FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def test_project_identity_database_snapshot_and_revision(self) -> None:
        identity = json.loads((self.repo.root / ".todo-orchestrator" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(identity["schema_version"], 2)
        self.assertEqual(identity["configuration"]["legacy_review_days"], {"planned": 3, "in_progress": 3, "blocked": 7, "stale": 3})
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        snapshot = json.loads((self.repo.root / ".todo-orchestrator" / "state.snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["project_revision"], self.repo.service.db.revision())
        self.assertNotIn("sessions", snapshot["tables"])
        self.assertNotIn("claims", snapshot["tables"])
        self.assertTrue(self.repo.service.db.integrity()["integrity"] == "ok")
        first = (self.repo.root / ".todo-orchestrator" / "state.snapshot.json").read_bytes()
        self.repo.service.export()
        second = (self.repo.root / ".todo-orchestrator" / "state.snapshot.json").read_bytes()
        self.assertEqual(first, second)

    def test_read_only_service_status_and_export_do_not_write_snapshot(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        snapshot_path = self.repo.root / ".todo-orchestrator" / "state.snapshot.json"
        before = snapshot_path.read_bytes()
        before_mtime = snapshot_path.stat().st_mtime_ns
        from todo_orchestrator.service import Service

        with patch.dict("os.environ", {"TODO_ORCHESTRATOR_READ_ONLY": "1"}):
            service = Service(self.repo.root)
            status = service.status()
            exported = service.export()
        self.assertEqual(status["project_revision"], exported["project_revision"])
        self.assertEqual(exported["state"]["project"]["project_uuid"], self.repo.service.project["project_uuid"])
        self.assertEqual(snapshot_path.read_bytes(), before)
        self.assertEqual(snapshot_path.stat().st_mtime_ns, before_mtime)

    def test_event_revisions_are_unique_and_monotonic(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        claim = self.repo.service.continue_work(task_id="A")
        self.repo.service.pulse(claim["claim"]["claim_token"])
        with self.repo.service.db.read() as conn:
            revisions = [row[0] for row in conn.execute("SELECT revision FROM events ORDER BY revision")]
        self.assertEqual(revisions, sorted(set(revisions)))
        self.assertEqual(revisions, list(range(1, max(revisions) + 1)))

    def test_database_reconstructs_from_snapshot(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        db_path = self.repo.service.paths.db_file
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            candidate = db_path.with_name(db_path.name + suffix)
            if candidate.exists():
                candidate.unlink()
        from todo_orchestrator.service import Service

        recovered = Service(self.repo.root)
        with recovered.db.read() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM tasks WHERE id='A'").fetchone())
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        self.assertGreater(recovered.db.revision(), 2)

    def test_invalid_plan_references_are_rejected(self) -> None:
        from todo_orchestrator.models import TodoError

        plan = base_plan([safe_task("A", "src/a", depends_on=[{"type": "checkpoint", "checkpoint_id": "MISSING"}])])
        path = self.repo.root / "invalid.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(TodoError) as caught:
            self.repo.service.plan_validate(str(path))
        self.assertEqual(caught.exception.code, "plan_validation_failed")

    def test_legacy_mutation_wrapper_is_disabled_after_bootstrap(self) -> None:
        script = Path(__file__).resolve().parent / "scripts" / "init_todos.py"
        result = subprocess.run(
            [sys.executable, str(script), "--repo-root", str(self.repo.root), "--objective", "must not mutate"],
            capture_output=True,
            text=True,
            env=self.repo.env(),
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["code"], "legacy_command_disabled_for_v2")


if __name__ == "__main__":
    unittest.main()
