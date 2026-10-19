from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from project_control.native_providers import (
    AuthenticatedChildObserver,
    JournaledCommandExecutor,
    TrackedProcessObserver,
    _process_start,
    ensure_native_integration_ref,
    provision_native_project,
    providers_for_project,
    record_external_producer_completion,
)


class _HostRegistry:
    def __init__(self, rows):
        self.rows = rows

    def owners_for_attempt(self, project_root, attempt_id):
        return self.rows


class _GitStub:
    def __init__(self, workspace: Path, digest: str):
        self.config = SimpleNamespace(workspace_root=workspace.parent)
        self.workspace = workspace
        self.digest = digest

    def workspace_path_for(self, _workspace_id):
        return self.workspace

    def _workspace_branch(self, _workspace_id):
        return "refs/heads/todo-orchestrator/workspaces/fixture"

    def _assert_worktree(self, workspace, _branch):
        return None, "head", None

    def _path_state(self, _workspace, _head):
        return (), (), None

    def _source_digest(self, _files, _index):
        from todo_orchestrator.contracts import Sha256Digest

        return Sha256Digest(self.digest)


def _command_request(operation_id: str, marker: Path, *, workspace_id: str = "workspace-one"):
    from todo_orchestrator.contracts import EntityKind, EntityRef, Sha256Digest, StableId
    from todo_orchestrator.contracts.providers import CommandExecutionRequest, ProviderCallContext

    project = StableId("project-native-test")
    actor = EntityRef(project, EntityKind.ACTOR, StableId("actor"), incarnation_id=StableId("actor-v1"))
    capability = EntityRef(project, EntityKind.CAPABILITY, StableId("capability"))
    attempt = EntityRef(project, EntityKind.ATTEMPT, StableId("attempt-one"))
    context = ProviderCallContext(project, actor, capability, StableId("context"), StableId("scope"))
    return CommandExecutionRequest(
        context, attempt, StableId(operation_id), StableId("idempotency-" + operation_id),
        Sha256Digest("a" * 64), 1, StableId("tool-command-check"),
        (sys.executable, "-c", "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text((p.read_text() if p.exists() else '')+'x')",
         str(marker)),
        5000, StableId(workspace_id),
    )


