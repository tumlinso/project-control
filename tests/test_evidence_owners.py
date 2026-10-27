import hashlib
import json
import subprocess

from project_evidence import EvidenceStore, Observation, SourceRef

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig
from project_control.models import ProjectSnapshot, RepositoryIdentity


def _repository(root):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    source = root / "src.py"
    source.write_text("def run(): return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
        text=True, capture_output=True).stdout.strip()
    return source, commit


def test_registered_lab_evidence_preserves_application_owner_and_source_identity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source, _ = _repository(repo)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    lab_db, lab_artifacts = tmp_path / "lab-state/evidence.sqlite3", tmp_path / "lab-state/evidence-artifacts"
    writer = EvidenceStore(lab_db, lab_artifacts)
    receipt = {"session_id": "lab_0123456789abcdef", "measurement": {"metric": "loss",
        "value": 0.25}, "provenance": {"scheduler_job_id": "job-1"}}
    artifact = writer.put_artifact(json.dumps(receipt, sort_keys=True).encode(), "application/json")
    writer.record_observation(Observation("effect-1", "project-lab", "experiment_receipt",
        "lab_0123456789abcdef", source_refs=(SourceRef("demo/source", "src.py", digest),),
        artifact_refs=(artifact,), payload={"proposal_id": "proposal-1", "state": "completed",
            "job_id": "job-1"}))
    monkeypatch.setenv("PROJECT_CONTROL_EVIDENCE_STORES", json.dumps({"demo": [{
        "database": str(lab_db), "artifact_dir": str(lab_artifacts)}]}))
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"source": RepositoryConfig(root=repo)})})
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        lambda _project: (_ for _ in ()).throw(AssertionError("neutral evidence lookup must not need Todo")),
        ContextHost("observer", "fixture", frozenset({"demo"})))

    result = service.call("evidence", project="demo", subject="lab_0123456789abcdef",
        kinds=["experiment_receipt"])

    record = next(item for item in result["data"]["observations"] if item["observation_id"] == "effect-1")
    assert record["owner"] == "project-lab"
    assert record["application_owner"] == "project-lab"
    assert record["payload"]["state"] == "completed"
    assert record["artifact_refs"][0]["sha256"] == artifact.sha256
    assert record["artifact_contents"][0]["content"]["measurement"]["value"] == 0.25
    assert result["sources"][0]["content_sha256"] == digest


def test_evidence_joins_todo_gate_outcomes_when_provider_is_available(tmp_path):
    repo = tmp_path / "repo"
    _, commit = _repository(repo)
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        authority_repository="source", repositories={"source": RepositoryConfig(root=repo)})})
    snapshot = ProjectSnapshot(workspace_id="demo", observed_at="2026-10-27T00:00:00Z",
        todo_revision=4, repositories={"source": RepositoryIdentity(commit=commit, dirty=False)},
        todo_tables={"tasks": [{"id": "TASK-1", "title": "Build"}],
            "gates": [{"id": "GATE-1", "task_id": "TASK-1", "status": "passed", "valid": 1}]})
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        lambda _project: snapshot, ContextHost("observer", "fixture", frozenset({"demo"})),
        todo_adapter=lambda _project: object())

    result = service.call("evidence", project="demo", subject="TASK-1", kinds=["gates"])

    assert result["data"]["todo_authority"]["support"][0]["id"] == "GATE-1"
    # This fixture is not in the active plan, so its successful gate is
    # retained as a reference outcome rather than promoted to current proof.
    assert result["data"]["todo_authority"]["support"][0]["relevance"] == "reference"
