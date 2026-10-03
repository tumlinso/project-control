from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_control.config import ProjectControlConfig, configured_observer_skills_root, configured_skills_root, render_config
from project_control.skills import SkillError, SkillRegistry


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "skills"
    skill = root / "cuda-review"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: CUDA review\ndescription: Review Volta kernels\n---\n# Review\nConsult supporting references.\n")
    (skill / "references" / "volta.md").write_text("# Volta\nWarp width is 32.\n")
    return root, skill, SkillRegistry(root)


def sid(registry):
    return registry.list()["skills"][0]["id"]


def test_discovery_read_journey(corpus):
    root, skill, registry = corpus
    listed = registry.list(query="Volta")
    row = listed["skills"][0]
    assert row["identity_scope"] == "frontmatter"
    assert row["resource_names"] == ["SKILL.md", "references/volta.md"]
    assert root.as_posix() not in json.dumps(listed)
    assert registry.read(row["id"])["content"].startswith("---")
    assert "Warp width" in registry.read(row["id"], "references/volta.md")["content"]
    assert listed["authority"] == "advisory_instruction"
    assert listed["mutation_authority"] is False


def test_config_runtime_independence(corpus):
    root, skill, registry = corpus
    runtime = root.parent / "runtime"
    config = ProjectControlConfig(skills_root=runtime, observer_skills_root=root)
    assert configured_observer_skills_root(config, {}) == root
    assert configured_skills_root(config, {}) == runtime
    assert configured_observer_skills_root(config, {"PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": str(root)}) == root
    assert configured_skills_root(config, {"PROJECT_CONTROL_OBSERVER_SKILLS_ROOT": str(root)}) == runtime
    import tomllib
    assert ProjectControlConfig.model_validate(tomllib.loads(render_config(config))) == config
    assert config.workspaces == {}
    original = configured_skills_root(config, {})
    (skill / "SKILL.md").write_text((skill / "SKILL.md").read_text() + "changed\n")
    assert configured_skills_root(config, {}) == original


@pytest.mark.parametrize("resource", ["../outside.md", "/tmp/outside.md", "references/../SKILL.md", "references//volta.md", "./SKILL.md", "references\\volta.md", "bad\x00.md", "C:/outside.md", "skill://local/foo/SKILL.md"])
def test_paths_fail_closed(corpus, resource):
    with pytest.raises(SkillError):
        corpus[2].read(sid(corpus[2]), resource)


def test_symlink_and_nonregular_fail_closed(corpus, tmp_path):
    root, skill, registry = corpus
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (skill / "link.md").symlink_to(outside)
    (skill / "directory").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(skill / "pipe.md")
    for resource in ("link.md", "directory/outside.md", "pipe.md", "references"):
        with pytest.raises(SkillError):
            registry.read(sid(registry), resource)
    assert "link.md" not in [r["path"] for r in registry.resources(sid(registry))]


def test_discovery_only_reads_header(corpus, monkeypatch):
    root, skill, registry = corpus
    (skill / "references" / "broken.md").write_bytes(b"\xff\x00")
    original = os.read
    read_bytes = 0
    def observed(fd, size):
        nonlocal read_bytes
        data = original(fd, size)
        read_bytes += len(data)
        return data
    monkeypatch.setattr(os, "read", observed)
    registry.list()
    # Discovery may rediscover to validate IDs, but never reads body/supporting files.
    assert read_bytes < 1024


@pytest.mark.parametrize("header", ["no frontmatter", "---\nname: only\n---\n", "---\nname: [bad]\ndescription: text\n---\n", "---\nname: &x title\ndescription: *x\n---\n", "---\nname: !!python/object:foo {}\ndescription: text\n---\n"])
def test_malformed_skills_not_discovered(corpus, header):
    root, skill, registry = corpus
    (skill / "SKILL.md").write_text(header)
    assert registry.list()["skills"] == []


