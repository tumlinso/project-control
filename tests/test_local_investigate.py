from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import LocalInvestigateInput, ProjectSnapshot, RepositoryIdentity, envelope
from project_control.services.local_investigate import (CAPABILITIES, FINAL_SYSTEM_PROMPT, LIMITS,
    PROTOCOL, SYSTEM_PROMPT, TRANSCRIPT_BUDGET, Limits, _compact_transcript, local_investigate)


def snapshot(commit: str = "a" * 40) -> ProjectSnapshot:
    return ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z",
        repositories={"source": RepositoryIdentity(commit=commit, dirty=False, working_tree_fingerprint="clean")})


def config() -> ProjectControlConfig:
    return ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"source": RepositoryConfig(root=Path.cwd())})})


def answer(evidence_id: str) -> dict:
    return {"turn": {"action": "answer", "requests": [], "answer": {
        "summary": "done", "facts": [{"text": "observed", "evidence_ids": [evidence_id]}],
        "inferences": [], "uncertainty": [], "citations": [evidence_id]}}}


class LocalInvestigateTests(unittest.TestCase):
    def test_v2_heterogeneous_calls_share_one_model_turn_and_conversation(self) -> None:
        initial, inputs = snapshot(), []
        turns = iter([
            {"turn": {"action": "continue", "calls": [
                {"tool": "search_source", "arguments": {"targets": [{"value": "machine_inspection"}]}},
                {"tool": "inspect_workflow", "arguments": {}},
            ]}},
            {"turn": {"action": "answer", "calls": [], "answer": {
                "summary": "done", "facts": [{"text": "observed", "evidence_ids": ["E1", "E2"]}],
                "inferences": [], "uncertainty": [], "citations": ["E1", "E2"]}}},
        ])
        with patch("project_control.services.local_investigate.source_context", return_value=envelope("source_context", initial, {"targets": []})), \
             patch("project_control.services.local_investigate.coordination_view", return_value=envelope("coordination_view", initial, {"active_run_id": "r"})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", compute_profile="wide", parallelism="row"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: (inputs.append(value) or next(turns)))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(result.data["metrics"]["reads_performed"], 2)
        self.assertEqual((inputs[0]["compute_profile"], inputs[0]["parallelism"]), ("wide", "row"))
        self.assertEqual([item["role"] for item in inputs[1]["messages"][-2:]], ["assistant", "user"])
        self.assertIn('"id":"E1"', inputs[1]["messages"][-1]["content"])
        self.assertIn('"id":"E2"', inputs[1]["messages"][-1]["content"])

    def test_explicit_parallelism_requires_wide_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires compute_profile='wide'"):
            LocalInvestigateInput(project="demo", question="q", compute_profile="narrow", parallelism="tensor")

    def test_transcript_compaction_preserves_origin_recent_pair_and_state(self) -> None:
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}]
        for index in range(8):
            messages.extend([{"role": "assistant", "content": f"call-{index}"},
                             {"role": "user", "content": f"TOOL_RESULTS-{index}-" + "x" * 12000}])
        compacted, changed = _compact_transcript(messages, {"evidence_ids": ["E8"]}, [
            {"id": f"E{index}", "kind": "inspect", "result": {"tool": "inspect", "status": "ok"}}
            for index in range(1, 9)])
        self.assertTrue(changed)
        self.assertLessEqual(len(__import__("json").dumps(compacted).encode()), TRANSCRIPT_BUDGET)
        self.assertEqual(compacted[:2], messages[:2])
        self.assertIn("COMPACTED_HISTORY", compacted[2]["content"])
        self.assertEqual(compacted[-2:], messages[-2:])

    def test_trace_is_opt_in_and_publicly_redacts_private_exec_evidence(self) -> None:
        initial, inputs = snapshot(), []
        private = "/home/tumlinson/project-control/src/project_control/services/machine_inspection.py"
        turns = iter([
            {"parallelism": "row", "turn": {"action": "continue", "calls": [{"tool": "exec_readonly", "arguments": {
                "argv": ["rg", "machine_inspection", private], "cwd": "/home/tumlinson/project-control"}}]}},
            {"parallelism": "row", "turn": {"action": "answer", "calls": [], "answer": {"summary": "found", "facts": [
                {"text": "found", "evidence_ids": ["E1"]}], "inferences": [], "uncertainty": [], "citations": ["E1"]}}},
        ])
        with patch("project_control.services.local_investigate.exec_readonly", return_value={
            "status": "ok", "argv": ["rg", private], "cwd": "/home/tumlinson/project-control",
            "returncode": 0, "stdout": private, "stderr": "", "elapsed_ms": 1}):
            traced = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", detail="trace", parallelism="row"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: (inputs.append(value) or next(turns)))
        self.assertIn(private, inputs[1]["messages"][-1]["content"])
        self.assertNotIn(private, str(traced.data["trace"]))
        self.assertNotIn(private, str(traced.data["evidence_index"]))
        self.assertEqual(traced.data["trace"]["rounds"][0]["calls"][0]["status"], "accepted")
        self.assertEqual(traced.data["trace"]["parallelism"], "row")
        plain_turns = iter([{"turn": {"action": "continue", "calls": [{"tool": "inspect_workflow", "arguments": {}}]}}, answer("E1")])
        with patch("project_control.services.local_investigate.coordination_view", return_value=envelope("coordination_view", initial, {})):
            plain = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"), snapshot=initial,
                snapshot_getter=lambda: initial, model_turn=lambda _: next(plain_turns))
        self.assertNotIn("trace", plain.data)

    def test_empty_snapshot_returns_structured_read_only_fallback(self) -> None:
        empty = ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z", repositories={})
        result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
            snapshot=empty, snapshot_getter=lambda: empty, model_turn=lambda _: self.fail("model must not run"))
        self.assertEqual(result.data["status"], "partial")
        self.assertFalse(result.data["authoritative"])
        self.assertIn("project_has_no_repository", result.warnings)

    def test_first_turn_is_model_directed_and_has_no_preselected_evidence(self) -> None:
        initial, inputs = snapshot(), []
        first = {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "machine_inspection"}]}]}}
        def model_turn(value: dict) -> dict:
            inputs.append(value)
            return first if len(inputs) == 1 else answer("E1")
        with patch("project_control.services.local_investigate.source_context", return_value=envelope("source_context", initial, {"targets": []})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="Where is machine inspection owned?"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=model_turn)
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual([item["role"] for item in inputs[0]["messages"]], ["system", "user"])
        self.assertNotIn("TOOL_RESULTS", inputs[0]["messages"][1]["content"])
        self.assertIn('"capabilities"', inputs[0]["messages"][1]["content"])
        self.assertEqual(PROTOCOL, "PC-LOCAL-INVESTIGATOR-TURN/2")
        self.assertIn("capable read-only coding", SYSTEM_PROMPT)
        self.assertIn("candidates, not proof", SYSTEM_PROMPT)
        self.assertIn("normally prefer\nsearch_source over orient", SYSTEM_PROMPT)
        self.assertIn("Tests, documentation, benchmarks, and callers", SYSTEM_PROMPT)
        self.assertIn("exact implementation path or symbol", FINAL_SYSTEM_PROMPT)

    def test_search_verify_answer_preserves_bounded_evidence_backed_working_state(self) -> None:
        initial, inputs = snapshot(), []
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "machine_inspection"}]}]}},
            {"turn": {"action": "read_source", "requests": [{"targets": [{"kind": "path", "value": "src/project_control/services/machine_inspection.py", "line_start": 1, "line_end": 60}]}],
                "working_state": {"findings": [{"text": "Search found the service candidate.", "evidence_ids": ["E1"]}],
                    "unresolved_questions": ["Verify ownership."], "evidence_ids": ["E1"]}}},
            answer("E2"),
        ])
        source = envelope("source_context", initial, {"targets": [{"matches": [{
            "path": "src/project_control/services/machine_inspection.py", "line": 1, "excerpt": "def machine_inspection"}]}]})
        with patch("project_control.services.local_investigate.source_context", return_value=source):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="Who owns host inspection?", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: (inputs.append(value) or next(turns)))
        self.assertEqual(result.data["status"], "ok")
        self.assertIn('"id":"E1"', inputs[1]["messages"][-1]["content"])
        self.assertIn('"id":"E2"', inputs[2]["messages"][-2]["content"])
        self.assertIn('"evidence_ids":["E1"]', inputs[2]["messages"][-2]["content"])
        self.assertEqual(inputs[1]["messages"][-2]["role"], "assistant")
        self.assertEqual((result.data["metrics"]["rounds"], result.data["metrics"]["reads_performed"]), (3, 2))

    def test_machine_inspection_ownership_verifies_service_not_shared_host_adapter(self) -> None:
        initial, calls = snapshot(), []
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "machine inspection ownership"}]}]}},
            {"turn": {"action": "read_source", "requests": [{"targets": [{"kind": "path", "value": "src/project_control/services/machine_inspection.py", "line_start": 1, "line_end": 80}]}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "done", "facts": [{"text": "The machine_inspection service owns host machine inspection.", "evidence_ids": ["E2"]}],
                "inferences": [], "uncertainty": [], "citations": ["E2"]}}}])
        search = envelope("source_context", initial, {"targets": [{"matches": [
            {"path": "src/project_control/adapters/host.py", "line": 1, "excerpt": "class HostReadAdapter"},
            {"path": "src/project_control/services/machine_inspection.py", "line": 1, "excerpt": "def machine_inspection"}]}]})
        read = envelope("source_context", initial, {"targets": [{"path": "src/project_control/services/machine_inspection.py", "excerpt": "def machine_inspection(...)"}]})
        def source_context_call(*args, **kwargs):
            calls.append(args[2]); return search if len(calls) == 1 else read
        with patch("project_control.services.local_investigate.source_context", side_effect=source_context_call):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="Give me the implementation that owns Project Control host machine inspection."),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(calls[0].targets[0].kind, "text")
        self.assertEqual(calls[1].targets[0].value, "src/project_control/services/machine_inspection.py")

    def test_search_only_ownership_fact_is_rejected_until_verified(self) -> None:
        initial = snapshot()
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "machine inspection"}]}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "done", "facts": [{"text": "The shared host adapter owns machine inspection implementation.", "evidence_ids": ["E1"]}],
                "inferences": [], "uncertainty": [], "citations": ["E1"]}}},
        ])
        with patch("project_control.services.local_investigate.source_context", return_value=envelope("source_context", initial, {"targets": [{"matches": [
            {"path": "src/project_control/adapters/host.py", "line": 1, "excerpt": "class HostReadAdapter"}]}]})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="Who owns host inspection?"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "partial")
        self.assertIn("local_model_final_ownership_claim_requires_verification", result.warnings)

    def test_hard_deadline_does_not_prematurely_force_final_turn(self) -> None:
        initial, inputs = snapshot(), []
        turns = iter([
            {"turn": {"action": "inspect_machine", "requests": [{"diagnostic": "host_memory"}]}}, answer("E1"),
        ])
        with patch.dict(LIMITS, {"quick": Limits(3, 8, 64 * 1024, 20, 8)}), \
             patch("project_control.services.local_investigate.machine_inspection", return_value=envelope("machine_inspection", initial, {"diagnostic": "host_memory"})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="MemTotal", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: (inputs.append(value) or next(turns)))
        self.assertEqual(result.data["status"], "ok")
        self.assertNotIn(" FINAL", inputs[0]["messages"][-1]["content"])
        self.assertIn("ownership or implementation fact", SYSTEM_PROMPT)

    def test_cellerator_geometry_owner_verifies_current_implementation_not_benchmark(self) -> None:
        initial, calls = snapshot(), []
        turns = iter([
            {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "exact geometry evaluator"}]}]}},
            {"turn": {"action": "read_source", "requests": [{"targets": [{"kind": "path", "value": "src/geometry/compiler/v2/exact_evaluator.cc", "line_start": 1, "line_end": 80}]}]}}, answer("E2")])
        search = envelope("source_context", initial, {"targets": [{"matches": [
            {"path": "bench/geometry/cellpack_evaluator_bench.cc", "line": 1, "excerpt": "benchmark reference"},
            {"path": "src/geometry/compiler/v2/exact_evaluator.cc", "line": 1, "excerpt": "implementation"}]}]})
        read = envelope("source_context", initial, {"targets": [{"path": "src/geometry/compiler/v2/exact_evaluator.cc", "excerpt": "implementation"}]})
        def source_context_call(*args, **kwargs):
            calls.append(args[2]); return search if len(calls) == 1 else read
        with patch("project_control.services.local_investigate.source_context", side_effect=source_context_call):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="Which implementation owns the exact geometry evaluator?", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(calls[1].targets[0].value, "src/geometry/compiler/v2/exact_evaluator.cc")

    def test_machine_action_remains_enum_validated_and_evidence_linked(self) -> None:
        initial = snapshot()
        turns = iter([{"turn": {"action": "inspect_machine", "requests": [{"diagnostic": "host_memory"}]}}, answer("E1")])
        machine = envelope("machine_inspection", initial, {"diagnostic": "host_memory", "meminfo": {"MemTotal": "1 kB"}})
        with patch("project_control.services.local_investigate.machine_inspection", return_value=machine) as inspect_machine:
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="MemTotal"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "ok")
        self.assertEqual(inspect_machine.call_args.kwargs["diagnostic"], "host_memory")

    def test_successful_answer_is_evidence_linked_and_compact(self) -> None:
        initial = snapshot()
        turns = iter([
            {"turn": {"action": "orient", "requests": [{"question": "q"}]}},
            {"turn": {"action": "answer", "requests": [], "answer": {
                "summary": "The broker is read-only.",
                "facts": [{"text": "Orientation was supplied.", "evidence_ids": ["E1"]}],
                "inferences": [], "uncertainty": [], "citations": ["E1"]}}},
        ])
        orientation = envelope("architecture_context", initial, {"repository": "source", "source_commit": "a" * 40,
            "targets": [{"path": "src/project_control/app.py", "line_start": 1, "line_end": 20}]})
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(result.data["status"], "ok")
        self.assertFalse(result.data["authoritative"])
        self.assertFalse(result.data["mutation_authority"])
        self.assertEqual(result.data["evidence_index"][0]["locations"][0]["path"], "src/project_control/app.py")
        self.assertIn("evidence_digest", result.data["evidence_index"][0])
        self.assertEqual(result.cursor.identity_digest, initial.identity_digest())
        self.assertEqual(result.cursor.worktrees, {})

    def test_uncited_or_oversized_final_is_not_accepted(self) -> None:
        initial = snapshot()
        seed = {"turn": {"action": "orient", "requests": [{"question": "q"}]}}
        uncited = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "unsupported", "facts": [], "inferences": [], "uncertainty": [], "citations": []}}}
        oversized = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "x" * 7999, "facts": [{"text": "y" * 3999, "evidence_ids": ["E1"]} for _ in range(4)],
            "inferences": [], "uncertainty": [], "citations": ["E1"]}}}
        orientation = envelope("architecture_context", initial, {"repository": "source"})
        with patch("project_control.services.local_investigate.architecture_context", return_value=orientation):
            first_turns, second_turns = iter([seed, uncited]), iter([seed, oversized])
            first = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"), snapshot=initial,
                snapshot_getter=lambda: initial, model_turn=lambda _: next(first_turns))
            second = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"), snapshot=initial,
                snapshot_getter=lambda: initial, model_turn=lambda _: next(second_turns))
        self.assertIn("local_model_final_invalid", first.warnings)
        self.assertIn("local_model_final_invalid", second.warnings)

    def test_fenced_json_batched_reads_and_citation_filtering_remain_validated(self) -> None:
        initial, calls = snapshot(), []
        fenced = {"text": "```json\n{\"action\":\"search_source\",\"requests\":[{\"targets\":[{\"value\":\"needle\"}]},{\"targets\":[{\"value\":\"other\"}]}]}\n```"}
        bad_final = {"turn": {"action": "answer", "requests": [], "answer": {
            "summary": "done", "facts": [], "inferences": [], "uncertainty": [], "citations": ["E1", "E2", "not-issued"]}}}
        def read(*args, **kwargs):
            calls.append(args[2]); return envelope("source_context", initial, {"safe": True})
        turns = iter([fenced, bad_final])
        with patch("project_control.services.local_investigate.source_context", side_effect=read):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: next(turns))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item.source_selector == "a" * 40 for item in calls))
        self.assertIn("local_model_final_has_unissued_citation", result.warnings)

    def test_bad_model_and_extra_read_parameters_are_clean_partials(self) -> None:
        initial = snapshot()
        malformed = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", detail="trace"), snapshot=initial,
            snapshot_getter=lambda: initial, model_turn=lambda _: {"turn": "not json", "status": "available",
                "response_metadata": {"finish_reason": "tool_calls", "message": {
                    "content_present": True, "content_characters": 8,
                    "content_prefix": "not json", "tool_calls": {"type": "array", "length": 1}}}})
        rejected = {"turn": {"action": "inspect_workflow", "requests": [{"hidden": "no"}]}}
        result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"), snapshot=initial,
            snapshot_getter=lambda: initial, model_turn=lambda _: rejected)
        self.assertEqual(malformed.data["status"], "partial")
        self.assertIn("local_model_turn_invalid_or_unavailable", malformed.warnings)
        failure = malformed.data["trace"]["rounds"][0]["parse_failure"]
        self.assertEqual(failure["exception_class"], "JSONDecodeError")
        self.assertEqual(failure["response_metadata"]["finish_reason"], "tool_calls")
        self.assertEqual(failure["response_metadata"]["message"]["content_prefix"], "not json")
        self.assertIn("investigator_read_request_rejected", result.warnings)
        self.assertNotIn("evidence", result.data)
        self.assertLess(len(str(result.model_dump(mode="json")).encode()), 48 * 1024 + 4096)

    def test_rejects_unissued_working_state(self) -> None:
        initial = snapshot()
        invalid = {"turn": {"action": "inspect_workflow", "requests": [{}], "working_state": {
            "findings": [{"text": "unsupported", "evidence_ids": ["E999"]}], "unresolved_questions": [], "evidence_ids": ["E999"]}}}
        with patch("project_control.services.local_investigate.coordination_view", return_value=envelope("coordination_view", initial, {})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: invalid)
        self.assertIn("investigator_working_state_rejected", result.warnings)

    def test_duplicate_read_forces_final_only_turn(self) -> None:
        initial, inputs = snapshot(), []
        read = {"turn": {"action": "search_source", "requests": [{"targets": [{"value": "needle"}]}]}}
        turns = iter([read, read, answer("E1")])
        with patch("project_control.services.local_investigate.source_context", return_value=envelope("source_context", initial, {"targets": []})):
            result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda value: (inputs.append(value) or next(turns)))
        self.assertEqual(result.data["status"], "ok")
        self.assertIn(" FINAL", inputs[2]["messages"][-1]["content"])

    def test_freshness_and_time_bounds_remain_clean(self) -> None:
        initial, changed = snapshot(), snapshot("b" * 40)
        result = local_investigate(config(), LocalInvestigateInput(project="demo", question="q"),
            snapshot=initial, snapshot_getter=lambda: changed, model_turn=lambda _: self.fail("model must not run"))
        self.assertEqual(result.data["status"], "refresh_required")
        calls = []
        def slow(*_args, **_kwargs):
            calls.append(1); time.sleep(0.02); return envelope("coordination_view", initial, {})
        read = {"turn": {"action": "inspect_workflow", "requests": [{}, {}, {}, {}]}}
        with patch.dict(LIMITS, {"quick": Limits(3, 8, 64 * 1024, 0.01, 8)}), \
             patch("project_control.services.local_investigate.coordination_view", side_effect=slow):
            bounded = local_investigate(config(), LocalInvestigateInput(project="demo", question="q", effort="quick"),
                snapshot=initial, snapshot_getter=lambda: initial, model_turn=lambda _: read)
        self.assertEqual(len(calls), 1)
        self.assertIn("investigation_time_budget_exhausted", bounded.warnings)
