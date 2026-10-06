"""Persistent notebook context remains advisory and source-bound in real services."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_trace import TraceService
from project_control.as1_contracts import SourceLocator, canonical_digest
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.knowledge import NotebookProvider
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


class NotebookInformationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.old_cache_home = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = str(self.base / "cache")
        self.root = self.base / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-b", "main")
        _git(self.root, "config", "user.name", "Fixture")
        _git(self.root, "config", "user.email", "fixture@example.invalid")
        (self.root / "README.md").write_text("Fixture project purpose.\n", encoding="utf-8")
        (self.root / "module.py").write_text("def calculate(value):\n    return value + 1\n", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=never-read\n", encoding="utf-8")
        (self.root / "secret-link").symlink_to(self.root / "README.md")
        _git(self.root, "add", "README.md", "module.py")
        _git(self.root, "commit", "-m", "fixture")
        self.head = _git(self.root, "rev-parse", "HEAD")
        self.config = ProjectControlConfig(workspaces={
            "demo": WorkspaceConfig(authority_repository="source", repositories={
                "source": RepositoryConfig(root=self.root)})})
        self.snapshot = ProjectSnapshot(workspace_id="demo", project_uuid="fixture-demo",
            observed_at="2026-10-06T00:00:00Z", todo_revision=17,
            repositories={"source": RepositoryIdentity(commit=self.head, dirty=False)},
            todo_tables={"tasks": [], "context_fragments": []})
        self.store = SQLitePacketStore(self.base / "state")
        self.scope = {"principal": "alice", "profile": "observer", "project": "demo"}
        self.host = ContextHost("observer", "alice", frozenset({"demo"}))
        self.service = InformationService(self.config, self.store,
            lambda project: self.snapshot, self.host)

    def tearDown(self):
        if self.old_cache_home is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = self.old_cache_home
        self.temporary.cleanup()

    def add_source_note(self, note_id: str, path="README.md", claim="bounded fixture notebook insight"):
        raw = (self.root / path).read_bytes()
        locator = SourceLocator(project="demo", repository="source", path=path,
                                content_sha256=hashlib.sha256(raw).hexdigest())
        evidence = self.store.create(tool="read", payload={"status": "ok", "data": {"read": True}},
            sources=[locator], access_scope=self.scope)
        record = {"note_id": note_id, "project": "demo", "kind": "decision",
            "claim": claim, "reason_matters": "Preserve the original project context.",
            "sources": [locator.model_dump(mode="json")],
            "evidence_packets": [evidence.packet_id], "dependencies": {},
            "coverage": {"complete": True}, "uncertainty": [], "provenance": "source"}
        self.store.put_note(record, access_scope=self.scope)
        return evidence, locator

    def test_search_and_evidence_return_shared_packet_store_notes_as_advisory(self):
        evidence, locator = self.add_source_note("source-note")
        search = self.service.call("search", project="demo", query="bounded fixture notebook insight", detail="extended")
        prepared = search["data"]["prepared_context"]
        self.assertFalse(prepared["authoritative"])
        self.assertFalse(prepared["mutation_authority"])
        self.assertTrue(prepared["advisory_instruction"])
        self.assertEqual(prepared["notes"][0]["note_id"], "source-note")
        self.assertEqual(prepared["notes"][0]["provenance"], "source")
        self.assertEqual(prepared["notes"][0]["sources"][0]["content_sha256"], locator.content_sha256)
        self.assertFalse(prepared["notes"][0]["independent_proof"])
        packet = self.store.resolve(search["packet"], access_scope=self.scope)
        self.assertIn(locator, packet.sources)
        self.assertEqual(self.store.lookup(evidence.packet_id, access_scope=self.scope).status, "ok")
        recycled = {"note_id": "derived-again", "project": "demo", "kind": "decision",
            "claim": "bounded fixture notebook insight", "reason_matters": "Must remain independent.",
            "sources": [locator.model_dump(mode="json")], "evidence_packets": [search["packet"]],
            "dependencies": {}, "coverage": {"complete": True}, "uncertainty": [],
            "provenance": "source"}
        with self.assertRaisesRegex(ValueError, "prepared knowledge context cannot corroborate"):
            self.store.put_note(recycled, access_scope=self.scope)

        result = self.service.call("evidence", project="demo", subject="bounded fixture notebook insight",
                                   kinds=["context"], detail="extended")
        self.assertEqual(result["data"]["prepared_context"]["notes"][0]["note_id"], "source-note")

    def test_material_file_change_marks_note_stale_and_deny_or_symlink_is_not_bypassed(self):
        self.add_source_note("changed-note")
        (self.root / "README.md").write_text("The material source changed.\n", encoding="utf-8")
        result = self.service.call("search", project="demo", query="bounded fixture notebook insight", detail="extended")
        self.assertEqual(result["status"], "partial")
        note = result["data"]["prepared_context"]["notes"][0]
        self.assertEqual(note["freshness"], "stale")
        self.assertIn("changed", {item["reason"] for item in note["freshness_omissions"]})

        for note_id, path, expected in (
            ("denied-note", ".env", "source_path_denied"),
            ("symlink-note", "secret-link", "dependency_unavailable:OSError"),
        ):
            self.add_source_note(note_id, path=path)
        result = self.service.call("search", project="demo", query="bounded fixture notebook insight", detail="extended")
        returned = {note["note_id"]: note for note in result["data"]["prepared_context"]["notes"]}
        for note_id, _, expected in (
            ("denied-note", ".env", "source_path_denied"),
            ("symlink-note", "secret-link", "dependency_unavailable:OSError"),
        ):
            reasons = {item["reason"] for item in returned[note_id]["freshness_omissions"]}
            self.assertIn(expected, reasons)

    def test_user_goals_remain_distinct_and_scoped_to_the_trusted_project(self):
        goal = {"note_id": "goal-1", "project": "demo", "kind": "goal",
            "claim": "Keep preparation concise", "reason_matters": "Useful future work.",
            "provenance": "user", "coverage": {}, "uncertainty": []}
        self.store.put_user_goal(goal, access_scope=self.scope)
        provider = NotebookProvider(self.store,
            resolve_dependency=self.service._resolve_notebook_dependency,
            trusted_projects=frozenset({"demo"}))
        allowed = provider.retrieve("unrelated query", project="demo", access_scope=self.scope)
        self.assertEqual(allowed["goals"][0]["note_id"], "goal-1")
        self.assertEqual(allowed["goals"][0]["provenance"], "user")
        self.assertFalse(allowed["authoritative"])
        denied = provider.retrieve("unrelated query", project="foreign", access_scope={
            **self.scope, "project": "foreign"})
        self.assertEqual(denied["omissions"][0]["reason"], "forbidden_project")

    def test_typed_exact_routing_and_existing_request_identity_are_unchanged(self):
        service = InformationService(self.config, self.store, lambda project: self.snapshot,
            self.host, job_lookup=lambda ident, scope: {
                "resolution": "resolved", "job_id": ident, "status": "completed"})
        exact = service.call("search", project="demo",
            query={"kind": "investigation", "target": "inv-1"})
        self.assertEqual(exact["data"]["job_id"], "inv-1")
        self.assertNotIn("prepared_context", exact["data"])
        packet = self.store.resolve(exact["packet"], access_scope=self.scope)
        with self.store._db() as db:
            metadata = json.loads(db.execute("SELECT metadata FROM packets WHERE id=?",
                                             (exact["packet"],)).fetchone()[0])
        self.assertEqual(metadata["_invocation"]["normalized_request"],
            {"tool": "search", "project": "demo", "detail": "compact"})

    def test_explicit_semantic_revision_and_trace_configuration_determinants(self):
        trace = TraceService(self.config, lambda project: self.snapshot, self.host)
        self.service.impact_provider = trace
        provider = NotebookProvider(self.store,
            resolve_dependency=self.service._resolve_notebook_dependency,
            trusted_projects=frozenset({"demo"}))
        semantic = provider.resolve_dependency("demo", "semantic_revision:demo")
        self.assertEqual(semantic, {"status": "current", "digest": canonical_digest(17)})
        observation = trace._repository("demo", "source", self.snapshot)
        expected = next(item["digest"] for item in observation["inputs"]
                        if item["kind"] == "configuration" and item["key"] == "source")
        self.assertEqual(provider.resolve_dependency("demo", "configuration:source"),
                         {"status": "current", "digest": expected})
        unsupported = provider.resolve_dependency("demo", "semantic_revision:foreign")
        self.assertEqual(unsupported["status"], "unavailable")

    def test_cross_project_source_freshness_keeps_project_identity_and_partial_coverage(self):
        beta_root = self.base / "beta"
        beta_root.mkdir()
        _git(beta_root, "init", "-b", "main")
        _git(beta_root, "config", "user.name", "Fixture")
        _git(beta_root, "config", "user.email", "fixture@example.invalid")
        beta_source = beta_root / "README.md"
        beta_source.write_text("Beta shared alias fact: stable contract.\n", encoding="utf-8")
        _git(beta_root, "add", "README.md")
        _git(beta_root, "commit", "-m", "beta fixture")
        beta_head = _git(beta_root, "rev-parse", "HEAD")
        workspaces = {
            "demo": self.config.workspaces["demo"],
            "beta": WorkspaceConfig(authority_repository="source", repositories={
                "source": RepositoryConfig(root=beta_root)}),
        }
        config = ProjectControlConfig(workspaces=workspaces)
        host = ContextHost("observer", "alice", frozenset({"demo", "beta"}))
        beta_snapshot = ProjectSnapshot(workspace_id="beta", project_uuid="fixture-beta",
            observed_at="2026-10-06T00:00:01Z", todo_revision=4,
            repositories={"source": RepositoryIdentity(commit=beta_head, dirty=False)},
            todo_tables={"tasks": [], "context_fragments": []})
        snapshots = {"demo": self.snapshot, "beta": beta_snapshot}
        allowed_projects = set(host.projects)

        def packet_access(required, supplied, sources):
            if required.get("project") not in allowed_projects:
                return False
            if any(source.get("project") not in allowed_projects for source in sources):
                return False
            trusted = {**supplied, "project": required.get("project")}
            return SQLitePacketStore._authorized(required, trusted)

        self.store.authority_access = packet_access
        beta_bytes = beta_source.read_bytes()
        beta_locator = SourceLocator(project="beta", repository="source", path="README.md",
            content_sha256=hashlib.sha256(beta_bytes).hexdigest())
        beta_packet = self.store.create(tool="read", payload={"status": "ok", "data": {"beta": True}},
            sources=[beta_locator], access_scope={"principal": "alice", "profile": "observer", "project": "beta"})
        self.store.put_note({"note_id": "alpha-note-from-beta", "project": "demo", "kind": "decision",
            "claim": "cross project shared alias fact", "reason_matters": "Beta source supports Alpha context.",
            "sources": [beta_locator.model_dump(mode="json")], "evidence_packets": [beta_packet.packet_id],
            "dependencies": {}, "coverage": {"complete": True}, "uncertainty": [], "provenance": "source"},
            access_scope=self.scope)

        service = InformationService(config, self.store, snapshots.__getitem__, host)
        beta_source.write_text("Beta shared alias fact changed.\n", encoding="utf-8")
        result = service.call("search", project="demo", query="cross project shared alias fact",
                              detail="extended")
        prepared = result["data"]["prepared_context"]
        note = prepared["notes"][0]
        self.assertEqual(note["note_id"], "alpha-note-from-beta")
        self.assertEqual(note["sources"][0]["project"], "beta")
        self.assertEqual(note["freshness"], "stale")
        self.assertTrue(any(item.get("project") == "beta" and item.get("reason") == "changed"
                            for item in note["freshness_omissions"]))
        self.assertEqual(prepared["coverage"]["project"], "demo")
        self.assertFalse(prepared["coverage"].get("global_atomic_snapshot", False))

        # The Alpha lookup can still see its note while the Beta resolver segment
        # is unavailable. Coverage states Alpha's query scope and the omission
        # names Beta, rather than claiming an all-project snapshot.
        alpha_only_config = ProjectControlConfig(workspaces={"demo": workspaces["demo"]})
        alpha_only = InformationService(alpha_only_config, self.store, snapshots.__getitem__, host)
        unresolved = alpha_only._prepared_context("cross project shared alias fact", "demo")
        unresolved_note = unresolved["notes"][0]
        self.assertEqual(unresolved_note["sources"][0]["project"], "beta")
        self.assertTrue(any(item.get("project") == "beta" and
                            item.get("reason") == "project_not_permitted"
                            for item in unresolved_note["freshness_omissions"]))
        self.assertEqual(unresolved["coverage"]["project"], "demo")


if __name__ == "__main__":
    unittest.main()
