#!/usr/bin/env python3
"""Measure opt-in source preparation economics against a disposable fixture.

The default mode only writes a plan. ``--execute-live`` is required to use the
already-running verified observer supervisor. All mutable repository and AS1
state is created under a private artifact root and retained for review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
FIXTURE = REPO / "planning/project-assistance-v1/fixtures/repository"
CENTRAL_STATE = Path.home() / ".cache/project-control/as1-observer-analysis"
MAX_WALL_SECONDS = 120
MAX_INQUIRIES = 6
MAX_AUTO_ROOTS = 2
MAX_RESERVED_TURNS = 12
SOURCE_PATHS = ("demo/budgets.py", "demo/controller.py")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_dir(path: Path) -> Path:
    if path.exists() and path.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = path.resolve(strict=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("artifact root must be a directory owned by this user")
    path.chmod(0o700)
    if path.stat().st_mode & 0o077:
        raise ValueError("artifact root permissions must be 0700")
    return path


def plan() -> dict[str, Any]:
    return {
        "format": "pa1-assistance-economics/1",
        "status": "planned_not_executed",
        "inference_performed": False,
        "sequence": [
            "baseline_foreground", "automatic_preparation_1", "prepared_foreground",
            "disposable_source_change", "automatic_refresh_2", "refreshed_foreground",
            "control_foreground_if_budget_allows",
        ],
        "budget": {"max_new_inquiries": MAX_INQUIRIES,
                   "foreground_inquiries": 4, "automatic_roots": MAX_AUTO_ROOTS,
                   "max_reserved_turns": MAX_RESERVED_TURNS,
                   "wall_seconds": MAX_WALL_SECONDS},
        "source_paths": list(SOURCE_PATHS),
        "thresholds": {"max_added_wait_seconds": 2.0,
                       "max_elapsed_overhead_fraction": 0.10,
                       "quality_degradation_allowed": False},
        "automatic_after_run": "disabled",
        "unmeasured_fields": ["foreground_model_turns", "foreground_tokens",
                              "reasoning_tokens", "prefill_time"],
    }


def _validate_source_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Seal the economics harness to its reviewed inputs before any read."""
    values = tuple(paths)
    if not values or len(values) > len(SOURCE_PATHS) or len(set(values)) != len(values):
        raise ValueError("source selection must use the reviewed fixture inputs")
    if any(not isinstance(value, str) or value not in SOURCE_PATHS or
           value == ".env" or value.startswith(".") for value in values):
        raise ValueError("source path is outside the reviewed fixture allowlist")
    return values


def _write_report(root: Path, report: dict[str, Any]) -> None:
    target = root / "report.json"
    temp = target.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, target)


def _disposable_repository(root: Path) -> Path:
    repo = root / "disposable-repository"
    if repo.exists():
        raise ValueError("disposable repository already exists; choose a fresh artifact root")
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    (repo / ".env").write_text("PA1_ECONOMICS_SECRET_SENTINEL=must-not-be-read\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "demo", "README.md", ".env"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=PA1 fixture",
                    "-c", "user.email=pa1-fixture@example.invalid", "commit", "-qm",
                    "fixture baseline"], check=True)
    return repo.resolve(strict=True)


def _private_config(repo: Path, root: Path, original):
    """Build a one-project configuration without changing canonical config."""
    from project_control.config import RepositoryConfig, WorkspaceConfig, save_config

    config = original.model_copy(deep=True)
    prior = next(iter(original.workspaces.values()), None)
    config.workspaces = {
        "economics": WorkspaceConfig(
            display_name="Disposable economics fixture", authority_repository="fixture",
            repositories={"fixture": RepositoryConfig(root=repo)},
            deny_patterns=list(prior.deny_patterns if prior else []),
        )
    }
    config.programs = {}
    config_root = root / "private-config"
    (config_root / "project-control").mkdir(parents=True, mode=0o700)
    save_config(config, config_root / "project-control/config.toml")
    return config_root


