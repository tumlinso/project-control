from __future__ import annotations

import importlib.util
import builtins
import io
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qualify_assistance_installed.py"
SPEC = importlib.util.spec_from_file_location("qualify_assistance_installed", SCRIPT)
qualifier = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = qualifier
SPEC.loader.exec_module(qualifier)


def test_default_invocation_is_inert_and_does_not_create_artifacts(tmp_path, capsys):
    output = tmp_path / "new-run"
    result = qualifier.main([
        "--project", "fixture", "--question", "Where is the baseline?",
        "--skill", "cuda", "--skill-question", "Explain the registered workflow.",
        "--out-dir", str(output),
    ])
    assert result == 2
    assert not output.exists()
    assert json.loads(capsys.readouterr().out) == {
        "status": "inert", "required_flag": "--execute-live"}


def test_private_artifact_directory_is_created_once_and_reuse_is_rejected(tmp_path):
    output = qualifier._private_new_directory(tmp_path / "run-1")
    assert output.stat().st_mode & 0o777 == 0o700
    with pytest.raises(qualifier.QualificationError, match="must be new"):
        qualifier._private_new_directory(output)


def test_status_identity_requires_all_server_and_release_fields():
    identity = {
        "release_digest": "a" * 64,
        "receiver_fingerprint": "b" * 64,
        "todo_runtime_fingerprint": "c" * 64,
        "supervisor_pid": 99,
        "supervisor_process_start": "12345",
        "daemon_epoch": "d" * 64,
    }
    value = {"runtime": {"readiness": "verified_ready", **identity}}
    assert qualifier._status_identity(value, "a" * 64) == identity
    value["runtime"].pop("daemon_epoch")
    with pytest.raises(qualifier.QualificationError, match="incomplete"):
        qualifier._status_identity(value)


def test_cold_gate_rejects_any_owned_slot_even_when_queue_is_empty():
    status = {"work": {"active_work": [], "active_execution_slots": [
        {"job_id": "job-1", "cleanup_pending": True}]}}
    assert not qualifier._work_is_idle(status)
    assert qualifier._work_is_idle({"work": {"active_work": [],
        "active_execution_slots": [], "owned_resources": {"active_sessions": 0}}})


def test_private_artifact_parent_symlink_is_resolved_before_creation(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    output = qualifier._private_new_directory(alias / "run")
    assert output.parent == parent.resolve()


def test_direct_role_helper_is_isolated_and_total_model_turn_ceiling_is_explicit():
    compile(qualifier.ROLE_HELPER, "<installed-role-helper>", "exec")
    assert "sys.path.insert" not in qualifier.ROLE_HELPER
    assert "RELEASE_DIGEST_VARIABLE" in qualifier.ROLE_HELPER
    assert qualifier.MAX_INQUIRY_SUBMISSIONS == 2
    assert qualifier.MAX_TURNS_PER_SUBMISSION == 6
    assert qualifier.MAX_DIRECT_ROLE_TURNS == 2
    potential_turns = (qualifier.MAX_INQUIRY_SUBMISSIONS
                       * qualifier.MAX_TURNS_PER_SUBMISSION
                       + qualifier.MAX_DIRECT_ROLE_TURNS)
    assert potential_turns == 14


def test_identical_public_question_is_repolled_until_terminal_without_changing_argv(tmp_path):
    now = [0.0]
    calls = []
    timeouts = []
    outcomes = [
        {"status": "thinking"},
        {"status": "thinking"},
        {"status": "completed", "answer": "The baseline is documented."},
    ]

    def runner(args, **kwargs):
        calls.append(list(args))
        timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, json.dumps(outcomes.pop(0)), "")

    driver = object.__new__(qualifier.InstalledQualification)
    driver.cli = tmp_path / "project-control"
    driver.events = []
    driver.clock = lambda: now[0]
    driver.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    driver.deadline = 600.0
    driver.runner = runner
    driver.release_root = tmp_path
    args = [str(driver.cli), "assistance", "ask", "same question", "--project", "fixture"]

    result = driver._poll_identical_question(args, "source_grounded_question")

    assert result["status"] == "completed"
    assert result["qualification_poll_count"] == 3
    assert calls == [args, args, args]
    assert timeouts[0] == 420
    assert all(0 < timeout <= 420 for timeout in timeouts)
    assert qualifier.MAX_INQUIRY_SUBMISSIONS == 2
    assert qualifier.MAX_INQUIRY_POLLS == 300


