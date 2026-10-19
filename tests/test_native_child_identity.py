from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.native_child_identity import NativeChildCredentialIssuer
from project_control.native_workflow import NativeWorkflowHost


def test_native_child_start_and_read_use_one_use_host_identity(tmp_path: Path):
    from todo_orchestrator.api import (
        DEFAULT_OWNER_ACTIONS, ProviderComposition, TodoApplication, read_project_metadata,
    )
    from todo_orchestrator.contracts import (
        Project, ProviderIdentity, ProviderReceipt, Sha256Digest, StableId,
    )
    from todo_orchestrator.model import (
        AttemptScopeSnapshot, CHILD_DELEGATE_ACTION, CHILD_READ_ACTION,
        QualifiedDestination, QualifiedSourceRevision, WorkspaceQualification,
    )
    from project_control.workflow_binding import store_child_bearer as binding_store_child_bearer

    root = tmp_path / "repo"
    root.mkdir()
    project = Project(StableId("child-frontdoor-project"), StableId("child-frontdoor-authority"))
    repository_id = StableId("source")
    host_identity = ProviderIdentity(StableId("child-frontdoor-host"), "1")

    class AttemptHost:
        identity = host_identity

        def qualify_workspace(self, workspace_id, _target, correlation_id):
            base = QualifiedSourceRevision(project.project_id, repository_id,
                                           StableId("child-frontdoor-base"), "a" * 64)
            receipt = ProviderReceipt(
                StableId("child-frontdoor-proof-" + str(correlation_id)[-12:]), self.identity,
                correlation_id, Sha256Digest("b" * 64), base.ref, datetime.now(timezone.utc),
            )
            return WorkspaceQualification(
                workspace_id, repository_id, base,
                QualifiedDestination(project.project_id, repository_id, StableId("main")), receipt,
            )

        def inspect_attempt(self, _attempt, _workspace):
            raise RuntimeError("fixture does not provide physical stop evidence")

    issuer = NativeChildCredentialIssuer()
    project_authenticator = issuer.for_project(str(project.project_id))
    providers = ProviderComposition(
        attempt_host=AttemptHost(), host_identity=host_identity,
        principal_authenticator=project_authenticator,
    )
    owner_actions = tuple(sorted({
        *DEFAULT_OWNER_ACTIONS, CHILD_DELEGATE_ACTION, CHILD_READ_ACTION,
    }))
    owner = TodoApplication.initialize(
        root, project_id=project.project_id, repository_id=repository_id,
        authority_id=project.authority_id,
        owner_credential="native child frontdoor test owner credential 604",
        owner_actions=owner_actions, providers=providers,
    )
    try:
        package = {
            "format": "todo-orchestrator.plan", "schema_version": 1,
            "package_id": "child-frontdoor-plan",
            "project": {"project_id": str(project.project_id),
                        "authority_id": str(project.authority_id)},
            "work_items": [{
                "id": "parent-work", "title": "Parent work", "objective": "Exercise child delegation",
                "outcome": "A bounded child can read its execution record",
                "scope": {"paths": [{"path": "src", "access": "write"}],
                          "concepts": [], "locks": []},
                "acceptance": {"assertions": ["child record is readable"],
                               "required_evidence": [], "allowed_dispositions": ["completed"]},
                "ownership": {"mode": "any", "actors": []},
            }],
        }
        package_json = json.dumps(package, sort_keys=True, separators=(",", ":"))
        preview = owner.preview_plan(package_json)
        committed = owner.commit_plan(package_json, preview,
                                      idempotency_key=StableId("child-frontdoor-commit"))
        plan_revision_id = StableId(committed.result["plan_revision_id"])
        claimed = owner.claim_attempt(
            "parent-work", "parent-workspace", plan_revision_id=plan_revision_id,
            idempotency_key=StableId("child-frontdoor-claim"),
        )
        parent_attempt_id = claimed.result["attempt_id"]

        host = SimpleNamespace(profile="coder", principal="trusted-codex-parent",
                               projects=frozenset({"workspace"}))
        child_identity: dict[str, str] = {}
        token_store = tmp_path / "session-tokens.json"
        provider_calls: list[dict[str, object]] = []

        def providers_for_project(_config, _workspace, project_root, *, principal, profile,
                                  repository, principal_authenticator):
            assert principal == host.principal and profile == "coder" and repository == "source"
            metadata = read_project_metadata(project_root)
            auth = principal_authenticator.for_project(str(metadata.project_id))
            selected = ProviderComposition(attempt_host=AttemptHost(), host_identity=host_identity,
                                           principal_authenticator=auth)
            provider_calls.append({"project_id": str(metadata.project_id), "auth": auth,
                                   "providers": selected})
            return selected

        def open_parent_session(project_root, *, principal, profile, attempt_id, providers):
            assert principal == host.principal and profile == "coder" and attempt_id == parent_attempt_id
            return TodoApplication.open(project_root, bearer_token=owner.bearer_token, providers=providers)

        def store_child_bearer(_root, *, principal, parent_attempt_id, execution_id,
                               child_principal, profile, bearer_token):
            assert principal == host.principal and parent_attempt_id == expected_parent_attempt_id
            assert execution_id == "child-frontdoor-execution" and profile == "coder"
            binding_store_child_bearer(
                _root, principal=principal, profile=profile, parent_attempt_id=parent_attempt_id,
                execution_id=execution_id, child_principal=child_principal, bearer_token=bearer_token,
                store_path=token_store,
            )
            child_identity["child_principal"] = child_principal

        expected_parent_attempt_id = parent_attempt_id
        workflow = NativeWorkflowHost(
            ProjectControlConfig(workspaces={"workspace": WorkspaceConfig(
                authority_repository="source",
                repositories={"source": RepositoryConfig(root=root)},
            )}), host, session_for_attempt=open_parent_session,
            store_child_bearer=store_child_bearer,
            providers_for_project=providers_for_project,
            child_identity_issuer=issuer,
            binding_validator=lambda: None,
        )
        public_start = workflow.invoke(
            "todo_start_child", project="workspace", parent_attempt_id=parent_attempt_id,
            execution_id="child-frontdoor-execution", logical_id="child-frontdoor-logical",
            scope={"paths": [["src/child", "write"]], "locks": []},
            actions=[str(CHILD_READ_ACTION)], idempotency_key="child-frontdoor-start",
        )

        assert public_start["status"] == "ok", public_start
        public_payload = public_start["data"]["payload"]
        assert public_payload["view"]["state"] == "active", public_payload
        assert public_payload["view"]["actor"]["entity_id"] == child_identity["child_principal"]
        stored = json.loads(token_store.read_text(encoding="utf-8"))
        child_token = stored["children"][0]["bearer_token"]
        assert child_token not in json.dumps(public_start)
        assert token_store.stat().st_mode & 0o077 == 0
        assert len(provider_calls) == 1

        with TodoApplication.open(root, bearer_token=child_token,
                                  providers=provider_calls[0]["providers"]) as child:
            read_view = child.read_child_execution("child-frontdoor-execution")
            assert read_view.execution_id == StableId("child-frontdoor-execution")
            assert str(read_view.actor.entity_id) == child_identity["child_principal"]
            assert set(read_view.actions) == {CHILD_READ_ACTION}
    finally:
        owner.close()


