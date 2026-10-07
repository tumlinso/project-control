"""CPU behavioral proof: actual sandbox/installed port and durable SQLite jobs.

Scripted model turns prove broker behavior; they do not qualify live model
reasoning, GPU execution, or the deferred agentic SQA campaign.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest

from project_control.as1_contracts import SourceLocator
from project_control.as1_jobs import JobService, TrustedObserverFactory
from project_control.as1_packets import SQLitePacketStore
from project_control.as1_skill import SkillObserverFactory, SkillService
import project_control.as1_skill as skill_module
import project_control.skills as skills_module

INSTALLED = Path('/home/tumlinson/.agents/skills')
RUNTIME_ROOT = Path(__file__).resolve().parents[2]/'src/project_control/local_runtime'
SCOPE = {'principal': 'alice', 'profile': 'observer'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_wait(predicate):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError('durable skill job did not terminate')


class Turns:
    def __init__(self, values):
        self.values, self.requests = iter(values), []
    def run_observer_turn(self, request):
        self.requests.append(request)
        value = next(self.values)
        if callable(value):
            value = value(request)
        return {'status': 'available', 'text': json.dumps(value)}


def command(path):
    return {'tool': 'command', 'arguments': {'argv': ['cat', str(path)], 'cwd': str(path.parent)}}


def item(root, name, resource, start=1, end=1, prerequisites=None):
    result = {'skill': name, 'resource': resource, 'content_sha256': digest(root/name/resource),
              'line_start': start, 'line_end': end, 'reason': 'Native authored route selected by worker'}
    if prerequisites is not None:
        result['prerequisites'] = prerequisites
    return result


def final(items, synthesis='Use selected native instructions.', unresolved=None):
    def answer(request):
        observations = []
        for message in request['messages'][2:]:
            if message['role'] == 'user':
                payload = json.loads(message['content'])
                observation = payload.get('retained_observation', payload)
                if observation.get('packet_id'):
                    observations.append(observation)
        return {'answer': 'Selected installed authority', 'findings': [{'text': 'Selected native source',
                'evidence_packets': [observations[-1]['packet_id']]}],
                'skill_selection': {'format': 'pc-skill-selection/1', 'selections': items,
                                    'synthesis': synthesis, 'unresolved': unresolved or []}}
    return answer


def fixture_root(tmp_path):
    root = tmp_path/'skills'; root.mkdir()
    entries = []
    for name in ('local-coding-worker', 'fixture', 'prerequisite'):
        skill = root/name; skill.mkdir()
        (skill/'SKILL.md').write_text('---\nname: ' + name + '\ndescription: Installed ' + name + ' guide\n---\n'
                                    '# Native guide\nRead resource.md. No map is installed; follow this reference.\n')
        (skill/'resource.md').write_bytes(b'# Exact instructions\r\n  keep indentation\r\n\r\n```python\r\nx = 1\r\n```\r\n')
        entries.append({'name': name, 'entry': name+'/SKILL.md', 'sha256': digest(skill/'SKILL.md'), 'status': 'accessible'})
    return root


def make(tmp_path, root, backend, *, registrations=None, wrap=True):
    names = [p.name for p in root.iterdir() if (p/'SKILL.md').is_file()]
    skills = registrations or {name: {'name': name, 'root': str(root/name)} for name in names}
    from project_control.runtime_binding import local_runtime_identity
    identity = local_runtime_identity(root=RUNTIME_ROOT)
    trusted = TrustedObserverFactory(identity.root, digest(identity.root/'local_worker/observer_runtime.py'),
        backend=backend, roots=[root], tools=lambda *args: {'status': 'ok'}, skills=skills)
    factory = SkillObserverFactory(trusted, skills_root=root) if wrap else trusted
    jobs = JobService(tmp_path/'jobs', packets=SQLitePacketStore(tmp_path/'packets'), worker_factory=factory)
    return SkillService(jobs, skills_root=root), jobs


def finish(service, admitted):
    assert admitted['accepted'], admitted
    ident = admitted['job_id']
    return bounded_wait(lambda: (v if (v := service.jobs.lookup(ident, access_scope=SCOPE))['job']['status']
                                in {'completed', 'partial', 'failed', 'cancelled'} else None))


@pytest.mark.as1_case('SKL-01')
def test_catalog_optional_name_hints_unscoped_worker_selection_and_profile(tmp_path):
    root = fixture_root(tmp_path)
    backend = Turns([command(root/'fixture/SKILL.md'),
                     command(root/'fixture/resource.md'), final([item(root, 'fixture', 'resource.md', 1, 6)])])
    service, jobs = make(tmp_path, root, backend)
    catalog = service.submit(access_scope=SCOPE)
    assert catalog['complete'] and catalog['continuation'] is None
    assert {e['name'] for e in catalog['skills']} == {'fixture', 'prerequisite'}
    assert all(e['description'] and e['id'] for e in catalog['skills'])
    assert catalog['external_dependencies'] == []
    assert all(source.path != 'integrations/native-skill-catalog.json'
               for source in service.packets.lookup(catalog['packet_id'], access_scope=SCOPE).packet.sources)
    assert not backend.requests and jobs.health()['dispatcher'] == 'stopped'
    jobs.start()
    try:
        admitted = service.submit(query='Worker choose a native instruction', hints=[catalog['alias']],
                                  request_id='catalog-discovery', access_scope=SCOPE)
        completed = finish(service, admitted)
        assert completed['job']['status'] == 'completed', completed
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='standard')
        assert result['excerpts'][0]['skill'] == 'fixture'
        assert jobs.lookup(admitted['job_id'], access_scope=SCOPE)['job']['hints'] == [catalog['alias']]
        assert service.submit(query='Worker choose a native instruction', hints=[catalog['alias']],
                              request_id='catalog-discovery', access_scope=SCOPE)['job_id'] == admitted['job_id']
        assert service.poll(admitted['job_id'], access_scope={**SCOPE, 'principal': 'bob'})['excerpts']
        assert service.submit(query='foo', skill='external', access_scope=SCOPE)['reason'] == 'unregistered_skill'
        with pytest.raises(PermissionError):
            service.submit(access_scope={**SCOPE, 'profile': 'coder'})
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-01')
def test_catalog_is_derived_from_current_entries_and_excludes_retired_skill(tmp_path):
    root = fixture_root(tmp_path)
    (root/'fixture/SKILL.md').write_text('---\nname: fixture\ndescription: Changed\n---\n')
    (root/'prerequisite/SKILL.md').unlink()
    registrations = {'fixture': {'name': 'fixture', 'root': str(root/'fixture')}}
    service, jobs = make(tmp_path, root, Turns([]), registrations=registrations)
    catalog = service.catalog(access_scope=SCOPE)
    rows = {r['name']: r for r in catalog['skills']}
    assert rows['fixture']['status'] == 'accessible'
    assert 'prerequisite' not in rows
    assert 'local-coding-worker' not in rows
    assert catalog['rejected'] >= 1


def test_skill_root_inventory_limit_counts_regular_files(tmp_path, monkeypatch):
    root = tmp_path/'skills'; root.mkdir()
    skill = root/'fixture'; skill.mkdir()
    (skill/'SKILL.md').write_text('---\nname: fixture\ndescription: Fixture\n---\n')
    (root/'one.txt').write_text('one')
    (root/'two.txt').write_text('two')
    monkeypatch.setattr(skills_module, 'MAX_ENTRIES', 2)
    with pytest.raises(skills_module.SkillError) as error:
        skills_module.SkillRegistry(root)._discover()
    assert error.value.code == 'inventory_limit'


def test_effective_catalog_digest_invalidates_skill_inquiry_identity(tmp_path):
    root = fixture_root(tmp_path)
    service, jobs = make(tmp_path, root, Turns([]))
    before = service.catalog_identity()
    identity_before = jobs._inquiry_identity('same question', SCOPE, 'skill', None)
    entry = root/'prerequisite/SKILL.md'
    entry.write_text(entry.read_text() + '\nUpdated routing guidance.\n')
    assert service.catalog_identity() != before
    assert jobs._inquiry_identity('same question', SCOPE, 'skill', None) != identity_before


def test_retired_skill_jobs_are_not_remapped_when_recovered(tmp_path, monkeypatch):
    root = fixture_root(tmp_path)
    service, jobs = make(tmp_path, root, Turns([]))
    captured = []
    monkeypatch.setattr(jobs, 'finish', lambda job_id, attempt, result: captured.append(result))
    jobs._execute(SimpleNamespace(mode='skill', skill='local-coding-worker', job_id='old-job', attempt=1))
    assert captured and captured[0]['reason'] == 'retired_skill_unavailable'
    assert not service.submit(query='legacy request', skill='local-coding-worker',
                              access_scope=SCOPE)['accepted']


def test_direct_observer_port_requires_trusted_skill_registrations(tmp_path):
    root = fixture_root(tmp_path)
    service, jobs = make(tmp_path, root, Turns([]))
    trusted = jobs.worker_factory.trusted
    module = trusted.load_runtime_module()
    class Command:
        roots = ()
        def allows(self, path):
            return True
        def run(self, *args, **kwargs):
            raise AssertionError('unregistered direct port must not read')
    backend = Turns([])
    worker = module.ObserverWorkerPort(backend, command=Command(), tools=lambda *args: {},
        fence=lambda *args: True, skills={})
    result = worker.run({'job_id': 'direct', 'attempt': 1, 'mode': 'skill', 'question': 'q',
                         'scope': SCOPE, 'hints': [], 'observations': []})
    assert result['status'] == 'partial'
    assert result['reason'] == 'skill_registrations_unavailable'
    assert not backend.requests


@pytest.mark.as1_case('SKL-02', 'SKL-03')
def test_actual_cuda_volta_nested_atlas_route_direct_original_bytes(tmp_path):
    resources = ['SKILL.md', 'references/architectures/volta/router.md',
                 'references/architectures/volta/v100_atlas/START_HERE.md']
    choices = [item(INSTALLED, 'cuda', resources[1], 1, 9),
               item(INSTALLED, 'cuda', resources[2], 1, 7, prerequisites=[resources[1]])]
    backend = Turns([command(INSTALLED/'cuda'/p) for p in resources] + [final(choices)])
    service, jobs = make(tmp_path, INSTALLED, backend)
    before = [digest(INSTALLED/'cuda'/p) for p in resources]
    jobs.start()
    try:
        admitted = service.submit(query='Use Volta route and nested atlas prerequisites', skill='cuda', access_scope=SCOPE)
        completed = finish(service, admitted)
        assert completed['job']['status'] == 'completed', completed
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert len(result['excerpts']) == 2 and not result['unresolved']
        for excerpt in result['excerpts']:
            raw = (INSTALLED/'cuda'/excerpt['resource']).read_bytes().decode()
            assert excerpt['content'] == ''.join(raw.splitlines(keepends=True)[excerpt['line_start']-1:excerpt['line_end']])
            assert excerpt['verbatim']
        assert result['excerpts'][1]['prerequisites'] == [resources[1]]
        assert before == [digest(INSTALLED/'cuda'/p) for p in resources]
        context = json.loads(backend.requests[0]['messages'][1]['content'])
        assert context['skill']['root'] == str(INSTALLED/'cuda')
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-02', 'SKL-04')
def test_missing_map_native_fallback_cross_skill_prerequisite_small_synthesis(tmp_path):
    root = fixture_root(tmp_path)
    choices = [item(root, 'fixture', 'resource.md', 1, 6, ['prerequisite/resource.md']),
               item(root, 'prerequisite', 'resource.md', 1, 6)]
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                     command(root/'prerequisite/SKILL.md'), command(root/'prerequisite/resource.md'),
                     final(choices, synthesis='Linked sources. '*500)])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Use authored reference; include explicit prerequisite', skill='fixture', access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert {e['skill'] for e in result['excerpts']} == {'fixture', 'prerequisite'}
        assert not result['unresolved']
        assert result['synthesis']['excerpt_refs'] == ['excerpt_0', 'excerpt_1']
        assert not result['synthesis']['entailment_verified']
        assert len(result['synthesis']['text'].encode()) <= int(sum(len(e['content'].encode()) for e in result['excerpts'])*.15)
        packet = service.packets.lookup(result['alias'], access_scope=SCOPE).packet
        assert packet.tool == 'skill' and packet.parents
        assert service.packets.lookup(packet.parents[0], access_scope=SCOPE).packet.tool == 'skill'
        # Polling after host restart reuses durable selection and direct rereads,
        # without loading a model or constructing another dispatch queue.
        jobs.shutdown()
        restored = JobService(tmp_path/'jobs', packets=SQLitePacketStore(tmp_path/'packets'), worker_factory=jobs.worker_factory)
        restarted = SkillService(restored, skills_root=root)
        assert restarted.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')['excerpts'] == result['excerpts']
        assert restored.health()['dispatcher'] == 'stopped'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-03')
@pytest.mark.parametrize('defect', ['hash', 'range', 'unread_entry', 'escape', 'unregistered'])
def test_real_port_rejects_wrong_hash_range_root_and_unread_cross_skill(tmp_path, defect):
    root = fixture_root(tmp_path)
    choice = item(root, 'fixture', 'resource.md')
    if defect == 'hash': choice['content_sha256'] = '0'*64
    if defect == 'range': choice['line_end'] = 99
    if defect == 'escape': choice['resource'] = '../prerequisite/resource.md'
    if defect == 'unregistered': choice['skill'] = 'external'
    if defect == 'unread_entry': choice['skill'] = 'prerequisite'
    turns = [command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md')]
    if defect == 'unread_entry': turns.append(command(root/'prerequisite/resource.md'))
    service, jobs = make(tmp_path, root, Turns(turns+[final([choice])])); jobs.start()
    try:
        admitted = service.submit(query='Select fixture source', skill='fixture', access_scope=SCOPE)
        completed = finish(service, admitted)
        assert completed['job']['status'] == 'partial', completed
        assert not service.poll(admitted['job_id'], access_scope=SCOPE).get('excerpts')
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-03')
@pytest.mark.parametrize('defect', ['changed', 'symlink', 'redacted'])
def test_assembly_revalidates_stale_escape_and_redaction_without_fake_verbatim(tmp_path, defect):
    root = fixture_root(tmp_path)
    if defect == 'redacted':
        (root/'fixture/resource.md').write_text('Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n')
    choice = item(root, 'fixture', 'resource.md')
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'), final([choice])])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Select fixture source', skill='fixture', access_scope=SCOPE)
        completed = finish(service, admitted)
        # Secret observations can be policy-masked before the port retains its
        # proof; either explicit port partial or broker redaction is honest.
        if completed['job']['status'] == 'completed':
            if defect == 'changed': (root/'fixture/resource.md').write_text('Shifted replacement\n')
            if defect == 'symlink':
                (root/'fixture/resource.md').unlink()
                (root/'fixture/resource.md').symlink_to(root/'prerequisite/resource.md')
            result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
            assert result['status'] == 'partial' and not result['excerpts']
            assert result['omissions'][0]['reason'] in {'stale_resource', 'unavailable', 'redacted_resource'}
        else:
            assert defect == 'redacted' and completed['job']['status'] == 'partial'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-03', 'SKL-04')
def test_broker_retains_valid_selection_and_reports_corrupt_range_proof_and_missing_prereq(tmp_path):
    root = fixture_root(tmp_path)
    choices = [item(root, 'fixture', 'resource.md', 1, 2, ['external/SKILL.md']),
               item(root, 'fixture', 'resource.md', 3, 6)]
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'), final(choices)])
    service, jobs = make(tmp_path, root, backend)
    original = jobs.worker_factory
    # Forced defective producer output after a real agentic port execution:
    # broker must independently validate every selection, retaining valid ones.
    class CorruptProducer:
        skills = original.skills
        def __call__(self, service, job):
            worker = original(service, job)
            class Worker:
                def run(self, request):
                    result = worker.run(request)
                    result['skill_selection']['selections'][1]['line_end'] = 999
                    return result
            return Worker()
    jobs.worker_factory = CorruptProducer(); jobs.start()
    try:
        admitted = service.submit(query='Select useful source', skill='fixture', access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert len(result['excerpts']) == 1
        assert result['omissions'][0]['reason'] == 'out_of_range'
        assert result['unresolved'] == ['Missing or unverified prerequisite: external/SKILL.md']
        assert '\r\n' in result['excerpts'][0]['content']
        with pytest.raises(ValueError, match='durable job identity'):
            service.assemble({'selections': choices, 'synthesis': 'forged'}, access_scope=SCOPE)
        assert service.assemble({'selections': choices, 'synthesis': 'forged'}, access_scope=SCOPE,
                                job_id=admitted['job_id'], attempt=1)['status'] == 'unverified_selection'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-04')
def test_real_port_between_turn_preemption_then_resume_and_cancel_fencing(tmp_path):
    root = fixture_root(tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Backend(Turns):
        preempt = True
        def preemption_status(self, session):
            if self.preempt:
                entered.set(); release.wait(5)
                return {'preempt_requested': True}
            return {}
        def open_sessions(self, *args, **kwargs): return {'status': 'available', 'session_ids': ['cpu-script-session']}
        def close_session(self, session): pass
    backend = Backend([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                       final([item(root, 'fixture', 'resource.md')])])
    service, jobs = make(tmp_path, root, backend); jobs.backend = backend; jobs.retry_seconds = .02; jobs.start()
    try:
        admitted = service.submit(query='Preserve and resume selected native source', skill='fixture', access_scope=SCOPE)
        assert entered.wait(3)
        old = jobs.lookup(admitted['job_id'], access_scope=SCOPE)['job']['attempt']
        release.set()
        bounded_wait(lambda: jobs.lookup(admitted['job_id'], access_scope=SCOPE)['job']['status'] == 'yielding')
        backend.preempt = False
        completed = finish(service, admitted)
        assert completed['job']['status'] == 'completed' and completed['job']['attempt'] > old
        assert not jobs.finish(admitted['job_id'], old, {'status': 'completed', 'answer': 'late'})
        assert service.assemble({'selections': [item(root, 'fixture', 'resource.md')], 'synthesis': 'late'},
                                access_scope=SCOPE, job_id=admitted['job_id'], attempt=old)['status'] == 'stale_attempt'
        result = service.poll(admitted['job_id'], access_scope=SCOPE)
        assert result['excerpts'] and result['freshness']['max_age_seconds'] == 0
    finally:
        release.set(); jobs.shutdown()


@pytest.mark.as1_case('SKL-03')
def test_wrong_registered_root_and_mid_read_replacement_are_explicit(tmp_path, monkeypatch):
    root = fixture_root(tmp_path)
    outside = tmp_path/'outside'; outside.mkdir()
    (outside/'SKILL.md').write_text('outside root\n')
    registration = {'fixture': {'name': 'fixture', 'root': str(outside)}}
    service, jobs = make(tmp_path, root, Turns([command(outside/'SKILL.md')]), registrations=registration)
    rows = {row['name']: row for row in service.catalog(access_scope=SCOPE)['skills']}
    assert rows['fixture']['status'] == 'registration_mismatch'
    jobs.start()
    try:
        admitted = service.submit(query='Select source', skill='fixture', access_scope=SCOPE)
        rejected = finish(service, admitted)['job']
        assert rejected['status'] == 'failed'
        assert rejected['failure_reason'] == 'skill_root_outside_trusted_mounts'
        assert not service.poll(admitted['job_id'], access_scope=SCOPE).get('excerpts')
    finally:
        jobs.shutdown()
    # New real port/SQLite job; inject replacement only during broker direct read.
    second = tmp_path/'second'; second.mkdir()
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                     final([item(root, 'fixture', 'resource.md')])])
    service, jobs = make(second, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Select source', skill='fixture', access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        source = root/'fixture/resource.md'; inode = source.stat().st_ino
        original_read, mutated = skill_module.os.read, []
        def racing_read(fd, size):
            chunk = original_read(fd, size)
            if not mutated and os.fstat(fd).st_ino == inode:
                source.write_text('replacement during read\n'); mutated.append(True)
            return chunk
        monkeypatch.setattr(skill_module.os, 'read', racing_read)
        result = service.poll(admitted['job_id'], access_scope=SCOPE)
        assert mutated and not result['excerpts']
        assert result['omissions'][0]['reason'] == 'stale_resource'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-04')
def test_current_retained_hint_can_supply_explicit_prerequisite(tmp_path):
    root = fixture_root(tmp_path)
    backend = Turns([])
    service, jobs = make(tmp_path, root, backend)
    prerequisite = root/'prerequisite/resource.md'
    hint = service.packets.create(tool='skill', payload={'excerpts': [{'content': prerequisite.read_bytes().decode(),
        'resource': 'prerequisite/resource.md', 'content_sha256': digest(prerequisite), 'verbatim': True}]},
        access_scope=SCOPE, sources=[SourceLocator(project='skills', repository=str(root),
            path='prerequisite/resource.md', content_sha256=digest(prerequisite), line_start=1, line_end=6)])
    backend.values = iter([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                          final([item(root, 'fixture', 'resource.md', 1, 6, [hint.alias])])])
    jobs.start()
    try:
        admitted = service.submit(query='Reuse explicitly retained prerequisite', skill='fixture',
                                  hints=[hint.alias], access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert result['retained_dependencies'] == [{'prerequisite': hint.alias, 'packet_id': hint.packet_id}]
        assert not result['unresolved']
        prerequisite.write_text('changed prerequisite\n')
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert not result['retained_dependencies'] and 'Missing or unverified prerequisite' in result['unresolved'][0]
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-04')
def test_cancel_during_real_model_turn_fences_selection_and_checkpoint(tmp_path):
    root = fixture_root(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def blocked(request):
        entered.set(); release.wait(5)
        return command(root/'fixture/resource.md')
    backend = Turns([command(root/'fixture/SKILL.md'), blocked])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Cancel selection', skill='fixture', access_scope=SCOPE)
        assert entered.wait(3)
        old = jobs.lookup(admitted['job_id'], access_scope=SCOPE)['job']['attempt']
        assert jobs.cancel(admitted['job_id'], access_scope=SCOPE)
        release.set()
        assert not jobs.fence(admitted['job_id'], old)
        with pytest.raises(RuntimeError, match='stale_attempt'):
            jobs.checkpoint(admitted['job_id'], old, [])
        result = service.poll(admitted['job_id'], access_scope=SCOPE)
        assert result['status'] == 'cancelled' and 'excerpts' not in result
    finally:
        release.set(); jobs.shutdown()


@pytest.mark.as1_case('SKL-03')
def test_real_port_mixed_selection_keeps_valid_source_with_unverified_hash_omission(tmp_path):
    root = fixture_root(tmp_path)
    choices = [item(root, 'fixture', 'resource.md', 1, 2), item(root, 'fixture', 'resource.md', 3, 6)]
    choices[1]['content_sha256'] = '0'*64
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'), final(choices)])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Keep valid selection when a second selection is stale', skill='fixture', access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert len(result['excerpts']) == 1 and result['excerpts'][0]['line_start'] == 1
        assert result['status'] == 'partial' and result['omissions'][0]['reason'] == 'unverified_reader_hash'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-02', 'SKL-03')
@pytest.mark.parametrize('order', ['ordered', 'reversed', 'broker_reversed'])
def test_cross_skill_entry_precedes_first_resource_read_in_durable_provenance(tmp_path, order):
    root = fixture_root(tmp_path)
    choice = item(root, 'prerequisite', 'resource.md')
    reads = [command(root/'prerequisite/SKILL.md'), command(root/'prerequisite/resource.md')]
    if order != 'ordered':
        reads.reverse()
    # The current worker exposes validation feedback and asks for correction.
    # Repeat the defective final through the remaining turn budget so rejection
    # returns its natural partial snapshot rather than exhausting the fake backend.
    def defective_final(request):
        answer = final([choice])(request)
        # Validation feedback is not source evidence. Retrying the same rejected
        # selection must keep citing the last successful source observation.
        for message in request['messages']:
            if message['role'] != 'user':
                continue
            payload = json.loads(message['content'])
            observed = payload.get('retained_observation', payload)
            if observed.get('status') == 'completed' and observed.get('source_reads'):
                answer['findings'][0]['evidence_packets'] = [observed['packet_id']]
        return answer
    backend = Turns([command(root/'fixture/SKILL.md')] + reads + [defective_final] * 3)
    service, jobs = make(tmp_path, root, backend)
    if order == 'broker_reversed':
        original = jobs.worker_factory
        class CorruptProducer:
            skills = original.skills
            def __call__(self, service, job):
                worker = original(service, job)
                class Worker:
                    def run(self, request):
                        result = worker.run(request)
                        assert result['status'] == 'partial'
                        result['skill_selection'] = {'format': 'pc-skill-selection/1', 'selections': [choice],
                                                     'synthesis': 'Defective producer bypassed ordered proof', 'unresolved': []}
                        return result
                return Worker()
        jobs.worker_factory = CorruptProducer()
    jobs.start()
    try:
        admitted = service.submit(query='Follow cross-skill prerequisite entry first', skill='fixture', access_scope=SCOPE)
        completed = finish(service, admitted)
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        if order == 'ordered':
            assert completed['job']['status'] == 'completed'
            assert result['excerpts'][0]['skill'] == 'prerequisite'
        elif order == 'reversed':
            assert completed['job']['status'] == 'partial'
            assert not result.get('excerpts')
            assert result['reason'] == 'step_budget_exhausted'
            feedback = [observation for observation in completed['observations']
                        if observation.get('reason') == 'skill_final_validation_failed']
            assert feedback
            assert all(observation['validation_error'] == 'selected_skill_entry_must_precede_resource'
                       for observation in feedback)
        else:
            assert not result['excerpts'] and result['status'] == 'partial'
            assert result['omissions'][0]['reason'] == 'skill_entry_read_after_resource'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-02', 'SKL-03')
def test_mixed_ordered_and_reversed_cross_skill_selections_keep_valid_excerpt(tmp_path):
    root = fixture_root(tmp_path)
    choices = [item(root, 'fixture', 'resource.md'), item(root, 'prerequisite', 'resource.md')]
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                     command(root/'prerequisite/resource.md'), command(root/'prerequisite/SKILL.md'), final(choices)])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Keep ordered source beside reversed dependency', skill='fixture', access_scope=SCOPE)
        assert finish(service, admitted)['job']['status'] == 'completed'
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert result['status'] == 'partial' and len(result['excerpts']) == 1
        assert result['excerpts'][0]['skill'] == 'fixture'
        assert result['omissions'][0]['reason'] == 'skill_entry_read_after_resource'
    finally:
        jobs.shutdown()


@pytest.mark.as1_case('SKL-04')
@pytest.mark.parametrize('overlapping_manifest', [False, True])
def test_durable_top_level_unresolved_questions_survive_terminal_assembly(tmp_path, overlapping_manifest):
    root = fixture_root(tmp_path)
    message = 'Missing cross-skill dependency: external/SKILL.md'
    manifest_message = 'Native map has an unavailable external reference'
    manifest_unresolved = [manifest_message, message] if overlapping_manifest else []
    scripted_final = final([item(root, 'fixture', 'resource.md')], unresolved=manifest_unresolved)
    def partial_answer(request):
        value = scripted_final(request)
        value['unresolved_questions'] = [message]
        return value
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'), partial_answer])
    service, jobs = make(tmp_path, root, backend); jobs.start()
    try:
        admitted = service.submit(query='Report external dependency honestly', skill='fixture', access_scope=SCOPE)
        completed = finish(service, admitted)
        assert completed['job']['status'] == 'partial'
        assert completed['job']['unresolved_questions'] == [message]
        result = service.poll(admitted['job_id'], access_scope=SCOPE, detail='extended')
        assert result['status'] == 'partial' and result['excerpts']
        assert result['unresolved'] == ([manifest_message, message] if overlapping_manifest else [message])
    finally:
        jobs.shutdown()


def test_shared_skill_inquiry_reuses_verified_selection_and_shares_assembly_packet(tmp_path):
    root = fixture_root(tmp_path)
    backend = Turns([command(root/'fixture/SKILL.md'), command(root/'fixture/resource.md'),
                     final([item(root, 'fixture', 'resource.md')])])
    service, jobs = make(tmp_path, root, backend)
    jobs.freshness_provider = lambda job: {'fresh': True}
    jobs.start()
    try:
        first = service.inquire(query='literal skill query', skill='fixture', access_scope=SCOPE)
        assert first['status'] in {'completed', 'partial'} and first['excerpts']
        assert all(o['reason'] == 'synthesis_budget' for o in first['omissions'])
        turns = len(backend.requests)
        cross_profile = jobs.inquire('literal skill query', {**SCOPE, 'principal': 'bob', 'profile': 'coder'},
                                     mode='skill', skill='fixture')
        assert cross_profile['status'] == 'completed' and len(backend.requests) == turns
        caller = {**SCOPE, 'principal': 'bob'}
        shared = service.inquire(query='literal skill query', skill='fixture', access_scope=caller)
        assert shared['status'] == first['status'] and shared['excerpts'] == first['excerpts']
        assert len(backend.requests) == turns
        resolved = jobs.packets.lookup(shared['packet_id'], access_scope=caller)
        assert resolved.status == 'ok' and resolved.packet.payload['excerpts'] == shared['excerpts']
        assert not {'job_id', 'attempt'} & resolved.packet.payload.keys()
        assert service.poll(cross_profile['job']['job_id'], access_scope=caller)['excerpts']
    finally:
        jobs.shutdown()
