from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests.todo.v2_helpers import ROOT, V2Repo, base_plan, safe_task


class V2RecoveryMigrationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = V2Repo()

    def tearDown(self) -> None:
        self.repo.close()

    def _expire(self, claim_id: str) -> None:
        conn = self.repo.service.db.connect()
        try:
            conn.execute("UPDATE claims SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (claim_id,))
        finally:
            conn.close()

    def test_expired_clean_claim_returns_to_ready(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        claim = self.repo.service.continue_work(task_id="A")
        self._expire(claim["claim"]["claim_id"])
        replacement = self.repo.service.continue_work(task_id="A")
        self.assertNotEqual(replacement["claim"]["claim_id"], claim["claim"]["claim_id"])
        with self.repo.service.db.read() as conn:
            state = conn.execute("SELECT state FROM claims WHERE id=?", (claim["claim"]["claim_id"],)).fetchone()[0]
        self.assertEqual(state, "expired_clean")

    def test_expired_dirty_claim_is_quarantined(self) -> None:
        self.repo.apply(base_plan([safe_task("A", "src/a")]))
        claim = self.repo.service.continue_work(task_id="A")
        (self.repo.root / "src" / "a").mkdir(parents=True)
        (self.repo.root / "src" / "a" / "work.txt").write_text("unfinished\n", encoding="utf-8")
        self._expire(claim["claim"]["claim_id"])
        process, payload = self.repo.run("continue")
        self.assertEqual(process.returncode, 10, payload)
        inspection = self.repo.service.recover("inspect", "A")
        self.assertFalse(inspection["clean"])
        self.assertEqual(self.repo.service.explain("A")["execution"], "attention_required")
        with self.assertRaisesRegex(Exception, "Owned paths changed"):
            self.repo.service.recover("release", "A")
        adopted = self.repo.service.recover("adopt", "A")
        self.assertEqual(adopted["task_id"], "A")
        self.assertIn("claim_token", adopted)

    def test_legacy_migration_preserves_unknown_sections_and_orphans_claims(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "legacy"
        shutil.copy2(fixture / "todos.md", self.repo.root / "todos.md")
        shutil.copy2(fixture / "todo-status.md", self.repo.root / "todo-status.md")
        shutil.copytree(fixture / "todos", self.repo.root / "todos", dirs_exist_ok=True)
        dry = self.repo.service.migrate_markdown(False)
        self.assertEqual(dry["tasks_found"], 2)
        self.assertTrue(any(item["code"] == "legacy_claim_orphaned" for item in dry["warnings"]))
        result = self.repo.service.migrate_markdown(True)
        self.assertEqual(result["tasks_imported"], 2)
        with self.repo.service.db.read() as conn:
            alpha = conn.execute("SELECT status,legacy_owner,legacy_payload_json FROM tasks WHERE id='alpha'").fetchone()
        self.assertEqual(alpha["status"], "attention_required")
        self.assertIn("User Notes", json.loads(alpha["legacy_payload_json"])["markdown"])

    def test_context_fixture_is_generic_and_within_budget(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "v2" / "generic-parallel-project.json"
        plan = json.loads(fixture.read_text(encoding="utf-8"))
        (self.repo.root / "include").mkdir()
        (self.repo.root / "include" / "public_contract.txt").write_text("contract v1\n", encoding="utf-8")
        self.repo.apply(plan)
        claim = self.repo.service.continue_work(task_id="FOUNDATION-API")
        capsule = claim
        self.assertLessEqual(capsule["context_size_bytes"], capsule["context_budget_bytes"])
        encoded = json.dumps(capsule, sort_keys=True).lower()
        for forbidden in ("cp-math", "cellpack", "cellerator", "packingplan"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(capsule["interlocks"][0]["id"], "contract-stable")
        self.assertIn("build-manifest", capsule["scope"]["shared_locks"])
        with self.assertRaisesRegex(Exception, "unsatisfied required gates"):
            self.repo.service.checkpoint("reach", "CONTRACT-FROZEN", capsule["claim"]["claim_token"])
        self.repo.service.gate_run("CONTRACT-EXISTS", capsule["claim"]["claim_token"])
        self.repo.service.checkpoint("reach", "CONTRACT-FROZEN", capsule["claim"]["claim_token"])
        self.assertTrue(self.repo.service.explain("COMPONENT-A")["ready"])

    def test_gate_inputs_are_precisely_invalidated(self) -> None:
        source = self.repo.root / "src" / "a"
        source.mkdir(parents=True)
        input_file = source / "input.txt"
        input_file.write_text("one\n", encoding="utf-8")
        task = safe_task("A", "src/a", gates=[{"id": "CHECK", "type": "file_exists", "path": "src/a/input.txt", "input_paths": ["src/a/input.txt"], "required": True}])
        self.repo.apply(base_plan([task]))
        claim = self.repo.service.continue_work(task_id="A")
        self.repo.service.gate_run("CHECK", claim["claim"]["claim_token"])
        input_file.write_text("two\n", encoding="utf-8")
        audit = self.repo.service.audit()
        self.assertTrue(any(item["code"] == "gate_inputs_changed" for item in audit["discrepancies"]))
        reconciled = self.repo.service.reconcile()
        self.assertEqual(reconciled["invalidated_gates"], ["CHECK"])


if __name__ == "__main__":
    unittest.main()
