"""Actual JobService tests for bounded private observer frames."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_contracts import SourceLocator
from project_control.runtime_binding import bind_local_runtime
from project_control.assistance.evaluation import FakeClock
from project_control.assistance.frames import FrameStore


class FakeBackend:
    """CPU-only stand-in for the existing backend session seam."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next = 0
        self.active: set[str] = set()
        self.opened: list[str] = []
        self.closed: list[str] = []

    def central_status(self):
        return {"status": "available"}

    def open_sessions(self, count, **_kwargs):
        with self._lock:
            sessions = []
            for _ in range(count):
                self._next += 1
                session = f"fixture-session-{self._next}"
                self.active.add(session)
                self.opened.append(session)
                sessions.append(session)
        return {"status": "available", "session_ids": sessions}

    def close_session(self, session):
        with self._lock:
            self.active.discard(session)
            self.closed.append(session)
        return {"released": True}


class Harness:
    def __init__(self, *, factory, clock=None, backend=None, can_execute=None, freshness_provider=None):
        self.temporary = tempfile.TemporaryDirectory(prefix="pa1-frames-")
        self.area = Path(self.temporary.name)
        self.clock = clock or FakeClock()
        self.backend = backend or FakeBackend()
        self.packets = SQLitePacketStore(self.area / "packets", namespace="pa1-frame-tests", clock=self.clock)
        self.service = JobService(self.area / "jobs", packets=self.packets, worker_factory=factory,
                                  backend=self.backend, clock=self.clock, can_execute=can_execute,
                                  freshness_provider=freshness_provider)
        self.service.start()

    def close(self):
        try:
            self.service.shutdown(timeout=5)
        finally:
            self.temporary.cleanup()

    def submit(self, question, scope, *, request_id=None):
        value = self.service.submit(question=question, access_scope=scope, request_id=request_id)
        if not value.get("accepted"):
            raise AssertionError(f"fixture public submission rejected: {value}")
        return value

    def wait_for(self, predicate, *, timeout=6, message="condition did not become true"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError(message)

    def record(self, job_id):
        with self.service._db() as db:
            row = db.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def frame(self, job_id):
        with self.service._db() as db:
            return FrameStore(db).get_frame(job_id)


def _scope(project):
    return {"principal": "fixture-user", "profile": "source-test", "project": project,
            "source_set": [f"{project}/only.txt"]}


def _response(request, status, **fields):
    used = request.get("model_turns_used", 0) + 1
    return {"status": status, "model_turns_used": 1,
            "cumulative_model_turns_used": used, **fields}


class TestFrameBroker(unittest.TestCase):
    def setUp(self):
        self._release_environment = {
            key: os.environ.get(key)
            for key in ("PROJECT_CONTROL_RELEASE_MANIFEST", "PROJECT_CONTROL_RELEASE_DIGEST")
        }
        os.environ.pop("PROJECT_CONTROL_RELEASE_MANIFEST", None)
        os.environ.pop("PROJECT_CONTROL_RELEASE_DIGEST", None)
        self.runtime_identity = bind_local_runtime()

    def tearDown(self):
        for key, value in self._release_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def wait_for(self, predicate, *, timeout=6, message="condition did not become true"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.01)
        self.fail(message)

    def test_two_parents_release_before_children_and_children_run_in_two_slots(self):
        backend = FakeBackend()
        child_started = {"parent-a": threading.Event(), "parent-b": threading.Event()}
        release_children = threading.Event()
        parent_runs = {"parent-a": 0, "parent-b": 0}
        child_ids = {}
        requests = {}
        source_area = tempfile.TemporaryDirectory(prefix="pa1-child-source-")
        source_files = {}
        source_digests = {}
        for name in ("parent-a", "parent-b"):
            project = "project-a" if name == "parent-a" else "project-b"
            source_file = Path(source_area.name) / project / "only.txt"
            source_file.parent.mkdir()
            source_file.write_text(f"source witness for {name}\n", encoding="utf-8")
            source_files[name] = source_file
            source_digests[name] = hashlib.sha256(source_file.read_bytes()).hexdigest()

        class Factory:
            def __call__(self, service, job):
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        assert max_model_turns == 1
                        requests.setdefault(job.job_id, []).append(json.loads(json.dumps(request)))
                        if job.question in parent_runs:
                            parent_runs[job.question] += 1
                            if not request.get("private_child_results"):
                                return _response(request, "yielding", reason="private_wait_proposed",
                                    private_wait={"kind": "child", "question": f"child-{job.question}", "max_turns": 1})
                            children = request["private_child_results"]
                            assert len(children) == 1
                            assert all("hidden_reasoning" not in child["result"] for child in children)
                            return _response(request, "completed", answer=f"integrated-{job.question}",
                                private_child_results_consumed=[children[0]["child_id"]])

                        if job.question.startswith("child-parent-"):
                            parent_name = job.question.removeprefix("child-")
                            child_started[parent_name].set()
                            if not release_children.wait(4):
                                raise AssertionError("child fixture release timed out")
                            content = source_files[parent_name].read_text(encoding="utf-8")
                            project = "project-a" if parent_name == "parent-a" else "project-b"
                            source = SourceLocator(project=project, repository="frame-fixture",
                                path=f"{project}/only.txt",
                                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                revision=f"fixture-{parent_name}").model_dump()
                            evidence = service.observe(job.job_id, job.attempt,
                                {"text": content, "sources": [source]}, tool="source")
                            return _response(request, "completed", answer=f"private-output-{parent_name}",
                                evidence_packets=[evidence], hidden_reasoning="never expose", analysis="never expose")

                        return _response(request, "completed", answer="public filler completed")
                return Worker()

        def identify_created_child(service, parent_id, name):
            self.wait_for(lambda: _has_private_child(service, parent_id),
                          message=f"{name} did not create its child")
            with service._db() as db:
                row = db.execute("SELECT child_id FROM pa1_frame_children WHERE parent_id=?", (parent_id,)).fetchone()
            child_ids[name] = row[0]

        harness = Harness(factory=Factory(), backend=backend,
                          can_execute=lambda job, _inquiry: not job.question.startswith("public-filler-"))
        try:
            scope_a, scope_b = _scope("project-a"), _scope("project-b")
            parent_a = harness.submit("parent-a", scope_a)["job_id"]
            parent_b = harness.submit("parent-b", scope_b)["job_id"]
            identify_created_child(harness.service, parent_a, "parent-a")
            identify_created_child(harness.service, parent_b, "parent-b")
            self.assertTrue(child_started["parent-a"].wait(3),
                f"child A stalled; jobs={_job_statuses(harness.service)} error={harness.service.last_error}")
            self.assertTrue(child_started["parent-b"].wait(3),
                f"child B stalled; jobs={_job_statuses(harness.service)} error={harness.service.last_error}")

            for index in range(4):
                result = harness.service.submit(question=f"public-filler-{index}", access_scope=_scope("filler"))
                self.assertTrue(result["accepted"])
            rejected = harness.service.submit(question="public-over-cap", access_scope=_scope("filler"))
            self.assertEqual((rejected["accepted"], rejected["reason"]), (False, "admission_limit"))

            with backend._lock:
                self.assertEqual(len(backend.active), 2)
            for name, parent_id, scope in (("parent-a", parent_a, scope_a), ("parent-b", parent_b, scope_b)):
                with harness.service._db() as db:
                    wait = db.execute("SELECT release_verified FROM pa1_frame_waits WHERE parent_id=?",
                                      (parent_id,)).fetchone()
                    parent_slot = db.execute("SELECT 1 FROM execution_slots WHERE job=?", (parent_id,)).fetchone()
                    child_id = db.execute("SELECT child_id FROM pa1_frame_children WHERE parent_id=?",
                                          (parent_id,)).fetchone()[0]
                    child_frame = FrameStore(db).get_frame(child_id)
                    parent_frame = FrameStore(db).get_frame(parent_id)
                self.assertTrue(wait[0])
                self.assertIsNone(parent_slot)
                self.assertEqual(child_frame.state, "running")
                self.assertEqual(parent_frame.state, "suspended")
                self.assertEqual(requests[child_id][0]["scope"], scope)
                self.assertEqual(requests[child_id][0]["trusted_turn_policy_id"], "gather-v1")
            with harness.service._db() as db:
                public_roots = db.execute("""SELECT count(*) FROM jobs j JOIN pa1_frames f ON f.job_id=j.id
                    WHERE f.parent_id IS NULL AND json_extract(j.record,'$.status') NOT IN
                    ('completed','partial','failed','cancelled')""").fetchone()[0]
                slots = db.execute("SELECT count(*) FROM execution_slots").fetchone()[0]
            self.assertEqual(public_roots, 6)
            self.assertEqual(slots, 2)

            release_children.set()
            for parent_id in (parent_a, parent_b):
                harness.wait_for(lambda job_id=parent_id: harness.record(job_id)["status"] == "completed",
                                 message="parent did not resume after its own child")
            for name, parent_id in (("parent-a", parent_a), ("parent-b", parent_b)):
                request = requests[parent_id][-1]
                self.assertEqual(request["trusted_turn_policy_id"], "synthesis-v1")
                self.assertEqual(request["scope"], scope_a if name == "parent-a" else scope_b)
                self.assertEqual(harness.record(parent_id)["answer"], f"integrated-{name}")
                child_id = child_ids[name]
                self.assertIn(child_id, [item["child_id"] for item in request["private_child_results"]])
                self.assertEqual(harness.service.lookup(child_id, access_scope=scope_a)["status"], "not_found")
                self.assertNotIn(child_id, json.dumps(harness.service.log(access_scope=scope_a, query="private-output")))
                other_name = "parent-b" if name == "parent-a" else "parent-a"
                self.assertIn(f"private-output-{name}", json.dumps(request))
                self.assertNotIn(f"private-output-{other_name}", json.dumps(request))
                self.assertNotIn("hidden_reasoning", json.dumps(request))
                self.assertNotIn("never expose", json.dumps(request))
                visible = harness.service.lookup(parent_id, access_scope=scope_a if name == "parent-a" else scope_b)
                provenance = [item for item in visible["observations"]
                              if item.get("provenance_note")]
                self.assertEqual(len(provenance), 1)
                synthetic = provenance[0]
                self.assertEqual(synthetic["provenance_note"],
                    "Private child result with inherited source provenance; not independent corroboration.")
                self.assertEqual(synthetic["source_locators"][0]["content_sha256"], source_digests[name])
                self.assertEqual({item["content_sha256"] for item in synthetic["source_locators"]},
                                 {source_digests[name]})
                synthetic_packet = harness.packets.lookup(synthetic["packet_id"],
                    access_scope=scope_a if name == "parent-a" else scope_b).packet
                self.assertEqual({source.content_sha256 for source in synthetic_packet.sources},
                                 {source_digests[name]})
                project = "project-a" if name == "parent-a" else "project-b"
                self.assertEqual({source.path for source in synthetic_packet.sources}, {f"{project}/only.txt"})
                self.assertEqual(set(synthetic_packet.parents), set(synthetic["child_evidence_packets"]))
                self.assertNotIn(child_id, json.dumps(synthetic))
        finally:
            release_children.set()
            harness.close()
            source_area.cleanup()

    def test_independent_watchdog_expires_two_blocked_workers_without_releasing_slots(self):
        clock = FakeClock(2_000_000)
        entered = threading.Barrier(2)
        allow_return = threading.Event()
        invocations = []

        class Factory:
            def __call__(self, _service, job):
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        invocations.append((job.job_id, request["deadline_epoch"]))
                        entered.wait(timeout=3)
                        if not allow_return.wait(4):
                            raise AssertionError("blocked fake worker was not released")
                        return _response(request, "completed", answer="late result must be fenced")
                return Worker()

        harness = Harness(factory=Factory(), clock=clock)
        try:
            scope = _scope("watchdog")
            first = harness.submit("blocked-first", scope, request_id="blocked-first")["job_id"]
            second = harness.submit("blocked-second", scope, request_id="blocked-second")["job_id"]
            self.wait_for(lambda: len(invocations) == 2, timeout=4, message="both worker slots did not block")
            self.assertTrue(harness.service._watchdog.is_alive())
            deadlines = {job_id: deadline for job_id, deadline in invocations}
            self.assertEqual(set(deadlines), {first, second})

            clock.advance(301)
            for job_id in (first, second):
                harness.wait_for(lambda ident=job_id: harness.record(ident)["status"] in {"failed", "partial"},
                                 timeout=3, message="watchdog did not expire blocked public work")
            with harness.service._db() as db:
                slots = {row[0] for row in db.execute("SELECT job FROM execution_slots")}
            self.assertEqual(slots, {first, second})
            with harness.backend._lock:
                self.assertEqual(len(harness.backend.active), 2)

            retry = harness.service.submit(question="blocked-first", access_scope=scope,
                                           request_id="blocked-first")
            self.assertTrue(retry["retry"])
            self.assertEqual(retry["job_id"], first)
            self.assertEqual(harness.record(first)["deadline_epoch"], deadlines[first])

            allow_return.set()
            for job_id in (first, second):
                harness.wait_for(lambda ident=job_id: not _has_slot(harness.service, ident),
                                 message="physical slot did not clear after fake cleanup")
                record = harness.record(job_id)
                self.assertIn(record["status"], {"failed", "partial"})
                self.assertNotEqual(record.get("answer"), "late result must be fenced")
        finally:
            allow_return.set()
            harness.close()

    def test_restart_reconciles_lost_wake_and_duplicate_delivery_once(self):
        parent_resume_enabled = {"value": True}
        child_started = threading.Event()
        parent_calls = []
        child_id = {"value": None}

        class Factory:
            def __call__(self, service, job):
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        if job.question == "restart-parent":
                            parent_calls.append(request.get("private_child_results", []))
                            if not request.get("private_child_results"):
                                parent_resume_enabled["value"] = False
                                return _response(request, "yielding", reason="private_wait_proposed",
                                    private_wait={"kind": "child", "question": "restart-child", "max_turns": 1})
                            results = request["private_child_results"]
                            return _response(request, "completed", answer="resumed once",
                                private_child_results_consumed=[item["child_id"] for item in results])
                        child_id["value"] = job.job_id
                        child_started.set()
                        return _response(request, "completed", answer="child already terminal")
                return Worker()

        scope = _scope("restart")
        can_execute = lambda job, _inquiry: job.question != "restart-parent" or parent_resume_enabled["value"]
        first = Harness(factory=Factory(), can_execute=can_execute)
        try:
            parent_id = first.submit("restart-parent", scope)["job_id"]
            # Simulate a lost notification after the child commits its durable terminal result.
            first.service._wake_ready_parents = lambda: None
            first.wait_for(lambda: child_started.is_set() and child_id["value"] is not None,
                           message="private child did not execute")
            first.wait_for(lambda: first.record(child_id["value"])["status"] == "completed",
                           message="child terminal result was not persisted")
            first.wait_for(lambda: first.frame(parent_id).state == "suspended",
                           message="parent did not release and suspend")
            self.assertEqual(first.service.lookup(child_id["value"], access_scope=scope)["status"], "not_found")
            self.assertTrue(first.service.shutdown(timeout=5))
            # Reopen the same broker and packet databases in a fresh service object.
            first.service = JobService(first.area / "jobs", packets=first.packets, worker_factory=Factory(),
                backend=first.backend, clock=first.clock, can_execute=can_execute)
            first.service.start()
            first.wait_for(lambda: first.record(parent_id)["status"] == "queued_after_eviction",
                           message="restart did not reconcile the committed child result")
            first.service._wake_ready_parents()
            first.service._wake_ready_parents()
            parent_resume_enabled["value"] = True
            first.service._wake.set()
            first.wait_for(lambda: first.record(parent_id)["status"] == "completed",
                           message="parent did not resume after restart")
            self.assertEqual(len(parent_calls), 2)
            with first.service._db() as db:
                wait = db.execute("SELECT wake_version,wake_consumed FROM pa1_frame_waits WHERE parent_id=?",
                                  (parent_id,)).fetchone()
            first.wait_for(lambda: not _has_slot(first.service, parent_id)
                           and _wake_consumed(first.service, parent_id) == 1,
                           message="parent completion did not consume its durable wake")
            with first.service._db() as db:
                wait = db.execute("SELECT wake_version,wake_consumed FROM pa1_frame_waits WHERE parent_id=?",
                                  (parent_id,)).fetchone()
            self.assertEqual(tuple(wait), (1, 1))
            self.assertEqual(first.record(parent_id)["answer"], "resumed once")
        finally:
            first.close()

    def test_successful_yields_use_one_shared_six_turn_budget_and_preserve_inquiry_cache(self):
        seen = []

        class Factory:
            def __call__(self, _service, job):
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        turn = request.get("model_turns_used", 0) + 1
                        seen.append((job.job_id, turn, max_model_turns, request["trusted_turn_policy_id"],
                                     request["scope"]))
                        if turn < 6:
                            return _response(request, "yielding", reason="slice_complete")
                        return _response(request, "completed", answer="finished on the sixth visible turn")
                return Worker()

        harness = Harness(factory=Factory(), freshness_provider=lambda _job: {"fresh": True})
        try:
            scope = _scope("six-turn")
            question = "Continue this bounded inquiry"
            initial = harness.service.inquire(question, scope, foreground_timeout=0)
            self.assertIn(initial["status"], {"thinking", "completed"})
            with harness.service._db() as db:
                job_id = db.execute("SELECT job FROM inquiry_index LIMIT 1").fetchone()[0]
            harness.wait_for(lambda: harness.record(job_id)["status"] == "completed",
                             message="successful worker slices exhausted before the bounded final answer")
            record = harness.record(job_id)
            frame = harness.frame(job_id)
            self.assertEqual(record["attempt"], 6, "normal yields must not consume a three-failure retry budget")
            self.assertEqual(frame.turns_used, 6)
            self.assertEqual([item[1] for item in seen], [1, 2, 3, 4, 5, 6])
            self.assertTrue(all(item[2] == 1 and item[3] == "gather-v1" and item[4] == scope for item in seen))

            harness.wait_for(lambda: harness.service._settled(job_id),
                             message="public inquiry outbox or execution slot did not settle")
            cached = harness.service.inquire(question, scope, foreground_timeout=0)
            self.assertEqual(cached["status"], "completed")
            self.assertEqual(cached["job"]["job_id"], job_id)
            self.assertEqual(cached["job"]["deadline_epoch"], record["deadline_epoch"])
            self.assertEqual(len(seen), 6)
        finally:
            harness.close()

    def test_child_turns_and_parent_turns_consume_one_shared_root_budget(self):
        observed = []

        class Factory:
            def __call__(self, _service, job):
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        turn = request.get("model_turns_used", 0) + 1
                        observed.append((job.question, turn, request["trusted_turn_policy_id"],
                                         list(request.get("private_child_results", []))))
                        if job.question == "shared-budget-root":
                            if request.get("private_child_results"):
                                return _response(request, "completed", answer="six turns shared",
                                    private_child_results_consumed=[
                                        item["child_id"] for item in request["private_child_results"]])
                            if turn < 3:
                                return _response(request, "yielding", reason="slice_complete")
                            return _response(request, "yielding", reason="private_wait_proposed",
                                private_wait={"kind": "child", "question": "shared-budget-child", "max_turns": 2})
                        if turn == 1:
                            return _response(request, "yielding", reason="slice_complete")
                        return _response(request, "completed", answer="child used two turns")
                return Worker()

        harness = Harness(factory=Factory())
        try:
            scope = _scope("shared-budget")
            root_id = harness.submit("shared-budget-root", scope)["job_id"]
            harness.wait_for(lambda: harness.record(root_id)["status"] == "completed",
                             message="parent and child did not finish inside shared six-turn budget")
            root = harness.frame(root_id)
            self.assertEqual(root.root_turns_used, 6)
            self.assertEqual(root.root_turns_reserved, 0)
            self.assertEqual([item[1] for item in observed if item[0] == "shared-budget-root"], [1, 2, 3, 6])
            self.assertEqual([item[1] for item in observed if item[0] == "shared-budget-child"], [1, 2])
            child_resume = next(item for item in observed if item[0] == "shared-budget-root" and item[1] == 6)
            self.assertEqual(child_resume[2], "synthesis-v1")
            self.assertEqual(len(child_resume[3]), 1)
        finally:
            harness.close()

    def test_private_descendant_depth_is_bounded_by_actual_job_service(self):
        class Factory:
            def __call__(self, service, job):
                with service._db() as db:
                    frame = FrameStore(db).get_frame(job.job_id)
                class Worker:
                    def run_slice(self, request, *, max_model_turns):
                        if request.get("private_child_results"):
                            children = request["private_child_results"]
                            return _response(request, "completed", answer=f"resumed-depth-{frame.depth}",
                                private_child_results_consumed=[child["child_id"] for child in children])
                        return _response(request, "yielding", reason="private_wait_proposed",
                            private_wait={"kind": "child", "question": f"depth-{frame.depth + 1}", "max_turns": 1})
                return Worker()

        harness = Harness(factory=Factory())
        try:
            scope = _scope("depth")
            root_id = harness.submit("depth-root", scope)["job_id"]
            harness.wait_for(lambda: harness.record(root_id)["status"] == "completed",
                             timeout=8, message=f"bounded descendant chain did not unwind: {harness.record(root_id)}; {harness.service.last_error}")
            with harness.service._db() as db:
                private_ids = [row[0] for row in db.execute("SELECT job_id FROM pa1_private_ids")]
                frames = db.execute("SELECT max(depth) FROM pa1_frames").fetchone()[0]
                waits = db.execute("SELECT count(*) FROM pa1_frame_waits").fetchone()[0]
            self.assertEqual(len(private_ids), 2)
            self.assertLessEqual(frames, 2)
            self.assertEqual(waits, 2)
            self.assertLessEqual(harness.frame(root_id).root_turns_used, 6)
        finally:
            harness.close()


def _has_private_child(service, parent_id):
    with service._db() as db:
        return db.execute("SELECT 1 FROM pa1_frame_children WHERE parent_id=?", (parent_id,)).fetchone() is not None


def _has_slot(service, job_id):
    with service._db() as db:
        return db.execute("SELECT 1 FROM execution_slots WHERE job=?", (job_id,)).fetchone() is not None


def _wake_consumed(service, parent_id):
    with service._db() as db:
        row = db.execute("SELECT wake_consumed FROM pa1_frame_waits WHERE parent_id=? ORDER BY generation DESC LIMIT 1",
                         (parent_id,)).fetchone()
    return row[0] if row else None


def _job_statuses(service):
    with service._db() as db:
        return [(row["id"], json.loads(row["record"])["question"], json.loads(row["record"])["status"])
                for row in db.execute("SELECT id,record FROM jobs")]
