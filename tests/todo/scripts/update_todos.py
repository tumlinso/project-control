#!/usr/bin/env python3
"""Update root or workstream todo ledgers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from todo_common import (
    ROOT_SECTION_ORDER,
    STATUS_SECTION_ORDER,
    WORKSTREAM_SECTION_ORDER,
    append_section_bullets,
    append_structured_values,
    ensure_workstream_frontmatter,
    ensure_root_files,
    first_status_next_action,
    load_root_doc,
    load_status_doc,
    load_workstream_doc,
    normalize_slug,
    parse_workstream_entries,
    persist_workstream_doc,
    set_section_text,
    set_task_status,
    sync_status_entries_from_root,
    upsert_status_entry,
    upsert_task,
    write_document,
)
from v2_compat import migration_error, v2_project_exists


LIST_FIELDS = {
    "quick_start",
    "planning_note",
    "assumption",
    "shared_assumption",
    "plan_step",
    "task",
    "task_status",
    "blocker",
    "global_blocker",
    "suggested_skill",
    "useful_reference",
    "progress_note",
    "next_action",
    "done_criterion",
}


def split_pairs(values: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for value in values:
        head, _, tail = value.partition("::")
        pairs.append((head.strip(), tail.strip()))
    return pairs


def apply_text_list(doc, section: str, values: list[str]) -> None:
    if values:
        append_section_bullets(doc, section, values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update structured sections in todos.md or a workstream ledger.")
    parser.add_argument("--repo-root", default=".", help="Repository root that should contain todos.md.")
    parser.add_argument(
        "--payload-file",
        help="Optional JSON file with argument values. Use '-' to read the payload from stdin.",
    )
    parser.add_argument("--workstream", help="Optional workstream slug to update instead of root todos.md.")
    parser.add_argument("--objective", help="Summary or objective for the workstream entry.")
    parser.add_argument("--owner", help="Owner or agent label for the workstream entry.")
    parser.add_argument(
        "--status",
        choices=["planned", "in_progress", "blocked", "stale", "done", "superseded"],
        help="Workstream status used in the root index.",
    )
    parser.add_argument(
        "--execution-state",
        choices=["ready", "claimed", "idle", "closed"],
        help="Pickup register state for todo-status.md.",
    )
    parser.add_argument("--pickup-note", help="Short next-step summary for todo-status.md.")
    parser.add_argument("--summary", help="Replacement text for the Summary section.")
    parser.add_argument("--quick-start", action="append", default=[], help="Bullet to append to Quick Start.")
    parser.add_argument("--planning-note", action="append", default=[], help="Bullet to append to Planning Notes.")
    parser.add_argument("--assumption", action="append", default=[], help="Bullet to append to Assumptions.")
    parser.add_argument("--shared-assumption", action="append", default=[], help="Bullet to append to Shared Assumptions.")
    parser.add_argument("--plan-step", action="append", default=[], help="Bullet to append to Plan.")
    parser.add_argument("--task", action="append", default=[], help="Task to add if it does not already exist.")
    parser.add_argument(
        "--task-status",
        action="append",
        default=[],
        help="Update an existing task using task::status where status is one of space, ~, x, !.",
    )
    parser.add_argument("--blocker", action="append", default=[], help="Bullet to append to Blockers.")
    parser.add_argument("--global-blocker", action="append", default=[], help="Bullet to append to Global Blockers.")
    parser.add_argument("--suggested-skill", action="append", default=[], help="Entry in the form name::why.")
    parser.add_argument("--useful-reference", action="append", default=[], help="Entry in the form path::why.")
    parser.add_argument("--progress-note", action="append", default=[], help="Bullet to append to Progress Notes.")
    parser.add_argument("--next-action", action="append", default=[], help="Bullet to append to Next Actions.")
    parser.add_argument("--done-criterion", action="append", default=[], help="Bullet to append to Done Criteria.")
    parser.add_argument("--review-now", action="store_true", help="Refresh the workstream review timestamp.")
    parser.add_argument("--stale-after-days", type=int, help="Override the stale threshold for this workstream.")
    parser.add_argument("--superseded-by", help="Slug of the workstream that superseded this one.")
    parser.add_argument("--waiting-on", help="Short note about the outstanding dependency for this workstream.")
    parser.add_argument("--stale-reason", help="Reason recorded when a workstream is marked stale.")
    return parser


def load_payload(path_value: str, parser: argparse.ArgumentParser) -> dict[str, Any]:
    try:
        if path_value == "-":
            payload = json.load(sys.stdin)
        else:
            with Path(path_value).open(encoding="utf-8") as handle:
                payload = json.load(handle)
    except FileNotFoundError as exc:
        parser.error(f"payload file not found: {exc.filename}")
    except json.JSONDecodeError as exc:
        parser.error(f"invalid JSON payload: {exc}")

    if not isinstance(payload, dict):
        parser.error("payload JSON must be an object.")
    return payload


def merge_payload(args: argparse.Namespace, defaults: dict[str, Any], payload: dict[str, Any], parser: argparse.ArgumentParser) -> None:
    valid_fields = set(defaults) - {"payload_file"}
    for raw_key, value in payload.items():
        key = raw_key.replace("-", "_")
        if key not in valid_fields:
            parser.error(f"unknown payload field: {raw_key}")

        if key in LIST_FIELDS:
            if isinstance(value, str):
                payload_values = [value]
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                payload_values = value
            else:
                parser.error(f"payload field '{raw_key}' must be a string or list of strings")
            setattr(args, key, payload_values + list(getattr(args, key)))
            continue

        default = defaults[key]
        current = getattr(args, key)
        if current != default:
            continue
        if key == "stale_after_days":
            if value is not None and not isinstance(value, int):
                parser.error(f"payload field '{raw_key}' must be an integer")
            setattr(args, key, value)
            continue
        if key == "review_now":
            if not isinstance(value, bool):
                parser.error(f"payload field '{raw_key}' must be a boolean")
            setattr(args, key, value)
            continue
        if value is not None and not isinstance(value, str):
            parser.error(f"payload field '{raw_key}' must be a string")
        setattr(args, key, value)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    defaults = vars(parser.parse_args([]))
    if args.payload_file:
        payload = load_payload(args.payload_file, parser)
        merge_payload(args, defaults, payload, parser)
    repo_root = Path(args.repo_root).resolve()
    if v2_project_exists(repo_root):
        return migration_error(repo_root, "update_todos.py", "status --json")
    ensure_root_files(repo_root)

    if args.workstream:
        slug = normalize_slug(args.workstream)
        root_doc = load_root_doc(repo_root)
        existing_entries = {entry["slug"]: entry for entry in parse_workstream_entries(root_doc.sections.get("Workstreams", []))}
        current_entry = existing_entries.get(slug, {})
        objective = args.objective or current_entry.get("objective", slug.replace("-", " "))
        effective_status = args.status or current_entry.get("status", "planned")
        effective_owner = args.owner or current_entry.get("owner", "unassigned")
        doc = load_workstream_doc(repo_root, slug, objective)
        if objective and doc.sections.get("Summary") in ([], ["_None recorded yet._"]):
            doc.sections["Summary"] = [objective]
        if args.summary:
            set_section_text(doc, "Summary", args.summary)
        apply_text_list(doc, "Quick Start", args.quick_start)
        apply_text_list(doc, "Planning Notes", args.planning_note)
        apply_text_list(doc, "Assumptions", args.assumption)
        apply_text_list(doc, "Plan", args.plan_step)
        for task in args.task:
            upsert_task(doc, task, status=" ")
        for value in args.task_status:
            task_text, _, task_status = value.partition("::")
            if task_text.strip():
                set_task_status(doc, task_text.strip(), task_status.strip() or " ")
        apply_text_list(doc, "Blockers", args.blocker)
        append_structured_values(doc, "Suggested Skills", split_pairs(args.suggested_skill))
        append_structured_values(doc, "Useful Reference Files", split_pairs(args.useful_reference))
        apply_text_list(doc, "Progress Notes", args.progress_note)
        apply_text_list(doc, "Next Actions", args.next_action)
        apply_text_list(doc, "Done Criteria", args.done_criterion)
        ensure_workstream_frontmatter(
            doc,
            slug=slug,
            objective=objective,
            status=effective_status,
            owner=effective_owner,
            execution=args.execution_state,
            touch_heartbeat=True,
            touch_review=args.review_now,
            stale_after_days=args.stale_after_days,
            superseded_by=args.superseded_by,
            waiting_on=args.waiting_on,
            stale_reason=args.stale_reason,
        )
        persist_workstream_doc(repo_root, slug, doc, objective=objective)

        root_doc = load_root_doc(repo_root)
        if args.shared_assumption:
            apply_text_list(root_doc, "Shared Assumptions", args.shared_assumption)
        if args.global_blocker:
            apply_text_list(root_doc, "Global Blockers", args.global_blocker)
        if args.progress_note:
            apply_text_list(root_doc, "Progress Notes", args.progress_note)
        if args.next_action:
            apply_text_list(root_doc, "Next Actions", args.next_action)
        if args.done_criterion:
            apply_text_list(root_doc, "Done Criteria", args.done_criterion)
        if args.suggested_skill:
            append_structured_values(root_doc, "Suggested Skills", split_pairs(args.suggested_skill))
        if args.useful_reference:
            append_structured_values(root_doc, "Useful Reference Files", split_pairs(args.useful_reference))
        write_document(repo_root / "todos.md", root_doc, ROOT_SECTION_ORDER)

        status_doc = load_status_doc(repo_root)
        sync_status_entries_from_root(root_doc, status_doc)
        upsert_status_entry(
            status_doc,
            slug=slug,
            objective=objective,
            status=effective_status,
            owner=effective_owner,
            execution=args.execution_state,
            next_action=first_status_next_action(args.pickup_note, doc),
        )
        write_document(repo_root / "todo-status.md", status_doc, STATUS_SECTION_ORDER)
        print(repo_root / "todos" / f"{slug}.md")
        return 0

    doc = load_root_doc(repo_root)
    if args.summary:
        set_section_text(doc, "Summary", args.summary)
    apply_text_list(doc, "Shared Assumptions", args.shared_assumption or args.assumption)
    apply_text_list(doc, "Global Blockers", args.global_blocker or args.blocker)
    append_structured_values(doc, "Suggested Skills", split_pairs(args.suggested_skill))
    append_structured_values(doc, "Useful Reference Files", split_pairs(args.useful_reference))
    apply_text_list(doc, "Progress Notes", args.progress_note)
    apply_text_list(doc, "Next Actions", args.next_action)
    apply_text_list(doc, "Done Criteria", args.done_criterion)
    write_document(repo_root / "todos.md", doc, ROOT_SECTION_ORDER)
    print(repo_root / "todos.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
