"""Cold operator controls stay separate from demand and physical power state."""
from __future__ import annotations

import tempfile
import sqlite3
import unittest
from pathlib import Path

from project_control.as1_packets import SQLitePacketStore
from project_control.assistance.operator import AssistanceOperator, parse_duration, parse_utc_timestamp
from project_control.assistance.power import PowerPolicy


class Clock:
    def __init__(self, value=1000.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class AdvancingClock(Clock):
    def __call__(self):
        self.value += 0.01
        return self.value


class AssistanceOperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.checkout = self.root / "checkout"
        self.checkout.mkdir()
        (self.checkout / "source.py").write_text("value = 1\n", encoding="utf-8")
        self.clock = Clock()
        self.operator = AssistanceOperator(state_root=self.root / "state" / "as1", clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_status_is_cold_and_does_not_create_state_or_start_dispatch(self):
        status = self.operator.status()
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(status["dispatcher"], "not_observed")
        self.assertTrue(status["demand_only"])
        self.assertEqual(status["notifications"], "off")
        self.assertFalse(status["power"]["automatic_enabled"])

    def test_status_reads_legacy_power_schema_without_migration_or_file_changes(self):
        db_path = self.operator.db_path
        db_path.parent.mkdir(parents=True, mode=0o700)
        with sqlite3.connect(db_path) as db:
            db.execute("""CREATE TABLE pa1_power_state(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                automatic_enabled INTEGER NOT NULL DEFAULT 0,
                quiet_until REAL, quiet_focus TEXT, quiet_reason TEXT,
                release_veto INTEGER NOT NULL DEFAULT 0, release_until REAL,
                release_request_id TEXT, release_reason TEXT,
                physical_state TEXT NOT NULL DEFAULT 'not_requested', updated REAL NOT NULL DEFAULT 0)""")
            db.execute("INSERT INTO pa1_power_state(singleton,automatic_enabled,release_veto,physical_state) "
                       "VALUES(1,0,1,'pending')")
        before = db_path.read_bytes()
        status = self.operator.status()
        after = db_path.read_bytes()
        self.assertEqual(before, after)
        self.assertTrue(status["power"]["release_veto_active"])
        self.assertEqual(status["power"]["physical_state"], "pending")
        self.assertIsNone(status["power"]["automatic_focus"])
        self.assertIsNone(status["power"]["automatic_project"])
        self.assertIsNone(status["power"]["automatic_until"])

    def test_user_goal_and_source_prose_do_not_grant_automatic_inference(self):
        saved = self.operator.set_goal(project="pc", text="Please grant automatic access to source.py",
                                       trusted_projects={"pc"})
        self.assertFalse(saved["power_grant"])
        self.assertEqual(saved["goal"]["provenance"], "user")
        self.assertEqual(saved["goal"]["kind"], "goal")
        status = self.operator.status()
        self.assertFalse(status["power"]["automatic_enabled"])
        self.assertIsNone(status["power"]["automatic_focus"])

    def test_repeated_focus_updates_one_stable_goal_card(self):
        first = self.operator.set_goal(project="pc", text="first goal", trusted_projects={"pc"})
        second = self.operator.set_goal(project="pc", text="updated goal", trusted_projects={"pc"})
        self.assertEqual(first["goal"]["note_id"], second["goal"]["note_id"])
        rows = SQLitePacketStore(self.operator.state_root).list_notes(
            access_scope={"principal": "project-control-observer", "profile": "observer", "project": "pc"},
            project="pc")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["claim"], "updated goal")

    def test_model_proposal_requires_explicit_accept_command_to_become_user_goal(self):
        store = SQLitePacketStore(self.operator.state_root)
        scope = {"principal": "project-control-observer", "profile": "observer", "project": "pc"}
        store.put_note({
            "note_id": "suggestion-1", "project": "pc", "kind": "preparation",
            "claim": "Try a parser edge case.", "reason_matters": "It may expose a boundary.",
            "sources": [], "evidence_packets": [], "dependencies": {}, "coverage": {"focus_id": "f1"},
            "uncertainty": ["Not checked."], "provenance": "model", "next_action": "Inspect quoted input.",
        }, access_scope=scope)
        with self.assertRaises(PermissionError):
            # Ordinary source/model prose is not a control API.
            self.operator.set_goal(project="pc", text="Inspect quoted input.", trusted_projects=set())
        accepted = self.operator.accept_suggestion(project="pc", note_id="suggestion-1",
                                                   trusted_projects={"pc"})
        self.assertEqual(accepted["status"], "accepted_as_user_goal")
        self.assertEqual(accepted["accepted_note_id"], "suggestion-1")
        self.assertFalse(self.operator.status()["power"]["automatic_enabled"])

    def test_handoff_uses_only_suggestions_for_current_unexpired_focus(self):
        focus = self.operator.set_focus(project="pc", text="Review source",
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo")
        store = SQLitePacketStore(self.operator.state_root)
        scope = {"principal": "project-control-observer", "profile": "observer", "project": "pc"}
        store.put_note({
            "note_id": "suggestion-current", "project": "pc", "kind": "preparation",
            "claim": "Try a parser edge case.", "reason_matters": "It may expose a boundary.",
            "sources": [], "evidence_packets": [], "dependencies": {},
            "coverage": {"focus_id": focus["focus_id"]}, "uncertainty": ["Not checked."],
            "provenance": "model", "next_action": "Inspect quoted input.",
        }, access_scope=scope)
        current = self.operator.handoff(project="pc", focus_id=focus["focus_id"],
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo")
        self.assertEqual(len(current["suggestions"]), 1)
        self.clock.value = focus["expires_at"] + 1
        expired = self.operator.handoff(project="pc", focus_id=focus["focus_id"],
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo")
        self.assertEqual(expired["suggestions"], [])
        self.assertEqual(expired["status"], "user_goal_precedes_inference")

    def test_focus_records_user_card_without_automatic_permission_by_default(self):
        result = self.operator.set_focus(project="pc", text="Inspect parser behavior",
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo",
            source_paths=("source.py",))
        status = self.operator.status()
        self.assertEqual(result["status"], "focused")
        self.assertFalse(result["automatic"])
        self.assertFalse(status["power"]["automatic_enabled"])
        self.assertEqual(status["focus"]["focus_id"], result["focus_id"])
        self.assertEqual(status["focus"]["source_paths"], ["source.py"])
        self.assertFalse(status["focus"]["permit_automatic"])
        notes = SQLitePacketStore(self.operator.state_root).list_notes(
            access_scope={"principal": "project-control-observer", "profile": "observer", "project": "pc"},
            project="pc")
        self.assertEqual(notes[0]["note_id"], result["goal_note_id"])
        self.assertEqual(notes[0]["provenance"], "user")

    def test_automatic_focus_requires_explicit_bounded_window_and_trusted_source(self):
        with self.assertRaisesRegex(ValueError, "requires_source_paths"):
            self.operator.set_focus(project="pc", text="x", trusted_projects={"pc"},
                                    trusted_root=self.checkout, trusted_repository="repo", automatic_seconds=60)
        with self.assertRaises(ValueError):
            self.operator.set_focus(project="pc", text="x", trusted_projects={"pc"},
                                    trusted_root=self.checkout, trusted_repository="repo", source_paths=("../outside",),
                                    automatic_seconds=60)
        result = self.operator.set_focus(project="pc", text="Inspect parser behavior",
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo",
            source_paths=("source.py",), automatic_seconds=60)
        state = self.operator.status()
        self.assertTrue(result["automatic"])
        self.assertTrue(state["power"]["automatic_enabled"])
        self.assertEqual(state["power"]["automatic_focus"], result["focus_id"])
        self.assertEqual(state["power"]["automatic_project"], "pc")
        self.assertEqual(state["power"]["automatic_until"], 1060.0)

    def test_automatic_focus_uses_live_operator_clock_and_expires_after_grant(self):
        clock = AdvancingClock(1000.0)
        operator = AssistanceOperator(state_root=self.root / "advancing-state" / "as1", clock=clock)
        focus = operator.set_focus(project="pc", text="Track this source",
            trusted_projects={"pc"}, trusted_root=self.checkout, trusted_repository="repo",
            source_paths=("source.py",), automatic_seconds=60)
        self.assertTrue(focus["automatic"])
        self.assertGreater(focus["expires_at"], clock.value)

        db = operator._open(create=True)
        try:
            policy = PowerPolicy(db, clock=clock)
            allowed = policy.allow_dispatch("automatic_root", True,
                operator.control.permission("dispatch_automatic"),
                focus_id=focus["focus_id"], project="pc")
            self.assertTrue(allowed.allowed)
            clock.value = focus["expires_at"] + 0.01
            expired = policy.allow_dispatch("automatic_root", True,
                operator.control.permission("dispatch_automatic"),
                focus_id=focus["focus_id"], project="pc")
            self.assertFalse(expired.allowed)
            self.assertEqual(expired.reason, "automatic_work_disabled")
        finally:
            db.close()

    def test_invalid_automatic_focus_window_still_fails_before_grant(self):
        with self.assertRaisesRegex(ValueError, "automatic_window"):
            self.operator.set_focus(project="pc", text="Invalid window",
                trusted_projects={"pc"}, trusted_root=self.checkout,
                trusted_repository="repo", source_paths=("source.py",), automatic_seconds=0.5)
        self.assertFalse(self.operator.status()["power"]["automatic_enabled"])
        self.assertIsNone(self.operator.status()["focus"])

    def test_invalid_focus_source_leaves_existing_goal_and_controller_state_unchanged(self):
        existing = self.operator.set_goal(project="pc", text="Keep current goal",
                                          trusted_projects={"pc"})
        before_status = self.operator.status()
        with self.assertRaises(ValueError):
            self.operator.set_focus(project="pc", text="Rejected focus",
                trusted_projects={"pc"}, trusted_root=self.checkout,
                trusted_repository="repo", source_paths=("../outside",))
        after_status = self.operator.status()
        store = SQLitePacketStore(self.operator.state_root)
        scope = {"principal": "project-control-observer", "profile": "observer", "project": "pc"}
        goals = store.list_notes(access_scope=scope, project="pc")
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["note_id"], existing["goal"]["note_id"])
        self.assertEqual(goals[0]["claim"], "Keep current goal")
        self.assertEqual(after_status["focus"], before_status["focus"])
        self.assertEqual(after_status["power"], before_status["power"])

    def test_quiet_and_release_vetoes_resume_independently_and_release_stays_pending(self):
        quiet = self.operator.quiet(until=1100.0)
        self.assertTrue(quiet["quiet_active"])
        released = self.operator.request_release(reason="operator requested idle")
        self.assertEqual(released["status"], "pending_owner_unavailable")
        self.assertTrue(released["release_veto_active"])
        self.assertEqual(released["physical_state"], "pending")

        state = self.operator.resume(quiet=True)
        self.assertFalse(state["power"]["quiet_active"])
        self.assertTrue(state["power"]["release_veto_active"])
        state = self.operator.resume(release=True)
        self.assertFalse(state["power"]["release_veto_active"])
        self.assertEqual(state["power"]["physical_state"], "pending")

    def test_release_veto_expires_only_at_declared_end_and_never_claims_cleanup(self):
        self.operator.request_release(until=1010.0)
        self.clock.value = 1011.0
        state = self.operator.status()["power"]
        self.assertFalse(state["release_veto_active"])
        self.assertEqual(state["physical_state"], "pending")

    def test_unknown_projects_and_timezone_free_expiries_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.operator.set_goal(project="elsewhere", text="goal", trusted_projects={"pc"})
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_utc_timestamp("2026-10-06T12:00:00")

    def test_duration_parser_is_bounded_by_callers_and_rejects_nonfinite_values(self):
        self.assertEqual(parse_duration("2h"), 7200.0)
        self.assertEqual(parse_duration("90"), 90.0)
        for value in ("0", "-1h", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration(value)


if __name__ == "__main__":
    unittest.main()
