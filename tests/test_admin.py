from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from project_control import admin


class AdminCliTests(unittest.TestCase):
    def test_inspect_only_forwards_without_recovery(self) -> None:
        with patch.object(admin, "inspect_recovery", return_value={"status": "safe"}) as inspect, \
             patch.object(admin, "recover") as recover, patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main(["recover", "--repo", "/repo", "--task", "T-1", "--reason", "owner", "--inspect-only"])
        self.assertEqual(result, 0)
        inspect.assert_called_once_with("/repo", "T-1")
        recover.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {"status": "safe"})

    def test_recovery_forwards_explicit_owner_reason(self) -> None:
        with patch.object(admin, "recover") as recover:
            result = admin.main(["recover", "--repo", "/repo", "--reason", "owner approved"])
        self.assertEqual(result, 0)
        recover.assert_called_once_with("/repo", reason="owner approved", task_id=None)

    def test_prepare_run_workspaces_cli_defaults_to_preview(self) -> None:
        prepared = {"status": "ready", "pending": [{"lane_id": "L-A"}]}
        with patch.object(admin, "prepare_run_workspaces", return_value=prepared) as prepare, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main([
                "prepare-run-workspaces", "--repo", "/repo", "--plan", "/plan.json", "--run", "RUN",
            ])
        self.assertEqual(result, 0)
        prepare.assert_called_once_with(
            "/repo", "/plan.json", "RUN", apply=False, confirmation=None,
        )
        self.assertEqual(json.loads(output.getvalue()), prepared)

    def test_reconcile_workspace_base_cli_defaults_to_preview(self) -> None:
        preview = {"status": "ready", "lane_id": "L-A"}
        with patch.object(admin, "reconcile_workspace_base", return_value=preview) as reconcile, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            result = admin.main([
                "reconcile-workspace-base", "--repo", "/repo", "--run", "RUN",
                "--lane", "L-A", "--base", "abc", "--reason", "prior wave",
            ])
        self.assertEqual(result, 0)
        reconcile.assert_called_once_with(
            "/repo", "RUN", "L-A", "abc", reason="prior wave",
            apply=False, confirmation=None,
        )
        self.assertEqual(json.loads(output.getvalue()), preview)


if __name__ == "__main__":
    unittest.main()
