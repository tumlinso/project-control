from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RECOVERY_FIXTURE = PROJECT_ROOT / "tests" / "todo" / "test_workflow_recovery.py"


class MaintenanceJourneyTests(unittest.TestCase):
    def test_fresh_process_maintains_stopped_target_then_publicly_claims_it(self) -> None:
        """One same-target clean-stop journey through the paired public boundary."""
        child = """
import json
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from tests.todo.test_workflow_recovery import WorkflowRecoveryTests
from todo_orchestrator.git_state import scope_manifest

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
    # Issuer, trusted operator, and ordinary implementer are independent
    # processes. The issuer has only its exact development/release bindings
    # plus V2Repo's disposable authority locator.
    issuer_environment = {
        'TODO_ORCHESTRATOR_STATE_DIR': str(fixture.repo.state_root),
        'PYTHONPATH': str(Path.cwd() / 'src'),
    }
    issued = subprocess.run([
        sys.executable, '-m', 'project_control.cli', 'admin', 'prepare-maintenance',
        '--repo', str(fixture.repo.root), '--task', 'A', '--recipient', 'test-operator-a',
    ], cwd=str(Path.cwd()), env=issuer_environment, text=True, capture_output=True, check=False)
    assert issued.returncode == 0, issued.stderr
    assignment = json.loads(issued.stdout)
    # V2Repo deliberately puts its disposable authority outside the repository.
    # This test-only locator is unrelated to runtime binding; no inherited
    # runtime/search-path setting reaches either external process.
    fixture_state = str(fixture.repo.state_root)
    async def public_call(command, name, arguments):
        environment = {**command['environment'], 'TODO_ORCHESTRATOR_STATE_DIR': fixture_state}
        parameters = StdioServerParameters(command=command['executable'], args=command['arguments'], env=environment, cwd=str(Path.cwd()))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                assert not result.isError, result.content
                return json.loads(result.content[0].text)

    operator = assignment['operator_launch']
    maintained = asyncio.run(public_call(operator, 'maintain_execution', {
        'repo_root': str(fixture.repo.root), 'authorization_id': assignment['assignment']['grant_reference'],
    }))
    ordinary = {'executable': operator['executable'], 'arguments': ['-m', 'project_control.cli', 'codex'], 'environment': operator['environment']}
    missing = asyncio.run(public_call(ordinary, 'maintain_execution', {
        'repo_root': str(fixture.repo.root), 'authorization_id': assignment['assignment']['grant_reference'],
    }))
    resumed = asyncio.run(public_call(ordinary, 'next_task', maintained['recommended_next_call']['arguments']))
    print(json.dumps({'assignment': assignment['status'], 'missing': missing['reason'], 'maintained': maintained['status'], 'resumed': resumed['status'], 'task': maintained['recommended_next_call']['arguments']['task_id']}))
finally:
    fixture.tearDown()
"""
        environment = dict(os.environ)
        for key in ("PROJECT_CONTROL_RELEASE_MANIFEST", "PROJECT_CONTROL_RELEASE_DIGEST",
                    "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT", "CODING_WORKFLOW_RUNTIME_FINGERPRINT",
                    "PROJECT_CONTROL_SKILLS_ROOT", "PROJECT_CONTROL_OBSERVER_SKILLS_ROOT",
                    "OBSERVER_SKILLS_ROOT", "CODING_WORKFLOW_SKILLS_ROOT"):
            environment.pop(key, None)
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        completed = subprocess.run(
            [sys.executable, "-c", child], cwd=PROJECT_ROOT, env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual("launch_required", result["assignment"])
        self.assertEqual("maintenance_host_unconfigured", result["missing"])
        self.assertEqual("maintained", result["maintained"])
        self.assertEqual("claimed", result["resumed"])
        self.assertEqual("A", result["task"])


if __name__ == "__main__":
    unittest.main()
