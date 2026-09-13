from __future__ import annotations

import unittest
import time
from unittest.mock import patch
from pathlib import Path

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import LocalInvestigateInput, ProjectSnapshot, RepositoryIdentity, ToolStatus, envelope
from project_control.services.local_investigate import LIMITS, PROTOCOL, SYSTEM_PROMPT, Limits, local_investigate


def snapshot(commit: str = "a" * 40) -> ProjectSnapshot:
    return ProjectSnapshot(workspace_id="demo", observed_at="2026-01-01T00:00:00Z",
        repositories={"source": RepositoryIdentity(commit=commit, dirty=False, working_tree_fingerprint="clean")})

def config() -> ProjectControlConfig:
    return ProjectControlConfig(workspaces={"demo": WorkspaceConfig(repositories={"source": RepositoryConfig(root=Path.cwd())})})


class LocalInvestigateTests(unittest.TestCase):
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