def _mutate_fixture(repo: Path, version: int) -> None:
    """A fixed scripted source edit used as the external change stimulus."""
    budget = repo / "demo/budgets.py"
    text = budget.read_text(encoding="utf-8")
    text = __import__("re").sub(r"MAX_STEPS\s*=\s*\d+", f"MAX_STEPS = {6 + version}", text)
    text = __import__("re").sub(r"INQUIRY_SECONDS\s*=\s*\d+", f"INQUIRY_SECONDS = {300 - 60 * version}", text)
    budget.write_text(text, encoding="utf-8")
    controller = repo / "demo/controller.py"
    controller_text = controller.read_text(encoding="utf-8")
    controller_text = controller_text.replace("budget_seconds=INQUIRY_SECONDS",
                                              f"budget_seconds=INQUIRY_SECONDS  # economics revision {version}")
    controller.write_text(controller_text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "demo/budgets.py", "demo/controller.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=PA1 fixture",
                    "-c", "user.email=pa1-fixture@example.invalid", "commit", "-qm",
                    f"scripted source edit {version}"], check=True)


def _source_evidence(repo: Path) -> tuple[str, list[dict[str, str]]]:
    rows = []
    for relative in _validate_source_paths(SOURCE_PATHS):
        data = (repo / relative).read_bytes()
        rows.append({"path": relative, "sha256": _hash(data),
                     "text": data.decode("utf-8", errors="strict")})
    content = "\n\n".join(f"SOURCE {item['path']} sha256={item['sha256']}\n{item['text']}"
                           for item in rows)
    return content, rows


def _locators(rows: list[dict[str, str]], revision: str) -> list[dict[str, str]]:
    return [{"project": "economics", "repository": "fixture", "path": row["path"],
             "content_sha256": row["sha256"], "revision": revision} for row in rows]


