#!/usr/bin/env python3
"""Package-only self-tests, with temporary fixtures and fake runtime ports.

Nothing here imports or mutates a live Project Control/Todo service. These tests
qualify delivery guards, not the future implementation or deployed runtime.
"""
from __future__ import annotations
import copy,io,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.dont_write_bytecode=True
from common import *
from compile_plan import assemble
from validate_package import validate_structure,validate,manifest_check,check_profile
from strict_unittest import run as strict_run
import native_bridge as nb
import todo_bootstrap as tb
import run_gate as rg
from verify_receipt import file_refs
PACKAGE=Path(__file__).resolve().parents[1]
MASTER=load(PACKAGE/'machine/proposed_todos.json')
PLAN=assemble(MASTER)


def fake_observation(plan=PLAN):
    tables={k:[] for k in tb.namespace_ids(plan)}
    tables['tasks']=copy.deepcopy(plan['tasks'])
    for t in tables['tasks']:t.setdefault('result',None)
    tables['runs']=[{k:v for k,v in r.items() if k in {'id','root_task_id'}} for r in plan['runs']]
    tables['lanes']=[dict(id=l['id'],run_id=r['id'],parent_lane_id=l.get('parent_lane_id'),role=l['role'],workspace_mode=l['workspace']['mode']) for r in plan['runs'] for l in r['lanes']]
    tables['lane_tasks']=[dict(lane_id=l['id'],task_id=t,position=n,state='queued') for r in plan['runs'] for l in r['lanes'] for n,t in enumerate(l['tasks'])]
    for name in ['interfaces','barriers','invariants']:tables[name]=copy.deepcopy(plan.get(name,[]))
    tables['locks']=copy.deepcopy(plan.get('locks',[]))
    for name in ['checkpoints','gates']:tables[name]=[copy.deepcopy(x) for t in plan['tasks'] for x in t.get(name,[])]
    return {'tables':tables,'preconditions':{'project_uuid':MASTER['project_uuid'],'todo_revision':7,'workflow_revision':7,'workflow_authority_fingerprint':'fixture-authority'}}


def fake_preview():
    obs=fake_observation()
    obs['tables']={k:[] for k in obs['tables']}
    return {'native':{'valid':True,'status':'validated','project_uuid':MASTER['project_uuid'],'plan_digest':object_digest(PLAN),'would_add':[t['id'] for t in PLAN['tasks']],'would_modify':[],'warnings':[],'current_observation_preconditions':obs['preconditions']},'observation':obs}


