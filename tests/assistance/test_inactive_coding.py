"""The receiver CLI keeps coding and mutation paths unavailable unconditionally."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "src/project_control/local_runtime/scripts/local_worker.py"


def _run_cli_sentinel_script() -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        """
        import contextlib
        import io
        import json
        import importlib.util
        import sys
        from pathlib import Path

        cli_path = Path('src/project_control/local_runtime/scripts/local_worker.py').resolve()
        sys.path.insert(0, str(cli_path.parent))  # same import context as direct script execution
        spec = importlib.util.spec_from_file_location('inactive_receiver_test', cli_path)
        cli = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cli
        spec.loader.exec_module(cli)

        def forbidden(*args, **kwargs):
            raise AssertionError('inactive coding handler was invoked')

        class ForbiddenController:
            def __init__(self, *args, **kwargs):
                forbidden()

        cli.IntegrationController = ForbiddenController
        cli._launch_delegate = forbidden
        cli._run_detached_delegate = forbidden
        cli.run_controller = forbidden
        cli.host_check = forbidden
        cli.evaluate = forbidden
        cli.release_check = forbidden
        cli._request = forbidden
        cli._load_receiver_runtime = forbidden

        denied = [
            ['run', '--request', '/must/not/be/read.json'],
            ['integrate', '--request', '/must/not/be/read.json'],
            ['delegate', '--claim-token', 'claim', '--mode', 'auto'],
            ['_delegate-worker', '--request', '/must/not/be/read.json'],
            ['self-test', '--repo', '/must/not/be/used'],
            ['host-check', '--scenario', 'readonly'],
            ['evaluate', '--phase', 'focused'],
            ['release-check', '--phase', 'integrated'],
            ['service', 'warm'],
            ['model-cache', 'install', '--candidate-id', 'candidate'],
            ['model-cache', 'activate', '--candidate-id', 'candidate'],
            ['model-cache', 'remove', '--candidate-id', 'candidate'],
        ]
        for argv in denied:
            sys.argv = [str(cli_path), *argv]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli.main()
            assert status == 2, (argv, status, output.getvalue())
            response = json.loads(output.getvalue())
            assert response['error'] == cli.INACTIVE_COMMAND_ERROR, (argv, response)

        class ReadOnlySupervisor:
            def __init__(self, *args, **kwargs):
                pass
            def request(self, command):
                assert command == 'status'
                return {'status': 'inspected'}

        class ReadOnlyCache:
            def active(self): return None
            def inspect(self): return {'inspection': 'ok'}
            def list(self): return []
            def verify(self, *args, **kwargs): return {'ready': True}

        def load_read_only_fakes(*, worker_core=False):
            assert worker_core is (sys.argv[1] == 'eligible')
            cli.SupervisorClient = ReadOnlySupervisor
            cli._model_cache = lambda: ReadOnlyCache()
            cli._request = lambda path: {'format': 'LCW-REQUEST/1'}
            cli.eligibility = lambda request: {'eligible': True}
            cli.validate_policy = lambda: {'policy': 'inspected'}
            cli.IntegrationError = RuntimeError

        cli._load_receiver_runtime = load_read_only_fakes

        allowed = [
            ['eligible', '--request', '-'],
            ['service', 'status'],
            ['model-cache', 'inspect'],
            ['model-cache', 'list'],
            ['model-cache', 'verify', '--candidate-id', 'candidate',
             '--payload-sha256', '0' * 64, '--quick'],
            ['policy', 'validate'],
        ]
        for argv in allowed:
            sys.argv = [str(cli_path), *argv]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = cli.main()
            assert status == 0, (argv, status, output.getvalue())
            assert json.loads(output.getvalue()), (argv, output.getvalue())
        print('inactive commands denied before handlers; read-only status/inspection routes preserved')
        """
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


class InactiveCodingTests(unittest.TestCase):
    def test_actual_cli_main_denies_coding_and_mutation_before_handlers(self):
        result = _run_cli_sentinel_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("read-only status/inspection routes preserved", result.stdout)