def test_utf8_binary_unsupported_and_denied(corpus):
    root, skill, registry = corpus
    skill_id = sid(registry)
    for name, payload in [("binary.md", b"abc\x00"), ("invalid.md", b"\xff"), ("video.mp4", b"text"), ("secret.md", b"text")]:
        (skill / name).write_bytes(payload)
        with pytest.raises(SkillError):
            registry.read(skill_id, name)


def test_readonly_and_no_execution(corpus, monkeypatch):
    root, skill, registry = corpus
    sentinel = skill / "executed"
    script = skill / "run.py"
    script.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n")
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    original = os.open
    calls = []
    def checked(path, flags, *args, **kwargs):
        calls.append(flags)
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", checked)
    assert "Path" in registry.read(sid(registry), "run.py")["content"]
    assert not sentinel.exists()
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert calls


def test_freshness_content_and_stale_identity(corpus):
    root, skill, registry = corpus
    skill_id = sid(registry)
    first = registry.read(skill_id, "references/volta.md")
    (skill / "references" / "volta.md").write_text("# Volta\nNew content\n")
    second = registry.read(skill_id, "references/volta.md")
    assert first["identity"] != second["identity"]
    assert first["freshness"] != second["freshness"]
    with pytest.raises(SkillError, match="identity"):
        registry.read(skill_id, "references/volta.md", expected_identity=first["identity"])


def test_bounded_response_and_continuation(corpus):
    root, skill, registry = corpus
    (skill / "large.md").write_text("abcdefghij\n" * 1000)
    skill_id = sid(registry)
    result = registry.read(skill_id, "large.md", budget_bytes=1024)
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 1024
    assert result["continuation_line"] > 1
    second = registry.read(skill_id, "large.md", line_start=result["continuation_line"], expected_identity=result["identity"])
    assert second["line_start"] == result["line_end"] + 1
    (skill / "long.md").write_text("x" * 2000)
    with pytest.raises(SkillError, match="line"):
        registry.read(skill_id, "long.md", budget_bytes=1024)


def test_redaction(corpus):
    root, skill, registry = corpus
    (skill / "references" / "volta.md").write_text("token = 'abcdefghijklmno'\n/home/alice/private/data\n")
    result = registry.read(sid(registry), "references/volta.md")
    assert "abcdefghijklmno" not in result["content"]
    assert "/home/alice" not in result["content"]


def test_read_race_rejected(corpus, monkeypatch):
    root, skill, registry = corpus
    skill_id = sid(registry)
    original = os.read
    replaced = False
    def racing(fd, size):
        nonlocal replaced
        payload = original(fd, size)
        if size > 8192 and payload and not replaced:
            replaced = True
            target = skill / "references" / "volta.md"
            target.unlink()
            target.write_text("replacement")
        return payload
    monkeypatch.setattr(os, "read", racing)
    with pytest.raises(SkillError) as exc:
        registry.read(skill_id, "references/volta.md")
    assert exc.value.code == "stale_resource"


def test_missing_root_and_root_replacement(tmp_path):
    root = tmp_path / "missing"
    registry = SkillRegistry(root)
    assert registry.list()["status"] == "unavailable"
    root.mkdir()
    assert registry.list()["status"] == "ok"
    root.rename(tmp_path / "moved")
    root.mkdir()
    with pytest.raises(SkillError) as exc:
        registry.list()
    assert exc.value.code == "stale_resource"


def test_list_pagination_stale_cursor(corpus):
    root, skill, registry = corpus
    other = root / "another"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: Another\ndescription: Other domain\n---\nbody\n")
    page = registry.list(max_items=1)
    assert page["continuation_cursor"]
    assert len(registry.list(max_items=1, continuation_cursor=page["continuation_cursor"])["skills"]) == 1
    (other / "SKILL.md").write_text("---\nname: Changed\ndescription: Other domain\n---\nbody\n")
    with pytest.raises(SkillError) as exc:
        registry.list(max_items=1, continuation_cursor=page["continuation_cursor"])
    assert exc.value.code == "stale_cursor"


