from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/qualify_assistance_economics.py"
SPEC = importlib.util.spec_from_file_location("qualify_assistance_economics", SCRIPT)
economics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(economics)


def test_default_invocation_is_a_bounded_inert_plan(capsys):
    assert economics.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "planned_not_executed"
    assert output["inference_performed"] is False
    assert output["budget"] == {
        "max_new_inquiries": 6, "foreground_inquiries": 4,
        "automatic_roots": 2, "max_reserved_turns": 12, "wall_seconds": 120,
    }
    assert output["automatic_after_run"] == "disabled"


def test_source_guard_rejects_dotenv_and_unregistered_paths_before_reading():
    assert economics._validate_source_paths(("demo/budgets.py", "demo/controller.py")) == (
        "demo/budgets.py", "demo/controller.py")
    for paths in ((".env",), ("demo/budgets.py", ".env"), ("README.md",),
                  ("../outside.py",), ("demo/budgets.py", "demo/budgets.py")):
        with pytest.raises(ValueError):
            economics._validate_source_paths(paths)


def test_execute_live_requires_the_exact_existing_supervisor_state_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", str(tmp_path / "wrong"))
    with pytest.raises(ValueError, match="verified central observer state"):
        economics._execute(tmp_path / "artifacts")
    assert not (tmp_path / "artifacts").exists()


def test_real_runtime_config_composes_observer_with_stub_backend(monkeypatch, tmp_path):
    from project_control.app import Runtime
    from project_control.as1_surface import compose_surface
    from project_control.config import load_config
    from project_control.profiles import MCPProfile

    repo = economics._disposable_repository(tmp_path)
    original = load_config()
    config_home = economics._private_config(repo, tmp_path, original)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    class StubBackend:
        available = True

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    backend = StubBackend()
    runtime = Runtime(load_config())
    assert runtime.config.workspaces["economics"].repositories["fixture"].root == repo
    composition = compose_surface(runtime, MCPProfile.OBSERVER,
                                  state_directory=tmp_path / "isolated-state",
                                  backend=backend)
    try:
        assert composition.scope("economics")["project"] == "economics"
        assert composition.jobs.worker_factory is not None
        assert not backend.closed
    finally:
        assert composition.close() is True
    assert backend.closed


def test_economic_gate_keeps_automatic_off_when_latency_or_quality_is_unmeasured():
    report = {"comparison": {"comparisons": []}, "elapsed_seconds": 9.0}
    result = economics._economics_result(
        report, {"status": "completed"}, {"status": "completed"}, {"status": "completed"})
    assert result["thresholds_measured"] is False
    assert result["all_thresholds_passed"] is False
    assert result["automatic_permanently_enabled"] is False
    assert result["decision"] == "insufficient_evidence_keep_automatic_off"


def test_economic_gate_fails_on_degraded_quality_or_over_budget_wait():
    report = {"comparison": {"comparisons": [
        {"elapsed_delta_seconds": 2.1, "quality_equal": False},
        {"elapsed_delta_seconds": 1.0, "quality_equal": True},
    ]}, "elapsed_seconds": 50.0}
    result = economics._economics_result(
        report, {"status": "completed"}, {"status": "completed"}, {"status": "completed"})
    assert result["added_wait_pass"] is False
    assert result["quality_degradation"] is True
    assert result["all_thresholds_passed"] is False
    assert result["automatic_permanently_enabled"] is False
