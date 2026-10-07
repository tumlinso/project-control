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

from scripts import qualification_runtime


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


def test_declared_runtime_defaults_to_strict_release():
    args = qualifier._parser().parse_args([
        "--project", "fixture", "--question", "q", "--skill", "cuda",
        "--skill-question", "q"])
    assert args.runtime == "release"
    source_args = qualifier._parser().parse_args([
        "--runtime", "source", "--project", "fixture", "--question", "q",
        "--skill", "cuda", "--skill-question", "q"])
    assert source_args.runtime == "source"


def test_live_and_dry_run_flags_cannot_be_combined(monkeypatch):
    monkeypatch.setattr(qualifier, "_runtime_provenance",
                        lambda *args, **kwargs: pytest.fail("runtime inspection must not run"))
    with pytest.raises(SystemExit) as raised:
        qualifier.main([
            "--runtime", "source", "--dry-run", "--execute-live",
            "--project", "fixture", "--question", "q", "--skill", "cuda",
            "--skill-question", "q", "--out-dir", "/tmp/never-created"])
    assert raised.value.code == 2


def test_source_status_identity_uses_receiver_and_demand_todo_stamps():
    expected = {"mode": "source", "source_root": "/checkout",
                "project_control_fingerprint": "e" * 64,
                "receiver_fingerprint": "a" * 64,
                "todo_runtime_fingerprint": "b" * 64}
    status = {"runtime": {"readiness": "verified_ready", "release_digest": None,
        "runtime_mode": "source", "source_root": "/checkout",
        "project_control_fingerprint": "e" * 64,
        "receiver_fingerprint": expected["receiver_fingerprint"],
        "todo_runtime_fingerprint": expected["todo_runtime_fingerprint"],
        "supervisor_pid": 123, "supervisor_process_start": "456",
        "daemon_epoch": "c" * 64}}
    observed = qualifier._status_identity(
        status, runtime_mode="source", expected_source=expected)
    assert observed["release_digest"] is None
    assert observed["receiver_fingerprint"] == expected["receiver_fingerprint"]
    assert observed["todo_runtime_fingerprint"] == expected["todo_runtime_fingerprint"]
    assert observed["project_control_fingerprint"] == expected["project_control_fingerprint"]
    status["runtime"]["todo_runtime_fingerprint"] = "d" * 64
    with pytest.raises(qualifier.QualificationError, match="differs from checked-out demand pin"):
        qualifier._status_identity(status, runtime_mode="source", expected_source=expected)


def test_source_start_receipts_are_checked_against_demand_pin(tmp_path):
    demand = {"mode": "source", "source_root": str(tmp_path),
              "project_control_fingerprint": "e" * 64,
              "receiver_fingerprint": "a" * 64,
              "todo_runtime_fingerprint": "b" * 64}
    receipt = {"status": "ready", "release_digest": None,
               "runtime_mode": "source", "source_root": demand["source_root"],
               "project_control_fingerprint": demand["project_control_fingerprint"],
               "receiver_fingerprint": demand["receiver_fingerprint"],
               "todo_runtime_fingerprint": demand["todo_runtime_fingerprint"],
               "supervisor_pid": 123, "supervisor_process_start": "456",
               "daemon_epoch": "c" * 64}
    driver = object.__new__(qualifier.InstalledQualification)
    driver.runtime = "source"
    driver.provenance = {"demand_runtime": demand}
    driver.release_digest = ""
    driver.cli_command = ["/checkout/scripts/pc-dev", "run"]
    driver.runtime_root = tmp_path
    driver.events = []
    driver.clock = lambda: 1.0
    driver._remaining = lambda limit=30: limit
    driver.runner = lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, json.dumps(receipt), "")
    assert driver._start_concurrently() == {
        "runtime_mode": "source", "source_root": demand["source_root"],
        "project_control_fingerprint": demand["project_control_fingerprint"],
        "release_digest": None, "receiver_fingerprint": demand["receiver_fingerprint"],
        "todo_runtime_fingerprint": demand["todo_runtime_fingerprint"], "supervisor_pid": 123,
        "supervisor_process_start": "456", "daemon_epoch": "c" * 64}


