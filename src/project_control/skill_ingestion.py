"""Explicit administrative ingestion; observer reads never extract archives."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import stat
import uuid
import zipfile

from .skills import SkillError, SkillRegistry

MAX_MEMBERS = 2048
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 2 * 1024 * 1024
CORPUS_MANIFEST = ".project-control-corpus.json"
_TEXT_SUFFIXES = {".md", ".json", ".jsonl", ".tsv"}
_PROTOTYPE_SUFFIXES = {".py", ".cpp", ".cu", ".c", ".h", ".hpp", ".sh"}


def _fail(code: str) -> None:
    raise SkillError(code)


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        _fail("invalid_archive_path")
    parts = value.split("/")
    if value.startswith("/") or any(p in {"", ".", ".."} or ":" in p or any(ord(c) < 32 for c in p) for p in parts):
        _fail("invalid_archive_path")
    return PurePosixPath(value).as_posix()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive(raw: bytes) -> tuple[dict[str, bytes], list[str], int]:
    """Validate every member, including prototype files that will not be extracted."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        infos = z.infolist()
        if len(infos) > MAX_MEMBERS:
            _fail("archive_member_limit")
        entries: dict[str, bytes] = {}
        total = 0
        for info in infos:
            name = _relative(info.filename.rstrip("/") if info.is_dir() else info.filename)
            if name in entries:
                _fail("archive_duplicate_path")
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if info.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                _fail("archive_unsupported_member")
            if info.is_dir():
                # Keep a sentinel to reject duplicates and file/directory collisions.
                entries[name] = b""
                continue
            if kind == stat.S_IFDIR:
                _fail("archive_unsupported_member")
            total += info.file_size
            if info.file_size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                _fail("archive_size_limit")
            if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 200:
                _fail("archive_compression_ratio")
            with z.open(info) as stream:
                data = stream.read(MAX_MEMBER_BYTES + 1)
            if len(data) != info.file_size or len(data) > MAX_MEMBER_BYTES:
                _fail("archive_size_limit")
            entries[name] = data
        directories = {i.filename.rstrip("/") for i in infos if i.is_dir()}
        files = {k: v for k, v in entries.items() if k not in directories}
        for name in files:
            if any(str(parent) in files for parent in PurePosixPath(name).parents if str(parent) != "."):
                _fail("archive_path_collision")
        # Only the known atlas wrapper is removed; no heuristic topology rewriting.
        if files and all(n.startswith("v100_atlas/") for n in files):
            files = {n.removeprefix("v100_atlas/"): b for n, b in files.items()}
        skipped: list[str] = []
        for name, data in files.items():
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in _PROTOTYPE_SUFFIXES:
                skipped.append(name)
            elif suffix not in _TEXT_SUFFIXES and PurePosixPath(name).name != "SHA256SUMS":
                _fail("archive_unsupported_resource")
            else:
                try:
                    text = data.decode("utf-8")
                    if "\x00" in text:
                        _fail("archive_invalid_text")
                except UnicodeError:
                    _fail("archive_invalid_text")
        if "SHA256SUMS" in files:
            seen: set[str] = set()
            for line in files["SHA256SUMS"].decode("utf-8").splitlines():
                match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
                if not match:
                    _fail("archive_invalid_checksums")
                digest, name = match.groups()
                name = _relative(name)
                if name in seen or name not in files or _sha(files[name]) != digest.lower():
                    _fail("archive_checksum_mismatch")
                seen.add(name)
            if seen != set(files) - {"SHA256SUMS"}:
                _fail("archive_checksum_coverage")
        return files, sorted(skipped), total
    except SkillError:
        raise
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, UnicodeError):
        _fail("archive_malformed")


