from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from tests.todo.scripts.todo_common import (  # noqa: E402
    load_root_doc,
    load_workstream_doc,
    parse_task_items,
    set_task_status,
    upsert_task,
    upsert_workstream_entry,
)


class StatusUpdateTests(unittest.TestCase):
    def test_task_status_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            doc = load_workstream_doc(repo_root, "ship-feature", "Ship feature")
            upsert_task(doc, "Write tests", status=" ")
            upsert_task(doc, "Implement parser", status="~")
            self.assertTrue(set_task_status(doc, "Write tests", "x"))
            tasks = {task["text"]: task["status"] for task in parse_task_items(doc.sections["Tasks"])}
            self.assertEqual(tasks["Write tests"], "x")
            self.assertEqual(tasks["Implement parser"], "~")

    def test_root_workstream_index_updates_existing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            doc = load_root_doc(repo_root)
            upsert_workstream_entry(doc, "ship-feature", "Ship feature", status="planned", owner="agent-a")
            upsert_workstream_entry(doc, "ship-feature", "Ship feature", status="done", owner="agent-b")
            workstreams = doc.sections["Workstreams"]
            self.assertEqual(len(workstreams), 1)
            self.assertIn("status: done", workstreams[0])
            self.assertIn("owner: agent-b", workstreams[0])


if __name__ == "__main__":
    unittest.main()
