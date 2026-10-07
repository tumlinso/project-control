"""Surface lifecycle runs only the explicit, bounded automatic focus path."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from project_control.assistance.attention import AttentionController
from project_control.assistance.operator import AssistanceOperator
from project_control.app import Runtime
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity
from project_control.profiles import MCPProfile
from project_control.as1_context import ContextHost
from project_control.as1_surface import SurfaceComposition, compose_surface
from project_control.runtime_binding import local_runtime_identity


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


class _Clock:
    def __init__(self, value=10_000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class _Backend:
    def __init__(self):
        self.opens = 0
        self.closed = 0

    def open_sessions(self, *args, **kwargs):
        self.opens += 1
        raise AssertionError("attention integration must not open inference sessions")

    def close(self):
        self.closed += 1


class _Runtime:
    def __init__(self, config, snapshot):
        self.config = config
        self._snapshot = snapshot

    def snapshot(self, project, **kwargs):
        if project != self._snapshot.workspace_id:
            raise KeyError(project)
        return self._snapshot

    def todo_adapter(self, project):
        return None


def _composition(base: Path, *, clock: _Clock | None = None):
    root = base / "workspace" / "repo"
    root.parent.mkdir(parents=True)
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    (root / "README.md").write_text("bounded attention fixture\n", encoding="utf-8")
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "private-notes.txt").write_text("must never reach the automatic worker\n",
                                             encoding="utf-8")
    _git(root, "add", "README.md", "module.py", "private-notes.txt")
    _git(root, "commit", "-m", "fixture")
    head = _git(root, "rev-parse", "HEAD")
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        authority_repository="source", repositories={
            "source": RepositoryConfig(root=root)})})
    snapshot = ProjectSnapshot(workspace_id="demo", project_uuid="fixture-demo",
        observed_at="2026-10-06T00:00:00Z", todo_revision=3,
        repositories={"source": RepositoryIdentity(commit=head, dirty=False)},
        todo_tables={"tasks": [], "context_fragments": []})
    runtime = _Runtime(config, snapshot)
    skills = base / "skills"
    (skills / "integrations").mkdir(parents=True)
    (skills / "integrations" / "native-skill-catalog.json").write_text(
        json.dumps({"entries": []}), encoding="utf-8")
    state = base / "state"
    backend = _Backend()
    runtime_identity = local_runtime_identity()
    runtime_root = runtime_identity.root
    observer_digest = hashlib.sha256(
        (runtime_root / "local_worker/observer_runtime.py").read_bytes()).hexdigest()
    return root, config, runtime, skills, state, backend, observer_digest, clock


class AttentionSurfaceTests(unittest.TestCase):
    def setUp(self):
        import os

        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.old_cache_home = os.environ.get("XDG_CACHE_HOME")
        self.old_release_pins = {
            name: os.environ.get(name) for name in (
                "PROJECT_CONTROL_RELEASE_MANIFEST", "PROJECT_CONTROL_RELEASE_DIGEST")
        }
        os.environ.pop("PROJECT_CONTROL_RELEASE_MANIFEST", None)
        os.environ.pop("PROJECT_CONTROL_RELEASE_DIGEST", None)
        os.environ["XDG_CACHE_HOME"] = str(self.base / "cache")

    def tearDown(self):
        import os

        if self.old_cache_home is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self.old_cache_home
        for name, value in self.old_release_pins.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temporary.cleanup()

    def make_composition(self, *, clock=None, setup=None):
        if setup is None:
            (root, config, runtime, skills, state, backend, digest, _) = _composition(
                self.base, clock=clock)
        else:
            root, config, runtime, skills, state, digest = setup
            backend = _Backend()
        with patch("project_control.as1_surface.configured_observer_skills_root",
                   return_value=skills):
            composition = compose_surface(runtime, MCPProfile.OBSERVER,
                state_directory=state, backend=backend, observer_runtime_sha256=digest)
        if clock is not None:
            composition.jobs.clock = clock
        return root, composition, backend, state, (root, config, runtime, skills, state, digest)

    def test_start_runs_one_bounded_watcher_and_stops_it_before_jobs_and_backend(self):
        order = []
        calls = threading.Event()

        class Jobs:
            worker_factory = object()

            def start(self):
                order.append("jobs-start")

            def shutdown(self):
                order.append("jobs-shutdown")
                return True

        class Backend:
            def close(self):
                order.append("backend-close")

        composition = SurfaceComposition()
        composition.host = ContextHost("observer", "fixture", frozenset())
        composition.jobs = Jobs()
        composition.backend = Backend()
        composition.attention_interval_seconds = 0.05

        def tick():
            order.append("attention-tick")
            calls.set()

        composition.attention_tick = tick
        self.assertIsNone(getattr(composition, "_attention_thread", None))
        composition.start()
        self.assertTrue(calls.wait(2))
        self.assertTrue(composition.close())
        self.assertEqual(order[0], "jobs-start")
        self.assertLess(order.index("attention-tick"), order.index("jobs-shutdown"))
        self.assertLess(order.index("jobs-shutdown"), order.index("backend-close"))
        tick_count = order.count("attention-tick")
        time.sleep(0.1)
        self.assertEqual(order.count("attention-tick"), tick_count)

    def test_composed_attention_is_cold_until_opted_in_then_queues_private_work_without_lease(self):
        clock = _Clock()
        root, composition, backend, state, setup = self.make_composition(clock=clock)
        try:
            # Composition creates the integration but does not start a watcher.
            self.assertTrue(callable(composition.attention_tick))
            self.assertIsNone(getattr(composition, "_attention_thread", None))
            composition.jobs.clock = clock
            operator = AssistanceOperator(state_root=state, clock=clock)
            operator.set_focus(project="demo", text="observe source changes",
                trusted_projects={"demo"}, trusted_root=root, trusted_repository="source",
                source_paths=())

            calls = []
            original_digest = AttentionController._digest

            def counted(controller, project, relative):
                calls.append((project, relative))
                return original_digest(controller, project, relative)

            with patch.object(AttentionController, "_digest", counted):
                demand_only = composition.attention_tick()
                self.assertEqual(demand_only["status"], "demand_only")
                self.assertEqual(calls, [])

                operator.set_focus(project="demo", text="review the selected file",
                    trusted_projects={"demo"}, trusted_root=root,
                    trusted_repository="source",
                    source_paths=("README.md",), automatic_seconds=600)
                calls.clear()  # Exclude the CLI focus grant's initial baseline read.
                unchanged = composition.attention_tick()
                self.assertEqual(unchanged["status"], "empty")
                self.assertEqual(len(calls), 1)
                self.assertEqual(backend.opens, 0)

                (root / "README.md").write_text("a changed selected source\n", encoding="utf-8")
                nominated = composition.attention_tick()
                self.assertEqual(nominated["status"], "empty")  # Debounce starts at detection.
                clock.value += 3
                enqueue = composition.jobs.enqueue_preparation

                def lose_successful_reply(**request):
                    enqueue(**request)  # Persist the broker job, then lose its acknowledgement.
                    raise RuntimeError("simulated_lost_admission_reply")

                with patch.object(composition.jobs, "enqueue_preparation", lose_successful_reply):
                    uncertain = composition.attention_tick()
                self.assertEqual(uncertain["status"], "admission_unknown")
                self.assertTrue(composition.close())

            # A fresh composition/process reconciles the immutable admission
            # intent instead of charging or creating a second private root.
            root, composition, backend, state, _ = self.make_composition(
                clock=clock, setup=setup)
            reconciled = composition.attention_tick()
            self.assertEqual(reconciled["status"], "empty")
            with composition.jobs._db() as db:
                candidate = db.execute("SELECT status,job_id FROM pa1_attention_candidates").fetchone()
                self.assertEqual(candidate["status"], "dispatched")
                job_id = candidate["job_id"]
                self.assertEqual(db.execute("SELECT count(*) FROM pa1_automatic_work").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE inquiry=0").fetchone()[0], 1)
            self.assertTrue(job_id)
            scope = composition.host.scope("demo")
            self.assertEqual(composition.jobs.lookup(job_id, access_scope=scope)["status"], "not_found")
            self.assertEqual(composition.jobs.log(access_scope=scope), [])
            with composition.jobs._db() as db:
                row = db.execute("SELECT inquiry,scope FROM jobs WHERE id=?", (job_id,)).fetchone()
                self.assertEqual(row["inquiry"], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM execution_slots WHERE job=?",
                                            (job_id,)).fetchone()[0], 0)
                self.assertIsNotNone(db.execute("SELECT 1 FROM pa1_private_ids WHERE job_id=?",
                                                (job_id,)).fetchone())
                self.assertIsNotNone(db.execute("SELECT 1 FROM pa1_automatic_work WHERE job_id=?",
                                                (job_id,)).fetchone())
            # Claim only the private job lease, then exercise the actual
            # composed worker port without running its model backend.
            claimed = composition.jobs.claim()
            self.assertIsNotNone(claimed)
            worker = composition.jobs.worker_factory(composition.jobs, claimed)
            try:
                with patch("subprocess.Popen", side_effect=AssertionError(
                        "selected automatic reads must not launch a process")):
                    selected = worker.command.run(
                        ["cat", str(root / "README.md")], str(root), max_output_bytes=8192)
                self.assertEqual(selected["status"], "completed")
                self.assertEqual(selected["stdout"], "a changed selected source\n")
                self.assertEqual(selected["operation"], "selected_file_read")
                self.assertFalse(selected["executed_subprocess"])
                self.assertEqual(selected["sources"][0]["path"], "README.md")
                with open(root / "README.md", "rb") as selected_file:
                    expected = hashlib.sha256(selected_file.read()).hexdigest()
                self.assertEqual(selected["sources"][0]["content_sha256"], expected)

                with patch("subprocess.Popen", side_effect=AssertionError(
                        "denied automatic commands must not launch a process")):
                    outside = worker.command.run(
                        ["cat", str(root / "private-notes.txt")], str(root))
                    self.assertEqual(outside["status"], "denied")
                    option = worker.command.run(
                        ["cat", "-n", str(root / "README.md")], str(root))
                    self.assertEqual(option["status"], "denied")
                    opaque = worker.command.run(["rg", "private-notes", "."], str(root))
                    self.assertEqual(opaque["status"], "denied")
                from project_control.assistance.frames import FrameError
                with patch.object(composition.jobs, "preparation_read_scope",
                                  side_effect=FrameError("stale attempt")), \
                     patch.object(type(worker.command), "_read",
                                  wraps=worker.command._read) as source_read:
                    stale_attempt = worker.command.run(
                        ["cat", str(root / "README.md")], str(root))
                    self.assertEqual(stale_attempt["status"], "denied")
                    self.assertEqual(stale_attempt["reason"], "automatic_scope_unavailable")
                    source_read.assert_not_called()

                parent = root.parent
                moved_parent = parent.with_name(parent.name + "-moved")
                parent.rename(moved_parent)
                parent.symlink_to(moved_parent, target_is_directory=True)
                try:
                    with self.assertRaises(OSError):
                        worker.command._read(root, "README.md")
                    with patch.object(type(worker.command), "_read",
                                      wraps=worker.command._read) as source_read:
                        swapped_root = worker.command.run(
                            ["cat", str(root / "README.md")], str(root))
                        self.assertEqual(swapped_root["status"], "denied")
                        source_read.assert_not_called()
                finally:
                    parent.unlink()
                    moved_parent.rename(parent)
                info = worker.tools("evidence", {"project": "demo",
                    "subject": "private-notes.txt", "kinds": [], "max_items": 5})
                self.assertEqual(info["status"], "denied")
                self.assertEqual(info["reason"], "automatic_source_scope_denied")
                (root / "README.md").write_text("changed after admission\n", encoding="utf-8")
                stale_bytes = worker.command.run(
                    ["cat", str(root / "README.md")], str(root))
                self.assertEqual(stale_bytes["status"], "denied")
                self.assertEqual(stale_bytes["reason"], "automatic_source_changed")
                self.assertEqual(backend.opens, 0)
            finally:
                self.assertTrue(composition.jobs.finish(claimed.job_id, claimed.attempt,
                    {"status": "failed", "reason": "surface_scope_test_complete"}))
            with self.assertRaises(Exception):
                worker.command.run(["cat", str(root / "README.md")], str(root))
            with composition.jobs._db() as db:
                # Manual test claim bypasses the normal dispatcher's finally
                # block, so release its fixture-only lease explicitly.
                db.execute("DELETE FROM execution_slots WHERE job=?", (job_id,))
                self.assertEqual(db.execute("SELECT count(*) FROM execution_slots WHERE job=?",
                                            (job_id,)).fetchone()[0], 0)
        finally:
            self.assertTrue(composition.close())


if __name__ == "__main__":
    unittest.main()