def _semantic(files: dict[str, bytes], destination: str, resource: str, digest: str) -> dict:
    resources: list[dict] = []
    relationships: list[dict] = []
    unresolved: list[dict] = []
    registered: set[str] = set()
    try:
        manifest = json.loads(files.get("manifest.json", b"{}"))
        if not isinstance(manifest, dict):
            _fail("archive_invalid_manifest")
        compendium = manifest.get("compendium")
        if compendium is not None:
            if not isinstance(compendium, dict):
                _fail("archive_invalid_manifest")
            compendium_path = _relative(compendium.get("path", ""))
            if compendium_path not in files or compendium.get("sha256") != _sha(files[compendium_path]):
                _fail("archive_checksum_mismatch")
        documents = manifest.get("documents", []) + manifest.get("sources", [])
        for doc in documents:
            if not isinstance(doc, dict):
                _fail("archive_invalid_manifest")
            identity = doc.get("id")
            path = _relative(doc.get("path", ""))
            if not isinstance(identity, str) or not identity or identity in registered or path not in files:
                _fail("archive_invalid_manifest")
            if doc.get("sha256") != _sha(files[path]):
                _fail("archive_checksum_mismatch")
            if not isinstance(doc.get("title", identity), str) or not isinstance(doc.get("summary", doc.get("evidence_capsule", "")), str):
                _fail("archive_invalid_manifest")
            if not isinstance(doc.get("tags", []), list) or not all(isinstance(tag, str) for tag in doc.get("tags", [])):
                _fail("archive_invalid_manifest")
            registered.add(identity)
            resources.append({
                "id": identity, "path": f"{destination}/{path}",
                "title": doc.get("title", identity),
                "summary": doc.get("summary", doc.get("evidence_capsule", "")),
                "tags": doc.get("tags", []), "aliases": [], "sha256": _sha(files[path]),
                "metadata": {k: v for k, v in doc.items() if k not in {"id", "path", "title", "summary", "tags", "sha256"}},
            })
        for doc in documents:
            for field, relation in (("requires", "prerequisite"), ("sources", "evidence")):
                targets = doc.get(field, [])
                if not isinstance(targets, list) or not all(isinstance(t, str) for t in targets):
                    _fail("archive_invalid_manifest")
                for target in targets:
                    edge = {"from": doc["id"], "to": target, "type": relation}
                    (relationships if target in registered else unresolved).append(edge)
        # NEED_INDEX is a fixed six-column semantic table, not executable routing code.
        if "NEED_INDEX.md" in files:
            data = files["NEED_INDEX.md"]
            for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
                cells = [p.strip() for p in line.strip().strip("|").split("|")]
                if not line.strip().startswith("|") or len(cells) != 6 or cells[0] == "Need" or re.fullmatch(r"[- :]+", cells[0]):
                    continue
                identity = "need-" + _sha(cells[0].encode())[:16]
                if identity in registered:
                    _fail("archive_invalid_manifest")
                registered.add(identity)
                resources.append({"id": identity, "path": f"{destination}/NEED_INDEX.md", "title": cells[0], "summary": cells[5], "tags": ["need"], "aliases": [], "sha256": _sha(data), "line_start": number, "line_end": number})
                for cell, relation in zip(cells[1:5], ("mechanism", "composition", "prerequisite", "test")):
                    for target in re.findall(r"\b[A-Z][0-9]{2}\b", cell):
                        edge = {"from": identity, "to": target, "type": relation}
                        (relationships if target in registered else unresolved).append(edge)
        # Retain non-card substantive resources as generic indexed resources.
        mapped_paths = {r["path"] for r in resources}
        for path, data in sorted(files.items()):
            if PurePosixPath(path).suffix.lower() not in _TEXT_SUFFIXES or f"{destination}/{path}" in mapped_paths:
                continue
            resources.append({"id": "resource-" + _sha(path.encode())[:16], "path": f"{destination}/{path}", "title": PurePosixPath(path).stem, "summary": "", "tags": [], "aliases": [], "sha256": _sha(data)})
        return {"schema_version": 1, "source": {"archive": resource, "sha256": digest}, "resources": resources, "relationships": relationships, "unresolved_relationships": unresolved}
    except SkillError:
        raise
    except (TypeError, ValueError, KeyError, UnicodeError):
        _fail("archive_invalid_manifest")


