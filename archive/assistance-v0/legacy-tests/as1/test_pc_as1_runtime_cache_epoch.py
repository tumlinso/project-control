"""Observer analysis cache entries are scoped to the receiver runtime epoch."""

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from project_control.as1_jobs import JobService
from project_control.as1_packets import SQLitePacketStore
import project_control.as1_surface as surface
from project_control.as1_surface import _analysis_runtime_identity
from project_control.runtime_binding import _verify_receiver, local_runtime_identity


SCOPE = {'principal': 'alice', 'profile': 'observer', 'project': 'pc'}
ROOT = Path(__file__).resolve().parents[2]
RECEIVER = ROOT / 'src/project_control/local_runtime'
PRODUCER_FILES = (
    'as1_jobs.py', 'as1_surface.py', 'as1_skill.py',
    'as1_context.py', 'as1_packets.py', 'assistance/knowledge.py',
    'assistance/frames.py', 'assistance/power.py',
    'assistance/policies.py', 'assistance/resources.py',
)


@pytest.fixture(autouse=True)
def _use_source_runtime(monkeypatch):
    """Keep source tests independent of a host's installed-release pins."""
    monkeypatch.delenv('PROJECT_CONTROL_RELEASE_MANIFEST', raising=False)
    monkeypatch.delenv('PROJECT_CONTROL_RELEASE_DIGEST', raising=False)


def _prepare_receiver_fixture(root):
    """Copy the complete canonical inventory into an independently verified fixture."""
    root.mkdir(parents=True)
    source_manifest = json.loads((RECEIVER / 'receiver-manifest.json').read_text())
    for relative in source_manifest['files']:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RECEIVER / relative, target)
    source_manifest['source_root'] = 'fixture://project-control/local-runtime'
    source_manifest['source_commit'] = 'fixture-base'
    (root / 'receiver-manifest.json').write_text(json.dumps(source_manifest, sort_keys=True))
    _verify_receiver(root)
    return root


def _refresh_fixture_manifest(root):
    manifest_path = root / 'receiver-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'] = {
        relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for relative in manifest['files']
    }
    fingerprint_material = json.dumps(manifest['files'], sort_keys=True, separators=(',', ':')).encode()
    manifest['source_commit'] = 'fixture-' + hashlib.sha256(fingerprint_material).hexdigest()[:16]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    _verify_receiver(root)


def _fixture_epoch(root, monkeypatch):
    monkeypatch.setattr(surface, 'local_runtime_identity',
        lambda *, root=None: _verify_receiver(Path(root)))
    observer = root / 'local_worker/observer_runtime.py'
    digest = hashlib.sha256(observer.read_bytes()).hexdigest()
    return _analysis_runtime_identity(root, digest)


class TerminalFailureBackend:
    def __init__(self):
        self.opens = 0

    def open_sessions(self, count, **kwargs):
        self.opens += 1
        return {'status': 'available', 'session_ids': ['fixture-session']}

    def close_session(self, session_id):
        return {'status': 'closed', 'released': True}


class TerminalFailureWorker:
    """Deterministic model-free terminal result for cache invalidation tests."""

    def run(self, request):
        return {'status': 'failed', 'reason': 'fixture_terminal_failure'}


def make(tmp_path, epoch, backend):
    return JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=lambda service, job: None, backend=backend, retry_seconds=0,
        analysis_runtime_identity=epoch)


def test_runtime_epoch_invalidates_old_negative_but_reuses_same_epoch(tmp_path, monkeypatch):
    identity = local_runtime_identity()
    observer = identity.root / 'local_worker/observer_runtime.py'
    observer_digest = hashlib.sha256(observer.read_bytes()).hexdigest()
    verified_manifest = json.loads((identity.root / 'receiver-manifest.json').read_text())
    assert verified_manifest['files']['local_worker/observer_runtime.py'] == observer_digest
    assert identity.file_count == len(verified_manifest['files'])

    identity_material = []
    canonical_digest = surface.canonical_digest
    monkeypatch.setattr(surface, 'canonical_digest',
        lambda value: identity_material.append(value) or canonical_digest(value))
    epoch_a = _analysis_runtime_identity(identity.root, observer_digest)
    assert identity_material[-1]['source_verification_state'] == 'source_verified'
    assert identity_material[-1]['live_qualification'] is None
    next_receiver = _prepare_receiver_fixture(tmp_path / 'next-runtime')
    config = next_receiver / 'config/production-profile.toml'
    config.write_bytes(config.read_bytes() + b'\n# new cache epoch\n')
    _refresh_fixture_manifest(next_receiver)
    monkeypatch.setattr(surface, 'local_runtime_identity',
        lambda *, root=None: _verify_receiver(Path(root)))
    epoch_b = _analysis_runtime_identity(next_receiver, observer_digest)
    old_backend = TerminalFailureBackend()
    old = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=lambda service, job: TerminalFailureWorker(), backend=old_backend,
        retry_seconds=0, analysis_runtime_identity=epoch_a).start()
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

    new_backend = TerminalFailureBackend()
    new = JobService(tmp_path / 'jobs', packets=SQLitePacketStore(tmp_path / 'packets'),
        worker_factory=lambda service, job: TerminalFailureWorker(), backend=new_backend,
        retry_seconds=0, analysis_runtime_identity=epoch_b).start()
    try:
        # Even a scope field with the same name cannot choose the cache epoch.
        scope_with_spoof = {**SCOPE, 'analysis_runtime_identity': epoch_a}
        assert new.inquire('identical question', scope_with_spoof,
                           foreground_timeout=3) == expected
        assert new_backend.opens
        with new._db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE json_extract(record,'$.question')=?",
                              ('identical question',)).fetchall()
        assert len(rows) == 2 and len({row['id'] for row in rows}) == 2
    finally:
        new.shutdown()


