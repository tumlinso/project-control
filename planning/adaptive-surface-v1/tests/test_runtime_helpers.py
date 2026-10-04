"""Exercise helper execution; these are not AS1 product implementation tests."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from acceptance_gate import assess
import native_bridge


class RuntimeHelperTests(unittest.TestCase):
    def test_pytest_plugin_reports_execution_and_refuses_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            test = home / 'test_fixture.py'
            test.write_text('import pytest\n@pytest.mark.as1_case("PASS")\ndef test_pass():\n    assert True\n@pytest.mark.as1_case("SKIP")\n@pytest.mark.skip(reason="fixture")\ndef test_skip():\n    assert True\n')
            report = home / 'report.json'
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'scripts'), PYTHONDONTWRITEBYTECODE='1')
            p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'as1_pytest', '--as1-report', str(report), str(test)], cwd=home, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            value = json.loads(report.read_text())
            self.assertEqual(assess({'PASS'}, value, 0)['status'], 'passed')
            self.assertEqual(assess({'PASS', 'SKIP'}, value, 0)['status'], 'failed')

    def _bridge(self, *, edited_plan=False, modify=False):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory); (repo / '.todo-orchestrator').mkdir()
            (repo / '.todo-orchestrator/project.json').write_text(json.dumps({'project_uuid': 'fixture-project'}))
            plan = {'schema_version': 3, 'tasks': [{'id': 'TASK', 'title': 'Fixture'}]}
            plan_path = repo / 'plan.json'; plan_path.write_text(json.dumps(plan))
            conditions = {'revision': 17, 'identity': 'reviewed-before-apply'}
            receipt = {'format': 'pc-as1-native-validation/1', 'project': 'fixture', 'repo': str(repo), 'plan_file_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(), 'result': {'valid': True, 'project_uuid': 'fixture-project', 'would_add': ['TASK'], 'would_modify': ['TASK'] if modify else [], 'current_observation_preconditions': conditions}}
            receipt_path = repo / 'receipt.json'; receipt_path.write_text(json.dumps(receipt))
            if edited_plan:
                plan['tasks'][0]['title'] = 'Changed'; plan_path.write_text(json.dumps(plan))
            calls = []
            class Registry:
                def __init__(self, config): pass
                def repository(self, project): return types.SimpleNamespace(root=repo)
            class Conditions:
                @staticmethod
                def model_validate(value): return value
            class Proposal:
                @staticmethod
                def create(**values): return values
            def apply(config, project, proposal):
                calls.append(proposal)
                return {'status': 'applied'}
            modules = {}
            for name in ('project_control','project_control.config','project_control.registry','project_control.mutation','project_control.models'):
                modules[name] = types.ModuleType(name)
            modules['project_control'].__path__ = []
            modules['project_control.config'].load_config = lambda: 'fixture-config'
            modules['project_control.registry'].WorkspaceRegistry = Registry
            modules['project_control.mutation'].validate_native_plan = lambda *a: None
            modules['project_control.mutation'].apply_proposal = apply
            modules['project_control.models'].ProposalEnvelope = Proposal
            modules['project_control.models'].ObservationPreconditions = Conditions
            argv = ['bridge', 'apply', '--project', 'fixture', '--repo', str(repo), '--plan', str(plan_path), '--receipt', str(receipt_path)]
            output = io.StringIO()
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
                code = native_bridge.main()
            return code, json.loads(output.getvalue()), calls, conditions

    def test_native_bridge_uses_reviewed_preconditions_not_fresh_snapshot(self):
        code, result, calls, conditions = self._bridge()
        self.assertEqual(code, 0, result)
        self.assertEqual(calls[0]['observation_preconditions'], conditions)

    def test_native_bridge_refuses_changed_plan(self):
        code, result, calls, _ = self._bridge(edited_plan=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])

    def test_native_bridge_refuses_existing_task_modification(self):
        code, result, calls, _ = self._bridge(modify=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, [])

    def test_native_cycle_rules_include_parent_edges(self):
        from check_package import assert_dag
        with self.assertRaises(ValueError):
            assert_dag({'root', 'child'}, [('root', 'child'), ('child', 'root')])


if __name__ == '__main__':
    unittest.main()