class GraphAndProjectionTests(unittest.TestCase):
    def setUp(self):self.m=copy.deepcopy(MASTER)
    def bad(self):
        with self.assertRaises((PackageError,KeyError)):validate_structure(self.m)
    def test_delivered_structure(self):self.assertEqual(validate_structure(self.m)[0]['tasks'],len(PLAN['tasks']))
    def test_all_native_tasks_planned(self):self.assertTrue(all(t['status']=='planned' for t in PLAN['tasks']))
    def test_native_projection_is_byte_stable(self):self.assertEqual(canonical(PLAN),(PACKAGE/'machine'/MASTER['native_plan_file']).read_bytes())
    def test_duplicate_native_task_rejected(self):self.m['tasks'][2]['native']['id']=self.m['tasks'][3]['id'];self.bad()
    def test_foreign_prerequisite_rejected(self):self.m['tasks'][2]['native']['depends_on']=[{'type':'task','task_id':'FOREIGN'}];self.bad()
    def test_self_dependency_rejected(self):
        t=self.m['tasks'][2]['native'];t['depends_on']=[{'type':'task','task_id':t['id']}];self.bad()
    def test_epic_child_dependency_cycle_rejected(self):
        t=next(t['native'] for t in self.m['tasks'] if t['id']==self.m['root_task']);t['depends_on']=[{'type':'task','task_id':self.m['coordinator_task']}];self.bad()
    def test_coordinator_not_acquisition_blocked_on_final(self):
        t=next(t['native'] for t in self.m['tasks'] if t['id']==self.m['coordinator_task']);t['depends_on']=[{'type':'task','task_id':self.m['final_task']}];self.bad()
    def test_coordinator_seat_lock_required(self):
        t=next(t['native'] for t in self.m['tasks'] if t['id']==self.m['coordinator_task']);t['claim_locks']=[];self.bad()
    def test_integrator_is_not_isolated_producer(self):
        l=next(l for l in self.m['native_header']['runs'][0]['lanes'] if l['role']=='integrator');l['workspace']['mode']='isolated_merge';self.bad()
    def test_duplicate_lane_assignment_rejected(self):
        lanes=self.m['native_header']['runs'][0]['lanes'];lanes[1]['tasks'].append(lanes[2]['tasks'][0]);self.bad()
    def test_missing_lane_assignment_rejected(self):self.m['native_header']['runs'][0]['lanes'][1]['tasks'].pop();self.bad()
    def test_missing_parent_lane_rejected(self):self.m['native_header']['runs'][0]['lanes'][1]['parent_lane_id']='MISSING';self.bad()
    def test_future_work_profile_not_native(self):self.m['tasks'][2]['native']['work_profile']={};self.bad()
    def test_future_completed_task_rejected(self):self.m['tasks'][2]['native']['status']='done';self.bad()
    def test_unknown_claim_lock_rejected(self):self.m['tasks'][2]['native']['claim_locks']=['MISSING'];self.bad()
    def test_duplicate_checkpoint_rejected(self):
        owners=[t['native'] for t in self.m['tasks'] if t['native'].get('checkpoints')]
        owners[1]['checkpoints'][0]['id']=owners[0]['checkpoints'][0]['id'];self.bad()
    def test_coordinator_cannot_own_publish_interface(self):
        self.m['native_header']['interfaces'][0]['owner_task_id']=self.m['coordinator_task'];self.bad()
    def test_future_interface_not_prefrozen(self):self.m['native_header']['interfaces'][0]['state']='frozen';self.bad()
    def test_required_gate_not_optional(self):self.m['tasks'][2]['native']['gates'][0]['required']=False;self.bad()
    def test_unordered_overlapping_scope_rejected(self):
        _,edges=validate_structure(self.m);_,reach=dag([t['id'] for t in self.m['tasks']],edges)
        choices=[t for t in self.m['tasks'] if t['stream']!='COORD']
        pair=next((a,b) for a in choices for b in choices if a['id']<b['id'] and a['id'] not in reach[b['id']] and b['id'] not in reach[a['id']])
        for t in pair:t['native']['scope']['exclusive_paths'].append('src/shared-fixture')
        self.bad()
    def test_safe_parallelism_not_parent_completion_order(self):
        _,edges=validate_structure(self.m);self.assertFalse(any(a==self.m['root_task'] for a,b in edges))


class ProfilesAndIntegrityTests(unittest.TestCase):
    def test_profiles_have_four_semantic_fields(self):
        for t in MASTER['tasks']:check_profile(t['work_profile'])
    def test_profile_rejects_model(self):
        p=copy.deepcopy(MASTER['tasks'][2]['work_profile']);p['model']='fictional'
        with self.assertRaises(PackageError):check_profile(p)
    def test_profile_rejects_missing_member(self):
        p=copy.deepcopy(MASTER['tasks'][2]['work_profile']);del p['risk']
        with self.assertRaises(PackageError):check_profile(p)
    def test_profile_rejects_enum_typo(self):
        p=copy.deepcopy(MASTER['tasks'][2]['work_profile']);p['difficulty']='rountine'
        with self.assertRaises(PackageError):check_profile(p)
    def test_profile_rejects_nonstring(self):
        p=copy.deepcopy(MASTER['tasks'][2]['work_profile']);p['risk']=5
        with self.assertRaises(PackageError):check_profile(p)
    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'a.json';p.write_text('{"a":1,"a":2}')
            with self.assertRaises(PackageError):load(p)
    def test_paths_reject_traversal_root_absolute_and_nul(self):
        for p in ['../x','/x','.','','a\\b','a\0b',None]:
            with self.subTest(path=p),self.assertRaises(PackageError):relpath(p)
    def test_symlink_dereference_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'a').write_text('x');(root/'b').symlink_to(root/'a')
            with self.assertRaises(PackageError):contained(root,'b')
    def test_manifest_valid_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'a.txt').write_text('x');(r/'MANIFEST.sha256').write_text(digest(r/'a.txt')+'  a.txt\n');self.assertEqual(manifest_check(r),1)
    def test_manifest_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'a.txt').write_text('x');(r/'MANIFEST.sha256').write_text(digest(r/'a.txt')+'  a.txt\n');(r/'a.txt').write_text('y')
            with self.assertRaises(PackageError):manifest_check(r)
    def test_manifest_detects_unlisted_file(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'a.txt').write_text('x');(r/'MANIFEST.sha256').write_text(digest(r/'a.txt')+'  a.txt\n');(r/'extra').write_text('y')
            with self.assertRaises(PackageError):manifest_check(r)
    def test_evidence_must_remain_external(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PackageError):new_output(Path(d)/'receipt.json',[Path(d)])
    def test_existing_evidence_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json';write_new(p,{'a':1})
            with self.assertRaises(PackageError):new_output(p)
    def test_content_reference_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'e').write_text('real')
            with self.assertRaises(PackageError):file_refs([{'domain':'evidence','path':'e','sha256':'0'*64}],source_root=r,evidence_root=r)


