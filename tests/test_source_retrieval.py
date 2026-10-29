"""Source and evidence retrieval do not require the Todo authority."""
import hashlib
import subprocess

from project_control.as1_context import ContextHost, InformationService
from project_control.as1_packets import SQLitePacketStore
from project_control.config import ProjectControlConfig, RepositoryConfig, WorkspaceConfig


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _service(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / "module.py").write_text(
        "def calculate_total(value):\n    return value + 1\n\n"
        "def caller(value):\n    return calculate_total(value)\n",
        encoding="utf-8")
    (repository / "test_module.py").write_text(
        "from module import calculate_total\n\ndef test_calculate_total():\n    assert calculate_total(1) == 2\n",
        encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "fixture")
    monkeypatch.setenv("PROJECT_CONTROL_SOURCE_STATE_DIR", str(tmp_path / "source-state"))
    config = ProjectControlConfig(workspaces={"demo": WorkspaceConfig(
        repositories={"source": RepositoryConfig(root=repository)})})
    def forbidden_snapshot(_project):
        raise AssertionError("retrieval attempted to inspect Todo")
    service = InformationService(config, SQLitePacketStore(tmp_path / "packets"),
        forbidden_snapshot, ContextHost("observer", "fixture", frozenset({"demo"})))
    return repository, service


def test_source_retrieval_and_symbol_resolution_are_model_and_todo_free(tmp_path, monkeypatch):
    repository, service = _service(tmp_path, monkeypatch)
    read = service.call("read", project="demo", paths=["module.py"])
    assert read["status"] == "ok"
    assert "def calculate_total" in read["data"]["files"][0]["content"]
    assert len(read["sources"][0]["content_sha256"]) == 64

    lexical = service.call("search", project="demo", query="calculate_total")
    assert lexical["status"] == "ok"
    assert lexical["data"]["query_kind"] == "lexical"
    assert lexical["data"]["intent"] == "literal substring search"

    exact = service.call("search", project="demo",
        query={"kind": "symbol", "target": "calculate_total"})
    assert exact["data"]["resolution"] == "resolved"
    assert exact["data"]["matches"][0]["path"] == "module.py"

    tree = service.call("inspect", project="demo", operation="tree")
    assert tree["data"]["repositories"]["source"]["complete"] is True
    assert "test_module.py" in tree["data"]["repositories"]["source"]["paths"]

    context = service.call("context", project="demo", targets=["source:module.py"],
        include=["implementation", "tests"], budget_bytes=2048)
    assert context["status"] == "ok"
    assert context["data"]["complete"] is True
    assert context["sources"][0]["path"] == "module.py"

    trace = service.call("trace", project="demo", targets=["source:module.py"],
        predicates=["call"])
    assert trace["data"]["repositories"]["source"]["edges"][0]["predicate"] == "call"


def test_evidence_retrieval_keeps_attribution_and_source_identity(tmp_path, monkeypatch):
    repository, service = _service(tmp_path, monkeypatch)
    from project_evidence import EvidenceStore, Observation, SourceRef
    path = repository / "module.py"
    reference = SourceRef("demo/source", "module.py", hashlib.sha256(path.read_bytes()).hexdigest())
    store = EvidenceStore(tmp_path / "evidence.sqlite3", tmp_path / "artifacts")
    store.record_observation(Observation("obs-1", "demo", "test_result", "test:calculate_total",
        source_refs=(reference,), payload={"result": "passed"}))
    # InformationService uses its separate Project Evidence store beside packets.
    result = service.call("evidence", project="demo", subject="test:calculate_total")
    assert result["data"]["provenance"] == "project-evidence"
    assert result["data"]["observations"][0]["owner"] == "demo"
    assert result["data"]["source_mentions_are_proof"] is False


def test_typed_targets_and_repository_root_aliases_are_explicit(tmp_path, monkeypatch):
    repository, service = _service(tmp_path, monkeypatch)
    root = str(repository.resolve())
    read = service.call("read", project="demo", repository=root, paths=["module.py"])
    assert read["data"]["repository"] == "source"

    context = service.call("context", project="demo", targets=[{
        "kind": "path", "target": "module.py", "repository": root,
        "line_start": 1, "line_end": 2,
    }], include=["implementation"], budget_bytes=2048)
    assert context["data"]["repositories"] == ["source"]
    assert "calculate_total" in str(context["data"]["sections"])

    # C++ qualified names stay a typed symbol value; their `::` is never
    # interpreted as a repository alias separator.
    qualified = service.call("context", project="demo", targets=[{
        "kind": "symbol", "target": "demo::Widget::run", "repository": "source",
    }], include=["implementation"], budget_bytes=2048)
    assert qualified["data"]["repositories"] == ["source"]
    assert qualified["status"] == "partial"


def test_context_continuation_reads_next_source_bytes(tmp_path, monkeypatch):
    repository, service = _service(tmp_path, monkeypatch)
    large = repository / "guide.md"
    large.write_text("# Guide\n" + ("one complete paragraph with useful details.\n" * 80),
        encoding="utf-8")
    first = service.call("context", project="demo", targets=["source:guide.md"],
        include=["implementation"], budget_bytes=180)
    assert first["status"] == "partial"
    continuation = first["continuation"]["calls"][0]
    second = service.call(continuation.pop("tool"), **continuation)
    assert second["data"]["files"][0]["content"]
    assert second["data"]["files"][0]["complete"] is False
    assert second["data"]["files"][0]["cursor"]


def test_exact_symbol_resolution_reaches_beyond_first_tree_page(tmp_path, monkeypatch):
    repository, service = _service(tmp_path, monkeypatch)
    padding = repository / 'a-padding'
    padding.mkdir()
    for number in range(2005):
        (padding / f'{number:04}.txt').write_text('fixture\n')
    (repository / 'z-last.py').write_text('def after_first_page():\n    return 1\n')
    result = service.call('search', project='demo',
        query={'kind': 'symbol', 'target': 'after_first_page'})
    assert result['data']['resolution'] == 'resolved'
    assert result['data']['complete'] is True
    assert result['data']['matches'][0]['path'] == 'z-last.py'
    assert len(result['data']['matches'][0]['content_sha256']) == 64
    (repository / 'z-other.py').write_text('def after_first_page():\n    return 2\n')
    ambiguous = service.call('search', project='demo',
        query={'kind': 'symbol', 'target': 'after_first_page'})
    assert ambiguous['data']['resolution'] == 'ambiguous'
    assert {item['path'] for item in ambiguous['data']['matches']} == {'z-last.py', 'z-other.py'}
