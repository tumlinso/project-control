"""Root-executed live release evidence consumer; never performs activation.

Missing live cutover, rollback or reconciliation evidence is a failure. Source
fixtures and qualification candidates cannot substitute for the live receipt.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT/'planning/adaptive-surface-v1/contracts/surface.json').read_text())
PROOF = Path(os.environ.get('AS1_RELEASE_PROOF', '/home/tumlinson/.local/state/project-control/as1-bootstrap/release/live-receipt.json'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def live_release():
    assert PROOF.is_file(), 'Actual root live release proof has not been executed'
    proof = json.loads(PROOF.read_text())
    assert proof['format'] == 'pc-as1-live-release/1' and proof['status'] == 'passed'
    identity = proof['source_identity']
    candidate = Path(identity['candidate_root']).resolve(strict=True)
    manifest = candidate/'release-manifest.json'
    release = json.loads(manifest.read_text())
    assert sha(manifest) == identity['release_sha256']
    assert release['project_control_commit'] == identity['pc_commit']
    assert release['todo_commit'] == identity['skills_commit']
    assert Path(release['skills_root']).resolve() == candidate/'runtime-skills'
    assert proof['artifacts'], 'Raw executed artifact bundle required'
    for relative, expected in proof['artifacts'].items():
        path = (PROOF.parent/relative).resolve(strict=True)
        assert path.is_relative_to(PROOF.parent.resolve())
        assert sha(path) == expected, relative
    def artifact(name):
        relative = proof['evidence'][name]
        assert relative in proof['artifacts'], name
        return json.loads((PROOF.parent/relative).read_text())
    return proof, candidate, release, artifact


def identity_agrees(observed, candidate, release, source):
    assert observed['release_sha256'] == source['release_sha256']
    assert observed['todo_runtime_fingerprint'] == release['todo_runtime_fingerprint']
    assert Path(observed['skills_root']).resolve() == candidate/'runtime-skills'
    for field in ('project_control_module', 'todo_module'):
        module = Path(observed[field]).resolve(strict=True)
        assert module.is_relative_to(candidate)
        assert sha(module) == observed[field+'_sha256']
    assert observed['runtime_validation']['status'] == 'passed'


def surfaces_agree(rows, required, origin):
    assert set(rows) == required
    for profile, observed in rows.items():
        assert observed['origin'] in ({'live_registered_client', 'live_launcher_client'} if origin == 'live_client' else {origin})
        assert observed['transport'] in {'stdio', 'streamable-http', 'installed_in_process'}
        schemas = observed['schemas']
        assert set(schemas) == set(CONTRACT['profiles']['coder' if profile == 'codex' else profile]['tools'])
        assert 'No overview is automatic.' in observed['instructions']
        for schema in schemas.values():
            detail = schema.get('properties', {}).get('detail')
            if detail:
                assert ('extended' in detail.get('enum', [])) == (profile == 'observer')
        denied = {row['name']:row for row in observed['denials']}
        required_denials = {'project_overview', 'source_context', 'local_investigate'}
        if profile in {'coder', 'codex', 'mutator'}:
            required_denials |= {'delegate_task', 'collect_delegation'}
        if profile == 'observer':
            required_denials |= {'plan', 'amend_project', 'next_task'}
        assert required_denials <= set(denied)
        for name in required_denials:
            assert denied[name]['is_error'] is True and denied[name]['error']
        if profile != 'observer':
            assert observed['extended_denial']['is_error'] is True
            assert observed['extended_denial']['error']


@pytest.mark.as1_case('REL-01')
def test_installed_and_live_standalone_pair_and_exact_role_surfaces(live_release):
    proof, candidate, release, artifact = live_release
    source = proof['source_identity']
    identity_agrees(artifact('installed_identity'), candidate, release, source)
    live_identity = artifact('live_identity')
    identity_agrees(live_identity, candidate, release, source)
    roles = artifact('role_surfaces')
    surfaces_agree(roles['candidate'], set(CONTRACT['profiles']) | {'codex'}, 'installed_candidate')
    surfaces_agree(roles['live'], {'observer', 'codex', 'mutator'}, 'live_client')
    assert roles['live']['observer']['transport'] == 'streamable-http'
    assert roles['live']['codex']['transport'] == roles['live']['mutator']['transport'] == 'stdio'
    registration = artifact('registration')
    service = registration['service']
    assert service['ActiveState'] == 'active' and service['Id']
    assert int(service['MainPID']) > 0
    assert live_identity['process_binding']['service_main_pid'] == int(service['MainPID'])
    protected = registration['protected_service_before']
    assert protected == registration['protected_service_after']
    assert protected['ActiveState'] == 'active' and int(protected['MainPID']) > 0
    assert protected['Id'] != service['Id']
    assert int(protected['MainPID']) != int(service['MainPID'])
    assert registration['protected_processes_before'] == registration['protected_processes_after']
    assert registration['protected_processes_before']

    assert 'project-control' in registration['codex_registrations']
    assert 'coding-workflow' not in registration['codex_registrations'], 'Consolidated public registration must not retain the old alias'
    for row in registration['launchers']:
        launcher = Path(row['path']).resolve(strict=True)
        # This immutable evidence row records the launcher bytes observed at
        # qualification time. The stable launcher path can change afterward;
        # its recorded digest is already bound by the artifact bundle above.
        assert launcher.is_file()
        assert len(row['sha256']) == 64 and all(c in '0123456789abcdef' for c in row['sha256'])
        assert row.get('references_expected_candidate') is True
    assert registration['launchers']
    assert registration['paired_manifest']['path'] == str(candidate/'release-manifest.json')
    assert registration['paired_manifest']['sha256'] == source['release_sha256']
    assert registration['skills_standalone']['gitlinks_to_project_control'] == []
    assert registration['skills_standalone']['project_control_source_copies'] == []
    qualification = artifact('qualification')
    assert qualification['source_identity'] == source
    for report, cases in [('pc', {f'E2E-{i:02}' for i in range(1,6)}), ('skills', {'SQA-01', 'SQA-02', 'SQA-03'})]:
        result = qualification[report]
        if result.get('kind') == 'executed_product_acceptance':
            assert result['status'] == 'passed' and result['pytest_returncode'] == 0
            assert not result['missing_or_failed_cases']
            assert cases == set(result['required_cases']) == set(result['passed_cases'])
        else:
            assert result['pytest_exitstatus'] == 0
            for case in cases:
                assert result['cases'][case] and all(row['outcome']=='passed' for row in result['cases'][case])


@pytest.mark.as1_case('REL-02')
def test_actual_rollback_preserves_launcher_forward_jobs_and_native_authority(live_release):
    proof, candidate, _, artifact = live_release
    rollback = artifact('rollback')
    assert rollback['executed'] is True
    assert rollback['transition_sequence'] == ['new', 'old', 'new']
    assert rollback['commands'] and all(row['returncode']==0 for row in rollback['commands'])
    prior = rollback['prior_launcher']
    assert Path(prior['backup_path']).is_file() and sha(prior['backup_path']) == prior['sha256']
    assert Path(rollback['prior_candidate']).is_dir()
    assert rollback['mode'] in {'forward_compatible_store', 'pause_dispatcher_preserve_forward_store'}
    assert rollback['store_scope'] in {'live_service_store', 'isolated_native_fixture'}
    if rollback['store_scope']=='isolated_native_fixture':
        assert rollback['backend_observation']['source']=='native_backend'
        assert rollback['backend_observation']['status'] in {'unavailable','evicted'}
    snapshots = rollback['snapshots']
    assert set(snapshots) == {'before', 'rollback', 'restored'}
    accepted = rollback['accepted_reply']
    assert accepted['accepted'] is True and accepted['job_id']
    job_id = accepted['job_id']
    rows = [snapshots[name]['jobs'][job_id] for name in ('before', 'rollback', 'restored')]
    for key in ('job_id', 'question_sha256', 'scope', 'request_id', 'hints'):
        assert rows[0][key] == rows[1][key] == rows[2][key]
    assert rows[0]['job_id'] == job_id
    assert rows[0]['status'] in {'queued', 'running', 'yielding', 'queued_after_eviction'}
    assert all(row['status'] not in {'missing', 'failed', 'cancelled'} for row in rows)
    assert snapshots['restored']['source_identity'] == proof['source_identity']
    assert snapshots['restored']['candidate_root'] == str(candidate)
    assert rollback['store_path'] == snapshots['before']['store_path'] == snapshots['rollback']['store_path'] == snapshots['restored']['store_path']
    assert Path(rollback['store_path']).is_file()
    for authority in snapshots['before']['todo_revisions']:
        revisions = [snapshots[name]['todo_revisions'][authority] for name in ('before', 'rollback', 'restored')]
        assert revisions == sorted(revisions)
    assert rollback['todo_database_restorations'] == []
    assert rollback['preserved_forward_packets']
    assert set(rollback['preserved_forward_packets']) <= set(snapshots['restored']['packet_ids'])


@pytest.mark.as1_case('REL-03')
def test_executed_pce2_adoption_preserves_source_evidence_unrelated_work_and_nf1a(live_release):
    proof, _, _, artifact = live_release
    reconciliation = artifact('reconciliation')
    assert reconciliation['source_identity'] == proof['source_identity']
    mappings = json.loads((ROOT/'planning/adaptive-surface-v1/planning/legacy-disposition.json').read_text())['mappings']
    expected = {(row['project'], row['old_task']) for row in mappings}
    observed = {(row['project'], row['old_task']) for row in reconciliation['adoptions']}
    assert expected <= observed
    assert set(reconciliation['publications']) == {'project-control', 'skills'}
    for project, publication in reconciliation['publications'].items():
        assert publication['api'] == 'coordinate_task'
        assert publication['action'] == 'publish_context'
        assert publication['invalidate_fragment_ids'] == []
        assert publication['revision_after'] >= publication['revision_before']
        reply = publication['native_reply']
        reference, content = reply['context_note'], reply['content']
        assert reference['fragment_id'] and reference['version'] >= 1
        assert reference['kind'] == 'context_note'
        assert reference['authority'] == content['authority'] == 'non_authoritative'
        assert reference['invalidated'] is False
        normalized = json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        assert hashlib.sha256(normalized).hexdigest() == reference['content_hash']
        assert reply['project_revision'] == publication['revision_after']
        assert reference['owner_scope']['task_id'] == publication['release_task_id']
        body = content['content']
        assert body['old_tasks_completed'] is False and body['old_tasks_resumed'] is False
        adopted = {row['old_task']:row for row in body['adopted_implemented_obligations']}
        retained = set(body['retained_old_records'])
        for row in reconciliation['adoptions']:
            if row['project'] == project:
                assert row['old_task'] in retained and row['old_task'] in adopted
                assert adopted[row['old_task']]['outcomes'] == row['outcomes']
                assert adopted[row['old_task']]['receipt_refs'] == row['receipt_refs']
    expected_outcomes = {(row['project'], row['old_task']):set(row['new_outcomes']) for row in mappings}
    for row in reconciliation['adoptions']:
        project, task = row['project'], row['old_task']
        assert task in reconciliation['observed_old_run_members'][project]
        assert row['method'] == 'append_context_adoption'
        assert row['old_record_before'] == row['old_record_after']
        assert {'task', 'run', 'status', 'history'} <= set(row['old_record_before'])
        assert row['preserved_successful_evidence_ids'] == row['before_successful_evidence_ids']
        assert row['source_before_sha256'] == row['source_after_sha256']
        assert row['receipt_refs'] and all(ref['path'] and ref['sha256'] for ref in row['receipt_refs'])
        assert expected_outcomes.get((project, task), set()) <= set(row['outcomes'])
    assert reconciliation['old_runs_before'] == reconciliation['old_runs_after']
    assert reconciliation['task_supersessions'] == reconciliation['task_retirements'] == []
    assert reconciliation['unrelated_before'] == reconciliation['unrelated_after']
    assert reconciliation['nf1a_before'] == reconciliation['nf1a_after']
    assert reconciliation['preservation_checks']
    for row in reconciliation['preservation_checks']:
        assert row['before_sha256'] == row['after_sha256']
        assert sha(row['path']) == row['after_sha256']
    assert reconciliation['todo_database_restorations'] == []
    assert reconciliation['unrelated_resumed_work'] == []
