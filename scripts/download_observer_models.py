#!/usr/bin/env python3
"""Download pinned observer GGUFs into durable CORE4 model storage."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile


BLOCK = Path("/mnt/block")
DEFAULT_ROOT = BLOCK / "core4-models"
MODELS = (
    {
        "model_id": "qwen3.6-35b-a3b-q4-k-m",
        "repo": "ggml-org/Qwen3.6-35B-A3B-GGUF",
        "revision": "baec3ebee244827cda0f4557eafa8b28f7545fa6",
        "filename": "Qwen3.6-35B-A3B-Q4_K_M.gguf",
        "size": 20419565568,
        "sha256": "671e47e0ec53c665d048b98c3ecbfd5236b5ca9c3e02ed19fc8f81f7b85140c7",
    },
    {
        "model_id": "qwen3.8-27b-q4-k-m",
        "repo": "ggml-org/Qwen3.8-27B-GGUF",
        "revision": "71bc7b627595dc8a91039addd9c791ae548d6747",
        "filename": "Qwen3.8-27B-Q4_K_M.gguf",
        "size": 18973870528,
        "sha256": "c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747",
    },
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def valid_model(path: Path, spec: dict) -> bool:
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    with path.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            return False
    return digest(path) == spec["sha256"]


def reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing symlink in model destination: {path}")


def reject_hf_local_cache(directory: Path) -> None:
    for path in (directory / ".cache", directory / ".cache" / "huggingface",
                 directory / ".cache" / "huggingface" / "download"):
        reject_symlink(path)


def fsync_file_and_parent(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def cli_command(binary: str, spec: dict, directory: Path) -> list[str]:
    return [binary, "download", spec["repo"], spec["filename"],
            "--revision", spec["revision"], "--local-dir", str(directory)]


def cache_environment(root: Path) -> dict[str, str]:
    cache = root / ".huggingface-cache"
    reject_symlink(cache)
    hub = cache / "hub"
    xet = cache / "xet"
    reject_symlink(hub)
    reject_symlink(xet)
    return {"HF_HUB_CACHE": str(hub), "HF_XET_CACHE": str(xet)}


def provenance(path: Path, spec: dict) -> None:
    record = {
        "model_id": spec["model_id"],
        "repository": spec["repo"],
        "revision": spec["revision"],
        "filename": spec["filename"],
        "size_bytes": spec["size"],
        "sha256": spec["sha256"],
        "source_url": (
            f"https://huggingface.co/{spec['repo']}/resolve/"
            f"{spec['revision']}/{spec['filename']}"
        ),
    }
    target = path.parent / "download-provenance.json"
    fd, temp_name = tempfile.mkstemp(prefix=".provenance-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help=f"destination beneath mounted {BLOCK} (default: {DEFAULT_ROOT})")
    parser.add_argument("--dry-run", action="store_true", help="show pinned hf commands without downloading")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    block = BLOCK.resolve()
    if not os.path.ismount(BLOCK):
        parser.error(f"{BLOCK} is not a mounted filesystem; refusing to write to SSD")
    if os.path.commonpath((str(block), str(root))) != str(block):
        parser.error(f"destination must be beneath {block}")

    print(f"Destination: {root}")
    print(f"HF_HUB_CACHE: {root / '.huggingface-cache' / 'hub'}")
    print(f"HF_XET_CACHE: {root / '.huggingface-cache' / 'xet'}")
    hf = shutil.which("hf") or "hf"
    for spec in MODELS:
        directory = root / spec["model_id"]
        target = directory / spec["filename"]
        print(f"{spec['model_id']}: {shlex.join(cli_command(hf, spec, directory))}")
        print(f"  file: {target} ({spec['size']} bytes, SHA-256 {spec['sha256']})")
    if args.dry_run:
        print(f"Total pinned size: {sum(m['size'] for m in MODELS)} bytes")
        return 0

    hf = shutil.which("hf")
    if hf is None:
        raise RuntimeError("Hugging Face CLI `hf` is not installed or not on PATH")
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".download-observer-models.lock"
    reject_symlink(lock_path)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        needed = 0
        for spec in MODELS:
            directory = root / spec["model_id"]
            target = directory / spec["filename"]
            reject_symlink(directory)
            reject_symlink(target)
            reject_hf_local_cache(directory)
            if target.exists():
                if valid_model(target, spec):
                    continue
                raise RuntimeError(f"existing file fails pinned size/hash/GGUF checks; refusing overwrite: {target}")
            needed += spec["size"]
        cache_env = cache_environment(root)
        free = shutil.disk_usage(root).free
        if free < needed:
            raise RuntimeError(f"insufficient free space: need {needed} bytes, available {free}")

        for spec in MODELS:
            directory = root / spec["model_id"]
            target = directory / spec["filename"]
            reject_symlink(directory)
            directory.mkdir(parents=True, exist_ok=True)
            reject_symlink(target)
            reject_hf_local_cache(directory)
            if target.exists():
                print(f"Verified existing file: {target}")
                fsync_file_and_parent(target)
                provenance(target, spec)
                continue
            for directory_path in (root / ".huggingface-cache",
                                   root / ".huggingface-cache" / "hub",
                                   root / ".huggingface-cache" / "xet"):
                reject_symlink(directory_path)
                directory_path.mkdir(parents=True, exist_ok=True)
            subprocess.run(cli_command(hf, spec, directory), check=True,
                           env={**os.environ, **cache_env})
            reject_symlink(target)
            reject_hf_local_cache(directory)
            if not valid_model(target, spec):
                raise RuntimeError(f"hf download returned without a valid pinned model at {target}")
            fsync_file_and_parent(target)
            provenance(target, spec)
            print(f"Verified and installed: {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"download failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
