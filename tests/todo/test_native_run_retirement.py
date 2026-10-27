from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from todo_orchestrator.contracts import EntityKind, EntityRef, StableId
from todo_orchestrator.operations import RunAssignmentHandoff, UnresolvedRunAssignment

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.native_workflow import NativeWorkflowHost


class _Session:
    def __init__(self) -> None:
        self.project_id = StableId("project-test")
        self.retirement_calls = []
        self.closed = False

    def run_schedule_digest(self, _run_id):
        return SimpleNamespace(hex="a" * 64)

    def assignment_ref(self, assignment_id):
        return EntityRef(self.project_id, EntityKind.ASSIGNMENT, StableId(str(assignment_id)))

    def retire_run(self, run_id, digest, **kwargs):
        self.retirement_calls.append((run_id, digest, kwargs))
        return SimpleNamespace(to_dict=lambda: {"run_id": run_id, "state": "retired"})

    def close_run(self, run_id, digest, **kwargs):
        return SimpleNamespace(to_dict=lambda: {"run_id": run_id, "state": "closed"})

    def close(self):
        self.closed = True


def _host(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(
            authority_repository="source",
            repositories={"source": RepositoryConfig(root=source)},
        ),
    })
    host_identity = SimpleNamespace(profile="mutator", principal="host", projects=frozenset({"demo"}))
    sessions: list[_Session] = []

    def open_session(*_args, **_kwargs):
        session = _Session()
        sessions.append(session)
        return session

    host = NativeWorkflowHost(
        config,
        host_identity,
        session_for_host=open_session,
        providers_for_project=lambda *_args, **_kwargs: None,
        binding_validator=lambda: None,
    )
    return host, sessions


def _base_args():
    return {
        "project": "demo",
        "run_id": "run-v1",
        "successor_run_id": "run-v2",
        "schedule_digest": "a" * 64,
        "idempotency_key": "retire-v1-to-v2",
        "expected_revision": 17,
    }


def test_retire_run_forwards_versioned_typed_handoffs_and_unresolved_reasons(tmp_path):
    host, sessions = _host(tmp_path)
    args = {
        **_base_args(),
        "disposition_schema_version": 1,
        "handoffs": [{
            "source_assignment_id": "v1-kernel-tests",
            "successor_assignment_id": "v2-kernel-tests",
        }],
        "unresolved": [{
            "assignment_id": "v1-profiling",
            "reason": "Superseded by v2 profiling acceptance; retain this assignment unresolved for audit.",
        }],
    }

    result = host.invoke("todo_retire_run", **args)

    assert result == {"status": "ok", "data": {"run_id": "run-v1", "state": "retired"}}
    session = sessions[0]
    run_id, digest, forwarded = session.retirement_calls[0]
    assert run_id == "run-v1"
    assert digest.hex == "a" * 64
    assert forwarded["successor_run"] == StableId("run-v2")
    assert forwarded["expected_revision"] == 17
    assert len(forwarded["handoffs"]) == len(forwarded["unresolved"]) == 1
    handoff = forwarded["handoffs"][0]
    unresolved = forwarded["unresolved"][0]
    assert isinstance(handoff, RunAssignmentHandoff)
    assert handoff.source_assignment.entity_id == StableId("v1-kernel-tests")
    assert handoff.successor_assignment.entity_id == StableId("v2-kernel-tests")
    assert isinstance(unresolved, UnresolvedRunAssignment)
    assert unresolved.assignment.entity_id == StableId("v1-profiling")
    assert unresolved.reason == args["unresolved"][0]["reason"]
    assert session.closed


def test_legacy_completed_run_call_keeps_existing_shape_and_empty_dispositions(tmp_path):
    host, sessions = _host(tmp_path)

    result = host.invoke("todo_retire_run", **_base_args())

    assert result["status"] == "ok"
    forwarded = sessions[0].retirement_calls[0][2]
    assert forwarded["handoffs"] == ()
    assert forwarded["unresolved"] == ()
    assert forwarded["successor_run"] == StableId("run-v2")


def test_close_run_path_is_unchanged(tmp_path):
    host, _sessions = _host(tmp_path)
    args = _base_args()
    args.pop("successor_run_id")

    result = host.invoke("todo_close_run", **args)

    assert result == {"status": "ok", "data": {"run_id": "run-v1", "state": "closed"}}


@pytest.mark.parametrize("extra", [
    {"handoffs": []},
    {"disposition_schema_version": 2, "handoffs": [], "unresolved": []},
    {"disposition_schema_version": 1, "handoffs": [], "unresolved": [], "unrecognized": True},
    {"disposition_schema_version": 1, "handoffs": [{"source_assignment_id": "a", "successor_assignment_id": "b"}], "unresolved": [], "successor_run_id": None},
    {"disposition_schema_version": True, "handoffs": [], "unresolved": []},
    {"disposition_schema_version": 1, "handoffs": [{"source_assignment_id": "a", "successor_assignment_id": "b", "reason": "extra"}], "unresolved": []},
])
def test_retirement_rejects_unversioned_or_malformed_dispositions_before_session(tmp_path, extra):
    host, sessions = _host(tmp_path)

    result = host.invoke("todo_retire_run", **{**_base_args(), **extra})

    assert result["status"] == "attention_required"
    assert not sessions


def test_retirement_rejects_duplicate_or_conflicting_assignment_dispositions(tmp_path):
    host, sessions = _host(tmp_path)
    args = {
        **_base_args(),
        "disposition_schema_version": 1,
        "handoffs": [
            {"source_assignment_id": "same", "successor_assignment_id": "next-a"},
            {"source_assignment_id": "same", "successor_assignment_id": "next-b"},
        ],
        "unresolved": [{"assignment_id": "same", "reason": "Retained unresolved because it was not handed off."}],
    }

    result = host.invoke("todo_retire_run", **args)

    assert result["status"] == "attention_required"
    assert not sessions


def test_retirement_bounds_assignment_count_and_reason_size(tmp_path):
    host, sessions = _host(tmp_path)
    too_many = [{"assignment_id": f"a-{index}", "reason": "retain explicitly for audit"} for index in range(4097)]
    result = host.invoke("todo_retire_run", **{
        **_base_args(), "disposition_schema_version": 1, "handoffs": [], "unresolved": too_many,
    })
    assert result["status"] == "attention_required"
    assert not sessions

    too_long = [{"assignment_id": "a", "reason": "r" * 513}]
    result = host.invoke("todo_retire_run", **{
        **_base_args(), "disposition_schema_version": 1, "handoffs": [], "unresolved": too_long,
    })
    assert result["status"] == "attention_required"
    assert not sessions
