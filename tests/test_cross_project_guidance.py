import hashlib
import subprocess

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


def _repo(root, files):
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def test_declared_cross_project_guidance_is_explicit_scoped_and_source_backed(tmp_path, monkeypatch):
    source = _repo(tmp_path / "source", {
        ".project-context.json": '{"guidance":[{"target":"guidance-project/docs:setup.md"}]}\n',
        "README.md": "Mention guidance-project/docs:incidental.md without declaring it.\n",
    })
    guidance = _repo(tmp_path / "guidance", {"setup.md": "# Setup\nUse this declared guidance.\n",
        "incidental.md": "This mention must not be read.\n"})
    monkeypatch.setenv("PROJECT_CONTROL_SOURCE_STATE_DIR", str(tmp_path / "source-cache"))
    config = ProjectControlConfig(workspaces={
        "demo": WorkspaceConfig(repositories={"implementation": RepositoryConfig(root=source)}),
        "guidance-project": WorkspaceConfig(repositories={"docs": RepositoryConfig(root=guidance)}),
    })
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        lambda _project: (_ for _ in ()).throw(AssertionError("source context must not consult Todo")),
        ContextHost("observer", "fixture", frozenset({"demo", "guidance-project"})))

    response = service.call("context", project="demo", targets=[{
        "kind": "path", "target": "README.md", "repository": "implementation"}],
        include=["guidance"], budget_bytes=8192)

    relation = next(row for row in response["data"]["sections"]["guidance"]
        if row.get("basis") == "guidance")
    assert "Use this declared guidance." in str(relation["target_context"])
    assert "mention must not be read" not in str(relation["target_context"])
    source_locator = next(row for row in response["sources"]
        if row["project"] == "guidance-project" and row["path"] == "setup.md")
    assert source_locator["content_sha256"] == hashlib.sha256((guidance / "setup.md").read_bytes()).hexdigest()

    restricted = InformationService(config, SQLitePacketStore(tmp_path / "restricted-packets"),
        lambda _project: (_ for _ in ()).throw(AssertionError("source context must not consult Todo")),
        ContextHost("observer", "restricted", frozenset({"demo"})))
    denied = restricted.call("context", project="demo", targets=["implementation:README.md"],
        include=["guidance"], budget_bytes=8192)
    unavailable = next(row for row in denied["data"]["sections"]["guidance"]
        if row.get("basis") == "guidance")
    assert unavailable["target_status"] == "unavailable"
    assert "not_permitted" in unavailable["target_reason"]
    assert denied["status"] == "partial"
