#!/usr/bin/env python3
"""Execute required AS1 product tests; missing/skipped cases are not success.

Product test implementations are written in the target repository by AS1 work.
This helper is delivered now; it does not pretend those tests already exist.
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path

PACKAGE=Path(__file__).resolve().parents[1]


def assess(expected: set[str], report: dict, returncode: int) -> dict:
    rows=report.get('cases',{})
    passed={case for case,results in rows.items() if results and any(r.get('outcome')=='passed' for r in results) and not any(r.get('outcome')=='failed' for r in results)}
    missing=sorted(expected-passed)
    okay=returncode==0 and report.get('pytest_exitstatus')==0 and not missing
    return {'status':'passed' if okay else 'failed','required_cases':sorted(expected),'passed_cases':sorted(expected&passed),'missing_or_failed_cases':missing,'pytest_returncode':returncode,'kind':'executed_product_acceptance'}


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--outcome',required=True);ap.add_argument('--repo',type=Path,default=Path.cwd())
    args=ap.parse_args(); repo=args.repo.resolve()
    outcomes=json.loads((PACKAGE/'planning/outcomes.json').read_text())['outcomes']
    selected=next((o for o in outcomes if o['id']==args.outcome),None)
    if selected is None:
        print(json.dumps({'status':'failed','reason':'unknown_outcome'}));return 2
    target=repo/selected['test_file']
    if not target.is_file():
        print(json.dumps({'status':'failed','reason':'product_acceptance_not_implemented','required_test_file':selected['test_file'],'required_cases':selected['acceptance_cases']}));return 2
    with tempfile.TemporaryDirectory(prefix='pc-as1-gate-') as temp:
        report_path=Path(temp)/'cases.json'
        argv=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','-p','as1_pytest','--as1-report',str(report_path),selected['test_file']]
        env=dict(os.environ);env['PYTHONDONTWRITEBYTECODE']='1'
        env['PYTHONPATH']=str(PACKAGE/'scripts')+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        try:
            proc=subprocess.run(argv,cwd=repo,env=env,capture_output=True,text=True,timeout=1100)
        except subprocess.TimeoutExpired:
            print(json.dumps({'status':'failed','reason':'acceptance_timeout','outcome':args.outcome}));return 2
        report=json.loads(report_path.read_text()) if report_path.is_file() else {}
        result=assess(set(selected['acceptance_cases']),report,proc.returncode)
        result.update(outcome=args.outcome,test_file=selected['test_file'],test_output_tail=(proc.stdout+'\n'+proc.stderr)[-12000:])
        print(json.dumps(result,sort_keys=True));return 0 if result['status']=='passed' else 1

if __name__=='__main__': raise SystemExit(main())
