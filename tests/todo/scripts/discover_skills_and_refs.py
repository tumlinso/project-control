#!/usr/bin/env python3
"""Discover repo-local skills and likely-useful reference files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from todo_common import dumps_json, parse_frontmatter


IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}

REFERENCE_KEYWORDS = [
    "plan",
    "planning",
    "workflow",
    "architecture",
    "style",
    "guide",
    "notes",
    "reference",
    "design",
    "validation",
]


def tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 3}


def walk_files(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for path in repo_root.rglob("*"):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.is_file():
            paths.append(path)
    return paths


def score_skill(path: Path, task_terms: set[str]) -> tuple[int, str]:
    text = path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(text)
    name = frontmatter.get("name", path.parent.name)
    description = frontmatter.get("description", "")
    haystack = " ".join([name, description, str(path.parent)])
    hay_terms = tokenize(haystack)
    matches = sorted(task_terms & hay_terms)
    score = 2 + len(matches) * 2
    reason = "skill metadata matches the task" if matches else "repo-local skill candidate"
    return score, reason


def looks_like_reference(path: Path) -> bool:
    if path.name == "AGENTS.md" or path.name == "todos.md":
        return True
    if path.suffix.lower() != ".md":
        return False
    stem = path.stem.lower()
    return any(keyword in stem for keyword in REFERENCE_KEYWORDS) or "references" in path.parts or "docs" in path.parts


def score_reference(path: Path, task_terms: set[str]) -> tuple[int, str]:
    score = 1
    matched_keywords = [keyword for keyword in REFERENCE_KEYWORDS if keyword in path.name.lower()]
    score += len(matched_keywords) * 2
    path_terms = tokenize(str(path))
    task_matches = sorted(task_terms & path_terms)
    score += len(task_matches) * 2
    reasons: list[str] = []
    if matched_keywords:
        reasons.append("reference-style filename")
    if task_matches:
        reasons.append("matches task terms")
    if path.name in {"AGENTS.md", "todos.md"}:
        reasons.append("repo workflow control file")
    return score, ", ".join(reasons) if reasons else "repo-local reference candidate"


def discover(repo_root: Path, task: str, limit: int) -> dict[str, object]:
    task_terms = tokenize(task)
    skills: list[dict[str, object]] = []
    refs: list[dict[str, object]] = []

    for path in walk_files(repo_root):
        rel_path = path.relative_to(repo_root)
        if path.name == "SKILL.md":
            text = path.read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(text)
            score, reason = score_skill(path, task_terms)
            skills.append(
                {
                    "name": frontmatter.get("name", path.parent.name),
                    "description": frontmatter.get("description", ""),
                    "path": str(rel_path),
                    "score": score,
                    "reason": reason,
                }
            )
        elif looks_like_reference(path):
            score, reason = score_reference(path, task_terms)
            refs.append(
                {
                    "path": str(rel_path),
                    "score": score,
                    "reason": reason,
                }
            )

    skills.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    refs.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return {
        "repo_root": str(repo_root),
        "task": task,
        "skills": skills[:limit],
        "reference_files": refs[:limit],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover repo-local skills and useful reference files.")
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument("--task", default="", help="Optional task description used to rank matches.")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of skills and references to emit.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    print(dumps_json(discover(repo_root, args.task, args.limit), pretty=args.pretty), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
