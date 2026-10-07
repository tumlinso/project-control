#!/usr/bin/env python3
"""Explicit cleanup for completed todo-orchestrator workstreams."""

from __future__ import annotations

import argparse
from pathlib import Path

from todo_common import (
    DONE_STATUSES,
    ROOT_SECTION_ORDER,
    STATUS_SECTION_ORDER,
    WORKSTREAM_PLACEHOLDER,
    clear_placeholder,
    load_root_doc,
    load_status_doc,
    parse_cleanup_scope,
    parse_workstream_entries,
    rebuild_root_after_cleanup,
    rebuild_status_after_cleanup,
    remove_status_entry,
    remove_workstream_entry,
    write_document,
)
from v2_compat import migration_error, v2_project_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean up completed todo ledgers.")
    parser.add_argument("--repo-root", default=".", help="Repository root that contains todos.md.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow cleanup of only the selected cleanup-eligible statuses while leaving survivors intact.",
    )
    parser.add_argument(
        "--scope",
        help="Comma-separated cleanup scope. In partial mode defaults to done,superseded; add stale explicitly to remove stale streams.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show what cleanup would do without modifying files.")
    mode.add_argument("--apply", action="store_true", help="Delete completed workstream files and compact ledgers.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if v2_project_exists(repo_root):
        return migration_error(repo_root, "cleanup_todos.py", "cleanup --json")

    root_doc = load_root_doc(repo_root)
    status_doc = load_status_doc(repo_root)
    entries = parse_workstream_entries(clear_placeholder(root_doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    try:
        cleanup_scope = parse_cleanup_scope(args.scope, partial=args.partial)
    except ValueError as exc:
        parser.error(str(exc))
    active = [entry["slug"] for entry in entries if entry["status"] not in DONE_STATUSES and entry["status"] != "stale"]
    stale = [entry["slug"] for entry in entries if entry["status"] == "stale"]
    to_delete = [(entry["slug"], repo_root / entry["file"]) for entry in entries if entry["status"] in cleanup_scope]
    delete_slugs = {slug for slug, _ in to_delete}
    cleanup_label = "todo-cleanup --partial" if args.partial else "todo-cleanup"

    print(f"Tracked workstreams: {len(entries)}")
    if not args.partial and (active or stale):
        print("Cleanup blocked.")
        if active:
            print("Active workstreams:")
            for slug in active:
                print(f"- {slug}")
        if stale:
            print("Stale workstreams pending review:")
            for slug in stale:
                print(f"- {slug}")
        if to_delete:
            print("Cleanup-eligible workstreams:")
            for slug, path in to_delete:
                suffix = "" if path.exists() else " (file already missing)"
                print(f"- {slug} -> {path.relative_to(repo_root)}{suffix}")
        return 1

    if args.partial:
        print("Partial cleanup mode.")
        print("Selected scope:")
        for status in sorted(cleanup_scope):
            print(f"- {status}")
        surviving_active = [slug for slug in active if slug not in delete_slugs]
        surviving_stale = [slug for slug in stale if slug not in delete_slugs]
        if surviving_active:
            print("Active workstreams kept:")
            for slug in surviving_active:
                print(f"- {slug}")
        if surviving_stale:
            print("Stale workstreams kept:")
            for slug in surviving_stale:
                print(f"- {slug}")

    if to_delete:
        print("Cleanup targets:")
        for slug, path in to_delete:
            suffix = "" if path.exists() else " (file already missing)"
            print(f"- {slug} -> {path.relative_to(repo_root)}{suffix}")
    else:
        print("Cleanup targets: none")

    if not args.apply:
        print("Dry run only. Re-run with --apply to compact ledgers.")
        return 0

    for slug, path in to_delete:
        if path.exists():
            path.unlink()
        remove_workstream_entry(root_doc, slug)
        remove_status_entry(status_doc, slug)

    removed_slugs = [slug for slug, _ in to_delete]
    rebuild_root_after_cleanup(root_doc, removed_slugs=removed_slugs, cleanup_label=cleanup_label)
    write_document(repo_root / "todos.md", root_doc, ROOT_SECTION_ORDER)

    rebuild_status_after_cleanup(repo_root, status_doc)
    write_document(repo_root / "todo-status.md", status_doc, STATUS_SECTION_ORDER)

    if args.partial:
        if removed_slugs:
            print("Partial cleanup complete.")
        else:
            print("Partial cleanup complete. No matching workstreams were removed.")
    else:
        print("Cleanup complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
