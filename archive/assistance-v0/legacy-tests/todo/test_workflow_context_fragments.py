from __future__ import annotations

import json
import tempfile
import unittest
from base64 import urlsafe_b64decode, urlsafe_b64encode
from pathlib import Path

from tests.todo.v2_helpers import V2Repo  # noqa: F401 - establishes the package test path

from todo_orchestrator.config import utc_now
from todo_orchestrator.db import Database
from todo_orchestrator.models import TodoError
from todo_orchestrator.workflow.context_fragments import (
    ContextFragmentStore,
    FragmentOwner,
    compose_child_packet,
    compose_legacy_capsule,
    validate_context_note_publication_scope,
)
from todo_orchestrator.workflow.foundation import canonical_json


class WorkflowContextFragmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "state.sqlite3")
        self.db.initialize({"project_uuid": "project-1", "project_name": "fixture"})
        now = utc_now()

        def seed(conn, revision):
            for task_id in ("ROOT", "TASK-1", "TASK-2"):
                conn.execute(
                    "INSERT INTO tasks(id,kind,title,status,created_at,updated_at,revision) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (task_id, "task", task_id, "planned", now, now, revision),
                )
            conn.execute(
                "INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES(?,?,?,?,?)",
                ("run-1", "ROOT", now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES(?,?,?,?,?,?)",
                ("lane-1", "run-1", "implementer", now, now, revision),
            )
            conn.execute(
                "INSERT INTO workflow_lanes(id,run_id,parent_lane_id,role,created_at,updated_at,revision) "
                "VALUES(?,?,?,?,?,?,?)",
                ("lane-2", "run-1", "lane-1", "validator", now, now, revision),
            )
            conn.executemany(
                "INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) "
                "VALUES(?,?,?,'queued',?,?)",
                [("lane-1", 0, "TASK-1", now, revision), ("lane-2", 0, "TASK-2", now, revision)],
            )
            conn.executemany(
                "INSERT INTO ownership_scopes(task_id,mode,path) VALUES(?,?,?)",
                [("TASK-1", "write", "src/cuda"), ("TASK-2", "write", "docs")],
            )

        self.db.mutate(
            actor_session_id=None,
            entity_type="fixture",
            entity_id="run-1",
            event_type="fixture_seeded",
            payload={},
            operation=seed,
        )
        self.store = ContextFragmentStore(self.db)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish(self, kind, content, *, lane_id=None, task_id=None, invalidate=()):
        return self.store.publish(
            actor_session_id=None,
            owner=FragmentOwner("run-1", lane_id, task_id),
            kind=kind,
            content=content,
            invalidate_fragment_ids=invalidate,
        )[0]

    def test_versions_and_hashes_are_stable_and_supersession_is_revisioned(self) -> None:
        content = {"objective": "Build the workflow", "invariants": ["children are subordinate"]}
        first = self.publish("run_charter", content)
        duplicate = self.publish("run_charter", dict(reversed(list(content.items()))))
        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(first.version, 1)
        self.assertEqual(first.content_hash, duplicate.content_hash)

        second = self.publish("run_charter", {**content, "objective": "Build protocol v2"})
        self.assertEqual(second.version, 2)
        old = self.store.get(first.id)
        self.assertFalse(old.active)
        self.assertEqual(old.superseded_by, second.id)
        self.assertGreater(old.invalidation_revision, old.creation_revision)

    def test_targeted_invalidation_does_not_stale_unrelated_fragments(self) -> None:
        lane = self.publish("lane_brief", {"role": "implementer"}, lane_id="lane-1")
        task = self.publish("task_brief", {"objective": "old"}, lane_id="lane-1", task_id="TASK-1")
        decision = self.publish(
            "decision_ledger",
            {"decisions": [{"id": "D-1", "value": "new"}]},
            invalidate=(task.id,),
        )
        self.assertTrue(self.store.get(lane.id).active)
        self.assertFalse(self.store.get(task.id).active)
        self.assertTrue(decision.active)
        stale = self.store.compose_first_class(
            run_id="run-1",
            lane_id="lane-1",
            task_id="TASK-1",
            known_manifest={lane.id: lane.version, task.id: task.version, decision.id: decision.version},
        )
        self.assertEqual(stale["status"], "context_stale")
        changed = {item["fragment_id"]: item for item in stale["changed_fragments"]}
        self.assertTrue(changed[task.id]["invalidated"])
        self.assertNotIn(lane.id, changed)

    def test_compact_capsule_manifest_and_targeted_context_stale(self) -> None:
        charter = self.publish(
            "run_charter",
            {"objective": "Parallel project", "invariants": ["serial lanes"], "internal_notes": "expand only"},
        )
        lane = self.publish(
            "lane_brief",
            {"role": "implementer", "ordered_tasks": ["TASK-1"], "sibling_transcript": "not replayed"},
            lane_id="lane-1",
        )
        task = self.publish(
            "task_brief",
            {"objective": "Implement", "scope": ["src/a.py"], "forbidden_mutations": ["src/b.py"]},
            lane_id="lane-1",
            task_id="TASK-1",
        )
        delta = self.publish(
            "delta_inbox",
            {"cursor": 4, "messages": [{"kind": "status", "summary": "ready"}]},
            lane_id="lane-1",
        )
        known = {item.id: item.version for item in (charter, lane, task, delta)}
        current = self.store.compose_first_class(
            run_id="run-1", lane_id="lane-1", task_id="TASK-1", known_manifest=known
        )
        self.assertEqual(current["status"], "current")
        self.assertLessEqual(len(canonical_json(current).encode()), 8 * 1024)
        self.assertNotIn("internal_notes", current["run_summary"])
        self.assertNotIn("sibling_transcript", current["lane_brief"])

        revised = self.publish(
            "task_brief",
            {"objective": "Implement revised contract", "scope": ["src/a.py"]},
            lane_id="lane-1",
            task_id="TASK-1",
        )
        stale = self.store.compose_first_class(
            run_id="run-1", lane_id="lane-1", task_id="TASK-1", known_manifest=known
        )
        self.assertEqual(stale["status"], "context_stale")
        changed = {item["fragment_id"]: item for item in stale["changed_fragments"]}
        self.assertEqual(set(changed), {task.id, revised.id})
        self.assertTrue(changed[task.id]["invalidated"])
        self.assertFalse(changed[revised.id]["invalidated"])

    def test_delta_is_lane_specific(self) -> None:
        self.publish("delta_inbox", {"cursor": 1, "messages": ["lane one"]}, lane_id="lane-1")
        self.publish("delta_inbox", {"cursor": 9, "messages": ["lane two"]}, lane_id="lane-2")
        capsule = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1")
        self.assertEqual(capsule["unread_delta"]["messages"], ["lane one"])

    def test_expansion_is_explicit_and_budgeted(self) -> None:
        fragment = self.publish("decision_ledger", {"decisions": [{"id": "D", "rationale": "x" * 500}]})
        expanded = self.store.expand(fragment.id, budget_bytes=2048)
        self.assertEqual(expanded["fragment"]["fragment_id"], fragment.id)
        with self.assertRaisesRegex(TodoError, "limit"):
            self.store.expand(fragment.id, budget_bytes=256)
        with self.assertRaisesRegex(TodoError, "256..65536"):
            self.store.expand(fragment.id, budget_bytes=128)

    def test_large_charter_pages_are_readable_and_cursor_bound(self) -> None:
        charter = self.publish("run_charter", {"objective": "x" * 14000})
        target = charter.id
        pieces: list[str] = []
        while target is not None:
            page = self.store.expand_page(target, budget_bytes=1400)
            self.assertLessEqual(len(canonical_json(page).encode()), 1400)
            self.assertEqual(page["page"]["encoding"], "canonical_json_utf8")
            pieces.append(page["page"]["content"])
            target = page["page"]["next_target"]
        self.assertEqual("".join(pieces), canonical_json(charter.content))

        cursor = self.store._page_cursor(charter, 0).removeprefix("ctxp:")
        cursor_data = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        cursor_data["content_hash"] = "0" * 64
        stale = "ctxp:" + urlsafe_b64encode(json.dumps(cursor_data).encode()).decode().rstrip("=")
        with self.assertRaisesRegex(TodoError, "cursor"):
            self.store.expand_page(stale, budget_bytes=1400)

    def test_delta_for_returns_only_changed_manifest_entries(self) -> None:
        lane = self.publish("lane_brief", {"role": "implementer"}, lane_id="lane-1")
        task = self.publish("task_brief", {"objective": "first"}, lane_id="lane-1", task_id="TASK-1")
        known = {lane.id: lane.version, task.id: task.version}
        revised = self.publish("task_brief", {"objective": "second"}, lane_id="lane-1", task_id="TASK-1")
        delta = self.store.delta_for(run_id="run-1", lane_id="lane-1", task_id="TASK-1", known_manifest=known)
        self.assertEqual({item["fragment_id"] for item in delta}, {task.id, revised.id})

    def test_source_context_is_reference_only_and_secrets_are_rejected(self) -> None:
        fragment = self.publish(
            "source_packet_ref",
            {"references": [{"packet_id": "ctx-1", "content_hash": "abc", "target": "Widget", "paths": ["src/widget.py"]}]},
            lane_id="lane-1",
            task_id="TASK-1",
        )
        self.assertEqual(fragment.content["references"][0]["packet_id"], "ctx-1")
        with self.assertRaisesRegex(TodoError, "references"):
            self.publish(
                "source_packet_ref",
                {"references": [{"packet_id": "ctx-2", "content_hash": "def", "source": "whole tree"}]},
                lane_id="lane-1",
                task_id="TASK-1",
            )
        with self.assertRaisesRegex(TodoError, "forbidden"):
            self.publish("task_brief", {"objective": "x", "claim_token": "raw-secret"}, task_id="TASK-1")

    def test_context_notes_have_independent_series_and_authority(self) -> None:
        owner = FragmentOwner("run-1", "lane-1", "TASK-1")
        first, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="cuda-layout",
            content={"content": {"summary": "tile alignment matters"}, "anchors": [{"kind": "path", "value": "src/cuda/kernels.cu"}], "classification": "implementation_insight", "source_identity": {"commit": "abc"}},
        )
        unrelated, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="api-caveat",
            content={"content": {"summary": "interface stays narrow"}, "anchors": [{"kind": "interface", "value": "API"}]},
        )
        revised, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="cuda-layout",
            content={"content": {"summary": "revised alignment finding"}, "anchors": [{"kind": "path", "value": "src/cuda/kernels.cu"}], "source_identity": {"commit": "def"}},
            invalidate_fragment_ids=[unrelated.id],
        )
        self.assertEqual(revised.version, 2)
        self.assertFalse(self.store.get(first.id).active)
        self.assertFalse(self.store.get(unrelated.id).active)
        self.assertEqual(revised.reference()["authority"], "non_authoritative")
        self.assertEqual(revised.content["source_identity"]["commit"], "def")

    def test_context_note_bounds_reject_before_any_mutation(self) -> None:
        revision = self.db.revision()
        with self.assertRaisesRegex(TodoError, "anchor sequence"):
            self.store.publish(
                actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"),
                kind="context_note", series_key="too-many-anchors",
                content={"content": {"summary": "too many"}, "anchors": [{"kind": "path", "value": f"src/{index}"} for index in range(33)]},
            )
        with self.assertRaisesRegex(TodoError, "limit"):
            self.store.publish(
                actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"),
                kind="context_note", series_key="too-large-note",
                content={"content": {"summary": "x" * 4096}, "anchors": [{"kind": "path", "value": "src"}]},
            )
        self.assertEqual(self.db.revision(), revision)
        with self.db.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_context_fragments WHERE kind='context_note'").fetchone()[0], 0)

    def test_note_capsule_keeps_manifest_when_compact_bodies_exceed_budget(self) -> None:
        owner = FragmentOwner("run-1", "lane-1", "TASK-1")
        first, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="large-one",
            content={"content": {"summary": "a" * 3800}, "anchors": [{"kind": "path", "value": "src/cuda"}]},
        )
        second, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="large-two",
            content={"content": {"summary": "b" * 3800}, "anchors": [{"kind": "path", "value": "src/cuda"}]},
        )
        capsule = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1")
        self.assertLessEqual(len(canonical_json(capsule).encode()), 8 * 1024)
        self.assertEqual({item["fragment_id"] for item in capsule["fragment_manifest"] if item["kind"] == "context_note"}, {first.id, second.id})
        self.assertEqual(capsule["context_notes_omitted"]["count"], 1)
        self.assertEqual(capsule["context_notes_omitted"]["retrieve_via"]["tool"], "coordination_view")

    def test_integrator_note_scope_is_narrow_while_coordinator_may_publish_wide(self) -> None:
        wide = {"content": {"summary": "project knowledge"}, "anchors": [{"kind": "project", "value": "project-1"}]}
        owned = {"content": {"summary": "kernel knowledge"}, "anchors": [{"kind": "path", "value": "src/cuda"}]}
        with self.db.read() as conn:
            validate_context_note_publication_scope(conn, role="coordinator", run_id="run-1", lane_id="lane-1", task_id="TASK-1", content=wide)
            validate_context_note_publication_scope(conn, role="integrator", run_id="run-1", lane_id="lane-1", task_id="TASK-1", content=owned)
            for denied in (wide, {"content": {"summary": "unrelated"}, "anchors": [{"kind": "path", "value": "docs"}]}, {"content": {"summary": "other task"}, "anchors": [{"kind": "task", "value": "TASK-2"}]}):
                with self.assertRaisesRegex(TodoError, "scope"):
                    validate_context_note_publication_scope(conn, role="integrator", run_id="run-1", lane_id="lane-1", task_id="TASK-1", content=denied)

    def test_many_relevant_notes_have_a_bounded_aggregate_route(self) -> None:
        owner = FragmentOwner("run-1", "lane-1", "TASK-1")
        for index in range(80):
            self.store.publish(
                actor_session_id=None, owner=owner, kind="context_note", series_key=f"note-{index}",
                content={"content": {"summary": f"finding {index}"}, "anchors": [{"kind": "path", "value": "src/cuda"}]},
            )
        capsule = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1")
        self.assertLessEqual(len(canonical_json(capsule).encode()), 8 * 1024)
        aggregate = next(item for item in capsule["fragment_manifest"] if item["kind"] == "context_note_aggregate")
        self.assertGreater(capsule["context_notes_omitted"]["count"], 0)
        self.assertEqual(aggregate["retrieve_via"]["max_items"], 1000)
        self.assertEqual(aggregate["select_kind"], "context_note")
        known = {item["fragment_id"]: {"version": item["version"], "content_hash": item["content_hash"]} for item in capsule["fragment_manifest"]}
        self.store.publish(actor_session_id=None, owner=owner, kind="context_note", series_key="note-80", content={"content": {"summary": "new omitted finding"}, "anchors": [{"kind": "path", "value": "src/cuda"}]})
        stale = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1", known_manifest=known)
        self.assertEqual(stale["status"], "context_stale")
        self.assertIn("context-notes:omitted", {item["fragment_id"] for item in stale["changed_fragments"]})

    def test_context_note_path_is_virtual_and_unrelated_note_does_not_stale(self) -> None:
        owner = FragmentOwner("run-1", "lane-2", "TASK-2")
        relevant, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="cuda-finding",
            content={"content": {"summary": "kernel observation"}, "anchors": [{"kind": "path", "value": "src/cuda"}]},
        )
        ignored, _ = self.store.publish(
            actor_session_id=None, owner=owner, kind="context_note", series_key="docs-finding",
            content={"content": {"summary": "documentation observation"}, "anchors": [{"kind": "path", "value": "docs/guide.md"}]},
        )
        capsule = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1")
        self.assertEqual([note["fragment_id"] for note in capsule["context_notes"]], [relevant.id])
        known = {item["fragment_id"]: item["version"] for item in capsule["fragment_manifest"]}
        current = self.store.compose_first_class(run_id="run-1", lane_id="lane-1", task_id="TASK-1", known_manifest=known)
        self.assertEqual(current["status"], "current")
        self.assertNotIn(ignored.id, {item["fragment_id"] for item in current["fragment_manifest"]})

    def test_context_note_series_survives_run_origin_and_supersedes_prior_origin(self) -> None:
        now = utc_now()
        self.db.mutate(
            actor_session_id=None, entity_type="fixture", entity_id="run-2", event_type="fixture.second_run", payload={},
            operation=lambda conn, revision: (
                conn.execute("INSERT INTO workflow_runs(id,root_task_id,created_at,updated_at,revision) VALUES('run-2','ROOT',?,?,?)", (now, now, revision)),
                conn.execute("INSERT INTO workflow_lanes(id,run_id,role,created_at,updated_at,revision) VALUES('lane-3','run-2','coordinator',?,?,?)", (now, now, revision)),
                conn.execute("INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES('lane-3',0,'TASK-2','queued',?,?)", (now, revision)),
            ),
        )
        first, _ = self.store.publish(actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"), kind="context_note", series_key="project-finding", content={"content": {"summary": "first"}, "anchors": [{"kind": "project", "value": "project-1"}]})
        second, _ = self.store.publish(actor_session_id=None, owner=FragmentOwner("run-2", "lane-3", "TASK-2"), kind="context_note", series_key="project-finding", content={"content": {"summary": "second"}, "anchors": [{"kind": "project", "value": "project-1"}]}, invalidate_fragment_ids=[first.id])
        self.assertFalse(self.store.get(first.id).active)
        self.assertEqual(second.version, 2)
        duplicate, _ = self.store.publish(actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"), kind="context_note", series_key="project-finding", content={"content": {"summary": "second"}, "anchors": [{"kind": "project", "value": "project-1"}]})
        self.assertEqual(duplicate.id, second.id)
        with self.assertRaisesRegex(TodoError, "series"):
            self.store.publish(actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"), kind="context_note", series_key="project-finding", content={"content": {"summary": "scope collision"}, "anchors": [{"kind": "path", "value": "docs"}]})
        other, _ = self.store.publish(actor_session_id=None, owner=FragmentOwner("run-2", "lane-3", "TASK-2"), kind="context_note", series_key="other-series", content={"content": {"summary": "other"}, "anchors": [{"kind": "project", "value": "project-1"}]})
        with self.assertRaisesRegex(TodoError, "same run"):
            self.store.publish(actor_session_id=None, owner=FragmentOwner("run-1", "lane-1", "TASK-1"), kind="context_note", series_key="attempt-series", content={"content": {"summary": "attempt"}, "anchors": [{"kind": "project", "value": "project-1"}]}, invalidate_fragment_ids=[other.id])
        capsule = self.store.compose_first_class(run_id="run-2", lane_id="lane-3", task_id="TASK-2")
        self.assertIn(second.id, {note["fragment_id"] for note in capsule["context_notes"]})

    def test_legacy_v2_capsule_remains_bounded_without_secrets(self) -> None:
        result = compose_legacy_capsule(
            {"task": {"id": "OLD", "objective": "continue"}, "scope": {"exclusive_paths": ["a.py"]}}
        )
        self.assertEqual(result["compatibility_mode"], "legacy_v2_single_lane")
        self.assertLessEqual(len(canonical_json(result).encode()), 8 * 1024)
        with self.assertRaisesRegex(TodoError, "forbidden"):
            compose_legacy_capsule({"task": {"id": "OLD"}, "session_token": "secret"})

    def test_child_packet_is_narrow_allowlisted_and_under_four_kib(self) -> None:
        packet = compose_child_packet(
            delegated_objective="Measure one parser function",
            parent_constraints=["read only", "return candidate evidence"],
            parent_authorized_paths=["src"],
            child_authorized_paths=["src/parser.py"],
            source_packet_refs=[{"packet_id": "ctx-child", "content_hash": "123", "target": "parse", "paths": ["src/parser.py"]}],
            required_output_schema={"kind": "test_result", "fields": ["status", "summary"]},
            candidate_gates=["unit parser"],
            acceptance_gates=["parent suite"],
            interface_facts=[{"name": "parser-api", "version": "1"}],
        )
        self.assertEqual(packet["packet_class"], "subordinate_local_child")
        self.assertLessEqual(len(canonical_json(packet).encode()), 4 * 1024)
        for forbidden in (
            "run_charter",
            "lane_brief",
            "sibling_lanes",
            "messages",
            "decision_ledger",
            "rendezvous",
            "role",
            "task_claim",
        ):
            self.assertNotIn(forbidden, packet)

    def test_child_scope_is_strict_and_packet_rejects_raw_diagnostics(self) -> None:
        common = dict(
            delegated_objective="Inspect",
            parent_constraints=[],
            source_packet_refs=[{"packet_id": "ctx", "content_hash": "123", "paths": ["src/a.py"]}],
            required_output_schema={"kind": "source_finding"},
            candidate_gates=[],
            acceptance_gates=[],
        )
        with self.assertRaisesRegex(TodoError, "narrower"):
            compose_child_packet(
                **common,
                parent_authorized_paths=["src/a.py"],
                child_authorized_paths=["src/a.py"],
            )
        with self.assertRaisesRegex(TodoError, "subset"):
            compose_child_packet(
                **common,
                parent_authorized_paths=["src"],
                child_authorized_paths=["tests"],
            )
        with self.assertRaisesRegex(TodoError, "forbidden"):
            compose_child_packet(
                **common,
                parent_authorized_paths=["src"],
                child_authorized_paths=["src/a.py"],
                interface_facts=[{"name": "api", "stdout": "raw output"}],
            )

    def test_child_source_paths_are_within_minimal_child_scope(self) -> None:
        with self.assertRaises(TodoError) as broad:
            compose_child_packet(
                delegated_objective="Inspect parser",
                parent_constraints=["read only"],
                parent_authorized_paths=["src"],
                child_authorized_paths=["src/parser"],
                source_packet_refs=[{"packet_id": "ctx", "content_hash": "abc", "paths": ["src/other"]}],
                required_output_schema={"type": "object"},
                candidate_gates=[],
                acceptance_gates=[],
            )
        self.assertEqual(broad.exception.code, "child_scope_expansion")

    def test_fragment_owner_must_match_run_lane_and_task(self) -> None:
        with self.assertRaises(TodoError) as mismatch:
            self.publish("task_brief", {"objective": "wrong"}, lane_id="lane-2", task_id="TASK-1")
        self.assertEqual(mismatch.exception.code, "fragment_task_owner_mismatch")


if __name__ == "__main__":
    unittest.main()