class ApprovalAndRuntimePortTests(unittest.TestCase):
    def test_fresh_review_accepted(self):check_age(100,now=101)
    def test_expired_review_refused(self):
        with self.assertRaises(PackageError):check_age(0,now=3601)
    def test_future_review_refused(self):
        with self.assertRaises(PackageError):check_age(200,now=100)
    def test_valid_additive_preview(self):tb.accepted_preview(fake_preview(),PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_native_warning_refused(self):
        p=fake_preview();p['native']['warnings']=['stale']
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_existing_record_modification_refused(self):
        p=fake_preview();p['native']['would_modify']=['historical']
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_wrong_native_digest_refused(self):
        p=fake_preview();p['native']['plan_digest']='0'*64
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_partial_task_addition_list_refused(self):
        p=fake_preview();p['native']['would_add'].pop()
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_exact_existing_id_verification_accepted(self):
        p=fake_preview();p['native']['would_add']=[];p['native']['would_modify']=[t['id'] for t in PLAN['tasks']]
        self.assertEqual(tb.accepted_verification(p,PLAN),'existing_task_ids_reported_as_updates')
    def test_partial_existing_id_verification_rejected(self):
        p=fake_preview();p['native']['would_add']=[];p['native']['would_modify']=[t['id'] for t in PLAN['tasks']][:-1]
        with self.assertRaises(PackageError):tb.accepted_verification(p,PLAN)
    def test_foreign_existing_id_verification_rejected(self):
        p=fake_preview();p['native']['would_add']=[];p['native']['would_modify']=[t['id'] for t in PLAN['tasks']]+['FOREIGN']
        with self.assertRaises(PackageError):tb.accepted_verification(p,PLAN)
    def test_hidden_interface_collision_refused(self):
        p=fake_preview();p['observation']['tables']['interfaces']=[PLAN['interfaces'][0]]
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_missing_authority_inventory_refused(self):
        p=fake_preview();del p['observation']['tables']['locks']
        with self.assertRaises(PackageError):tb.accepted_preview(p,PLAN,{'project_uuid':MASTER['project_uuid']})
    def test_imported_fields_verified(self):self.assertEqual(tb.verify_records(PLAN,fake_observation(),strict_import=True)['verified_tasks'],len(PLAN['tasks']))
    def test_imported_role_mismatch_rejected(self):
        o=fake_observation();o['tables']['lanes'][0]['role']='implementer'
        with self.assertRaises(PackageError):tb.verify_records(PLAN,o)
    def test_imported_queue_mismatch_rejected(self):
        o=fake_observation();o['tables']['lane_tasks'].pop()
        with self.assertRaises(PackageError):tb.verify_records(PLAN,o)
    def test_import_must_not_start_workers(self):
        o=fake_observation();o['tables']['tasks'][0]['status']='in_progress'
        with self.assertRaises(PackageError):tb.verify_records(PLAN,o,strict_import=True)
    def test_validate_port_cannot_call_apply(self):
        calls=[]
        class Mutation:
            @staticmethod
            def validate_native_plan(c,p,n):calls.append('validate');return {'valid':True}
            @staticmethod
            def apply_proposal(*args):raise AssertionError('validate reached apply')
        with patch.object(nb,'observe',return_value={'fixture':True}),patch.object(nb,'api',return_value=(Mutation,None,None,None,None)):
            r=nb.operation({'operation':'validate','project':'fixture','repo':'fixture','plan':{}})
        self.assertEqual(calls,['validate']);self.assertTrue(r['native']['valid'])
    def test_apply_port_uses_original_preconditions_once(self):
        calls=[];expected={'revision':'reviewed-not-current'}
        class Preconditions:
            @staticmethod
            def model_validate(x):return x
        class Proposal:
            @staticmethod
            def create(**kwargs):return kwargs
        class Mutation:
            @staticmethod
            def apply_proposal(c,p,proposal):calls.append(proposal);return {'applied':True}
        models=SimpleNamespace(ObservationPreconditions=Preconditions,ProposalEnvelope=Proposal)
        with patch.object(nb,'observe',return_value={'revision':'newer'}),patch.object(nb,'api',return_value=(Mutation,models,None,None,None)):
            nb.operation({'operation':'apply','project':'fixture','repo':'fixture','plan':{},'approved_preconditions':expected,'run_id':'RUN','confirm':'APPLY-RUN'})
        self.assertEqual(len(calls),1);self.assertEqual(calls[0]['observation_preconditions'],expected)
    def test_apply_port_rejects_wrong_confirmation(self):
        with patch.object(nb,'observe',return_value={}),patch.object(nb,'api',return_value=(None,None,None,None,None)):
            with self.assertRaises(PackageError):nb.operation({'operation':'apply','project':'fixture','repo':'fixture','plan':{},'run_id':'RUN','confirm':'yes'})
    def test_one_attempt_evidence_marker_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'preview.attempt.json';write_new(p,{'attempt':'first'})
            with self.assertRaises(FileExistsError):write_new(p,{'attempt':'second'})
            self.assertEqual(load(p),{'attempt':'first'})


class ExactTestInventoryTests(unittest.TestCase):
    def run_fixture(self,body,names=('C.test_one',)):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);(r/'case.py').write_text('import unittest\n'+body)
            return strict_run(r,{'path':'case.py','test_names':list(names)})
    def test_real_positive_fixture(self):
        r=self.run_fixture('class C(unittest.TestCase):\n def test_one(self): self.assertEqual(2+2,4)\n');self.assertEqual(r['tests_run'],1);self.assertEqual(r['status'],'passed')
    def test_failure_not_pass(self):self.assertEqual(self.run_fixture('class C(unittest.TestCase):\n def test_one(self): self.fail("intentional")\n')['status'],'failed')
    def test_skip_not_pass(self):self.assertEqual(self.run_fixture('class C(unittest.TestCase):\n @unittest.skip("not implemented")\n def test_one(self): pass\n')['status'],'failed')
    def test_expected_failure_not_pass(self):self.assertEqual(self.run_fixture('class C(unittest.TestCase):\n @unittest.expectedFailure\n def test_one(self): self.fail("expected")\n')['status'],'failed')
    def test_zero_test_inventory_refused(self):
        with self.assertRaises(PackageError):self.run_fixture('class C(unittest.TestCase): pass\n',())
    def test_duplicate_test_inventory_refused(self):
        with self.assertRaises(PackageError):self.run_fixture('class C(unittest.TestCase):\n def test_one(self): pass\n',('C.test_one','C.test_one'))
    def test_missing_test_not_pass(self):
        with self.assertRaises(PackageError):self.run_fixture('class C(unittest.TestCase): pass\n')
    def test_missing_file_refused(self):
        with tempfile.TemporaryDirectory() as d,self.assertRaises(PackageError):strict_run(Path(d),{'path':'missing.py','test_names':['C.test_one']})
    def test_future_gate_requires_bindings(self):
        env={k:v for k,v in os.environ.items() if k!='WF2_BINDINGS'}
        r=subprocess.run([sys.executable,'-B',str(PACKAGE/'scripts/run_gate.py'),'--task',MASTER['tasks'][2]['id']],env=env,text=True,capture_output=True)
        self.assertNotEqual(r.returncode,0);self.assertFalse(json.loads(r.stdout)['qualification'])


if __name__=='__main__':
    unittest.main(verbosity=2)
