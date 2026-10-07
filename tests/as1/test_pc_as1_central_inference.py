"""CPU-only central-owner contract and real Unix transport qualification."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from project_control.observer_analysis import SkillsObserverAnalysisProvider
from project_control.runtime_binding import _verify_receiver


RECEIVER_SOURCE = Path(__file__).resolve().parents[2] / 'src/project_control/local_runtime'


def receiver_fixture():
    # Source checkouts bind the package-owned receiver without an installed
    # release manifest. Keep tests on that exact src/project_control shape.
    return _verify_receiver(RECEIVER_SOURCE)


@pytest.fixture
def central(monkeypatch, tmp_path):
    identity = receiver_fixture()
    source = identity.package_root / 'supervisor.py'
    state = tmp_path / 'state'
    monkeypatch.delenv('PROJECT_CONTROL_SKILLS_ROOT', raising=False)
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR', str(state))
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256', raising=False)
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', raising=False)
    status = {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1',
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'service_state_root': str(state), 'runtime_root': str(state / 'runtime'),
              'observer_only': True, 'supervisor_pid': 42, 'supervisor_process_start': '123',
              'daemon_epoch': 'f' * 64, 'runtime_fingerprint': 'e' * 64,
              'allowed_gpu_uuids': [], 'model_id': 'warm-model'}
    client = Mock()
    client._observer_owner = (42, '123')
    client._observer_daemon_epoch = 'f' * 64
    client.observer_status.return_value = status
    client.run_observer_turn.return_value = {'status': 'available', 'model_id': 'warm-model'}
    module = SimpleNamespace(__file__=str(source), SupervisorClient=Mock(return_value=client),
                             ProductionBackend=Mock(side_effect=AssertionError('frontend backend forbidden')))
    monkeypatch.setattr('project_control.observer_analysis.bind_local_runtime', lambda: identity)
    monkeypatch.setattr('project_control.observer_analysis.importlib.import_module', lambda _: module)
    return module, client, status, state


def turn(provider, **kwargs):
    return provider.investigate_turn({'messages': [{'role': 'user', 'content': 'bounded'}], **kwargs})


def test_frontend_never_constructs_backend_and_close_keeps_owner_warm(central):
    module, client, status, state = central
    narrow, wide = SkillsObserverAnalysisProvider(), SkillsObserverAnalysisProvider()
    assert turn(narrow, compute_profile='narrow')['model_id'] == 'warm-model'
    assert turn(wide, compute_profile='wide')['model_id'] == 'warm-model'
    assert module.SupervisorClient.call_args.args == (state,)
    assert module.SupervisorClient.call_args.kwargs == {'root': state / 'runtime'}
    narrow.close()
    client.close.assert_called_once_with()
    client.poll.assert_not_called()
    module.ProductionBackend.assert_not_called()
    assert turn(wide)['model_id'] == 'warm-model'


def test_reasoning_mode_is_forwarded_and_validated_at_frontend(central):
    _, client, _, _ = central
    assert turn(SkillsObserverAnalysisProvider(), reasoning_mode='off')['status'] == 'available'
    request = client.run_observer_turn.call_args.args[0]
    assert request['reasoning_mode'] == 'off'
    assert request['max_tokens'] == 2048
    client.run_observer_turn.reset_mock()
    assert turn(SkillsObserverAnalysisProvider(), reasoning_mode='hidden') == {
        'status': 'unavailable', 'reason': 'local_investigator_reasoning_mode_invalid'}
    client.run_observer_turn.assert_not_called()


def test_trusted_policy_id_is_forwarded_without_caller_budget_overrides(central):
    _, client, _, _ = central
    result = turn(SkillsObserverAnalysisProvider(), turn_policy_id='gather-v1',
                  max_tokens='caller-controlled-invalid-budget', reasoning_mode='hidden')
    assert result['status'] == 'available'
    request = client.run_observer_turn.call_args.args[0]
    assert request['turn_policy_id'] == 'gather-v1'
    assert 'max_tokens' not in request
    assert 'reasoning_mode' not in request
    client.run_observer_turn.reset_mock()

    invalid = turn(SkillsObserverAnalysisProvider(), turn_policy_id='gather-v999')
    assert invalid == {'status': 'unavailable', 'reason': 'observer_turn_policy_unknown'}
    client.run_observer_turn.assert_not_called()


def test_legacy_turn_without_policy_keeps_existing_budget_contract(central):
    _, client, _, _ = central
    assert turn(SkillsObserverAnalysisProvider(), max_tokens=321, reasoning_mode='off')['status'] == 'available'
    request = client.run_observer_turn.call_args.args[0]
    assert request['max_tokens'] == 321
    assert request['reasoning_mode'] == 'off'
    assert 'turn_policy_id' not in request


def test_release_idle_runtime_requires_physical_cleanup_receipts(central):
    _, client, status, _ = central
    gpu = 'GPU-12345678-1234-1234-1234-123456789abc'
    status.update(active_leases=0, active_admissions=0, slots=[{
        'slot_id': 'slot-a', 'state': 'idle', 'leased': False,
        'owner_id': 'owner-a', 'server_pid': 987, 'gpu_uuids': [gpu],
    }])
    client.stop_if_quiescent.return_value = {
        'stopped': True, 'evicted': True, 'quiescent': True,
        'supervisor_pid': 42, 'supervisor_process_start': '123', 'daemon_epoch': 'f' * 64,
        'cleanup_receipts': [{
            'owner_id': 'owner-a', 'owned_pid': 987, 'gpu_uuids': [gpu],
            'released': True, 'process_released': True, 'memory_released': True,
        }],
    }
    result = SkillsObserverAnalysisProvider().release_idle_runtime(deadline_epoch=time.time() + 30)
    assert result['status'] == 'released_verified'
    assert result['released_verified'] is True
    assert result['stopped'] is True
    assert result['supervisor_pid'] == 42
    assert result['supervisor_process_start'] == '123'
    assert result['daemon_epoch'] == 'f' * 64
    assert result['runtime_fingerprint'] == 'e' * 64
    client.stop_if_quiescent.assert_called_once()


def test_release_idle_runtime_refuses_failed_cleanup_receipt(central):
    _, client, status, _ = central
    gpu = 'GPU-12345678-1234-1234-1234-123456789abc'
    status.update(active_leases=0, active_admissions=0, slots=[{
        'slot_id': 'slot-a', 'state': 'idle', 'leased': False,
        'owner_id': 'owner-a', 'server_pid': 987, 'gpu_uuids': [gpu],
    }])
    client.stop_if_quiescent.return_value = {
        'stopped': True, 'evicted': False, 'quiescent': False,
        'cleanup_receipts': [{
            'owner_id': 'owner-a', 'owned_pid': 987, 'gpu_uuids': [gpu],
            'released': False, 'process_released': False, 'memory_released': False,
        }],
    }
    with pytest.raises(RuntimeError, match='observer_runtime_release_not_quiescent'):
        SkillsObserverAnalysisProvider().release_idle_runtime(deadline_epoch=time.time() + 30)


def test_release_idle_runtime_never_stops_leased_or_foreign_slot(central):
    _, client, status, _ = central
    status.update(active_leases=0, active_admissions=0, slots=[{
        'slot_id': 'foreign-slot', 'state': 'active', 'leased': True,
        'owner_id': 'foreign-owner', 'server_pid': 987,
        'gpu_uuids': ['GPU-12345678-1234-1234-1234-123456789abc'],
    }])
    with pytest.raises(RuntimeError, match='observer_runtime_release_slot_not_idle'):
        SkillsObserverAnalysisProvider().release_idle_runtime(deadline_epoch=time.time() + 30)
    client.stop_if_quiescent.assert_not_called()


def test_release_idle_runtime_proves_empty_pool_without_stopping_owner(central):
    _, client, status, _ = central
    status.update(active_leases=0, active_admissions=0, slots=[])
    result = SkillsObserverAnalysisProvider().release_idle_runtime(deadline_epoch=time.time() + 30)
    assert result == {'status': 'released', 'quiescent': True, 'evicted': True,
                      'stopped': False, 'already_empty': True, 'cleanup_receipts': []}
    client.stop_if_quiescent.assert_not_called()


def test_turn_transport_envelope_allows_extended_context_without_changing_public_answer_cap(central):
    _, client, _, _ = central
    result = SkillsObserverAnalysisProvider().investigate_turn({
        'messages': [{'role': 'user', 'content': 'x' * (300 * 1024)}],
        'max_tokens': 2048, 'reasoning_mode': 'auto'})
    assert result['status'] == 'available'
    assert len(client.run_observer_turn.call_args.args[0]['messages'][0]['content']) == 300 * 1024


@pytest.mark.parametrize(('field', 'replacement', 'reason'), [
    ('service_state_root', '/foreign', 'central_supervisor_root_mismatch'),
    ('runtime_root', '/foreign', 'central_supervisor_root_mismatch'),
    ('source_sha256', '0' * 64, 'central_supervisor_source_mismatch'),
    ('observer_contract', 'unknown', 'central_supervisor_contract_mismatch'),
    ('observer_only', False, 'central_supervisor_ownership_mismatch'),
    ('supervisor_pid', 0, 'central_supervisor_ownership_invalid'),
])
def test_owner_mismatch_fails_closed(central, field, replacement, reason):
    module, client, status, _ = central
    status[field] = replacement
    assert turn(SkillsObserverAnalysisProvider())['reason'] == reason
    client.run_observer_turn.assert_not_called()
    module.ProductionBackend.assert_not_called()


def test_missing_and_timeout_are_not_global_capacity_busy(central):
    module, client, _, _ = central
    for error, reason in [(FileNotFoundError(), 'central_supervisor_unavailable'),
                          (TimeoutError(), 'central_supervisor_transport_timeout')]:
        client.observer_status.side_effect = error
        assert turn(SkillsObserverAnalysisProvider())['reason'] == reason
    module.ProductionBackend.assert_not_called()


def test_deadline_and_gpu_operator_pin(central, monkeypatch):
    _, client, status, _ = central
    uuid = 'GPU-12345678-1234-1234-1234-123456789abc'
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_GPU_UUIDS', json.dumps([uuid]))
    provider = SkillsObserverAnalysisProvider()
    assert turn(provider)['reason'] == 'central_supervisor_gpu_policy_mismatch'
    status['allowed_gpu_uuids'] = [uuid]
    deadline = time.time() + .1
    assert turn(provider, deadline_epoch=deadline)['status'] == 'available'
    assert client.observer_status.call_args.kwargs['deadline_epoch'] <= deadline
    assert client.run_observer_turn.call_args.args[0]['deadline_epoch'] <= deadline


def test_two_process_clients_share_unix_owner_and_total_ipc_deadline(tmp_path):
    """Receiver-bound clients share the warm fake owner; no model or GPU launch."""
    identity = receiver_fixture()
    receiver = identity.root
    state = tmp_path / 'state'
    runtime = state / 'runtime'
    runtime.mkdir(parents=True, mode=0o700)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(runtime / 'supervisor.sock'))
    (runtime / 'supervisor.sock').chmod(0o600)
    server.listen()
    stop = threading.Event()
    status = {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1',
              'runtime_identity': {'fixture': True}, 'observer_only': True,
              'supervisor_pid': os.getpid(), 'supervisor_process_start': Path('/proc/self/stat').read_text().split(')')[-1].split()[19],
              'daemon_epoch': 'a' * 64,
              'receiver_manifest_sha256': identity.manifest_sha256,
              'receiver_fingerprint': identity.fingerprint,
              'receiver_source_commit': identity.source_commit,
              'source_sha256': hashlib.sha256((identity.package_root / 'supervisor.py').read_bytes()).hexdigest(),
              'service_state_root': str(state), 'runtime_root': str(runtime), 'allowed_gpu_uuids': [],
              'slots': [{'server_pid': 987, 'owner_id': 'single-owner', 'model_id': 'warm', 'model_sha256': 'a' * 64}]}
    calls = []
    def serve():
        while not stop.is_set():
            connection, _ = server.accept()
            with connection:
                data = b''
                while b'\n' not in data:
                    block = connection.recv(4096)
                    if not block:
                        break
                    data += block
                if not data:
                    continue
                request = json.loads(data)
                calls.append(request)
                if request['operation'] == 'observer-status':
                    result = status
                elif request.get('request', {}).get('messages', [{}])[0].get('content') == 'stall':
                    # Delayed transport must obey the caller's entire deadline.
                    time.sleep(.3)
                    result = {'status': 'available'}
                else:
                    result = {'status': 'available', 'owner_id': 'single-owner', 'model_id': 'warm', 'server_pid': 987}
                try:
                    connection.sendall(json.dumps({'ok': True, 'data': result}).encode() + b'\n')
                except BrokenPipeError:
                    pass
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    script = '''
import json, time
import os, sys
from pathlib import Path
from unittest.mock import Mock
import project_control.observer_analysis as observer_analysis
from project_control.runtime_binding import _verify_receiver
receiver = Path(os.environ['PA1_RECEIVER_ROOT'])
identity = _verify_receiver(receiver)
for name in tuple(sys.modules):
 if name == 'local_worker' or name.startswith('local_worker.'):
  del sys.modules[name]
sys.path.insert(0, str(receiver))
observer_analysis.bind_local_runtime = lambda: identity
import local_worker.supervisor as module
module.bind_canonical_runtime = lambda root: (object(), {'fixture': True})
module.validate_canonical_runtime = lambda identity: None
module.ProductionBackend = Mock(side_effect=AssertionError('no backend'))
from project_control.observer_analysis import SkillsObserverAnalysisProvider
provider = SkillsObserverAnalysisProvider()
result = provider.investigate_turn({'messages': [{'role': 'user', 'content': CONTENT}], 'compute_profile': PROFILE, 'deadline_epoch': time.time()+BUDGET})
provider.close()
assert not module.ProductionBackend.called
print(json.dumps(result))
'''
    environment = dict(os.environ, PA1_RECEIVER_ROOT=str(receiver),
                       PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR=str(state),
                       PYTHONPATH=os.pathsep.join([str(receiver), str(Path(__file__).resolve().parents[2] / 'src')]))
    for key in ('PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256', 'PROJECT_CONTROL_OBSERVER_GPU_UUIDS', 'PROJECT_CONTROL_SKILLS_ROOT'):
        environment.pop(key, None)
    try:
        results = []
        for profile in ('narrow', 'wide'):
            code = script.replace('CONTENT', repr('bounded')).replace('PROFILE', repr(profile)).replace('BUDGET', '2')
            completed = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, timeout=5, check=True)
            results.append(json.loads(completed.stdout))
        assert results[0] == results[1] == {'status': 'available', 'owner_id': 'single-owner', 'model_id': 'warm', 'server_pid': 987}
        code = script.replace('CONTENT', repr('stall')).replace('PROFILE', repr('wide')).replace('BUDGET', '.1')
        completed = subprocess.run([sys.executable, '-c', code], env=environment, capture_output=True, text=True, timeout=5, check=True)
        assert json.loads(completed.stdout)['reason'] == 'central_supervisor_transport_timeout'
        assert {c['operation'] for c in calls} <= {'observer-status', 'observer-turn'}
    finally:
        stop.set()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
            wake.connect(str(runtime / 'supervisor.sock'))
        thread.join(2)
        server.close()


def test_http_health_and_readiness_require_validated_central_owner(monkeypatch, tmp_path):
    from starlette.testclient import TestClient
    from project_control import app
    from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
    backend = Mock()
    backend.central_status.return_value = {'observer_contract': 'PC-OBSERVER-SUPERVISOR/1',
                                           'supervisor_pid': 42, 'supervisor_process_start': '123',
                                           'source_sha256': 'a' * 64}
    composition = Mock(backend=backend)
    composition.jobs.health.return_value = {'status': 'ok'}
    monkeypatch.setattr('project_control.as1_surface.compose_surface', lambda *args, **kwargs: composition)
    monkeypatch.setattr('project_control.as1_surface.register_surface', lambda *args: None)
    monkeypatch.setattr(app, 'todo_read_port_factory', lambda: None)
    config = ProjectControlConfig(workspaces={'demo': WorkspaceConfig(
        repositories={'source': RepositoryConfig(root=tmp_path)})})
    with TestClient(app.create_asgi_app(config)) as client:
        assert client.get('/readyz').status_code == 200
        assert client.get('/healthz').json()['central_inference']['supervisor_pid'] == 42
        backend.central_status.side_effect = RuntimeError('central_supervisor_unavailable')
        ready = client.get('/readyz')
        assert ready.status_code == 200
        assert ready.json()['core']['status'] == 'available'
        assert ready.json()['central_inference']['reason'] == 'central_supervisor_unavailable'
        assert client.get('/healthz').json()['central_inference']['status'] == 'unavailable'
    composition.close.assert_called_once()


def test_operator_source_pin_and_module_binding_fail_before_client(central, monkeypatch):
    module, _, _, _ = central
    monkeypatch.setenv('PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256', '0' * 64)
    assert turn(SkillsObserverAnalysisProvider())['reason'] == 'central_supervisor_source_mismatch'
    module.SupervisorClient.assert_not_called()
    monkeypatch.delenv('PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256')
    module.__file__ = '/untrusted/supervisor.py'
    assert turn(SkillsObserverAnalysisProvider())['reason'] == 'observer_analysis_runtime_binding_invalid'
    module.SupervisorClient.assert_not_called()


@pytest.mark.parametrize('remaining', [300, 10, None])
def test_session_admission_keeps_whole_job_deadline(central, monkeypatch, remaining):
    _, client, _, _ = central
    client.open_observer_sessions.return_value = {'status': 'available', 'session_ids': ['owned']}
    monkeypatch.setattr(time, 'time', lambda: 1000.0)
    deadline = 1000.0 + remaining if remaining is not None else None
    provider = SkillsObserverAnalysisProvider()
    assert provider.open_sessions(1, compute_profile='narrow', parallelism='default',
                                  deadline_epoch=deadline)['status'] == 'available'
    # Admission and lease expiry span the full accepted question, not one turn.
    assert client.open_observer_sessions.call_args.kwargs['deadline_epoch'] == (deadline or 1300.0)
    assert client.observer_status.call_args.kwargs['deadline_epoch'] == min(deadline or 1300.0, 1002.0)
    assert turn(provider, deadline_epoch=1300.0)['status'] == 'available'
    assert client.run_observer_turn.call_args.args[0]['deadline_epoch'] == 1060.0
