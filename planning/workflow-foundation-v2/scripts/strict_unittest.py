#!/usr/bin/env python3
"""Execute an exact future acceptance inventory; zero tests or skips are failure."""
from __future__ import annotations
import argparse,contextlib,hashlib,importlib.util,io,json,os,sys,time,unittest
from pathlib import Path
sys.dont_write_bytecode=True
from common import contained,demand,load,PackageError,digest

def run(root:Path,spec:dict):
    names=spec.get('test_names')
    demand(isinstance(names,list) and bool(names) and all(isinstance(n,str) and n for n in names) and len(names)==len(set(names)),'test names must be a nonempty unique exact inventory')
    path=contained(root,spec['path']);specification=importlib.util.spec_from_file_location('wf2_case_'+hashlib.sha256(str(path).encode()).hexdigest()[:12],path)
    demand(specification and specification.loader,'test module loader unavailable')
    module=importlib.util.module_from_spec(specification);sys.modules[specification.name]=module
    root=str(root.resolve());sys.path.insert(0,root)
    test_output=io.StringIO()
    with contextlib.redirect_stdout(test_output),contextlib.redirect_stderr(test_output):
        specification.loader.exec_module(module)
        suite=unittest.TestSuite();loader=unittest.TestLoader()
        for name in spec['test_names']:suite.addTests(loader.loadTestsFromName(name,module))
        demand(not loader.errors,'test inventory could not be loaded: '+str(loader.errors))
        expected=len(spec['test_names']);demand(expected>0 and suite.countTestCases()==expected,'empty/expanded test inventory is not exact')
        start=time.monotonic();result=unittest.TextTestRunner(stream=test_output,verbosity=2).run(suite)
    success=result.wasSuccessful() and result.testsRun==expected and not result.skipped and not result.expectedFailures and not result.unexpectedSuccesses
    return {'format':'wf2-test-execution-v1','source_test_file':spec['path'],'test_file_sha256':digest(path),'test_names':spec['test_names'],'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped),'expected_failures':len(result.expectedFailures),'unexpected_successes':len(result.unexpectedSuccesses),'elapsed_seconds':time.monotonic()-start,'status':'passed' if success else 'failed','log':test_output.getvalue()}

if __name__=='__main__':
    a=argparse.ArgumentParser(description=__doc__);a.add_argument('--repo',type=Path,required=True);a.add_argument('--spec',type=Path,required=True);o=a.parse_args()
    try:report=run(o.repo,load(o.spec));print(json.dumps(report));raise SystemExit(0 if report['status']=='passed' else 1)
    except (PackageError,OSError) as e:print(json.dumps({'status':'failed','error':str(e)}));raise SystemExit(2)
