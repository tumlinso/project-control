"""Compact policy telemetry derived from objective bake-off evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .policy import DelegationPolicy


FORMAT = "CORE4-POLICY-REPORT/1"


def qwen_harness_telemetry(records: object) -> dict[str, Any]:
    """Compact nested-event telemetry; Qwen result statistics remain evidence."""
    tool_names: list[str] = []
    terminal_reason = ""
    stats: dict[str, Any] = {}

    def visit(value: object) -> None:
        nonlocal terminal_reason, stats
        if isinstance(value, list):
            for item in value: visit(item)
        elif isinstance(value, dict):
            if value.get("type") == "tool_use" and value.get("name"):
                tool_names.append(str(value["name"]))
            if value.get("type") == "result":
                terminal_reason = str(value.get("subtype") or value.get("result") or "")
                if isinstance(value.get("stats"), dict): stats = dict(value["stats"])
            for item in value.values(): visit(item)

    visit(records)
    reported = stats.get("tools", {}).get("totalCalls") if isinstance(stats.get("tools"), dict) else None
    tool_calls = max(len(tool_names), int(reported or 0))
    lower = terminal_reason.lower()
    token_total = None
    models = stats.get("models")
    if isinstance(models, dict):
        totals = []
        for model in models.values():
            tokens = model.get("tokens") if isinstance(model, dict) else None
            if isinstance(tokens, dict) and isinstance(tokens.get("total"), (int, float)):
                totals.append(int(tokens["total"]))
        if totals:
            token_total = sum(totals)
    return {
        "tool_calls": tool_calls,
        "tool_names": sorted(set(tool_names)),
        "terminal_reason": terminal_reason[:500],
        "budget_exhausted": any(token in lower for token in ("budget", "max session turns", "max tool", "wall-clock")),
        "preempted": "preempt" in lower or "cancel" in lower,
        "token_total": token_total,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_policy_report(bakeoff: object, *, source_sha256: str) -> dict[str, Any]:
    if not isinstance(bakeoff, dict):
        raise ValueError("bake-off evidence must be an object")
    policy = DelegationPolicy.from_bakeoff(bakeoff)
    phase_b = bakeoff.get("phases", {}).get("B", [])
    attempts = [task for candidate in phase_b if isinstance(candidate, dict)
                for task in candidate.get("tasks", []) if isinstance(task, dict)]
    completed = [candidate for candidate in phase_b
                 if isinstance(candidate, dict) and candidate.get("status") == "completed"]
    nearest = max(completed, key=lambda item: float(item.get("acceptance_rate", 0)), default=None)
    accepted = sum(1 for task in attempts if task.get("accepted") is True)
    rework = sum(int(task.get("frontier_rework_required", 0) or 0) for task in attempts)
    summary = bakeoff.get("summary") if isinstance(bakeoff.get("summary"), dict) else {}
    return {
        "format": FORMAT,
        "schema_version": 1,
        "source": {"format": bakeoff.get("format"), "sha256": source_sha256},
        "measurement": {
            "phase_a_candidates": int(summary.get("evaluated_candidates", 0)),
            "phase_a_survivors": int(summary.get("phase_a_survivors", 0)),
            "phase_b_survivors": int(summary.get("phase_b_survivors", 0)),
            "phase_b_task_attempts": len(attempts),
            "accepted_task_attempts": accepted,
            "frontier_rework_events": rework,
            "nearest_candidate": None if nearest is None else {
                "candidate_id": nearest.get("candidate_id"),
                "acceptance_rate": nearest.get("acceptance_rate"),
            },
        },
        "policy": policy.as_record(),
        "review": {
            "reviewer_default": False,
            "double_solve_default": False,
            "reason": "CORE4-17 measured no accepted production profile or marginal reviewer value.",
        },
        "context": {
            "model_context_default": None,
            "packet_budget_basis": "CTXPP fixed budget tiers; consumer outcomes were unavailable.",
        },
        "deployment": {
            "selected_candidate": None,
            "selected_quantization": None,
            "selected_harness": None,
            "selected_worker_layout": None,
            "idle_model_residency": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bakeoff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bakeoff = json.loads(args.bakeoff.read_text(encoding="utf-8"))
    report = build_policy_report(bakeoff, source_sha256=_sha256(args.bakeoff))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"format": FORMAT, "output": str(args.output),
                      "real_local_enabled": report["policy"]["real_local_enabled"]}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
