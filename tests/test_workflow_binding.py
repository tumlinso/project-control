from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from project_control import workflow_binding as binding


class HostSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.store = base / "config" / "session-tokens.json"
        self.store.parent.mkdir(mode=0o700)
        self.metadata = SimpleNamespace(project_id="p-1", authority_id="a-1")
        self.patch_metadata = patch.object(binding, "_project_metadata", return_value=self.metadata)
        self.patch_metadata.start()
        self.addCleanup(self.patch_metadata.stop)

    def entry(self, **overrides):
        result = {"project_id": "p-1", "authority_id": "a-1", "principal_id": "agent-1",
                  "profile": "observer", "capability_id": "cap-1",
                  "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                  "bearer_token": "private-token"}
        result.update(overrides)
        return result

    def test_workflow_binding_uses_keyword_only_native_session_open(self):
        from todo_orchestrator.api import TodoApplication

        opened = SimpleNamespace(marker="native-session")

        def native_open(project_root, *, bearer_token, providers):
            self.assertEqual(project_root, self.root)
            self.assertEqual(bearer_token, "host-token")
            self.assertIsNotNone(providers)
            return opened

        workflow_binding = binding.WorkflowBinding(identity=SimpleNamespace())
        with patch.object(binding, "validate_runtime"), patch.object(
            TodoApplication, "open", side_effect=native_open
        ):
            self.assertIs(workflow_binding.open_session(self.root, "host-token"), opened)

    def write(self, sessions):
        os.chmod(self.store.parent, 0o700)
        binding._write_store(self.store, {"format": binding.TOKEN_STORE_FORMAT,
                                         "version": binding.TOKEN_STORE_VERSION,
                                         "sessions": sessions, "attempts": [], "children": []})

    def test_resolves_only_exact_project_principal_and_profile(self):
        self.write([self.entry(), self.entry(project_id="p-2"), self.entry(profile="coder")])
        self.assertEqual(binding.resolve_session_token(self.root, principal="agent-1", profile="observer",
                                                       store_path=self.store), "private-token")

    def test_readonly_session_attempt_and_child_resolution_need_no_lockfile(self):
        protected_home = Path(self.temp.name) / "readonly-home"
        protected_store = protected_home / ".config" / "project-control" / "todo-orchestrator" / "session-tokens.json"
        protected_store.parent.mkdir(mode=0o700, parents=True)
        binding._write_store(protected_store, {"format": binding.TOKEN_STORE_FORMAT,
                                               "version": binding.TOKEN_STORE_VERSION,
                                               "sessions": [self.entry()], "attempts": [], "children": []})
        raw = json.loads(protected_store.read_text(encoding="utf-8"))
        raw["attempts"].append({"project_id": "p-1", "authority_id": "a-1", "principal_id": "agent-1",
                                "profile": "coder", "attempt_id": "attempt-1", "bearer_token": "attempt-token"})
        raw["children"].append({"project_id": "p-1", "authority_id": "a-1", "principal_id": "agent-1",
                                "profile": "coder", "parent_attempt_id": "attempt-1", "execution_id": "exec-1",
                                "child_principal_id": "child-1", "bearer_token": "child-token"})
        binding._write_store(protected_store, raw)
        protected_store.chmod(0o400)
        protected_store.parent.chmod(0o500)
        self.addCleanup(lambda: protected_store.parent.chmod(0o700))

        class SessionBinding:
            def __init__(self):
                self.tokens = []

            def open_session(self, _root, token, _providers):
                self.tokens.append(token)
                return SimpleNamespace(metadata=self.metadata)

        session_binding = SessionBinding()
        session_binding.metadata = self.metadata
        with (patch.object(binding, "initialize_workflow_binding", return_value=session_binding),
              patch.object(binding, "TOKEN_STORE_PATH", protected_store),
              patch.object(binding.Path, "home", return_value=protected_home)):
            self.assertEqual(binding.resolve_session_token(
                self.root, principal="agent-1", profile="observer",
            ), "private-token")
            binding.open_attempt_session(
                self.root, principal="agent-1", attempt_id="attempt-1",
            )
            binding.open_child_session(
                self.root, principal="agent-1", parent_attempt_id="attempt-1", execution_id="exec-1",
                child_principal="child-1",
            )
        self.assertEqual(session_binding.tokens, ["attempt-token", "child-token"])
        self.assertFalse(protected_store.with_suffix(protected_store.suffix + ".lock").exists())

    def test_read_resolution_does_not_create_missing_private_directory(self):
        missing = Path(self.temp.name) / "missing" / "session-tokens.json"
        with self.assertRaises(binding.HostSessionUnavailable):
            binding.resolve_session_token(self.root, principal="agent-1", profile="observer", store_path=missing)
        self.assertFalse(missing.parent.exists())

    def test_missing_expired_duplicate_or_authority_mismatch_fails_closed(self):
        for entries in (
            [],
            [self.entry(expires_at="2000-01-01T00:00:00+00:00")],
            [self.entry(), self.entry()],
            [self.entry(authority_id="different")],
        ):
            self.write(entries)
            with self.assertRaises(binding.HostSessionUnavailable):
                binding.resolve_session_token(self.root, principal="agent-1", profile="observer",
                                              store_path=self.store)

    def test_symlink_store_and_permissive_store_are_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.write_text("{}", encoding="utf-8")
        self.store.symlink_to(outside)
        with self.assertRaises(binding.HostSessionUnavailable):
            binding.resolve_session_token(self.root, principal="agent-1", profile="observer",
                                          store_path=self.store)
        self.store.unlink()
        self.write([self.entry()])
        self.store.chmod(0o644)
        with self.assertRaises(binding.HostSessionUnavailable):
            binding.resolve_session_token(self.root, principal="agent-1", profile="observer",
                                          store_path=self.store)

    def test_child_bearer_is_isolated_by_parent_execution_and_child(self):
        binding.store_child_bearer(self.root, principal="host-1", parent_attempt_id="parent-1",
                                   execution_id="exec-1", child_principal="child-1",
                                   bearer_token="child-secret", store_path=self.store)
        raw = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["children"]), 1)
        entry = raw["children"][0]
        self.assertEqual(entry["child_principal_id"], "child-1")
        with self.assertRaises(binding.HostSessionUnavailable):
            binding.open_child_session(self.root, principal="host-1", parent_attempt_id="other-parent",
                                       execution_id="exec-1", child_principal="child-1",
                                       store_path=self.store)
        binding.forget_child_bearer(self.root, principal="host-1", parent_attempt_id="parent-1",
                                    execution_id="exec-1", child_principal="child-1",
                                    store_path=self.store)
        self.assertEqual(json.loads(self.store.read_text(encoding="utf-8"))["children"], [])


if __name__ == "__main__":
    unittest.main()
