from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any


ROLES = {"explain", "debug", "review", "test_plan"}
INTENTS = {"understand", "debug", "test", "api", "performance"}
REQUEST_KEYS = {
    "format", "schema_version", "backend", "role", "readonly", "repo_root",
    "child_token", "objective", "scopes", "target", "intent",
    "budget_tokens", "max_items",
}
REQUEST_V2_KEYS = REQUEST_KEYS | {"execution", "context_packet"}


class WorkerError(RuntimeError):
    pass


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkerError("scopes must be non-empty repository-relative paths")
    path = PurePosixPath(value.replace("\\", "/"))
    normalized = str(path)
    if path.is_absolute() or normalized in {".", ".."} or ".." in path.parts:
        raise WorkerError(f"scope is not repository-relative: {value!r}")
    return normalized


def normalize_request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkerError("request must be a JSON object")
    version = value.get("schema_version")
    keys = REQUEST_V2_KEYS if version == 2 else REQUEST_KEYS
    extra = sorted(set(value) - keys)
    required = keys - ({"context_packet"} if version == 2 else set())
    missing = sorted(required - set(value))
    if extra or missing:
        raise WorkerError(f"request fields invalid: missing={missing} extra={extra}")
    if version not in {1, 2} or value.get("format") != f"LCW-REQUEST/{version}":
        raise WorkerError("request format must be LCW-REQUEST/1 or LCW-REQUEST/2")
    if value["backend"] not in ({"fake"} if version == 1 else {"fake", "real"}):
        raise WorkerError("request backend is unsupported for its version")
    if value["role"] not in ROLES or value["intent"] not in INTENTS:
        raise WorkerError("unsupported read-only role or ctxpp intent")
    if value["readonly"] is not True:
        raise WorkerError("CORE4 read-only MVP requires readonly=true")
    if not isinstance(value["child_token"], str) or not value["child_token"].startswith("toch_"):
        raise WorkerError("a restricted todo child token is required")
    for key, limit in (("objective", 500), ("target", 300)):
        allow_empty = key == "target" and version == 2
        if not isinstance(value[key], str) or (not allow_empty and not value[key].strip()) or len(value[key]) > limit:
            raise WorkerError(f"{key} must contain 1-{limit} characters")
    if not isinstance(value["repo_root"], str) or not value["repo_root"]:
        raise WorkerError("repo_root must be a non-empty path")
    root = Path(value["repo_root"]).resolve()
    if not root.is_dir():
        raise WorkerError(f"repo_root does not exist: {root}")
    scopes = value["scopes"]
    if not isinstance(scopes, list) or not 1 <= len(scopes) <= 16:
        raise WorkerError("scopes must contain 1-16 paths")
    normalized_scopes = sorted({_relative(item) for item in scopes})
    for scope in normalized_scopes:
        path = root / scope
        if not path.exists() or path.is_symlink():
            raise WorkerError(f"scope must exist and may not be a symlink: {scope}")
    budget = value["budget_tokens"]
    max_items = value["max_items"]
    if isinstance(budget, bool) or not isinstance(budget, int) or not 256 <= budget <= 12000:
        raise WorkerError("budget_tokens must be between 256 and 12000")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 32:
        raise WorkerError("max_items must be between 1 and 32")
    result = dict(value)
    result["repo_root"] = str(root)
    result["scopes"] = normalized_scopes
    if version == 2:
        execution = value.get("execution")
        if not isinstance(execution, dict):
            raise WorkerError("execution must be an object")
        if set(execution) - {"backend", "harness", "gpu_count", "service_profile", "harness_config", "admission_id"}:
            raise WorkerError("execution contains unknown fields")
        if execution.get("backend") != value["backend"]:
            raise WorkerError("execution backend must match request backend")
        if execution.get("harness", "qwen") not in {"qwen", "codex"}:
            raise WorkerError("execution harness must be qwen or codex")
        count = execution.get("gpu_count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 16:
            raise WorkerError("execution gpu_count must be between 1 and 16")
        if "context_packet" in value and not isinstance(value["context_packet"], dict):
            raise WorkerError("context_packet must be an object")
    return result


def eligibility(request: object) -> dict[str, Any]:
    try:
        normalized = normalize_request(request)
    except WorkerError as error:
        return {"format": "LCW-ELIGIBILITY/1", "eligible": False, "reasons": [str(error)]}
    return {
        "format": "LCW-ELIGIBILITY/1",
        "eligible": True,
        "reasons": [],
        "role": normalized["role"],
        "backend": normalized["backend"],
        "readonly": True,
    }


def _run_json(command: list[str], cwd: Path) -> dict[str, Any]:
    process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise WorkerError(f"command failed ({process.returncode}): {detail[:500]}")
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise WorkerError("command did not return JSON") from error
    if isinstance(value, dict) and value.get("ok") is True and isinstance(value.get("data"), dict):
        return value["data"]
    if not isinstance(value, dict):
        raise WorkerError("command JSON must be an object")
    return value


def _inside(path: str, scope: str) -> bool:
    return path == scope or path.startswith(scope + "/")


def _validate_prepared_packet(root: Path, request: dict[str, Any], packet: dict[str, Any]) -> None:
    if packet.get("format") != "CTXPP-CONTEXT-PACKET/2" or packet.get("readonly") is not True:
        raise WorkerError("prepared ctxpp packet format is invalid")
    claimed_hash = str(packet.get("packet_hash", ""))
    unhashed = dict(packet)
    unhashed.pop("packet_hash", None)
    actual_hash = hashlib.sha256(
        json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if claimed_hash != actual_hash:
        raise WorkerError("prepared ctxpp packet hash is invalid")
    trust = packet.get("trust") if isinstance(packet.get("trust"), dict) else {}
    if (
        trust.get("freshness") != "hash-verified"
        or trust.get("relationships") != "semantic"
        or trust.get("missing_required")
        or request["role"] not in list(trust.get("sufficient_for") or [])
        or packet.get("budget_exceeded") is not False
    ):
        raise WorkerError("prepared ctxpp packet is not usable for the delegated role")
    targets = packet.get("canonical_targets")
    if not isinstance(targets, list) or not targets:
        raise WorkerError("prepared ctxpp packet has no canonical target")
    for target in targets:
        location = target.get("location") if isinstance(target, dict) and isinstance(target.get("location"), dict) else {}
        relative = str(location.get("path", ""))
        if not relative or not any(_inside(relative, scope) for scope in request["scopes"]):
            raise WorkerError("prepared ctxpp target exceeds delegated scopes")
        source = root / relative
        expected = str(location.get("content_sha256", ""))
        if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise WorkerError("prepared ctxpp target changed before worker execution")


def _evidence_root(root: Path, child_token: str) -> Path:
    process = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=root, text=True, capture_output=True, check=False)
    if process.returncode:
        raise WorkerError("cannot resolve Git common directory for worker evidence")
    common = Path(process.stdout.strip())
    if not common.is_absolute():
        common = root / common
    digest = hashlib.sha256(child_token.encode("utf-8")).hexdigest()[:24]
    destination = common.resolve() / "local-coding-worker/executions" / digest
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _write_evidence(path: Path, value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded) > 256 * 1024:
        raise WorkerError(f"bounded evidence exceeded for {path.name}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)
    return str(path)


class ReadOnlySnapshot:
    def __init__(self, repo_root: Path, scopes: list[str]):
        self.repo_root = repo_root
        self.scopes = scopes
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.paths = 0

    def __enter__(self) -> "ReadOnlySnapshot":
        self._temp = tempfile.TemporaryDirectory(prefix="local-worker-readonly-")
        self.root = Path(self._temp.name) / "repo"
        self.root.mkdir()
        for scope in self.scopes:
            source = self.repo_root / scope
            destination = self.root / scope
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                if any(path.is_symlink() for path in source.rglob("*")):
                    raise WorkerError(f"scope contains a symlink: {scope}")
                shutil.copytree(source, destination, symlinks=False)
            else:
                shutil.copy2(source, destination)
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_symlink():
                raise WorkerError("snapshot contains a symlink")
            mode = path.stat().st_mode
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            self.paths += 1
        self.root.chmod(self.root.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        self.paths += 1
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.root and self.root.exists():
            for path in [self.root, *self.root.rglob("*")]:
                try:
                    path.chmod(path.stat().st_mode | stat.S_IWUSR)
                except OSError:
                    pass
        if self._temp:
            self._temp.cleanup()


def _fake_backend(request: dict[str, Any], packet: dict[str, Any], snapshot: ReadOnlySnapshot) -> dict[str, Any]:
    if snapshot.root is None:
        raise WorkerError("read-only snapshot was not initialized")
    target = packet.get("target") or next(iter(packet.get("canonical_targets", [])), {})
    location = target.get("location", {})
    target_path = str(location.get("path", ""))
    if not target_path or not any(_inside(target_path, scope) for scope in request["scopes"]):
        return {"status": "needs_codex", "summary": "Target is outside the authorized snapshot scopes.", "findings": []}
    copied_target = snapshot.root / target_path
    expected_hash = str(location.get("content_sha256", ""))
    if not copied_target.is_file() or hashlib.sha256(copied_target.read_bytes()).hexdigest() != expected_hash:
        return {"status": "needs_codex", "summary": "Snapshot target does not match the ctxpp packet source hash.", "findings": []}
    writable = [path for path in [snapshot.root, *snapshot.root.rglob("*")] if path.stat().st_mode & 0o222]
    if writable:
        raise WorkerError("read-only snapshot contains writable paths")
    trust = packet.get("trust", {})
    coverage = packet.get("coverage", {})
    sufficient = ((trust.get("target_range") == "hash-verified"
                   and trust.get("relationships") == "semantic"
                   and not trust.get("index_incomplete", True)
                   and coverage.get("sufficient") is True)
                  or (request["schema_version"] == 2
                      and request["role"] in trust.get("sufficient_for", [])
                      and not trust.get("missing_required")
                      and packet.get("budget_exceeded") is False))
    if not sufficient:
        return {
            "status": "needs_codex",
            "summary": "NEEDS_CODEX: packet trust or coverage is insufficient for bounded local judgment.",
            "findings": [f"Inspect canonical target {target_path} and requested expansion handles."],
        }
    return {
        "status": "no_change",
        "summary": f"Read-only {request['role']} completed against a hash-verified packet.",
        "findings": [
            f"Target: {target['name']}",
            f"Canonical range: {target_path}:{location.get('line')}-{location.get('end_line')}",
            "Snapshot exposed no writable paths.",
        ],
    }


def run_controller(request_value: object, *, production_runtime: object | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    request = normalize_request(request_value)
    root = Path(request["repo_root"])
    skills_value = os.environ.get("PROJECT_CONTROL_SKILLS_ROOT")
    skills_root = Path(skills_value).expanduser().resolve() if skills_value else None

    def configured_tool(variable: str, relative: str) -> Path:
        value = os.environ.get(variable)
        if value:
            return Path(value).expanduser().resolve()
        if skills_root is None:
            raise WorkerError(
                "PROJECT_CONTROL_SKILLS_ROOT or an explicit domain-tool path must be configured"
            )
        return skills_root / relative

    todo_cli = configured_tool("LCW_TODO_CLI", "todo-orchestrator/scripts/todo.py")
    ctxpp_cli = configured_tool("LCW_CTXPP_CLI", "cpp-context-compiler/scripts/ctxpp")
    heartbeat_command = [
        sys.executable, str(todo_cli), "child", "heartbeat", "--repo-root", str(root),
        "--child-token", request["child_token"], "--lease-seconds", "300", "--json",
    ]
    _run_json(heartbeat_command, root)
    stop_heartbeat = threading.Event()
    heartbeat_failure: list[Exception] = []
    def heartbeat() -> None:
        while not stop_heartbeat.wait(60):
            try:
                _run_json(heartbeat_command, root)
            except Exception as error:
                heartbeat_failure.append(error)
                return
    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
      if request["schema_version"] == 2 and isinstance(request.get("context_packet"), dict):
        packet = request["context_packet"]
        _validate_prepared_packet(root, request, packet)
      elif request["schema_version"] == 2:
        task_spec = {"objective": request["objective"], "role": request["role"],
            "read_paths": request["scopes"], "write_paths": [], "forbidden_paths": [],
            "target_symbols": [request["target"]] if request["target"] else [], "failing_tests": [], "interface_ids": [],
            "acceptance_gates": []}
        task_spec_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="local-worker-task-", suffix=".json", delete=False,
            ) as task_spec_file:
                json.dump(task_spec, task_spec_file, separators=(",", ":"))
                task_spec_path = Path(task_spec_file.name)
            packet_argv = [str(ctxpp_cli), "--root", str(root), "--json", "packet",
                           "--task-spec", str(task_spec_path), "--consumer", "local-worker",
                           "--budget", str(request["budget_tokens"]), "--max-items", str(request["max_items"])]
            packet = _run_json(packet_argv, root)
        finally:
            if task_spec_path is not None:
                task_spec_path.unlink(missing_ok=True)
      else:
        packet_argv = [str(ctxpp_cli), "--root", str(root), "--json", "packet", request["target"],
                       "--intent", request["intent"], "--budget", str(request["budget_tokens"]),
                       "--max-items", str(request["max_items"])]
        packet = _run_json(packet_argv, root)
      if packet.get("format") not in {"CTXPP-CONTEXT-PACKET/1", "CTXPP-CONTEXT-PACKET/2"} or packet.get("readonly") is not True:
        raise WorkerError("ctxpp did not return a read-only context packet")
      with ReadOnlySnapshot(root, request["scopes"]) as snapshot:
        if request["backend"] == "fake":
            backend = _fake_backend(request, packet, snapshot)
        else:
            if production_runtime is None:
                receiver_root = Path(__file__).resolve().parents[1]
                if str(receiver_root) not in sys.path:
                    sys.path.insert(0, str(receiver_root))
                from local_worker.controller import ProductionReadOnlyRuntime
                production_runtime = ProductionReadOnlyRuntime()
            execute = getattr(production_runtime, "execute", None)
            if not callable(execute):
                raise WorkerError("production runtime must implement execute")
            backend = execute(request, packet, snapshot)
            if not isinstance(backend, dict):
                raise WorkerError("production runtime result must be an object")
        snapshot_paths = snapshot.paths
      if heartbeat_failure:
        raise WorkerError(f"child heartbeat failed: {heartbeat_failure[0]}")
      status = str(backend["status"])
      if status not in {"completed", "no_change", "needs_codex", "failed", "preempted"}:
        raise WorkerError("worker backend returned an unsupported status")
      telemetry = {
        "backend_calls": 1, "tool_calls": int(backend.get("tool_calls", 3)),
        "packet_tokens": int(packet.get("estimated_tokens", 0)), "snapshot_paths": snapshot_paths,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
      }
      result = {
        "format": f"LOCAL-CODING-WORKER-RESULT/{request['schema_version']}",
        "schema_version": request["schema_version"], "status": status, "role": request["role"],
        "summary": str(backend["summary"])[:500],
        "findings": [str(item)[:500] for item in backend.get("findings", [])[:16]],
        "changed_paths": [], "packet_hash": packet.get("packet_hash"),
        "source_identity": packet.get("source_identity"), "telemetry": telemetry,
        "child_reported": True,
        **({"backend": request["backend"], "artifacts": list(backend.get("artifacts", [])),
            **({"model_outcome": backend["model_outcome"]} if isinstance(backend.get("model_outcome"), dict) else {})}
           if request["schema_version"] == 2 else {}),
      }
      references = {}
      if request["schema_version"] == 2:
        evidence_root = _evidence_root(root, request["child_token"])
        references = {
          "context_packet": _write_evidence(evidence_root / "context-packet.json", packet),
          "telemetry": _write_evidence(evidence_root / "telemetry.json", telemetry),
          "compact_logs": _write_evidence(evidence_root / "worker-result.json", result),
        }
        if isinstance(backend.get("model_outcome"), dict):
          references["reviewer_evidence"] = _write_evidence(evidence_root / "model-outcome.json", backend["model_outcome"])
      todo_status = "needs_codex" if status in {"needs_codex", "preempted"} else "failed" if status == "failed" else "succeeded"
      _run_json([
        sys.executable, str(todo_cli), "child", "report", "--repo-root", str(root),
        "--child-token", request["child_token"], "--status", todo_status,
        "--summary", str(backend["summary"])[:500],
        *[item for name, path in sorted(references.items()) for item in (f"--{name.replace('_', '-')}-ref", path)],
        "--json",
      ], root)
      return result
    finally:
      stop_heartbeat.set()
      heartbeat_thread.join(timeout=2)
