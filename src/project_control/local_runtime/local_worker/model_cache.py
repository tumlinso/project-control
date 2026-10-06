"""Persistent, content-addressed CORE4 model cache."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Iterator


class ModelCacheError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ModelCache:
    def __init__(self, cache_root: str | Path, cold_root: str | Path,
                 *, lease_root: str | Path | None = None):
        self.root = Path(cache_root).expanduser().resolve()
        self.cold_root = Path(cold_root).expanduser().resolve()
        self.lease_root = (Path(lease_root).expanduser().resolve()
                           if lease_root is not None else self.root / ".leases")
        if self.root == self.cold_root or self.root in self.cold_root.parents or self.cold_root in self.root.parents:
            raise ModelCacheError("cache and cold roots must not overlap")
        if self.lease_root == self.root or self.lease_root == self.cold_root:
            raise ModelCacheError("lease root must be distinct from model roots")

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".cache.lock"
        with lock.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @property
    def active_pointer(self) -> Path:
        return self.root / "active-profile.json"

    def _cold_manifest(self, candidate_id: str) -> tuple[dict, Path, dict]:
        if not candidate_id or candidate_id in {".", ".."} or "/" in candidate_id or "\\" in candidate_id:
            raise ModelCacheError("candidate ID must be one safe path component")
        candidate = self.cold_root / candidate_id
        manifest_path = candidate / "asset-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ModelCacheError(f"invalid cold asset manifest: {manifest_path}") from error
        if manifest.get("format") != "CORE4-MODEL-ASSET/1" or manifest.get("candidate_id") != candidate_id:
            raise ModelCacheError("cold asset manifest identity is invalid")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
            raise ModelCacheError("persistent cache currently requires one manifest payload")
        record = files[0]
        relative = Path(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise ModelCacheError("manifest payload path escapes its candidate directory")
        source = candidate / relative
        if not source.is_file():
            raise ModelCacheError(f"cold payload is missing: {source}")
        return manifest, source, record

    def payload_dir(self, candidate_id: str, payload_sha256: str) -> Path:
        return self.root / candidate_id / payload_sha256

    def install(self, candidate_id: str, *, activate: bool = False) -> dict[str, object]:
        manifest, source, record = self._cold_manifest(candidate_id)
        expected_hash = str(record.get("sha256", ""))
        expected_bytes = int(record.get("bytes", -1))
        if len(expected_hash) != 64 or expected_bytes < 0 or source.stat().st_size != expected_bytes:
            raise ModelCacheError("cold manifest size or SHA-256 metadata is invalid")
        final = self.payload_dir(candidate_id, expected_hash)
        with self._locked():
            if (final / "READY").is_file():
                result = self.verify(candidate_id, expected_hash, full=False)
                if activate:
                    self.activate(candidate_id, expected_hash)
                return result | {"installed": False}
            required = expected_bytes + max(1, (expected_bytes + 9) // 10)
            free = shutil.disk_usage(self.root).free
            if free < required:
                raise ModelCacheError(f"insufficient cache space: additional_bytes_required={required - free}")
            partial = self.root / f".partial-{candidate_id}-{uuid.uuid4().hex}"
            partial.mkdir()
            try:
                destination = partial / "model.gguf"
                shutil.copyfile(source, destination)
                with destination.open("rb") as stream:
                    os.fsync(stream.fileno())
                actual_hash = _sha256(destination)
                if actual_hash != expected_hash:
                    raise ModelCacheError(f"staged payload checksum mismatch: {actual_hash}")
                stat = destination.stat()
                cache_manifest = {
                    "format": "CORE4-MODEL-CACHE/1",
                    "schema_version": 1,
                    "candidate_id": candidate_id,
                    "payload_sha256": expected_hash,
                    "payload_bytes": expected_bytes,
                    "source_manifest": manifest,
                    "cache_metadata": {"inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns},
                }
                _atomic_json(partial / "asset-manifest.json", cache_manifest)
                ready = partial / "READY"
                ready.write_text(expected_hash + "\n", encoding="ascii")
                with ready.open("rb") as stream:
                    os.fsync(stream.fileno())
                _fsync_dir(partial)
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(partial, final)
                _fsync_dir(final.parent)
            finally:
                if partial.exists():
                    shutil.rmtree(partial)
            result = self.verify(candidate_id, expected_hash, full=False)
            if activate:
                self.activate(candidate_id, expected_hash)
            return result | {"installed": True}

    def verify(self, candidate_id: str, payload_sha256: str, *, full: bool) -> dict[str, object]:
        directory = self.payload_dir(candidate_id, payload_sha256)
        ready = directory / "READY"
        manifest_path = directory / "asset-manifest.json"
        model = directory / "model.gguf"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ModelCacheError(f"cache manifest is invalid: {manifest_path}") from error
        if not ready.is_file() or ready.read_text(encoding="ascii").strip() != payload_sha256:
            raise ModelCacheError("cache READY marker is absent or invalid")
        if manifest.get("format") != "CORE4-MODEL-CACHE/1" or manifest.get("candidate_id") != candidate_id:
            raise ModelCacheError("cache manifest identity is invalid")
        if manifest.get("payload_sha256") != payload_sha256 or not model.is_file():
            raise ModelCacheError("cache payload identity is invalid")
        stat = model.stat()
        if stat.st_size != int(manifest.get("payload_bytes", -1)):
            raise ModelCacheError("cache payload size changed")
        with model.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ModelCacheError("cache payload is not a GGUF file")
        metadata = manifest.get("cache_metadata", {})
        metadata_changed = stat.st_ino != metadata.get("inode") or stat.st_mtime_ns != metadata.get("mtime_ns")
        if full or metadata_changed:
            actual = _sha256(model)
            if actual != payload_sha256:
                raise ModelCacheError(f"cache payload checksum mismatch: {actual}")
        return {
            "format": "CORE4-MODEL-CACHE-RESULT/1",
            "candidate_id": candidate_id,
            "payload_sha256": payload_sha256,
            "payload_bytes": stat.st_size,
            "payload_path": str(model),
            "verification": "full" if full or metadata_changed else "quick",
            "ready": True,
        }

    def activate(self, candidate_id: str, payload_sha256: str) -> dict[str, object]:
        result = self.verify(candidate_id, payload_sha256, full=False)
        pointer = {
            "format": "CORE4-ACTIVE-MODEL/1",
            "candidate_id": candidate_id,
            "payload_sha256": payload_sha256,
            "payload_path": result["payload_path"],
        }
        _atomic_json(self.active_pointer, pointer)
        return pointer

    def active(self) -> dict[str, object] | None:
        try:
            value = json.loads(self.active_pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if value.get("format") == "CORE4-ACTIVE-MODEL/1" else None

    def list(self) -> list[dict[str, object]]:
        entries = []
        if not self.root.is_dir():
            return entries
        for ready in sorted(self.root.glob("*/*/READY")):
            try:
                entries.append(self.verify(ready.parent.parent.name, ready.parent.name, full=False))
            except ModelCacheError as error:
                entries.append({"candidate_id": ready.parent.parent.name, "payload_sha256": ready.parent.name,
                                "ready": False, "error": str(error)})
        return entries

    def inspect(self) -> dict[str, object]:
        return {
            "format": "CORE4-MODEL-CACHE-INSPECT/1",
            "cache_root": str(self.root),
            "cold_root": str(self.cold_root),
            "active": self.active(),
            "entries": self.list(),
        }

    def remove(self, candidate_id: str, payload_sha256: str) -> dict[str, object]:
        directory = self.payload_dir(candidate_id, payload_sha256)
        with self._locked():
            active = self.active()
            if active and active.get("candidate_id") == candidate_id and active.get("payload_sha256") == payload_sha256:
                raise ModelCacheError("refusing to remove the active payload")
            lease_dir = self.lease_root / candidate_id / payload_sha256
            if lease_dir.is_dir() and any(lease_dir.iterdir()):
                raise ModelCacheError("refusing to remove a leased payload")
            if directory.exists():
                shutil.rmtree(directory)
            return {"candidate_id": candidate_id, "payload_sha256": payload_sha256, "removed": True}

    @contextlib.contextmanager
    def lease(self, candidate_id: str, payload_sha256: str, owner_id: str) -> Iterator[Path]:
        result = self.verify(candidate_id, payload_sha256, full=False)
        marker = self.lease_root / candidate_id / payload_sha256 / f"{owner_id}.json"
        _atomic_json(marker, {"owner_id": owner_id, "payload_sha256": payload_sha256})
        try:
            yield Path(str(result["payload_path"]))
        finally:
            marker.unlink(missing_ok=True)
