#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "packaging"))

from installer import InstallError, build_candidate, candidate_manifest_digest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an isolated Project Control candidate")
    parser.add_argument("--project-control-root", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        identity = build_candidate(
            project_control_root=args.project_control_root,
            skills_root=args.skills_root,
            destination=args.destination,
        )
    except InstallError as exc:
        parser.error(str(exc))
    print(json.dumps({"identity": asdict(identity), "digest": candidate_manifest_digest(args.destination)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
