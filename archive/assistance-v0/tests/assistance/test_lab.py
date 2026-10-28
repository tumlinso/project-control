"""Durable operator journal and candidate isolation tests for the LAB service."""
from __future__ import annotations

import hashlib
from contextlib import closing
import os
from pathlib import Path
import json
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from project_control.assistance import lab
from project_control.assistance.lab_runner import (
    ExecutionGrant, ExecutionResult, OwnedProcessIdentity,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def _repo(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (source / "src").mkdir()
    (source / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
    _git(source, "add", "src/module.py")
    subprocess.run(["git", "-c", "user.name=LAB Test", "-c",
                    "user.email=lab@example.invalid", "commit", "-qm", "initial"],
                   cwd=source, check=True)
    return source


def _service(root: Path, source: Path) -> lab.LabService:
    return lab.LabService(
        projects={"fixture": lab.LabProject("fixture", source, "fixture-repo")},
        state_root=root / "private-state",
    )


def _selection() -> lab.LabSelection:
    return lab.LabSelection(
        project="fixture", source_paths=("src",),
        hypothesis="the selected test command measures the fixture behavior",
        argv=("python", "-c", "print('bounded')"),
        reference="fixture baseline", expected_measurements="exit status and output",
        stop_rule="one bounded invocation",)


class LabServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = _repo(self.root)
        self.service = _service(self.root, self.source)
        self.operator = self.service.operator()

    def _result(self, attempt_root: Path, *, status="ok", returncode=0) -> ExecutionResult:
        artifact = attempt_root / "result.bin"
        artifact.write_bytes(b"result")
        return ExecutionResult(status=status, returncode=returncode,
            stdout=b"measured\n", stderr=b"", elapsed_ms=12.5,
            artifact_path=artifact, artifact_sha256=hashlib.sha256(b"result").hexdigest(),
            artifact_bytes=6, containment_backend="test-double", effect_id="effect",
            process_identity=None, cleanup_verified=True)

    def test_run_persists_intent_before_execution_and_reopens_receipt(self):
        grant = self.service.select(self.operator, _selection())
        observed = {}

        def execute(snapshot_root, argv, execution_grant, *, attempt_root, effect_id,
                    on_started=None, cgroup_parent=None):
            with closing(sqlite3.connect(self.service.db_path)) as db:
                observed["intent"] = db.execute(
                    "SELECT state,effect_id FROM lab_attempts").fetchone()
            self.assertEqual(Path(snapshot_root), Path(grant.snapshot_root))
            self.assertEqual(argv, grant.argv)
            self.assertIsInstance(execution_grant, ExecutionGrant)
            return self._result(attempt_root)

        with patch.object(lab.lab_runner, "run_cpu", side_effect=execute):
            receipt = self.service.run(self.operator, grant)
        self.assertEqual(observed["intent"][0], "intent")
        self.assertTrue(observed["intent"][1].startswith("effect_"))
        self.assertEqual(receipt["outcome"], "positive")
        self.assertEqual(receipt["artifact"]["sha256"], hashlib.sha256(b"result").hexdigest())
        reopened = _service(self.root, self.source)
        status = reopened.status(reopened.operator(), grant.experiment_id)
        experiment = status["experiments"][0]
        self.assertEqual(experiment["state"], "finished")
        self.assertEqual(experiment["attempts"][0]["receipt"]["outcome"], "positive")

    def test_negative_timeout_and_containment_failure_are_not_promoted(self):
        negative = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root, status="command_failed", returncode=3)):
            receipt = self.service.run(self.operator, negative)
        self.assertEqual(receipt["outcome"], "negative")

        timeout = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root, status="timeout", returncode=None)):
            receipt = self.service.run(self.operator, timeout)
        self.assertEqual(receipt["outcome"], "timeout")

        blocked = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lab.lab_runner.ContainmentUnavailable("no sandbox")):
            with self.assertRaises(lab.lab_runner.ContainmentUnavailable):
                self.service.run(self.operator, blocked)
        state = self.service.status(self.operator, blocked.experiment_id)["experiments"][0]
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["attempts"][0]["outcome"], "inconclusive")

    def test_interrupted_intent_is_marked_unknown_and_never_replayed(self):
        grant = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=RuntimeError("interrupted")) as runner:
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self.service.run(self.operator, grant)
            status = self.service.status(self.operator, grant.experiment_id)
            self.assertEqual(status["experiments"][0]["state"], "unknown")
            with self.assertRaisesRegex(lab.LabError, "consumed"):
                self.service.run(self.operator, grant)
            runner.assert_called_once()

    def test_ambiguous_restart_reconciles_without_replay(self):
        grant = self.service.select(self.operator, _selection())
        # Model a crash after the prelaunch intent commit and before ownership
        # identity could be persisted.
        db = sqlite3.connect(self.service.db_path)
        db.execute("INSERT INTO lab_attempts(attempt_id,experiment_id,generation,effect_id,state,intent_at,argv,grant_json) "
                   "VALUES(?,?,?,?,?,?,?,?)", ("attempt_crashed", grant.experiment_id,
                   grant.generation, "effect_crashed", "intent", 1.0, "[]", "{}"))
        db.execute("UPDATE lab_experiments SET state='intent' WHERE experiment_id=?", (grant.experiment_id,))
        db.commit()
        db.close()
        reopened = _service(self.root, self.source)
        with patch.object(lab.lab_runner, "run_cpu") as runner:
            result = reopened.reconcile(reopened.operator())
            self.assertEqual(result[0]["state"], "unknown")
            self.assertFalse(result[0]["replayed"])
            runner.assert_not_called()
        attempt = reopened.status(reopened.operator(), grant.experiment_id)["experiments"][0]["attempts"][0]
        self.assertEqual(attempt["state"], "unknown")

    def test_restart_checks_persisted_exact_process_identity_without_replay(self):
        grant = self.service.select(self.operator, _selection())
        identity = OwnedProcessIdentity(1234, 98765, "/sys/fs/cgroup/lab/effect",
                                        4321, "effect_live")
        db = sqlite3.connect(self.service.db_path)
        db.execute("INSERT INTO lab_attempts(attempt_id,experiment_id,generation,effect_id,state,intent_at,argv,grant_json,process_identity) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", ("attempt_live", grant.experiment_id,
                   grant.generation, "effect_live", "running", 1.0, "[]", "{}",
                   lab._dump({"pid": identity.pid, "start_time_ticks": identity.start_time_ticks,
                              "cgroup_path": identity.cgroup_path, "cgroup_inode": identity.cgroup_inode,
                              "effect_id": identity.effect_id})))
        db.execute("UPDATE lab_experiments SET state='running' WHERE experiment_id=?", (grant.experiment_id,))
        db.commit()
        db.close()
        reopened = _service(self.root, self.source)
        with patch.object(lab.lab_runner, "reconcile_process", return_value="active") as reconcile, \
             patch.object(lab.lab_runner, "run_cpu") as runner:
            result = reopened.reconcile(reopened.operator())
        self.assertEqual(result[0]["state"], "active")
        self.assertFalse(result[0]["replayed"])
        reconcile.assert_called_once_with(identity)
        runner.assert_not_called()

    def test_snapshot_mutation_and_extra_files_block_execution(self):
        for mutation in ("content", "extra", "extra_directory", "symlink"):
            with self.subTest(mutation=mutation):
                grant = self.service.select(self.operator, _selection())
                snapshot = Path(grant.snapshot_root)
                if mutation == "content":
                    (snapshot / "src" / "module.py").write_text("changed = 1\n", encoding="utf-8")
                elif mutation == "extra":
                    (snapshot / "src" / "extra.py").write_text("unlisted = True\n", encoding="utf-8")
                elif mutation == "extra_directory":
                    (snapshot / "src" / "extra_dir").mkdir()
                else:
                    (snapshot / "src" / "external.py").symlink_to("/etc/passwd")
                with patch.object(lab.lab_runner, "run_cpu") as runner:
                    with self.assertRaisesRegex(lab.LabError, "captured source"):
                        self.service.run(self.operator, grant)
                    runner.assert_not_called()

    def test_real_nested_fixture_capture_keeps_every_directory_private_under_umask_022(self):
        source_root = Path(__file__).resolve().parents[2]
        relative = "planning/project-assistance-v1/fixtures/repository/demo/pairs.py"
        canonical_file = source_root / relative
        canonical_bytes = canonical_file.read_bytes()
        canonical_directories = [canonical_file.parents[index]
                                 for index in range(len(Path(relative).parts) - 1)]
        canonical_modes = [stat.S_IMODE(path.stat().st_mode) for path in canonical_directories]

        previous_umask = os.umask(0o022)
        try:
            snapshot = lab.Snapshot.capture(source_root, (relative,), self.root / "real-fixture-capture")
        finally:
            os.umask(previous_umask)

        directories = [snapshot.destination, *(
            path for path in snapshot.destination.rglob("*") if path.is_dir())]
        self.assertGreater(len(directories), 1)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories))
        manifest_path = snapshot.destination / ".lab-snapshot.json"
        manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        manifest = lab._verify_snapshot(snapshot.destination, manifest_digest)
        self.assertEqual(manifest["files"], [{
            "path": relative,
            "mode": stat.S_IMODE(canonical_file.stat().st_mode),
            "size": len(canonical_bytes),
            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        }])

        (snapshot.destination / relative).write_bytes(canonical_bytes + b"# tampered capture\n")
        with self.assertRaisesRegex(lab.LabError, f"captured source file changed: {relative}"):
            lab._verify_snapshot(snapshot.destination, manifest_digest)

        self.assertEqual(canonical_file.read_bytes(), canonical_bytes)
        self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in canonical_directories],
                         canonical_modes)

    def test_post_selection_hardlink_in_snapshot_blocks_runner_without_sentinel_change(self):
        grant = self.service.select(self.operator, _selection())
        sentinel = self.root / "outside-secret"
        sentinel.write_bytes(b"private sentinel must not change\n")
        captured_file = Path(grant.snapshot_root) / "src" / "module.py"
        captured_file.unlink()
        os.link(sentinel, captured_file)

        with patch.object(lab.lab_runner, "run_cpu") as runner:
            with self.assertRaisesRegex(lab.LabError, "hard-linked"):
                self.service.run(self.operator, grant)
            runner.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"private sentinel must not change\n")

    def test_candidate_verification_detects_snapshot_file_tampering(self):
        grant = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root)):
            self.service.run(self.operator, grant)
        patch_text = ("diff --git a/src/module.py b/src/module.py\n"
                      "--- a/src/module.py\n+++ b/src/module.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n")
        candidate = self.service.create_candidate(self.operator, grant.experiment_id, patch_text)
        (Path(grant.snapshot_root) / "src" / "module.py").write_text(
            "tampered = True\n", encoding="utf-8")
        result = self.service.verify_candidate(self.operator, candidate["candidate_id"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["source_identity"], "captured_snapshot_changed")

        manifest_grant = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root)):
            self.service.run(self.operator, manifest_grant)
        manifest_candidate = self.service.create_candidate(
            self.operator, manifest_grant.experiment_id, patch_text)
        manifest_path = Path(manifest_grant.snapshot_root) / ".lab-snapshot.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = []
        manifest["total_bytes"] = 0
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_result = self.service.verify_candidate(
            self.operator, manifest_candidate["candidate_id"])
        self.assertEqual(manifest_result["status"], "stale")
        self.assertEqual(manifest_result["source_identity"], "captured_snapshot_changed")

    def test_unverified_cleanup_remains_pending_until_exact_reconcile(self):
        grant = self.service.select(self.operator, _selection())
        identity = OwnedProcessIdentity(1234, 98765, "/sys/fs/cgroup/lab/effect",
                                        4321, "effect_unverified")

        def unverified(*args, attempt_root, on_started, **kwargs):
            on_started(identity)
            result = self._result(attempt_root, status="cleanup_unverified", returncode=0)
            return replace(result, process_identity=identity, cleanup_verified=False)

        with patch.object(lab.lab_runner, "run_cpu", side_effect=unverified):
            receipt = self.service.run(self.operator, grant)
        self.assertEqual(receipt["outcome"], "inconclusive")
        self.assertFalse(receipt["cleanup_verified"])
        self.assertEqual(receipt["artifact"]["state"], "partial")
        with patch.object(lab.lab_runner, "reconcile_process", return_value="unknown") as reconcile:
            pending = self.service.status(self.operator, grant.experiment_id)
        self.assertEqual(pending["experiments"][0]["state"], "running")
        self.assertEqual(pending["experiments"][0]["attempts"][0]["state"], "running")
        reconcile.assert_called_once_with(identity)
        with self.assertRaisesRegex(lab.LabError, "consumed"):
            self.service.run(self.operator, grant)
        with patch.object(lab.lab_runner, "reconcile_process", return_value="exited"):
            terminal = self.service.status(self.operator, grant.experiment_id)
        self.assertEqual(terminal["experiments"][0]["state"], "unknown")
        self.assertEqual(terminal["experiments"][0]["attempts"][0]["state"], "unknown")

    def test_candidate_stays_unpromoted_and_detects_current_source_change(self):
        grant = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root)):
            self.service.run(self.operator, grant)
        patch_text = ("diff --git a/src/module.py b/src/module.py\n"
                      "--- a/src/module.py\n+++ b/src/module.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n")
        candidate = self.service.create_candidate(self.operator, grant.experiment_id, patch_text)
        self.assertEqual(candidate["status"], "unpromoted")
        self.assertFalse(candidate["canonical_applied"])
        verified = self.service.verify_candidate(self.operator, candidate["candidate_id"])
        self.assertEqual(verified["status"], "verified")
        self.assertFalse(verified["todo_mutated"])
        (self.source / "src" / "module.py").write_text("value = 3\n", encoding="utf-8")
        stale = self.service.verify_candidate(self.operator, candidate["candidate_id"])
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["current_source_matches_capture"])

    def test_tampered_grant_and_unregistered_project_are_rejected(self):
        grant = self.service.select(self.operator, _selection())
        forged = replace(grant, generation=grant.generation + 1)
        with patch.object(lab.lab_runner, "run_cpu") as runner:
            with self.assertRaisesRegex(lab.LabError, "does not match"):
                self.service.run(self.operator, forged)
            runner.assert_not_called()
        with self.assertRaises(PermissionError):
            self.service.select(self.operator, lab.LabSelection(
                project="unregistered", source_paths=("src",), hypothesis="h", argv=("true",),
                reference="r", expected_measurements="m", stop_rule="s"))

    def test_argv_contract_matches_runner_before_snapshot_creation(self):
        for argv in (("true",) * 65, ("x" * 2049,), ("x" * 2048,) * 7):
            with self.subTest(count=len(argv), first_size=len(argv[0])):
                with self.assertRaises(lab.LabError):
                    self.service.select(self.operator, replace(_selection(), argv=argv))
        self.assertFalse((self.root / "private-state").exists())

    def test_source_selection_caps_are_checked_before_capture_and_leave_no_partial_state(self):
        invalid = (
            replace(_selection(), source_paths=tuple(f"src/{index}.py" for index in range(65))),
            replace(_selection(), source_paths=("src/" + "x" * 40_000,)),
        )
        for selection in invalid:
            with self.subTest(path_count=len(selection.source_paths)):
                with self.assertRaises(lab.LabError):
                    self.service.select(self.operator, selection)
        experiments = self.root / "private-state" / "experiments"
        self.assertFalse(experiments.exists() and list(experiments.iterdir()))

    def test_private_journal_fails_closed_on_directory_or_database_symlink(self):
        outside = self.root / "outside-state"
        outside.mkdir(mode=0o700)
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"must remain unchanged")
        state_link = self.root / "state-link"
        state_link.symlink_to(outside, target_is_directory=True)
        linked_service = lab.LabService(
            projects={"fixture": lab.LabProject("fixture", self.source, "fixture-repo")},
            state_root=state_link,
        )
        with self.assertRaisesRegex(lab.LabError, "symlink"):
            linked_service.select(linked_service.operator(), _selection())
        self.assertEqual(sentinel.read_bytes(), b"must remain unchanged")
        self.assertFalse((outside / "lab.sqlite3").exists())

        private_state = self.root / "private-link-state"
        private_state.mkdir(mode=0o700)
        private_state.chmod(0o700)
        database_link = private_state / "lab.sqlite3"
        database_link.symlink_to(sentinel)
        linked_db_service = lab.LabService(
            projects={"fixture": lab.LabProject("fixture", self.source, "fixture-repo")},
            state_root=private_state,
        )
        with self.assertRaisesRegex(lab.LabError, "mode 0600"):
            linked_db_service.select(linked_db_service.operator(), _selection())
        self.assertEqual(sentinel.read_bytes(), b"must remain unchanged")

    def test_concurrent_selections_receive_unique_generations(self):
        def select_one(_):
            return self.service.select(self.operator, _selection())

        with ThreadPoolExecutor(max_workers=2) as pool:
            grants = list(pool.map(select_one, range(2)))
        self.assertEqual({grant.generation for grant in grants}, {1, 2})

    def test_candidate_verification_detects_file_mode_change(self):
        grant = self.service.select(self.operator, _selection())
        with patch.object(lab.lab_runner, "run_cpu", side_effect=lambda *a, attempt_root, **k:
                          self._result(attempt_root)):
            self.service.run(self.operator, grant)
        patch_text = ("diff --git a/src/module.py b/src/module.py\n"
                      "--- a/src/module.py\n+++ b/src/module.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n")
        candidate = self.service.create_candidate(self.operator, grant.experiment_id, patch_text)
        original_identity = lab.GitReadAdapter(self.source).identity()
        source_file = self.source / "src" / "module.py"
        source_file.chmod(0o755)
        with patch.object(lab.GitReadAdapter, "identity", return_value=original_identity):
            result = self.service.verify_candidate(self.operator, candidate["candidate_id"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["source_identity"], "captured_file_or_mode_changed")


if __name__ == "__main__":
    unittest.main()
