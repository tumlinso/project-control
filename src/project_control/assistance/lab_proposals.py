"""Strict parsing and validation for model-authored LAB proposals.

The model output is a data object, never a command string to interpret. Source
citations are resolved against the exact detached snapshot used for planning.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping, Sequence


class ProposalError(ValueError):
    """A proposal is malformed, stale, or outside its authorized scope."""


MAX_PROPOSAL_BYTES = 65 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS = 64
MAX_CITATIONS = 128
MAX_ARGV = 64
MAX_TEXT_BYTES = 8192
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceCitation:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ProposalArtifact:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class LabProposal:
    hypothesis: str
    source_citations: tuple[SourceCitation, ...]
    artifacts: tuple[ProposalArtifact, ...]
    argv: tuple[str, ...]
    measurements: tuple[str, ...]
    stop_rule: str
    done: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "source_citations": [vars_dataclass(item) for item in self.source_citations],
            "artifacts": [vars_dataclass(item) for item in self.artifacts],
            "argv": list(self.argv), "measurements": list(self.measurements),
            "stop_rule": self.stop_rule, "done": self.done,
        }


def parse_proposal(value: str | bytes | Mapping[str, Any]) -> LabProposal:
    """Parse exactly one JSON object with the documented proposal fields."""
    if isinstance(value, bytes):
        if len(value) > MAX_PROPOSAL_BYTES:
            raise ProposalError("proposal exceeds the 65 MiB limit")
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProposalError("proposal must be UTF-8 JSON") from exc
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ProposalError("proposal exceeds the 65 MiB limit")
        try:
            value = json.loads(value, object_pairs_hook=_unique_object,
                               parse_constant=lambda token: (_ for _ in ()).throw(
                                   ProposalError(f"non-finite JSON value is forbidden: {token}")))
        except (json.JSONDecodeError, UnicodeEncodeError) as exc:
            raise ProposalError("planner must return one JSON object") from exc
    if not isinstance(value, Mapping):
        raise ProposalError("planner must return one JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProposalError("proposal mapping is not bounded JSON data") from exc
    if len(encoded) > MAX_PROPOSAL_BYTES:
        raise ProposalError("proposal exceeds the 65 MiB limit")
    required = {"hypothesis", "source_citations", "artifacts", "argv", "measurements", "stop_rule", "done"}
    if set(value) != required:
        raise ProposalError("proposal must contain exactly the documented fields")
    hypothesis = _text(value["hypothesis"], "hypothesis", allow_empty=bool(value["done"]))
    stop_rule = _text(value["stop_rule"], "stop_rule", allow_empty=bool(value["done"]))
    done = value["done"]
    if not isinstance(done, bool):
        raise ProposalError("done must be a boolean")
    citations_raw = value["source_citations"]
    if not isinstance(citations_raw, list) or len(citations_raw) > MAX_CITATIONS:
        raise ProposalError("source_citations must be a bounded array")
    citations: list[SourceCitation] = []
    for item in citations_raw:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ProposalError("each source citation must contain path and sha256")
        path = _relative_path(item["path"], "citation path")
        digest = item["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ProposalError("citation sha256 must be lowercase hexadecimal")
        citations.append(SourceCitation(path, digest))
    if len({item.path for item in citations}) != len(citations):
        raise ProposalError("source citation paths must be unique")
    artifacts_raw = value["artifacts"]
    if not isinstance(artifacts_raw, list) or len(artifacts_raw) > MAX_ARTIFACTS:
        raise ProposalError("artifacts must be a bounded array")
    artifacts: list[ProposalArtifact] = []
    total = 0
    for item in artifacts_raw:
        if not isinstance(item, Mapping) or set(item) != {"path", "content"}:
            raise ProposalError("each artifact must contain path and content")
        path = _relative_path(item["path"], "artifact path")
        content = item["content"]
        if not isinstance(content, str):
            raise ProposalError("artifact content must be UTF-8 text")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ProposalError("artifact content must be valid UTF-8") from exc
        total += len(encoded)
        if total > MAX_ARTIFACT_BYTES:
            raise ProposalError("proposal artifacts exceed the 64 MiB limit")
        artifacts.append(ProposalArtifact(path, content))
    if len({item.path for item in artifacts}) != len(artifacts):
        raise ProposalError("artifact paths must be unique")
    argv_raw = value["argv"]
    if not isinstance(argv_raw, list) or len(argv_raw) > MAX_ARGV:
        raise ProposalError("argv must be a bounded array")
    argv = tuple(_text(arg, "argv item") for arg in argv_raw)
    if sum(len(arg.encode("utf-8")) for arg in argv) > 12 * 1024:
        raise ProposalError("argv exceeds the 12 KiB limit")
    measurements_raw = value["measurements"]
    if not isinstance(measurements_raw, list) or len(measurements_raw) > 64:
        raise ProposalError("measurements must be a bounded array")
    measurements = tuple(_text(item, "measurement") for item in measurements_raw)
    if not done and (not citations or not artifacts or not argv or not measurements):
        raise ProposalError("executable proposals require citations, artifacts, argv, and measurements")
    return LabProposal(hypothesis, tuple(citations), tuple(artifacts), argv,
                       measurements, stop_rule, done)


def validate_proposal(proposal: LabProposal, *, source_root: Path,
                      source_manifest: Mapping[str, Any], allowed_tools: Sequence[str]) -> None:
    """Check citations against snapshot bytes and enforce the frozen tool grant."""
    entries = {item["path"]: item for item in source_manifest.get("files", [])
               if isinstance(item, Mapping) and isinstance(item.get("path"), str)}
    for citation in proposal.source_citations:
        entry = entries.get(citation.path)
        if entry is None or entry.get("sha256") != citation.sha256:
            raise ProposalError(f"citation does not match selected captured source: {citation.path}")
        path = Path(source_root).joinpath(*PurePosixPath(citation.path).parts)
        if path.is_symlink() or not path.is_file():
            raise ProposalError("citation source is not a regular captured file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != citation.sha256:
            raise ProposalError(f"captured citation bytes changed: {citation.path}")
    if proposal.done:
        return
    if not proposal.argv or proposal.argv[0] not in tuple(allowed_tools):
        raise ProposalError("proposal executable is outside the authorized tool set")


def verify_artifacts(proposal: LabProposal, root: Path) -> None:
    """Verify the private proposal tree still matches its exact recorded bytes."""
    base = Path(root)
    if base.is_symlink() or not base.is_dir():
        raise ProposalError("proposal tree is unavailable or linked")
    expected_files = {item.path: item for item in proposal.artifacts}
    observed_files: set[str] = set()
    observed_dirs: set[str] = set()
    for directory, dirnames, filenames in os.walk(base, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(base).as_posix()
        if relative_dir != ".":
            observed_dirs.add(relative_dir)
        for name in list(dirnames):
            path = current / name
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise ProposalError("proposal tree contains a linked or special directory")
        for name in filenames:
            path = current / name
            info = path.lstat()
            relative = path.relative_to(base).as_posix()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o400):
                raise ProposalError("proposal artifact mode or file type changed")
            item = expected_files.get(relative)
            if item is None:
                raise ProposalError("proposal tree contains an unrecorded file")
            observed_files.add(relative)
            if path.read_bytes() != item.content.encode("utf-8"):
                raise ProposalError("proposal artifact bytes changed")
    expected_dirs: set[str] = set()
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent.as_posix() not in {".", "/"}:
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    if observed_files != set(expected_files) or observed_dirs != expected_dirs:
        raise ProposalError("proposal tree inventory changed")


def write_artifacts(proposal: LabProposal, destination: Path) -> Path:
    """Materialize generated files in a fresh private, immutable-by-contract tree."""
    root = Path(destination)
    if root.exists() or root.is_symlink():
        raise ProposalError("proposal destination must be new")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        for artifact in proposal.artifacts:
            path = root.joinpath(*PurePosixPath(artifact.path).parts)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o400)
            try:
                data = artifact.content.encode("utf-8")
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fchmod(fd, 0o400)
            finally:
                os.close(fd)
        for parent in sorted({p.parent for p in root.rglob("*") if p.is_file()}, key=lambda p: len(p.parts), reverse=True):
            os.chmod(parent, 0o500)
        os.chmod(root, 0o500)
        return root
    except BaseException:
        import shutil
        for directory, _, _ in os.walk(root, topdown=True, followlinks=False):
            os.chmod(directory, 0o700)
        shutil.rmtree(root, ignore_errors=True)
        raise


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ProposalError(f"{label} must be a safe relative path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ProposalError(f"{label} must be a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ProposalError(f"{label} must be a safe relative path")
    return path.as_posix()


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ProposalError(f"{label} must be non-empty text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ProposalError(f"{label} must be valid UTF-8") from exc
    if size > MAX_TEXT_BYTES or "\0" in value:
        raise ProposalError(f"{label} exceeds its text limit")
    return value


def vars_dataclass(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProposalError(f"duplicate JSON field: {key}")
        result[key] = value
    return result
