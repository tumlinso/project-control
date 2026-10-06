"""Native source-mode tests for request-local observer turn policies."""

from __future__ import annotations

import importlib
import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, RLock, Semaphore
from types import SimpleNamespace
from unittest import mock

from project_control.assistance.policies import (
    generation_settings,
    policy_for,
    validate_policy_id,
)
from project_control import runtime_binding


class TurnPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Source-checkout policy tests must not inherit a release pin from the
        # installed production environment. Installed-release rejection is
        # covered independently by test_runtime_binding.
        with mock.patch.dict(os.environ):
            os.environ.pop(runtime_binding.RELEASE_MANIFEST_VARIABLE, None)
            os.environ.pop(runtime_binding.RELEASE_DIGEST_VARIABLE, None)
            cls.supervisor_module = runtime_binding.import_local_worker_supervisor()
        cls.adapter_module = importlib.import_module("local_worker.servers.llama_cpp")

    def test_registry_is_versioned_bounded_and_behaviorally_distinct(self):
        gather = policy_for("gather-v1")
        synthesis = policy_for("synthesis-v1")
        plan = policy_for("experiment-plan-v1")

        self.assertEqual((gather.version, gather.behavior, gather.logical_context_tokens,
                          gather.reasoning_tokens, gather.visible_tokens),
                         (1, "gather", 8192, 512, 1024))
        self.assertEqual((synthesis.behavior, synthesis.logical_context_tokens,
                          synthesis.reasoning_tokens, synthesis.visible_tokens),
                         ("synthesis", 16384, 2048, 2048))
        self.assertEqual(plan.behavior, "experiment_plan")
        instructions = {gather.instruction, synthesis.instruction, plan.instruction}
        self.assertEqual(len(instructions), 3)
        self.assertIn("facts", gather.instruction)
        self.assertIn("Synthesize", synthesis.instruction)
        self.assertIn("experiment plan", plan.instruction)
        with self.assertRaises(AttributeError):
            gather.visible_tokens = 10000
        with self.assertRaisesRegex(ValueError, "observer_turn_policy_unknown"):
            validate_policy_id("gather-v999")
        with self.assertRaisesRegex(ValueError, "observer_turn_policy_unknown"):
            validate_policy_id({"reasoning_tokens": 16384})

    def test_generation_settings_are_fresh_request_local_values(self):
        first = generation_settings("gather-v1")
        second = generation_settings("synthesis-v1")
        first["reasoning_tokens"] = 16384

        self.assertEqual(second["reasoning_tokens"], 2048)
        self.assertEqual(generation_settings("gather-v1")["reasoning_tokens"], 512)

    @classmethod
    def _supervisor_fixture(cls):
        supervisor = cls.supervisor_module.ProductionBackend.__new__(cls.supervisor_module.ProductionBackend)
        supervisor.profile = {"observer_generation": {"reasoning_tokens": 4096}}
        supervisor._analysis_capacity = Semaphore(2)
        supervisor._pool_lock = RLock()
        supervisor._leases = {"session-gather": "slot-gather", "session-synthesis": "slot-synthesis"}
        supervisor._slots = {
            "slot-gather": SimpleNamespace(slot_id="slot-gather", service_lease_id="session-gather", endpoint_descriptor={
                "model_id": "same", "compute_profile": "narrow", "parallelism": "default"}),
            "slot-synthesis": SimpleNamespace(slot_id="slot-synthesis", service_lease_id="session-synthesis", endpoint_descriptor={
                "model_id": "same", "compute_profile": "narrow", "parallelism": "default"}),
        }
        supervisor._resolved_parallelism = lambda *_: "default"
        return supervisor

    def test_concurrent_supervisor_turns_do_not_share_policy_settings(self):
        supervisor_type = self.supervisor_module.ProductionBackend
        supervisor = self._supervisor_fixture()
        rendezvous = Barrier(2)
        observed = {}
        observed_lock = threading.Lock()

        def run_slot(_self, _slot, request):
            rendezvous.wait(timeout=5)
            with observed_lock:
                observed[request["turn_policy_id"]] = {
                    "reasoning_tokens": request["observer_generation"]["reasoning_tokens"],
                    "instruction": request["turn_policy_instruction"],
                    "max_tokens": request["max_tokens"],
                    "logical_context_tokens": request["logical_context_tokens"],
                }
            return {"text": "ok", "usage": {}}

        def invoke(policy_id, session_id):
            return supervisor.run_observer_turn({
                "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
                "messages": [{"role": "user", "content": "bounded request"}],
                "timeout_seconds": 20,
                "session_id": session_id,
                "turn_policy_id": policy_id,
            })

        with mock.patch.object(supervisor_type, "_run_slot", run_slot):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: invoke(*pair), [
                    ("gather-v1", "session-gather"),
                    ("synthesis-v1", "session-synthesis"),
                ]))

        self.assertTrue(all(result["status"] == "available" for result in results))
        self.assertEqual(observed, {
            "gather-v1": {"reasoning_tokens": 512,
                          "instruction": policy_for("gather-v1").instruction,
                          "max_tokens": 1024, "logical_context_tokens": 8192},
            "synthesis-v1": {"reasoning_tokens": 2048,
                             "instruction": policy_for("synthesis-v1").instruction,
                             "max_tokens": 2048, "logical_context_tokens": 16384},
        })
        self.assertEqual(results[0]["turn_policy"]["policy_id"], "gather-v1")
        self.assertEqual(results[1]["turn_policy"]["policy_id"], "synthesis-v1")

    def test_adapter_injects_registry_instruction_on_copy_and_reports_budgets(self):
        captured_messages = []
        completion_number = 0

        def transport(_method, url, payload, _timeout):
            nonlocal completion_number
            if url.endswith("/apply-template"):
                captured_messages.append(payload["messages"])
                prompt = "\n".join(f"{item['role']}:{item['content']}" for item in payload["messages"])
                if payload.get("chat_template_kwargs", {}).get("enable_thinking"):
                    prompt += "<think>"
                return 200, {"prompt": prompt}
            if url.endswith("/tokenize"):
                return 200, {"tokens": list(range(len(payload["content"])))}
            if url.endswith("/completion"):
                completion_number += 1
                if completion_number % 2:
                    return 200, {
                        "content": "synthetic hidden reasoning",
                        "tokens": [1, 2, 3],
                        "timings": {"predicted_n": 3, "predicted_ms": 1.0,
                                    "predicted_per_second": 100.0},
                    }
                return 200, {
                    "content": "visible answer",
                    "tokens": [11, 12, 13],
                    "timings": {"predicted_n": 3, "predicted_ms": 1.0},
                }
            raise AssertionError(f"unexpected fake adapter path: {url}")

        adapter = self.adapter_module.LlamaCppServerAdapter(transport=transport)
        adapter._servers["fake"] = {
            "base_url": "http://fake",
            "accepting": True,
            "evicted": False,
            "canceled": set(),
            "active_requests": set(),
            "profile": {"context_size": 32768},
            "effective_context_size": 7000,
            "conservative_tokens_per_second": 100.0,
            "conservative_prompt_tokens_per_second": 1000.0,
            "reasoning_state": {},
            "last_completion_tokens": None,
            "usage": {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0, "duration_ms": 0.0},
        }
        original = [
            {"role": "system", "content": "Use only the evidence supplied."},
            {"role": "user", "content": "Summarize this evidence."},
        ]
        original_copy = json.loads(json.dumps(original))

        outputs = {}
        for policy_id in ("gather-v1", "synthesis-v1", "experiment-plan-v1"):
            selected = policy_for(policy_id)
            internal = {
                "messages": original,
                "max_tokens": selected.visible_tokens,
                "timeout_seconds": 30,
                "reasoning_mode": selected.reasoning_mode,
                "reasoning_state_key": f"session-{policy_id}",
                "observer_generation": generation_settings(policy_id),
                "turn_policy_id": policy_id,
                "turn_policy_instruction": selected.instruction,
                "logical_context_tokens": selected.logical_context_tokens,
            }
            outputs[policy_id] = adapter._run_observer_generation("fake", internal, internal["observer_generation"])

        self.assertEqual(original, original_copy)
        expected_instructions = {policy_for(policy_id).instruction for policy_id in outputs}
        seen_instructions = set()
        for captured in captured_messages:
            self.assertEqual(captured[0], original[0])
            self.assertEqual(captured[1]["role"], "system")
            seen_instructions.add(captured[1]["content"])
            self.assertEqual(captured[2], original[1])
        self.assertEqual(seen_instructions, expected_instructions)

        for policy_id, result in outputs.items():
            selected = policy_for(policy_id)
            budget = result["usage"]["budget"]
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(budget["requested"], {
                "logical_context_tokens": selected.logical_context_tokens,
                "reasoning_tokens": selected.reasoning_tokens,
                "visible_tokens": selected.visible_tokens,
            })
            self.assertEqual(budget["effective"]["logical_context_tokens"],
                             min(7000, selected.logical_context_tokens))
            self.assertGreater(budget["effective"]["reasoning_tokens"], 0)
            self.assertLessEqual(budget["effective"]["reasoning_tokens"], selected.reasoning_tokens)
            self.assertEqual(budget["effective"]["visible_tokens"], selected.visible_tokens)
            self.assertEqual(budget["used"]["reasoning_tokens"], 3)
            self.assertEqual(budget["used"]["visible_tokens"], 3)
            self.assertGreater(budget["used"]["context_tokens"], 0)
            serialized = json.dumps({"text": result["text"], "usage": result["usage"],
                                     "response_metadata": result["response_metadata"]})
            self.assertNotIn("synthetic hidden reasoning", serialized)

    def test_adapter_rejects_instruction_that_does_not_match_policy(self):
        adapter = self.adapter_module.LlamaCppServerAdapter(transport=lambda *_: self.fail("transport called"))
        adapter._servers["fake"] = {
            "base_url": "http://fake", "accepting": True, "evicted": False,
            "canceled": set(), "active_requests": set(), "profile": {"context_size": 32768},
            "reasoning_state": {}, "last_completion_tokens": None,
            "usage": {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0, "duration_ms": 0.0},
        }
        request = {
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 1024,
            "timeout_seconds": 5,
            "reasoning_mode": "auto",
            "reasoning_state_key": "session",
            "observer_generation": generation_settings("gather-v1"),
            "turn_policy_id": "gather-v1",
            "turn_policy_instruction": policy_for("synthesis-v1").instruction,
            "logical_context_tokens": 8192,
        }
        with self.assertRaisesRegex(self.adapter_module.AdapterError, "observer_turn_policy_mismatch"):
            adapter._run_observer_generation("fake", request, request["observer_generation"])

    def test_policy_turn_rejects_raw_budget_or_mode_overrides(self):
        for extra in ({"max_tokens": 2048}, {"reasoning_mode": "off"}):
            with self.subTest(extra=extra):
                supervisor = self._supervisor_fixture()
                request = {
                    "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
                    "messages": [{"role": "user", "content": "bounded request"}],
                    "timeout_seconds": 20,
                    "session_id": "session-gather",
                    "turn_policy_id": "gather-v1",
                    **extra,
                }
                result = supervisor.run_observer_turn(request)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["reason"], "investigator_turn_policy_raw_budget_denied")

    def test_supervisor_rejects_caller_supplied_policy_instruction(self):
        supervisor = self._supervisor_fixture()
        result = supervisor.run_observer_turn({
            "format": "PC-LOCAL-INVESTIGATOR-TURN/2",
            "messages": [{"role": "user", "content": "bounded request"}],
            "timeout_seconds": 20,
            "session_id": "session-gather",
            "turn_policy_id": "gather-v1",
            "turn_policy_instruction": "caller-controlled prompt",
        })
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "investigator_turn_invalid_request")


if __name__ == "__main__":
    unittest.main()