class NativeProviderTests(unittest.TestCase):
    def test_process_observer_requires_registered_pid_and_matches_generation(self):
        from todo_orchestrator.model import ProcessLiveness

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = SimpleNamespace(ref=SimpleNamespace(entity_id="attempt-one"))
            unknown = TrackedProcessObserver(root, _HostRegistry([]))
            self.assertIs(unknown.inspect(attempt), ProcessLiveness.UNKNOWN)

            start, state = _process_start(os.getpid())
            self.assertIsNotNone(start)
            self.assertNotIn(state, {"Z", "X", "x"})
            live = TrackedProcessObserver(root, _HostRegistry([
                {"pid": os.getpid(), "process_start": start},
            ]))
            self.assertIs(live.inspect(attempt), ProcessLiveness.LIVE)

            reused_pid = TrackedProcessObserver(root, _HostRegistry([
                {"pid": os.getpid(), "process_start": str(start) + "-old"},
            ]))
            self.assertIs(reused_pid.inspect(attempt), ProcessLiveness.STOPPED)

    def test_child_observer_uses_authenticated_native_rows_and_fails_closed(self):
        from todo_orchestrator.model import ChildActivity

        attempt = SimpleNamespace(
            project_id="project-native-test",
            ref=SimpleNamespace(entity_id="attempt-one"),
        )

        class Session:
            def __init__(self, rows):
                self.rows = rows
                self.closed = False

            def read_model(self):
                return SimpleNamespace(project_id="project-native-test",
                                       sections={"child_executions": tuple(self.rows)})

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active_session = Session([{"parent_attempt_id": "attempt-one", "state": "active"}])
            observer = AuthenticatedChildObserver(
                root, principal="host-agent", profile="coder",
                open_session=lambda *_args, **_kwargs: active_session,
            )
            self.assertIs(observer.inspect(attempt), ChildActivity.ACTIVE)
            self.assertTrue(active_session.closed)

            stopped = AuthenticatedChildObserver(
                root, principal="host-agent", profile="coder",
                open_session=lambda *_args, **_kwargs: Session([
                    {"parent_attempt_id": "attempt-one", "state": "stopped"},
                ]),
            )
            self.assertIs(stopped.inspect(attempt), ChildActivity.NONE)

            denied = AuthenticatedChildObserver(
                root, principal="host-agent", profile="coder",
                open_session=lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError()),
            )
            self.assertIs(denied.inspect(attempt), ChildActivity.UNKNOWN)

    def test_command_executor_runs_once_and_inspection_does_not_replay(self):
        from todo_orchestrator.contracts import ProviderIdentity, StableId
        from todo_orchestrator.contracts.providers import (
            CommandExecutionInspection, CommandInspectionState, EffectInspectionRequest,
            ProviderStatus, provider_request_digest,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces" / "w-one"
            workspace.mkdir(parents=True)
            state_home = root / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)
            def restore_state_home():
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous
            self.addCleanup(restore_state_home)
            journal = state_home / "project-control" / "todo-orchestrator" / "project-native-test" / "commands"
            expected = "a" * 64
            executor = JournaledCommandExecutor(
                project_id=StableId("project-native-test"),
                identity=ProviderIdentity(StableId("project-control.command.local"), "1"),
                git_provider=_GitStub(workspace, expected), journal_root=journal,
            )
            marker = workspace / "marker.txt"
            request = _command_request("command-operation-one", marker)
            result = executor.execute(request)
            self.assertIs(result.status, ProviderStatus.OBSERVED_SUCCESS, result)
            self.assertEqual(result.value.exit_code, 0)
            self.assertEqual(marker.read_text(), "x")

            inspection_request = EffectInspectionRequest(
                request.context, request.operation_id, request.idempotency_key,
                provider_request_digest(request), request.attempt.entity_id,
            )
            inspected = executor.inspect(inspection_request)
            self.assertIs(inspected.status, ProviderStatus.OBSERVED_SUCCESS)
            self.assertIsInstance(inspected.value, CommandExecutionInspection)
            self.assertIs(inspected.value.state, CommandInspectionState.COMPLETED)
            duplicate = executor.execute(request)
            self.assertIs(duplicate.status, ProviderStatus.OBSERVED_SUCCESS)
            self.assertEqual(marker.read_text(), "x")

            # A completed receipt is not enough without the supervisor's
            # durable descendant-quiescence proof.
            record_path, _lock_path = executor._paths(request.operation_id)
            record = executor._read(record_path)
            record["tree_quiescent"] = False
            executor._write(record_path, record)
            active_group_inspection = executor.inspect(inspection_request)
            self.assertIs(active_group_inspection.status, ProviderStatus.UNKNOWN)

    def test_command_executor_waits_for_setsid_descendant_before_success(self):
        from dataclasses import replace
        from todo_orchestrator.contracts import ProviderIdentity, StableId
        from todo_orchestrator.contracts.providers import ProviderStatus

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces" / "w-one"
            workspace.mkdir(parents=True)
            state_home = root / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)
            def restore_state_home():
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous
            self.addCleanup(restore_state_home)
            journal = state_home / "project-control" / "todo-orchestrator" / "project-native-test" / "commands"
            executor = JournaledCommandExecutor(
                project_id=StableId("project-native-test"),
                identity=ProviderIdentity(StableId("project-control.command.local"), "1"),
                git_provider=_GitStub(workspace, "a" * 64), journal_root=journal,
            )
            workspace = root / "workspaces" / "w-one"
            ready = workspace / "detached-ready.txt"
            late = workspace / "detached-late.txt"
            private_report_access = workspace / "private-report-access.txt"
            child_code = (
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_text('ready'); "
                "time.sleep(0.25); pathlib.Path(sys.argv[2]).write_text('late')"
            )
            parent_code = """
import os, pathlib, subprocess, sys, time
access, ready, late, child_code = sys.argv[1:]
try:
    os.listdir('/proc/' + str(os.getppid()) + '/fd')
except PermissionError:
    pathlib.Path(access).write_text('blocked')
else:
    pathlib.Path(access).write_text('visible')
subprocess.Popen([sys.executable, '-c', child_code, ready, late],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
deadline = time.monotonic() + 3
while not pathlib.Path(ready).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not pathlib.Path(ready).exists():
    raise SystemExit(9)
"""
            request = _command_request("command-operation-setsid-child", root / "unused")
            request = replace(
                request,
                argv=(sys.executable, "-c", parent_code, str(private_report_access),
                      str(ready), str(late), child_code),
            )

            started = time.monotonic()
            result = executor.execute(request)
            elapsed = time.monotonic() - started
            self.assertIs(result.status, ProviderStatus.OBSERVED_SUCCESS, result)
            self.assertEqual(result.value.exit_code, 0)
            self.assertFalse(result.value.timed_out)
            self.assertGreaterEqual(elapsed, 0.2)
            self.assertEqual(late.read_text(), "late")
            self.assertEqual(private_report_access.read_text(), "blocked")

    def test_command_executor_timeout_kills_and_reaps_setsid_descendant(self):
        from dataclasses import replace
        from todo_orchestrator.contracts import ProviderIdentity, StableId
        from todo_orchestrator.contracts.providers import ProviderStatus

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces" / "w-one"
            workspace.mkdir(parents=True)
            state_home = root / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)

            def restore_state_home():
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous

            self.addCleanup(restore_state_home)
            journal = state_home / "project-control" / "todo-orchestrator" / "project-native-test" / "commands"
            executor = JournaledCommandExecutor(
                project_id=StableId("project-native-test"),
                identity=ProviderIdentity(StableId("project-control.command.local"), "1"),
                git_provider=_GitStub(workspace, "a" * 64), journal_root=journal,
            )
            ready = workspace / "timeout-ready.txt"
            late = workspace / "timeout-late.txt"
            child_code = (
                "import pathlib,sys,time; "
                "pathlib.Path(sys.argv[1]).write_text('ready'); "
                "time.sleep(0.6); pathlib.Path(sys.argv[2]).write_text('late')"
            )
            parent_code = """
import pathlib, subprocess, sys, time
ready, late, child_code = sys.argv[1:]
subprocess.Popen([sys.executable, '-c', child_code, ready, late],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
deadline = time.monotonic() + 3
while not pathlib.Path(ready).exists() and time.monotonic() < deadline:
    time.sleep(0.01)
if not pathlib.Path(ready).exists():
    raise SystemExit(9)
"""
            request = _command_request("command-operation-setsid-timeout", root / "unused")
            request = replace(
                request,
                argv=(sys.executable, "-c", parent_code, str(ready), str(late), child_code),
                timeout_ms=100,
            )

            result = executor.execute(request)
            self.assertIs(result.status, ProviderStatus.OBSERVED_SUCCESS, result)
            self.assertTrue(result.value.timed_out)
            self.assertIsNone(result.value.exit_code)
            time.sleep(0.7)
            self.assertFalse(late.exists())
            record_path, _lock_path = executor._paths(request.operation_id)
            record = executor._read(record_path)
            self.assertEqual(record["state"], "completed")
            self.assertTrue(record["tree_quiescent"])

    def test_command_sandbox_contains_host_authority_network_and_user_bus(self):
        from dataclasses import replace
        import hashlib
        import json
        import socket
        import sqlite3
        from todo_orchestrator.contracts import ProviderIdentity, StableId
        from todo_orchestrator.contracts.providers import ProviderStatus

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces" / "w-one"
            workspace.mkdir(parents=True)
            credential = root / "session-credential"
            credential.write_text("host-secret-do-not-read")
            database = root / "todo-state.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE authority (value TEXT NOT NULL)")
                connection.execute("INSERT INTO authority VALUES ('host-authority-original')")
            database_before = database.read_bytes()
            user_bus = root / "user-bus"
            bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            bus.bind(str(user_bus))
            self.addCleanup(bus.close)
            host_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            host_listener.bind(("127.0.0.1", 0))
            host_listener.listen(1)
            host_port = host_listener.getsockname()[1]
            self.addCleanup(host_listener.close)

            state_home = root / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)

            def restore_state_home():
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous

            self.addCleanup(restore_state_home)
            journal = state_home / "project-control" / "todo-orchestrator" / "project-native-test" / "commands"
            executor = JournaledCommandExecutor(
                project_id=StableId("project-native-test"),
                identity=ProviderIdentity(StableId("project-control.command.local"), "1"),
                git_provider=_GitStub(workspace, "a" * 64), journal_root=journal,
            )
            receipt = workspace / "isolation-result.json"
            host_pid = os.getpid()
            script = r"""
import json, pathlib, socket, sqlite3, sys
credential, database, bus, port, host_pid, result = sys.argv[1:]
try:
    pathlib.Path(credential).read_text()
    credential_visible = True
except OSError:
    credential_visible = False
try:
    with sqlite3.connect(database) as connection:
        row = connection.execute('SELECT value FROM authority').fetchone()
        connection.execute('DELETE FROM authority')
        connection.execute("INSERT INTO authority VALUES ('unauthorized-write')")
    authority_visible = row == ('host-authority-original',)
except sqlite3.Error:
    authority_visible = False
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS authority (value TEXT NOT NULL)')
        connection.execute("INSERT INTO authority VALUES ('unauthorized-write')")
bus_visible = pathlib.Path(bus).exists()
try:
    socket.create_connection(('127.0.0.1', int(port)), timeout=0.25).close()
    host_network_visible = True
except OSError:
    host_network_visible = False
host_proc_visible = pathlib.Path('/proc', host_pid, 'environ').exists()
pathlib.Path(result).write_text(json.dumps({
    'credential_visible': credential_visible,
    'authority_visible': authority_visible,
    'bus_visible': bus_visible,
    'host_network_visible': host_network_visible,
    'host_proc_visible': host_proc_visible,
}, sort_keys=True))
print('contained command output')
"""
            request = _command_request("command-operation-sandbox-containment", root / "unused")
            request = replace(
                request,
                argv=("python", "-c", script, str(credential), str(database),
                      str(user_bus), str(host_port), str(host_pid), str(receipt)),
            )

            with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent)
                                         + ":" + os.environ.get("PATH", "")}):
                result = executor.execute(request)
            self.assertIs(result.status, ProviderStatus.OBSERVED_SUCCESS, result)
            observed = json.loads(receipt.read_text())
            self.assertEqual(observed, {
                "authority_visible": False,
                "bus_visible": False,
                "credential_visible": False,
                "host_network_visible": False,
                "host_proc_visible": False,
            })
            self.assertEqual(database.read_bytes(), database_before)
            self.assertEqual(result.value.stdout_digest.hex,
                             hashlib.sha256(b"contained command output\n").hexdigest())

    def test_command_sandbox_refuses_unconfigured_host_executable(self):
        from dataclasses import replace
        from todo_orchestrator.contracts import ProviderIdentity, StableId
        from todo_orchestrator.contracts.providers import ProviderStatus

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspaces" / "w-one"
            workspace.mkdir(parents=True)
            credential_script = root / "credential-tool"
            marker = workspace / "must-not-be-created"
            credential_script.write_text(
                "#!/usr/bin/python3\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text(Path(__file__).read_text())\n"
                "# credential sentinel: host-only\n"
            )
            credential_script.chmod(0o700)
            state_home = root / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)

            def restore_state_home():
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous

            self.addCleanup(restore_state_home)
            executor = JournaledCommandExecutor(
                project_id=StableId("project-native-test"),
                identity=ProviderIdentity(StableId("project-control.command.local"), "1"),
                git_provider=_GitStub(workspace, "a" * 64),
                journal_root=state_home / "project-control" / "todo-orchestrator"
                / "project-native-test" / "commands",
            )
            request = _command_request("command-operation-untrusted-binary", root / "unused")
            request = replace(request, argv=(str(credential_script),))

            result = executor.execute(request)
            self.assertIs(result.status, ProviderStatus.UNAVAILABLE, result)
            self.assertEqual(str(result.reason_code), "command_executable_unavailable")
            self.assertFalse(marker.exists())
            self.assertIn("credential sentinel: host-only", credential_script.read_text())

    def test_provider_factory_pins_registered_authority_and_keeps_runtime_outside_repo(self):
        from todo_orchestrator.api import TodoApplication
        from todo_orchestrator.contracts import (
            EntityKind, EntityRef, ProviderCallContext, Sha256Digest, StableId,
        )
        from todo_orchestrator.contracts.providers import (
            GitEffectRequest, GitOperationKind, ProviderStatus,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(root)], check=True)
            (root / "README.md").write_text("fixture\n")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture",
            ], check=True)
            TodoApplication.initialize(
                root, project_id=StableId("project-native-factory"),
                repository_id=StableId("source"), owner_credential="fixture-owner-credential",
            ).close()
            config = SimpleNamespace(workspaces={
                "native": SimpleNamespace(
                    authority_repository="source",
                    repositories={"source": SimpleNamespace(root=root)},
                ),
            })
            state_home = Path(directory) / "state-home"
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)
            try:
                with self.assertRaisesRegex(Exception, "integration ref is uninitialized"):
                    providers_for_project(
                        config, "native", root, principal="project-control-host", profile="coder",
                    )
                self.assertFalse(state_home.exists())
                provisioning = provision_native_project(config, "native", root)
                integration_ref = StableId(provisioning["integration_ref"])
                state_root = Path(provisioning["state_root"])
                for private_directory in (
                    state_root, state_root / "workspaces", state_root / "git-journal",
                    state_root / "command-journal", state_root / "external-producer-attestations",
                ):
                    self.assertEqual(private_directory.stat().st_mode & 0o777, 0o700)
                branch_head = subprocess.run(
                    ["git", "-C", str(root), "show-ref", "--verify", "--hash", str(integration_ref)],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
                main_head = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
                self.assertEqual(branch_head, main_head)
                bound_authenticator = SimpleNamespace(
                    provider_id="fixture-native-authenticator",
                    authenticate=lambda *_args, **_kwargs: None,
                )
                bound_project_ids = []
                authenticator = SimpleNamespace(
                    for_project=lambda project_id: (bound_project_ids.append(project_id)
                                                     or bound_authenticator),
                )
                providers = providers_for_project(
                    config, "native", root, principal="project-control-host", profile="coder",
                    principal_authenticator=authenticator,
                )
                self.assertTrue(state_home.exists())
            finally:
                if previous is None:
                    os.environ.pop("XDG_STATE_HOME", None)
                else:
                    os.environ["XDG_STATE_HOME"] = previous

            git = providers.git_provider
            self.assertEqual(git.config.repository_root, root.resolve())
            self.assertEqual(git.config.excluded_paths, (".todo-orchestrator",))
            self.assertNotEqual(git.config.workspace_root, root)
            self.assertNotEqual(git.config.journal_root, root)
            self.assertEqual(git.config.workspace_root.parent, state_home / "project-control" / "todo-orchestrator" / "project-native-factory")
            self.assertIsNotNone(providers.command_execution_provider)
            self.assertIsNotNone(providers.verification_provider)
            self.assertIsNone(providers.resource_permit_provider)
            self.assertIs(providers.principal_authenticator, bound_authenticator)
            self.assertEqual(bound_project_ids, ["project-native-factory"])

            project_id = StableId("project-native-factory")
            actor = EntityRef(project_id, EntityKind.ACTOR, StableId("fixture-host"),
                              incarnation_id=StableId("fixture-host-v1"))
            capability = EntityRef(project_id, EntityKind.CAPABILITY, StableId("git"))
            context = ProviderCallContext(
                project_id, actor, capability, StableId("fixture-context"), StableId("fixture-scope"),
            )
            revision = git.qualified_revision()
            workspace_id = StableId("fresh-provisioned-workspace")
            request = GitEffectRequest(
                context, StableId("fresh-provisioned-create"), StableId("fresh-provisioned-idem"),
                GitOperationKind.CREATE_WORKSPACE, Sha256Digest(revision.digest), 1,
                workspace_id, git.config.base_ref,
            )
            created = git.perform(request)
            self.assertIs(created.status, ProviderStatus.OBSERVED_SUCCESS, created)
            self.assertTrue(git.workspace_path_for(workspace_id).is_dir())
            with patch.dict(os.environ, {"XDG_STATE_HOME": str(state_home)}):
                reopened = providers_for_project(
                    config, "native", root, principal="project-control-host", profile="coder",
                )
            reopened_workspace = reopened.git_provider.workspace_path_for(workspace_id)
            self.assertTrue(reopened_workspace.is_dir())
            reopened_branch = reopened.git_provider._workspace_branch(workspace_id)
            reopened.git_provider._assert_worktree(reopened_workspace, reopened_branch)

    def test_root_attestation_is_typed_bounded_and_not_kernel_stop(self):
        from datetime import datetime, timedelta, timezone
        from todo_orchestrator.contracts import (
            AttemptState, EntityKind, EntityRef, ProviderIdentity, ProviderReceipt,
            Sha256Digest, StableId,
        )
        from todo_orchestrator.model import (
            AttemptSafetyObservation, ChildActivity, ProcessLiveness, QualifiedDestination,
            QualifiedSourceRevision, SourceSafety, WorkspaceIsolation, WorkspaceQualification,
        )
        from project_control.native_providers import _attestation_path, _read_attestation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            state_home = Path(directory) / "state-home"
            state_home.mkdir()
            previous = os.environ.get("XDG_STATE_HOME")
            os.environ["XDG_STATE_HOME"] = str(state_home)
            self.addCleanup(lambda: os.environ.__setitem__("XDG_STATE_HOME", previous)
                            if previous is not None else os.environ.pop("XDG_STATE_HOME", None))

            project_id = StableId("attestation-project")
            attempt_id = StableId("attestation-attempt")
            workspace_id = StableId("attestation-workspace")
            repo_id = StableId("source")
            actor = EntityRef(project_id, EntityKind.ACTOR, StableId("codex-agent"),
                              incarnation_id=StableId("codex-agent-v1"))
            target = EntityRef(project_id, EntityKind.WORK_ITEM, StableId("work-item"),
                               definition_revision_id=StableId("work-rev-1"))
            attempt_ref = EntityRef(project_id, EntityKind.ATTEMPT, attempt_id)
            expires = datetime.now(timezone.utc) + timedelta(hours=1)
            base = QualifiedSourceRevision(project_id, repo_id, StableId("base-commit"), "a" * 64)
            destination = QualifiedDestination(project_id, repo_id, StableId("integration"))
            provider_identity = ProviderIdentity(StableId("project-control.git.local"), "1")
            workspace_receipt = ProviderReceipt(
                StableId("workspace-receipt"), provider_identity, attempt_id,
                Sha256Digest("b" * 64), base.ref, datetime.now(timezone.utc),
            )
            isolation = WorkspaceIsolation(
                Sha256Digest("c" * 64), Sha256Digest("d" * 64), Sha256Digest("e" * 64),
                StableId("refs/heads/todo-orchestrator/workspaces/test"), Sha256Digest("f" * 64),
            )
            qualification = WorkspaceQualification(
                workspace_id, repo_id, base, destination, workspace_receipt, isolation,
            )
            host_receipt = ProviderReceipt(
                StableId("safety-receipt"), provider_identity, attempt_id,
                Sha256Digest("1" * 64), base.ref, datetime.now(timezone.utc),
            )
            observation = AttemptSafetyObservation(
                attempt_ref, AttemptState.ACTIVE, StableId("lease-1"), expires, qualification,
                SourceSafety.DIRTY, ProcessLiveness.UNKNOWN, ChildActivity.NONE,
                host_receipt, Sha256Digest("2" * 64),
            )
            attempt_row = {
                "attempt_id": str(attempt_id), "actor_id": str(actor.entity_id),
                "actor_incarnation_id": str(actor.incarnation_id),
                "work_item_id": str(target.entity_id),
                "definition_revision_id": str(target.definition_revision_id),
                "state": AttemptState.ACTIVE.value, "lease_id": "lease-1",
                "lease_expires_at": expires.isoformat(timespec="microseconds"),
            }
            model = SimpleNamespace(project_id=project_id, sections={"attempts": (attempt_row,)})
            inspection = SimpleNamespace(
                attempt=attempt_ref, lease_id=StableId("lease-1"), lease_expires_at=expires,
                workspace_id=workspace_id, host_observation=observation,
            )

            class Session:
                def inspect_attempt(self, _attempt):
                    return inspection

                def read_model(self):
                    return model

                def close(self):
                    pass

            host_registry = _HostRegistry([])
            tracked = TrackedProcessObserver(root, host_registry)
            fake_git = SimpleNamespace(
                config=SimpleNamespace(project_id=project_id),
                process_observer=SimpleNamespace(tracked=tracked),
            )
            composition = SimpleNamespace(git_provider=fake_git)
            with patch("project_control.native_providers.providers_for_project", return_value=composition), \
                    patch("project_control.workflow_binding.open_session_for_host", return_value=Session()):
                receipt = record_external_producer_completion(
                    SimpleNamespace(), "fixture", root, principal="host-root", profile="coder",
                    attempt_id=str(attempt_id), producer_id="codex-subagent-17",
                    lifecycle_state="completed",
                )

            path = _attestation_path(
                state_home / "project-control" / "todo-orchestrator" / str(project_id)
                / "external-producer-attestations", attempt_id,
            )
            record = _read_attestation(path)
            self.assertEqual(receipt["attestation_id"], record["attestation_id"])
            self.assertEqual(record["lease_id"], "lease-1")
            self.assertEqual(record["source_safety"], SourceSafety.DIRTY.value)
            self.assertEqual(record["source_digest"], "2" * 64)
            self.assertEqual(record["lifecycle_source"], "configured_codex_collaboration")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            from todo_orchestrator.model import ProcessLiveness
            fake_git.qualify_workspace = lambda *_args: qualification
            fake_git.workspace_path_for = lambda *_args: root
            fake_git._workspace_branch = lambda *_args: isolation.head_ref
            fake_git._assert_worktree = lambda *_args: (None, "head", None)
            live_safety = [SourceSafety.DIRTY]
            fake_git._path_state = lambda *_args: ((), (), live_safety[0])
            fake_git._source_digest = lambda *_args: Sha256Digest("2" * 64)
            process_observer = __import__(
                "project_control.native_providers", fromlist=["ExternalProducerProcessObserver"],
            ).ExternalProducerProcessObserver(
                project_root=root, host_resources=host_registry,
                command_journal_root=state_home / "project-control" / "todo-orchestrator"
                / str(project_id) / "command-journal",
                attestation_root=path.parent, git_provider=fake_git,
            )
            attempt = SimpleNamespace(
                project_id=project_id, ref=attempt_ref, actor=actor, work_item=target,
                lease=SimpleNamespace(lease_id=StableId("lease-1"), expires_at=expires),
            )
            self.assertIs(process_observer.inspect(attempt), ProcessLiveness.EXTERNAL_PRODUCER_QUIESCENT)
            attempt.lease = SimpleNamespace(lease_id=StableId("rotated-lease"), expires_at=expires)
            self.assertIs(process_observer.inspect(attempt), ProcessLiveness.UNKNOWN)
            attempt.lease = SimpleNamespace(lease_id=StableId("lease-1"), expires_at=expires)
            live_safety[0] = SourceSafety.CLEAN
            self.assertIs(process_observer.inspect(attempt), ProcessLiveness.UNKNOWN)
            live_safety[0] = SourceSafety.DIRTY
            fake_git._source_digest = lambda *_args: Sha256Digest("3" * 64)
            self.assertIs(process_observer.inspect(attempt), ProcessLiveness.UNKNOWN)
            fake_git._source_digest = lambda *_args: Sha256Digest("2" * 64)
            start, _state = _process_start(os.getpid())
            host_registry.rows = [{"pid": os.getpid(), "process_start": start}]
            self.assertIs(process_observer.inspect(attempt), ProcessLiveness.LIVE)


if __name__ == "__main__":
    unittest.main()
