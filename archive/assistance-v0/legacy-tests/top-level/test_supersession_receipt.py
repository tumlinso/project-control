from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from project_control.workflow_core import supersession
from project_control.workflow_core.supersession import run_authorized_supersession


class SupersessionReceiptTests(unittest.TestCase):
    def test_real_retirement_replays_canonical_receipt_after_projection_failure_and_expiry(self) -> None:
        from project_control import admin
        from tests.test_supersession_journey import SupersessionJourneyTests

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            with patch.dict(os.environ, {"TODO_ORCHESTRATOR_STATE_DIR": str(root / "runtime")}, clear=False), patch.object(admin, "_runtime_identity"):
                fixture = SupersessionJourneyTests()
                service = fixture._fixture(root)
                intent = root / "intent.json"
                intent.write_text('{"source_run_id":"OLD-RUN","successor_run_id":"NEW-RUN","reason":"replace"}', encoding="utf-8")
                grant = admin.prepare_supersession_assignment(root, intent, recipient_principal="operator", expires_seconds=1)
                authorization_id = str(grant["assignment"]["grant_reference"])
                original_replace = supersession._replace
                failed = False
                def fail_once(path, value):
                    nonlocal failed
                    if value.get("completed_receipt") is not None and not failed:
                        failed = True
                        raise OSError("simulated post-commit receipt failure")
                    return original_replace(path, value)
                with patch.object(supersession, "_replace", side_effect=fail_once):
                    with self.assertRaisesRegex(OSError, "post-commit receipt"):
                        run_authorized_supersession(service, authorization_id=authorization_id, recipient_principal="operator")
                    with patch.object(supersession, "_now", return_value=datetime(2099, 1, 1, tzinfo=timezone.utc)):
                        replay = run_authorized_supersession(service, authorization_id=authorization_id, recipient_principal="operator")
                self.assertEqual(replay["status"], "already_retired")
                with service.db.read() as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM events WHERE event_type='workflow.run.retired'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
