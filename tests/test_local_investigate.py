from __future__ import annotations

import unittest
import time
from unittest.mock import patch
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import LocalInvestigateInput, ProjectSnapshot, RepositoryIdentity, ToolStatus, envelope
from project_control.services.local_investigate import LIMITS, PROTOCOL, SYSTEM_PROMPT, Limits, _initial_route, local_investigate


def snapshot(commit: str = "a" * 40) -> ProjectSnapshot:
    return ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z",
        repositories={"source": RepositoryIdentity(commit=commit, dirty=False, working_tree_fingerprint="clean")})

def config() -> ProjectControlConfig:
    return ProjectControlConfig(workspaces={"demo": WorkspaceConfig(repositories={"source": RepositoryConfig(root=Path.cwd())})})


class LocalInvestigateTests(unittest.TestCase):
    def test_deterministic_initial_routing(self) -> None:
        self.assertEqual(_initial_route("Read src/project_control/app.py"), ("read_source", ["src/project_control/app.py"]))
        self.assertEqual(_initial_route("Where is observer_analysis implemented?"), ("search_source", ["observer_analysis"]))
        self.assertEqual(_initial_route("How is PC-LOCAL-INVESTIGATOR/1 implemented?"), ("search_source", ["PC-LOCAL-INVESTIGATOR"]))
        self.assertEqual(_initial_route("What is task PC-WF2-A03 status?"), ("inspect_task", ["PC-WF2-A03"]))
        self.assertEqual(_initial_route("Which workflow lanes are ready?"), ("inspect_workflow", []))
        self.assertEqual(_initial_route("What GPU topology is visible?"), ("inspect_machine", ["gpu_topology"]))
        self.assertEqual(_initial_route("Show GPU process usage"), ("inspect_machine", ["gpu_processes"]))
        self.assertEqual(_initial_route("What host memory is free?"), ("inspect_machine", ["host_memory"]))
        self.assertEqual(_initial_route("How much disk capacity is available?"), ("inspect_machine", ["filesystem_capacity"]))
        self.assertEqual(_initial_route("Is Project Control service healthy?"), ("inspect_machine", ["services"]))
        self.assertEqual(_initial_route("What kernel system diagnostics are available?"), ("inspect_machine", ["system"]))
        self.assertEqual(_initial_route("Which workflow task status is ready?"), ("inspect_workflow", []))
        self.assertEqual(_initial_route("Explain the broad architecture"), ("orient", []))
        self.assertEqual(_initial_route("Give the current host MemTotal and free memory"), ("inspect_machine", ["host_memory"]))
        self.assertEqual(_initial_route("Show host memory for MemTotal"), ("inspect_machine", ["host_memory"]))
        self.assertEqual(
            _initial_route("Report the host MemTotal value from machine evidence only."),
            ("inspect_machine", ["host_memory"]),
        )
        self.assertEqual(
            _initial_route("Read host memory information and report MemTotal exactly as observed, without unit conversion or inference."),
            ("inspect_machine", ["host_memory"]),
        )
        self.assertEqual(_initial_route("Summarize inference throughput."), ("orient", []))
        self.assertEqual(_initial_route("Give a summary"), ("orient", []))

    def test_narrow_source_seed_preserves_hits_and_populates_metrics(self) -> None:
        initial = snapshot()
        captured = []
        source = envelope("source_context", initial, {
            "repository": "source", "source_commit": "a" * 40, "source_freshness": "immutable_commit",
            "targets": [{"kind": "text", "target": "observer_analysis", "matches": [
                {"path": "src/project_control/observer_analysis.py", "line": 63,
                 "excerpt": "class SkillsObserverAnalysisProvider:"},
            ]}],
            "observation_preconditions": {"large": "x" * 20000},
        })
        answer = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "Provider found.", "facts": [{"text": "The provider is defined in observer_analysis.py.", "evidence_ids": ["E1"]}],
            "inferences": [], "uncertainty": [], "citations": ["E1"]}}, "warm_model_reused": True}
        def source_read(*args, **kwargs):
            captured.append((args, kwargs))
            return source
        with patch("project_control.services.local_investigate.architecture_context") as orient, \
             patch("project_control.services.local_investigate.source_context", side_effect=source_read):
            result = local_investigate(config(), LocalInvestigateInput(
                project="demo", question="Where is observer_analysis implemented?"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: answer)
        orient.assert_not_called()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0][0][2].source_selector, "a" * 40)
        observed = result.data["evidence_index"][0]["observed"]
        self.assertEqual(observed["targets"][0]["matches"][0]["path"], "src/project_control/observer_analysis.py")
        self.assertIn("SkillsObserverAnalysisProvider", observed["targets"][0]["matches"][0]["excerpt"])
        self.assertNotIn("observation_preconditions", observed)
        metrics = result.data["metrics"]
        self.assertEqual((metrics["rounds"], metrics["reads_performed"]), (1, 1))
        self.assertGreater(metrics["evidence_bytes_read"], 0)
        self.assertGreater(metrics["model_context_bytes"], 0)
        self.assertGreaterEqual(metrics["elapsed_ms"], metrics["model_ms"] + metrics["read_ms"])
        self.assertTrue(metrics["warm_model_reused"])
        self.assertFalse(result.data["authoritative"])
        self.assertFalse(result.data["mutation_authority"])

    def test_quick_narrow_seed_uses_compact_small_source_budget(self) -> None:
        initial = snapshot()
        captured = []
        answer = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "Found.", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E1"]}}}
        def source_read(*args, **kwargs):
            captured.append(args[2])
            return envelope("source_context", initial, {"targets": []})
        with patch("project_control.services.local_investigate.source_context", side_effect=source_read):
            local_investigate(config(), LocalInvestigateInput(
                project="demo", question="Read src/project_control/app.py", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: answer)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].detail, "compact")
        self.assertEqual(captured[0].budget_bytes, 4 * 1024)

    def test_quick_sufficient_seed_forces_answer_without_a_second_read(self) -> None:
        initial = snapshot()
        inputs = []
        answer = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "Found.", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E1"]}}}
        with patch("project_control.services.local_investigate.source_context", return_value=envelope(
            "source_context", initial, {"targets": [{"matches": [{"path": "x.py", "line": 1}]}]})):
            result = local_investigate(config(), LocalInvestigateInput(
                project="demo", question="Where is needle implemented?", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial,
                model_turn=lambda value: (inputs.append(value) or answer))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(len(inputs), 1)
        self.assertTrue(inputs[0]["must_answer"])

    def test_empty_snapshot_returns_structured_read_only_fallback(self) -> None:
        empty = ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z", repositories={})
        result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
            snapshot=empty, snapshot_getter=lambda: empty, model_turn=lambda _: self.fail("model must not run"))
        self.assertEqual(result.data["status"], "partial")
        self.assertFalse(result.data["authoritative"])
        self.assertIn("project_has_no_repository", result.warnings)

    def test_successful_answer_is_evidence_linked_and_compact(self) -> None:
        initial = snapshot()
        turn = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "The broker is read-only.",
            "facts": [{"text": "Project Control supplied orientation evidence.", "evidence_ids": ["E1"]}],
            "inferences": [{"text": "No further read is needed.", "evidence_ids": ["E1"]}],
            "uncertainty": [], "citations": ["E1"],
        }}}
        orientation = envelope("architecture_context", initial, {
            "repository": "source", "source_commit": "a" * 40,
            "targets": [{"path": "src/project_control/app.py", "line_start": 1, "line_end": 20}],
        })
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: turn)
        self.assertEqual(result.data["status"], "ok")
        self.assertFalse(result.data["authoritative"])
        self.assertFalse(result.data["mutation_authority"])
        self.assertEqual(result.data["answer"]["facts"][0]["evidence_ids"], ["E1"])
        self.assertEqual(result.data["evidence_index"][0]["locations"][0]["path"], "src/project_control/app.py")
        self.assertIn("evidence_digest", result.data["evidence_index"][0])
        self.assertEqual(result.data["evidence_index"][0]["observed"]["repository"], "source")
        self.assertEqual(result.cursor.identity_digest, initial.identity_digest())
        self.assertEqual(result.cursor.worktrees, {})

    def test_uncited_or_oversized_final_is_not_accepted(self) -> None:
        initial = snapshot()
        uncited = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "unsupported", "facts": [], "inferences": [], "uncertainty": [], "citations": []}}}
        oversized = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "x" * 7999, "facts": [{"text": "y" * 3999, "evidence_ids": ["E1"]} for _ in range(4)],
            "inferences": [], "uncertainty": [], "citations": ["E1"]}}}
        orientation = envelope("architecture_context", initial, {"repository": "source"})
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation):
            first = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: uncited)
            second = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: oversized)
        self.assertEqual(first.data["status"], "partial")
        self.assertIn("local_model_final_invalid", first.warnings)
        self.assertEqual(second.data["status"], "partial")
        self.assertIn("local_model_final_invalid", second.warnings)

    def test_accepts_exact_single_json_fence_and_stops_between_slow_reads(self) -> None:
        initial = snapshot()
        fenced = {"text": "```json\n{\"action\":\"answer\",\"requests\":[],\"answer\":{\"summary\":\"ok\",\"facts\":[],\"inferences\":[],\"uncertainty\":[],\"citations\":[\"E1\"]}}\n```"}
        orientation = envelope("architecture_context", initial, {"repository": "source"})
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation):
            accepted = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: fenced)
        self.assertEqual(accepted.data["status"], "ok")

        calls = []
        def slow(*_args, **_kwargs):
            calls.append(1)
            time.sleep(0.02)
            return envelope("coordination_view", initial, {})
        read_turn = {"turn": {"action": "inspect_workflow", "requests": [{}, {}, {}, {}]}}
        with patch.dict(LIMITS, {"quick": Limits(3, 8, 64 * 1024, 0.01, 8)}), \
             patch("project_control.services.local_investigate.architecture_context", return_value=orientation), \
             patch("project_control.services.local_investigate.coordination_view", side_effect=slow):
            bounded = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: read_turn)
        self.assertEqual(len(calls), 1)
        self.assertIn("investigation_time_budget_exhausted", bounded.warnings)

    def test_batched_reads_pin_commit_and_filter_citations(self) -> None:
        initial = snapshot()
        turns = iter([
            {"turn": {"action": "search_source", "requests": [
                {"targets": [{"value": "needle"}]}, {"targets": [{"value": "other"}]},
            ]}},
            {"turn": {"action": "answer", "answer": {"summary": "done", "facts": [], "inferences": [],
                "uncertainty": [], "citations": ["E1", "E2", "not-issued"]}}},
        ])
        calls = []
        def read(*args, **kwargs):
            calls.append(args[2])
            return envelope("source_context", initial, {"safe": True})
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {"safe": True})), \
             patch("project_control.services.local_investigate.source_context", side_effect=read):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: next(turns))
        self.assertEqual(PROTOCOL, "PC-LOCAL-INVESTIGATOR-TURN/1")
        self.assertIn("no tools", SYSTEM_PROMPT)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item.source_selector == "a" * 40 for item in calls))
        self.assertEqual(result.data["status"], "partial")
        self.assertIn("local_model_final_has_unissued_citation", result.warnings)

    def test_drift_and_bad_model_are_clean_partials(self) -> None:
        initial = snapshot()
        changed = snapshot("b" * 40)
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {})):
            drift = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: changed, model_turn=lambda _: {"turn": {"action": "answer", "answer": {}}})
            malformed = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: {"turn": "not json"})
        self.assertEqual(drift.data["status"], "refresh_required")
        self.assertEqual(malformed.data["status"], "partial")
        self.assertEqual(malformed.status, ToolStatus.PARTIAL)

    def test_rejects_extra_read_parameters_and_keeps_final_compact(self) -> None:
        initial = snapshot()
        model_inputs = []
        turns = iter([
            {"turn": {"action": "inspect_workflow", "requests": [{"hidden": "no"}]}},
        ])
        def model_turn(value):
            model_inputs.append(value)
            return next(turns)
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {"large": "x" * 100000})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=model_turn)
        self.assertIn("investigator_read_request_rejected", result.warnings)
        self.assertNotIn("evidence", result.data)
        self.assertLess(len(str(model_inputs[0]["evidence"]).encode()), 22 * 1024)
        self.assertIn("large", model_inputs[0]["evidence"][0]["result"]["data"])
        self.assertLess(len(str(result.model_dump(mode="json")).encode()), 48 * 1024 + 4096)

    def test_duplicate_read_forces_final_only_turn(self) -> None:
        initial = snapshot()
        read = {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "needle"}]}]}}
        answer = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "done", "facts": [{"text": "observed", "evidence_ids": ["E2"]}],
            "inferences": [], "uncertainty": [], "citations": ["E2"]}}}
        turns = iter([read, read, answer])
        inputs = []
        def model(value):
            inputs.append(value)
            return next(turns)
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {})), \
             patch("project_control.services.local_investigate.source_context", return_value=envelope("source_context", initial, {"targets": [{"matches": [{"path": "x.py", "line": 1}]}]})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=model)
        self.assertEqual(result.data["status"], "ok")
        self.assertNotIn("duplicate_investigator_read_rejected", result.warnings)
        self.assertTrue(inputs[2]["must_answer"])
        self.assertIn("FINAL", inputs[2]["system_prompt"])

    def test_initial_equivalent_read_is_not_reissued(self) -> None:
        initial = snapshot()
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "needle"}]}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "done", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E1"]}}},
        ])
        calls = []
        def source_read(*args, **kwargs):
            calls.append(args[2])
            return envelope("source_context", initial, {"targets": []})
        with patch("project_control.services.local_investigate.source_context", side_effect=source_read):
            result = local_investigate(config(), LocalInvestigateInput(
                project="demo", question="Where is needle implemented?"), snapshot=initial,
                snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.data["status"], "ok")

    def test_later_model_turn_sends_only_new_evidence(self) -> None:
        initial = snapshot()
        inputs = []
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "needle"}]}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "done", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E2"]}}},
        ])
        def model(value):
            inputs.append(value)
            return next(turns)
        orientation = envelope("architecture_context", initial, {"large": "x" * 9000})
        source = envelope("source_context", initial, {"targets": [{"matches": [{"path": "x.py", "line": 1}]}]})
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation), \
             patch("project_control.services.local_investigate.source_context", return_value=source):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=model)
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual([item["id"] for item in inputs[0]["evidence"]], ["E1"])
        self.assertEqual([item["id"] for item in inputs[1]["evidence"]], ["E2"])
        self.assertEqual(inputs[1]["issued_evidence"][0]["id"], "E1")
        self.assertNotIn("result", inputs[1]["issued_evidence"][0])
        self.assertLess(len(str(inputs[1]).encode()), len(str(inputs[0]).encode()))

    def test_machine_action_is_enum_only_and_evidence_linked(self) -> None:
        initial = snapshot()
        turns = iter([
            {"turn": {"action": "inspect_machine", "requests": [{"diagnostic": "gpu_summary"}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "done", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E2"]}}},
        ])
        machine = envelope("machine_inspection", initial, {"diagnostic": "gpu_summary", "devices": []})
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {})), \
             patch("project_control.services.local_investigate.machine_inspection", return_value=machine) as inspect_machine:
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(inspect_machine.call_args.kwargs["diagnostic"], "gpu_summary")

        rejected = {"turn": {"action": "inspect_machine", "requests": [{"diagnostic": "gpu_summary", "argv": ["sh"]}]}}
        with patch("project_control.services.local_investigate.architecture_context", return_value=envelope("architecture_context", initial, {})), \
             patch("project_control.services.local_investigate.machine_inspection") as forbidden:
            partial = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: rejected)
        forbidden.assert_not_called()
        self.assertIn("investigator_read_request_rejected", partial.warnings)
