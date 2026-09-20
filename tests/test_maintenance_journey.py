from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


SKILLS = Path("/home/tumlinson/.agents/skills")
RECOVERY_FIXTURE = SKILLS / "todo-orchestrator/tests/test_workflow_recovery.py"


@unittest.skipUnless(RECOVERY_FIXTURE.is_file(), "local Todo recovery fixture unavailable")
class MaintenanceJourneyTests(unittest.TestCase):
    def test_fresh_process_maintains_stopped_target_then_publicly_claims_it(self) -> None:
        """One same-target clean-stop journey through the paired public boundary."""
        child = """
import json
import asyncio
import sys
from pathlib import Path
from test_workflow_recovery import WorkflowRecoveryTests
from todo_orchestrator.git_state import scope_manifest
from project_control import admin
from project_control.app import create_mcp
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.workflow_tools import trusted_maintenance_context

fixture = WorkflowRecoveryTests('test_expired_readonly_coordinator_requeues_atomically_with_live_process')
fixture.setUp()
try:
    fixture.seed_dispatch(capability=True)
    fixture.mutate(lambda conn, revision: (
        conn.execute('INSERT INTO workflow_lane_tasks(lane_id,position,task_id,state,enqueued_at,revision) VALUES(?,?,?,?,?,?)', ('LANE', 0, 'A', 'queued', 'now', revision)),
        conn.execute('UPDATE claims SET state=?,released_at=?,expires_at=?,baseline_manifest_json=? WHERE id=?', ('expired_clean', '2000-01-01', '2000-01-01', json.dumps(scope_manifest(fixture.repo.root, ['src/a'])), fixture.claim_id)),
        conn.execute('UPDATE tasks SET status=? WHERE id=?', ('planned', 'A')),
        conn.execute('UPDATE lock_leases SET state=? WHERE claim_id=?', ('released', fixture.claim_id)),
    ))
    assignment = admin.prepare_maintenance_assignment(fixture.repo.root, task_id='A', recipient_principal='test-operator-a')
    config = ProjectControlConfig(skills_root=Path(sys.argv[1]), workspaces={
        'fixture': WorkspaceConfig(authority_repository='source', repositories={
            'source': RepositoryConfig(root=fixture.repo.root),
        }),
    })
    mcp = create_mcp(config, profile='codex', maintenance_host=trusted_maintenance_context('test-operator-a'))
    wrong_host = create_mcp(config, profile='codex', maintenance_host=trusted_maintenance_context('test-operator-b'))
    unconfigured = create_mcp(config, profile='codex')
    missing = asyncio.run(unconfigured._tool_manager.call_tool('maintain_execution', {
        'repo_root': str(fixture.repo.root), 'authorization_id': assignment['assignment']['grant_reference'],
    }))
    try:
        asyncio.run(wrong_host._tool_manager.call_tool('maintain_execution', {
            'repo_root': str(fixture.repo.root), 'authorization_id': assignment['assignment']['grant_reference'],
        }))
    except Exception as error:
        wrong = str(error)
    else:
        wrong = 'unexpected_success'
    maintained = asyncio.run(mcp._tool_manager.call_tool('maintain_execution', {
        'repo_root': str(fixture.repo.root), 'authorization_id': assignment['assignment']['grant_reference'],
    }))
    resumed = asyncio.run(mcp._tool_manager.call_tool('next_task', {
        'repo_root': str(fixture.repo.root), 'task_id': 'A',
    }))
    print(json.dumps({'assignment': assignment['status'], 'missing': missing['reason'], 'wrong': wrong, 'maintained': maintained['status'], 'resumed': resumed['status']}))
finally:
    fixture.tearDown()
"""
        environment = dict(os.environ)
        runtime_skills = Path(environment.get("PROJECT_CONTROL_SKILLS_ROOT", str(SKILLS)))
        environment.setdefault("PROJECT_CONTROL_SKILLS_ROOT", str(runtime_skills))
        fixture_tests = runtime_skills / "todo-orchestrator/tests"
        self.assertTrue(fixture_tests.is_dir(), f"candidate recovery fixture unavailable: {fixture_tests}")
        environment["PYTHONPATH"] = os.pathsep.join([
            str(fixture_tests),
            environment.get("PYTHONPATH", ""),
        ])
        completed = subprocess.run(
            [sys.executable, "-c", child, str(runtime_skills)], cwd=Path(__file__).parents[1], env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("launch_required", result["assignment"])
        self.assertEqual("maintenance_host_unconfigured", result["missing"])
        self.assertIn("recovery_authorization_principal_mismatch", result["wrong"])
        self.assertEqual("maintained", result["maintained"])
        self.assertEqual("claimed", result["resumed"])


if __name__ == "__main__":
    unittest.main()
