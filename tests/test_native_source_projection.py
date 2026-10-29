"""Large source inventories must not hide orchestration effect identities."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest
from todo_orchestrator.api.serialization import ApiSerializationError, public_value, serialize_public_document
from todo_orchestrator.contracts import EntityKind, EntityRef, EvidenceInput, Sha256Digest, StableId
from todo_orchestrator.contracts.providers import (
    GitEffectDisposition, GitEffectObservation, GitEffectRequest, GitOperationKind,
    ProviderCallContext, SourceObservation, WorkingTreeState,
)
from todo_orchestrator.providers.git.service import GitEffectOperationView, GitEffectState
from todo_orchestrator.views.recovery import RecoveryInspection, RecoveryOperation, RecoveryOperationStatus, RecoveryStatus
from project_control.compact_workflow import CompactWorkflowHost
from project_control.native_workflow import _public


def fixtures():
    project = StableId('fixture-project')
    def ref(kind, identity):
        return EntityRef(project, kind, StableId(identity),
                         incarnation_id=StableId('actor-incarnation') if kind is EntityKind.ACTOR else None,
                         definition_revision_id=StableId('definition') if kind is EntityKind.WORK_ITEM else None)
    attempt, target = ref(EntityKind.ATTEMPT, 'attempt'), ref(EntityKind.WORK_ITEM, 'work')
    source = SourceObservation(StableId('repo'), Sha256Digest('a'*64), Sha256Digest('b'*64),
        WorkingTreeState.CLEAN, tuple(EvidenceInput(StableId(f'source/path-{i:05d}.py'), Sha256Digest('c'*64))
                                     for i in range(10802)), source_revision=StableId('d'*40))
    request = GitEffectRequest(ProviderCallContext(project, ref(EntityKind.ACTOR, 'actor'),
        ref(EntityKind.CAPABILITY, 'capability'), StableId('host-context'), StableId('scope')),
        StableId('git-operation'), StableId('retry-key'), GitOperationKind.INTEGRATE,
        source.source_digest, 53, attempt=attempt, target=target)
    observation = GitEffectObservation(request.operation_id, GitEffectDisposition.REJECTED)
    view = GitEffectOperationView(request.operation_id, GitEffectState.REJECTED, request,
        Sha256Digest('e'*64), observation, None, source_observation=source,
        reason_code=StableId('provider-rejected'))
    operation = RecoveryOperation(ref(EntityKind.EXTERNAL_OPERATION, 'git-operation'), attempt, target,
        None, 'git', 'rejected', RecoveryOperationStatus.IRRECOVERABLE, view.request_digest, None,
        source_observation=source, git_observation=observation)
    recovery = RecoveryInspection(attempt,target,None,'accepted',None,None,None,RecoveryStatus.IRRECOVERABLE,
        55,datetime.now(timezone.utc),None,source,None,(),(operation,),(),(),())
    return source, view, recovery


@pytest.mark.parametrize('kind,index', [('git_effect',1),('recovery_inspection',2)])
def test_large_source_inventory_is_explicitly_summarized_after_native_validation(kind,index):
    values=fixtures(); value=values[index]; full=public_value(value)
    assert len(json.dumps(full)) > 512*1024
    with pytest.raises(ApiSerializationError):
        serialize_public_document(kind,value)
    session=SimpleNamespace(serialize=serialize_public_document)
    result=_public(session,kind,value)
    assert len(json.dumps(result).encode()) < 8192
    payload=result['payload']; summary=payload['source_observation']; source=values[0]
    assert summary['source_digest']==source.source_digest.hex
    assert summary['source_revision']==str(source.source_revision)
    assert summary['working_tree']=='clean'
    assert 'inputs' not in summary and 'artifacts' not in summary
    inventory=summary['inventory_summary']
    assert inventory['included'] is False
    encoded=json.dumps(full['source_observation']['inputs'],ensure_ascii=True,allow_nan=False,
                       sort_keys=True,separators=(',',':')).encode()
    assert inventory['collections']['inputs']=={'count':10802,'sha256':hashlib.sha256(encoded).hexdigest()}
    # Projection must not mutate the native DTO or weaken its full API limit.
    assert len(source.inputs)==10802
    with pytest.raises(ApiSerializationError):serialize_public_document(kind,value)
    if kind=='git_effect':
        for key in ['operation_id','state','request','request_digest','observation','provider_receipt','reason_code']:
            assert payload[key]==full[key]
        assert not CompactWorkflowHost._git_integration_result(result)['applied']
        applied=replace(value,state=GitEffectState.FINALIZED,
            observation=GitEffectObservation(value.operation_id,GitEffectDisposition.APPLIED,source.source_digest))
        accepted=_public(session,kind,applied)
        assert CompactWorkflowHost._git_integration_result(accepted)['applied']
    else:
        assert payload['operations'][0]['source_observation']==summary
        for key in ['operation','state','status','request_digest','git_observation','blockers']:
            assert payload['operations'][0][key]==full['operations'][0][key]
        for key in ['intents','blockers','preconditions','authority_revision']:
            assert payload[key]==full[key]


def test_recovery_apply_result_summarizes_nested_large_git_effect():
    source, view, _recovery = fixtures()
    value = {"disposition": "retry_original_operation", "result": view}
    with pytest.raises(ApiSerializationError):serialize_public_document("recovery",value)
    result = _public(SimpleNamespace(serialize=serialize_public_document),"recovery",value)
    assert len(json.dumps(result)) < 8192
    assert result["payload"]["result"]["source_observation"]["inventory_summary"]["collections"]["inputs"]["count"] == 10802
    assert result["payload"]["result"]["operation_id"] == str(view.operation_id)