def test_source_skill_helper_clears_release_pins_and_checks_checkout_path(
        tmp_path, monkeypatch, capsys):
    root = tmp_path / "checkout"
    package = root / "src/project_control"
    package.mkdir(parents=True)
    module_file = package / "__init__.py"
    module_file.write_text("", encoding="utf-8")
    for name in qualification_runtime._SOURCE_PINS:
        monkeypatch.setenv(name, "/stale/pin")

    class Jobs:
        def shutdown(self, timeout):
            return True

    class Skills:
        def inquire(self, **kwargs):
            return {"status": "ok"}

    class Composition:
        jobs = Jobs()
        skills = Skills()

        def start(self):
            pass

        def scope(self, project):
            return {"project": project}

    fake_project_control = types.ModuleType("project_control")
    fake_project_control.__file__ = str(module_file)
    fake_cli = types.ModuleType("project_control.cli")
    fake_cli._assistance_composition = lambda project: (None, Composition())
    fake_binding = types.ModuleType("project_control.runtime_binding")
    fake_binding.RELEASE_DIGEST_VARIABLE = "PROJECT_CONTROL_RELEASE_DIGEST"
    fake_binding.RELEASE_MANIFEST_VARIABLE = "PROJECT_CONTROL_RELEASE_MANIFEST"
    original_import = builtins.__import__

    def import_hook(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "project_control":
            return fake_project_control
        if name == "project_control.cli":
            return fake_cli
        if name == "project_control.runtime_binding":
            return fake_binding
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(sys, "argv", ["skill-helper", "source", "fixture", "cuda",
                                      "question", str(root), ""])
    namespace = {"__builtins__": {**builtins.__dict__, "__import__": import_hook}}
    exec(qualifier.SKILL_HELPER, namespace)
    assert all(name not in __import__("os").environ
               for name in qualification_runtime._SOURCE_PINS)
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


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

    monkeypatch.setattr(sys, "argv", ["skill-helper", "release", "fixture", "cuda", "same query",
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


def test_source_provenance_uses_checkout_packages_dynamic_receiver_and_pc_dev():
    root = SCRIPT.parents[1]
    value = qualification_runtime.runtime_provenance("source", repository_root=root)
    assert value["runtime"] == "source"
    assert value["git"]["head"]
    assert isinstance(value["git"]["dirty"], bool)
    assert value["interpreter"]["resolved"] == str(Path(sys.executable).resolve())
    assert value["interpreter"]["prefix"] == str(Path(sys.prefix).resolve())
    assert value["cli_command"] == [str(root / "scripts/pc-dev"), "run"]
    assert Path(value["project_control"]["path"]).is_relative_to(root / "src")
    assert Path(value["todo"]["path"]).is_relative_to(root / "src")
    assert len(value["project_control"]["fingerprint"]) == 64
    assert len(value["todo"]["fingerprint"]) == 64
    assert value["receiver"]["file_count"] == len(value["receiver"]["files"])
    assert len(value["receiver"]["fingerprint"]) == 64


def test_source_provenance_refuses_foreign_imported_packages(monkeypatch, tmp_path):
    foreign_pc = types.ModuleType("project_control")
    pc_file = tmp_path / "foreign/project_control/__init__.py"
    pc_file.parent.mkdir(parents=True)
    pc_file.touch()
    foreign_pc.__file__ = str(pc_file)
    foreign_todo = types.ModuleType("todo_orchestrator")
    todo_file = tmp_path / "foreign/todo_orchestrator/__init__.py"
    todo_file.parent.mkdir(parents=True)
    todo_file.touch()
    foreign_todo.__file__ = str(todo_file)
    monkeypatch.setitem(sys.modules, "project_control", foreign_pc)
    monkeypatch.setitem(sys.modules, "todo_orchestrator", foreign_todo)
    with pytest.raises(qualification_runtime.QualificationRuntimeError,
                       match="outside this checkout"):
        qualification_runtime.runtime_provenance(
            "source", repository_root=SCRIPT.parents[1])


def test_source_provenance_rejects_python_outside_pc_dev_environment(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3.12")
    monkeypatch.setattr(sys, "prefix", "/usr")
    with pytest.raises(qualification_runtime.QualificationRuntimeError,
                       match="differs from the checked-in pc-dev environment"):
        qualification_runtime.runtime_provenance(
            "source", repository_root=SCRIPT.parents[1])


def test_source_provenance_clears_inherited_release_pins():
    environment = {
        "PROJECT_CONTROL_RELEASE_MANIFEST": "/retired/manifest.json",
        "PROJECT_CONTROL_RELEASE_DIGEST": "f" * 64,
        "PROJECT_CONTROL_TODO_RUNTIME_FINGERPRINT": "e" * 64,
        "PROJECT_CONTROL_RUNTIME_PYTHON": "/retired/python",
    }
    qualification_runtime.clear_source_pins(environment)
    assert environment == {}


def test_release_provenance_requires_digest_pinned_runtime(monkeypatch):
    for name in qualification_runtime._SOURCE_PINS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(qualification_runtime.QualificationRuntimeError,
                       match="release runtime pins are missing"):
        qualification_runtime.runtime_provenance(
            "release", repository_root=SCRIPT.parents[1])


def test_release_provenance_rejects_manifest_digest_mismatch(monkeypatch, tmp_path):
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text('{"schema_version":3}', encoding="utf-8")
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_MANIFEST", str(manifest))
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_DIGEST", "0" * 64)
    with pytest.raises(qualification_runtime.QualificationRuntimeError,
                       match="manifest digest mismatch"):
        qualification_runtime.runtime_provenance(
            "release", repository_root=SCRIPT.parents[1])


def test_release_schema_three_requires_pc_code_fingerprint(monkeypatch, tmp_path):
    manifest = tmp_path / "release-manifest.json"
    raw = b'{"schema_version":3}'
    manifest.write_bytes(raw)
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_MANIFEST", str(manifest))
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_DIGEST", qualifier._sha256(raw))
    with pytest.raises(qualification_runtime.QualificationRuntimeError,
                       match="Project Control fingerprint is missing"):
        qualification_runtime.runtime_provenance(
            "release", repository_root=SCRIPT.parents[1])


def test_source_dry_run_clears_stale_pins_and_reports_checkout(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_MANIFEST", "/retired/manifest.json")
    monkeypatch.setenv("PROJECT_CONTROL_RELEASE_DIGEST", "f" * 64)
    result = qualifier.main([
        "--runtime", "source", "--dry-run", "--project", "fixture",
        "--question", "Question", "--skill", "cuda", "--skill-question", "Question",
    ])
    assert result == 0
    assert "PROJECT_CONTROL_RELEASE_MANIFEST" not in __import__("os").environ
    output = json.loads(capsys.readouterr().out)
    assert output["runtime_provenance"]["runtime"] == "source"
    assert output["runtime_provenance"]["cli_command"] == [
        str(SCRIPT.parents[1] / "scripts/pc-dev"), "run"]
