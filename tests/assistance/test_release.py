"""Finite guard tests for the staged PA1 live qualification harness."""
from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from scripts import qualify_assistance_live as live


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "planning/project-assistance-v1"
SOURCE = ROOT / "src/project_control"
FIXTURE = PACKAGE / "fixtures/repository"


class LiveQualificationPlanTests(unittest.TestCase):
    def test_inert_plan_is_explicitly_planned_and_does_not_claim_inference(self):
        plan = live.source_plan(PACKAGE, SOURCE, FIXTURE)
        self.assertEqual(plan["status"], "planned")
        self.assertIs(plan["inference_performed"], False)
        self.assertEqual(plan["case_ids"], ["E01", "E02", "E03", "E04"])
        self.assertEqual(plan["initial_budget"]["max_new_inquiries"], 12)
        self.assertEqual(plan["initial_budget"]["wall_seconds"], 900)
        self.assertEqual(plan["initial_budget"]["max_parallel_executions"], 2)
        self.assertEqual(plan["held_out_budget"]["max_new_inquiries"], 4)
        self.assertEqual(plan["held_out_budget"]["wall_seconds"], 300)
        self.assertIn("A31", plan["counterexample_case_id"])
        self.assertIn("A37", plan["continuation_case_id"])
        self.assertNotIn("qualified", plan)

    def test_initial_budget_rejects_over_cap_and_case_underfunding(self):
        with self.assertRaises(live.QualificationError):
            live._effective_budget("baseline", 4, 13, 900)
        with self.assertRaises(live.QualificationError):
            live._effective_budget("baseline", 4, 3, 900)

    def test_held_out_budget_has_separate_cap(self):
        budget = live._effective_budget("heldout", 4, 4, 300)
        self.assertEqual(budget["max_new_inquiries"], 4)
        self.assertEqual(budget["wall_seconds"], 300)
        with self.assertRaises(live.QualificationError):
            live._effective_budget("heldout", 4, 4, 301)

    def test_no_owned_receipts_means_two_slots_are_not_proven(self):
        identity = live._slot_identity({"slots": []}, [])
        self.assertIs(identity["two_distinct_slots"], False)
        self.assertIs(identity["same_model_sha256"], False)
        self.assertIs(identity["same_binary_sha256"], False)

    def test_model_controlled_reasoning_fields_are_not_execution_evidence(self):
        parsed = live._model_response({"text": '{"answer":"supported", "citations":[], '
                                      '"reasoning_tokens":987654, "trace":["claimed"]}'})
        self.assertEqual(parsed["answer"], "supported")
        self.assertNotIn("reasoning_tokens", parsed)
        self.assertNotIn("trace", parsed)

    def test_private_artifact_root_is_user_owned_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = live._private_artifact_root(Path(temporary) / "artifacts")
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_response_failure_is_not_a_qualified_release(self):
        report = {"status": "partial", "inference_performed": False,
                  "qualification": "model_evidence_only",
                  "gaps": ["public_as1_job_journey_not_run"]}
        self.assertNotEqual(report["status"], "qualified")
        self.assertNotEqual(report["qualification"], "production_ready")

    def test_unavailable_or_different_slot_receipts_do_not_claim_interchangeability(self):
        identity = live._slot_identity({"slots": []}, [
            {"slot_id": "missing-one"}, {"slot_id": "missing-two"}])
        self.assertIs(identity["two_distinct_slots"], False)
        self.assertEqual(identity["slots"], [])
        self.assertIs(identity["same_model_sha256"], False)

    def test_fixture_source_locator_uses_registered_repository_identity_and_path(self):
        from project_control.config import load_config
        from project_control.registry import WorkspaceRegistry

        registry = WorkspaceRegistry(load_config())
        evidence = live._source_packet({"evidence_paths": ["demo/budgets.py"]}, FIXTURE)
        locators = live._fixture_source_locators(registry, evidence, FIXTURE)
        self.assertEqual(len(locators), 1)
        self.assertEqual(locators[0]["project"], "project-control")
        self.assertEqual(locators[0]["repository"], "project-control")
        self.assertEqual(locators[0]["path"],
                         "planning/project-assistance-v1/fixtures/repository/demo/budgets.py")
        self.assertEqual(locators[0]["content_sha256"], evidence[0]["sha256"])
        self.assertNotIn("revision", locators[0])

    def test_preflight_failure_is_persistable_zero_inquiry_evidence(self):
        report = live._preflight_failure_report(
            {"cases": [], "inference_performed": False}, RuntimeError("canonical service unavailable"))
        self.assertEqual(report["status"], "preflight_failed")
        self.assertEqual(report["inquiry_count"], 0)
        self.assertFalse(report["inference_performed"])
        self.assertFalse(report["cleanup"]["isolated_job_service_started"])
        self.assertEqual(report["failure"]["reason"], "canonical service unavailable")

    def test_attempted_inquiry_is_not_reported_as_confirmed_inference(self):
        self.assertFalse(live._visible_inference_confirmed(
            "unavailable", "not a public answer", []))
        self.assertFalse(live._visible_inference_confirmed("completed", None, []))
        self.assertTrue(live._visible_inference_confirmed("completed", "visible answer", []))
        self.assertTrue(live._visible_inference_confirmed("partial", None, [{"text": "visible finding"}]))

    def test_failure_stop_requires_two_failures_of_the_same_class(self):
        self.assertFalse(live._same_failure_stop({"runtime_mismatch": 1, "worker_failure": 1}))
        self.assertTrue(live._same_failure_stop({"runtime_mismatch": 2}))

    def test_negative_public_result_links_to_exact_isolated_job_id_without_content(self):
        scope = {"principal": "observer", "profile": "observer", "project": "catalog"}
        case = {"question": "bounded test question"}
        runtime_identity = {"fingerprint": "source-runtime"}
        expected_identity = live.canonical_digest({
            "question": case["question"],
            "context": {"project": "catalog", "analysis_runtime_identity": runtime_identity},
            "mode": "investigate", "skill": None,
        })

        class Cursor:
            def fetchone(self):
                return {"id": "job_actual_17"}

        class DB:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _query, parameters):
                self.parameters = parameters
                return Cursor()

        class Jobs:
            analysis_runtime_identity = runtime_identity
            inquiry_calls = 0

            @staticmethod
            def inquiry_context(access_scope, identity):
                return {"project": access_scope["project"], "analysis_runtime_identity": identity}

            def _db(self):
                db = DB()
                self.db = db
                return db

            def lookup(self, job_id, *, access_scope):
                self.lookup_args = (job_id, access_scope)
                return {"status": "ok", "job": {"job_id": job_id, "status": "failed", "attempt": 1}}

            def inquire(self, *_args, **_kwargs):
                self.inquiry_calls += 1
                return {"status": "unavailable", "reason": "analysis_unavailable"}

            @staticmethod
            def inquiry_failure_diagnostics(*, limit):
                assert limit == 50
                return [{"job_id": "job_actual_17", "failure_class": "runtime_mismatch"}]

        class Composition:
            jobs = Jobs()

        composition = Composition()
        linkage = live._lookup_inquiry_job_linkage(composition, case=case, scope=scope)
        self.assertEqual(linkage["job_id"], "job_actual_17")
        self.assertEqual(linkage["job_status"], "failed")
        self.assertEqual(linkage["failure_class"], "runtime_mismatch")
        self.assertEqual(composition.jobs.db.parameters, (expected_identity,))
        self.assertEqual(composition.jobs.lookup_args[0], "job_actual_17")

        composition.jobs.lookup = lambda job_id, *, access_scope: {
            "status": "ok", "job": {"job_id": job_id, "status": "completed", "attempt": 2,
                                       "answer": "private answer", "findings": [{"text": "private finding"}]}}
        observed = live._lookup_inquiry_job_linkage(composition, case=case, scope=scope)
        self.assertTrue(observed["actual_inference_confirmed"])
        self.assertTrue(observed["private_job_answer_observed"])
        self.assertEqual(observed["private_job_findings_count"], 1)
        self.assertEqual(observed["actual_inference_evidence"],
                         "isolated_scoped_job_answer_or_findings")
        self.assertNotIn("answer", observed)
        self.assertNotIn("findings", observed)

        attempts = []
        outcome = live._inquire_to_terminal(
            composition, case=case, scope=scope, hint_aliases=["fixture-source"],
            deadline=live.time.monotonic() + 2, on_attempt=lambda: attempts.append("attempted"))
        self.assertEqual(attempts, ["attempted"])
        self.assertEqual(composition.jobs.inquiry_calls, 1)
        self.assertEqual(outcome["public_result"], {
            "status": "unavailable", "reason": "analysis_unavailable"})
        self.assertEqual(outcome["job_linkage"]["job_id"], "job_actual_17")

    def test_real_runtime_composes_public_as1_surface_with_private_state_and_stub_backend(self):
        from project_control.app import Runtime
        from project_control.config import load_config
        from project_control.runtime_binding import bind_local_runtime

        class StubBackend:
            available = True

            def __init__(self, state_root):
                self._state_root = state_root

        with tempfile.TemporaryDirectory(prefix="pa1-compose-smoke-") as temporary:
            private_root = Path(temporary) / "as1-state"
            identity = bind_local_runtime()
            runtime = Runtime(load_config())
            backend = StubBackend(Path(temporary) / "canonical-supervisor-state")
            composition = live._compose_isolated_surface(private_root, runtime, backend)
            try:
                self.assertIs(composition.backend, backend)
                self.assertIsNotNone(composition.jobs.worker_factory)
                self.assertEqual(composition.jobs.directory, private_root / "jobs-v2")
                self.assertEqual(composition.store.path.parent, private_root)
                report = live._runtime_report(identity, backend, {
                    "runtime_fingerprint": identity.fingerprint,
                    "source_sha256": identity.manifest_sha256,
                    "supervisor_pid": 123,
                    "service_state_root": str(backend._state_root),
                })
                self.assertEqual(report["root"], str(identity.root))
                self.assertEqual(report["fingerprint"], identity.fingerprint)
                self.assertEqual(report["supervisor_runtime_fingerprint"], identity.fingerprint)
            finally:
                self.assertTrue(composition.close())

    def test_heldout_fixture_uses_ephemeral_workspace_and_fresh_source_locator(self):
        from project_control.app import Runtime
        from project_control.config import load_config

        class StubBackend:
            available = True

            def __init__(self, state_root):
                self._state_root = state_root

        canonical_config = load_config()
        canonical_before = canonical_config.model_dump()
        with tempfile.TemporaryDirectory(prefix="pa1-heldout-compose-") as temporary:
            area = Path(temporary)
            fixture = area / "heldout-fixture"
            source = fixture / "demo" / "budgets.py"
            source.parent.mkdir(parents=True)
            source.write_text("INQUIRY_SECONDS = 240\n", encoding="utf-8")
            private_config = live._ephemeral_fixture_config(canonical_config, fixture)
            self.assertEqual(set(private_config.workspaces), set(canonical_config.workspaces))
            workspace = private_config.workspaces["project-control"]
            self.assertEqual(workspace.authority_repository,
                             canonical_config.workspaces["project-control"].authority_repository)
            self.assertEqual(workspace.repositories["pa1-heldout-fixture"].root, fixture.resolve())
            self.assertEqual(workspace.repositories[workspace.authority_repository].root,
                             canonical_config.workspaces["project-control"].repositories[
                                 workspace.authority_repository].root)

            backend = StubBackend(area / "canonical-supervisor-state")
            composition = live._compose_isolated_surface(
                area / "isolated-as1-state", Runtime(private_config), backend)
            try:
                case_scope, packet_ids, evidence = live._insert_fixture_packet(
                    composition, {"id": "H01", "evidence_paths": ["demo/budgets.py"]}, fixture)
                packet_id = packet_ids[0]
                self.assertEqual(case_scope["project"], "project-control")
                located = composition.store.lookup(packet_id, access_scope=case_scope)
                self.assertEqual(located.status, "ok")
                locator = located.packet.sources[0]
                self.assertEqual((locator.project, locator.repository, locator.path),
                                 ("project-control", "pa1-heldout-fixture", "demo/budgets.py"))
                self.assertEqual(locator.content_sha256, evidence[0]["sha256"])
                freshness = live._assert_source_packet_freshness(composition, case_scope, packet_ids)
                self.assertTrue(freshness["fresh"])
                self.assertEqual(composition.jobs.directory, area / "isolated-as1-state" / "jobs-v2")
            finally:
                self.assertTrue(composition.close())
        self.assertEqual(load_config().model_dump(), canonical_before)

    def test_supported_read_packet_passes_real_freshness_and_wrong_hash_fails(self):
        from project_control.app import Runtime
        from project_control.config import load_config

        class StubBackend:
            available = True

            def __init__(self, state_root):
                self._state_root = state_root

        with tempfile.TemporaryDirectory(prefix="pa1-read-freshness-") as temporary:
            area = Path(temporary)
            runtime = Runtime(load_config())
            backend = StubBackend(area / "canonical-supervisor-state")
            composition = live._compose_isolated_surface(area / "as1-state", runtime, backend)
            try:
                case_scope, packet_ids, _ = live._insert_fixture_packet(
                    composition, {"id": "F01", "evidence_paths": ["demo/budgets.py"]}, FIXTURE)
                self.assertEqual(case_scope["project"], "project-control")
                self.assertTrue(live._assert_source_packet_freshness(
                    composition, case_scope, packet_ids)["fresh"])

                source_packet = composition.store.lookup(packet_ids[0], access_scope=case_scope).packet
                bad_locator = source_packet.sources[0].model_copy(
                    update={"content_sha256": "0" * 64})
                bad_packet = composition.store.create(
                    tool="read", access_scope=case_scope, payload=source_packet.payload,
                    sources=[bad_locator], freshness=source_packet.freshness, ttl_seconds=None)
                with self.assertRaisesRegex(live.QualificationError, "freshness verification"):
                    live._assert_source_packet_freshness(composition, case_scope,
                                                         [bad_packet.packet_id])
            finally:
                self.assertTrue(composition.close())


if __name__ == "__main__":
    unittest.main()
