"""Acceptance probes for durable knowledge pins and public inquiry identity."""
from __future__ import annotations

import json
import tempfile
import time
import unittest

from project_control.as1_contracts import SourceLocator
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore


SCOPE = {"principal": "acceptance", "profile": "observer", "project": "fixture"}


class ScriptedBackend:
    """Small session adapter so these scenarios exercise JobService dispatch."""

    def __init__(self):
        self.next_id = 0

    def open_sessions(self, count, **_kwargs):
        sessions = []
        for _ in range(count):
            self.next_id += 1
            sessions.append(f"acceptance-session-{self.next_id}")
        return {"status": "available", "session_ids": sessions}

    def close_session(self, _session):
        return {"released": True}


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("scripted JobService inquiry did not settle within 8 seconds")


class RetentionAcceptanceTests(unittest.TestCase):
    def test_note_owned_evidence_survives_more_than_fifty_public_inquiries_and_gc(self):
        with tempfile.TemporaryDirectory(prefix="pc-a18-retention-") as directory:
            root = __import__("pathlib").Path(directory)
            packet_time = [time.time()]
            packets = SQLitePacketStore(root / "packets", namespace="a18-retention",
                                        clock=lambda: packet_time[0])
            source = SourceLocator(project="fixture", repository="fixture", path="facts.md",
                                   content_sha256="a" * 64)
            evidence = packets.create(tool="read", payload={"text": "retained source"},
                                      access_scope=SCOPE, sources=[source])
            unpinned_control = packets.create(tool="read", payload={"text": "expires without a pin"},
                                              access_scope=SCOPE, ttl_seconds=1)
            packets.put_note({
                "note_id": "retained-contract", "project": "fixture", "kind": "finding",
                "claim": "The fixture contract has one retained source.",
                "reason_matters": "This note must remain inspectable after recent inquiries rotate.",
                "sources": [source.model_dump(mode="json")],
                "evidence_packets": [evidence.packet_id],
                "dependencies": {"file:fixture/facts.md": "a" * 64},
                "coverage": {"scenario": "bounded acceptance"},
                "uncertainty": ["One fixture source was used."], "provenance": "source",
            }, access_scope=SCOPE)

            def factory(_service, job):
                class Worker:
                    def run(self, _request):
                        return {"status": "completed", "answer": f"answer {job.question}"}
                return Worker()

            service = JobService(root / "jobs", packets=packets, worker_factory=factory,
                                 backend=ScriptedBackend(),
                                 freshness_provider=lambda _job: {"fresh": True}).start()
            try:
                answers = []
                for index in range(52):
                    question = f"unrelated acceptance inquiry {index}"
                    first = service.inquire(question, SCOPE, foreground_timeout=3)
                    if first.get("status") != "completed":
                        first = wait_for(lambda: (value if (value := service.inquire(
                            question, SCOPE, foreground_timeout=0)).get("status") == "completed" else None))
                    answers.append(first["job"]["job_id"])

                service.reconcile()
                with service._db() as db:
                    indexed = db.execute("SELECT count(*) FROM inquiry_index").fetchone()[0]
                    total = db.execute("SELECT count(*) FROM jobs WHERE inquiry=1").fetchone()[0]
                    oldest = json.loads(db.execute("SELECT record FROM jobs WHERE id=?",
                                                   (answers[0],)).fetchone()[0])
                self.assertEqual(total, 52)
                self.assertEqual(indexed, 50)

                packet_time[0] += 8 * 24 * 60 * 60
                collected = packets.gc()
                self.assertIn(unpinned_control.packet_id, collected)
                self.assertEqual(packets.lookup(unpinned_control.packet_id,
                                                access_scope=SCOPE).status, "expired")
                self.assertNotIn(evidence.packet_id, collected)
                self.assertEqual(packets.lookup(evidence.packet_id, access_scope=SCOPE).status, "ok")
                self.assertEqual(packets.get_note("retained-contract", access_scope=SCOPE)[
                    "evidence_packets"], [evidence.packet_id])

                # The oldest public answer has left the recent inquiry index;
                # its note-owned source remains independently retrievable.
                self.assertEqual(oldest["question"], "unrelated acceptance inquiry 0")
                self.assertEqual(packets.lookup(evidence.packet_id, access_scope=SCOPE).status, "ok")
            finally:
                self.assertTrue(service.shutdown(timeout=5))

    def test_exact_identity_whitespace_refresh_historical_warning_and_negative_cache(self):
        with tempfile.TemporaryDirectory(prefix="pc-a19-cache-") as directory:
            root = __import__("pathlib").Path(directory)
            packets = SQLitePacketStore(root / "packets", namespace="a19-cache")
            freshness_mode = {"state": "fresh"}
            calls: list[tuple[str, bool]] = []

            def freshness(job):
                if freshness_mode["state"] == "fresh":
                    return {"fresh": True}
                if freshness_mode["state"] == "historical":
                    ref = job["evidence_packets"][0]
                    return {"fresh": False, "changed_sources": [
                        {"reference": ref, "reason": "stale",
                         "dependencies": [{"reason": "volatile_observation_expired"}]},
                        {"reason": "dependency_manifest_missing"},
                    ]}
                return {"fresh": False, "changed_sources": [{"reason": "changed"}]}

            def factory(service, job):
                class Worker:
                    def run(self, request):
                        refreshed = bool(request.get("refresh_context"))
                        calls.append((job.question, refreshed))
                        if job.question == "historical command":
                            service.observe(job.job_id, job.attempt, {
                                "status": "completed", "exit_code": 0,
                                "stdout": "historical command output",
                                "truncated": False, "timed_out": False,
                            })
                        if refreshed:
                            freshness_mode["state"] = "fresh"
                        return {"status": "completed", "answer":
                                "refreshed supported answer" if refreshed else f"answer for {job.question}"}
                return Worker()

            service = JobService(root / "jobs", packets=packets, worker_factory=factory,
                                 backend=ScriptedBackend(), freshness_provider=freshness,
                                 retry_seconds=0).start()
            try:
                exact = "  exact   question  "
                first = service.inquire(exact, SCOPE, foreground_timeout=3)
                self.assertEqual(first["status"], "completed")
                exact_hit = service.inquire(exact, SCOPE, foreground_timeout=0)
                self.assertEqual(exact_hit["job"]["job_id"], first["job"]["job_id"])
                self.assertEqual(len([call for call in calls if call[0] == exact]), 1)

                whitespace_variant = service.inquire("exact question", SCOPE, foreground_timeout=3)
                self.assertEqual(whitespace_variant["status"], "completed")
                self.assertNotEqual(whitespace_variant["job"]["job_id"], first["job"]["job_id"])

                freshness_mode["state"] = "refreshable"
                before_refresh = service.inquire("refreshable answer", SCOPE, foreground_timeout=3)
                self.assertEqual(before_refresh["status"], "completed")
                refreshed = service.inquire("refreshable answer", SCOPE, foreground_timeout=3)
                if refreshed["status"] != "completed" or refreshed["job"]["answer"] != "refreshed supported answer":
                    refreshed = wait_for(lambda: (value if (value := service.inquire(
                        "refreshable answer", SCOPE, foreground_timeout=0)).get("status") == "completed"
                        and value.get("job", {}).get("answer") == "refreshed supported answer" else None))
                self.assertEqual(refreshed["job"]["answer"], "refreshed supported answer")
                self.assertEqual([call for call in calls if call[0] == "refreshable answer"],
                                 [("refreshable answer", False), ("refreshable answer", True)])

                freshness_mode["state"] = "historical"
                historical = service.inquire("historical command", SCOPE, foreground_timeout=3)
                self.assertEqual(historical["status"], "completed")
                warning = service.inquire("historical command", SCOPE, foreground_timeout=0)
                self.assertEqual(warning["status"], "partial")
                self.assertTrue(any("freshness is not verified" in question
                                    for question in warning["job"]["unresolved_questions"]))
                self.assertEqual(warning["observations"][0]["stdout"], "historical command output")
                self.assertEqual(len([call for call in calls if call[0] == "historical command"]), 1)

                negative_calls = []
                # Swap in a deterministic worker only for a new service namespace
                # state: empty failures must stay negative and never be refreshed.
                service.shutdown(timeout=5)
                def failing_factory(_service, job):
                    class FailedWorker:
                        def run(self, _request):
                            negative_calls.append(job.question)
                            return {"status": "failed", "reason": "final_round_requires_answer"}
                    return FailedWorker()
                failed = JobService(root / "failed-jobs", packets=packets,
                    worker_factory=failing_factory, backend=ScriptedBackend(),
                    freshness_provider=lambda _job: (_ for _ in ()).throw(
                        AssertionError("negative cache must not ask freshness")), retry_seconds=0).start()
                try:
                    negative = failed.inquire("empty failure", SCOPE, foreground_timeout=3)
                    self.assertEqual(negative["status"], "unavailable")
                    again = failed.inquire("empty failure", SCOPE, foreground_timeout=0)
                    self.assertEqual(again["status"], "unavailable")
                    self.assertEqual(negative_calls, ["empty failure"])
                finally:
                    self.assertTrue(failed.shutdown(timeout=5))
            finally:
                if service._thread is not None and service._thread.is_alive():
                    self.assertTrue(service.shutdown(timeout=5))


if __name__ == "__main__":
    unittest.main()