def _open_dir(parent: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def _identical(directory: int, expected: dict[str, bytes]) -> bool:
    actual: dict[str, bytes] = {}
    def scan(fd: int, prefix: str = "") -> None:
        for name in os.listdir(fd):
            relative = prefix + name
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = _open_dir(fd, name)
                try:
                    scan(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode) and relative in expected and st.st_size == len(expected[relative]):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    with os.fdopen(child, "rb") as stream:
                        actual[relative] = stream.read(MAX_MEMBER_BYTES + 1)
                except Exception:
                    raise
            else:
                _fail("ingestion_destination_conflict")
    scan(directory)
    return actual == expected


def _publish(parent: int, stage: str, target: str) -> None:
    # Linux no-replace rename closes the destination-creation race without overwrite.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        _fail("ingestion_atomic_publish_unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(stage), parent, os.fsencode(target), 1) != 0:
        if ctypes.get_errno() == errno.EEXIST:
            _fail("ingestion_destination_conflict")
        _fail("ingestion_publish_failed")


def ingest_skill_archive(registry: SkillRegistry, skill_id: str, resource: str, destination: str, *, apply: bool = False) -> dict:
    """Validate a configured skill ZIP and explicitly stage faithful text extraction."""
    destination = _relative(destination)
    resource = _relative(resource)
    if destination == resource or resource.startswith(destination + "/"):
        _fail("ingestion_source_overlap")
    skill = registry.get(skill_id)
    raw, digest = registry.read_bytes(skill_id, resource, max_bytes=MAX_TOTAL_BYTES)
    files, skipped, total = _archive(raw)
    corpus = _semantic(files, destination, resource, digest)
    extracted = {p: b for p, b in files.items() if p not in skipped}
    if CORPUS_MANIFEST in extracted:
        _fail("archive_reserved_resource")
    extracted[CORPUS_MANIFEST] = (json.dumps(corpus, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    if len(extracted[CORPUS_MANIFEST]) > MAX_MEMBER_BYTES:
        _fail("archive_size_limit")
    receipt = {"status": "validated", "applied": False, "skill_id": skill_id, "archive": resource, "archive_sha256": digest, "destination": destination, "members": len(files), "extracted_resources": len(extracted) - 1, "decompressed_bytes": total, "skipped_prototypes": skipped, "semantic_resources": len(corpus["resources"]), "relationships": len(corpus["relationships"]), "unresolved_relationships": corpus["unresolved_relationships"]}
    if not apply:
        return receipt
    descriptors: list[int] = []
    links: list[tuple[int, str, tuple[int, int]]] = []
    def verify_ancestors() -> None:
        if descriptors:
            root_info = os.stat(registry.root, follow_symlinks=False)
            opened_root = os.fstat(descriptors[0])
            if (root_info.st_dev, root_info.st_ino) != (opened_root.st_dev, opened_root.st_ino):
                _fail("ingestion_destination_changed")
        for fd, name, before in links:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != before or not stat.S_ISDIR(info.st_mode):
                _fail("ingestion_destination_changed")
    stage = ".pc-ingest-" + uuid.uuid4().hex
    parent = None
    try:
        root = os.open(registry.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(root)
        info = os.fstat(root)
        if registry._root_stamp != (info.st_dev, info.st_ino):
            _fail("ingestion_destination_changed")
        current = root
        for component in PurePosixPath(str(skill.directory)).parts:
            child = _open_dir(current, component)
            info = os.fstat(child)
            links.append((current, component, (info.st_dev, info.st_ino)))
            current = child
            descriptors.append(current)
        parts = destination.split("/")
        for component in parts[:-1]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            child = _open_dir(current, component)
            info = os.fstat(child)
            links.append((current, component, (info.st_dev, info.st_ino)))
            current = child
            descriptors.append(current)
        parent = current
        try:
            existing = _open_dir(parent, parts[-1])
        except FileNotFoundError:
            existing = None
        if existing is not None:
            try:
                if not _identical(existing, extracted):
                    _fail("ingestion_destination_conflict")
            finally:
                os.close(existing)
            receipt.update(status="unchanged", applied=True)
            return receipt
        verify_ancestors()
        os.mkdir(stage, mode=0o700, dir_fd=parent)
        stagefd = _open_dir(parent, stage)
        try:
            for path, data in extracted.items():
                fd = os.dup(stagefd)
                try:
                    pieces = path.split("/")
                    for piece in pieces[:-1]:
                        try:
                            os.mkdir(piece, mode=0o700, dir_fd=fd)
                        except FileExistsError:
                            pass
                        nextfd = _open_dir(fd, piece)
                        os.close(fd)
                        fd = nextfd
                    out = os.open(pieces[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                    with os.fdopen(out, "wb") as stream:
                        stream.write(data)
                finally:
                    os.close(fd)
        finally:
            os.close(stagefd)
        # Recheck the immutable input before exposing the extraction.
        _, current_digest = registry.read_bytes(skill_id, resource, max_bytes=MAX_TOTAL_BYTES)
        if current_digest != digest:
            _fail("ingestion_source_changed")
        verify_ancestors()
        _publish(parent, stage, parts[-1])
        receipt.update(status="ingested", applied=True)
        return receipt
    except SkillError:
        raise
    except OSError:
        _fail("ingestion_unsafe_destination")
    finally:
        if parent is not None:
            try:
                shutil.rmtree(stage, dir_fd=parent)
            except FileNotFoundError:
                pass
        for fd in reversed(descriptors):
            os.close(fd)
