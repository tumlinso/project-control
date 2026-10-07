from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.local_runtime import assert_receiver_module, receiver_runtime_path
RECEIVER_ROOT = receiver_runtime_path()

from local_worker import controller as _controller_module
assert_receiver_module(_controller_module)
from local_worker.controller import IntegrationController, IntegrationError, ProductionReadOnlyRuntime


def command(code: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "argv": [sys.executable, "-c", code],
        "cwd": ".",
        "timeout_seconds": 20,
    }


class Core4IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        source_repository = Path(__file__).resolve().parents[2]
        def bind_fixture(repo_root):
            if Path(repo_root).resolve() != source_repository:
                raise AssertionError("integration fixture attempted a non-source runtime bind")

        binding = mock.patch("local_worker.canonical_runtime.bind", side_effect=bind_fixture)
        binding.start()
        self.addCleanup(binding.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "core4@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "CORE4 Test"], cwd=self.root, check=True)
        (self.root / "src").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "src/kernel.cu").write_text("// baseline\n", encoding="utf-8")
        (self.root / "docs/note.txt").write_text("stable\n", encoding="utf-8")
        self.registry = self.root / "cuda-benchmarks.json"
        self.registry.write_text(json.dumps({
            "format": "CUDA-BENCHMARK-REGISTRY/1",
            "schema_version": 1,
            "project_root": str(self.root),
            "campaigns": [{
                "id": "fixture-sm70",
                "description": "Fixture discovery only",
                "targets": [],
                "paths": ["src/**/*.cu"],
                "symbols": [],
                "task_ids": [],
                "task_prefixes": [],
                "build": None,
                "correctness": {"argv": [sys.executable, "-c", "assert True"], "repetitions": 1},
                "benchmark": {"argv": [sys.executable, "-c", "print('{\\\"latency_ms\\\":1}')"], "warmups": 0, "repetitions": 1},
                "metric": {"format": "CUDA-METRIC/1", "schema_version": 1, "name": "latency_ms",
                           "path": "latency_ms", "direction": "minimize", "unit": "ms",
                           "practical_regression_percent": 2.0, "target": None},
                "resources": {"gpu_count": 1, "architecture": "volta"},
                "policy": {"initial_characterization": False},
            }],
        }), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.root, check=True)

        self.state = Path(self.temporary.name) / "todo-state.json"
        self.state.write_text(json.dumps({"state": "running", "result": None}), encoding="utf-8")
        self.fake_todo = Path(self.temporary.name) / "fake_todo.py"
        self.fake_todo.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "args=sys.argv[1:]; path=Path(os.environ['CORE4_FAKE_TODO_STATE']); state=json.loads(path.read_text())\n"
            "data={}\n"
            "if args[:1]==['context']:\n"
            " data={'task':{'id':os.environ['CORE4_FAKE_TASK_ID'],'objective':'Review the bounded source.'},'scope':{'exclusive_paths':['src'],'read_paths':['docs'],'forbidden_paths':[]},'gates':[{'id':'CHECK','required':1}]}\n"
            "elif args[:2]==['gate','explain']:\n"
            " data={'id':'CHECK','type':'command','config':{'argv':[sys.executable,'-c','raise SystemExit(0)'],'cwd':'.','timeout':60}}\n"
            "elif args[:2]==['gate','run']:\n"
            " data={'gate_id':args[2],'status':'passed','valid':True}\n"
            "elif args[:2]==['child','create']:\n"
            " data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':'running','child_token':'toch_fixture'}\n"
            "elif args[:2]==['child','heartbeat']:\n"
            " data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':'running'}\n"
            "elif args[:2]==['child','report']:\n"
            " status=args[args.index('--status')+1]; changed=[args[i+1] for i,v in enumerate(args) if v=='--changed-path']; state={'state':status,'result':{'status':status,'changed_paths':changed}}; path.write_text(json.dumps(state)); data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':status,'result':state['result']}\n"
            "elif args[:2]==['child','status']:\n"
            " data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':state['state'],'result':state['result']}\n"
            "elif len(args)>1 and args[0]=='child' and args[1] in {'accept','reject','stale','supersede'}:\n"
            " target={'accept':'accepted','reject':'rejected','stale':'stale','supersede':'stale'}[args[1]]; state={'state':target,'result':state['result']}; path.write_text(json.dumps(state)); data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':target}\n"
            "elif args[:2]==['child','cancel']:\n"
            " state={'state':'canceled','result':None}; path.write_text(json.dumps(state)); data={'child_execution_id':'child-fixture','task_id':os.environ['CORE4_FAKE_TASK_ID'],'state':'canceled'}\n"
            "else: raise SystemExit(3)\n"
            "print(json.dumps({'ok':True,'data':data}))\n",
            encoding="utf-8",
        )
        os.chmod(self.fake_todo, 0o755)
        self.fake_ctxpp = Path(self.temporary.name) / "fake_ctxpp.py"
        self.fake_ctxpp.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib,json,sys\n"
            "from pathlib import Path\n"
            "root=Path(sys.argv[sys.argv.index('--root')+1]); target=sys.argv[sys.argv.index('packet')+1]; source=root/'src/kernel.cu'; complete=target!='needs'\n"
            "packet={'format':'CTXPP-CONTEXT-PACKET/1','readonly':True,'target':{'id':'kernel','name':target,'signature':target,'location':{'path':'src/kernel.cu','line':1,'end_line':1,'content_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}},'trust':{'target_range':'hash-verified','relationships':'semantic' if complete else 'lexical-or-partial','index_incomplete':not complete},'coverage':{'sufficient':complete},'packet_hash':'a'*64,'source_identity':{'schema_version':1,'repo_root':str(root),'git_head':None,'dirty_paths':[],'fingerprint':'b'*64},'estimated_tokens':96}\n"
            "print(json.dumps(packet))\n",
            encoding="utf-8",
        )
        os.chmod(self.fake_ctxpp, 0o755)
        self.environment = {
            "CORE4_FAKE_TODO_STATE": str(self.state),
            "CORE4_FAKE_TASK_ID": "CORE4-FIXTURE",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def controller(self, **kwargs) -> IntegrationController:
        kwargs.setdefault("cuda_discovery_runner", lambda evidence: {
            "status": "unambiguous", "auto_queue_safe": True,
            "matches": [{"campaign_id": "fixture-sm70", "reasons": [{"source": "accepted_patch"}]}],
            "auto_queue": {"state": "queued", "controller": {"watch_id": "fixture-watch"}},
        })
        return IntegrationController(
            todo_cli=self.fake_todo,
            ctxpp_cli=self.fake_ctxpp,
            environment=self.environment,
            **kwargs,
        )

    def request(self, mode: str, **updates) -> dict[str, object]:
        common: dict[str, object] = {
            "format": "CORE4-INTEGRATION-REQUEST/1",
            "schema_version": 1,
            "mode": mode,
            "repo_root": str(self.root),
            "parent_claim_token": "toc_fixture",
            "task_id": "CORE4-FIXTURE",
            "objective": "Perform one bounded fake-backend child execution.",
            "scopes": ["src"],
            "gates": [],
            "cuda_registry": str(self.registry),
        }
        if mode == "readonly":
            common.update(role="review", target="kernel", intent="understand", budget_tokens=1024, max_items=8)
        else:
            common.update(
                fake_changes={"src/kernel.cu": "// accepted candidate\n"},
                baseline_commands=[command("from pathlib import Path; assert Path('src/kernel.cu').read_text() == '// baseline\\n'")],
                verification_commands=[command("from pathlib import Path; assert 'candidate' in Path('src/kernel.cu').read_text()")],
                acceptance_commands=[command("from pathlib import Path; assert 'candidate' in Path('src/kernel.cu').read_text()")],
            )
        common.update(updates)
        return common

    def test_legacy_receiver_rejects_disabled_readonly_command(self) -> None:
        before = (self.root / "src/kernel.cu").read_bytes()
        self.environment["LCW_CUDA_CLI"] = str(self.fake_ctxpp)
        with self.assertRaisesRegex(IntegrationError, "disabled at this receiver"):
            self.controller(worker_cli=RECEIVER_ROOT / "scripts/local_worker.py",
                            ).run(self.request("readonly"))
        self.assertEqual((self.root / "src/kernel.cu").read_bytes(), before)
        self.assertEqual(json.loads(self.state.read_text())["state"], "failed")

    def test_public_delegate_derives_read_child_request_from_todo_capsule(self) -> None:
        seen = {}
        def terminal(request):
            seen.update(request)
            return {"status": "completed", "summary": "bounded", "changed_paths": [],
                    "child_reported": False, "packet_hash": "a" * 64, "artifacts": []}
        result = self.controller(terminal_runner=terminal).delegate(
            self.root, "toc_fixture", mode="readonly",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen["objective"], "Review the bounded source.")
        self.assertEqual(seen["scopes"], ["src"])
        self.assertEqual(seen["target"], "src/kernel.cu")
        self.assertEqual((seen["format"], seen["backend"]), ("LCW-REQUEST/2", "real"))

    def test_ce_style_writable_objective_selects_bounded_write_scope_and_separate_target(self) -> None:
        for index in range(20):
            (self.root / f"src/seam_{index}.cu").write_text("// seam\n", encoding="utf-8")
        capsule = {
            "task": {
                "id": "CE-ARCH-71", "title": "Register operation candidates",
                "objective": "Implement the bounded operation seam.",
                "next_action": "Edit src/seam_17.cu and preserve launch bindings.",
            },
            "scope": {
                "exclusive_paths": [f"src/seam_{index}.cu" for index in range(20)],
                "read_paths": ["docs/note.txt"], "forbidden_paths": [],
            },
            "gates": [],
        }
        controller = self.controller()
        controller._todo = mock.Mock(return_value=capsule)
        objective = (
            "CE-ARCH-71 bounded implementation seam only: register the prepared operation "
            "without widening the architecture or changing unrelated candidates."
        )
        request = controller.request_from_claim(
            self.root, "toc_fixture", mode="writable", objective=objective,
        )
        self.assertIn("Implement the bounded operation seam.", request["objective"])
        self.assertIn(f"Delegation focus: {objective}", request["objective"])
        self.assertEqual(request["scopes"], ["src/seam_17.cu"])
        self.assertEqual(request["target"], "src/seam_17.cu")
        self.assertLessEqual(len(request["scopes"]), 16)

    def test_ce_style_writable_natural_language_is_not_a_ctxpp_target_and_caps_scopes(self) -> None:
        write_paths = []
        for index in range(20):
            path = self.root / f"src/seam_{index}.cu"
            path.write_text("// seam\n", encoding="utf-8")
            write_paths.append(f"src/seam_{index}.cu")
        capsule = {
            "task": {
                "id": "CE-ARCH-71", "title": "Register operation candidates",
                "objective": "Implement the bounded candidate registration seam.", "next_action": "",
            },
            "scope": {"exclusive_paths": write_paths, "read_paths": [], "forbidden_paths": []},
            "gates": [],
        }
        controller = self.controller()
        controller._todo = mock.Mock(return_value=capsule)
        objective = (
            "CE-ARCH-71 bounded implementation seam only: preserve the prepared launch contract "
            "and avoid architecture changes."
        )
        request = controller.request_from_claim(
            self.root, "toc_fixture", mode="writable", objective=objective,
        )
        self.assertIn(request["target"], request["scopes"])
        self.assertNotEqual(request["target"], objective)
        self.assertEqual(len(request["scopes"]), 16)
        self.assertTrue(all(path in write_paths for path in request["scopes"]))

    def test_ce_style_readonly_objective_selects_relevant_subset_and_omits_unproven_target(self) -> None:
        read_paths = []
        for index in range(20):
            path = self.root / f"docs/topic_{index}.txt"
            path.write_text("bounded\n", encoding="utf-8")
            read_paths.append(f"docs/topic_{index}.txt")
        operation = self.root / "docs/operation_contract.txt"
        operation.write_text("operation\n", encoding="utf-8")
        read_paths.append("docs/operation_contract.txt")
        capsule = {
            "task": {
                "id": "CE-ARCH-71", "title": "Review operation registration",
                "objective": "Review the prepared operation contract.", "next_action": "",
            },
            "scope": {
                "exclusive_paths": ["src/kernel.cu"], "read_paths": read_paths,
                "forbidden_paths": [],
            },
            "gates": [],
        }
        controller = self.controller()
        controller._todo = mock.Mock(return_value=capsule)
        objective = (
            "CE-ARCH-71 bounded read-only review: inspect operation launch registration and "
            "report whether the seam is internally consistent."
        )
        request = controller.request_from_claim(
            self.root, "toc_fixture", mode="readonly", objective=objective,
        )
        self.assertIn("Review the prepared operation contract.", request["objective"])
        self.assertIn(f"Delegation focus: {objective}", request["objective"])
        self.assertEqual(request["scopes"], ["docs/operation_contract.txt"])
        self.assertEqual(request["target"], "")
        self.assertLessEqual(len(request["scopes"]), 16)

    def test_public_writable_delegate_forwards_explicit_ctxpp_target(self) -> None:
        request = self.controller().request_from_claim(
            self.root, "toc_fixture", mode="writable", target="kernel",
        )
        self.assertEqual(request["target"], "kernel")

    def test_public_auto_delegation_is_conservatively_readonly(self) -> None:
        request = self.controller().request_from_claim(
            self.root, "toc_fixture", mode="auto", target="kernel",
        )
        self.assertEqual(request["mode"], "readonly")
        self.assertEqual(request["target"], "kernel")

    def test_public_writable_delegate_verifies_applies_credits_and_accepts(self) -> None:
        def runner(worktree: Path, request: dict[str, object]) -> dict[str, object]:
            (worktree / "src/kernel.cu").write_text("// delegated candidate\n", encoding="utf-8")
            return {"format": "LOCAL-WORKER-REVIEW/1", "verdict": "pass"}
        result = self.controller(writable_runner=runner).delegate(
            self.root, "toc_fixture", mode="writable",
        )
        self.assertEqual((result["status"], result["child_state"]), ("accepted", "accepted"))
        self.assertEqual(result["changed_paths"], ["src/kernel.cu"])
        self.assertEqual((self.root / "src/kernel.cu").read_text(), "// delegated candidate\n")
        self.assertFalse(result["parent_task_completed"])

    def test_legacy_receiver_rejects_disabled_needs_codex_command(self) -> None:
        self.environment["LCW_CUDA_CLI"] = str(self.fake_ctxpp)
        with self.assertRaisesRegex(IntegrationError, "disabled at this receiver"):
            self.controller(worker_cli=RECEIVER_ROOT / "scripts/local_worker.py",
                            ).run(
                self.request("readonly", target="needs"))

    def test_v2_integration_forwards_policy_selected_real_execution(self) -> None:
        seen = {}
        def terminal(request):
            seen.update(request)
            return {"status": "completed", "summary": "bounded", "changed_paths": [],
                    "child_reported": False, "packet_hash": "a" * 64, "artifacts": []}
        request = self.request("readonly")
        request.update(format="CORE4-INTEGRATION-REQUEST/2", schema_version=2,
                       execution={"backend": "real", "harness": "qwen", "gpu_count": 1})
        result = self.controller(terminal_runner=terminal).run(request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual((seen["format"], seen["backend"]), ("LCW-REQUEST/2", "real"))

    def test_production_runtime_reuses_supervisor_and_releases_to_idle(self) -> None:
        events = []
        profile = {"deployment_policy": {"real_local_enabled": True, "hot_idle_seconds": 0},
                   "server": {"split_mode": "layer", "base_port": 8080, "startup_timeout_seconds": 5},
                   "experiment": {"initial_context": 16384}, "harnesses": {"qwen": "qwen", "codex": "codex"},
                   "storage": {"cache_root": "/cache", "canonical_root": "/cold"}}
        class Supervisor:
            def request(self, operation, **parameters):
                events.append(operation)
                if operation == "warm":
                    return {"base_url": "http://127.0.0.1:8080/v1", "model_id": "candidate",
                            "model_sha256": "a" * 64, "gpu_uuids": ["GPU-a"], "reused": True,
                            "slot_id": "slot-a", "service_lease_id": "lease-a", "server_pid": 42}
                return {"released": True}
        class Harness:
            def start(self, context): events.append("harness-start"); return "harness"
            def run(self, handle, request): events.append("harness-run"); return {"status": "succeeded", "text": "done", "usage": {"core4": {"tool_calls": 2}}, "duration_ms": 1}
            def evict(self, handle): events.append("harness-evict")
        runtime = ProductionReadOnlyRuntime(profile=profile, supervisor=Supervisor(),
                                            harness_factory=lambda name: Harness())
        snapshot = type("Snapshot", (), {"root": self.root})()
        request = {"repo_root": str(self.root), "objective": "bounded", "role": "review", "scopes": ["src"],
                   "execution": {"backend": "real", "harness": "qwen", "gpu_count": 1}}
        result = runtime.execute(request, {"format": "CTXPP-CONTEXT-PACKET/2"}, snapshot)
        self.assertEqual((result["status"], result["tool_calls"]), ("completed", 2))
        self.assertEqual(events[0], "warm")
        self.assertEqual(events[-2:], ["harness-evict", "release"])

    def test_production_runtime_consumes_pre_child_admission(self) -> None:
        calls = []
        profile = {"deployment_policy": {"real_local_enabled": True},
                   "harnesses": {"qwen": "qwen", "codex": "codex"}}
        class Supervisor:
            def request(self, operation, **parameters):
                calls.append((operation, parameters))
                if operation == "warm":
                    return {"base_url": "http://127.0.0.1:8080/v1", "model_id": "candidate",
                            "model_sha256": "a" * 64, "gpu_uuids": ["GPU-a"], "reused": True,
                            "slot_id": "slot-a", "service_lease_id": "lease-a", "server_pid": 42}
                return {"released": True}
        class Harness:
            def start(self, context): calls.append(("model-start", {})); return "harness"
            def run(self, handle, request):
                calls.append(("model-run", {}))
                return {"status": "succeeded", "usage": {}, "duration_ms": 1}
            def evict(self, handle): calls.append(("model-evict", {}))
        runtime = ProductionReadOnlyRuntime(profile=profile, supervisor=Supervisor(),
                                            harness_factory=lambda name: Harness())
        snapshot = type("Snapshot", (), {"root": self.root})()
        request = {"repo_root": str(self.root),
                   "objective": "CE-ARCH-71 bounded read-only operation registration review.",
                   "role": "review", "scopes": ["docs/note.txt"],
                   "execution": {"backend": "real", "harness": "qwen", "admission_id": "admission-1"}}
        self.assertEqual(runtime.execute(request, {}, snapshot)["status"], "completed")
        self.assertEqual(calls[0], ("warm", {"admission_id": "admission-1"}))
        self.assertEqual(calls[1:3], [("model-start", {}), ("model-run", {})])

    def test_ce_style_writable_request_reaches_admitted_model_launch(self) -> None:
        events = []
        (self.root / ".ctxpp").mkdir()
        (self.root / ".ctxpp/index.jsonl").write_text("{}\n", encoding="utf-8")
        class Supervisor:
            def request(self, operation, **parameters):
                events.append((operation, parameters))
                if operation == "warm":
                    return {
                        "base_url": "http://127.0.0.1:8080/v1", "model_id": "candidate",
                        "slot_id": "slot-a", "service_lease_id": "lease-a", "server_pid": 42,
                        "gpu_uuids": ["GPU-a"], "reused": True,
                    }
                return {"released": True}
        class Harness:
            def start(self, context): events.append(("model-start", {})); return "harness"
            def run(self, handle, request):
                events.append(("model-run", {}))
                return {"model_outcome": {"placeholder": True}, "usage": {}}
            def evict(self, handle): events.append(("model-evict", {}))
        request = {
            "objective": (
                "CE-ARCH-71 bounded implementation seam only: register the prepared operation. "
                + ("constraint " * 40)
            ),
            "repo_root": str(self.root),
            "scopes": ["src/kernel.cu"], "read_dependencies": ["docs/note.txt"],
            "gates": [], "execution": {"backend": "real", "harness": "qwen", "admission_id": "admission-1"},
            "context_packet": {"format": "prepared-fixture"},
        }
        validated = {
            "outcome": "completed", "summary": "bounded candidate", "claims": [],
            "changed_paths": [], "risk": "low", "blocker": None,
        }
        controller = self.controller()
        with mock.patch("local_worker.supervisor.SupervisorClient", return_value=Supervisor()), \
             mock.patch("local_worker.harnesses.QwenCodeAdapter", return_value=Harness()), \
             mock.patch("local_worker.result_validation.validate_model_outcome", return_value=validated):
            result = controller._writable_model(self.root, request)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(events[0], ("warm", {"admission_id": "admission-1"}))
        self.assertEqual(events[1:3], [("model-start", {}), ("model-run", {})])

    def test_child_creation_failure_cancels_unconsumed_admission(self) -> None:
        request = self.request("readonly")
        request.update(format="CORE4-INTEGRATION-REQUEST/2", schema_version=2,
                       execution={"backend": "real", "harness": "qwen", "admission_id": "admission-1"})
        cancelled = []
        class Supervisor:
            def request(self, operation, **parameters):
                cancelled.append((operation, parameters))
                return {"cancelled": True}
        controller = self.controller()
        with mock.patch.object(controller, "_create_child", side_effect=RuntimeError("child failed")), \
             mock.patch("local_worker.supervisor.SupervisorClient", return_value=Supervisor()):
            with self.assertRaisesRegex(RuntimeError, "child failed"):
                controller.run(request)
        self.assertEqual(cancelled, [("cancel-admission", {"admission_id": "admission-1"})])

    def test_two_public_runtime_calls_use_isolated_services_and_qwen_runtime_dirs(self) -> None:
        lock = threading.Lock()
        barrier = threading.Barrier(2)
        available = [
            {"slot_id": "slot-a", "service_lease_id": "lease-a", "server_pid": 41,
             "gpu_uuids": ["GPU-a", "GPU-b"], "base_url": "http://127.0.0.1:8080/v1"},
            {"slot_id": "slot-b", "service_lease_id": "lease-b", "server_pid": 42,
             "gpu_uuids": ["GPU-c", "GPU-d"], "base_url": "http://127.0.0.1:8081/v1"},
        ]
        releases = []
        runtime_dirs = []
        class Supervisor:
            def request(self, operation, **parameters):
                if operation == "warm":
                    with lock:
                        endpoint = available.pop(0)
                    return {**endpoint, "model_id": "candidate", "model_sha256": "a" * 64,
                            "compatibility_key": "b" * 64, "reused": False}
                if operation == "release":
                    releases.append(parameters["service_lease_id"])
                return {"released": True}
        class Harness:
            def start(self, context): runtime_dirs.append(context["runtime_dir"]); return context["runtime_dir"]
            def run(self, handle, request):
                barrier.wait(timeout=2)
                return {"status": "succeeded", "usage": {"core4": {"tool_calls": 1}}, "duration_ms": 1}
            def evict(self, handle): return None
        profile = {"deployment_policy": {"real_local_enabled": True},
                   "harnesses": {"qwen": "qwen", "codex": "codex"}}
        runtime = ProductionReadOnlyRuntime(profile=profile, supervisor=Supervisor(),
                                            harness_factory=lambda name: Harness())
        snapshot = type("Snapshot", (), {"root": self.root})()
        outputs = []
        request = {"repo_root": str(self.root), "objective": "bounded", "role": "review",
                   "scopes": ["src"], "execution": {"backend": "real", "harness": "qwen"}}
        threads = [threading.Thread(target=lambda: outputs.append(runtime.execute(request, {}, snapshot)))
                   for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=3)
        artifacts = [output["artifacts"][0] for output in outputs]
        self.assertEqual({item["slot_id"] for item in artifacts}, {"slot-a", "slot-b"})
        self.assertEqual(set(releases), {"lease-a", "lease-b"})
        self.assertEqual(len(set(runtime_dirs)), 2)
        self.assertTrue(set(artifacts[0]["gpu_uuids"]).isdisjoint(artifacts[1]["gpu_uuids"]))

    def test_preempted_terminal_worker_returns_needs_codex_without_source_change(self) -> None:
        before = (self.root / "src/kernel.cu").read_bytes()
        terminal = lambda request: {
            "status": "needs_codex", "summary": "resource_preempted",
            "changed_paths": [], "child_reported": False,
        }
        result = self.controller(terminal_runner=terminal).run(self.request("readonly"))
        self.assertEqual(result["status"], "needs_codex")
        self.assertIn("preempted", result["summary"])
        self.assertEqual((self.root / "src/kernel.cu").read_bytes(), before)

    def test_writable_patch_is_externally_verified_guarded_and_triggers_cuda(self) -> None:
        result = self.controller().run(self.request("writable"))
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["changed_paths"], ["src/kernel.cu"])
        self.assertEqual(result["cuda"]["state"], "queued")
        self.assertTrue(result["cuda"]["auto_queued"])
        self.assertEqual(result["cuda"]["campaign_ids"], ["fixture-sm70"])
        self.assertEqual(result["cuda"]["context_packets"], 1)
        self.assertEqual((self.root / "src/kernel.cu").read_text(encoding="utf-8"), "// accepted candidate\n")
        self.assertFalse(result["parent_task_completed"])

    def test_stale_patch_is_rejected_without_overwriting_concurrent_user_change(self) -> None:
        def make_stale(root: Path, artifact: dict[str, object]) -> None:
            (root / "src/kernel.cu").write_text("// concurrent user work\n", encoding="utf-8")

        result = self.controller(before_accept=make_stale).run(self.request("writable"))
        self.assertEqual(result["status"], "stale_patch")
        self.assertFalse(result["accepted"])
        self.assertEqual((self.root / "src/kernel.cu").read_text(encoding="utf-8"), "// concurrent user work\n")
        self.assertEqual(result["cuda"]["state"], "silent")

    def test_disjoint_user_change_does_not_stale_scoped_acceptance(self) -> None:
        def mutate_disjoint(root: Path, artifact: dict[str, object]) -> None:
            (root / "docs/note.txt").write_text("independent change\n", encoding="utf-8")

        result = self.controller(before_accept=mutate_disjoint).run(self.request("writable"))
        self.assertEqual(result["status"], "accepted")
        self.assertEqual((self.root / "src/kernel.cu").read_text(encoding="utf-8"), "// accepted candidate\n")
        self.assertEqual((self.root / "docs/note.txt").read_text(encoding="utf-8"), "independent change\n")

    def test_cuda_handoff_is_silent_on_no_match_and_compact_on_ambiguity(self) -> None:
        no_match = self.controller(cuda_discovery_runner=lambda evidence: {
            "status": "no_match", "matches": [], "auto_queue": {"state": "not_queued", "reason": "no_match"},
        }).run(self.request("writable"))
        self.assertEqual(no_match["cuda"]["state"], "silent")

        subprocess.run(["git", "add", "src/kernel.cu"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "accepted fixture"], cwd=self.root, check=True)
        ambiguous = self.controller(cuda_discovery_runner=lambda evidence: {
            "status": "ambiguous",
            "matches": [
                {"campaign_id": "first", "reasons": [{"source": "accepted_patch"}]},
                {"campaign_id": "second", "reasons": [{"source": "ctxpp_symbol"}, {"source": "accepted_patch"}]},
            ],
            "auto_queue": {"state": "not_queued", "reason": "ambiguous"},
        }).run(self.request(
            "writable", fake_changes={"src/kernel.cu": "// next candidate\n"},
            baseline_commands=[command("from pathlib import Path; assert Path('src/kernel.cu').read_text() == '// accepted candidate\\n'")],
        ))
        self.assertEqual(ambiguous["cuda"]["state"], "choice_required")
        self.assertFalse(ambiguous["cuda"]["auto_queued"])
        self.assertEqual(ambiguous["cuda"]["choices"], [
            {"campaign_id": "first", "reasons": 1}, {"campaign_id": "second", "reasons": 2},
        ])

    def test_v2_writable_runner_uses_approved_overlay_and_persists_reviewer_evidence(self) -> None:
        (self.root / "docs/note.txt").write_text("unrelated user work\n", encoding="utf-8")
        def runner(worktree: Path, request: dict[str, object]) -> dict[str, object]:
            self.assertEqual((worktree / "docs/note.txt").read_text(), "stable\n")
            (worktree / "src/kernel.cu").write_text("// real-path candidate\n", encoding="utf-8")
            return {"format": "LOCAL-WORKER-REVIEW/1", "verdict": "pass"}
        request = self.request("writable")
        request.update(
            format="CORE4-INTEGRATION-REQUEST/2", schema_version=2,
            read_dependencies=["docs"], approved_overlays=[], execution={"backend": "real"},
        )
        request.pop("fake_changes")
        result = self.controller(writable_runner=runner).run(request)
        self.assertEqual((result["status"], result["child_state"]), ("accepted", "accepted"))
        artifact_root = Path(result["artifact_root"])
        self.assertTrue((artifact_root / "reviewer-evidence.json").is_file())
        self.assertTrue((artifact_root / "acceptance-verification.json").is_file())
        self.assertEqual((self.root / "docs/note.txt").read_text(), "unrelated user work\n")


if __name__ == "__main__":
    unittest.main()
