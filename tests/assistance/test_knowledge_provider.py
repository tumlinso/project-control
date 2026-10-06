from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from project_control.as1_contracts import SourceLocator
from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.knowledge import NotebookProvider


SCOPE = {"principal": "fixture", "project": "alpha", "profile": "observer"}


class MemoryStore:
    def __init__(self, records):
        self.records = records

    def list_notes(self, *, access_scope, project, limit=128):
        del access_scope
        return [dict(row) for row in self.records if row["project"] == project][:limit]


def note(note_id, claim, *, provenance="source", dependencies=None, **extra):
    return {"note_id": note_id, "project": "alpha", "kind": "finding",
            "claim": claim, "reason_matters": "fixture", "sources": [],
            "evidence_packets": ["pkt_original"],
            "dependencies": dependencies or {"config:alpha/method": "a" * 64},
            "coverage": {"tested": "bounded"}, "uncertainty": "one project",
            "provenance": provenance, **extra}


class NotebookProviderTests(unittest.TestCase):
    def provider(self, records, resolver=None, projects=("alpha",)):
        return NotebookProvider(MemoryStore(records),
            resolve_dependency=resolver or (lambda project, key: "a" * 64),
            trusted_projects=projects, clock=lambda: 1000.0)

    def test_material_determinants_are_per_record_and_head_is_irrelevant(self):
        records = [note("n-current", "current method result", dependencies={"config:alpha/method": "a" * 64}),
                   note("n-changed", "changed method result", dependencies={"config:alpha/method": "b" * 64})]
        calls = []

        def resolve(project, key):
            calls.append((project, key))
            return {"state": "current", "sha256": "a" * 64}

        result = self.provider(records, resolve).retrieve("method result", project="alpha",
                                                          access_scope=SCOPE)
        by_id = {row["note_id"]: row for row in result["notes"]}
        self.assertEqual(by_id["n-current"]["freshness"], "current")
        self.assertEqual(by_id["n-changed"]["freshness"], "stale")
        self.assertIn("changed", json.dumps(result["omissions"]))
        self.assertTrue(calls)
        self.assertTrue(all(key.startswith("config:") for _, key in calls))
        self.assertFalse(any("HEAD" in key for _, key in calls))
        self.assertFalse(result["authoritative"])

    def test_user_goals_rank_before_notes_and_contradictions_keep_original_witness(self):
        goal = note("g-1", "finish alpha migration", provenance="user", kind="goal")
        first = note("n-1", "migration is supported", contradicts=["n-2"])
        second = note("n-2", "migration is not supported", contradicts=["n-1"])
        result = self.provider([first, second, goal]).retrieve("migration", project="alpha",
                                                               access_scope=SCOPE, limit=3)
        self.assertEqual([row["note_id"] for row in result["goals"]], ["g-1"])
        self.assertEqual({row["note_id"] for row in result["notes"]}, {"n-1", "n-2"})
        for row in [*result["goals"], *result["notes"]]:
            self.assertEqual(row["evidence_packets"], ["pkt_original"])
            self.assertFalse(row["independent_proof"])
        self.assertEqual(result["notes"][0]["contradicts"], ["n-2"])

    def test_scope_denial_and_inaccessible_material_are_explicit(self):
        denied = self.provider([note("n", "private fact")]).retrieve(
            "private", project="beta", access_scope={"principal": "fixture", "project": "beta"})
        self.assertEqual(denied["omissions"][0]["reason"], "forbidden_project")
        self.assertEqual(denied["notes"], [])

        def unavailable(project, key):
            return {"state": "unavailable", "reason": "forbidden"}

        result = self.provider([note("n", "source fact")], unavailable).retrieve(
            "source", project="alpha", access_scope=SCOPE)
        self.assertEqual(result["notes"][0]["freshness"], "stale")
        self.assertIn("forbidden", json.dumps(result["omissions"]))
        self.assertEqual(result["notes"][0]["evidence_packets"], ["pkt_original"])

    def test_budget_and_limit_are_honored_with_visible_partial_coverage(self):
        records = [note(f"n-{i}", f"bounded search fact {i}") for i in range(8)]
        provider = self.provider(records)
        result = provider.retrieve("bounded search", project="alpha", access_scope=SCOPE,
                                   budget_bytes=700, limit=3)
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode()), 700)
        self.assertLessEqual(result["coverage"]["returned"], 3)
        self.assertTrue(result["coverage"]["partial"])
        self.assertTrue(result["omissions"])

    def test_source_deletion_and_absent_material_map_do_not_turn_into_proof(self):
        with_source = note("n-source", "removed config evidence", sources=[{
            "project": "alpha", "repository": "alpha", "path": "settings.toml",
            "content_sha256": "b" * 64,
        }], dependencies={})

        def deleted(project, key):
            self.assertEqual(key, "file:alpha/settings.toml")
            return {"state": "missing", "reason": "source_deleted"}

        result = self.provider([with_source], deleted).retrieve("removed config", project="alpha",
                                                                 access_scope=SCOPE)
        row = result["notes"][0]
        self.assertEqual(row["freshness"], "stale")
        self.assertIn("source_deleted", json.dumps(result["omissions"]))
        self.assertFalse(row["independent_proof"])
        self.assertEqual(row["sources"][0]["path"], "settings.toml")

    def test_actual_sqlite_store_returns_pinned_notes_and_user_goals(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLitePacketStore(Path(directory), namespace="knowledge-provider-test")
            source = SourceLocator(project="alpha", repository="alpha", path="facts.md",
                                   content_sha256="a" * 64)
            packet = store.create(tool="fixture", payload={"claim": "original fact"},
                                  access_scope=SCOPE, sources=[source])
            store.put_note({"note_id": "n-sqlite", "project": "alpha", "kind": "finding",
                            "claim": "original fact", "reason_matters": "fixture",
                            "sources": [source.model_dump(mode="json")],
                            "evidence_packets": [packet.packet_id],
                            "dependencies": {"file:alpha/facts.md": "a" * 64},
                            "coverage": {"source": "fixture"}, "uncertainty": "small",
                            "provenance": "source"}, access_scope=SCOPE)
            store.put_user_goal({"note_id": "g-sqlite", "project": "alpha", "kind": "goal",
                                 "claim": "preserve original fact", "reason_matters": "user intent",
                                 "coverage": {}, "uncertainty": "none"}, access_scope=SCOPE)
            provider = NotebookProvider(store,
                resolve_dependency=lambda project, key: {"status": "current", "digest": "a" * 64},
                trusted_projects={"alpha"})
            result = provider.retrieve("original fact", project="alpha", access_scope=SCOPE)
            self.assertEqual(result["notes"][0]["evidence_packets"], [packet.packet_id])
            self.assertEqual(result["goals"][0]["provenance"], "user")
            self.assertEqual(result["notes"][0]["freshness"], "current")

    def test_actual_store_cross_project_source_freshness_keeps_project_identity(self):
        allowed_projects = {"alpha", "beta"}

        def scope_access(saved, supplied, sources):
            return (saved.get("project") == supplied.get("project") == "alpha"
                    and all(source.get("project") in allowed_projects for source in sources))

        with tempfile.TemporaryDirectory() as directory:
            store = SQLitePacketStore(Path(directory), namespace="knowledge-cross-project-test",
                                      authority_access=scope_access)
            beta_source = SourceLocator(project="beta", repository="shared", path="facts.md",
                                        content_sha256="a" * 64)
            packet = store.create(tool="fixture", payload={"claim": "shared source fact"},
                                  access_scope=SCOPE, sources=[beta_source])
            store.put_note({"note_id": "n-cross", "project": "alpha", "kind": "finding",
                            "claim": "shared source fact", "reason_matters": "fixture",
                            "sources": [beta_source.model_dump(mode="json")],
                            "evidence_packets": [packet.packet_id],
                            "dependencies": {"config:alpha/settings": "c" * 64},
                            "coverage": {"tested": "partial"}, "uncertainty": "cross-project",
                            "provenance": "source"}, access_scope=SCOPE)

            calls = []

            def resolver(project, key):
                calls.append((project, key))
                if project == "alpha":
                    return {"status": "current", "digest": "c" * 64}
                return {"status": "current", "digest": "b" * 64}

            current_projects = {"alpha", "beta"}
            provider = NotebookProvider(store, resolve_dependency=resolver,
                                        trusted_projects=current_projects)
            changed = provider.retrieve("shared source", project="alpha", access_scope=SCOPE)
            self.assertEqual(changed["notes"][0]["freshness"], "stale")
            self.assertIn({"project": "beta", "dependency": "file:shared/facts.md",
                           "reason": "changed"}, changed["notes"][0]["freshness_omissions"])
            self.assertIn(("alpha", "config:alpha/settings"), calls)
            self.assertIn(("beta", "file:shared/facts.md"), calls)

            calls.clear()

            def missing_resolver(project, key):
                calls.append((project, key))
                if project == "alpha":
                    return {"status": "current", "digest": "c" * 64}
                return {"state": "missing", "reason": "source_deleted"}

            provider = NotebookProvider(store, resolve_dependency=missing_resolver,
                                        trusted_projects={"alpha", "beta"})
            missing = provider.retrieve("shared source", project="alpha", access_scope=SCOPE)
            self.assertIn({"project": "beta", "dependency": "file:shared/facts.md",
                           "reason": "source_deleted"}, missing["notes"][0]["freshness_omissions"])
            self.assertTrue(missing["coverage"]["partial"])
            self.assertEqual(len(calls), 2)

            calls.clear()
            provider = NotebookProvider(store, resolve_dependency=resolver,
                                        trusted_projects={"alpha"})
            forbidden = provider.retrieve("shared source", project="alpha", access_scope=SCOPE)
            self.assertEqual(forbidden["notes"][0]["freshness"], "stale")
            self.assertIn({"project": "beta", "reason": "forbidden_project"},
                          forbidden["notes"][0]["freshness_omissions"])
            self.assertEqual(calls, [("alpha", "config:alpha/settings")])
            self.assertTrue(forbidden["coverage"]["partial"])


if __name__ == "__main__":
    unittest.main()
