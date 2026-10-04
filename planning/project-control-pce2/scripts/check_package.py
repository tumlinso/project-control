#!/usr/bin/env python3
"""Read-only integrity and consistency checks for the PCE2 bootstrap.

This is not a product test suite, native Todo interpreter, authorization check,
or billing limiter. Remote validation records remain dated observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit


class PackageError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value: str) -> Any:
    raise PackageError(f"Non-finite JSON constant: {value}")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"),
                      object_pairs_hook=unique_pairs, parse_constant=reject_constant)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def acyclic(nodes: set[str], edges: list[tuple[str, str]], label: str) -> None:
    followers: dict[str, set[str]] = {node: set() for node in nodes}
    indegrees = {node: 0 for node in nodes}
    for before, after in edges:
        require(before in nodes and after in nodes, f"{label}: missing node in {before} -> {after}")
        if after not in followers[before]:
            followers[before].add(after)
            indegrees[after] += 1
    queue = sorted(node for node, degree in indegrees.items() if degree == 0)
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for following in followers[node]:
            indegrees[following] -= 1
            if not indegrees[following]:
                queue.append(following)
    require(visited == len(nodes), f"{label}: cycle detected")


def indexed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result = {row["id"]: row for row in rows}
    require(len(result) == len(rows), f"{label}: duplicate IDs")
    return result


def verify_manifest(root: Path) -> int:
    manifest = root / "MANIFEST.sha256"
    require(manifest.is_file(), "Missing MANIFEST.sha256")
    actual: set[str] = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), f"Symlink not allowed in sealed package: {path}")
        if path.is_file() and path != manifest:
            actual.add(path.relative_to(root).as_posix())
    expected: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
        require(match is not None, "Malformed manifest line")
        assert match is not None
        digest, relative = match.groups()
        require(safe_relative(relative) and relative != "MANIFEST.sha256", "Unsafe manifest path")
        require(relative not in expected, f"Duplicate manifest entry: {relative}")
        expected.add(relative)
        target = root / relative
        require(target.is_file(), f"Missing sealed file: {relative}")
        require(sha(target.read_bytes()) == digest, f"Manifest mismatch: {relative}")
    require(actual == expected, f"Manifest inventory differs: {sorted(actual ^ expected)}")
    return len(expected)


def check(root: Path, *, installed_native: bool = False) -> dict[str, Any]:
    root = root.resolve()
    file_count = verify_manifest(root)
    documents = {p.relative_to(root).as_posix(): load(p) for p in root.rglob("*.json")}
    source = documents["evidence/sources.json"]
    evidence = indexed(source["sources"] + source["retained_findings"] + source["observations"], "evidence")
    outcomes = indexed(documents["machine/outcomes.json"]["outcomes"], "outcomes")
    cases = indexed(documents["machine/acceptance.json"]["scenarios"], "scenarios")
    require(len(outcomes) == 6, "Expected six durable outcomes")
    require(set(cases) == {f"J{i:02}" for i in range(1, 25)}, "Expected J01-J24")
    for row in list(outcomes.values()) + list(cases.values()):
        require(set(row.get("sources", [])) <= set(evidence), f"Unknown evidence in {row['id']}")
    for case in cases.values():
        require(case["owner"] in outcomes and case["mandatory"] is True, f"Invalid acceptance owner: {case['id']}")
    for outcome in outcomes.values():
        require(any(c["owner"] == outcome["id"] for c in cases.values()), f"Outcome has no acceptance: {outcome['id']}")

    usage = documents["machine/usage.json"]
    require(set(usage["allocations"]) == set(outcomes), "Usage allocations do not match outcomes")
    require(all(type(v) is int and v > 0 for v in usage["allocations"].values()), "Invalid allocation")
    require(type(usage["shared_reserve"]) is int and usage["shared_reserve"] >= 0, "Invalid reserve")
    cap = usage["shared_stop_work_ceiling"]
    require(type(cap) is int and cap == 3_000_000, "Unexpected shared ceiling")
    require(sum(usage["allocations"].values()) + usage["shared_reserve"] == cap, "Usage allocations + reserve differ from ceiling")
    lower, upper = usage["expected_total_range"]
    require(0 < lower <= upper <= cap, "Invalid expected usage range")
    require(usage["structural_quotas"] is None, "PCE2 must not silently restore structural quotas")
    require(all(o["allocation"] == usage["allocations"][k] for k, o in outcomes.items()), "Outcome allocation disagreement")

    replacements = documents["machine/pce1-mapping.json"]
    require(set(replacements["acceptance_map"]) == {f"A{i:02}" for i in range(1, 25)}, "PCE1 coverage incomplete")
    require(all(v and set(v) <= set(cases) for v in replacements["acceptance_map"].values()), "Invalid PCE1 mapping")
    require(set(replacements["policy_replacements"]) == {"A16", "A17", "A24"}, "Policy replacement set changed")

    native_tasks: dict[str, dict[str, Any]] = {}
    schemas: dict[str, dict[str, Any]] = {}
    native_reports: dict[str, Any] = {}
    for repository, prefix in (("skills", "SK"), ("project-control", "PC")):
        relative = f"machine/{repository}.todo-plan.json"
        plan = documents[relative]
        schemas[repository] = plan
        require(plan["schema_version"] == 3 and plan["project"]["name"] == repository, f"Wrong native target/schema: {repository}")
        tasks = indexed(plan["tasks"], repository)
        require(len(tasks) == 4 and all(k.startswith(prefix + "-PCE2-") for k in tasks), f"Wrong native membership: {repository}")
        aggregate = prefix + "-PCE2-0000"
        require(tasks[aggregate]["kind"] == "epic", f"Missing aggregate: {repository}")
        edges = []
        for task in tasks.values():
            parent = task.get("parent_id")
            if parent:
                require(parent in tasks, f"Missing native parent: {parent}")
                edges.append((parent, task["id"]))
            if task["id"] != aggregate:
                require(task["id"] in outcomes and parent == aggregate, f"Incorrect outcome parent: {task['id']}")
                outcome = outcomes[task["id"]]
                require(outcome["repository"] == repository, "Cross-authority task placed in wrong plan")
                require(task["title"] == outcome["title"] and task["objective"] == outcome["objective"], f"Task/catalog disagreement: {task['id']}")
                require(set(task["scope"]["exclusive_paths"]) == set(outcome["scopes"]), f"Scope disagreement: {task['id']}")
            for category in ("exclusive_paths", "read_paths", "forbidden_paths"):
                require(all(safe_relative(path) for path in task.get("scope", {}).get(category, [])), "Unsafe native scope path")
            prerequisites = []
            for dep in task.get("depends_on", []):
                require(dep["type"] == "task" and dep["task_id"] in tasks, "Unsupported/foreign native prerequisite")
                require(dep["task_id"] != aggregate, "Child depends on its aggregate")
                edges.append((dep["task_id"], task["id"]))
                prerequisites.append(dep["task_id"])
            if task["id"] in outcomes:
                require(set(prerequisites) == set(outcomes[task["id"]]["depends_on"]), "Native/catalog dependencies differ")
        acyclic(set(tasks), edges, repository + " native graph")
        require(len(plan["runs"]) == 1, "Expected one bootstrap run per repository")
        run = plan["runs"][0]
        require(run["root_task_id"] == aggregate, "Wrong run root")
        require(len(run["lanes"]) == 1, "Expected one default lane")
        lane = run["lanes"][0]
        require(lane["role"] == "implementer" and lane["workspace"]["mode"] == "exclusive", "Unexpected bootstrap lane contract")
        queue = lane["tasks"]
        require(len(queue) == len(set(queue)) and set(queue) == set(tasks) and queue[-1] == aggregate, "Invalid lane queue")
        positions = {k: i for i, k in enumerate(queue)}
        for task in tasks.values():
            for dep in task.get("depends_on", []):
                require(positions[dep["task_id"]] < positions[task["id"]], "Queue contradicts dependency order")
        native_tasks.update(tasks)
        require(len(json.dumps(run["charter"]).encode()) <= 8192, "Charter exceeds current native budget")
        if installed_native:
            from todo_orchestrator.plan import validate_plan
            native_reports[repository] = validate_plan(plan, repo_root=None)

    require(set(native_tasks) == set(outcomes) | {"PC-PCE2-0000", "SK-PCE2-0000"}, "Native/catalog membership differs")
    combined_edges = [(d, k) for k, o in outcomes.items() for d in o["depends_on"]]
    for edge in documents["machine/cross-authority.json"]["edges"]:
        producer, consumer = edge["producer"], edge["consumer"]
        require(producer in outcomes and consumer in outcomes, "Missing cross-authority endpoint")
        require(outcomes[producer]["repository"] != outcomes[consumer]["repository"], "Cross-authority edge is local")
        require(bool(edge["contract"].strip()), "Empty cross-authority contract")
        combined_edges.append((producer, consumer))
    acyclic(set(outcomes), combined_edges, "combined outcome graph")

    validation = documents["evidence/validation.json"]
    require(validation["product_acceptance_executed"] is False and validation["live_mutations"] is False, "Package must not claim implementation acceptance")
    require(set(validation["native_plans"]) == set(schemas), "Missing native validation record")
    for repository, plan in schemas.items():
        record = validation["native_plans"][repository]
        path = root / f"machine/{repository}.todo-plan.json"
        require(record["file_sha256"] == sha(path.read_bytes()), f"Native file no longer matches validation: {repository}")
        digest = sha(json.dumps(plan, sort_keys=True, indent=2).encode())
        require(record["validated_payload_digest"] == digest, f"Remote-validated native payload changed: {repository}")
        require(record["payload_valid"] is True, "Native payload validity not established")
    require(validation["native_plans"]["project-control"]["actual_target_validated"] is False, "Do not claim a successful PC target diff")

    links = 0
    for path in root.rglob("*.md"):
        for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            parsed = urlsplit(raw)
            if parsed.scheme or not parsed.path:
                continue
            target = (path.parent / unquote(parsed.path)).resolve()
            require(target.is_relative_to(root) and target.is_file(), f"Broken/escaping Markdown link in {path.name}: {raw}")
            links += 1
    return {"status": "passed", "sealed_files": file_count, "json_files": len(documents),
            "outcomes": len(outcomes), "native_tasks": len(native_tasks), "acceptance_groups": len(cases),
            "checked_markdown_links": links, "shared_ceiling": cap,
            "installed_native_validation": native_reports or "not_requested",
            "limitations": "Package consistency only; dated native validation is not current target readiness, product acceptance or a billing limit."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--installed-native", action="store_true", help="Also use an already installed Todo validator; never change import paths or install packages")
    args = parser.parse_args()
    try:
        result = check(args.root, installed_native=args.installed_native)
    except (PackageError, OSError, ValueError, KeyError, TypeError, ImportError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
