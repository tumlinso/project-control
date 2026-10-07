"""Actual-source CPU tests for the PA1 source-mode evaluation seam."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import tempfile
import unittest

from project_control.assistance.evaluation import (
    ExperimentLedger,
    FakeClock,
    ScriptedStep,
    classify_failure,
    run_source_evaluation,
    source_observation,
)
from project_control.as1_contracts import SourceLocator
from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "planning/project-assistance-v1/fixtures/repository"


class FastLoopTests(unittest.TestCase):
    def run_trial(self, name: str, steps: list[ScriptedStep], *, clock: FakeClock | None = None):
        directory = Path(tempfile.mkdtemp(prefix=f"pa1-{name}-"))
        return run_source_evaluation(
            directory,
            question="Read the fixture and report the configured inquiry deadline.",
            steps=steps,
            clock=clock,
            source_root=ROOT,
        )

    def test_runs_actual_job_service_with_private_sqlite_and_source_identity(self):
        payload = source_observation(FIXTURE, "demo/budgets.py")
        result = self.run_trial("source", [
            ScriptedStep(observation=payload),
            ScriptedStep(result={
                "status": "completed",
                "answer": "The whole inquiry timeout is 300 seconds.",
            }),
        ])

        self.assertEqual(result.status, "completed")
        self.assertIn("300 seconds", result.answer)
        self.assertGreater(len(result.observations), 0)
        self.assertFalse(result.production_state_used)
        self.assertFalse(result.backend_used)
        self.assertFalse(result.inference_used)
        self.assertTrue(Path(result.job_database).is_relative_to(Path(tempfile.gettempdir())))
        self.assertTrue(Path(result.packet_database).is_relative_to(Path(tempfile.gettempdir())))
        for module, identity in result.source_modules.items():
            path = Path(identity["path"])
            self.assertEqual(path, ROOT / "src/project_control" / f"{module}.py")
            self.assertEqual(identity["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_source_locator_is_bound_to_fixture_bytes(self):
        payload = source_observation(FIXTURE, "demo/budgets.py")
        self.assertEqual(len(payload["sources"]), 1)
        locator = payload["sources"][0]
        source_file = FIXTURE / locator["path"]
        self.assertEqual(locator["content_sha256"], hashlib.sha256(source_file.read_bytes()).hexdigest())
        self.assertIn("INQUIRY_SECONDS = 300", payload["text"])

    def test_inquiry_reuses_prepared_source_until_fixture_mutation_then_refreshes(self):
        with tempfile.TemporaryDirectory(prefix="pa1-refresh-") as temporary:
            area = Path(temporary)
            fixture = area / "fixture"
            fixture.mkdir()
            source_file = fixture / "answer.txt"
            source_file.write_text("version one", encoding="utf-8")
            clock = FakeClock(30_000)
            scope = {"principal": "tester", "profile": "source-evaluation", "project": "fixture"}
            packets = SQLitePacketStore(area / "packets", namespace="pa1-refresh-test", clock=clock)
            observed_answers: list[str] = []

            def fresh(previous):
                changed = []
                for packet_id in previous.get("evidence_packets", []):
                    lookup = packets.lookup(packet_id, access_scope=scope)
                    if lookup.status != "ok" or lookup.packet is None:
                        changed.append({"reference": packet_id, "reason": "unverified"})
                        continue
                    for source in lookup.packet.sources:
                        current = fixture / source.path
                        digest = hashlib.sha256(current.read_bytes()).hexdigest() if current.exists() else None
                        if digest != source.content_sha256:
                            changed.append({"reference": packet_id, "reason": "stale", "path": source.path})
                return {"fresh": not changed, "changed_sources": changed}

            def factory(service, job):
                class Worker:
                    def run(self, request):
                        content = source_file.read_text(encoding="utf-8")
                        encoded = content.encode()
                        observed_answers.append(content)
                        packet = service.observe(job.job_id, job.attempt, {
                            "text": content,
                            "sources": [SourceLocator(
                                project="fixture", repository="scratch", path="answer.txt",
                                content_sha256=hashlib.sha256(encoded).hexdigest(), revision="scratch-v1",
                            ).model_dump()],
                        }, tool="source")
                        return {"status": "completed", "answer": content, "findings": [
                            {"text": content, "evidence_packets": [packet]}
                        ]}
                return Worker()

            service = JobService(area / "jobs", packets=packets, worker_factory=factory,
                                 clock=clock, freshness_provider=fresh)
            try:
                service.start()
                first = service.inquire("Read answer.txt", scope, foreground_timeout=0)
                self.assertIn(first["status"], {"thinking", "completed"})
                first_id = self._inquiry_job_id(service)
                self._wait_for_inquiry(service, first_id)
                answer = service.inquire("Read answer.txt", scope, foreground_timeout=0)
                self.assertEqual(answer["status"], "completed")
                first_packet_id = answer["job"]["evidence_packets"][0]
                first_packet = packets.lookup(first_packet_id, access_scope=scope).packet
                self.assertEqual(first_packet.sources[0].content_sha256, hashlib.sha256(b"version one").hexdigest())

                reused = service.inquire("Read answer.txt", scope, foreground_timeout=0)
                self.assertEqual(reused["job"]["job_id"], first_id)
                self.assertEqual(len(observed_answers), 1)

                source_file.write_text("version two", encoding="utf-8")
                refresh = service.inquire("Read answer.txt", scope, foreground_timeout=0)
                self.assertIn(refresh["status"], {"thinking", "completed"})
                second_id = self._inquiry_job_id(service)
                self.assertNotEqual(second_id, first_id)
                self._wait_for_inquiry(service, second_id)
                updated = service.inquire("Read answer.txt", scope, foreground_timeout=0)
                self.assertEqual(updated["status"], "completed")
                self.assertEqual(updated["job"]["job_id"], second_id)
                self.assertEqual(updated["job"]["answer"], "version two")
                second_packet = packets.lookup(updated["job"]["evidence_packets"][0], access_scope=scope).packet
                self.assertEqual(second_packet.sources[0].content_sha256, hashlib.sha256(b"version two").hexdigest())
                self.assertEqual(observed_answers, ["version one", "version two"])
            finally:
                service.shutdown()

    @staticmethod
    def _inquiry_job_id(service):
        with service._db() as db:
            row = db.execute("SELECT job FROM inquiry_index LIMIT 1").fetchone()
        if row is None:
            raise AssertionError("inquiry identity was not persisted")
        return row[0]

    @staticmethod
    def _wait_for_inquiry(service, job_id, timeout=3):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with service._db() as db:
                row = db.execute("SELECT record FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is not None:
                import json as _json
                if (_json.loads(row[0])["status"] in {"completed", "partial", "failed", "cancelled"}
                        and service._settled(job_id)):
                    return
            time.sleep(0.01)
        raise AssertionError("inquiry did not complete within bounded wait")

    def test_retry_reuses_exact_job_and_deadline_and_clock_isolated_per_trial(self):
        first_clock = FakeClock(10_000)
        first = self.run_trial("retry-a", [ScriptedStep(result={"status": "completed", "answer": "done"})], clock=first_clock)
        first_clock.advance(40)
        second_clock = FakeClock(20_000)
        second = self.run_trial("retry-b", [ScriptedStep(result={"status": "completed", "answer": "done"})], clock=second_clock)

        self.assertTrue(first.retry_verified)
        self.assertTrue(second.retry_verified)
        self.assertEqual(first.deadline_epoch, 10_300)
        self.assertEqual(second.deadline_epoch, 20_300)
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(first_clock(), 10_040)
        self.assertEqual(second_clock(), 20_000)

    def test_deadline_exhaustion_classifies_empty_and_evidence_bearing_results(self):
        empty = self.run_trial("deadline-empty", [
            ScriptedStep(delay_seconds=301, result={"status": "completed", "answer": "too late"})
        ])
        self.assertEqual(empty.status, "failed")
        self.assertEqual(empty.answer, "too late")
        self.assertEqual(empty.observations, [])
        self.assertFalse(empty.backend_used)
        self.assertFalse(empty.inference_used)

        partial = self.run_trial("deadline-with-evidence", [
            ScriptedStep(observation=source_observation(FIXTURE, "demo/budgets.py")),
            ScriptedStep(delay_seconds=301, result={"status": "completed", "answer": "too late"}),
        ])
        self.assertEqual(partial.status, "partial")
        self.assertEqual(len(partial.observations), 1)
        self.assertFalse(partial.backend_used)
        self.assertFalse(partial.inference_used)
        failure = classify_failure("cpu", "attempt_or_deadline_exhausted")
        self.assertEqual(failure, {
            "layer": "cpu", "reason": "attempt_or_deadline_exhausted"
        })

    def test_visible_replay_contains_only_public_events_and_no_hidden_reasoning(self):
        result = self.run_trial("trace", [
            ScriptedStep(observation=source_observation(FIXTURE, "demo/budgets.py")),
            ScriptedStep(result={"status": "completed", "answer": "300 seconds"}),
        ])
        self.assertEqual([event["event"] for event in result.visible_trace], [
            "worker_request", "observation_accepted", "worker_result"
        ])
        serialized = json.dumps(result.model_dump()).lower()
        self.assertNotIn("chain_of_thought", serialized)
        self.assertNotIn("hidden_reasoning", serialized)
        self.assertTrue(all("reasoning" not in event for event in result.visible_trace))
        accepted = result.visible_trace[1]
        self.assertIn("demo/budgets.py", accepted["source_paths"])

    def test_experiment_ledger_enforces_declared_entry_and_case_caps(self):
        ledger = ExperimentLedger(max_entries=2, max_cases=1, max_wall_seconds=30)
        ledger.record(case_id="E01", configuration_id="baseline", outcome="pass", elapsed_seconds=0.1)
        with self.assertRaisesRegex(RuntimeError, "case_budget"):
            ledger.record(case_id="E02", configuration_id="candidate-a", outcome="pass", elapsed_seconds=0.2)
        ledger.record(case_id="E01", configuration_id="candidate-a", outcome="partial", elapsed_seconds=0.3,
                      failure=classify_failure("trace", "source proof omitted"))
        with self.assertRaisesRegex(RuntimeError, "entry_budget"):
            ledger.record(case_id="E01", configuration_id="candidate-b", outcome="pass", elapsed_seconds=0.4)
        self.assertEqual(len(ledger.entries), 2)

    def test_experiment_ledger_stops_branch_after_two_non_improving_scores(self):
        ledger = ExperimentLedger(max_entries=8, max_cases=2, max_wall_seconds=30)
        ledger.record(case_id="E01", configuration_id="baseline", branch_id="prompt-a",
                      outcome="pass", elapsed_seconds=0.1, score=1.0)
        first = ledger.record(case_id="E01", configuration_id="candidate-1", branch_id="prompt-a",
                              outcome="pass", elapsed_seconds=0.1, score=0.9)
        second = ledger.record(case_id="E01", configuration_id="candidate-2", branch_id="prompt-a",
                               outcome="pass", elapsed_seconds=0.1, score=0.8)
        self.assertEqual(first["consecutive_non_improvements"], 1)
        self.assertEqual(second["consecutive_non_improvements"], 2)
        with self.assertRaisesRegex(RuntimeError, "two_non_improvements"):
            ledger.record(case_id="E01", configuration_id="candidate-3", branch_id="prompt-a",
                          outcome="pass", elapsed_seconds=0.1, score=0.7)

    def test_admission_rejects_exhausted_budgets_before_candidate_dispatch(self):
        ledger = ExperimentLedger(max_entries=1, max_cases=1, max_wall_seconds=30)
        dispatches = []

        def dispatch(case_id):
            ledger.check_admission(case_ids=[case_id], branch_id="candidate")
            dispatches.append(case_id)

        with self.assertRaisesRegex(RuntimeError, "case_budget"):
            ledger.check_admission(case_count=2, branch_id="candidate")
        with self.assertRaisesRegex(RuntimeError, "entry_budget"):
            ledger.check_admission(case_ids=["E01"], entries=2, branch_id="candidate")
        with self.assertRaisesRegex(RuntimeError, "wall_budget"):
            expired = ExperimentLedger(max_entries=2, max_cases=2, max_wall_seconds=1)
            expired._started_at -= 2
            expired.check_admission(case_count=1)
        self.assertEqual(dispatches, [])
        dispatch("E01")
        self.assertEqual(dispatches, ["E01"])

    def test_admission_rejects_already_stopped_branch_before_candidate_dispatch(self):
        ledger = ExperimentLedger(max_entries=8, max_cases=2, max_wall_seconds=30)
        for index, score in enumerate((1.0, 0.9, 0.8)):
            ledger.record(case_id="E01", configuration_id=f"candidate-{index}", branch_id="prompt-a",
                          outcome="pass", elapsed_seconds=0.1, score=score)
        dispatches = []
        with self.assertRaisesRegex(RuntimeError, "two_non_improvements"):
            ledger.check_admission(case_ids=["E01"], branch_id="prompt-a")
            dispatches.append("must-not-run")
        self.assertEqual(dispatches, [])

    def test_unknown_failure_layer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown evaluation layer"):
            classify_failure("production", "not applicable")


if __name__ == "__main__":
    unittest.main()
