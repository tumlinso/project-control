#!/usr/bin/env python3
"""Review workstream freshness and optionally mark stale candidates as stale."""

from __future__ import annotations

import argparse
from pathlib import Path

from todo_common import (
    ensure_root_files,
    ensure_workstream_frontmatter,
    persist_workstream_doc,
    review_workstream_staleness,
    write_staleness_review,
)
from v2_compat import migration_error, v2_project_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review workstream freshness for stale candidates.")
    parser.add_argument("--repo-root", default=".", help="Repository root that contains todos.md.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show the current freshness classifications without mutating files.")
    mode.add_argument("--apply", action="store_true", help="Mark eligible stale candidates as stale and refresh todo-status.md.")
    return parser


def print_review(results: list[dict[str, object]]) -> None:
    if not results:
        print("No tracked workstreams.")
        return
    print(f"Reviewed workstreams: {len(results)}")
    for item in results:
        classification = item["classification"]
        if classification == "done":
            continue
        age_days = item["age_days"]
        age_text = "unknown" if age_days is None else f"{float(age_days):.1f}d"
        print(
            f"- {item['slug']} [{classification}] age={age_text} threshold={item['threshold_days']}d reason={item['reason']}"
        )
        if item.get("claimed_inconsistency"):
            print(f"  inconsistency: {item['slug']} is stale but still marked claimed.")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if v2_project_exists(repo_root):
        return migration_error(repo_root, "review_staleness.py", "audit --json")
    ensure_root_files(repo_root)

    results = review_workstream_staleness(repo_root)
    print_review(results)

    if not args.apply:
        print("Dry run only. Re-run with --apply to update stale statuses and refresh `todo-status.md`.")
        return 0

    updated = 0
    for item in results:
        if not item.get("eligible_for_stale_apply"):
            continue
        doc = item["doc"]
        ensure_workstream_frontmatter(
            doc,
            slug=str(item["slug"]),
            objective=str(item["objective"]),
            status="stale",
            owner=str(item["owner"]),
            execution="closed",
            touch_heartbeat=False,
            touch_review=True,
            stale_after_days=int(item["threshold_days"]),
            stale_reason=str(item["reason"]),
        )
        persist_workstream_doc(repo_root, str(item["slug"]), doc, objective=str(item["objective"]))
        updated += 1

    refreshed = review_workstream_staleness(repo_root)
    write_staleness_review(repo_root, refreshed)
    print(f"Updated stale statuses: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
