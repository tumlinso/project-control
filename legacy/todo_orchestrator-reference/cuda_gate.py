"""Execute an explicitly GPU-bound gate through the canonical CUDA controller."""
from __future__ import annotations
import json
import math
from pathlib import Path
import subprocess
import sys


def validate_quiescence(config: object) -> None:
    """Validate gate overrides without relaxing the three-idle-sample proof."""
    allowed = {'timeout_seconds', 'consecutive_idle_samples', 'interval_seconds'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('Unsupported GPU gate quiescence configuration')
    for key, lower, upper in [('timeout_seconds', 0.0, 60.0),
                              ('interval_seconds', 0.01, 5.0)]:
        if key in config:
            value = config[key]
            if (type(value) not in (int, float) or not lower <= value <= upper
                    or not math.isfinite(value)):
                raise ValueError(f'GPU gate quiescence {key} must be finite in [{lower}, {upper}]')
    if 'consecutive_idle_samples' in config:
        value = config['consecutive_idle_samples']
        if type(value) is not int or not 3 <= value <= 64:
            raise ValueError('GPU gate quiescence consecutive_idle_samples must be an integer in [3, 64]')


def run(argv: list[str], cwd: Path, environment: dict[str, str], config: dict, timeout: float = 3600) -> subprocess.CompletedProcess:
    skills = environment.get('PROJECT_CONTROL_SKILLS_ROOT')
    if not skills:
        raise ValueError('GPU gate requires the configured Project Control Skills runtime')
    controller = Path(skills) / 'cuda/scripts/cuda_controller.py'
    if not controller.is_file():
        raise ValueError('Canonical CUDA controller is unavailable')
    allowed = {'gpus', 'gpu_uuids', 'cpu_threads', 'isolate_pcie_root', 'isolate_nvlink_domain', 'toolchain', 'build_argv', 'binary_paths', 'quiescence'}
    if set(config) - allowed:
        raise ValueError('Unsupported GPU gate fields: ' + ', '.join(sorted(set(config) - allowed)))
    if int(config.get('gpus', 1)) < 1:
        raise ValueError('GPU gates require at least one actual accelerator')
    spec = {'schema_version': 1, 'project_root': str(cwd), 'recipe': 'baseline',
            'campaign_id': 'workflow-required-gate', 'argv': argv, 'timeout': timeout, 'command_cwd': str(cwd),
            'resources': {k: v for k, v in config.items() if k not in {'toolchain', 'build_argv', 'binary_paths', 'quiescence'}},
            'toolchain': config.get('toolchain', {'require_sanitizer': True})}
    if 'quiescence' in config:
        validate_quiescence(config['quiescence'])
        spec['quiescence'] = dict(config['quiescence'])
    spec['binary_paths'] = config.get('binary_paths', [])
    if 'build_argv' in config:
        spec['benchmark'] = {'build_argv': config['build_argv']}
    result = subprocess.run([sys.executable, str(controller), 'run', '--spec', '-', '--json'],
                            input=json.dumps(spec), cwd=cwd, env=environment, text=True, capture_output=True)
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        return subprocess.CompletedProcess(argv, 1, result.stdout, result.stderr + "\nInvalid CUDA controller receipt")
    stdout = Path(receipt['stdout_path']).read_text() if receipt.get('stdout_path') else result.stdout
    stderr = Path(receipt['stderr_path']).read_text() if receipt.get('stderr_path') else result.stderr
    stderr += '\nCUDA gate receipt: ' + json.dumps(receipt, sort_keys=True) + '\n'
    return subprocess.CompletedProcess(argv, 0 if receipt.get('ok') is True and result.returncode == 0 else 1, stdout, stderr)
