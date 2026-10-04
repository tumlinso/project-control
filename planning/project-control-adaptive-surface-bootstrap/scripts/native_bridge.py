#!/usr/bin/env python3
"""Use the currently installed Project Control plan backend with pinned validation.

Run under the actual bound candidate/installed Python. This is not a new product
API or a direct Todo/SQLite editor. It imports functions observed in the current
Project Control surface. Missing/incompatible bindings fail closed.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import sys


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=['validate','apply'])
    ap.add_argument('--project',required=True);ap.add_argument('--repo',type=Path,required=True)
    ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--receipt',type=Path)
    args=ap.parse_args()
    try:
        from project_control.config import load_config
        from project_control.registry import WorkspaceRegistry
        from project_control.mutation import validate_native_plan, apply_proposal
        from project_control.models import ProposalEnvelope, ObservationPreconditions
        config=load_config()
        registered=WorkspaceRegistry(config).repository(args.project).root.resolve(strict=True)
        if registered!=args.repo.resolve(strict=True):
            raise ValueError('supplied repo is not the registered authority repository')
        source=args.plan.read_bytes();plan=json.loads(source)
        sha=hashlib.sha256(source).hexdigest()
        local_identity=json.loads((registered/'.todo-orchestrator/project.json').read_text())['project_uuid']
        if args.action=='validate':
            result=validate_native_plan(config,args.project,plan)
            if result.get('project_uuid')!=local_identity: raise ValueError('registered project UUID mismatch')
            output={'format':'pc-as1-native-validation/1','project':args.project,'repo':str(registered),'plan_file_sha256':sha,'result':result}
        else:
            if args.receipt is None: raise ValueError('saved validation receipt is required')
            saved=json.loads(args.receipt.read_text())
            if saved.get('format')!='pc-as1-native-validation/1' or saved.get('project')!=args.project or saved.get('repo')!=str(registered) or saved.get('plan_file_sha256')!=sha:
                raise ValueError('validation receipt does not match this exact project/plan')
            validation=saved['result']
            if not validation.get('valid') or validation.get('would_modify'):
                raise ValueError('only a valid additive plan may be bootstrapped; inspect existing work')
            if validation.get('project_uuid')!=local_identity: raise ValueError('validation authority UUID mismatch')
            expected={t['id'] for t in plan['tasks']}
            if set(validation.get('would_add',[])) != expected:
                raise ValueError('bootstrap expected all new task IDs; partial/existing plan requires explicit reconciliation')
            # Crucially: do not create these preconditions from a fresh snapshot.
            conditions=ObservationPreconditions.model_validate(validation['current_observation_preconditions'])
            proposal=ProposalEnvelope.create(intent=f'AS1 additive bootstrap for {args.project}',proposed_change=plan,observation_preconditions=conditions)
            result=apply_proposal(config,args.project,proposal)
            output={'format':'pc-as1-native-apply/1','project':args.project,'repo':str(registered),'plan_file_sha256':sha,'result':result}
        print(json.dumps(output,sort_keys=True,default=str))
        return 0
    except Exception as e:
        # No exception traceback/source or arbitrary environment contents on stdout.
        print(json.dumps({'status':'failed','error_type':type(e).__name__,'error':str(e)[:1500],'hint':'Use the bound installed Project Control Python, inspect current authority, and revalidate. Do not bypass the canonical backend.'},sort_keys=True))
        return 2

if __name__=='__main__': raise SystemExit(main())
