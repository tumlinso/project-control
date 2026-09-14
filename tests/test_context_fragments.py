from __future__ import annotations

import json
import unittest

from project_control.context_fragments import canonical_context_fragments

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class ContextFragmentNormalizationTests(unittest.TestCase):
    def test_aliases_merge_and_preserve_versioned_note_metadata(self) -> None:
        snapshot = cellerator_snapshot()
        row = {"id": "NOTE-1", "kind": "context_note", "series_key": "insight", "version": 2,
               "content_json": json.dumps({"anchors": [{"kind": "symbol", "value": "Kernel"}], "content": {"summary": "keep warp order"}})}
        snapshot.todo_tables["context_fragments"] = [dict(row, content_hash="abc")]
        snapshot.todo_tables["run_context_fragments"] = [dict(row, task_id="CE-ARCH-92")]
        snapshot.todo_tables["workflow_context_fragments"] = [dict(row, run_id="RUN-1", lane_id="L-1")]
        rows = canonical_context_fragments(snapshot)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["authority"], "non_authoritative_context")
        self.assertEqual(rows[0]["content"]["summary"], "keep warp order")
        self.assertEqual(rows[0]["task_id"], "CE-ARCH-92")
        self.assertEqual(rows[0]["anchors"]["symbol"], "Kernel")


if __name__ == "__main__":
    unittest.main()
