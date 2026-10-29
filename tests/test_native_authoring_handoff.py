from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from project_control.native_authoring_handoff import NativeAttemptCredentialProvider
from project_control.native_providers import NativeProviderUnavailable


def _credential_request(*, intent_digest: str = "a" * 64, idempotency_key: str = "dispatch-1"):
    return SimpleNamespace(
        project_id="project-1",
        authority_id="authority-1",
        attempt_id="attempt-1",
        capability_id="capability-1",
        idempotency_key=idempotency_key,
        intent_digest=intent_digest,
    )


def test_attempt_credential_is_durable_and_idempotent_across_provider_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    state_home = tmp_path / "state"
    state_home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    record_root = state_home / "project-control" / "todo-orchestrator" / "project-1" / "attempt-credentials"
    request = _credential_request()

    token = NativeAttemptCredentialProvider("project-1", "authority-1", record_root).prepare_attempt_credential(request)
    replay = NativeAttemptCredentialProvider("project-1", "authority-1", record_root).prepare_attempt_credential(request)

    assert token == replay
    record_files = tuple(record_root.glob("*.json"))
    assert len(record_files) == 1
    assert record_root.stat().st_mode & 0o777 == 0o700
    assert record_files[0].stat().st_mode & 0o777 == 0o600
    record = json.loads(record_files[0].read_text(encoding="utf-8"))
    assert record["bearer_token"] == token
    assert record["intent_digest"] == "a" * 64


@pytest.mark.parametrize(
    "conflicting_request",
    [
        _credential_request(intent_digest="b" * 64),
        _credential_request(idempotency_key="dispatch-2"),
    ],
)
def test_attempt_credential_refuses_conflicting_identity_or_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflicting_request: SimpleNamespace,
):
    state_home = tmp_path / "state"
    state_home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    record_root = state_home / "project-control" / "todo-orchestrator" / "project-1" / "attempt-credentials"
    provider = NativeAttemptCredentialProvider("project-1", "authority-1", record_root)
    provider.prepare_attempt_credential(_credential_request())

    with pytest.raises(NativeProviderUnavailable):
        provider.prepare_attempt_credential(conflicting_request)


def test_attempt_credential_rejects_other_project_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_home = tmp_path / "state"
    state_home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    record_root = state_home / "project-control" / "todo-orchestrator" / "project-1" / "attempt-credentials"

    with pytest.raises(NativeProviderUnavailable):
        NativeAttemptCredentialProvider("project-1", "authority-1", record_root).prepare_attempt_credential(
            SimpleNamespace(**{**vars(_credential_request()), "authority_id": "authority-2"})
        )