def test_runtime_identity_hashes_receiver_manifest_profile_and_nested_adapter(tmp_path, monkeypatch):
    root = _prepare_receiver_fixture(tmp_path / 'receiver-a')
    first = _fixture_epoch(root, monkeypatch)
    assert first == _fixture_epoch(root, monkeypatch)
    same_bytes_other_root = _prepare_receiver_fixture(tmp_path / 'receiver-b')
    assert _fixture_epoch(same_bytes_other_root, monkeypatch) != first
    (root / 'local_worker/servers/llama_cpp.py').write_bytes(b'adapter-v2')
    _refresh_fixture_manifest(root)
    assert _fixture_epoch(root, monkeypatch) != first


def test_runtime_identity_hashes_bounded_current_producer_modules(tmp_path, monkeypatch):
    root = _prepare_receiver_fixture(tmp_path / 'receiver')
    _fixture_epoch(root, monkeypatch)
    producer_root = tmp_path / 'project-control'
    producer_root.mkdir()
    actual_sources = {
        relative: (ROOT / 'src/project_control' / relative).read_bytes()
        for relative in PRODUCER_FILES
    }
    actual_source_hashes = {
        relative: hashlib.sha256((ROOT / 'src/project_control' / relative).read_bytes()).hexdigest()
        for relative in PRODUCER_FILES
    }
    for relative, content in actual_sources.items():
        target = producer_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    monkeypatch.setattr(surface, '__file__', str(producer_root / 'as1_surface.py'))

    first = _fixture_epoch(root, monkeypatch)
    assert _fixture_epoch(root, monkeypatch) == first

    # Receiver commit labels and unrelated documentation metadata do not
    # become cache identity inputs when the verified file inventory is stable.
    manifest_path = root / 'receiver-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['source_commit'] = 'head-b'
    manifest['unrelated_metadata'] = {'documentation_revision': 'doc-only-v2'}
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    assert _fixture_epoch(root, monkeypatch) == first

    docs = producer_root / 'docs/notes.md'
    docs.parent.mkdir(parents=True)
    docs.write_text('unrelated documentation v1\n')
    assert _fixture_epoch(root, monkeypatch) == first
    docs.write_text('unrelated documentation v2\n')
    assert _fixture_epoch(root, monkeypatch) == first

    # Every admitted producer module, including each newly added assistance
    # component, changes the epoch independently when its source bytes change.
    for relative, original in actual_sources.items():
        path = producer_root / relative
        path.write_bytes(original + b'\n# cache epoch mutation\n')
        assert _fixture_epoch(root, monkeypatch) != first, relative
        path.write_bytes(original)
        assert _fixture_epoch(root, monkeypatch) == first, relative

    # The inert fixture operates under tmp_path; the original potentially
    # dirty source tree remains byte-for-byte untouched.
    assert {
        relative: hashlib.sha256((ROOT / 'src/project_control' / relative).read_bytes()).hexdigest()
        for relative in PRODUCER_FILES
    } == actual_source_hashes


def test_runtime_identity_rejects_oversized_producer_module(tmp_path, monkeypatch):
    import pytest
    root = _prepare_receiver_fixture(tmp_path / 'receiver')
    _fixture_epoch(root, monkeypatch)
    producer_root = tmp_path / 'project-control'
    producer_root.mkdir()
    for relative in PRODUCER_FILES:
        target = producer_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'x')
    (producer_root / 'as1_jobs.py').write_bytes(b'x' * (1024 * 1024 + 1))
    monkeypatch.setattr(surface, '__file__', str(producer_root / 'as1_surface.py'))

    with pytest.raises(ValueError, match='analysis_runtime_identity_file_too_large'):
        _fixture_epoch(root, monkeypatch)
