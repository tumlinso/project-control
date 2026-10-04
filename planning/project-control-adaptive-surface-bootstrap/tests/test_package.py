from __future__ import annotations
import json, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_package import check, relative_path, assert_dag
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

if __name__=='__main__':unittest.main()
