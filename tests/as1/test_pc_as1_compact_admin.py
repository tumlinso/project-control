"""Focused contracts for the compact mutator adapter over native public APIs."""
import pytest

from project_control.as1_context import ContextHost
from project_control.as1_control import ControlService
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


class NativeHostDouble:
    def __init__(self, host):
        self.host = host
        self.revision = 7
        self.calls = []

    def invoke(self, operation, **arguments):
        self.calls.append((operation, arguments))
        if operation == "todo_read_authority":
            data = {"semantic_revision": self.revision}
        elif operation == "todo_preview_plan":
            data = {"valid": True, "preview_id": "native-preview", "package": arguments["package_json"]}
        elif operation == "todo_commit_plan":
            self.revision += 1
            data = {"status": "applied", "revision": self.revision, "preview_id": "native-preview"}
        elif operation == "todo_read_run":
            data = {"run_id": arguments["run_id"], "state": "active"}
        elif operation == "todo_retire_run":
            self.revision += 1
            data = {"status": "retired", "run_id": arguments["run_id"], "revision": self.revision}
        else:
            raise AssertionError(f"unexpected native call: {operation}")
        return {"status": "ok", "data": data}


@pytest.fixture
def control(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(authority_repository="source", repositories={
            "source": RepositoryConfig(root=source),
        }),
    })
    host = ContextHost("mutator", "compact-test", frozenset({"demo"}))
    service = ControlService(config, host)
    native = NativeHostDouble(host)
    service.workflow = native
    return service, native


def test_plan_apply_requires_exact_preview_and_replays_receipt(control):
    service, native = control
    package = {"schema_version": 1, "work_items": [{"id": "T1", "title": "one"}]}
    preview = service.plan("demo", "validate", native_plan=package)
    assert preview["status"] == "preview"
    assert preview["proposal"]["proposal_ref"]
    result = service.plan("demo", "apply", native_plan=package, proposal=preview["proposal"])
    assert result["status"] == "applied"
    assert [tool for tool, _args in native.calls].count("todo_commit_plan") == 1

    replay = service.plan("demo", "apply", native_plan=package, proposal=preview["proposal"])
    assert replay == result
    assert [tool for tool, _args in native.calls].count("todo_commit_plan") == 1


def test_apply_rejects_fabricated_or_stale_preview_without_native_commit(control):
    service, native = control
    package = {"schema_version": 1, "work_items": [{"id": "T1"}]}
    fabricated = service.plan("demo", "apply", native_plan=package,
                              proposal={"proposal_ref": "model-invented"})
    assert fabricated["status"] == "needs_preview"
    assert not any(tool == "todo_commit_plan" for tool, _args in native.calls)

    preview = service.plan("demo", "diff", native_plan=package)
    native.revision += 1
    stale = service.plan("demo", "apply", native_plan=package, proposal=preview["proposal"])
    assert stale["status"] == "needs_preview"
    assert stale["reason"] == "authority_revision_changed"
    assert not any(tool == "todo_commit_plan" for tool, _args in native.calls)


def test_run_retirement_requires_host_issued_fresh_request(control):
    service, native = control
    prepared = service.plan("demo", "supersede", intent={
        "run_id": "run-a", "successor_run_id": "run-b",
    })
    assert prepared["status"] == "prepared"
    typed = prepared["prepared_request"]
    assert typed["proposal_ref"] and typed["expected_revision"] == 7
    assert not any(tool == "todo_retire_run" for tool, _args in native.calls)

    result = service.plan("demo", "supersede", prepared_request=typed)
    assert result["status"] == "retired"
    retirement = next(args for tool, args in native.calls if tool == "todo_retire_run")
    assert retirement["run_id"] == "run-a" and retirement["successor_run_id"] == "run-b"
    assert retirement["expected_revision"] == 7 and retirement["idempotency_key"]


def test_mutator_surface_rejects_other_profiles(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(authority_repository="source", repositories={
            "source": RepositoryConfig(root=source),
        }),
    })
    observer = ControlService(config, ContextHost("observer", "observer", frozenset({"demo"})))
    with pytest.raises(PermissionError, match="mutator_profile_required"):
        observer.plan("demo", "validate", native_plan={"schema_version": 1})
    with pytest.raises(PermissionError, match="mutator_profile_required"):
        observer.maintain_execution({"project": "demo", "action": "diagnose", "attempt_id": "A"})
