#!/usr/bin/env python3
"""Record a trusted operator observation of a completed external Codex producer.

Run only after observing the exact configured agent's terminal lifecycle state.
This records external lifecycle provenance, not kernel process-stop evidence.
It is deliberately not registered as an MCP tool or worker operation.
"""
from __future__ import annotations
import argparse
import json
from project_control.config import load_config
from project_control.registry import WorkspaceRegistry
from project_control.native_providers import record_external_producer_completion


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--repository')
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--producer-id', required=True, help='Exact configured Codex agent identity observed by the operator')
    parser.add_argument('--observed-agent-state', required=True, choices=['completed'])
    args = parser.parse_args(argv)
    config = load_config()
    registry = WorkspaceRegistry(config)
    workspace = registry.workspace(args.project)
    alias = args.repository or workspace.authority_repository
    if not alias:
        parser.error('select a registered repository')
    root = registry.repository(args.project, alias).root
    result = record_external_producer_completion(config, args.project, root,
        principal='project-control-mutator', profile='mutator', attempt_id=args.attempt_id,
        producer_id=args.producer_id, lifecycle_state=args.observed_agent_state)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
