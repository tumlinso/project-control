from __future__ import annotations
import copy, json, shutil, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_package import check, check_lanes, relative_path, assert_dag
from acceptance_gate import assess

class PackageTests(unittest.TestCase):
    def test_consistent_complete_package(self):
        result=check(ROOT)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['outcomes'],16)
        self.assertEqual(result['native_tasks'],{'project-control':11,'skills':7})

    def test_relative_paths_accept_normal(self):
        for p in ['src/file.py','docs/a b.md','src/Δ.cpp']:
            self.assertEqual(relative_path(p),p)
        self.assertEqual(relative_path('.',allow_dot=True),'.')

    def test_relative_paths_reject_escape(self):
        for p in ['/etc/passwd','../x','a/../b','C:/x','C:x',r'\\server\x','a\x00b',r'a\b','']:
            with self.subTest(p=p),self.assertRaises(ValueError): relative_path(p)

    def test_cycle_is_rejected(self):
        with self.assertRaises(ValueError): assert_dag({'a','b'},[('a','b'),('b','a')])

    def test_unknown_endpoint_is_rejected(self):
        with self.assertRaises(ValueError): assert_dag({'a'},[('b','a')])

    def test_missing_required_case_not_success(self):
        self.assertEqual(assess({'X'},{'pytest_exitstatus':0,'cases':{}},0)['status'],'failed')

    def test_skipped_only_case_not_success(self):
        self.assertEqual(assess({'X'},{'pytest_exitstatus':0,'cases':{'X':[{'outcome':'skipped'}]}},0)['status'],'failed')

    def test_failed_test_cannot_be_overridden_by_other_pass(self):
        r={'pytest_exitstatus':0,'cases':{'X':[{'outcome':'passed'},{'outcome':'failed'}]}}
        self.assertEqual(assess({'X'},r,0)['status'],'failed')

    def test_executed_case_success(self):
        r={'pytest_exitstatus':0,'cases':{'X':[{'outcome':'passed'}]}}
        self.assertEqual(assess({'X'},r,0)['status'],'passed')

    def test_failed_teardown_or_collection_keeps_gate_failed(self):
        r={'pytest_exitstatus':1,'cases':{'X':[{'outcome':'passed'}]}}
        self.assertEqual(assess({'X'},r,1)['status'],'failed')

    def test_native_roles_have_no_observer_adapter(self):
        spec=json.loads((ROOT/'contracts/surface.json').read_text())
        for name,p in spec['profiles'].items():
            self.assertFalse(p['automatic_overview'])
            if name!='observer':
                self.assertFalse({'read','skill'}&set(p['tools']))
                self.assertNotIn('extended',p['details'])

    def test_stale_preview_correction_does_not_accept_further_plan_drift(self):
        for project in ('project-control', 'skills'):
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                copied=Path(directory)/'package'
                shutil.copytree(ROOT,copied)
                plan_path=copied/f'planning/{project}.todo-plan.json'
                plan=json.loads(plan_path.read_text())
                plan['tasks'][0]['title']='Unrecorded edit'
                plan_path.write_text(json.dumps(plan,indent=2)+'\n')
                with self.assertRaisesRegex(ValueError,'explicit stale correction'):
                    check(copied)

    def test_parallel_lanes_reject_missing_duplicate_or_unowned_integration(self):
        plan=json.loads((ROOT/'planning/project-control.todo-plan.json').read_text())
        check_lanes(plan)
        for failure in ('missing', 'duplicate', 'unknown_target', 'non_integrator', 'missing_dependency'):
            with self.subTest(failure=failure):
                changed=copy.deepcopy(plan)
                lanes=changed['runs'][0]['lanes']
                if failure=='missing': lanes[1]['tasks'].pop()
                elif failure=='duplicate': lanes[2]['tasks'].append(lanes[1]['tasks'][0])
                elif failure=='unknown_target': lanes[1]['workspace']['integration_task_id']='PC-AS1-UNKNOWN'
                elif failure=='non_integrator': lanes[0]['role']='implementer'
                else:
                    surface=next(t for t in changed['tasks'] if t['id']=='PC-AS1-SURFACE')
                    surface['depends_on'].pop()
                with self.assertRaises(ValueError): check_lanes(changed)

    def test_search_absorbs_exact_lookup_without_public_find(self):
        spec=json.loads((ROOT/'contracts/surface.json').read_text())
        self.assertEqual(len(spec['shared_information_tools']),8)
        self.assertNotIn('find',spec['shared_information_tools'])
        self.assertIn('find',spec['removed_default_names'])
        for profile in spec['profiles'].values():
            self.assertNotIn('find',profile['tools'])
            self.assertIn('search',profile['tools'])
        import jsonschema
        schema=json.loads((ROOT/'contracts/search-query.schema.json').read_text())
        validator=jsonschema.Draft202012Validator(schema)
        validator.validate('def X')
        for kind in ('task','gate','interface','decision','run','packet','investigation'):
            validator.validate({'kind':kind,'target':'known-id'})
        for query in ({'kind':'task'},{'target':'known-id'},{'kind':'task','target':''}):
            self.assertFalse(validator.is_valid(query))

if __name__=='__main__':unittest.main()
