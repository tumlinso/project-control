#!/usr/bin/env python3
"""Qualify PA1 evaluation source identity without starting model inference."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from project_control.assistance.quality import (  # noqa: E402
    QualificationError,
    evaluate_cases,
    create_held_out_fixture,
    load_eval_contract,
    qualify_source,
)


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=REPO / "planning/project-assistance-v1")
    parser.add_argument("--source", type=Path, default=REPO / "src/project_control")
    parser.add_argument("--fixture", type=Path, default=REPO / "planning/project-assistance-v1/fixtures/repository")
    parser.add_argument("--config", type=Path, action="append", default=[], help="hash a configuration file")
    parser.add_argument("--expect-source-sha256")
    parser.add_argument("--expect-fixture-sha256")
    parser.add_argument("--responses", type=Path, help="score supplied JSON responses; this is not inference")
    parser.add_argument("--case", action="append", dest="cases", help="fixed case ID; repeat as needed")
    parser.add_argument("--held-out", action="store_true", help="mark the supplied fixture as held out")
    parser.add_argument("--create-held-out", type=Path,
                        help="create a deterministic renamed/numeric-varied copy at this path")
    parser.add_argument("--output", type=Path, help="write JSON report here (stdout by default)")
    # There is intentionally no model client in this CLI. An actual-model path
    # must be added only with an explicit adapter and the existing lease contract.
    parser.add_argument("--go-real", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.go_real:
        parser.error("actual inference is unavailable in this source-only CLI; use the existing qualified adapter and lease workflow")
    source_root = args.source.resolve(strict=True)
    spec = importlib.util.find_spec("project_control.assistance.quality")
    if spec is None or spec.origin is None or not _under(Path(spec.origin), source_root):
        raise QualificationError("imported quality module is not from the requested source tree")
    cases_contract, _ = load_eval_contract(args.package)
    fixture_root = args.fixture.resolve(strict=True)
    if args.create_held_out:
        fixture_root = create_held_out_fixture(fixture_root, args.create_held_out)
        args.held_out = True
    source = qualify_source(source_root, config_paths=args.config,
                            expected_source_sha256=args.expect_source_sha256,
                            fixture_root=fixture_root,
                            expected_fixture_sha256=args.expect_fixture_sha256)
    report: dict
    if args.responses:
        payload = json.loads(args.responses.read_text(encoding="utf-8"))
        responses = payload.get("responses") if isinstance(payload, dict) else None
        if not isinstance(responses, dict):
            raise QualificationError("responses file must contain an object named 'responses'")
        case_ids = args.cases or [case_id for case_id in cases_contract["first_comparison"]
                                  if case_id in responses]
        if not case_ids:
            raise QualificationError("responses file contains no selected first-comparison cases")
        report = evaluate_cases(lambda request: responses[request["case_id"]],
                                package_root=args.package, fixture_root=fixture_root,
                                case_ids=case_ids, source=source,
                                axes=payload.get("axes", {"mode": "supplied_responses"}),
                                budgets=payload.get("budgets", {"max_cases": len(case_ids), "max_trials": 1}),
                                held_out=args.held_out, execution_mode="scripted")
    else:
        report = {"format": "pa1-source-qualification/1", **source.as_dict(),
                  "quality_cases_executed": 0, "inference_performed": False,
                  "status": "qualified_source_only"}
    output = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except QualificationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
