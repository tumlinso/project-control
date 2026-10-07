from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_attempt_cap_is_operator_bounded_and_reported(capsys):
    assert economics.main(["--max-inquiries", "5", "--wall-seconds", "79"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "planned_not_executed"
    assert output["budget"]["max_new_inquiries"] == 5
    assert output["budget"]["wall_seconds"] == 79
    with pytest.raises(SystemExit):
        economics.main(["--max-inquiries", "7"])
    with pytest.raises(SystemExit):
        economics.main(["--wall-seconds", "121"])


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


def test_ephemeral_fixture_alias_preserves_existing_workspace_authority(tmp_path):
    from project_control.config import load_config

    original = load_config()
    assert economics.SEMANTIC_PROJECT in original.workspaces
    original_authority = original.workspaces[economics.SEMANTIC_PROJECT].authority_repository
    original_repositories = set(original.workspaces[economics.SEMANTIC_PROJECT].repositories)
    repo = economics._disposable_repository(tmp_path)
    config = economics._ephemeral_fixture_config(original, repo)
    workspace = config.workspaces[economics.SEMANTIC_PROJECT]
    assert workspace.authority_repository == original_authority
    assert workspace.repositories[original_authority].root == original.workspaces[
        economics.SEMANTIC_PROJECT].repositories[original_authority].root
    assert set(workspace.repositories) == original_repositories | {economics.FIXTURE_REPOSITORY_ALIAS}
    assert economics.FIXTURE_REPOSITORY_ALIAS not in original.workspaces[
        economics.SEMANTIC_PROJECT].repositories


def test_real_runtime_reads_fresh_fixture_sources_under_existing_authority(tmp_path):
    from project_control.app import Runtime
    from project_control.as1_surface import compose_surface
    from project_control.config import load_config
    from project_control.profiles import MCPProfile

    original = load_config()
    original_authority = original.workspaces[economics.SEMANTIC_PROJECT].authority_repository
    repo = economics._disposable_repository(tmp_path)
    config = economics._ephemeral_fixture_config(original, repo)
    assert config.workspaces[economics.SEMANTIC_PROJECT].authority_repository == original_authority
    assert economics.FIXTURE_REPOSITORY_ALIAS in config.workspaces[economics.SEMANTIC_PROJECT].repositories
    assert economics.FIXTURE_REPOSITORY_ALIAS not in original.workspaces[economics.SEMANTIC_PROJECT].repositories

    class StubBackend:
        available = True

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    backend = StubBackend()
    runtime = Runtime(config)
    composition = compose_surface(runtime, MCPProfile.OBSERVER,
                                  state_directory=tmp_path / "isolated-state",
                                  backend=backend)
    try:
        scope = composition.scope(economics.SEMANTIC_PROJECT)
        assert scope["project"] == economics.SEMANTIC_PROJECT
        assert composition.jobs.worker_factory is not None
        assert not backend.closed
        packet_ids, rows, freshness = economics._read_public_sources(
            composition, repo, scope)
        assert len(packet_ids) == 2
        assert {row["path"] for row in rows} == set(economics.SOURCE_PATHS)
        assert freshness["fresh"] is True
        packets = [composition.store.lookup(packet_id, access_scope=scope).packet
                   for packet_id in packet_ids]
        assert all(packet is not None for packet in packets)
        assert all(source.project == economics.SEMANTIC_PROJECT and
                   source.repository == economics.FIXTURE_REPOSITORY_ALIAS
                   for packet in packets for source in packet.sources)
        assert all(source.path != ".env" for packet in packets for source in packet.sources)
    finally:
        assert composition.close() is True
    assert backend.closed


def test_economic_gate_keeps_automatic_off_when_latency_or_quality_is_unmeasured():
    report = {"comparison": {"comparisons": []}, "elapsed_seconds": 9.0}
    result = economics._economics_result(
        report, {"status": "completed"}, {"status": "completed"}, {"status": "completed"})
    assert result["thresholds_measured"] is False
    assert result["elapsed_overhead_threshold_measured"] is False
    assert result["elapsed_overhead_pass"] is False
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


def test_low_latency_delta_cannot_pass_without_measured_elapsed_overhead():
    report = {"comparison": {"comparisons": [
        {"elapsed_delta_seconds": 0.1, "quality_equal": True},
        {"elapsed_delta_seconds": 0.2, "quality_equal": True},
    ]}, "elapsed_seconds": 10.0}
    result = economics._economics_result(
        report, {"status": "completed"}, {"status": "completed"}, {"status": "completed"})
    assert result["latency_thresholds_measured"] is True
    assert result["thresholds_measured"] is False
    assert result["all_thresholds_passed"] is False


def test_execute_stubbed_full_path_uses_real_focus_policy_and_counts_thinking_attempts(monkeypatch, tmp_path):
    import sqlite3
    import subprocess
    import time

    import project_control.app as app_module
    import project_control.as1_surface as surface_module
    import project_control.assistance.attention as attention_module
    from project_control.assistance.attention import AttentionController as RealAttentionController
    import project_control.observer_analysis as observer_module
    import project_control.runtime_binding as binding_module

    monkeypatch.setenv("PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR", str(economics.CENTRAL_STATE))

    class DB:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS inquiry_index(identity TEXT PRIMARY KEY, job TEXT NOT NULL)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS pa1_owned_resource_sessions(session_id TEXT,state TEXT,close_receipt TEXT,updated REAL)")
            return self.connection
        def __exit__(self, *_args):
            self.connection.commit()
            self.connection.close()
            return False

    class StubStore:
        def __init__(self):
            self.packets = {}
            self.counter = 0
        def add(self, project, repository, relative, data):
            self.counter += 1
            packet_id = f"pkt_{self.counter}"
            source = SimpleNamespace(project=project, repository=repository, path=relative,
                                     content_sha256=economics._hash(data))
            packet = SimpleNamespace(packet_id=packet_id, alias=packet_id,
                                     sources=[source], payload={"text": data.decode()})
            self.packets[packet_id] = packet
            return packet_id
        def lookup(self, packet_id, access_scope):
            packet = self.packets.get(packet_id)
            return SimpleNamespace(status="ok" if packet else "unavailable", packet=packet)
        def create(self, *, tool, access_scope, payload, sources, ttl_seconds):
            self.counter += 1
            packet_id = f"pkt_{self.counter}"
            normalized = [item if hasattr(item, "project") else SimpleNamespace(**item) for item in sources]
            packet = SimpleNamespace(packet_id=packet_id, alias=packet_id, sources=normalized, payload=payload)
            self.packets[packet_id] = packet
            return packet

    class StubJobs:
        analysis_runtime_identity = "stub-runtime"
        def __init__(self, composition):
            self.composition = composition
            self.clock = time.time
            self.freshness_provider = lambda _probe: {"fresh": True, "changed_sources": []}
            self.responses = {}
            self.foreground_count = 0
            self.preparation_lookup_scopes = []
        def _db(self):
            return DB(self.composition.db_path)
        def inquiry_context(self, scope, _identity):
            return {"scope": scope, "hints": []}
        def start(self):
            self.composition.jobs_started = True
        def inquire(self, question, *, access_scope, hints, request_id, foreground_timeout):
            from project_control.as1_contracts import canonical_digest
            identity = canonical_digest({"question": question,
                "context": self.inquiry_context(access_scope, self.analysis_runtime_identity),
                "mode": "investigate", "skill": None})
            if request_id not in self.responses:
                persisted = json.loads((self.composition.artifact_root / "report.json").read_text())
                assert persisted["inquiry_count"] >= 1
                assert any(item["kind"] == "foreground" and item["request_sha256"] == economics._hash(
                    question.encode()) for item in persisted["attempts"])
                self.foreground_count += 1
                job_id = f"foreground-{self.foreground_count}"
                with self._db() as db:
                    db.execute("INSERT INTO jobs(id) VALUES(?)", (job_id,))
                    db.execute("INSERT INTO inquiry_index(identity,job) VALUES(?,?)", (identity, job_id))
                self.responses[request_id] = {"job_id": job_id, "status": "running", "answer": ""}
                return {"status": "thinking"}  # Deliberately content-free, with no job dict.
            job = self.responses[request_id]
            text = (self.composition.repo / "demo/budgets.py").read_text()
            import re
            steps = re.search(r"MAX_STEPS\s*=\s*(\d+)", text).group(1)
            seconds = re.search(r"INQUIRY_SECONDS\s*=\s*(\d+)", text).group(1)
            job.update(status="completed", answer=f"MAX_STEPS = {steps}; INQUIRY_SECONDS = {seconds}; controller.py uses them.")
            return {"status": "completed", "job": dict(job)}
        def lookup(self, job_id, *, access_scope):
            job = next((row for row in self.responses.values() if row["job_id"] == job_id), None)
            return {"status": "ok", "job": dict(job)} if job else {"status": "unavailable"}
        def preparation_lookup(self, job_id, *, access_scope):
            self.preparation_lookup_scopes.append(dict(access_scope))
            return {"status": "completed", "model_turns_used": 6,
                "sources": [{"path": path, "content_sha256": economics._hash(
                    (self.composition.repo / path).read_bytes())} for path in economics.SOURCE_PATHS]}
        def shutdown(self):
            self.composition.jobs_started = False
            return True

    class StubBackend:
        def central_status(self, *, deadline_epoch):
            return {"supervisor_pid": 123, "runtime_fingerprint": "stub-runtime",
                    "source_sha256": "stub-source", "service_state_root": str(economics.CENTRAL_STATE)}
        def close(self):
            self.composition.backend_closed = True

    class StubComposition:
        def __init__(self, runtime, backend, state_directory):
            self.repo = runtime.config.workspaces[economics.SEMANTIC_PROJECT].repositories[
                economics.FIXTURE_REPOSITORY_ALIAS].root
            self.db_path = Path(state_directory) / "jobs-v2/jobs.sqlite3"
            self.artifact_root = Path(state_directory).parent
            self.backend = backend
            backend.composition = self
            self.host = SimpleNamespace(projects=frozenset(runtime.config.workspaces))
            self.store = StubStore()
            self.jobs = StubJobs(self)
            self.jobs_started = False
            self.backend_closed = False
            self.focus_id = None
            self.auto_roots = 0
            self.candidate = None
            self.last_revision = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
            class Information:
                def call(inner, tool, *, project, repository, paths, detail):
                    assert tool == "read" and project == economics.SEMANTIC_PROJECT
                    assert repository == economics.FIXTURE_REPOSITORY_ALIAS
                    assert len(paths) == 1 and paths[0] in economics.SOURCE_PATHS
                    packet = self.store.add(project, repository, paths[0], (self.repo / paths[0]).read_bytes())
                    return {"status": "ok", "packet": packet}
            self.information = Information()
        def scope(self, project):
            return {"principal": "test", "profile": "observer", "project": project}
        def close(self):
            self.jobs.shutdown()
            self.backend.close()
            return True

    class StubAttentionController(RealAttentionController):
        def __init__(self, db, *, power_policy, trusted_roots, repository_for, access_scope,
                     broker, notebook, clock):
            super().__init__(db, power_policy=power_policy, trusted_roots=trusted_roots,
                repository_for=repository_for, access_scope=access_scope, broker=broker,
                notebook=notebook, clock=clock)
            self.policy, self.clock = power_policy, clock
        def active_focuses(self, *, permit_automatic):
            state = self.policy.snapshot()
            if not state["automatic_enabled"]:
                return []
            return [{"focus_id": state["automatic_focus"], "project": economics.SEMANTIC_PROJECT}]
        def candidates(self):
            rows = self.db.execute("SELECT candidate_id,job_id,focus_id,status FROM pa1_attention_candidates").fetchall()
            return [SimpleNamespace(**dict(row)) for row in rows]
        def reconcile_admission(self, candidate_id):
            return {"status": "completed"}
        def scan(self, focus_id):
            revision = subprocess.check_output(["git", "-C", str(composition.repo), "rev-parse", "HEAD"], text=True).strip()
            if revision != composition.last_revision:
                composition.last_revision = revision
                composition.auto_roots += 1
                candidate_id = f"candidate-{composition.auto_roots}"
                job_id = f"automatic-{composition.auto_roots}"
                composition.candidate = {"candidate_id": candidate_id, "job_id": job_id,
                    "focus_id": focus_id, "status": "dispatched"}
                self.db.execute("INSERT INTO pa1_attention_candidates(candidate_id,focus_id,project,paths,fingerprint,changed_at,stable_after,status,job_id,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (candidate_id, focus_id, economics.SEMANTIC_PROJECT, json.dumps(list(economics.SOURCE_PATHS)),
                     f"fingerprint-{composition.auto_roots}", self.clock(), 0, "dispatched", job_id, self.clock()))
            return {"status": "scanned"}
        def dispatch_next(self, control):
            if composition.candidate and composition.candidate["status"] == "dispatched":
                return {"status": "dispatched", "job_id": composition.candidate["job_id"]}
            return {"status": "idle"}

    class StubRuntime:
        def __init__(self, config):
            self.config = config
    class StubBinderIdentity:
        root = tmp_path
        manifest_sha256 = "manifest-stub"
        fingerprint = "identity-stub"
        source_commit = "commit-stub"

    composition = None
    def compose(runtime, profile, *, state_directory, backend):
        nonlocal composition
        composition = StubComposition(runtime, backend, state_directory)
        return composition

    monkeypatch.setattr(app_module, "Runtime", StubRuntime)
    monkeypatch.setattr(surface_module, "compose_surface", compose)
    monkeypatch.setattr(attention_module, "AttentionController", StubAttentionController)
    monkeypatch.setattr(observer_module, "SkillsObserverAnalysisProvider", StubBackend)
    monkeypatch.setattr(binding_module, "bind_local_runtime", lambda: StubBinderIdentity())

    report = economics._execute(tmp_path / "artifacts", max_inquiries=5, wall_seconds=79)
    assert report["status"] == "completed", (report.get("failure"), report.get("cleanup_failure"), report.get("inquiry_count"))
    assert report["inquiry_count"] == 5
    assert report["budget"]["wall_seconds"] == 79
    assert len(report["attempts"]) == 5
    assert all(attempt["actual_inference_confirmed"] for attempt in report["attempts"])
    assert sum(attempt["kind"] == "automatic" for attempt in report["attempts"]) == 2
    assert sum(attempt["kind"] == "foreground" for attempt in report["attempts"]) == 3
    assert report["automatic_root_count"] == 2
    assert len(report["foreground"]) == 3
    assert [item["label"] for item in report["automatic"]] == [
        "automatic_preparation_1", "automatic_refresh_2"]
    assert report["automatic_remains_disabled"] is True
    assert report["inference_performed"] is True
    assert report["result"]["all_thresholds_passed"] is False
    assert report["result"]["elapsed_overhead_threshold_measured"] is False
    assert composition.jobs.foreground_count == 3
    assert composition.jobs.preparation_lookup_scopes
    assert all(scope["project"] == economics.SEMANTIC_PROJECT
               for scope in composition.jobs.preparation_lookup_scopes)
    assert composition.backend_closed is True
