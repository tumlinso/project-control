#!/usr/bin/env python3
"""Summarize the canonical todo ledger."""

from __future__ import annotations

import argparse
from pathlib import Path

from todo_common import (
    PLACEHOLDER,
    STATUS_PLACEHOLDER,
    WORKSTREAM_PLACEHOLDER,
    clear_placeholder,
    load_root_doc,
    load_status_doc,
    parse_status_entries,
    parse_workstream_entries,
    pickup_ready_entries,
    review_workstream_staleness,
)
from v2_compat import v2_project_exists


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize root todos.md.")
    parser.add_argument("--repo-root", default=".", help="Repository root that contains todos.md.")
    return parser


def first_real_line(lines: list[str]) -> str:
    cleaned = clear_placeholder(lines)
    return cleaned[0] if cleaned else "No summary recorded."


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    if v2_project_exists(repo_root):
        package_root = Path(__file__).resolve().parents[1]
        if str(package_root) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(package_root))
        from todo_orchestrator.service import Service
        import json
        print(json.dumps(Service(repo_root).status(), indent=2, sort_keys=True))
        return 0
    root_doc = load_root_doc(repo_root)
    status_doc = load_status_doc(repo_root)

    summary = first_real_line(root_doc.sections.get("Summary", [PLACEHOLDER]))
    blockers = clear_placeholder(root_doc.sections.get("Global Blockers", [PLACEHOLDER]))
    next_actions = clear_placeholder(root_doc.sections.get("Next Actions", [PLACEHOLDER]))
    entries = parse_workstream_entries(clear_placeholder(root_doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    status_entries = parse_status_entries(clear_placeholder(status_doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    ready_entries = pickup_ready_entries(status_entries)
    claimed_entries = [entry for entry in status_entries if entry["execution"] == "claimed"]
    reviews = review_workstream_staleness(repo_root)
    stale_candidates = [entry for entry in reviews if entry["classification"] == "stale_candidate"]
    stale_entries = [entry for entry in reviews if entry["classification"] == "stale"]
    stale_claimed = [entry for entry in reviews if entry.get("claimed_inconsistency")]
    cleanup = clear_placeholder(status_doc.sections.get("Cleanup Status", [PLACEHOLDER]))

    print(f"Summary: {summary}")
    if entries:
        print("Workstreams:")
        for entry in entries:
            print(f"- {entry['slug']} [{entry['status']}] -> {entry['objective']}")
    else:
        print("Workstreams: none")
    if ready_entries:
        print("Pickup Ready:")
        for entry in ready_entries:
            print(f"- {entry['slug']} [{entry['status']}/{entry['execution']}] -> {entry['next']}")
    if claimed_entries:
        print("Claimed:")
        for entry in claimed_entries:
            print(f"- {entry['slug']} by {entry['owner']} -> {entry['next']}")
    if stale_candidates:
        print("Stale Candidates:")
        for entry in stale_candidates:
            age_text = "unknown" if entry["age_days"] is None else f"{float(entry['age_days']):.1f}d"
            print(f"- {entry['slug']} [{age_text} > {entry['threshold_days']}d] -> {entry['reason']}")
    if stale_entries:
        print("Stale:")
        for entry in stale_entries:
            print(f"- {entry['slug']} -> {entry['reason']}")
    if stale_claimed:
        print("Claimed But Stale:")
        for entry in stale_claimed:
            print(f"- {entry['slug']} is stale but still marked claimed.")
    if blockers:
        print("Blockers:")
        for blocker in blockers:
            print(blocker)
    if next_actions:
        print("Next Actions:")
        for action in next_actions:
            print(action)
    if cleanup:
        print("Cleanup Status:")
        for line in cleanup:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
