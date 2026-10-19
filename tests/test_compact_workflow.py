"""Focused end-to-end unit coverage for the compact workflow journal and lifecycle."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time
import threading

import project_control.compact_workflow as compact_module
from project_control.compact_workflow import CompactWorkflowHost


class _NativeHost:
    def __init__(self, profile: str = "coder") -> None:
        self.host = SimpleNamespace(principal=f"actor-{profile}", profile=profile)
        self.projects = frozenset({"workspace"})
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.candidates: list[dict[str, object]] = []
        self.attempts: list[dict[str, object]] = []
        self.revision = 7
        self.stale_workspace_once = False
        self.lose_dispatch_once = False
        self.large_context = False
        self.context_build_gate: tuple[threading.Event, threading.Event] | None = None
        self._dispatch_intent: tuple[object, object] | None = None

    def _project_root(self, project: str, repository: str | None = None):
        if project != "workspace" or repository not in {None, "source"}:
            raise RuntimeError("project_not_permitted")
        return Path("/tmp"), repository or "source"

    def select_next_work(self, **_kwargs):
        raise AssertionError("selection must use the authenticated native invoke route")

    def invoke(self, tool: str, **arguments):
        self.calls.append((tool, dict(arguments)))
        data: dict[str, object]
        if tool == "todo_select_next_work":
            data = {
                "work_item_id": "task-1", "run_id": "run-1",
                "assignment_id": "assignment-1", "plan_revision_id": "plan-1",
                "authority_revision": 7,
            }
        elif tool == "todo_create_workspace":
            if self.stale_workspace_once:
                self.stale_workspace_once = False
                self.revision += 1  # A separate writer advanced authority before commit.
                return {"status": "attention_required", "reason": "stale_authority_revision"}
            self.revision += 1
            data = {"workspace_ref": {"entity_id": "workspace-1"},
                    "workspace": {"workspace_id": "workspace-1",
                                  "path": "/tmp/workspaces/task-1", "repository": "source"}}
        elif tool == "todo_dispatch_attempt":
            intent = (arguments.get("idempotency_key"), arguments.get("expected_revision"))
            if self._dispatch_intent is not None:
                assert intent == self._dispatch_intent, "dispatch replay must retain the exact native request"
            self._dispatch_intent = intent
            if self.lose_dispatch_once:
                self.lose_dispatch_once = False
                self.revision += 1  # Native commit succeeded; only the response was lost.
                return {"status": "attention_required", "reason": "simulated_lost_response"}
            data = {"attempt_ref": {"entity_id": "attempt-1"}, "lease_id": "lease-1"}
        elif tool == "todo_build_context":
            if self.context_build_gate is not None:
                entered, release = self.context_build_gate
                entered.set()
                assert release.wait(1.0), "test did not release blocked context build"
            if self.large_context:
                work_ref = {"fragment_id": "work-fragment", "kind": "work_item",
                            "digest": "d" * 64}
                delta_ref = {"fragment_id": "delta-fragment", "kind": "scoped_deltas",
                             "digest": "e" * 64}
                data = {
                    "format": "todo-orchestrator.context-capsule", "version": 1,
                    "manifest": {"fragments": [work_ref, delta_ref],
                                 "digest": "a" * 64, "source_digest": "b" * 64},
                    "semantic_revision": 8, "status": "ready", "changes": [],
                    "fragments": [
                        {"reference": work_ref, "payload": {"definition": {"title": "Task one"},
                                                             "authored": {"objective": "Make a useful change"},
                                                             "requirements": [{"id": "check-1", "kind": "check"}]}},
                        {"reference": delta_ref, "payload": {"events": [
                            {"event_id": f"event-{index}", "kind": "todo.attempt.updated",
                             "note": "historical context " * 10} for index in range(100)
                        ]}},
                    ],
                    "omitted": [], "encoded_bytes": 12000,
                }
            else:
                data = {
                    "manifest": {"fragments": []},
                    "fragments": [{"payload": {"requirements": [
                        {"id": "check-1", "kind": "check"},
                    ]}}],
                }
        elif tool == "todo_expand_context":
            reference = {"fragment_id": arguments["fragment_id"],
                         "kind": "scoped_deltas", "digest": "e" * 64}
            offset = arguments.get("offset") or 0
            data = {
                "status": "ready", "manifest": {"fragments": [reference]},
                "fragment": None, "chunk_base64": "eA==", "offset": offset,
                "total_bytes": 9000, "content_digest": "f" * 64,
                "next_offset": offset + 2048, "reference": reference,
            }
        elif tool == "todo_verify_attempt":
            data = {"requirement_id": arguments["requirement_id"], "state": "passed"}
        elif tool == "todo_inspect_attempt":
            data = {"attempt_id": arguments["attempt_id"], "state": "active"}
        elif tool == "todo_resume_attempt":
            data = {"attempt_id": arguments["attempt_id"], "state": "active",
                    "result": {"lease_id": "lease-resumed"}}
        elif tool == "todo_heartbeat_attempt":
            data = {"attempt_id": arguments["attempt_id"], "state": "active",
                    "result": {"lease_id": "lease-heartbeat"}}
        elif tool == "todo_send_message":
            data = {"message_id": "message-1"}
        elif tool == "todo_handback_attempt":
            data = {
                "attempt_id": arguments["attempt_id"], "lease_id": arguments["lease_id"],
                "source_commit": "c" * 40, "source_digest": "a" * 64,
                "receipt": {"receipt_id": "receipt-handoff"},
            }
        elif tool == "todo_report_outcome":
            self.candidates = [{
                "candidate_id": "candidate-1", "attempt_id": "attempt-1",
                "work_item_id": "task-1", "assignment_id": "assignment-1",
                "state": "pending", "disposition": arguments["disposition"],
                "source_digest": "a" * 64,
            }]
            data = {"candidate_ref": {"entity_id": "candidate-1"}}
        elif tool == "todo_release_attempt":
            data = {"state": "released"}
        elif tool == "todo_read_authority":
            data = {
                "semantic_revision": self.revision,
                "sections": {"outcome_candidates": list(self.candidates),
                             "attempts": [{"attempt_id": "attempt-1", "run_id": "run-1",
                                           "assignment_id": "assignment-1"}]},
                "page": {"next_page_token": None},
            }
        elif tool == "todo_read_candidate":
            data = {"candidate_id": arguments["candidate_id"], "state": "pending",
                    "source_digest": "a" * 64}
        elif tool in {"todo_accept_outcome", "todo_reject_outcome", "todo_integrate_attempt"}:
            data = {"state": tool.removeprefix("todo_")}
        else:
            raise AssertionError(f"unexpected native tool {tool}")
        return {"status": "ok", "data": data}


def _host(tmp_path: Path, native: _NativeHost) -> CompactWorkflowHost:
    return CompactWorkflowHost(native, state_directory=tmp_path / "state")


def test_complete_hands_back_before_report_and_keeps_reported_attempt_for_review(tmp_path: Path):
    native = _NativeHost("coder")
    facade = _host(tmp_path, native)

    claimed = facade.next_task(project="workspace", repository="source")
    assert claimed["status"] == "claimed"
    handle = claimed["workflow_handle"]

    finished = facade.finish_task(workflow_handle=handle, action="complete")
    assert finished["status"] == "review_pending"
    assert finished["task_id"] == "task-1"
    calls = [tool for tool, _arguments in native.calls]
    assert calls.index("todo_verify_attempt") < calls.index("todo_handback_attempt")
    assert calls.index("todo_handback_attempt") < calls.index("todo_report_outcome")
    assert "todo_release_attempt" not in calls

    replay = facade.finish_task(workflow_handle=handle, action="complete")
    assert replay["status"] == "review_pending"
    assert [tool for tool, _arguments in native.calls].count("todo_report_outcome") == 1
    assert [tool for tool, _arguments in native.calls].count("todo_handback_attempt") == 1


def test_reviewer_gets_separate_handle_and_can_accept_then_integrate(tmp_path: Path):
    native = _NativeHost("coder")
    coder = _host(tmp_path, native)
    author = coder.next_task(project="workspace")
    coder_result = coder.finish_task(workflow_handle=author["workflow_handle"], action="handoff")
    assert coder_result["status"] == "review_pending"

    reviewer_native = _NativeHost("mutator")
    reviewer_native.candidates = list(native.candidates)
    reviewer_native.attempts = list(native.attempts)
    reviewer = _host(tmp_path, reviewer_native)
    review_task = reviewer.next_task(project="workspace", task_id="task-1")
    assert review_task["status"] == "review_required"
    assert review_task["workflow_handle"] != author["workflow_handle"]
    handle = review_task["workflow_handle"]

    detail = reviewer.inspect_task(workflow_handle=handle, kind="task")
    assert detail["status"] == "ok"
    accepted = reviewer.finish_task(workflow_handle=handle, action="accept")
    assert accepted["status"] == "review_accepted"
    assert reviewer.finish_task(workflow_handle=handle, action="accept")["status"] == "review_accepted"
    integrated = reviewer.finish_task(workflow_handle=handle, action="integrate")
    assert integrated["status"] == "integrated"
    assert [tool for tool, _arguments in reviewer_native.calls].count("todo_accept_outcome") == 1
    assert [tool for tool, _arguments in reviewer_native.calls].count("todo_integrate_attempt") == 1


def test_release_hands_back_then_releases_without_reporting(tmp_path: Path):
    native = _NativeHost("coder")
    facade = _host(tmp_path, native)
    claimed = facade.next_task(project="workspace")

    result = facade.finish_task(workflow_handle=claimed["workflow_handle"], action="release")
    assert result["status"] == "released"
    calls = [tool for tool, _arguments in native.calls]
    assert calls.index("todo_handback_attempt") < calls.index("todo_release_attempt")
    assert "todo_report_outcome" not in calls


def test_dispatch_lost_response_replays_exact_key_and_expected_revision(tmp_path: Path):
    native = _NativeHost("coder")
    native.lose_dispatch_once = True
    facade = _host(tmp_path, native)

    first = facade.next_task(project="workspace")
    assert first["status"] == "attention_required"
    assert first["reason"] == "simulated_lost_response"
    first_dispatch = next(arguments for tool, arguments in native.calls
                          if tool == "todo_dispatch_attempt")

    resumed = facade.next_task(project="workspace")
    assert resumed["status"] == "resumed"
    replay = [arguments for tool, arguments in native.calls
              if tool == "todo_dispatch_attempt"]
    assert len(replay) == 2
    assert replay[0]["idempotency_key"] == replay[1]["idempotency_key"]
    assert replay[0]["expected_revision"] == replay[1]["expected_revision"]
    assert replay[0]["expected_revision"] == 8
    assert first_dispatch["idempotency_key"] == replay[1]["idempotency_key"]


def test_active_resume_uses_and_persists_the_replacement_lease(tmp_path: Path):
    native = _NativeHost("coder")
    facade = _host(tmp_path, native)
    claimed = facade.next_task(project="workspace")

    resumed = facade.next_task(project="workspace")
    assert resumed["status"] == "resumed"
    record = facade._store.get("workflow", claimed["workflow_handle"], "workspace", "source")
    assert record["lease_id"] == "lease-resumed"
    resume_call = next(arguments for tool, arguments in native.calls
                       if tool == "todo_resume_attempt")
    assert resume_call["attempt_id"] == "attempt-1"
    assert resume_call["lease_id"] == "lease-1"
    assert type(resume_call["expected_revision"]) is int


def test_host_renews_active_lease_in_background(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(compact_module, "_LEASE_RENEWAL_INTERVAL_SECONDS", 0.01)
    native = _NativeHost("coder")
    facade = _host(tmp_path, native)
    claimed = facade.next_task(project="workspace")
    handle = claimed["workflow_handle"]

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if any(tool == "todo_heartbeat_attempt" for tool, _arguments in native.calls):
            break
        time.sleep(0.005)
    else:
        raise AssertionError("host did not renew the active attempt lease")

    facade._stop_lease_renewal(handle)
    record = facade._store.get("workflow", handle, "workspace", "source")
    assert record["lease_id"] == "lease-heartbeat"
    heartbeat = next(arguments for tool, arguments in native.calls
                     if tool == "todo_heartbeat_attempt")
    assert heartbeat["expected_revision"] == 8


def test_context_pending_attempt_is_renewed_while_capsule_is_built(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(compact_module, "_LEASE_RENEWAL_INTERVAL_SECONDS", 0.01)
    native = _NativeHost("coder")
    entered = threading.Event()
    release = threading.Event()
    native.context_build_gate = (entered, release)
    facade = _host(tmp_path, native)
    result: list[dict[str, object]] = []
    worker = threading.Thread(target=lambda: result.append(
        facade.next_task(project="workspace")))
    worker.start()
    assert entered.wait(1.0), "context build did not enter its pending phase"

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if any(tool == "todo_heartbeat_attempt" for tool, _args in native.calls):
            break
        time.sleep(0.005)
    else:
        release.set()
        worker.join(1.0)
        raise AssertionError("context-pending lease was not renewed")

    release.set()
    worker.join(1.0)
    assert not worker.is_alive()
    assert result and result[0]["status"] == "claimed"
    context_call = next(args for tool, args in native.calls if tool == "todo_build_context")
    assert context_call["max_bytes"] == 4096
    handle = result[0]["workflow_handle"]
    record = facade._store.get("workflow", handle, "workspace", "source")
    assert record["phase"] == "active"
    assert record["lease_id"] == "lease-heartbeat"
    facade._stop_lease_renewal(handle)


def test_oversized_context_is_cached_privately_and_returned_with_continuation(tmp_path: Path):
    native = _NativeHost("coder")
    native.large_context = True
    facade = _host(tmp_path, native)

    packet = facade.next_task(project="workspace")
    handle = packet["workflow_handle"]
    record = facade._store.get("workflow", handle, "workspace", "source")
    assert len(compact_module._canonical(record["context"])) > compact_module._MAX_RESPONSE_BYTES
    assert len(compact_module._canonical(packet)) <= 8192
    assert packet["status"] == "claimed"
    assert packet["context_status"] == "partial"
    assert packet["workspace"]["path"] == "/tmp/workspaces/task-1"
    assert packet["task_summary"]["objective"] == "Make a useful change"
    assert packet["context_continuation"]["omitted_fragment_ids"] == ["delta-fragment"]

    first = facade.inspect_task(workflow_handle=handle, kind="context_fragment",
                                target="delta-fragment")
    assert first["status"] == "ok"
    assert len(compact_module._canonical(first)) <= 8192
    continuation = first["result"]["continuation_target"]
    assert continuation == "delta-fragment#offset=2048"
    second = facade.inspect_task(workflow_handle=handle, kind="context_fragment",
                                 target=continuation)
    assert second["status"] == "ok"
    expansion_calls = [args for tool, args in native.calls if tool == "todo_expand_context"]
    assert expansion_calls[0]["offset"] is None
    assert expansion_calls[1]["offset"] == 2048


def test_coordinate_mutation_waits_for_heartbeat_snapshot_call_and_store_update(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(compact_module, "_LEASE_RENEWAL_INTERVAL_SECONDS", 0.01)
    native = _NativeHost("coder")
    facade = _host(tmp_path, native)
    claimed = facade.next_task(project="workspace")
    handle = claimed["workflow_handle"]

    heartbeat_entered = threading.Event()
    release_heartbeat = threading.Event()
    original_invoke = native.invoke

    def paused_invoke(tool: str, **arguments):
        if tool == "todo_heartbeat_attempt":
            heartbeat_entered.set()
            assert release_heartbeat.wait(1.0), "test did not release paused heartbeat"
        return original_invoke(tool, **arguments)

    native.invoke = paused_invoke
    assert heartbeat_entered.wait(1.0), "background heartbeat did not enter native call"
    prior_call_count = len(native.calls)
    coordinate_started = threading.Event()
    coordinate_result: list[dict[str, object]] = []

    def coordinate():
        coordinate_started.set()
        coordinate_result.append(facade.coordinate_task(
            workflow_handle=handle, action="message",
            payload={"scope": {"kind": "attempt", "entity_id": "attempt-1"},
                     "recipients": ["actor-reviewer"], "subject": "status"},
        ))

    worker = threading.Thread(target=coordinate)
    worker.start()
    assert coordinate_started.wait(1.0)
    time.sleep(0.03)
    assert len(native.calls) == prior_call_count, "coordinate interleaved with heartbeat intent"

    release_heartbeat.set()
    worker.join(1.0)
    assert not worker.is_alive()
    facade._stop_lease_renewal(handle)
    assert coordinate_result and coordinate_result[0]["status"] == "ok"
    calls = [tool for tool, _arguments in native.calls]
    assert calls.index("todo_heartbeat_attempt") < calls.index("todo_send_message")


def test_only_definite_stale_cas_refreshes_the_persisted_phase_intent(tmp_path: Path):
    native = _NativeHost("coder")
    native.stale_workspace_once = True
    facade = _host(tmp_path, native)

    claimed = facade.next_task(project="workspace")
    assert claimed["status"] == "claimed"
    workspace_calls = [arguments for tool, arguments in native.calls
                       if tool == "todo_create_workspace"]
    assert len(workspace_calls) == 2
    assert workspace_calls[0]["expected_revision"] == 7
    assert workspace_calls[1]["expected_revision"] == 8
    assert workspace_calls[0]["idempotency_key"] != workspace_calls[1]["idempotency_key"]