def test_native_child_credentials_are_project_bound_and_one_use():
    from todo_orchestrator.api import AuthenticatedPrincipal
    from todo_orchestrator.contracts import ActorKind
    from todo_orchestrator.model import AttemptScopeSnapshot

    issuer = NativeChildCredentialIssuer()
    credential = issuer.issue(
        project_id="project-one", principal="trusted-host", profile="coder",
        parent_attempt_id="attempt-one", execution_id="execution-one", logical_id="logical-one",
        scope=AttemptScopeSnapshot((("src/child", "read"),), ()),
        actions=("todo.child.read",), idempotency_key="child-start-one",
    )
    assert repr(credential) == "<opaque native child credential>"
    rejected = issuer.for_project("project-two").authenticate(credential)
    assert rejected is None
    assert issuer.for_project("project-one").authenticate(credential) is None

    second = issuer.issue(
        project_id="project-one", principal="trusted-host", profile="coder",
        parent_attempt_id="attempt-one", execution_id="execution-two", logical_id="logical-two",
        scope=AttemptScopeSnapshot((("src/child", "read"),), ()),
        actions=("todo.child.read",), idempotency_key="child-start-two",
    )
    authenticated = issuer.for_project("project-one").authenticate(second)
    assert isinstance(authenticated, AuthenticatedPrincipal)
    assert authenticated.kind is ActorKind.AUTOMATION
    assert authenticated.subject_id.startswith("pc-child-")
    assert issuer.for_project("project-one").authenticate(second) is None