def test_polling_stops_at_work_deadline_and_preserves_cleanup_reserve(tmp_path):
    now = [0.0]
    calls = []

    def runner(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, '{"status":"thinking"}', "")

    driver = object.__new__(qualifier.InstalledQualification)
    driver.cli = tmp_path / "project-control"
    driver.events = []
    driver.clock = lambda: now[0]
    driver.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    driver.deadline = 125.0
    driver.runner = runner
    driver.release_root = tmp_path
    with pytest.raises(qualifier.QualificationError, match="bounded polling limit"):
        driver._poll_identical_question([str(driver.cli), "assistance", "ask", "q"], "ask")

    assert len(calls) == 5
    # Inquiry work stops at 5 seconds, while the separate 120-second cleanup
    # reserve remains available for the supported stop and final status call.
    now[0] = 5.0
    assert driver._remaining(120, cleanup=True) == 120
    with pytest.raises(qualifier.QualificationError, match="work budget exhausted"):
        driver._remaining()


def test_inquiry_reason_is_retained_and_missing_procfs_pid_fails_closed(tmp_path):
    record = qualifier.InstalledQualification._inquiry_record(
        object.__new__(qualifier.InstalledQualification), "ask",
        {"status": "unavailable", "reason": "analysis_unavailable"})
    assert record["reason"] == "analysis_unavailable"

    assert qualifier._procfs_start_time(999999, tmp_path) is None
    assert qualifier._procfs_start_time(None, tmp_path) is None

    proc_root = tmp_path / "proc"
    process = proc_root / "4242"
    process.mkdir(parents=True)
    fields = ["S", *(["1"] * 18), "98765"]
    (process / "stat").write_text(f"4242 (server worker (model)) {' '.join(fields)}\n",
                                  encoding="ascii")
    assert qualifier._procfs_start_time(4242, proc_root) == "98765"


def test_skill_helper_keeps_one_composition_alive_while_polling_and_joins_boundedly():
    compile(qualifier.SKILL_HELPER, "<installed-skill-helper>", "exec")
    assert "while polls < 300" in qualifier.SKILL_HELPER
    assert "composition.skills.inquire(access_scope=scope, query=query, skill=skill)" in qualifier.SKILL_HELPER
    assert "shutdown(timeout=120)" in qualifier.SKILL_HELPER
    assert "shutdown(timeout=2)" not in qualifier.SKILL_HELPER


def test_skill_helper_repeats_the_same_query_inside_one_live_composition(tmp_path, monkeypatch, capsys):
    root = tmp_path / "release"
    root.mkdir()
    manifest = root / "release.json"
    manifest.write_text("{}", encoding="utf-8")
    digest = qualifier._sha256(manifest.read_bytes())
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_MANIFEST", str(manifest))
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_DIGEST", digest)

    calls = []
    shutdown_timeouts = []
    values = iter([{"status": "thinking"}, {"status": "ok", "resources": ["excerpt"]}])

    class Jobs:
        def shutdown(self, timeout):
            shutdown_timeouts.append(timeout)
            return True

    class Skills:
        def inquire(self, **kwargs):
            calls.append(kwargs.copy())
            return next(values)

    class Composition:
        jobs = Jobs()
        skills = Skills()

        def start(self):
            pass

        def scope(self, project):
            return {"project": project}

    composition = Composition()
    fake_project_control = types.ModuleType("project_control")
    fake_project_control.__file__ = str(manifest)
    fake_cli = types.ModuleType("project_control.cli")
    fake_cli._assistance_composition = lambda project: (None, composition)
    fake_binding = types.ModuleType("project_control.runtime_binding")
    fake_binding.RELEASE_DIGEST_VARIABLE = "PROJECT_CONTROL_RELEASE_DIGEST"
    fake_binding.RELEASE_MANIFEST_VARIABLE = "PROJECT_CONTROL_RELEASE_MANIFEST"
    fake_time = types.SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda seconds: None)
    original_import = builtins.__import__

    def import_hook(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "time":
            return fake_time
        if name == "project_control":
            return fake_project_control
        if name == "project_control.cli":
            return fake_cli
        if name == "project_control.runtime_binding":
            return fake_binding
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(sys, "argv", ["skill-helper", "fixture", "cuda", "same query",
                                      str(root), digest])
    namespace = {"__builtins__": {**builtins.__dict__, "__import__": import_hook}}
    exec(qualifier.SKILL_HELPER, namespace)

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["qualification_poll_count"] == 2
    assert len(calls) == 2
    assert calls[1] == calls[0]
    assert calls[0] == {"access_scope": {"project": "fixture"},
                        "query": "same query", "skill": "cuda"}
    assert shutdown_timeouts == [120]


def test_role_helper_requires_procfs_start_and_equal_owner_descriptors():
    compile(qualifier.ROLE_HELPER, "<installed-role-helper>", "exec")
    assert '"server_process_start_method": "procfs:/proc/<pid>/stat:starttime"' in qualifier.ROLE_HELPER
    assert 'after_by_id[item["slot_id"]].get("owner_id") == item.get("owner_descriptor")' in qualifier.ROLE_HELPER
    assert 'and isinstance(item.get("owner_descriptor"), str) and item.get("owner_descriptor")' in qualifier.ROLE_HELPER
    assert '"reason": response.get("reason")' in qualifier.ROLE_HELPER
