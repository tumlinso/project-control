"""Operator GPU policy reaches the lazy native constructor without GPU work."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_control import observer_analysis as module

GPU_A = 'GPU-cf22c41f-5b58-77b1-3535-8fadd1ca6505'
GPU_B = 'GPU-6c1cac7f-a360-0aef-ba98-2828bfd1db1a'


@pytest.fixture
def native_constructor(tmp_path, monkeypatch):
    root = tmp_path / 'skills' / 'local-coding-worker'
    (root / 'local_worker').mkdir(parents=True)
    (root / 'config').mkdir()
    profile = root / 'config/production-profile.toml'
    profile.write_text('[deployment_policy]\nmax_real_workers = 1\n'
                       '[compute_profiles]\nnarrow = "installed-qualified-model"\n'
                       '[storage]\ncache_root = "/operator/model-cache"\n'
                       '[server]\nbinary = "/operator/llama-server"\n')
    calls, imports = [], []
    source = root / 'local_worker/supervisor.py'
    source.write_text('# release-bound supervisor\n')
    state = tmp_path / 'private'
    class Client:
        def __init__(self, repo_root, *, root):
            calls.append({'root': repo_root, 'runtime': root})
        def observer_status(self, **kwargs):
            return {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1',
                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'service_state_root': str(state), 'runtime_root': str(state / 'runtime'),
                    'observer_only': True, 'supervisor_pid': 42, 'supervisor_process_start': '123',
                    'allowed_gpu_uuids': native.allowed_gpu_uuids}
    def forbidden_backend(*args, **kwargs):
        raise AssertionError('frontend must never construct ProductionBackend')
    native = SimpleNamespace(__file__=str(source), SupervisorClient=Client,
                             ProductionBackend=forbidden_backend, allowed_gpu_uuids=[])
    def load(name):
        imports.append(name)
        assert name == 'local_worker.supervisor'
        return native
    monkeypatch.setattr(module.importlib, 'import_module', load)
    # The unit fake represents a receiver-owned supervisor module. Skills is
    # no longer the runtime owner, so bind this synthetic module to its own
    # explicit package root while retaining the provider's containment check.
    monkeypatch.setattr(module, 'bind_local_runtime',
                        lambda: SimpleNamespace(package_root=root / 'local_worker'))
    monkeypatch.setenv('PROJECT_CONTROL_SKILLS_ROOT', str(root.parent))
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR', str(tmp_path / 'private'))
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', raising=False)
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256', raising=False)
    return root, profile, calls, imports, native


@pytest.mark.as1_case('API-04')
def test_operator_gpu_policy_is_snapshotted_lazy_and_reused(native_constructor, monkeypatch):
    root, profile_path, calls, imports, native = native_constructor
    original_profile = profile_path.read_bytes()
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', json.dumps([GPU_A, GPU_B, GPU_A]))
    provider = module.SkillsObserverAnalysisProvider('/observed/project')
    assert calls == imports == []
    assert not (root.parents[1] / 'private').exists()
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', json.dumps([GPU_A, 'GPU-00000000-0000-0000-0000-000000000000']))
    backend = provider._get_backend()
    assert imports == ['local_worker.supervisor'] and len(calls) == 1
    call = calls[0]
    assert call == {'root': root.parents[1] / 'private', 'runtime': root.parents[1] / 'private/runtime'}
    native.allowed_gpu_uuids = [GPU_A, GPU_B]
    assert provider.central_status()['allowed_gpu_uuids'] == [GPU_A, GPU_B]
    native.allowed_gpu_uuids = [GPU_A]
    with pytest.raises(RuntimeError, match='central_supervisor_gpu_policy_mismatch'):
        provider.central_status()
    assert profile_path.read_bytes() == original_profile
    profile_path.unlink()  # Frontends never load or rewrite the daemon profile.
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', 'malformed-after-startup')
    assert provider._get_backend() is backend
    assert len(calls) == len(imports) == 1


@pytest.mark.as1_case('API-04')
def test_unset_operator_gpu_policy_keeps_native_defaults(native_constructor, monkeypatch):
    _, profile_path, calls, imports, _ = native_constructor
    provider = module.SkillsObserverAnalysisProvider()
    assert calls == imports == []
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', json.dumps([GPU_A]))
    profile_path.unlink()  # Default constructor retains native responsibility for its profile.
    provider._get_backend()
    assert len(calls) == 1
    assert set(calls[0]) == {'root', 'runtime'}
    assert provider.central_status()['allowed_gpu_uuids'] == []


@pytest.mark.as1_case('API-04')
@pytest.mark.parametrize('raw', ['', 'not-json', 'null', '{}', '[]', '"GPU-uuid"',
    '[1]', '[true]', '[null]', '["0","3"]', '["GPU-not-a-uuid"]',
    json.dumps([GPU_A + ' ']), json.dumps([GPU_A, 'bad'])])
def test_invalid_operator_gpu_policy_fails_before_native_import(native_constructor, monkeypatch, raw):
    _, _, calls, imports, _ = native_constructor
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', raw)
    with pytest.raises(ValueError, match='PROJECT_CONTROL_OBSERVER_GPU_UUIDS'):
        module.SkillsObserverAnalysisProvider()
    assert calls == imports == []


@pytest.mark.as1_case('API-04')
def test_operator_policy_retains_native_source_binding_check(native_constructor, monkeypatch):
    _, _, calls, _, native = native_constructor
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', json.dumps([GPU_A]))
    native.__file__ = '/ambient/local_worker/supervisor.py'
    provider = module.SkillsObserverAnalysisProvider()
    with pytest.raises(RuntimeError, match='imported_runtime_source_mismatch'):
        provider._get_backend()
    assert calls == []