def test_oversized_resource_and_frontmatter(corpus):
    root, skill, registry = corpus
    skill_id = sid(registry)
    target = skill / "huge.md"
    with target.open("wb") as stream:
        stream.truncate(2 * 1024 * 1024 + 1)
    with pytest.raises(SkillError) as exc:
        registry.read(skill_id, "huge.md")
    assert exc.value.code == "oversized"
    (skill / "SKILL.md").write_text("---\nname: test\ndescription: " + "x" * 8192 + "\n---\n")
    assert registry.list()["skills"] == []


def test_inventory_bound_and_depth(corpus, monkeypatch):
    import project_control.skills as module
    root, skill, registry = corpus
    skill_id = sid(registry)
    monkeypatch.setattr(module, "MAX_RESOURCES", 1)
    with pytest.raises(SkillError) as exc:
        registry.resources(skill_id)
    assert exc.value.code == "inventory_limit"
    monkeypatch.setattr(module, "MAX_RESOURCES", 4096)
    deep = skill.joinpath(*("level" for _ in range(9)))
    deep.mkdir(parents=True)
    (deep / "ref.md").write_text("text")
    with pytest.raises(SkillError) as exc:
        registry.resources(skill_id)
    assert exc.value.code == "inventory_limit"


def test_metadata_redaction_and_vanished_resource(corpus):
    root, skill, registry = corpus
    (skill / "SKILL.md").write_text("---\nname: Test\ndescription: '/home/alice/private/data bearer abcdefghijklmnop'\n---\nbody\n")
    listed = registry.list()
    assert "/home/alice" not in json.dumps(listed)
    assert "abcdefghijklmnop" not in json.dumps(listed)
    skill_id = listed["skills"][0]["id"]
    (skill / "references" / "volta.md").unlink()
    with pytest.raises(SkillError):
        registry.read(skill_id, "references/volta.md")


def test_operational_caches_excluded_and_reads_denied(corpus, monkeypatch):
    import project_control.skills as module
    root, skill, registry = corpus
    for directory in (".venv", ".ctxpp", "tool/build", "scripts/__pycache__"):
        cache = skill / directory
        cache.mkdir(parents=True)
        for index in range(12):
            (cache / f"generated{index}.md").write_text("generated cache\n" * 100)
    monkeypatch.setattr(module, "MAX_RESOURCES", 2)
    rows = registry.list()["skills"]
    assert len(rows) == 1
    assert rows[0]["resource_count"] == 2
    skill_id = rows[0]["id"]
    for directory in (".venv", ".ctxpp", "tool/build", "scripts/__pycache__"):
        with pytest.raises(SkillError) as exc:
            registry.read(skill_id, f"{directory}/generated0.md")
        assert exc.value.code == "denied_resource"
    assert (skill / "tool/build/generated0.md").exists()


def test_bad_inventory_isolated_from_other_skill_discovery(corpus, monkeypatch):
    import project_control.skills as module
    root, skill, registry = corpus
    other = root / "good"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: Good\ndescription: Good domain\n---\nbody\n")
    monkeypatch.setattr(module, "MAX_RESOURCES", 1)
    listed = registry.list()
    assert [row["name"] for row in listed["skills"]] == ["Good"]
    assert listed["discovery_incomplete"]
    assert listed["warnings"][0]["code"] == "inventory_limit"
    # The malformed corpus is still rejected on explicit inspection.
    cuda = next(s for s in registry._discover()[0] if s.name == "CUDA review")
    with pytest.raises(SkillError):
        registry.resources(cuda.id)


def test_selected_text_read_never_reenumerates_corpus(corpus, monkeypatch):
    root, skill, registry = corpus
    skill_id = sid(registry)
    expected_freshness = next(row["freshness"] for row in registry.resources(skill_id) if row["path"] == "references/volta.md")
    def forbidden(*args, **kwargs):
        pytest.fail("Selected resource read must not enumerate the whole skill corpus")
    monkeypatch.setattr(registry, "resources", forbidden)
    read = registry.read_text(skill_id, "references/volta.md")
    assert "Warp width" in read["content"]
    assert read["freshness"] == expected_freshness
    assert registry.read_bytes(skill_id, "references/volta.md")[1] == read["identity"]
