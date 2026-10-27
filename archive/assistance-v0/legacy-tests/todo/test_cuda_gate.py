import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from todo_orchestrator import cuda_gate
from todo_orchestrator.models import TodoError
from todo_orchestrator.plan import validate_plan
from tests.todo.v2_helpers import V2Repo, base_plan, safe_task


class CudaGateQuiescenceTests(unittest.TestCase):
    def forward(self, config):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = root / 'cuda/scripts/cuda_controller.py'
            controller.parent.mkdir(parents=True)
            controller.write_text('# mock controller location\n')
            with patch.object(cuda_gate.subprocess, 'run', return_value=
                    subprocess.CompletedProcess([], 0, '{"ok":true}', '')) as execute:
                result = cuda_gate.run(['true'], root,
                    {'PROJECT_CONTROL_SKILLS_ROOT': str(root)}, config)
            self.assertEqual(result.returncode, 0)
            return json.loads(execute.call_args.kwargs['input'])

    def test_defaults_unchanged_when_override_omitted(self):
        spec = self.forward({'gpus': 1})
        self.assertNotIn('quiescence', spec)
        self.assertEqual(spec['resources'], {'gpus': 1})

    def test_override_is_top_level_not_resource_request(self):
        override = {'timeout_seconds': 60, 'consecutive_idle_samples': 3,
                    'interval_seconds': 0.2}
        spec = self.forward({'gpus': 1, 'quiescence': override})
        self.assertEqual(spec['quiescence'], override)
        self.assertEqual(spec['resources'], {'gpus': 1})

    def test_invalid_options_rejected_before_dispatch_and_by_plan_owner(self):
        invalid = [None, [], {'typo': 60}, {'timeout_seconds': -1},
                   {'timeout_seconds': 61}, {'timeout_seconds': 10**1000}, {'timeout_seconds': float('nan')},
                   {'timeout_seconds': float('inf')}, {'timeout_seconds': True},
                   {'timeout_seconds': '60'}, {'consecutive_idle_samples': 2},
                   {'consecutive_idle_samples': 65}, {'consecutive_idle_samples': 3.5},
                   {'consecutive_idle_samples': True}, {'interval_seconds': 0},
                   {'interval_seconds': 6}]
        for override in invalid:
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    self.forward({'gpus': 1, 'quiescence': override})
                plan = base_plan([safe_task('A', 'src/a')])
                plan['tasks'][0]['gates'] = [{'id': 'GPU', 'type': 'command',
                    'argv': ['true'], 'cuda': {'gpus': 1, 'quiescence': override}}]
                with self.assertRaises(TodoError):
                    validate_plan(plan)

    def test_plan_accepts_bounded_override(self):
        plan = base_plan([safe_task('A', 'src/a')])
        plan['tasks'][0]['gates'] = [{'id': 'GPU', 'type': 'command',
            'argv': ['true'], 'cuda': {'gpus': 1, 'quiescence': {'timeout_seconds': 60}}}]
        self.assertTrue(validate_plan(plan)['valid'])

    def test_native_plan_apply_preserves_override_and_rejects_invalid_update(self):
        repo = V2Repo()
        try:
            plan = base_plan([safe_task('A', 'src/a')])
            config = {'gpus': 1, 'quiescence': {'timeout_seconds': 60}}
            plan['tasks'][0]['gates'] = [{'id': 'GPU', 'type': 'command',
                'argv': ['true'], 'cuda': config}]
            repo.apply(plan)
            with repo.service.db.read() as conn:
                before = dict(conn.execute("SELECT * FROM gates WHERE id='GPU'").fetchone())
            self.assertEqual(json.loads(before['config_json'])['cuda'], config)
            config['quiescence']['timeout_seconds'] = 61
            with self.assertRaises(TodoError):
                repo.apply(plan)
            with repo.service.db.read() as conn:
                after = dict(conn.execute("SELECT * FROM gates WHERE id='GPU'").fetchone())
            self.assertEqual(before, after)
        finally:
            repo.close()

    def test_slow_idle_samples_keep_three_sample_requirement(self):
        import importlib.util
        module_path = Path.home() / '.agents' / 'skills' / 'cuda' / 'scripts' / 'cuda_quiescence.py'
        spec = importlib.util.spec_from_file_location('quiescence_test_owner', module_path)
        owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(owner)
        def proof(timeout):
            clock = [0.0]
            def sample(_):
                clock[0] += 7.0
                return {'idle': True, 'busy': False, 'foreign_processes': False}
            return owner.prove_quiescence(['GPU-test'], sample, timeout_seconds=timeout,
                monotonic=lambda: clock[0], sleep=lambda dt: clock.__setitem__(0, clock[0]+dt))
        short = proof(10)
        self.assertEqual(short['state'], 'timeout')
        self.assertEqual(short['samples_observed'], 2)
        extended = proof(60)
        self.assertTrue(extended['uncontaminated'])
        self.assertEqual(extended['required_consecutive_samples'], 3)
        self.assertEqual(extended['observed_consecutive_samples'], 3)


if __name__ == '__main__':
    unittest.main()
