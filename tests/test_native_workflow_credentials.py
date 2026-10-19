from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.native_workflow import NativeWorkflowHost


class _PublicValue:
    def __init__(self, name: str):
        self.name = name

    def to_dict(self):
        return {"operation": self.name}


class _Session:
    def __init__(self, source: str):
        self.source = source
        self.calls: list[tuple[object, ...]] = []
        self.closed = False

    def _call(self, name: str, *args, **kwargs):
        self.calls.append((name, *args, kwargs))
        return _PublicValue(name)

    def integrate_attempt(self, attempt_id, **kwargs):
        return self._call("integrate_attempt", attempt_id, **kwargs)

    def accept(self, attempt_id, candidate_id, **kwargs):
        return self._call("accept", attempt_id, candidate_id, **kwargs)

    def reject(self, attempt_id, candidate_id, **kwargs):
        return self._call("reject", attempt_id, candidate_id, **kwargs)

    def inspect_attempt(self, attempt_id):
        return self._call("inspect_attempt", attempt_id)

    def verify(self, attempt_id, requirement_id, **kwargs):
        return self._call("verify", attempt_id, requirement_id, **kwargs)

    def report(self, attempt_id, disposition, **kwargs):
        return self._call("report", attempt_id, disposition, **kwargs)

    def heartbeat_attempt(self, attempt_id, lease_id, **kwargs):
        return self._call("heartbeat_attempt", attempt_id, lease_id, **kwargs)

    def release_attempt(self, attempt_id, lease_id, **kwargs):
        return self._call("release_attempt", attempt_id, lease_id, **kwargs)

    def finish(self, attempt_id, lease_id, **kwargs):
        return self._call("finish", attempt_id, lease_id, **kwargs)

    def reserve_resource(self, attempt_id, resource_class, units, **kwargs):
        return self._call("reserve_resource", attempt_id, resource_class, units, **kwargs)

    def inspect_resource(self, operation_id):
        return self._call("inspect_resource", operation_id)

    def reconcile_resource(self, operation_id, **kwargs):
        return self._call("reconcile_resource", operation_id, **kwargs)

    def retry_resource(self, operation_id, **kwargs):
        return self._call("retry_resource", operation_id, **kwargs)

    def release_resource(self, operation_id, **kwargs):
        return self._call("release_resource", operation_id, **kwargs)

    def close(self):
        self.closed = True


def test_host_review_and_integration_use_host_session_but_worker_effects_use_attempt_session(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    host_sessions: list[_Session] = []
    attempt_sessions: list[_Session] = []
    forgotten_attempts: list[tuple[str, str, str]] = []

    def open_host(_root, *, principal, profile, providers=None):
        assert principal == "trusted-mutator" and profile == "mutator"
        session = _Session("host")
        host_sessions.append(session)
        return session

    def open_attempt(_root, *, principal, profile, attempt_id, providers=None):
        assert principal == "trusted-mutator" and profile == "mutator"
        assert attempt_id == "attempt-42"
        session = _Session("attempt")
        attempt_sessions.append(session)
        return session

    def forget_attempt(_root, *, principal, profile, attempt_id):
        forgotten_attempts.append((principal, profile, attempt_id))

    host = SimpleNamespace(profile="mutator", principal="trusted-mutator",
                           projects=frozenset({"workspace"}))
    config = ProjectControlConfig(workspaces={"workspace": WorkspaceConfig(
        authority_repository="source",
        repositories={"source": RepositoryConfig(root=repository)},
    )})
    workflow = NativeWorkflowHost(
        config, host, session_for_host=open_host, session_for_attempt=open_attempt,
        forget_attempt_bearer=forget_attempt,
        providers_for_project=lambda *_args, **_kwargs: None,
        binding_validator=lambda: None,
    )

    cases = {
        "todo_integrate_attempt": ({"attempt_id": "attempt-42", "idempotency_key": "key-integrate"},
                                    "host", "integrate_attempt", "attempt-42"),
        "todo_accept_outcome": ({"attempt_id": "attempt-42", "candidate_id": "candidate-1",
                                  "idempotency_key": "key-accept"}, "host", "accept", "attempt-42"),
        "todo_reject_outcome": ({"attempt_id": "attempt-42", "candidate_id": "candidate-1",
                                  "idempotency_key": "key-reject"}, "host", "reject", "attempt-42"),
        "todo_finish_attempt": ({"attempt_id": "attempt-42", "lease_id": "lease-1",
                                 "idempotency_key": "key-finish"}, "host", "finish", "attempt-42"),
        "todo_inspect_attempt": ({"attempt_id": "attempt-42"}, "host", "inspect_attempt", "attempt-42"),
        "todo_inspect_resource": ({"operation_id": "resource-op-1"}, "host", "inspect_resource",
                                   "resource-op-1"),
        "todo_reconcile_resource": ({"operation_id": "resource-op-1", "idempotency_key": "key-resource-reconcile"},
                                     "host", "reconcile_resource", "resource-op-1"),
        "todo_retry_resource": ({"operation_id": "resource-op-1", "idempotency_key": "key-resource-retry"},
                                 "host", "retry_resource", "resource-op-1"),
        "todo_release_resource": ({"operation_id": "resource-op-1", "idempotency_key": "key-resource-release"},
                                   "host", "release_resource", "resource-op-1"),
        "todo_verify_attempt": ({"attempt_id": "attempt-42", "requirement_id": "req-1",
                                  "idempotency_key": "key-verify"}, "attempt", "verify", "attempt-42"),
        "todo_report_outcome": ({"attempt_id": "attempt-42", "disposition": "completed",
                                 "idempotency_key": "key-report"}, "attempt", "report", "attempt-42"),
        "todo_heartbeat_attempt": ({"attempt_id": "attempt-42", "lease_id": "lease-1",
                                     "plan_revision_id": "plan-1", "idempotency_key": "key-heartbeat"},
                                    "attempt", "heartbeat_attempt", "attempt-42"),
        "todo_release_attempt": ({"attempt_id": "attempt-42", "lease_id": "lease-1",
                                   "idempotency_key": "key-release"}, "attempt", "release_attempt", "attempt-42"),
        "todo_reserve_resource": ({"attempt_id": "attempt-42", "resource_class": "cpu", "units": 1,
                                    "idempotency_key": "key-resource-reserve"},
                                   "attempt", "reserve_resource", "attempt-42"),
    }
    for tool, (arguments, expected_source, expected_method, expected_selector) in cases.items():
        result = workflow._attempt_operation(tool, "workspace", arguments, None)
        assert result["status"] == "ok", (tool, result)
        sessions = host_sessions if expected_source == "host" else attempt_sessions
        session = sessions[-1]
        assert session.source == expected_source
        assert session.calls[-1][0] == expected_method
        assert session.calls[-1][1] == expected_selector
        if tool in {"todo_finish_attempt", "todo_heartbeat_attempt", "todo_release_attempt"}:
            assert session.calls[-1][2] == "lease-1"
        assert session.closed
    assert forgotten_attempts == [("trusted-mutator", "mutator", "attempt-42")]

