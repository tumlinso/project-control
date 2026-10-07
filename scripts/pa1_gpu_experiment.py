#!/usr/bin/env python3
"""Prepare and execute one root-authorized PA1 CUDA correctness experiment.

This CLI has no model interface. ``prepare`` persists a private effect intent
and emits a CUDA foreground-controller spec. ``--go-real`` is the controller
target and fails closed unless both the root-owned release marker and the
active four-GPU foreground receipt match the typed request.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = Path("/home/tumlinson/.agents/skills/cuda")
CUDA_TOOLKIT = Path("/opt/nvidia/hpc_sdk/Linux_x86_64/26.1/cuda/12.9")
SMOKE_SOURCE = Path(__file__).with_name("pa1_gpu_smoke.cu").resolve()
REQUEST_FORMAT = "PC-PA1-GPU-EXPERIMENT/1"
RELEASE_FORMAT = "PC-PA1-VERIFIED-GPU-RELEASE/1"
INTENT_FORMAT = "PC-PA1-GPU-EFFECT-INTENT/1"
MAX_WALL_SECONDS = 60
MAX_CPU_THREADS = 2
MAX_CAPTURE_BYTES = 64 * 1024


class ExperimentError(ValueError):
    """A GPU experiment request or its authority evidence is invalid."""


@dataclass(frozen=True, slots=True)
class TargetIdentity:
    target_id: str
    session_id: str
    daemon_epoch: str
    slot_id: str
    gpu_uuids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "session_id": self.session_id,
            "daemon_epoch": self.daemon_epoch,
            "slot_id": self.slot_id,
            "gpu_uuids": list(self.gpu_uuids),
            "state": "released_verified",
        }


@dataclass(frozen=True, slots=True)
class ExperimentRequest:
    task_id: str
    experiment_id: str
    attempt_id: str
    hypothesis: str
    attempt_dir: Path
    source_snapshot_path: Path
    source_manifest_sha256: str
    source_artifact_sha256: str
    gpu_uuids: tuple[str, ...]
    release_marker_path: Path
    release_marker_sha256: str
    release_request_id: str
    release_proof_digest: str
    targets: tuple[TargetIdentity, ...]
    wall_seconds: int
    cpu_threads: int

    @property
    def source_path(self) -> Path:
        return self.source_snapshot_path / "scripts" / "pa1_gpu_smoke.cu"

    @property
    def manifest_path(self) -> Path:
        return self.source_snapshot_path / ".lab-snapshot.json"

    @property
    def binary_path(self) -> Path:
        return self.attempt_dir / "pa1_gpu_smoke"


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ExperimentError("non-finite JSON number")


def _load_json(path: Path) -> tuple[dict[str, object], bytes]:
    if path.is_symlink():
        raise ExperimentError("JSON path is a symlink")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError("JSON input is unavailable or malformed") from exc
    if not isinstance(payload, dict):
        raise ExperimentError("JSON input must be an object")
    return payload, raw


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ExperimentError(f"{field} must be a UUID")
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ExperimentError(f"{field} must be a UUID") from exc
    if normalized != value.lower():
        raise ExperimentError(f"{field} must use canonical UUID form")
    return normalized


def _gpu_uuid(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("GPU-"):
        raise ExperimentError("GPU identities must be full NVIDIA UUIDs")
    try:
        parsed = str(uuid.UUID(value[4:]))
    except (ValueError, AttributeError) as exc:
        raise ExperimentError("GPU identities must be full NVIDIA UUIDs") from exc
    normalized = f"GPU-{parsed}"
    if value.lower() != normalized.lower():
        raise ExperimentError("GPU identities must be canonical")
    return normalized


def _private_directory(path: Path, *, create: bool = False) -> Path:
    if not path.is_absolute():
        raise ExperimentError("private paths must be absolute")
    if any(part in {".", ".."} for part in path.parts) or path != path.resolve(strict=False):
        raise ExperimentError("private directory path must be canonical")
    if path == REPO_ROOT or path.is_relative_to(REPO_ROOT):
        raise ExperimentError("private attempt paths must be outside the canonical repository")
    if create and not path.exists():
        parent = path.parent
        _private_directory(parent)
        path.mkdir(mode=0o700)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise ExperimentError("private directory is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ExperimentError("private path contains a symlink or non-directory")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ExperimentError("private directory must be user-owned and mode 0700")
    return path


def _private_file(path: Path, *, mode: int = 0o600) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ExperimentError("private file path is invalid")
    if path != path.resolve(strict=True):
        raise ExperimentError("private file path contains a symlink")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ExperimentError("private file must be user-owned and inaccessible to group and others")
    if stat.S_IMODE(info.st_mode) != mode:
        raise ExperimentError("private file mode does not match its required mode")
    return path


def _parse_target(value: object) -> TargetIdentity:
    if not isinstance(value, dict):
        raise ExperimentError("release targets must be objects")
    keys = ("target_id", "session_id", "daemon_epoch", "slot_id")
    identities = []
    for key in keys:
        raw = value.get(key)
        if not isinstance(raw, str) or not raw or len(raw) > 128:
            raise ExperimentError(f"release target {key} is invalid")
        identities.append(raw)
    raw_uuids = value.get("gpu_uuids")
    if not isinstance(raw_uuids, list) or len(raw_uuids) != 2:
        raise ExperimentError("each release target must identify one exact GPU pair")
    gpus = tuple(sorted(_gpu_uuid(item) for item in raw_uuids))
    if len(set(gpus)) != 2:
        raise ExperimentError("release target GPU identities must be distinct")
    return TargetIdentity(*identities, gpus)


def load_request(path: Path) -> tuple[ExperimentRequest, bytes]:
    payload, raw = _load_json(path)
    required = {
        "format", "task_id", "experiment_id", "attempt_id", "hypothesis", "attempt_dir",
        "source_snapshot_path", "source_manifest_sha256", "source_artifact_sha256", "gpu_uuids",
        "release_marker_path", "release_marker_sha256", "release_request_id", "release_proof_digest",
        "release_targets", "wall_seconds", "cpu_threads",
    }
    if set(payload) != required or payload.get("format") != REQUEST_FORMAT:
        raise ExperimentError("request schema does not match PC-PA1-GPU-EXPERIMENT/1")
    task_id, hypothesis = payload["task_id"], payload["hypothesis"]
    if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
        raise ExperimentError("task_id is invalid")
    if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 512:
        raise ExperimentError("hypothesis must be explicit and at most 512 characters")
    experiment_id = _uuid(payload["experiment_id"], "experiment_id")
    attempt_id = _uuid(payload["attempt_id"], "attempt_id")
    attempt_dir = Path(str(payload["attempt_dir"])).expanduser()
    source_snapshot_path = Path(str(payload["source_snapshot_path"])).expanduser()
    release_marker_path = Path(str(payload["release_marker_path"])).expanduser()
    for key in ("source_manifest_sha256", "source_artifact_sha256", "release_marker_sha256", "release_proof_digest"):
        if not _is_digest(payload[key]):
            raise ExperimentError(f"{key} must be a lowercase SHA-256 digest")
    gpu_values = payload["gpu_uuids"]
    if not isinstance(gpu_values, list) or len(gpu_values) != 4:
        raise ExperimentError("exactly four GPU UUIDs are required")
    gpus = tuple(sorted(_gpu_uuid(item) for item in gpu_values))
    if len(set(gpus)) != 4:
        raise ExperimentError("four distinct GPU UUIDs are required")
    raw_targets = payload["release_targets"]
    if not isinstance(raw_targets, list) or len(raw_targets) != 2:
        raise ExperimentError("release proof must contain exactly two owned target pairs")
    targets = tuple(sorted((_parse_target(item) for item in raw_targets), key=lambda item: item.target_id))
    flattened = tuple(sorted(gpu for target in targets for gpu in target.gpu_uuids))
    if flattened != gpus:
        raise ExperimentError("release target identities must cover exactly the four requested GPUs")
    wall = payload["wall_seconds"]
    cpu = payload["cpu_threads"]
    if isinstance(wall, bool) or not isinstance(wall, int) or not 1 <= wall <= MAX_WALL_SECONDS:
        raise ExperimentError("wall_seconds must be in 1..60")
    if isinstance(cpu, bool) or not isinstance(cpu, int) or not 1 <= cpu <= MAX_CPU_THREADS:
        raise ExperimentError("cpu_threads must be in 1..2")
    request = ExperimentRequest(
        task_id, experiment_id, attempt_id, hypothesis.strip(), attempt_dir,
        source_snapshot_path, str(payload["source_manifest_sha256"]),
        str(payload["source_artifact_sha256"]), gpus, release_marker_path,
        str(payload["release_marker_sha256"]),
        str(payload["release_request_id"]), str(payload["release_proof_digest"]),
        targets, wall, cpu,
    )
    if not request.release_request_id or len(request.release_request_id) > 128:
        raise ExperimentError("release_request_id is invalid")
    return request, raw


def _validate_source(request: ExperimentRequest) -> None:
    _private_directory(request.attempt_dir)
    _private_directory(request.source_snapshot_path)
    if (request.attempt_dir == request.source_snapshot_path
            or request.attempt_dir.is_relative_to(request.source_snapshot_path)
            or request.source_snapshot_path.is_relative_to(request.attempt_dir)):
        raise ExperimentError("attempt output and detached source must be separate")
    if not request.source_path.is_file() or request.source_path.is_symlink():
        raise ExperimentError("detached CUDA fixture source is unavailable")
    if (request.source_path.parent.is_symlink()
            or not request.source_path.resolve(strict=True).is_relative_to(request.source_snapshot_path.resolve(strict=True))):
        raise ExperimentError("detached CUDA fixture source escapes its source snapshot")
    _private_file(request.manifest_path)
    if _sha256(request.manifest_path) != request.source_manifest_sha256:
        raise ExperimentError("detached source manifest digest changed")
    if _sha256(request.source_path) != request.source_artifact_sha256:
        raise ExperimentError("detached CUDA fixture source digest changed")
    manifest, _ = _load_json(request.manifest_path)
    files = manifest.get("files")
    entry = next((item for item in files if isinstance(item, dict) and item.get("path") == "scripts/pa1_gpu_smoke.cu"), None) if isinstance(files, list) else None
    if not isinstance(entry, dict) or entry.get("sha256") != request.source_artifact_sha256:
        raise ExperimentError("detached source manifest does not bind the CUDA fixture bytes")


def _target_dicts(targets: tuple[TargetIdentity, ...]) -> list[dict[str, object]]:
    return [target.as_dict() for target in targets]


def validate_release_marker(request: ExperimentRequest, marker_path: Path | None = None) -> dict[str, object]:
    path = marker_path or request.release_marker_path
    if path != request.release_marker_path:
        raise ExperimentError("release marker path differs from the typed request")
    if not path.is_relative_to(request.attempt_dir):
        raise ExperimentError("release marker must remain inside the private attempt directory")
    _private_directory(request.attempt_dir)
    _private_file(path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != request.release_marker_sha256:
        raise ExperimentError("root release marker digest does not match the typed request")
    try:
        marker = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExperimentError("root release marker is malformed") from exc
    marker_keys = {"format", "state", "experiment_id", "attempt_id", "source_manifest_sha256",
                   "source_artifact_sha256",
                   "release_request_id", "proof_digest", "issued_at", "valid_until", "targets"}
    if not isinstance(marker, dict) or set(marker) != marker_keys or marker.get("format") != RELEASE_FORMAT:
        raise ExperimentError("root release marker format is invalid")
    expected = {
        "state": "released_verified",
        "experiment_id": request.experiment_id,
        "attempt_id": request.attempt_id,
        "source_manifest_sha256": request.source_manifest_sha256,
        "source_artifact_sha256": request.source_artifact_sha256,
        "release_request_id": request.release_request_id,
        "proof_digest": request.release_proof_digest,
        "targets": _target_dicts(request.targets),
    }
    if any(marker.get(key) != value for key, value in expected.items()):
        raise ExperimentError("root release marker scope differs from the typed request")
    issued, expires = marker.get("issued_at"), marker.get("valid_until")
    now = time.time()
    if (isinstance(issued, bool) or not isinstance(issued, (int, float)) or not math.isfinite(float(issued))
            or isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(float(expires))
            or issued > now + 2 or expires <= issued or expires - issued > 60 or now > expires):
        raise ExperimentError("root release marker is expired or exceeds its 60-second validity")
    return marker


def load_foreground_receipt(expected_uuids: tuple[str, ...], expected_project: Path) -> dict[str, object]:
    raw_path = os.environ.get("TODO_GPU_LEASE_RECEIPT")
    if not raw_path:
        raise ExperimentError("TODO_GPU_LEASE_RECEIPT is required for --go-real")
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink():
        raise ExperimentError("active CUDA lease receipt path is invalid")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError) as exc:
        raise ExperimentError("active CUDA lease receipt is unavailable") from exc
    resource_ids = receipt.get("resource_ids") if isinstance(receipt, dict) else None
    try:
        observed = sorted(_gpu_uuid(item.removeprefix("accelerator:")) for item in resource_ids) if isinstance(resource_ids, list) and all(isinstance(item, str) for item in resource_ids) else []
    except ExperimentError:
        observed = []
    if (receipt.get("format") != "CUDA-FOREGROUND-LEASE/1" or receipt.get("state") != "active"
            or observed != sorted(expected_uuids) or len(observed) != 4
            or Path(str(receipt.get("project_root", ""))).resolve() != expected_project.resolve()):
        raise ExperimentError("active foreground receipt does not match the four requested GPU UUIDs")
    owner_id = receipt.get("owner_id")
    pid = receipt.get("pid")
    if not isinstance(owner_id, str) or not owner_id.startswith("foreground:"):
        raise ExperimentError("active foreground receipt has no controller owner identity")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        raise ExperimentError("active foreground receipt has no valid controller PID")
    try:
        os.kill(pid, 0)
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
    except OSError as exc:
        raise ExperimentError("foreground controller owner is not live") from exc
    if b"cuda_controller.py" not in command:
        raise ExperimentError("active lease owner is not the CUDA foreground controller")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if len(visible) != 4 or any(not value for value in visible):
        raise ExperimentError("controller visible-device count is not four")
    try:
        sample = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,index", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExperimentError("physical GPU identity sample is unavailable") from exc
    by_index = {}
    for line in sample.stdout.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 2:
            raise ExperimentError("physical GPU identity sample is malformed")
        by_index[fields[1]] = fields[0]
    visible_uuids = [value if value.startswith("GPU-") else by_index.get(value) for value in visible]
    try:
        visible_uuids = [_gpu_uuid(value) for value in visible_uuids]
    except ExperimentError as exc:
        raise ExperimentError("controller-visible GPU UUID mapping is malformed") from exc
    if sorted(visible_uuids) != sorted(expected_uuids):
        raise ExperimentError("controller-visible GPU UUIDs differ from the release scope")
    return {"receipt_path": str(path), "owner_id": owner_id, "pid": pid,
            "resource_ids": observed, "visible_gpu_uuids": visible_uuids}


def build_controller_spec(request: ExperimentRequest, request_path: Path) -> dict[str, object]:
    _validate_source(request)
    validate_release_marker(request)
    attempt = request.attempt_dir
    binary = request.binary_path
    if binary.exists() or binary.is_symlink():
        raise ExperimentError("standalone smoke binary path already exists")
    argv = [sys.executable, str(Path(__file__).resolve()), "--go-real", "--request", str(request_path)]
    return {
        "schema_version": 1,
        "project_root": str(REPO_ROOT),
        "command_cwd": str(REPO_ROOT),
        "_storage_root": str(attempt / "controller-state"),
        "recipe": "baseline",
        "toolchain": {"root": str(CUDA_TOOLKIT), "require_sanitizer": False},
        "benchmark": {"build_argv": [
            str(CUDA_TOOLKIT / "bin" / "nvcc"), "-std=c++17", "-O2", "-arch=sm_70",
            "-cudart=static", str(request.source_path), "-o", str(binary),
        ]},
        "argv": argv,
        "resources": {"gpus": 4, "gpu_uuids": list(request.gpu_uuids),
                      "cpu_threads": request.cpu_threads, "ram_bytes": 1_073_741_824},
        "timeout": request.wall_seconds,
        "build_timeout": 60,
        "paths": [],
    }


def _atomic_write(path: Path, payload: dict[str, object], *, create_only: bool = False) -> None:
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if create_only:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def prepare(request_path: Path) -> dict[str, object]:
    request, raw = load_request(request_path)
    _validate_source(request)
    request_path = request_path.resolve(strict=True)
    _private_file(request_path)
    if request_path.parent != request.attempt_dir:
        raise ExperimentError("typed request must be stored in the private attempt directory")
    validate_release_marker(request)
    intent_path = request.attempt_dir / "effect-intent.json"
    intent = {
        "format": INTENT_FORMAT,
        "state": "prepared",
        "task_id": request.task_id,
        "experiment_id": request.experiment_id,
        "attempt_id": request.attempt_id,
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": request.source_manifest_sha256,
        "source_artifact_sha256": request.source_artifact_sha256,
        "release_request_id": request.release_request_id,
        "release_proof_digest": request.release_proof_digest,
        "gpu_uuids": list(request.gpu_uuids),
        "targets": _target_dicts(request.targets),
        "created_at": time.time(),
    }
    spec = build_controller_spec(request, request_path.resolve())
    controller_state = request.attempt_dir / "controller-state"
    controller_state.mkdir(mode=0o700, exist_ok=True)
    _private_directory(controller_state)
    os.chmod(controller_state, 0o700)
    _atomic_write(intent_path, intent, create_only=True)
    spec_path = request.attempt_dir / "cuda-controller-spec.json"
    _atomic_write(spec_path, spec, create_only=True)
    return {"status": "prepared", "effect_intent": str(intent_path), "request_sha256": intent["request_sha256"],
            "controller_spec_path": str(spec_path), "controller_invocation": [
                sys.executable, str(SKILL_ROOT / "scripts" / "cuda_controller.py"), "run",
                "--spec", str(spec_path), "--json",
            ]}


def _read_intent(request: ExperimentRequest, request_path: Path, raw: bytes) -> tuple[Path, dict[str, object]]:
    path = request.attempt_dir / "effect-intent.json"
    _private_file(path)
    intent, _ = _load_json(path)
    if (intent.get("format") != INTENT_FORMAT or intent.get("state") != "prepared"
            or intent.get("experiment_id") != request.experiment_id
            or intent.get("attempt_id") != request.attempt_id
            or intent.get("request_sha256") != hashlib.sha256(raw).hexdigest()):
        raise ExperimentError("prepared effect intent does not match the typed request")
    return path, intent


def _proc_start_time(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return text.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _terminate_owned(process: subprocess.Popen[bytes], start_time: str | None) -> None:
    if process.poll() is not None:
        return
    pid = process.pid
    if _proc_start_time(pid) != start_time or start_time is None:
        process.kill()
        process.wait()
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        if _proc_start_time(pid) == start_time:
            process.kill()
        process.wait()
    except ProcessLookupError:
        process.wait()


def _run_bounded(binary: Path, visible: str, timeout: int) -> dict[str, object]:
    command = [str(binary), "--all-visible"]
    environment = {"PATH": "/usr/bin:/bin", "CUDA_VISIBLE_DEVICES": visible,
                   "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=binary.parent, env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=False)
    process_start = _proc_start_time(process.pid)
    interrupted: dict[str, int] = {}

    def forward_signal(signum, _frame):
        interrupted["signal"] = signum
        if process.poll() is None:
            try:
                if _proc_start_time(process.pid) == process_start:
                    process.send_signal(signum)
            except ProcessLookupError:
                pass

    prior_handlers = {number: signal.signal(number, forward_signal) for number in (signal.SIGTERM, signal.SIGINT)}
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = False
    overflow = False
    deadline = started + timeout
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or interrupted:
            timed_out = True
            _terminate_owned(process, process_start)
        for key, _ in selector.select(min(0.1, max(0.0, remaining)) if not timed_out else 0.05):
            chunk = os.read(key.fileobj.fileno(), 8192)
            if not chunk:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            sink = output[key.data]
            room = MAX_CAPTURE_BYTES - len(sink)
            if len(chunk) > room:
                sink.extend(chunk[:max(0, room)])
                overflow = True
                _terminate_owned(process, process_start)
            else:
                sink.extend(chunk)
        if timed_out or overflow:
            # Drain any already-buffered pipe bytes while respecting the cap.
            for key in list(selector.get_map().values()):
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                else:
                    sink = output[key.data]
                    sink.extend(chunk[:max(0, MAX_CAPTURE_BYTES - len(sink))])
            if process.poll() is not None and not selector.get_map():
                break
    for number, handler in prior_handlers.items():
        signal.signal(number, handler)
    return {"argv": command, "returncode": process.wait(), "timed_out": timed_out,
            "output_limit_exceeded": overflow, "elapsed_seconds": time.monotonic() - started,
            "stdout": bytes(output["stdout"]).decode("utf-8", errors="replace"),
            "stderr": bytes(output["stderr"]).decode("utf-8", errors="replace")}


def go_real(request_path: Path) -> dict[str, object]:
    request, raw = load_request(request_path)
    _validate_source(request)
    intent_path, intent = _read_intent(request, request_path, raw)
    marker = validate_release_marker(request)
    lease = load_foreground_receipt(request.gpu_uuids, REPO_ROOT)
    binary = request.binary_path
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise ExperimentError("controller-built standalone CUDA fixture is unavailable")
    binary_hash = _sha256(binary)
    marker = validate_release_marker(request)
    launched = time.time()
    intent.update({"state": "launching", "launch_started_at": launched,
                   "controller_pid": os.getppid(), "binary_sha256": binary_hash,
                   "lease_owner_id": lease["owner_id"]})
    _atomic_write(intent_path, intent)
    child_timeout = max(1, request.wall_seconds - 10)
    result = _run_bounded(binary, os.environ["CUDA_VISIBLE_DEVICES"], child_timeout)
    stdout = str(result.pop("stdout"))
    stderr = str(result.pop("stderr"))
    result_payload = {
        "format": "PC-PA1-GPU-EXPERIMENT-RESULT/1",
        "state": "completed" if result["returncode"] == 0 and not result["timed_out"] and not result["output_limit_exceeded"] else "failed",
        "task_id": request.task_id,
        "experiment_id": request.experiment_id,
        "attempt_id": request.attempt_id,
        "source_manifest_sha256": request.source_manifest_sha256,
        "source_artifact_sha256": request.source_artifact_sha256,
        "binary_sha256": binary_hash,
        "release_proof_digest": marker["proof_digest"],
        "lease_owner_id": lease["owner_id"],
        "leased_gpu_uuids": list(lease["resource_ids"]),
        "result": result,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "completed_at": time.time(),
        "child_timeout_seconds": child_timeout,
        "claim_limit": "correctness fixture only; no benchmark, speedup, or production qualification",
    }
    _atomic_write(request.attempt_dir / "effect-result.json", result_payload, create_only=True)
    intent.update({"state": result_payload["state"], "completed_at": result_payload["completed_at"],
                   "result_sha256": _sha256(request.attempt_dir / "effect-result.json")})
    _atomic_write(intent_path, intent)
    return {"status": result_payload["state"], "effect_result": str(request.attempt_dir / "effect-result.json"),
            "returncode": result["returncode"], "timed_out": result["timed_out"],
            "output_limit_exceeded": result["output_limit_exceeded"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="persist a private effect intent and emit controller spec")
    parser.add_argument("--dry-run", action="store_true", help="validate scope and emit the controller spec without writing")
    parser.add_argument("--go-real", action="store_true", help="run only as the target of the active CUDA controller")
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    if sum((args.prepare, args.dry_run, args.go_real)) != 1:
        parser.error("select exactly one of --dry-run, --prepare, or --go-real")
    try:
        if args.dry_run:
            request, _ = load_request(args.request)
            _validate_source(request)
            resolved_request = args.request.resolve(strict=True)
            _private_file(resolved_request)
            if resolved_request.parent != request.attempt_dir:
                raise ExperimentError("typed request must be stored in the private attempt directory")
            result = {"status": "dry_run", "controller_spec": build_controller_spec(request, resolved_request)}
        else:
            result = prepare(args.request) if args.prepare else go_real(args.request)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result.get("status") in {"dry_run", "prepared", "completed"} else 2
    except Exception as exc:
        print(json.dumps({"status": "refused", "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
