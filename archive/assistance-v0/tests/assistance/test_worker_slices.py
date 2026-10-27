"""Focused fake-backend tests for one-turn observer slices."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from project_control.local_runtime.local_worker.observer_runtime import ObserverWorkerPort


class FakeBackend:
    def __init__(self, responses, *, on_call=None):
        self.responses = list(responses)
        self.turns = []
        self.on_call = on_call

    def run_observer_turn(self, turn):
        self.turns.append(json.loads(json.dumps(turn)))
        if self.on_call:
            self.on_call(turn)
        if not self.responses:
            raise AssertionError("unexpected backend call")
        response = self.responses.pop(0)
        return response(turn) if callable(response) else response


class FakeCommand:
    def __init__(self, root):
        self.roots = (Path(root),)
        self.packet_ids = 0

    def allows(self, path):
        target = Path(path).resolve()
        return any(target == root.resolve() or root.resolve() in target.parents for root in self.roots)

    def _packet(self, payload, guard=None):
        if guard is not None and not guard():
            raise RuntimeError("stale")
        self.packet_ids += 1
        return {**payload, "packet_id": f"feedback-{self.packet_ids}"}

    def run(self, argv, cwd, max_output_bytes=8192, guard=None, deadline_epoch=None, **kwargs):
        if guard is not None and not guard():
            raise RuntimeError("stale")
        path = Path(argv[-1]).resolve()
        payload = path.read_text(encoding="utf-8")
        self.packet_ids += 1
        return {"status": "completed", "exit_code": 0, "stdout": payload, "stderr": "",
                "truncated": False, "packet_id": f"command-{self.packet_ids}",
                "source_reads": [{"path": str(path), "content_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                                  "line_count": len(payload.splitlines()), "method": "direct_cat"}]}


class WorkerSliceTests(unittest.TestCase):
    def make_worker(self, responses, *, tool_result=None, fence=None, on_call=None):
        directory = tempfile.TemporaryDirectory(prefix="pa1-worker-slice-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        command = FakeCommand(root)
        calls = []
        checkpoints = []

        def tools(name, arguments):
            calls.append((name, arguments))
            return tool_result or {"status": "completed", "packet_id": "obs-1", "text": "source says 300 seconds",
                "source_reads": [{"path": "/fixture/budgets.py", "content_sha256": "a" * 64,
                                  "line_count": 1, "method": "direct_cat"}]}

        backend = FakeBackend(responses, on_call=on_call)
        fence = fence or (lambda job_id, attempt: True)
        worker = ObserverWorkerPort(backend, command=command, tools=tools, fence=fence,
                                    checkpoint=lambda job_id, attempt, obs: checkpoints.append(json.loads(json.dumps(obs))))
        return worker, backend, calls, checkpoints, root

    @staticmethod
    def request(**overrides):
        return {"job_id": "job-1", "attempt": 1, "mode": "investigate", "question": "Find the deadline",
                "max_steps": 2, "deadline_epoch": time.time() + 60, **overrides}

    @staticmethod
    def available(value):
        return {"status": "available", "text": json.dumps(value)}

    def test_accepted_tool_is_checkpointed_then_cited_without_redispatch(self):
        worker, backend, calls, checkpoints, _ = self.make_worker([
            self.available({"tool": "search", "arguments": {"query": "inquiry deadline"}}),
            self.available({"answer": "The deadline is 300 seconds.", "findings": [
                {"text": "The source reports 300 seconds.", "evidence_packets": ["obs-1"]}], "unresolved_questions": []}),
        ])
        first = worker.run_slice(self.request(), max_model_turns=1)
        self.assertEqual((first["status"], first["reason"]), ("yielding", "slice_complete"))
        self.assertEqual(first["model_turns_used"], 1)
        self.assertEqual(first["remaining_steps"], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(checkpoints[-1], first["observations"])

        resumed = worker.run_slice(self.request(observations=first["observations"], model_turns_used=1), max_model_turns=1)
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["answer"], "The deadline is 300 seconds.")
        self.assertEqual(len(calls), 1, "accepted tool call must not be executed again after resume")
        second_turn = backend.turns[1]
        assistant_calls = [json.loads(item["content"]) for item in second_turn["messages"]
                           if item["role"] == "assistant"]
        self.assertEqual(assistant_calls, [{"tool": "search", "arguments": {"query": "inquiry deadline"}}])
        self.assertIn("300 seconds", json.dumps(second_turn["messages"]))

    def test_invalid_wait_is_not_dispatched_and_final_answer_can_follow(self):
        worker, backend, calls, _, _ = self.make_worker([
            self.available({"private_wait": {"kind": "child", "question": "read source", "max_turns": True}}),
            self.available({"answer": "No child was needed.", "findings": [], "unresolved_questions": []}),
        ])
        result = worker.run_slice(self.request(max_steps=4), max_model_turns=2)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("private_wait", result)
        self.assertEqual(calls, [])
        self.assertIn("invalid_private_wait_proposal", json.dumps(backend.turns[1]["messages"]))
        self.assertEqual(result["model_turns_used"], 2)
        self.assertEqual(result["remaining_steps"], 2)

    def test_valid_private_wait_is_typed_and_final_budget_never_waits(self):
        proposal = {"kind": "child", "question": "Read one named source file", "max_turns": 2}
        worker, _, calls, _, _ = self.make_worker([self.available({"private_wait": proposal})])
        result = worker.run_slice(self.request(max_steps=4), max_model_turns=1)
        self.assertEqual(result["status"], "yielding")
        self.assertEqual(result["reason"], "private_wait_proposed")
        self.assertEqual(result["private_wait"], proposal)
        self.assertEqual(result["remaining_steps"], 3)
        self.assertEqual(calls, [])

        final_worker, _, final_calls, _, _ = self.make_worker([self.available({"private_wait": proposal})])
        final = final_worker.run_slice(self.request(max_steps=1), max_model_turns=1)
        self.assertEqual((final["status"], final["reason"]), ("partial", "final_round_requires_answer"))
        self.assertNotIn("private_wait", final)
        self.assertEqual(final_calls, [])

    def test_broker_child_results_are_context_only_and_acknowledged_after_turn(self):
        child_results = [{"child_id": "child-1", "status": "completed",
                          "result": {"answer": "The file says 300 seconds."}, "terminal_version": 3}]
        worker, backend, _, _, _ = self.make_worker([
            self.available({"answer": "The child reports 300 seconds.", "findings": [
                {"text": "Child answer", "evidence_packets": ["child-1"]}], "unresolved_questions": []})
        ])
        result = worker.run_slice(self.request(max_steps=3, private_child_results=child_results), max_model_turns=1)
        self.assertEqual((result["status"], result["reason"]), ("yielding", "slice_complete"))
        self.assertEqual(result["private_child_results_consumed"], ["child-1"])
        message_text = json.dumps(backend.turns[0]["messages"])
        self.assertIn("private controller result context; not a public observation packet and not citable", message_text)
        self.assertNotIn("child-1", [item["packet_id"] for item in result["observations"]])

    def test_expired_deadline_is_terminal_and_does_not_call_backend(self):
        worker, backend, calls, checkpoints, _ = self.make_worker([self.available({"answer": "too late", "findings": [],
                                                                                     "unresolved_questions": []})])
        result = worker.run_slice(self.request(deadline_epoch=time.time() - 1), max_model_turns=1)
        self.assertEqual(result["status"], "partial")
        self.assertIn("inquiry_deadline_exceeded", result["reason"])
        self.assertEqual(backend.turns, [])
        self.assertEqual(calls, [])
        self.assertEqual(checkpoints, [])

    def test_expired_legacy_deadline_raises_before_any_effect(self):
        worker, backend, calls, checkpoints, _ = self.make_worker([self.available({"answer": "too late", "findings": [],
                                                                                     "unresolved_questions": []})])
        with self.assertRaisesRegex(TimeoutError, "inquiry_deadline_exceeded"):
            worker.run(self.request(deadline_epoch=time.time() - 1))
        self.assertEqual(backend.turns, [])
        self.assertEqual(calls, [])
        self.assertEqual(checkpoints, [])

    def test_generation_fence_rejects_late_model_result(self):
        current = {"valid": True}

        def invalidate(_turn):
            current["valid"] = False

        worker, backend, calls, checkpoints, _ = self.make_worker(
            [self.available({"tool": "search", "arguments": {"query": "must not dispatch"}})],
            fence=lambda job_id, attempt: current["valid"], on_call=invalidate)
        result = worker.run_slice(self.request(), max_model_turns=1)
        self.assertEqual(result["status"], "stale_attempt")
        self.assertEqual(calls, [])
        self.assertEqual(checkpoints, [])
        self.assertEqual(len(backend.turns), 1)

    def test_private_child_result_shape_is_bounded_and_strict(self):
        worker, _, _, _, _ = self.make_worker([])
        bad_requests = [
            self.request(private_child_results=[{"child_id": "c", "status": "completed", "result": {},
                                                  "terminal_version": 1, "scope": "/"}]),
            self.request(private_child_results=[{"child_id": "c", "status": "completed",
                "result": {"answer": "visible", "hidden_reasoning": "private"}, "terminal_version": 1}]),
        ]
        for bad in bad_requests:
            with self.subTest(result=bad["private_child_results"][0]["result"]), self.assertRaisesRegex(
                    ValueError, "invalid private child results"):
                worker.run_slice(bad, max_model_turns=1)

    def test_trusted_policy_id_is_validated_and_sent_without_raw_budget_overrides(self):
        worker, backend, _, _, _ = self.make_worker([
            self.available({"answer": "done", "findings": [], "unresolved_questions": []})
        ])
        result = worker.run_slice(self.request(max_steps=2, trusted_turn_policy_id="gather-v1"), max_model_turns=1)
        self.assertEqual(result["status"], "completed")
        turn = backend.turns[0]
        self.assertEqual(turn["turn_policy_id"], "gather-v1")
        self.assertNotIn("max_tokens", turn)
        self.assertNotIn("reasoning_mode", turn)

        invalid_worker, _, _, _, _ = self.make_worker([])
        with self.assertRaisesRegex(ValueError, "observer_turn_policy_unknown"):
            invalid_worker.run_slice(self.request(trusted_turn_policy_id="gather-v999"), max_model_turns=1)

    def test_skill_entry_and_selected_source_proof_survive_four_slices(self):
        worker, backend, tool_calls, checkpoints, root = self.make_worker([])
        entry = root / "SKILL.md"
        resource = root / "maps.md"
        entry.write_text("# Fixture\nUse maps.md\n", encoding="utf-8")
        resource.write_text("Exact selected instructions\n", encoding="utf-8")
        entry_text = entry.read_text(encoding="utf-8")
        resource_text = resource.read_text(encoding="utf-8")
        resource_hash = hashlib.sha256(resource_text.encode()).hexdigest()

        def final(turn):
            messages = turn["messages"]
            replayed = [(json.loads(message["content"]), json.loads(messages[index + 1]["content"]))
                for index, message in enumerate(messages[:-1])
                if message["role"] == "assistant" and messages[index + 1]["role"] == "user"]
            self.assertEqual([call["tool"] for call, _ in replayed], ["command", "command", "search"])
            self.assertEqual(replayed[0][0]["arguments"]["argv"], ["cat", str(entry)])
            self.assertEqual(replayed[0][1]["stdout"], entry_text)
            self.assertEqual(replayed[1][0]["arguments"]["argv"], ["cat", str(resource)])
            self.assertEqual(replayed[1][1]["stdout"], resource_text)
            self.assertEqual(replayed[1][1]["source_reads"][0]["content_sha256"], resource_hash)
            return self.available({"answer": "read sources", "findings": [
                {"text": "Selected source", "evidence_packets": [replayed[1][1]["packet_id"]]}],
                "unresolved_questions": [], "skill_selection": {
                    "format": "pc-skill-selection/1", "selections": [{"skill": "fixture", "resource": "maps.md",
                        "content_sha256": resource_hash, "line_start": 1, "line_end": 1, "reason": "useful"}],
                    "synthesis": "selected", "unresolved": []}})

        backend.responses = [
            self.available({"tool": "command", "arguments": {"argv": ["cat", str(entry)], "cwd": str(root)}}),
            self.available({"tool": "command", "arguments": {"argv": ["cat", str(resource)], "cwd": str(root)}}),
            self.available({"tool": "search", "arguments": {"query": "fixture"}}),
            final,
        ]
        skill = {"name": "fixture", "root": str(root)}
        observations = []
        turns_used = 0
        for _ in range(4):
            result = worker.run_slice(self.request(mode="skill", skill=skill, max_steps=6,
                observations=observations, model_turns_used=turns_used), max_model_turns=1)
            observations = result["observations"]
            turns_used += result["model_turns_used"]
            if result["status"] == "completed":
                break
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(turns_used, 4)
        self.assertEqual(tool_calls, [("search", {"query": "fixture"})])
        self.assertEqual(len(checkpoints), 4)
        self.assertTrue(checkpoints)


if __name__ == "__main__":
    unittest.main()