def test_authoring_handoff_pins_current_clean_source_and_fences_later_host_git_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from dataclasses import replace
    from todo_orchestrator.api import TodoApplication, read_project_metadata
    from todo_orchestrator.contracts import EntityKind, EntityRef, Sha256Digest, StableId
    from todo_orchestrator.contracts.providers import (
        GitEffectRequest, GitOperationKind, ProviderCallContext, ProviderStatus,
    )
    from todo_orchestrator.model import ChildActivity
    from todo_orchestrator.model.attempts import AttemptAuthoringHandoffRequest
    from project_control.native_providers import provision_native_project, providers_for_project

    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *args], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(repository)], check=True)
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    git("add", "README.md")
    subprocess.run([
        "git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture",
    ], check=True)

    project_id = StableId("handoff-project")
    TodoApplication.initialize(
        repository, project_id=project_id, repository_id=StableId("source"),
        owner_credential="fixture-owner-credential",
    ).close()
    authority_id = read_project_metadata(repository).authority_id
    config = SimpleNamespace(workspaces={
        "native": SimpleNamespace(
            authority_repository="source",
            repositories={"source": SimpleNamespace(root=repository)},
        ),
    })
    state_home = tmp_path / "state-home"
    state_home.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    provision_native_project(config, "native", repository)
    composition = providers_for_project(
        config, "native", repository, principal="project-control-host", profile="coder",
        host_resources=SimpleNamespace(owners_for_attempt=lambda *_args: []),
    )
    git_provider = composition.git_provider
    git_provider.child_observer = SimpleNamespace(inspect=lambda _attempt: ChildActivity.NONE)
    attempt_ref = EntityRef(project_id, EntityKind.ATTEMPT, StableId("attempt-1"))
    lease_id = StableId("lease-1")
    workspace_id = StableId("workspace-1")
    context = ProviderCallContext(
        project_id,
        EntityRef(project_id, EntityKind.ACTOR, StableId("host"), incarnation_id=StableId("host-v1")),
        EntityRef(project_id, EntityKind.CAPABILITY, StableId("git-capability")),
        StableId("context"), StableId("scope"),
    )
    revision = git_provider.qualified_revision()
    create_request = GitEffectRequest(
        context, StableId("create-workspace"), StableId("create-workspace-key"),
        GitOperationKind.CREATE_WORKSPACE, Sha256Digest(revision.digest), 1,
        workspace_id, git_provider.config.base_ref,
    )
    created = git_provider.perform(create_request)
    assert created.status is ProviderStatus.OBSERVED_SUCCESS, created
    workspace = git_provider.workspace_path_for(workspace_id)
    (workspace / "source.txt").write_text("qualified handback source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "source.txt"], check=True)
    subprocess.run([
        "git", "-C", str(workspace), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "source",
    ], check=True)

    request = AttemptAuthoringHandoffRequest(
        project_id, authority_id, attempt_ref, lease_id,
        workspace_id, StableId("handback-key"),
    )
    proof = git_provider.handoff_authoring(request)
    assert git_provider.handoff_authoring(request) == proof
    assert proof.request == request
    assert proof.source_commit == StableId(git_provider._git(["rev-parse", "HEAD"], cwd=git_provider.workspace_path_for(workspace_id)).decode().strip())
    assert proof.source_tree == StableId(git_provider._git(["rev-parse", "HEAD^{tree}"], cwd=git_provider.workspace_path_for(workspace_id)).decode().strip())
    assert proof.receipt.correlation_id == attempt_ref.entity_id
    assert proof.receipt.provenance_ref == proof.source_commit

    live_attempt = SimpleNamespace(ref=attempt_ref, lease=SimpleNamespace(lease_id=lease_id))
    assert git_provider.authoring_handoff_for(live_attempt) == proof
    from todo_orchestrator.contracts import AttemptState
    accepted_attempt = SimpleNamespace(ref=attempt_ref, lease=None, state=AttemptState.ACCEPTED)
    assert git_provider.authoring_handoff_for(accepted_attempt) == proof
    for state in (AttemptState.RELEASED, AttemptState.REJECTED, AttemptState.ADMITTED):
        assert git_provider.authoring_handoff_for(SimpleNamespace(ref=attempt_ref, lease=None, state=state)) is None
    handoff_provider = git_provider.handoff_authoring.__self__
    handoff_provider.host_resources.owners_for_attempt = lambda *_args: [{"state": "held"}]
    assert git_provider.authoring_handoff_for(accepted_attempt) is None
    handoff_provider.host_resources.owners_for_attempt = lambda *_args: []
    git_provider.child_observer = SimpleNamespace(inspect=lambda _attempt: ChildActivity.UNKNOWN)
    assert git_provider.authoring_handoff_for(accepted_attempt) is None
    git_provider.child_observer = SimpleNamespace(inspect=lambda _attempt: ChildActivity.NONE)
    assert git_provider.authoring_handoff_for(accepted_attempt) == proof
    assert git_provider.has_authoring_handoff(attempt_ref.entity_id)

    source_revision = git_provider.qualified_revision(proof.source_commit)
    integration = GitEffectRequest(
        context, StableId("integrate-pinned-source"), StableId("integrate-pinned-source-key"),
        GitOperationKind.INTEGRATE, Sha256Digest(source_revision.digest), 1,
        workspace_id, source_commit=proof.source_commit,
        destination_ref=git_provider.config.integration_ref,
        expected_destination=StableId(git_provider._resolve_ref(git_provider.config.integration_ref)),
        attempt=attempt_ref,
    )
    integrated = git_provider.perform(integration)
    assert integrated.status is ProviderStatus.OBSERVED_SUCCESS, integrated

    blocked = git_provider.perform(replace(
        create_request, operation_id=StableId("post-handoff-effect"),
        idempotency_key=StableId("post-handoff-effect-key"), attempt=attempt_ref,
    ))
    assert blocked.status is ProviderStatus.UNAVAILABLE
    assert str(blocked.reason_code) == "attempt_authoring_handed_off"

    (workspace / "later.txt").write_text("changed after the handoff\n", encoding="utf-8")
    assert git_provider.authoring_handoff_for(live_attempt) is None
    assert git_provider.authoring_handoff_for(accepted_attempt) is None
    assert git_provider.has_authoring_handoff(attempt_ref.entity_id)

