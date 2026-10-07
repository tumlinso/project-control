from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from project_control.assistance.lab import LabError, LabProject, LabService
from project_control.assistance.lab_runner import ExecutionResult
from project_control.assistance.lab_session import LabScope, LabSessionService


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
    subprocess.run(["git", "-c", "user.name=LAB Test", "-c", "user.email=lab@example.invalid",
                    "commit", "-qm", "initial"], cwd=source, check=True)
    return source


def _proposal(request, *, done=False):
    if done:
        return {"hypothesis": "", "source_citations": [], "artifacts": [], "argv": [],
                "measurements": [], "stop_rule": "", "done": True}
    source = next(item for item in request["sources"] if item["path"] == "src/module.py")
    return {"hypothesis": "verify selected source behavior",
        "source_citations": [{"path": source["path"], "sha256": source["sha256"]}],
        "artifacts": [{"path": "test_generated.py", "content": "print('bounded result')\n"}],
        "argv": ["python3", "/proposal/test_generated.py"],
        "measurements": ["exit status", "stdout"], "stop_rule": "stop after the bounded test",
        "done": False}


class _Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


class LabSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = _repo(self.root)
        self.clock = _Clock()
        self.lab = LabService(projects={"fixture": LabProject("fixture", self.source, "fixture-repo")},
                              state_root=self.root / "private-state", clock=self.clock)
        self.operator = self.lab.operator()

    def _service(self, planner):
        return LabSessionService(self.lab, planner, clock=self.clock)

    def _preview_authorize(self, service, **kwargs):
        scope = LabScope("fixture", ("src",), "find a bounded counterexample", **kwargs)
        preview = service.preview(self.operator, scope)
        self.assertFalse(preview["inference_started"])
        return preview["session_id"], service.authorize(self.operator, preview["session_id"])

    def _runner_result(self, snapshot, argv, grant, *, attempt_root, effect_id,
                       proposal_root=None, should_cancel=None, on_started=None):
        self.assertTrue((proposal_root / "test_generated.py").is_file())
        self.assertEqual((proposal_root / "test_generated.py").stat().st_mode & 0o222, 0)
        artifact = attempt_root / "result.bin"
        artifact.write_bytes(b"result")
        return ExecutionResult(status="ok", returncode=0, stdout=b"bounded result\n", stderr=b"",
            elapsed_ms=4, artifact_path=artifact, artifact_sha256=hashlib.sha256(b"result").hexdigest(),
            artifact_bytes=6, containment_backend="test-double", effect_id=effect_id,
            process_identity=None, cleanup_verified=True)

    def test_two_autonomous_effects_then_done_are_durable_and_scoped(self):
        calls = []

        def planner(request):
            calls.append(request)
            self.assertEqual(request["tools"], ["python3"])
            self.assertEqual(request["artifact_mount"], "/proposal")
            return _proposal(request, done=len(calls) == 3)

        service = self._service(planner)
        session_id, authorized = self._preview_authorize(service)
        self.assertEqual(authorized["remaining_experiments"], 6)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu", side_effect=self._runner_result) as runner:
            result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["cleanup_verified"])
        self.assertEqual(result["experiment_count"], 2)
        self.assertEqual(runner.call_count, 2)
        status = service.status(self.operator, session_id)["sessions"][0]
        self.assertEqual(status["state"], "completed")
        self.assertEqual(len(status["effects"]), 2)
        self.assertTrue(all(item["receipt"]["cleanup_verified"] for item in status["effects"]))

    def test_source_change_during_planning_causes_a_fresh_plan_and_no_stale_effect(self):
        calls = []

        def planner(request):
            calls.append(request)
            if len(calls) == 1:
                result = _proposal(request)
                (self.source / "src" / "module.py").write_text("value = 2\n", encoding="utf-8")
                return result
            return _proposal(request, done=True)

        service = self._service(planner)
        session_id, _ = self._preview_authorize(service)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu") as runner:
            result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(calls), 2)
        runner.assert_not_called()

    def test_cancel_during_planning_prevents_effect(self):
        holder = {}

        def planner(request):
            holder["service"].cancel(self.operator, request["session_id"])
            return _proposal(request)

        service = self._service(planner)
        holder["service"] = service
        session_id, _ = self._preview_authorize(service)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu") as runner:
            result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "cancelled")
        runner.assert_not_called()

    def test_cancel_during_cpu_effect_cleans_up_then_stops_session(self):
        holder = {}

        def planner(request):
            return _proposal(request)

        def execute(snapshot, argv, grant, *, attempt_root, effect_id, proposal_root=None,
                    should_cancel=None, on_started=None):
            holder["service"].cancel(self.operator, holder["session_id"])
            artifact = attempt_root / "result.bin"
            artifact.write_bytes(b"partial")
            return ExecutionResult(status="interrupted", returncode=-15, stdout=b"", stderr=b"",
                elapsed_ms=1, artifact_path=artifact, artifact_sha256=hashlib.sha256(b"partial").hexdigest(),
                artifact_bytes=7, containment_backend="test-double", effect_id=effect_id,
                process_identity=None, cleanup_verified=True)

        service = self._service(planner)
        session_id, _ = self._preview_authorize(service)
        holder.update(service=service, session_id=session_id)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu", side_effect=execute):
            result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(result["cleanup_verified"])
        self.assertEqual(result["experiment_count"], 1)

    def test_safe_paused_session_can_resume_without_replaying_an_effect(self):
        calls = []

        def planner(request):
            calls.append(request)
            if len(calls) == 1:
                raise RuntimeError("temporary planner failure")
            return _proposal(request, done=True)

        service = self._service(planner)
        session_id, _ = self._preview_authorize(service)
        with self.assertRaisesRegex(RuntimeError, "temporary planner failure"):
            service.run(self.operator, session_id)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu") as runner:
            result = service.resume(self.operator, session_id)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["experiment_count"], 0)
        runner.assert_not_called()

    def test_budget_expiry_blocks_new_planning_and_effects(self):
        service = self._service(lambda request: (_ for _ in ()).throw(AssertionError("planner called")))
        session_id, _ = self._preview_authorize(service, wall_seconds=2)
        self.clock.value += 3
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu") as runner:
            with self.assertRaisesRegex(ValueError, "deadline has expired"):
                service.run(self.operator, session_id)
            runner.assert_not_called()

    def test_parallel_run_rejected_and_ambiguous_effect_never_replayed(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def planner(request):
            calls.append(request)
            entered.set()
            release.wait(2)
            return _proposal(request, done=len(calls) > 1)

        service = self._service(planner)
        session_id, _ = self._preview_authorize(service)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu", side_effect=self._runner_result):
            thread = threading.Thread(target=lambda: service.run(self.operator, session_id))
            thread.start()
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(ValueError, "already running"):
                service.resume(self.operator, session_id)
            release.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())

        other = self._service(lambda request: _proposal(request))
        ambiguous_id, _ = self._preview_authorize(other)
        with patch("project_control.assistance.lab_session.lab_runner.run_cpu", side_effect=RuntimeError("runner failure")) as runner:
            result = other.run(self.operator, ambiguous_id)
            self.assertEqual(result["state"], "unknown")
            with self.assertRaisesRegex(ValueError, "only an authorized or safely paused"):
                other.resume(self.operator, ambiguous_id)
            runner.assert_called_once()

    def test_gpu_without_executor_is_unknown_not_a_fake_success(self):
        def gpu_proposal(request):
            value = _proposal(request)
            value["argv"] = ["cuda"]
            return value

        service = self._service(gpu_proposal)
        session_id, _ = self._preview_authorize(service,
            gpu_uuids=("GPU-01234567-89ab-cdef-0123-456789abcdef",),
            tools=("python3", "cuda"))
        result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "unknown")
        self.assertFalse(result["cleanup_verified"])

    def test_gpu_verified_negative_build_is_a_completed_receipt_without_run_replay(self):
        uuid = "GPU-01234567-89ab-cdef-0123-456789abcdef"
        proposal_artifact = {"path": "test_generated.py", "content": "print('bounded result')\n"}
        digest = hashlib.sha256(proposal_artifact["content"].encode()).hexdigest()
        calls = []

        def planner(request):
            calls.append(request)
            result = _proposal(request, done=len(calls) == 2)
            if not result["done"]:
                result["argv"] = ["cuda"]
            return result

        def gpu_executor(scope, proposal, source_root, proposal_root, **kwargs):
            return {"format": "PC-GPU-LAB-RESULT/1", "status": "experiment_failed",
                "effect_id": kwargs["effect_id"], "scope_project": scope.project,
                "gpu_uuids": [uuid], "cleanup_verified": True,
                "proposal_artifacts": [{"path": "test_generated.py", "sha256": digest,
                                        "bytes": len(proposal_artifact["content"].encode())}],
                "probe": {"phase": "probe", "status": "ok",
                          "cleanup_verified": True, "cgroup_removed": True},
                "build": {"phase": "build", "status": "command_failed",
                          "cleanup_verified": True, "cgroup_removed": True},
                "run": None,
                "foreground_terminal": {"verified": True, "state": "released", "resources": [],
                                        "gpu_uuids": [uuid]},
                "resume": {"request_id": kwargs["effect_id"], "continuation_id": "continue-1",
                           "resource_ids": [f"accelerator:{uuid}"], "status": "resumed"}}

        service = LabSessionService(self.lab, planner, gpu_executor=gpu_executor, clock=self.clock)
        session_id, _ = self._preview_authorize(service, gpu_uuids=(uuid,), tools=("python3", "cuda"))
        result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["cleanup_verified"])
        self.assertEqual(result["experiment_count"], 1)
        effects = service.status(self.operator, session_id)["sessions"][0]["effects"]
        self.assertEqual(effects[0]["receipt"]["status"], "experiment_failed")
        self.assertIsNone(effects[0]["receipt"]["run"])

    def test_gpu_build_failure_before_admission_requires_no_foreground_effect_proof(self):
        uuid = "GPU-01234567-89ab-cdef-0123-456789abcdef"
        artifact_content = "print('bounded result')\n"
        digest = hashlib.sha256(artifact_content.encode()).hexdigest()
        calls = []

        def planner(request):
            calls.append(request)
            result = _proposal(request, done=len(calls) == 2)
            if not result["done"]:
                result["argv"] = ["cuda"]
            return result

        def gpu_executor(scope, proposal, source_root, proposal_root, **kwargs):
            effect_id = kwargs["effect_id"]
            return {"format": "PC-GPU-LAB-RESULT/1", "status": "not_started",
                "phase": "build", "effect_id": effect_id, "scope_project": scope.project,
                "gpu_uuids": [uuid], "cleanup_verified": True,
                "proposal_artifacts": [{"path": "test_generated.py", "sha256": digest,
                                        "bytes": len(artifact_content.encode())}],
                "build": {"phase": "build", "status": "command_failed",
                          "cleanup_verified": True, "cgroup_removed": True,
                          "process_identity": {"pid": 1234}},
                "controller": {"code": "foreground_build_failed", "returned": True,
                               "admission_started": False},
                "payload_started": False,
                "no_foreground_effect": True,
                "no_effect_receipt": {"format": "PC-GPU-LAB-NO-FOREGROUND-EFFECT/1",
                    "effect_id": effect_id, "controller_code": "foreground_build_failed",
                    "controller_returned": True, "payload_started": False,
                    "lease_receipt": None, "native_owner_claimed": False},
                "foreground_terminal": None, "probe": None, "run": None,
                "resume": {"request_id": effect_id, "continuation_id": "continue-2",
                    "resource_ids": [f"accelerator:{uuid}"], "status": "resumed"}}

        service = LabSessionService(self.lab, planner, gpu_executor=gpu_executor, clock=self.clock)
        session_id, _ = self._preview_authorize(service, gpu_uuids=(uuid,), tools=("python3", "cuda"))
        result = service.run(self.operator, session_id)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["cleanup_verified"])
        self.assertEqual(result["experiment_count"], 1)
        receipt = service.status(self.operator, session_id)["sessions"][0]["effects"][0]["receipt"]
        self.assertEqual(receipt["status"], "not_started")
        self.assertTrue(receipt["no_foreground_effect"])
        self.assertIsNone(receipt["foreground_terminal"])

    def test_gpu_known_admission_failures_before_payload_are_safe_no_effects(self):
        uuid = "GPU-01234567-89ab-cdef-0123-456789abcdef"
        codes = ("foreground_resource_contention", "foreign_gpu_activity", "gpu_not_quiescent")
        artifact_content = "print('bounded result')\n"
        digest = hashlib.sha256(artifact_content.encode()).hexdigest()

        for code in codes:
            with self.subTest(controller_code=code):
                calls = []

                def planner(request):
                    calls.append(request)
                    result = _proposal(request, done=len(calls) == 2)
                    if not result["done"]:
                        result["argv"] = ["cuda"]
                    return result

                def gpu_executor(scope, proposal, source_root, proposal_root, **kwargs):
                    effect_id = kwargs["effect_id"]
                    return {"format": "PC-GPU-LAB-RESULT/1", "status": "not_started",
                        "phase": "admission", "effect_id": effect_id, "scope_project": scope.project,
                        "gpu_uuids": [uuid], "cleanup_verified": True,
                        "proposal_artifacts": [{"path": "test_generated.py", "sha256": digest,
                                                "bytes": len(artifact_content.encode())}],
                        "build": {"phase": "build", "status": "ok",
                                  "cleanup_verified": True, "cgroup_removed": True},
                        "controller": {"code": code, "returned": True, "admission_started": True},
                        "payload_started": False, "no_foreground_effect": True,
                        "no_effect_receipt": {"format": "PC-GPU-LAB-NO-FOREGROUND-EFFECT/1",
                            "effect_id": effect_id, "controller_code": code,
                            "controller_returned": True, "payload_started": False,
                            "lease_receipt": None, "native_owner_claimed": False},
                        "foreground_terminal": None, "probe": None, "run": None,
                        "resume": {"request_id": effect_id, "continuation_id": "continue-3",
                            "resource_ids": [f"accelerator:{uuid}"], "status": "resumed"}}

                service = LabSessionService(self.lab, planner, gpu_executor=gpu_executor, clock=self.clock)
                session_id, _ = self._preview_authorize(service, gpu_uuids=(uuid,),
                                                        tools=("python3", "cuda"))
                result = service.run(self.operator, session_id)
                self.assertEqual(result["state"], "completed")
                self.assertEqual(result["experiment_count"], 1)
                receipt = service.status(self.operator, session_id)["sessions"][0]["effects"][0]["receipt"]
                self.assertEqual(receipt["status"], "not_started")
                self.assertTrue(receipt["continuation_ready"])

    def test_registration_root_or_repository_drift_fails_before_planner(self):
        replacement_root = self.root / "replacement-root"
        replacement_root.mkdir()
        for replacement in (
            LabProject("fixture", _repo(replacement_root), "fixture-repo"),
            LabProject("fixture", self.source, "renamed-repository"),
        ):
            with self.subTest(repository=replacement.repository, root=replacement.root):
                calls = []
                service = self._service(lambda request: calls.append(request))
                session_id, _ = self._preview_authorize(service)
                self.lab.projects["fixture"] = replacement
                with self.assertRaisesRegex(LabError, "registration changed"):
                    service.resolve_project(session_id)
                with self.assertRaisesRegex(LabError, "registration changed"):
                    service.run(self.operator, session_id)
                self.assertEqual(calls, [])

    def test_registration_drift_after_preview_blocks_authorization(self):
        service = self._service(lambda request: self.fail("planner must not run"))
        preview = service.preview(self.operator, LabScope("fixture", ("src",), "goal"))
        self.lab.projects["fixture"] = LabProject("fixture", self.source, "new-identity")
        with self.assertRaisesRegex(LabError, "registration changed"):
            service.authorize(self.operator, preview["session_id"])

    def test_tampered_persisted_proposal_digest_or_json_blocks_effect_intent(self):
        for field, value in (("proposal_sha256", "0" * 64),
                             ("proposal_json", '{"hypothesis":"tampered"}')):
            with self.subTest(field=field):
                service = self._service(lambda request: _proposal(request))
                session_id, _ = self._preview_authorize(service)
                original = service._persist_proposal

                def persist_then_tamper(*args, **kwargs):
                    proposal_id, proposal_root = original(*args, **kwargs)
                    db = self.lab._open(create=True)
                    try:
                        db.execute(f"UPDATE lab_session_proposals SET {field}=? WHERE proposal_id=?",
                                   (value, proposal_id))
                        db.commit()
                    finally:
                        db.close()
                    return proposal_id, proposal_root

                with patch.object(service, "_persist_proposal", side_effect=persist_then_tamper), \
                     patch("project_control.assistance.lab_session.lab_runner.run_cpu") as runner:
                    result = service.run(self.operator, session_id)
                self.assertEqual(result["state"], "paused")
                self.assertEqual(result["experiment_count"], 0)
                runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
