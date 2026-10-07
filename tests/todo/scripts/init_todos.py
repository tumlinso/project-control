#!/usr/bin/env python3
"""Initialize root and workstream todo ledgers."""

from __future__ import annotations

import argparse
from pathlib import Path

from todo_common import ensure_agents_md, ensure_root_files, ensure_workstream_file, normalize_slug
from v2_compat import migration_error, v2_project_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize todos.md and optional workstream ledgers.")
    parser.add_argument("--repo-root", default=".", help="Repository root that should contain todos.md.")
    parser.add_argument("--objective", help="Objective summary for a new workstream.")
    parser.add_argument("--workstream", help="Optional workstream slug. Derived from objective if omitted.")
    parser.add_argument("--owner", default="unassigned", help="Owner or agent label for the workstream entry.")
    parser.add_argument(
        "--status",
        default="planned",
        choices=["planned", "in_progress", "blocked", "stale", "done", "superseded"],
        help="Initial workstream status.",
    )
    parser.add_argument(
        "--execution-state",
        choices=["ready", "claimed", "idle", "closed"],
        help="Optional pickup state for todo-status.md. Defaults from the workstream status.",
    )
    parser.add_argument(
        "--update-agents",
        action="store_true",
        help="Also create or update repo-level AGENTS.md with the workflow ledger guidance.",
    )
    parser.add_argument("--stale-after-days", type=int, help="Override the initial stale threshold for the workstream.")
    parser.add_argument("--review-now", action="store_true", help="Initialize the workstream with an explicit review timestamp.")
    parser.add_argument("--superseded-by", help="Slug of the workstream that superseded this one.")
    parser.add_argument("--waiting-on", help="Short note about the outstanding dependency for this workstream.")
    parser.add_argument("--stale-reason", help="Reason recorded when initializing a stale workstream.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if v2_project_exists(repo_root):
        return migration_error(repo_root, "init_todos.py", "plan apply --file <plan.json>")

    ensure_root_files(repo_root)
    created_path = repo_root / "todos.md"

    if args.objective or args.workstream:
        slug_source = args.workstream or args.objective or "workstream"
        slug = normalize_slug(slug_source)
        created_path = ensure_workstream_file(
            repo_root,
            slug=slug,
            objective=args.objective or slug.replace("-", " "),
            status=args.status,
            owner=args.owner,
            execution=args.execution_state,
            stale_after_days=args.stale_after_days,
            superseded_by=args.superseded_by,
            waiting_on=args.waiting_on,
            stale_reason=args.stale_reason,
            touch_review=args.review_now,
        )

    if args.update_agents:
        ensure_agents_md(repo_root)

    print(created_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
