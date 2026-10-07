#!/usr/bin/env python3
"""Shared helpers for the todo-orchestrator scripts."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT_TITLE = "Active Objectives"
WORKSTREAM_TITLE = "Current Objective"
STATUS_TITLE = "Todo Status"

ROOT_SECTION_ORDER = [
    "Summary",
    "Shared Assumptions",
    "Suggested Skills",
    "Useful Reference Files",
    "Workstreams",
    "Global Blockers",
    "Progress Notes",
    "Next Actions",
    "Done Criteria",
]

WORKSTREAM_SECTION_ORDER = [
    "Summary",
    "Quick Start",
    "Planning Notes",
    "Assumptions",
    "Suggested Skills",
    "Useful Reference Files",
    "Plan",
    "Tasks",
    "Blockers",
    "Progress Notes",
    "Next Actions",
    "Done Criteria",
]

STATUS_SECTION_ORDER = [
    "Summary",
    "Workstreams",
    "Staleness Review",
    "Cleanup Status",
]

PLACEHOLDER = "_None recorded yet._"
WORKSTREAM_PLACEHOLDER = "_No active workstreams yet._"
STATUS_PLACEHOLDER = "_No tracked workstreams yet._"
STALENESS_PLACEHOLDER = "_No staleness review recorded yet._"
TASK_PATTERN = re.compile(r"^- \[([ x~!])\] (.+)$")
WORKSTREAM_PATTERN = re.compile(
    r"^- `(?P<slug>[^`]+)` \| status: (?P<status>[^|]+) \| owner: (?P<owner>[^|]+) "
    r"\| file: `(?P<file>[^`]+)` \| objective: (?P<objective>.+)$"
)
STATUS_PATTERN = re.compile(
    r"^- `(?P<slug>[^`]+)` \| status: (?P<status>[^|]+) \| execution: (?P<execution>[^|]+) "
    r"\| owner: (?P<owner>[^|]+) \| file: `(?P<file>[^`]+)` \| next: (?P<next>.+)$"
)
FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
AGENTS_START = "<!-- todo-orchestrator:start -->"
AGENTS_END = "<!-- todo-orchestrator:end -->"

DONE_STATUSES = {"done", "complete", "archived", "superseded"}
TERMINAL_STATUSES = DONE_STATUSES
NON_PICKUP_STATUSES = {"blocked", "stale"} | TERMINAL_STATUSES
PICKUP_READY_STATES = {"ready", "idle"}
PARTIAL_CLEANUP_ALLOWED_STATUSES = DONE_STATUSES | {"stale"}
DEFAULT_STALE_AFTER_DAYS = {
    "planned": 3,
    "in_progress": 3,
    "blocked": 7,
    "stale": 3,
}
DEFAULT_QUICK_START_LINES = [
    "- Why this stream exists: _Summarize the domain boundary and why it was split out._",
    "- In scope: _List the work this stream owns._",
    "- Out of scope / dependencies: _List handoffs, upstream dependencies, or adjacent streams._",
    "- Required skills: _List the exact repo-local skills to read before starting._",
    "- Required references: _List the exact repo-local references to read before starting._",
]
WORKSTREAM_FRONTMATTER_ORDER = [
    "slug",
    "status",
    "execution",
    "owner",
    "created_at",
    "last_heartbeat_at",
    "last_reviewed_at",
    "stale_after_days",
    "objective",
    "superseded_by",
    "waiting_on",
    "stale_reason",
]


@dataclass
class MarkdownDoc:
    title: str
    preamble: list[str]
    sections: "OrderedDict[str, list[str]]"
    order: list[str]
    frontmatter: "OrderedDict[str, str]" = field(default_factory=OrderedDict)


def normalize_slug(value: str) -> str:
    slug = value.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-") or "workstream"


def humanize_slug(value: str) -> str:
    text = value.replace("-", " ").strip()
    if not text:
        return "Workstream"
    return text[0].upper() + text[1:]


def normalize_text_lines(lines: Iterable[str]) -> list[str]:
    normalized = list(lines)
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return normalized


def split_frontmatter(text: str) -> tuple["OrderedDict[str, str]", str]:
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return OrderedDict(), text
    body = text[match.end() :]
    return parse_frontmatter_block(match.group(1)), body


def parse_markdown_document(text: str) -> MarkdownDoc:
    frontmatter, body = split_frontmatter(text)
    lines = body.splitlines()
    title = ""
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line:
            if line.startswith("# "):
                title = line[2:].strip()
                index += 1
            break
        index += 1

    preamble: list[str] = []
    sections: "OrderedDict[str, list[str]]" = OrderedDict()
    current_name: str | None = None
    current_lines: list[str] = []
    saw_section = False

    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            if current_name is not None:
                sections[current_name] = normalize_text_lines(current_lines)
            elif preamble:
                preamble = normalize_text_lines(preamble)
            current_name = line[3:].strip()
            current_lines = []
            saw_section = True
        else:
            if saw_section and current_name is not None:
                current_lines.append(line)
            else:
                preamble.append(line)
        index += 1

    if current_name is not None:
        sections[current_name] = normalize_text_lines(current_lines)
    else:
        preamble = normalize_text_lines(preamble)

    return MarkdownDoc(
        title=title,
        preamble=preamble,
        sections=sections,
        order=list(sections.keys()),
        frontmatter=frontmatter,
    )


def render_yaml_scalar(value: str) -> str:
    text = str(value)
    if re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", text):
        return text
    return json.dumps(text)


def render_frontmatter_lines(frontmatter: "OrderedDict[str, str]") -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    keys = [key for key in WORKSTREAM_FRONTMATTER_ORDER if key in frontmatter]
    keys.extend(key for key in frontmatter if key not in seen and key not in keys)
    for key in keys:
        seen.add(key)
        value = str(frontmatter[key]).strip()
        if not value:
            continue
        if "\n" in value:
            lines.append(f"{key}: |-")
            for raw_line in value.splitlines():
                lines.append(f"  {raw_line}")
            continue
        lines.append(f"{key}: {render_yaml_scalar(value)}")
    return lines


def render_markdown_document(doc: MarkdownDoc, preferred_order: list[str]) -> str:
    lines: list[str] = []
    if doc.frontmatter:
        lines.append("---")
        lines.extend(render_frontmatter_lines(doc.frontmatter))
        lines.append("---")
        lines.append("")

    lines.extend([f"# {doc.title}", ""])
    if doc.preamble:
        lines.extend(normalize_text_lines(doc.preamble))
        lines.append("")

    seen: set[str] = set()
    section_names = [name for name in preferred_order if name in doc.sections]
    section_names.extend(name for name in doc.order if name in doc.sections and name not in seen and name not in section_names)

    for name in section_names:
        seen.add(name)
        lines.append(f"## {name}")
        section_lines = normalize_text_lines(doc.sections.get(name, []))
        if section_lines:
            lines.extend(section_lines)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def default_root_doc() -> MarkdownDoc:
    sections = OrderedDict(
        (
            ("Summary", ["Use this file as the canonical index for substantial multi-step work."]),
            ("Shared Assumptions", [PLACEHOLDER]),
            ("Suggested Skills", [PLACEHOLDER]),
            ("Useful Reference Files", [PLACEHOLDER]),
            ("Workstreams", [WORKSTREAM_PLACEHOLDER]),
            ("Global Blockers", [PLACEHOLDER]),
            ("Progress Notes", [PLACEHOLDER]),
            ("Next Actions", ["- Create or resume a workstream ledger under `todos/` for the next substantial task."]),
            ("Done Criteria", ["- Every active workstream in `todos/` is reflected here with a current status."]),
        )
    )
    return MarkdownDoc(title=ROOT_TITLE, preamble=[], sections=sections, order=list(sections.keys()))


def default_workstream_doc(objective: str) -> MarkdownDoc:
    sections = OrderedDict(
        (
            ("Summary", [objective or PLACEHOLDER]),
            ("Quick Start", DEFAULT_QUICK_START_LINES.copy()),
            ("Planning Notes", [PLACEHOLDER]),
            ("Assumptions", [PLACEHOLDER]),
            ("Suggested Skills", [PLACEHOLDER]),
            ("Useful Reference Files", [PLACEHOLDER]),
            ("Plan", [PLACEHOLDER]),
            ("Tasks", [PLACEHOLDER]),
            ("Blockers", [PLACEHOLDER]),
            ("Progress Notes", [PLACEHOLDER]),
            ("Next Actions", [PLACEHOLDER]),
            ("Done Criteria", [PLACEHOLDER]),
        )
    )
    return MarkdownDoc(title=WORKSTREAM_TITLE, preamble=[], sections=sections, order=list(sections.keys()))


def default_status_doc() -> MarkdownDoc:
    sections = OrderedDict(
        (
            (
                "Summary",
                [
                    "Use this file as the quick pickup register for `todos.md` workstreams.",
                    "- `ready`: planned work that can be started now.",
                    "- `claimed`: currently being written; choose another stream.",
                    "- `idle`: unfinished but resumable; safe to pick up.",
                    "- `closed`: completed or removed from pickup rotation.",
                ],
            ),
            ("Workstreams", [STATUS_PLACEHOLDER]),
            ("Staleness Review", [STALENESS_PLACEHOLDER]),
            (
                "Cleanup Status",
                [
                    "- Cleanup mode is explicit only.",
                    "- Safe to call `todo-cleanup`: no, there are unfinished workstreams.",
                ],
            ),
        )
    )
    return MarkdownDoc(title=STATUS_TITLE, preamble=[], sections=sections, order=list(sections.keys()))


def ensure_sections(doc: MarkdownDoc, ordered_sections: list[str], placeholders: dict[str, list[str]] | None = None) -> MarkdownDoc:
    placeholders = placeholders or {}
    for name in ordered_sections:
        if name not in doc.sections:
            doc.sections[name] = placeholders.get(name, [PLACEHOLDER])
            doc.order.append(name)
    return doc


def write_document(path: Path, doc: MarkdownDoc, preferred_order: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_document(doc, preferred_order), encoding="utf-8")


def load_root_doc(repo_root: Path) -> MarkdownDoc:
    path = repo_root / "todos.md"
    if path.exists():
        doc = parse_markdown_document(path.read_text(encoding="utf-8"))
    else:
        doc = default_root_doc()
    return ensure_sections(doc, ROOT_SECTION_ORDER, {"Workstreams": [WORKSTREAM_PLACEHOLDER]})


def load_workstream_doc(repo_root: Path, slug: str, objective: str = "") -> MarkdownDoc:
    path = repo_root / "todos" / f"{slug}.md"
    if path.exists():
        doc = parse_markdown_document(path.read_text(encoding="utf-8"))
    else:
        doc = default_workstream_doc(objective or humanize_slug(slug))
    return ensure_sections(doc, WORKSTREAM_SECTION_ORDER)


def load_status_doc(repo_root: Path) -> MarkdownDoc:
    path = repo_root / "todo-status.md"
    if path.exists():
        doc = parse_markdown_document(path.read_text(encoding="utf-8"))
    else:
        doc = default_status_doc()
    return ensure_sections(
        doc,
        STATUS_SECTION_ORDER,
        {
            "Workstreams": [STATUS_PLACEHOLDER],
            "Staleness Review": [STALENESS_PLACEHOLDER],
        },
    )


def clear_placeholder(lines: list[str], placeholder: str = PLACEHOLDER) -> list[str]:
    normalized = normalize_text_lines(lines)
    if normalized in (
        [placeholder],
        [WORKSTREAM_PLACEHOLDER],
        [STATUS_PLACEHOLDER],
        [STALENESS_PLACEHOLDER],
    ):
        return []
    return normalized


def append_unique_bullets(lines: list[str], values: Iterable[str]) -> list[str]:
    current = clear_placeholder(lines)
    for value in values:
        item = value.strip()
        if not item:
            continue
        bullet = item if item.startswith("- ") else f"- {item}"
        if bullet not in current:
            current.append(bullet)
    return current or [PLACEHOLDER]


def set_section_text(doc: MarkdownDoc, section: str, text: str) -> None:
    content = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    doc.sections[section] = content or [PLACEHOLDER]


def append_section_bullets(doc: MarkdownDoc, section: str, values: Iterable[str]) -> None:
    doc.sections[section] = append_unique_bullets(doc.sections.get(section, [PLACEHOLDER]), values)


def append_structured_values(doc: MarkdownDoc, section: str, pairs: Iterable[tuple[str, str]]) -> None:
    current = clear_placeholder(doc.sections.get(section, [PLACEHOLDER]))
    for first, second in pairs:
        head = first.strip()
        tail = second.strip()
        if not head:
            continue
        bullet = f"- `{head}` - {tail}" if tail else f"- `{head}`"
        if bullet not in current:
            current.append(bullet)
    doc.sections[section] = current or [PLACEHOLDER]


def parse_task_items(lines: Iterable[str]) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    for line in lines:
        match = TASK_PATTERN.match(line.strip())
        if not match:
            continue
        tasks.append({"status": match.group(1), "text": match.group(2).strip()})
    return tasks


def render_task_items(tasks: Iterable[dict[str, str]]) -> list[str]:
    rendered = [f"- [{task['status']}] {task['text']}" for task in tasks if task.get("text")]
    return rendered or [PLACEHOLDER]


def upsert_task(doc: MarkdownDoc, text: str, status: str | None = None) -> None:
    task_text = text.strip()
    if not task_text:
        return
    normalized_status = (status or " ").strip() or " "
    tasks = parse_task_items(clear_placeholder(doc.sections.get("Tasks", [PLACEHOLDER])))
    for task in tasks:
        if task["text"] == task_text:
            if status is not None:
                task["status"] = normalized_status
            doc.sections["Tasks"] = render_task_items(tasks)
            return
    tasks.append({"status": normalized_status, "text": task_text})
    doc.sections["Tasks"] = render_task_items(tasks)


def set_task_status(doc: MarkdownDoc, text: str, status: str) -> bool:
    task_text = text.strip()
    tasks = parse_task_items(clear_placeholder(doc.sections.get("Tasks", [PLACEHOLDER])))
    updated = False
    for task in tasks:
        if task["text"] == task_text:
            task["status"] = status.strip() or " "
            updated = True
            break
    doc.sections["Tasks"] = render_task_items(tasks)
    return updated


def parse_workstream_entries(lines: Iterable[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in lines:
        match = WORKSTREAM_PATTERN.match(line.strip())
        if not match:
            continue
        entries.append(match.groupdict())
    return entries


def parse_status_entries(lines: Iterable[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in lines:
        match = STATUS_PATTERN.match(line.strip())
        if not match:
            continue
        entries.append(match.groupdict())
    return entries


def render_workstream_entries(entries: Iterable[dict[str, str]]) -> list[str]:
    rendered = [
        (
            f"- `{entry['slug']}` | status: {entry['status']} | owner: {entry['owner']} "
            f"| file: `{entry['file']}` | objective: {entry['objective']}"
        )
        for entry in entries
    ]
    return rendered or [WORKSTREAM_PLACEHOLDER]


def render_status_entries(entries: Iterable[dict[str, str]]) -> list[str]:
    rendered = [
        (
            f"- `{entry['slug']}` | status: {entry['status']} | execution: {entry['execution']} "
            f"| owner: {entry['owner']} | file: `{entry['file']}` | next: {entry['next']}"
        )
        for entry in entries
    ]
    return rendered or [STATUS_PLACEHOLDER]


def upsert_workstream_entry(
    doc: MarkdownDoc,
    slug: str,
    objective: str,
    status: str = "planned",
    owner: str = "unassigned",
) -> None:
    entries = parse_workstream_entries(clear_placeholder(doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    target_file = f"todos/{slug}.md"
    for entry in entries:
        if entry["slug"] == slug:
            entry["status"] = status
            entry["owner"] = owner
            entry["objective"] = objective
            entry["file"] = target_file
            doc.sections["Workstreams"] = render_workstream_entries(entries)
            return
    entries.append(
        {
            "slug": slug,
            "status": status,
            "owner": owner,
            "file": target_file,
            "objective": objective,
        }
    )
    doc.sections["Workstreams"] = render_workstream_entries(entries)


def remove_workstream_entry(doc: MarkdownDoc, slug: str) -> None:
    entries = parse_workstream_entries(clear_placeholder(doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    filtered = [entry for entry in entries if entry["slug"] != slug]
    doc.sections["Workstreams"] = render_workstream_entries(filtered)


def is_done_status(status: str) -> bool:
    return status.strip() in DONE_STATUSES


def derive_execution_state(status: str, current: str | None = None) -> str:
    normalized = status.strip()
    if normalized in TERMINAL_STATUSES or normalized == "stale":
        return "closed"
    existing = (current or "").strip()
    if existing:
        return existing
    if normalized == "planned":
        return "ready"
    if normalized == "in_progress":
        return "claimed"
    if normalized == "blocked":
        return "idle"
    return "idle"


def first_status_next_action(next_action: str | None = None, doc: MarkdownDoc | None = None) -> str:
    if next_action and next_action.strip():
        return next_action.strip()
    if doc is not None:
        lines = clear_placeholder(doc.sections.get("Next Actions", [PLACEHOLDER]))
        if lines:
            return re.sub(r"^- ", "", lines[0]).strip()
    return "Review the workstream ledger and pick the next concrete step."


def upsert_status_entry(
    doc: MarkdownDoc,
    slug: str,
    objective: str,
    status: str,
    owner: str,
    execution: str | None = None,
    next_action: str | None = None,
) -> None:
    entries = parse_status_entries(clear_placeholder(doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    target_file = f"todos/{slug}.md"
    normalized_execution = derive_execution_state(status, execution)
    summary = next_action.strip() if next_action and next_action.strip() else objective.strip() or humanize_slug(slug)
    for entry in entries:
        if entry["slug"] == slug:
            entry["status"] = status
            entry["execution"] = normalized_execution
            entry["owner"] = owner
            entry["file"] = target_file
            entry["next"] = summary
            doc.sections["Workstreams"] = render_status_entries(entries)
            refresh_cleanup_status(doc)
            return
    entries.append(
        {
            "slug": slug,
            "status": status,
            "execution": normalized_execution,
            "owner": owner,
            "file": target_file,
            "next": summary,
        }
    )
    doc.sections["Workstreams"] = render_status_entries(entries)
    refresh_cleanup_status(doc)


def sync_status_entries_from_root(root_doc: MarkdownDoc, status_doc: MarkdownDoc) -> None:
    root_entries = parse_workstream_entries(clear_placeholder(root_doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    current_entries = {
        entry["slug"]: entry
        for entry in parse_status_entries(clear_placeholder(status_doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    }
    synced: list[dict[str, str]] = []
    for entry in root_entries:
        current = current_entries.get(entry["slug"], {})
        synced.append(
            {
                "slug": entry["slug"],
                "status": entry["status"],
                "execution": derive_execution_state(entry["status"], current.get("execution")),
                "owner": entry["owner"],
                "file": entry["file"],
                "next": current.get("next", entry["objective"]),
            }
        )
    status_doc.sections["Workstreams"] = render_status_entries(synced)
    refresh_cleanup_status(status_doc)


def remove_status_entry(doc: MarkdownDoc, slug: str) -> None:
    entries = parse_status_entries(clear_placeholder(doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    filtered = [entry for entry in entries if entry["slug"] != slug]
    doc.sections["Workstreams"] = render_status_entries(filtered)
    refresh_cleanup_status(doc)


def cleanup_status_lines(entries: Iterable[dict[str, str]]) -> list[str]:
    normalized_entries = list(entries)
    active = [
        entry["slug"]
        for entry in normalized_entries
        if entry["status"].strip() not in TERMINAL_STATUSES and entry["status"].strip() != "stale"
    ]
    stale = [entry["slug"] for entry in normalized_entries if entry["status"].strip() == "stale"]
    done = [entry["slug"] for entry in normalized_entries if entry["status"].strip() in TERMINAL_STATUSES]
    cleanup_candidates = done + stale
    if active or stale:
        lines = ["- Cleanup mode is explicit only."]
        if active:
            lines.append(f"- Safe to call `todo-cleanup`: no, active workstreams: {', '.join(active)}.")
        if stale:
            lines.append(f"- Cleanup still blocked by stale workstreams pending review: {', '.join(stale)}.")
        if cleanup_candidates:
            lines.append(
                "- Partial cleanup is available via `todo-cleanup --partial`; include `stale` in `--scope` only when explicitly intended."
            )
        return lines
    if normalized_entries:
        return [
            "- Cleanup mode is explicit only.",
            "- Safe to call `todo-cleanup`: yes, every tracked workstream is done or superseded.",
        ]
    return [
        "- Cleanup mode is explicit only.",
        "- Safe to call `todo-cleanup`: yes, there are no tracked workstreams left.",
    ]


def refresh_cleanup_status(doc: MarkdownDoc) -> None:
    entries = parse_status_entries(clear_placeholder(doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    doc.sections["Cleanup Status"] = cleanup_status_lines(entries)


def rebuild_root_after_cleanup(
    root_doc: MarkdownDoc,
    *,
    removed_slugs: list[str],
    cleanup_label: str,
) -> None:
    default_root = default_root_doc()
    root_doc.sections["Summary"] = default_root.sections["Summary"]
    root_doc.sections["Shared Assumptions"] = [PLACEHOLDER]
    root_doc.sections["Suggested Skills"] = [PLACEHOLDER]
    root_doc.sections["Useful Reference Files"] = [PLACEHOLDER]
    root_doc.sections["Global Blockers"] = [PLACEHOLDER]
    if removed_slugs:
        slugs = ", ".join(removed_slugs)
        root_doc.sections["Progress Notes"] = [f"- Ran `{cleanup_label}` and cleared workstreams: {slugs}."]
    else:
        root_doc.sections["Progress Notes"] = [PLACEHOLDER]
    root_doc.sections["Next Actions"] = default_root.sections["Next Actions"]
    root_doc.sections["Done Criteria"] = default_root.sections["Done Criteria"]


def rebuild_status_after_cleanup(repo_root: Path, status_doc: MarkdownDoc) -> None:
    status_doc.sections["Summary"] = default_status_doc().sections["Summary"]
    reviews = review_workstream_staleness(repo_root)
    status_doc.sections["Staleness Review"] = build_staleness_review_lines(reviews)
    refresh_cleanup_status(status_doc)


def parse_cleanup_scope(scope_text: str | None, *, partial: bool) -> set[str]:
    if not partial:
        return set(DONE_STATUSES)
    default_scope = "done,superseded"
    raw = (scope_text or default_scope).strip() or default_scope
    scope: set[str] = set()
    for piece in raw.split(","):
        token = piece.strip().lower()
        if not token:
            continue
        if token == "done":
            scope.update({"done", "complete", "archived"})
            continue
        if token in PARTIAL_CLEANUP_ALLOWED_STATUSES:
            scope.add(token)
            continue
        raise ValueError(
            "unsupported cleanup scope token "
            f"{token!r}; allowed values are done, complete, archived, superseded, stale"
        )
    if not scope:
        raise ValueError("cleanup scope must include at least one cleanup-eligible status")
    return scope


def pickup_ready_entries(entries: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    ready: list[dict[str, str]] = []
    for entry in entries:
        status = entry["status"].strip()
        if status in NON_PICKUP_STATUSES:
            continue
        if entry.get("execution", "").strip() not in PICKUP_READY_STATES:
            continue
        ready.append(entry)
    return ready


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_activity_timestamp(metadata: dict[str, str]) -> datetime | None:
    timestamps = [
        parse_iso_timestamp(metadata.get("last_heartbeat_at")),
        parse_iso_timestamp(metadata.get("last_reviewed_at")),
    ]
    candidates = [item for item in timestamps if item is not None]
    if not candidates:
        return None
    return max(candidates)


def stale_after_days_for(status: str, metadata: dict[str, str]) -> int:
    raw = metadata.get("stale_after_days", "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return DEFAULT_STALE_AFTER_DAYS.get(status.strip(), DEFAULT_STALE_AFTER_DAYS["in_progress"])


def workstream_objective(doc: MarkdownDoc, fallback: str = "") -> str:
    objective = doc.frontmatter.get("objective", "").strip()
    if objective:
        return objective
    summary = clear_placeholder(doc.sections.get("Summary", [PLACEHOLDER]))
    if summary:
        return summary[0].strip()
    return fallback.strip()


def set_metadata_value(metadata: "OrderedDict[str, str]", key: str, value: str | int | None) -> None:
    if value is None:
        return
    text = str(value).strip()
    if text:
        metadata[key] = text
        return
    metadata.pop(key, None)


def ensure_workstream_frontmatter(
    doc: MarkdownDoc,
    slug: str,
    objective: str,
    status: str,
    owner: str,
    execution: str | None = None,
    *,
    touch_heartbeat: bool = False,
    touch_review: bool = False,
    stale_after_days: int | str | None = None,
    superseded_by: str | None = None,
    waiting_on: str | None = None,
    stale_reason: str | None = None,
    now: str | None = None,
) -> "OrderedDict[str, str]":
    metadata: "OrderedDict[str, str]" = OrderedDict(doc.frontmatter)
    timestamp = now or utc_now()
    normalized_status = status.strip() or metadata.get("status", "planned").strip() or "planned"
    metadata["slug"] = slug
    metadata["status"] = normalized_status
    metadata["execution"] = derive_execution_state(normalized_status, execution or metadata.get("execution"))
    metadata["owner"] = owner.strip() or metadata.get("owner", "unassigned")
    metadata["objective"] = objective.strip() or workstream_objective(doc, humanize_slug(slug))
    metadata["created_at"] = metadata.get("created_at", timestamp)
    if touch_heartbeat or not metadata.get("last_heartbeat_at"):
        metadata["last_heartbeat_at"] = timestamp
    if touch_review or not metadata.get("last_reviewed_at"):
        metadata["last_reviewed_at"] = timestamp
    metadata["stale_after_days"] = str(stale_after_days_for(normalized_status, {"stale_after_days": str(stale_after_days) if stale_after_days is not None else metadata.get("stale_after_days", "")}))
    if stale_after_days is not None:
        metadata["stale_after_days"] = str(int(stale_after_days))
    set_metadata_value(metadata, "waiting_on", waiting_on)
    if normalized_status == "superseded":
        set_metadata_value(metadata, "superseded_by", superseded_by)
    elif superseded_by is not None:
        set_metadata_value(metadata, "superseded_by", superseded_by)
    else:
        metadata.pop("superseded_by", None)
    if normalized_status == "stale":
        set_metadata_value(metadata, "stale_reason", stale_reason or "Marked stale pending review.")
    elif stale_reason is not None:
        set_metadata_value(metadata, "stale_reason", stale_reason)
    else:
        metadata.pop("stale_reason", None)
    doc.frontmatter = metadata
    return metadata


def classify_workstream_staleness(metadata: dict[str, str], *, as_of: datetime | None = None) -> dict[str, object]:
    status = metadata.get("status", "planned").strip() or "planned"
    execution = metadata.get("execution", "").strip()
    threshold_days = stale_after_days_for(status, metadata)
    latest = latest_activity_timestamp(metadata)
    now = as_of or datetime.now(timezone.utc)
    age_days = None if latest is None else (now - latest).total_seconds() / 86400.0
    result: dict[str, object] = {
        "slug": metadata.get("slug", ""),
        "status": status,
        "execution": execution,
        "owner": metadata.get("owner", "unassigned"),
        "objective": metadata.get("objective", ""),
        "threshold_days": threshold_days,
        "age_days": age_days,
        "last_touch": latest.isoformat().replace("+00:00", "Z") if latest is not None else "",
        "classification": "fresh",
        "reason": f"Fresh within the {threshold_days}-day threshold.",
        "claimed_inconsistency": False,
        "eligible_for_stale_apply": False,
    }
    if status == "superseded":
        result["classification"] = "superseded"
        result["reason"] = f"Superseded by {metadata.get('superseded_by', 'another stream')}."
        return result
    if status in TERMINAL_STATUSES:
        result["classification"] = "done"
        result["reason"] = "Terminal workstream."
        return result
    if status == "stale":
        result["classification"] = "stale"
        result["reason"] = metadata.get("stale_reason", "Explicitly marked stale pending review.")
        result["claimed_inconsistency"] = execution == "claimed"
        return result
    if latest is None:
        result["classification"] = "stale_candidate"
        result["reason"] = "Missing freshness metadata; review before pickup."
        return result
    if age_days is not None and age_days > threshold_days:
        result["classification"] = "stale_candidate"
        result["reason"] = f"No heartbeat or review within {threshold_days} days."
        result["eligible_for_stale_apply"] = True
        return result
    if age_days is not None and age_days >= max(1.0, threshold_days / 2.0):
        result["classification"] = "aging"
        result["reason"] = f"Older than half of the {threshold_days}-day threshold."
        return result
    return result


def build_staleness_review_lines(results: Iterable[dict[str, object]]) -> list[str]:
    collected = list(results)
    if not collected:
        return [STALENESS_PLACEHOLDER]

    def count(name: str) -> int:
        return sum(1 for item in collected if item["classification"] == name)

    lines = [
        f"- Fresh: {count('fresh')}",
        f"- Aging: {count('aging')}",
        f"- Stale candidates: {count('stale_candidate')}",
        f"- Stale: {count('stale')}",
        f"- Superseded: {count('superseded')}",
    ]
    for item in collected:
        classification = str(item["classification"])
        if classification == "fresh":
            continue
        age_days = item.get("age_days")
        age_text = "unknown" if age_days is None else f"{float(age_days):.1f}d"
        threshold = item.get("threshold_days", "?")
        reason = str(item.get("reason", "")).strip()
        lines.append(
            f"- `{item['slug']}` | {classification} | age: {age_text} | threshold: {threshold}d | reason: {reason}"
        )
        if item.get("claimed_inconsistency"):
            lines.append(f"- `{item['slug']}` | inconsistency | stale workstream is still marked `claimed`.")
    return lines


def write_staleness_review(repo_root: Path, results: Iterable[dict[str, object]]) -> None:
    status_doc = load_status_doc(repo_root)
    status_doc.sections["Staleness Review"] = build_staleness_review_lines(results)
    refresh_cleanup_status(status_doc)
    write_document(repo_root / "todo-status.md", status_doc, STATUS_SECTION_ORDER)


def gather_workstream_records(repo_root: Path) -> list[dict[str, object]]:
    root_doc = load_root_doc(repo_root)
    status_doc = load_status_doc(repo_root)
    root_entries = parse_workstream_entries(clear_placeholder(root_doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    status_entries = {
        entry["slug"]: entry
        for entry in parse_status_entries(clear_placeholder(status_doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    }
    records: list[dict[str, object]] = []
    for entry in root_entries:
        slug = entry["slug"]
        path = repo_root / entry["file"]
        doc = load_workstream_doc(repo_root, slug, entry["objective"])
        metadata = OrderedDict(doc.frontmatter)
        metadata.setdefault("slug", slug)
        metadata.setdefault("status", entry["status"])
        metadata.setdefault("owner", entry["owner"])
        metadata.setdefault(
            "execution",
            derive_execution_state(metadata["status"], status_entries.get(slug, {}).get("execution")),
        )
        metadata.setdefault("objective", entry["objective"])
        if not metadata.get("stale_after_days"):
            metadata["stale_after_days"] = str(stale_after_days_for(metadata["status"], metadata))
        records.append(
            {
                "slug": slug,
                "path": path,
                "doc": doc,
                "metadata": metadata,
                "root_entry": entry,
                "status_entry": status_entries.get(slug, {}),
            }
        )
    return records


def review_workstream_staleness(repo_root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for record in gather_workstream_records(repo_root):
        review = classify_workstream_staleness(record["metadata"])
        review["path"] = str(record["path"])
        review["doc"] = record["doc"]
        review["metadata"] = record["metadata"]
        results.append(review)
    return results


def persist_workstream_doc(repo_root: Path, slug: str, doc: MarkdownDoc, objective: str = "") -> Path:
    normalized_slug = normalize_slug(slug)
    objective_text = workstream_objective(doc, objective or humanize_slug(normalized_slug))
    status = doc.frontmatter.get("status", "planned").strip() or "planned"
    owner = doc.frontmatter.get("owner", "unassigned").strip() or "unassigned"
    execution = derive_execution_state(status, doc.frontmatter.get("execution"))
    doc.frontmatter["slug"] = normalized_slug
    doc.frontmatter["status"] = status
    doc.frontmatter["owner"] = owner
    doc.frontmatter["execution"] = execution
    doc.frontmatter["objective"] = objective_text

    workstream_path = repo_root / "todos" / f"{normalized_slug}.md"
    write_document(workstream_path, doc, WORKSTREAM_SECTION_ORDER)

    root_doc = load_root_doc(repo_root)
    upsert_workstream_entry(root_doc, normalized_slug, objective_text, status=status, owner=owner)
    write_document(repo_root / "todos.md", root_doc, ROOT_SECTION_ORDER)

    status_doc = load_status_doc(repo_root)
    sync_status_entries_from_root(root_doc, status_doc)
    upsert_status_entry(
        status_doc,
        slug=normalized_slug,
        objective=objective_text,
        status=status,
        owner=owner,
        execution=execution,
        next_action=first_status_next_action(doc=doc),
    )
    write_document(repo_root / "todo-status.md", status_doc, STATUS_SECTION_ORDER)
    return workstream_path


def ensure_root_files(repo_root: Path) -> None:
    root_doc = load_root_doc(repo_root)
    write_document(repo_root / "todos.md", root_doc, ROOT_SECTION_ORDER)
    (repo_root / "todos").mkdir(parents=True, exist_ok=True)
    status_doc = load_status_doc(repo_root)
    sync_status_entries_from_root(root_doc, status_doc)
    write_document(repo_root / "todo-status.md", status_doc, STATUS_SECTION_ORDER)


def ensure_workstream_file(
    repo_root: Path,
    slug: str,
    objective: str,
    status: str,
    owner: str,
    execution: str | None = None,
    next_action: str | None = None,
    *,
    stale_after_days: int | str | None = None,
    superseded_by: str | None = None,
    waiting_on: str | None = None,
    stale_reason: str | None = None,
    touch_heartbeat: bool = True,
    touch_review: bool = False,
    now: str | None = None,
) -> Path:
    ensure_root_files(repo_root)
    workstream_doc = load_workstream_doc(repo_root, slug, objective)
    if objective and workstream_doc.sections.get("Summary") in ([PLACEHOLDER], []):
        workstream_doc.sections["Summary"] = [objective]
    ensure_workstream_frontmatter(
        workstream_doc,
        slug=slug,
        objective=objective,
        status=status,
        owner=owner,
        execution=execution,
        touch_heartbeat=touch_heartbeat,
        touch_review=touch_review,
        stale_after_days=stale_after_days,
        superseded_by=superseded_by,
        waiting_on=waiting_on,
        stale_reason=stale_reason,
        now=now,
    )
    if next_action and next_action.strip():
        workstream_doc.sections["Next Actions"] = [f"- {next_action.strip()}"]
    return persist_workstream_doc(repo_root, slug, workstream_doc, objective=objective)


def managed_agents_block() -> str:
    return "\n".join(
        [
            AGENTS_START,
            "## Workflow Ledger",
            "",
            "- For substantial multi-step work, consult `todos.md` first.",
            "- Consult `todo-status.md` for pickup-ready, claimed, idle, and stale workstreams before starting parallel work.",
            "- Treat `todos.md` as the canonical active plan and progress ledger.",
            "- For concurrent workstreams, consult the relevant file under `todos/`.",
            "- In plan mode, consult `todo-orchestrator/references/planning-workflow.md`.",
            "- In implementation mode, continue from the recorded plan non-interactively unless truly blocked.",
            "- Run `review_staleness.py` before assuming an old idle stream is still current.",
            "- Prefer relevant repo-local skills and reference files when they match the task.",
            AGENTS_END,
            "",
        ]
    )


def ensure_agents_md(repo_root: Path) -> Path:
    path = repo_root / "AGENTS.md"
    block = managed_agents_block()
    if path.exists():
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"{re.escape(AGENTS_START)}.*?{re.escape(AGENTS_END)}\n?",
            re.DOTALL,
        )
        if pattern.search(text):
            text = pattern.sub(block, text).rstrip() + "\n"
        else:
            text = text.rstrip() + "\n\n" + block
    else:
        text = "# Repo Guidance\n\n" + block
    path.write_text(text, encoding="utf-8")
    return path


def detect_resume_state(repo_root: Path) -> dict[str, object]:
    root_path = repo_root / "todos.md"
    if not root_path.exists():
        return {
            "has_root_todos": False,
            "has_agents_md": (repo_root / "AGENTS.md").exists(),
            "active_workstreams": [],
            "pickup_ready_workstreams": [],
            "claimed_workstreams": [],
            "stale_workstreams": [],
            "stale_candidate_workstreams": [],
            "stale_claimed_workstreams": [],
            "cleanup_ready": False,
        }
    root_doc = load_root_doc(repo_root)
    entries = parse_workstream_entries(clear_placeholder(root_doc.sections.get("Workstreams", [WORKSTREAM_PLACEHOLDER])))
    active = [entry for entry in entries if not is_done_status(entry["status"])]
    status_doc = load_status_doc(repo_root)
    status_entries = parse_status_entries(clear_placeholder(status_doc.sections.get("Workstreams", [STATUS_PLACEHOLDER])))
    claimed = [entry for entry in status_entries if entry.get("execution", "").strip() == "claimed"]
    reviews = review_workstream_staleness(repo_root)
    stale = [entry for entry in reviews if entry["classification"] == "stale"]
    stale_candidates = [entry for entry in reviews if entry["classification"] == "stale_candidate"]
    stale_claimed = [entry for entry in reviews if entry.get("claimed_inconsistency")]
    return {
        "has_root_todos": True,
        "has_agents_md": (repo_root / "AGENTS.md").exists(),
        "active_workstreams": active,
        "pickup_ready_workstreams": pickup_ready_entries(status_entries),
        "claimed_workstreams": claimed,
        "stale_workstreams": stale,
        "stale_candidate_workstreams": stale_candidates,
        "stale_claimed_workstreams": stale_claimed,
        "cleanup_ready": bool(status_entries) and not active,
    }


def parse_frontmatter_block(text: str) -> "OrderedDict[str, str]":
    data: "OrderedDict[str, str]" = OrderedDict()
    current_key: str | None = None
    block_key: str | None = None
    block_lines: list[str] = []
    for raw_line in text.splitlines():
        if re.match(r"^[A-Za-z0-9_-]+:", raw_line):
            if block_key is not None:
                data[block_key] = "\n".join(block_lines).strip()
                block_key = None
                block_lines = []
            current_key = None
            key, value = raw_line.split(":", 1)
            cleaned_key = key.strip()
            cleaned_value = value.strip()
            if cleaned_value in {">", ">-", "|", "|-"}:
                current_key = cleaned_key
                block_key = cleaned_key
                data.setdefault(cleaned_key, "")
            else:
                parsed_value = cleaned_value
                if parsed_value.startswith('"') and parsed_value.endswith('"'):
                    try:
                        parsed_value = json.loads(parsed_value)
                    except json.JSONDecodeError:
                        parsed_value = parsed_value.strip('"')
                data[cleaned_key] = parsed_value.strip('"')
                current_key = cleaned_key
            continue
        if raw_line.startswith((" ", "\t")) and current_key is not None:
            continuation = raw_line.strip()
            if block_key == current_key:
                block_lines.append(continuation)
            elif continuation:
                data[current_key] = f"{data.get(current_key, '')} {continuation}".strip()
            continue
        if block_key is not None:
            data[block_key] = "\n".join(block_lines).strip()
            block_key = None
            block_lines = []
        current_key = None
    if block_key is not None:
        data[block_key] = "\n".join(block_lines).strip()
    return data


def parse_frontmatter(text: str) -> dict[str, str]:
    frontmatter, body = split_frontmatter(text)
    if frontmatter:
        return dict(frontmatter)
    match = FRONTMATTER_PATTERN.match(text)
    if match:
        return dict(parse_frontmatter_block(match.group(1)))
    if text.startswith("---\n"):
        return dict(parse_frontmatter_block(text))
    _ = body
    return {}


def dumps_json(data: object, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(data, indent=2, sort_keys=True) + "\n"
    return json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n"
