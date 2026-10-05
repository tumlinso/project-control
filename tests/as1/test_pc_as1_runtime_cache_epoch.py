"""Observer analysis cache entries are scoped to the frozen runtime epoch."""

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_surface import _analysis_runtime_identity


SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}


class UnavailableBackend:
    def __init__(self):
        self.opens = 0

    def open_sessions(self, count, **kwargs):
        self.opens += 1
        return {'status': 'unavailable', 'reason': 'gpu_role_unavailable'}


def make(tmp_path, epoch, backend):
    return JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=lambda service, job: None, backend=backend, retry_seconds=0,
        analysis_runtime_identity=epoch)


def test_runtime_epoch_invalidates_old_negative_but_reuses_same_epoch(tmp_path):
    old_backend = UnavailableBackend()
    old = make(tmp_path, 'frozen-runtime-a', old_backend).start()
    try:
        expected = {'status': 'unavailable', 'reason': 'analysis_unavailable'}
        assert old.inquire('identical question', SCOPE, foreground_timeout=3) == expected
        old_calls = old_backend.opens
        assert old_calls
        assert old.inquire('identical question', SCOPE, foreground_timeout=3) == expected
        assert old_backend.opens == old_calls
    finally:
        old.shutdown()

    # An unqualified frozen worker must not fall back to a prior qualified cache.
    unqualified = JobService(tmp_path / 'jobs',
        packets=SQLitePacketStore(tmp_path / 'packets'),
        analysis_runtime_identity='frozen-runtime-unqualified')
    fallback = unqualified.inquire('identical question', SCOPE, foreground_timeout=0)
    assert fallback == {'status': 'unavailable', 'reason': 'durable_processing_unavailable'}

    new_backend = UnavailableBackend()
    new = make(tmp_path, 'frozen-runtime-b', new_backend).start()
    try:
        # Even a scope field with the same name cannot choose the cache epoch.
        scope_with_spoof = {**SCOPE, 'analysis_runtime_identity': 'frozen-runtime-a'}
        assert new.inquire('identical question', scope_with_spoof,
                           foreground_timeout=3) == expected
        assert new_backend.opens
        with new._db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')=?",
                              ('identical question',)).fetchall()
        assert len(rows) == 2 and len({row['id'] for row in rows}) == 2
    finally:
        new.shutdown()


def test_runtime_identity_hashes_frozen_profile_and_nested_adapter(tmp_path):
    root = tmp_path / 'runtime-skills'
    files = {
        'local-coding-worker/config/production-profile.toml': b'model = "qwen"\n',
        'local-coding-worker/local_worker/servers/llama_cpp.py': b'adapter-v1',
        'local-coding-worker/local_worker/supervisor.py': b'supervisor-v1',
    }
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    first = _analysis_runtime_identity(root, 'qualified-observer-runtime')
    assert first == _analysis_runtime_identity(root, 'qualified-observer-runtime')
    assert first != _analysis_runtime_identity(root, 'qualified-observer-runtime',
        qualification_state='unavailable')
    (root / 'local-coding-worker/local_worker/servers/llama_cpp.py').write_bytes(b'adapter-v2')
    assert _analysis_runtime_identity(root, 'qualified-observer-runtime') != first