def _execute(root: Path) -> dict[str, Any]:
    if Path(os.environ.get("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", "")).expanduser().resolve() != CENTRAL_STATE.resolve():
        raise ValueError("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR must identify the verified central observer state")

    from project_control.app import Runtime
    from project_control.as1_surface import compose_surface
    from project_control.assistance.operator import AssistanceOperator
    from project_control.assistance.power import PowerPolicy
    from project_control.config import load_config
    from project_control.observer_analysis import SkillsObserverAnalysisProvider
    from project_control.profiles import MCPProfile
    from project_control.runtime_binding import bind_local_runtime
    from project_control.assistance import attention as attention_module

    original_config = load_config()
    repo = _disposable_repository(root)
    config_home = _private_config(repo, root, original_config)
    state_root = root / "isolated-as1-state"
    if state_root.exists():
        raise ValueError("isolated state already exists; choose a fresh artifact root")
    previous_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    composition = None
    operator = None
    report = plan()
    report.update({"status": "preflight", "fixture_root": str(repo),
                   "private_config": str(config_home), "isolated_state_root": str(state_root),
                   "inquiry_count": 0, "automatic_root_count": 0, "foreground": [],
                   "automatic": [], "owned_receipts": [], "source_shadow_denied": False})
    started = time.monotonic()
    original_turn_cap = attention_module.MAX_RESERVED_TURNS
    attention_module.MAX_RESERVED_TURNS = MAX_RESERVED_TURNS
    focus_id = None
    try:
        runtime_identity = bind_local_runtime()
        if not runtime_identity.manifest_sha256 or not runtime_identity.root.is_dir():
            raise ValueError("canonical source/runtime identity is incomplete")
        runtime = Runtime(load_config())
        backend = SkillsObserverAnalysisProvider()
        status = backend.central_status(deadline_epoch=time.time() + 5)
        if isinstance(status.get("supervisor_pid"), bool) or not isinstance(status.get("supervisor_pid"), int):
            raise ValueError("the verified central observer supervisor is not ready")
        report["runtime"] = {"root": str(runtime_identity.root),
                             "manifest_sha256": runtime_identity.manifest_sha256,
                             "fingerprint": runtime_identity.fingerprint,
                             "source_commit": runtime.source_commit,
                             "source_sha256": _hash(SCRIPT.read_bytes()),
                             "supervisor_runtime_fingerprint": status.get("runtime_fingerprint"),
                             "supervisor_source_sha256": status.get("source_sha256"),
                             "supervisor_state_root": status.get("service_state_root")}
        composition = compose_surface(runtime, MCPProfile.OBSERVER, state_directory=state_root,
                                      backend=backend)
        scope = composition.scope("economics")
        composition.jobs.start()
        operator = AssistanceOperator(state_root=state_root)
        try:
            _validate_source_paths((".env",))
        except ValueError:
            report["source_shadow_denied"] = True
        if not report["source_shadow_denied"]:
            raise ValueError("private .env was not denied by the source-selection seal")

        def packet_and_inquire(label: str, question: str, *, use_handoff: bool = False) -> dict[str, Any]:
            evidence_text, evidence_rows = _source_evidence(repo)
            revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            packet_text = evidence_text
            packet_sources = _locators(evidence_rows, revision)
            handoff_record = None
            if use_handoff:
                proposal = operator.handoff(project="economics", focus_id=focus_id,
                    trusted_projects={"economics"}, trusted_root=repo, trusted_repository="fixture")
                suggestions = proposal.get("suggestions", [])
                handoff_record = suggestions[0] if suggestions else None
                if handoff_record:
                    packet_text += "\n\nSOURCE-BACKED PREPARED HANDOFF\n" + json.dumps(
                        {key: handoff_record.get(key) for key in
                         ("note_id", "claim", "reason_matters", "sources", "uncertainty", "next_action")},
                        sort_keys=True, ensure_ascii=False)
            packet = composition.store.create(tool="read", access_scope=scope,
                payload={"text": packet_text, "source_files": evidence_rows},
                sources=packet_sources, ttl_seconds=None)
            before = time.monotonic()
            remaining = max(0.0, started + MAX_WALL_SECONDS - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("120-second economics wall budget exhausted")
            result = composition.jobs.inquire(question, access_scope=scope, hints=[packet.alias],
                request_id="pa1-econ-" + uuid.uuid4().hex,
                foreground_timeout=min(12.0, remaining))
            elapsed = time.monotonic() - before
            job = result.get("job") if isinstance(result.get("job"), dict) else {}
            answer = job.get("answer")
            report["inference_performed"] = report["inference_performed"] or bool(job.get("job_id"))
            return {"label": label, "status": result.get("status"), "job_id": job.get("job_id"),
                    "request_sha256": _hash(question.encode()), "elapsed_seconds": round(elapsed, 4),
                    "answer": answer, "source_revision": revision,
                    "context_bytes": len(packet_text.encode("utf-8")),
                    "context_tokens": "not_exposed_by_public_job_result",
                    "model_turns": "not_exposed_by_public_job_result",
                    "reasoning_tokens": "not_exposed_by_public_job_result",
                    "prefill_time": "not_exposed_by_public_job_result",
                    "handoff_note_id": handoff_record.get("note_id") if handoff_record else None,
                    "source_hashes": {row["path"]: row["sha256"] for row in evidence_rows}}

        def foreground(label: str, expected_steps: int, expected_timeout: int, prepared: bool) -> dict[str, Any]:
            question = (f"For {label}, read demo/budgets.py and report MAX_STEPS and INQUIRY_SECONDS "
                        "with exact source expressions; also state which controller file consumes them.")
            row = packet_and_inquire(label, question, use_handoff=prepared)
            row["expected"] = {"MAX_STEPS": expected_steps, "INQUIRY_SECONDS": expected_timeout}
            answer = row.get("answer") or ""
            row["quality_pass"] = (f"MAX_STEPS = {expected_steps}" in answer and
                                   f"INQUIRY_SECONDS = {expected_timeout}" in answer and
                                   "controller.py" in answer)
            report["foreground"].append(row)
            report["inquiry_count"] += int(bool(row.get("job_id")))
            return row

        def await_preparation(label: str) -> dict[str, Any]:
            preparation_started = time.monotonic()
            counted_jobs: set[str] = set()
            end = min(started + MAX_WALL_SECONDS, preparation_started + 25)
            last = {"status": "pending"}
            while time.monotonic() < end:
                tick = composition.attention_tick()
                candidates = operator.status().get("focus")
                # The tick dispatches once stable and reconciles completed work on
                # the following tick. The private candidate table is read only.
                with composition.jobs._db() as db:
                    row = db.execute("SELECT candidate_id,job_id,status FROM pa1_attention_candidates "
                                     "WHERE focus_id=? ORDER BY changed_at DESC LIMIT 1", (focus_id,)).fetchone()
                    candidate = dict(row) if row else None
                if candidate and candidate.get("job_id"):
                    if candidate["job_id"] not in counted_jobs:
                        counted_jobs.add(candidate["job_id"])
                        report["automatic_root_count"] += 1
                        report["inquiry_count"] += 1
                        report["inference_performed"] = True
                        if report["automatic_root_count"] > MAX_AUTO_ROOTS or report["inquiry_count"] > MAX_INQUIRIES:
                            raise ValueError("automatic root or inquiry budget was exceeded")
                    lookup = composition.jobs.preparation_lookup(candidate["job_id"], access_scope=scope)
                    if lookup.get("status") in {"completed", "partial", "failed", "cancelled", "unavailable"}:
                        composition.attention_tick()
                        row = {"label": label, "status": lookup.get("status"),
                               "job_id": candidate["job_id"],
                               "elapsed_seconds": round(time.monotonic() - preparation_started, 4),
                               "context_bytes": sum((repo / path).stat().st_size
                                                    for path in SOURCE_PATHS),
                               "context_tokens": "not_exposed_by_public_job_result",
                               "model_turns": lookup.get("model_turns_used", 0),
                               "active_seconds": "not_exposed_by_public_job_result",
                               "source_hashes": {s.get("path"): s.get("content_sha256")
                                                 for s in lookup.get("sources", [])},
                               "candidate_status": candidate.get("status"),
                               "tick_status": tick.get("status") if isinstance(tick, dict) else None}
                        report["automatic"].append(row)
                        with composition.jobs._db() as db:
                            reserved = db.execute("SELECT reserved_turns FROM pa1_attention_focus WHERE focus_id=?",
                                                  (focus_id,)).fetchone()
                        row["reserved_turns"] = int(reserved[0]) if reserved else None
                        if report["automatic_root_count"] > MAX_AUTO_ROOTS or not reserved or int(reserved[0]) > MAX_RESERVED_TURNS:
                            raise ValueError("automatic root or reserved-turn cap was exceeded")
                        return row
                last = {"status": tick.get("status") if isinstance(tick, dict) else "pending",
                        "focus": candidates}
                time.sleep(0.5)
            row = {"label": label, **last, "status": "timeout"}
            report["automatic"].append(row)
            return row

        # Initial foreground answer is the point-in-time baseline.
        first = foreground("baseline", 6, 300, False)
        if report["inquiry_count"] >= MAX_INQUIRIES:
            raise ValueError("inquiry budget exhausted before preparation")
        focus = operator.set_focus(project="economics", text="Track fixture budget and controller changes.",
            trusted_projects={"economics"}, trusted_root=repo, trusted_repository="fixture",
            source_paths=SOURCE_PATHS, automatic_seconds=MAX_WALL_SECONDS)
        focus_id = focus["focus_id"]
        _mutate_fixture(repo, 1)
        auto1 = await_preparation("automatic_preparation_1")
        if report["inquiry_count"] >= MAX_INQUIRIES:
            raise ValueError("inquiry budget exhausted after first preparation")
        foreground("prepared_foreground", 7, 240, True)
        _mutate_fixture(repo, 2)
        auto2 = await_preparation("automatic_refresh_2")
        foreground("refreshed_foreground", 8, 180, True)
        if report["inquiry_count"] < MAX_INQUIRIES and time.monotonic() < started + MAX_WALL_SECONDS:
            foreground("control_foreground", 8, 180, False)
        report["source_identity"] = {"project": "economics", "repository": "fixture",
            "root": str(repo), "git_revision": subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
            "selected_source_paths": list(SOURCE_PATHS),
            "foreign_env_denied": report["source_shadow_denied"]}
        report["comparison"] = _compare(report["foreground"])
        report["automatic_remains_disabled"] = True
        report["result"] = _economics_result(report, first, auto1, auto2)
        report["status"] = "completed" if report["inquiry_count"] == MAX_INQUIRIES else "partial"
        report["elapsed_seconds"] = round(time.monotonic() - started, 4)
        report["inference_performed"] = report["inquiry_count"] > 0
        return report
    except Exception as error:
        report.update({"status": "partial" if report["inference_performed"] else "preflight_failed",
                       "failure": {"class": type(error).__name__, "reason": str(error)[:300]},
                       "inference_performed": report["inference_performed"]})
        return report
    finally:
        # The private automatic grant is revoked even after a timeout or error.
        if operator is not None and focus_id:
            try:
                db = operator._open(create=True)
                try:
                    db.execute("BEGIN IMMEDIATE")
                    PowerPolicy(db).set_automatic(operator.control, False)
                    focus_state = db.execute("SELECT reserved_turns,active_seconds FROM pa1_attention_focus "
                                             "WHERE focus_id=?", (focus_id,)).fetchone()
                    db.execute("UPDATE pa1_attention_focus SET permit_automatic=0,state='expired',expires_at=?,updated=? "
                               "WHERE focus_id=?", (time.time(), time.time(), focus_id))
                    db.commit()
                    report["focus_budget_used"] = ({"reserved_turns": int(focus_state[0]),
                        "active_seconds": float(focus_state[1])} if focus_state else None)
                finally:
                    db.close()
                report["automatic_remains_disabled"] = True
            except Exception as error:
                report["cleanup_failure"] = type(error).__name__
        if composition is not None:
            try:
                with composition.jobs._db() as db:
                    rows = db.execute("SELECT session_id,state,close_receipt FROM pa1_owned_resource_sessions "
                                      "WHERE state <> 'superseded' ORDER BY updated,session_id").fetchall()
                report["owned_receipts"] = [{"session_id": row[0], "state": row[1],
                    "close_receipt": json.loads(row[2]) if row[2] else {}} for row in rows]
                report["cleanup"] = {"isolated_job_service_stopped": bool(composition.close()),
                                      "central_supervisor_stopped": False,
                                      "isolated_state_retained": True}
            except Exception as error:
                report["cleanup"] = {"isolated_job_service_stopped": False,
                                      "cleanup_error": type(error).__name__,
                                      "central_supervisor_stopped": False}
        attention_module.MAX_RESERVED_TURNS = original_turn_cap
        if previous_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = previous_xdg
        report["elapsed_seconds"] = round(time.monotonic() - started, 4)


def _compare(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_label = {row["label"]: row for row in rows}
    base = by_label.get("baseline")
    prepared = by_label.get("prepared_foreground")
    refreshed = by_label.get("refreshed_foreground")
    control = by_label.get("control_foreground")
    comparisons = []
    for label, item in (("prepared", prepared), ("refreshed", refreshed)):
        if base and item and isinstance(base.get("elapsed_seconds"), (int, float)):
            comparisons.append({"against": label,
                "elapsed_delta_seconds": round(item["elapsed_seconds"] - base["elapsed_seconds"], 4),
                "quality_equal": bool(item.get("quality_pass") == base.get("quality_pass")),
                "comparison_sample_size": 1})
    if control and base:
        comparisons.append({"against": "control_during_or_after_refresh",
            "elapsed_delta_seconds": round(control["elapsed_seconds"] - base["elapsed_seconds"], 4),
            "quality_equal": bool(control.get("quality_pass") == base.get("quality_pass")),
            "comparison_sample_size": 1})
    return {"comparisons": comparisons, "sample_size_per_condition": 1,
            "confidence_or_spread": "not_measurable_with_single_observation"}


def _economics_result(report: dict[str, Any], baseline: dict[str, Any],
                      auto1: dict[str, Any], auto2: dict[str, Any]) -> dict[str, Any]:
    comparisons = report.get("comparison", {}).get("comparisons", [])
    measured = len(comparisons) >= 2 and all(isinstance(row.get("elapsed_delta_seconds"), (int, float))
                                              for row in comparisons)
    max_delta = max((row["elapsed_delta_seconds"] for row in comparisons), default=None)
    quality_ok = all(row.get("quality_equal") for row in comparisons) if comparisons else False
    wait_ok = all(row.get("status") in {"completed", "partial"} for row in (auto1, auto2))
    pass_all = bool(measured and wait_ok and quality_ok and max_delta is not None and
                    max_delta <= 2.0 and report.get("elapsed_seconds", 0) <= MAX_WALL_SECONDS)
    return {"thresholds_measured": measured, "max_added_wait_seconds": 2.0,
            "observed_max_added_wait_seconds": max_delta,
            "added_wait_pass": bool(max_delta is not None and max_delta <= 2.0),
            "elapsed_overhead_fraction": "not_comparable_without_repeated_control_samples",
            "quality_degradation": not quality_ok, "automatic_root_completion_pass": wait_ok,
            "all_thresholds_passed": pass_all,
            "automatic_permanently_enabled": False,
            "decision": "insufficient_evidence_keep_automatic_off"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-live", action="store_true",
                        help="run bounded live inquiries through the verified existing supervisor")
    parser.add_argument("--artifact-root", type=Path,
                        default=Path.home() / ".local/state/project-control/assistance-economics")
    args = parser.parse_args(argv)
    report = plan()
    if not args.execute_live:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    try:
        root = _private_dir(args.artifact_root)
        if any(root.iterdir()):
            raise ValueError("artifact root must be empty; use a fresh private directory")
        report = _execute(root)
        _write_report(root, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("status") == "completed" else 2
    except Exception as error:
        report.update({"status": "preflight_failed", "inference_performed": False,
                       "failure": {"class": type(error).__name__, "reason": str(error)[:300]}})
        try:
            root = _private_dir(args.artifact_root)
            _write_report(root, report)
        except Exception:
            pass
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
