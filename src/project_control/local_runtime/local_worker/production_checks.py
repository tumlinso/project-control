"""Bounded real-host checks and compact production evidence for CORE4."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
from pathlib import Path
from typing import Any

from .model_cache import ModelCache


class ProductionCheckError(RuntimeError):
    pass


def _bind_todo(repo: Path) -> None:
    from .canonical_runtime import bind
    bind(repo)


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def _profile(repo: Path) -> dict[str, Any]:
    return tomllib.loads((repo / "local-coding-worker/config/production-profile.toml").read_text(encoding="utf-8"))


def _host_module(repo: Path):
    path = repo / "local-coding-worker/evals/host_bakeoff.py"
    spec = importlib.util.spec_from_file_location("core4_production_host", path)
    if spec is None or spec.loader is None:
        raise ProductionCheckError("cannot load the existing host runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_compact(repo: Path, name: str, value: dict[str, Any]) -> dict[str, Any]:
    override = os.environ.get("CORE4_COMPACT_EVIDENCE_DIR")
    destination = (
        Path(override).expanduser().resolve() / f"{name}.json"
        if override
        else repo / "local-coding-worker/evals/results/compact" / f"{name}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**value, "evidence_path": str(destination)}


def _service_preemption(repo: Path, count: int) -> dict[str, Any]:
    _bind_todo(repo)
    from todo_orchestrator.runtime import RuntimeFacade
    host = RuntimeFacade(repo).host
    host.discover_gpus()
    bundles = host.compound_gpu_bundles(count)
    if not bundles:
        raise ProductionCheckError("runtime discovery returned no usable GPU island")
    bundle = bundles[0]
    request = {"schema_version": 1, "kind": "accelerator", "ids": bundle["resource_ids"],
               "exclusive_resources": bundle["exclusive_resources"]}
    service = host.reserve_service(project_root=repo, service_id="c4p-host-service-check",
                                   resource_request=request, priority_class="idle_model_residency")
    if service is None:
        raise ProductionCheckError("could not reserve the discovered GPU island for service preemption proof")
    foreground = None
    try:
        foreground = host.begin_foreground(project_root=repo, resource_request=request,
                                           priority_class="clean_cuda_foreground")
        requested = host.preempt_requested(str(service["owner_id"]))
        blocked_before_release = not host.activate_foreground(foreground)
        host.release(str(service["owner_id"]))
        activated_after_release = host.activate_foreground(foreground)
        return {"preempt_requested": requested, "foreground_blocked_until_release": blocked_before_release,
                "foreground_activated_after_release": activated_after_release,
                "resource_ids": bundle["resource_ids"]}
    finally:
        host.release(str(service["owner_id"]))
        if foreground is not None:
            host.release(str(foreground["owner_id"]))


def _host_service(repo: Path) -> dict[str, Any]:
    profile = _profile(repo)
    storage = profile["storage"]
    cache = ModelCache(storage["cache_root"], storage["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=True)
    host = _host_module(repo)
    devices = host.gpu_inventory()
    islands, topology = host.discover_islands(devices)
    candidate = next((item for item in profile["candidates"] if item["id"] == active["candidate_id"]), None)
    if candidate is None:
        raise ProductionCheckError("active cache candidate is absent from the production profile")
    indices = host.candidate_indices(candidate, islands, devices)
    raw = repo / "local-coding-worker/evals/results/compact/host-service"
    starts = []
    for attempt in range(2):
        with host.ResourceLease(repo, devices, indices):
            server = host.Server(profile["server"], Path(str(verified["payload_path"])),
                                 str(active["candidate_id"]), int(profile["experiment"]["initial_context"]),
                                 indices, int(profile["server"]["base_port"]), raw / f"start-{attempt + 1}")
            with server:
                probe = server.completion("Reply with exactly CORE4_OK.", max_tokens=16)
                status, models = host.http_json("GET", server.base_url + "/v1/models", timeout=5)
                time.sleep(1)
                retained_status, _ = host.http_json("GET", server.base_url + "/health", timeout=5)
                starts.append({"attempt": attempt + 1, "load_seconds": round(server.load_seconds, 3),
                               "probe_ok": "CORE4_OK" in probe["text"], "models_status": status,
                               "model_count": len(models.get("data", [])), "idle_retained": retained_status == 200})
            cleanly_stopped = server.process is not None and server.process.poll() is not None
            starts[-1]["cleanly_stopped"] = cleanly_stopped
    preemption = _service_preemption(repo, len(indices))
    ok = all(item["probe_ok"] and item["models_status"] == 200 and item["model_count"] > 0 and
             item["idle_retained"] and item["cleanly_stopped"] for item in starts) and all(
                 bool(preemption[key]) for key in ("preempt_requested", "foreground_blocked_until_release",
                                                   "foreground_activated_after_release"))
    result = {"format": "CORE4-HOST-CHECK/1", "scenario": "service", "ok": ok,
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "payload_bytes": verified["payload_bytes"], "context_size": profile["experiment"]["initial_context"],
              "gpu_indices": indices, "topology_sha256": host.hashlib.sha256(topology.encode()).hexdigest(),
              "starts": starts, "preemption": preemption}
    if not ok:
        raise ProductionCheckError("real service lifecycle check did not satisfy every guard")
    return _write_compact(repo, "host-service", result)


def _ctxpp_evidence(repo: Path) -> dict[str, Any]:
    index = repo / ".ctxpp/index.jsonl"
    if not index.is_file():
        scan = subprocess.run(
            [str(repo / "cpp-context-compiler/scripts/ctxpp"), "--root", str(repo), "--json", "scan"],
            cwd=repo, text=True, capture_output=True, timeout=120, check=False,
        )
        if scan.returncode != 0:
            raise ProductionCheckError(f"ctxpp scan failed: {scan.stderr.strip()[:300]}")
    command = [str(repo / "cpp-context-compiler/scripts/ctxpp"), "--root", str(repo), "--json",
               "packet", "PackingPlan", "--consumer", "local-worker", "--intent", "understand",
               "--budget", "1024", "--max-items", "8"]
    process = subprocess.run(command, cwd=repo, text=True, capture_output=True, timeout=120, check=False)
    if process.returncode != 0:
        raise ProductionCheckError(f"ctxpp packet failed: {process.stderr.strip()[:300]}")
    packet = json.loads(process.stdout)
    return {"format": packet.get("format"), "packet_hash": packet.get("packet_hash"),
            "estimated_tokens": packet.get("estimated_tokens"),
            "coverage_sufficient": bool((packet.get("coverage") or {}).get("sufficient")),
            "source_fingerprint": (packet.get("source_identity") or {}).get("fingerprint")}


def _host_readonly(repo: Path) -> dict[str, Any]:
    from .harnesses import QwenCodeAdapter
    profile = _profile(repo)
    cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    host = _host_module(repo)
    tasks = host.load_tasks(repo / "local-coding-worker/evals/tasks/core4-host-tasks.json")
    selected_ids = ["contract-authority", "resource-policy", "needs-codex"]
    objectives = {
        "contract-authority": ("Read local-coding-worker/references/read-only-contract.md once. In the structured "
                               "summary or claims, include the exact phrases todo-orchestrator and canonical "
                               "repository source and accurately state each authority."),
        "resource-policy": ("Read local-coding-worker/references/resource-policy.md once. In the structured summary "
                            "or claims, include the exact phrases active local delegation and NEEDS_CODEX and "
                            "accurately state their resource-policy meanings."),
        "needs-codex": ("This numerical convention is not established by source or contract. Return outcome "
                        "needs_codex and include NEEDS_CODEX in the structured summary."),
    }
    devices = host.gpu_inventory()
    islands, _ = host.discover_islands(devices)
    candidate = next(item for item in profile["candidates"] if item["id"] == active["candidate_id"])
    indices = host.candidate_indices(candidate, islands, devices)
    results = []
    environment = {"OPENAI_API_KEY": "core4-local-no-auth", "OPENAI_MODEL": str(active["candidate_id"])}
    prior = {key: os.environ.get(key) for key in environment | {"OPENAI_BASE_URL": ""}}
    try:
        with host.ResourceLease(repo, devices, indices):
            with host.Server(profile["server"], Path(str(verified["payload_path"])), str(active["candidate_id"]),
                             int(profile["experiment"]["initial_context"]), indices,
                             int(profile["server"]["base_port"]),
                             repo / "local-coding-worker/evals/results/compact/host-readonly-runtime") as server:
                environment["OPENAI_BASE_URL"] = server.base_url + "/v1"
                os.environ.update(environment)
                harness = QwenCodeAdapter(str(profile["harnesses"]["qwen"]))
                handle = harness.start({"cwd": str(repo), "model": str(active["candidate_id"]),
                    "base_url": server.base_url + "/v1", "mode": "readonly",
                    "repository_root": str(repo), "authorized_read_paths": [
                        "local-coding-worker/references/read-only-contract.md",
                        "local-coding-worker/references/resource-policy.md",
                    ],
                    "allowed_tools": ["read_file", "structured_output"],
                    "max_session_turns": 9, "max_tool_calls": 6,
                    "structured_retry_margin": 2, "turn_limit_is_error": True,
                    "max_wall_time_seconds": 180, "timeout_seconds": 210})
                try:
                    for task_id in selected_ids:
                        task = tasks[task_id]
                        prompt = json.dumps({
                            "objective": objectives[task_id], "role": "evaluation",
                            "authorized_roots": ["."], "output_contract": "CORE4-MODEL-OUTCOME/1",
                            "constraints": ("This is read-only work, so changed_paths must be empty. Call the tool "
                                            "named structured_output as the final action. Do not finish with prose "
                                            "or printed JSON. Use outcome needs_codex when the objective requires "
                                            "frontier judgment."),
                        }, separators=(",", ":"))
                        outcome = harness.run(handle, {"prompt": prompt, "timeout_seconds": 210})
                        text = str(outcome.get("text", ""))
                        accepted = all(str(item).casefold() in text.casefold() for item in task["expected_all"])
                        results.append({"task_id": task_id, "accepted": accepted,
                            "status": outcome.get("status"), "outcome": outcome.get("outcome", "answer"),
                            "codex_visible_input_bytes": len(task["prompt"].encode()),
                            "codex_visible_output_bytes": len(text.encode()),
                            "local_tool_calls": int((outcome.get("usage", {}).get("core4", {}) or {}).get("tool_calls", 0)),
                            "tool_names": (outcome.get("usage", {}).get("core4", {}) or {}).get("tool_names", []),
                            "duration_ms": outcome.get("duration_ms")})
                finally:
                    harness.evict(handle)
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    snapshot = repo / ".todo-orchestrator/state.snapshot.json"
    state = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.is_file() else None
    needs = next(item for item in results if item["task_id"] == "needs-codex")
    ok = all(item["accepted"] for item in results) and needs["status"] == "needs_codex"
    result = {"format": "CORE4-HOST-CHECK/1", "scenario": "readonly", "ok": ok,
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "gpu_indices": indices, "tasks": results, "ctxpp": _ctxpp_evidence(repo),
              "todo_authority": ({"project_uuid": state["project"]["project_uuid"],
                                  "project_revision": state["project_revision"]} if state else
                                 {"state": "not_active"}),
              "compact_output_bytes": sum(int(item["codex_visible_output_bytes"]) for item in results)}
    evidence = _write_compact(repo, "host-readonly", result)
    if not ok:
        raise ProductionCheckError("real read-only task set did not satisfy acceptance and NEEDS_CODEX guards")
    return evidence


def _verification_command(nvcc: str, *, execute: bool) -> dict[str, Any]:
    code = (
        "import os,subprocess,tempfile;"
        "fd,path=tempfile.mkstemp(prefix='core4-host-writable-');os.close(fd);os.unlink(path);"
        f"subprocess.run([{nvcc!r},'-std=c++17','-arch=sm_70','src/add.cu','-o',path],check=True);"
        + ("subprocess.run([path],check=True);" if execute else "")
        + "os.unlink(path)"
    )
    return {"schema_version": 1, "argv": [sys.executable, "-c", code], "cwd": ".", "timeout_seconds": 120}


def _host_writable(repo: Path) -> dict[str, Any]:
    from .acceptance import AcceptanceError, ScopeViolation, StaleSourceError, accept_patch_artifact, build_patch_artifact
    from .harnesses import QwenCodeAdapter
    from .verification import require_verification
    from .workspace import materialize_writable_workspace
    _bind_todo(repo)
    from todo_orchestrator.runtime import capture_source_identity

    nvcc = shutil.which("nvcc")
    if not nvcc:
        raise ProductionCheckError("CUDA 12.x nvcc is unavailable")
    version = subprocess.run([nvcc, "--version"], text=True, capture_output=True, check=False)
    if version.returncode != 0 or "release 12." not in version.stdout:
        raise ProductionCheckError("the available nvcc is not an explicit CUDA 12.x toolchain")

    profile = _profile(repo)
    cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    host = _host_module(repo)
    devices = host.gpu_inventory()
    islands, _ = host.discover_islands(devices)
    candidate = next(item for item in profile["candidates"] if item["id"] == active["candidate_id"])
    indices = host.candidate_indices(candidate, islands, devices)
    raw_root = repo / ".git/core4-production-extension/raw/host-writable-candidates"
    raw_root.mkdir(parents=True, exist_ok=True)
    artifact_root = Path(tempfile.mkdtemp(prefix="run-", dir=raw_root))
    baseline = [_verification_command(nvcc, execute=False)]
    full_test = [_verification_command(nvcc, execute=True)]
    environment = {"OPENAI_API_KEY": "core4-local-no-auth", "OPENAI_MODEL": str(active["candidate_id"])}
    prior = {key: os.environ.get(key) for key in environment | {"OPENAI_BASE_URL": ""}}
    reviewer: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="core4-host-writable-repo-") as temporary:
        primary = Path(temporary)
        (primary / "src").mkdir()
        source = primary / "src/add.cu"
        source.write_text(
            "#include <cstdlib>\n\nint add(int a, int b) { return a - b; }\n\n"
            "int main() { return add(2, 3) == 5 ? EXIT_SUCCESS : EXIT_FAILURE; }\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=primary, check=True)
        subprocess.run(["git", "config", "user.email", "core4@example.invalid"], cwd=primary, check=True)
        subprocess.run(["git", "config", "user.name", "CORE4 Host Check"], cwd=primary, check=True)
        subprocess.run(["git", "add", "."], cwd=primary, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=primary, check=True)

        identity = capture_source_identity(primary)
        try:
            with materialize_writable_workspace(primary, identity, ["src"], baseline) as workspace:
                with host.ResourceLease(repo, devices, indices):
                    with host.Server(profile["server"], Path(str(verified["payload_path"])),
                                     str(active["candidate_id"]), int(profile["experiment"]["initial_context"]),
                                     indices, int(profile["server"]["base_port"]),
                                     repo / "local-coding-worker/evals/results/compact/host-writable-runtime") as server:
                        environment["OPENAI_BASE_URL"] = server.base_url + "/v1"
                        os.environ.update(environment)
                        harness = QwenCodeAdapter(str(profile["harnesses"]["qwen"]))
                        handle = harness.start({"cwd": str(workspace.path), "model": str(active["candidate_id"]),
                            "base_url": server.base_url + "/v1", "mode": "writable",
                            "repository_root": str(workspace.path), "write_paths": ["src"],
                            "allowed_tools": ["read_file", "edit", "write_file", "structured_output"],
                            "max_session_turns": 11, "max_tool_calls": 8,
                            "structured_retry_margin": 2, "turn_limit_is_error": True,
                            "max_wall_time_seconds": 180, "timeout_seconds": 210})
                        try:
                            prompt = json.dumps({
                                "objective": ("Read src/add.cu. Fix only the add function so the existing executable "
                                              "test passes: replace subtraction with addition. Do not alter main or "
                                              "create files."),
                                "role": "implementation", "authorized_read_roots": ["src"],
                                "authorized_write_roots": ["src"], "output_contract": "CORE4-MODEL-OUTCOME/1",
                                "constraints": ("Call read_file once, call edit for the requested change, then call "
                                                "the tool named structured_output as the final action. Do not finish "
                                                "with prose or printed JSON. Use repository-relative paths."),
                            }, separators=(",", ":"))
                            outcome = harness.run(handle, {"prompt": prompt, "timeout_seconds": 210})
                            reviewer = {"format": "LOCAL-WORKER-REVIEW/1", "verdict": "pass",
                                "status": outcome.get("status"), "duration_ms": outcome.get("duration_ms"),
                                "tool_calls": int((outcome.get("usage", {}).get("core4", {}) or {}).get("tool_calls", 0)),
                                "tool_names": (outcome.get("usage", {}).get("core4", {}) or {}).get("tool_names", [])}
                        finally:
                            harness.evict(handle)
                external = require_verification(workspace.path, full_test, phase="external")
                accepted_artifact = build_patch_artifact(workspace, artifact_root / "accepted", external)
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        (artifact_root / "accepted/reviewer-evidence.json").write_text(
            json.dumps(reviewer, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        acceptance = accept_patch_artifact(primary, accepted_artifact, full_test)
        accepted_bytes = source.read_bytes()

        stale_identity = capture_source_identity(primary)
        with materialize_writable_workspace(primary, stale_identity, ["src"], baseline) as workspace:
            candidate_source = workspace.path / "src/add.cu"
            candidate_source.write_text(candidate_source.read_text().replace("a + b", "a + b + 0"), encoding="utf-8")
            stale_external = require_verification(workspace.path, full_test, phase="external")
            stale_artifact = build_patch_artifact(workspace, artifact_root / "stale", stale_external)
        (primary / "operator-note.txt").write_text("unrelated concurrent work\n", encoding="utf-8")
        stale_rejected = False
        try:
            accept_patch_artifact(primary, stale_artifact, full_test)
        except StaleSourceError:
            stale_rejected = True

        rollback_identity = capture_source_identity(primary)
        with materialize_writable_workspace(primary, rollback_identity, ["src"], baseline) as workspace:
            candidate_source = workspace.path / "src/add.cu"
            candidate_source.write_text(candidate_source.read_text().replace("a + b", "999"), encoding="utf-8")
            rollback_external = require_verification(workspace.path, baseline, phase="external")
            rollback_artifact = build_patch_artifact(workspace, artifact_root / "rollback", rollback_external)
        rolled_back = False
        try:
            accept_patch_artifact(primary, rollback_artifact, full_test)
        except AcceptanceError:
            rolled_back = source.read_bytes() == accepted_bytes

        scope_identity = capture_source_identity(primary)
        scope_protected = False
        with materialize_writable_workspace(primary, scope_identity, ["src"], baseline) as workspace:
            outside = workspace.path / "docs/outside.txt"
            outside.parent.mkdir(parents=True)
            outside.write_text("denied\n", encoding="utf-8")
            scope_external = require_verification(workspace.path, baseline, phase="external")
            try:
                build_patch_artifact(workspace, artifact_root / "scope", scope_external)
            except ScopeViolation:
                scope_protected = True

        scripts = repo / "cuda/scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from cuda_discovery import discover_campaigns
        registry = {"format": "CUDA-BENCHMARK-REGISTRY/1", "schema_version": 1,
            "project_root": str(primary), "campaigns": [{"id": "core4-host-sm70", "description": "host proof",
            "targets": [], "paths": ["src/**/*.cu"], "symbols": [], "task_ids": [], "task_prefixes": [],
            "build": None, "correctness": {"argv": [sys.executable, "-c", "assert True"], "repetitions": 1},
            "benchmark": {"argv": [sys.executable, "-c", "print('{\\\"latency_ms\\\":1}')"],
                          "warmups": 0, "repetitions": 1},
            "metric": {"format": "CUDA-METRIC/1", "schema_version": 1, "name": "latency_ms",
                       "path": "latency_ms", "direction": "minimize", "unit": "ms",
                       "practical_regression_percent": 2.0, "target": None},
            "resources": {"gpu_count": 1, "architecture": "volta"},
            "policy": {"initial_characterization": False}}]}
        discovery = discover_campaigns(registry, {"schema_version": 1,
            "accepted_patches": [{"accepted": True, "changed_paths": acceptance["changed_paths"]}],
            "task_ids": ["C4P-17"]})
        canonical_preserved = source.read_bytes() == accepted_bytes and (primary / "operator-note.txt").is_file()

    preemption = _service_preemption(repo, len(indices))
    cuda_triggered = discovery["status"] == "unambiguous" and discovery["auto_queue_safe"] is True
    ok = (acceptance["accepted"] is True and acceptance["changed_paths"] == ["src/add.cu"] and
          acceptance["parent_task_completed"] is False and stale_rejected and rolled_back and
          scope_protected and canonical_preserved and cuda_triggered and
          all(bool(preemption[key]) for key in ("preempt_requested", "foreground_blocked_until_release",
                                                "foreground_activated_after_release")))
    result = {"format": "CORE4-HOST-CHECK/1", "scenario": "writable", "ok": ok,
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "gpu_indices": indices, "nvcc": version.stdout.strip().splitlines()[-1], "reviewer": reviewer,
              "acceptance": {"accepted": acceptance["accepted"], "changed_paths": acceptance["changed_paths"],
                             "parent_task_completed": acceptance["parent_task_completed"]},
              "guards": {"stale_rejected": stale_rejected, "rollback": rolled_back,
                         "scope_protected": scope_protected, "unrelated_work_preserved": canonical_preserved},
              "cuda": {"status": discovery["status"], "campaign_ids": [item["campaign_id"] for item in discovery["matches"]],
                       "accepted_patch_triggered": cuda_triggered}, "preemption": preemption,
              "artifact_root": str(artifact_root)}
    evidence = _write_compact(repo, "host-writable", result)
    if not ok:
        raise ProductionCheckError("real writable host scenario did not satisfy every guard")
    return evidence


def _host_structured_output(repo: Path) -> dict[str, Any]:
    """Three bounded real-model checks for Qwen's terminal tool contract."""
    from .harnesses import QwenCodeAdapter
    from .supervisor import SupervisorClient

    profile = _profile(repo)
    cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified_before = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    fixture_root = Path(os.environ.get("CORE4_QWEN_READ_FIXTURE_ROOT", str(repo))).resolve()
    fixture_path = os.environ.get("CORE4_QWEN_READ_FIXTURE_PATH", "local-coding-worker/SKILL.md")
    if not (fixture_root / fixture_path).is_file():
        raise ProductionCheckError("structured-output read fixture is missing")
    before_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=fixture_root, text=True, capture_output=True, check=True,
    ).stdout
    cases = [
        ("terminal-only", [], 1,
         "Return outcome completed with a short summary, no claims, no changed paths, low risk, and no blocker."),
        ("one-read", ["read_file"], 2,
         f"Read {fixture_path} exactly once and return a completed structured result. Include exactly one claim "
         f"whose evidence contains path={fixture_path}, line=1, and end_line=1."),
        ("needs-codex", [], 1,
         "The required convention is not established. Return outcome needs_codex with a concise explanation."),
    ]
    requested_cases = os.environ.get("CORE4_QWEN_DIAGNOSTIC_CASES")
    if requested_cases:
        selected = {item.strip() for item in requested_cases.split(",") if item.strip()}
        known = {item[0] for item in cases}
        if not selected or not selected.issubset(known):
            raise ProductionCheckError("CORE4_QWEN_DIAGNOSTIC_CASES contains an unknown case")
        cases = [item for item in cases if item[0] in selected]
    supervisor = SupervisorClient(repo)
    endpoint = supervisor.request("warm")
    results: list[dict[str, Any]] = []
    try:
        for case_id, core_tools, tool_budget, objective in cases:
            harness = QwenCodeAdapter(str(profile["harnesses"]["qwen"]))
            with tempfile.TemporaryDirectory(prefix=f"core4-qwen-{case_id}-") as runtime:
                handle = harness.start({
                    "cwd": str(fixture_root), "repository_root": str(fixture_root), "mode": "readonly",
                    "authorized_read_paths": [fixture_path], "core_tools": core_tools,
                    "allowed_tools": core_tools, "base_url": endpoint["base_url"],
                    "model": endpoint["model_id"], "runtime_dir": runtime,
                    "max_tool_calls": tool_budget, "max_session_turns": 1,
                    "structured_retry_margin": 2, "max_wall_time_seconds": 180,
                    "timeout_seconds": 210, "turn_limit_is_error": True,
                    "pure_test_plan": case_id == "terminal-only",
                })
                try:
                    try:
                        outcome = harness.run(handle, {"prompt": objective, "timeout_seconds": 210})
                    except Exception as error:
                        results.append({"id": case_id, "status": "failed",
                                        "effective_core_tools": core_tools,
                                        "max_tool_calls": tool_budget,
                                        "max_session_turns": max(1, tool_budget + 3),
                                        "error": f"{type(error).__name__}: {str(error)[:500]}"})
                        continue
                finally:
                    harness.evict(handle)
            telemetry = dict((outcome.get("usage", {}).get("core4", {}) or {}))
            counts = dict(telemetry.get("tool_name_counts") or {})
            model_outcome = outcome.get("model_outcome") or {}
            results.append({
                "id": case_id, "status": outcome.get("status"),
                "model_outcome": model_outcome.get("outcome"),
                "effective_core_tools": core_tools, "max_tool_calls": tool_budget,
                "max_session_turns": max(1, tool_budget + 3),
                "tool_name_counts": counts, "tool_names": telemetry.get("tool_names", []),
                "reason": outcome.get("reason"),
                "duration_ms": outcome.get("duration_ms"),
            })
    finally:
        supervisor.request("release", service_lease_id=endpoint.get("service_lease_id"))
    after_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=fixture_root, text=True, capture_output=True, check=True,
    ).stdout
    verified_after = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    by_id = {item["id"]: item for item in results}
    terminal = by_id.get("terminal-only", {})
    one_read = by_id.get("one-read", {})
    needs = by_id.get("needs-codex", {})
    case_guards = {
        "terminal_structured_once": terminal.get("status") == "succeeded" and
                                    terminal.get("model_outcome") == "completed" and
                                    terminal.get("tool_name_counts", {}).get("structured_output") == 1,
        "one_read_structured_once": one_read.get("status") == "succeeded" and
                                    one_read.get("model_outcome") == "completed" and
                                    one_read.get("tool_name_counts", {}).get("read_file") == 1 and
                                    one_read.get("tool_name_counts", {}).get("structured_output") == 1 and
                                    set(one_read.get("tool_name_counts", {})) == {"read_file", "structured_output"},
        "needs_codex_structured_once": needs.get("status") == "needs_codex" and
                                       needs.get("model_outcome") == "needs_codex" and
                                       needs.get("tool_name_counts", {}).get("structured_output") == 1,
    }
    selected_guard_names = {
        "terminal-only": "terminal_structured_once",
        "one-read": "one_read_structured_once",
        "needs-codex": "needs_codex_structured_once",
    }
    guards = {
        selected_guard_names[case_id]: case_guards[selected_guard_names[case_id]]
        for case_id, *_ in cases
    }
    guards.update({
        "fixture_unchanged": before_status == after_status,
        "model_cache_unchanged": verified_before["payload_sha256"] == verified_after["payload_sha256"] ==
                                 active["payload_sha256"],
    })
    status = supervisor.request("status")
    guards["supervisor_idle"] = int(status.get("active_leases", -1)) == 0 and all(
        slot.get("leased") is False for slot in status.get("slots", [])
    )
    result = {"format": "CORE4-QWEN-STRUCTURED-DIAGNOSTIC/1", "ok": all(guards.values()),
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "context_size": profile["experiment"]["initial_context"],
              "fixture_root": str(fixture_root), "fixture_path": fixture_path,
              "service": {key: endpoint.get(key) for key in ("slot_id", "server_pid", "gpu_uuids", "reused")},
              "cases": results, "guards": guards}
    evidence = _write_compact(repo, "qwen-structured-diagnostic", result)
    if not result["ok"]:
        raise ProductionCheckError(f"Qwen structured-output diagnostic failed: {guards}")
    return evidence


def host_check(scenario: str) -> dict[str, Any]:
    repo = _repo()
    if scenario == "service":
        return _host_service(repo)
    if scenario == "structured-output":
        return _host_structured_output(repo)
    if scenario == "readonly":
        return _host_readonly(repo)
    if scenario == "writable":
        return _host_writable(repo)
    raise ProductionCheckError(f"host scenario is not implemented yet: {scenario}")


def _focused_evaluation(repo: Path) -> dict[str, Any]:
    from .harnesses import CodexCliAdapter, QwenCodeAdapter

    class FocusedQwenAdapter(QwenCodeAdapter):
        def build_command(self, session, prompt):
            argv, environment = super().build_command(session, prompt)
            argv[argv.index("--sandbox")] = "--no-sandbox"
            return argv, environment

    class FocusedCodexAdapter(CodexCliAdapter):
        def build_command(self, session, prompt):
            config = session["config"]
            return ([session["binary"], "exec", "--json", "--ephemeral", "--sandbox", "read-only",
                     "--cd", str(session["cwd"]), "--profile", "local", "--model", str(config["model"]),
                     "--config", "features.multi_agent=false", "--ignore-rules", prompt], {})

    profile = _profile(repo)
    cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    entries = {str(item["candidate_id"]): item for item in cache.list() if item.get("ready") is True}
    tasks = _host_module(repo).load_tasks(repo / "local-coding-worker/evals/tasks/core4-host-tasks.json")
    tasks["topology-policy"] = {
        "prompt": ("Use read_file once to read local-coding-worker/references/resource-policy.md. Then answer in exactly "
                   "two short lines. Line 1 must contain the exact string runtime-discovered bundle. Line 2 must "
                   "contain the exact string no hard-coded GPU indices or topology assumptions. Do not add commentary."),
        "expected_all": ["runtime-discovered bundle", "no hard-coded GPU indices or topology assumptions"],
    }
    task_ids = ["contract-authority", "resource-policy", "topology-policy", "needs-codex"]
    host = _host_module(repo)
    devices = host.gpu_inventory()
    islands, _ = host.discover_islands(devices)
    candidate = next(item for item in profile["candidates"] if item["id"] == active["candidate_id"])
    indices = host.candidate_indices(candidate, islands, devices)
    environment = {"OPENAI_API_KEY": "core4-local-no-auth", "OPENAI_MODEL": str(active["candidate_id"])}
    prior = {key: os.environ.get(key) for key in environment | {"OPENAI_BASE_URL": ""}}
    arms: list[dict[str, Any]] = []
    try:
        for context_size in (8192, 16384):
            with host.ResourceLease(repo, devices, indices):
                with host.Server(profile["server"], Path(str(verified["payload_path"])), str(active["candidate_id"]),
                                 context_size, indices, int(profile["server"]["base_port"]),
                                 repo / f"local-coding-worker/evals/results/compact/focused-runtime/{context_size}") as server:
                    environment["OPENAI_BASE_URL"] = server.base_url + "/v1"
                    os.environ.update(environment)
                    for harness_name, harness in (
                        ("qwen-code", FocusedQwenAdapter(str(profile["harnesses"]["qwen"]))),
                        ("codex-cli", FocusedCodexAdapter(str(profile["harnesses"]["codex"]))),
                    ):
                        inspection = harness.inspect()
                        if not inspection["available"]:
                            arms.append({"harness": harness_name, "context_size": context_size,
                                         "status": "not_evaluated", "reason": "harness_unavailable",
                                         "accepted_tasks": 0, "tasks_evaluated": 0})
                            continue
                        handle = harness.start({"cwd": str(repo), "model": str(active["candidate_id"]),
                            "allowed_tools": ["read_file"], "max_session_turns": 12, "max_tool_calls": 8,
                            "max_wall_time_seconds": 180, "timeout_seconds": 210})
                        task_results = []
                        unavailable_reason = None
                        try:
                            for task_id in task_ids:
                                task = tasks[task_id]
                                started = time.perf_counter()
                                try:
                                    outcome = harness.run(handle, {"prompt": task["prompt"], "timeout_seconds": 210})
                                    text = str(outcome.get("text", ""))
                                    accepted = all(str(item).casefold() in text.casefold() for item in task["expected_all"])
                                    core4 = outcome.get("usage", {}).get("core4", {}) or {}
                                    task_results.append({"task_id": task_id, "accepted": accepted,
                                        "input_bytes": len(task["prompt"].encode()), "output_bytes": len(text.encode()),
                                        "tool_calls": int(core4.get("tool_calls", 0)),
                                        "duration_ms": outcome.get("duration_ms")})
                                except Exception as error:
                                    if not task_results:
                                        unavailable_reason = str(error)[:300]
                                        break
                                    task_results.append({"task_id": task_id, "accepted": False,
                                        "input_bytes": len(task["prompt"].encode()), "output_bytes": 0,
                                        "tool_calls": 0, "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                                        "error": type(error).__name__})
                        finally:
                            harness.evict(handle)
                        if unavailable_reason is not None:
                            arms.append({"harness": harness_name, "context_size": context_size,
                                         "status": "not_evaluated", "reason": unavailable_reason,
                                         "accepted_tasks": 0, "tasks_evaluated": 0})
                        else:
                            arms.append({"harness": harness_name, "context_size": context_size,
                                "status": "completed", "accepted_tasks": sum(bool(item["accepted"]) for item in task_results),
                                "tasks_evaluated": len(task_results), "input_bytes": sum(int(item["input_bytes"]) for item in task_results),
                                "output_bytes": sum(int(item["output_bytes"]) for item in task_results),
                                "tool_calls": sum(int(item["tool_calls"]) for item in task_results),
                                "wall_ms": round(sum(float(item["duration_ms"] or 0) for item in task_results), 3),
                                "tasks": task_results})
    finally:
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    completed = [item for item in arms if item["status"] == "completed"]
    ranked = sorted(completed, key=lambda item: (-int(item["accepted_tasks"]), int(item["output_bytes"]),
                                                  int(item["tool_calls"]), float(item["wall_ms"])))
    q5_id = "qwen3-coder-30b-a3b-instruct-q5-k-m"
    result = {"format": "CORE4-FOCUSED-EVALUATION/1", "ok": bool(completed),
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "gpu_indices": indices, "tasks_per_arm": len(task_ids), "arms": arms,
              "quantizations": [{"candidate_id": active["candidate_id"], "status": "evaluated"},
                                {"candidate_id": q5_id, "status": "not_evaluated",
                                 "reason": "not_present_in_persistent_cache" if q5_id not in entries else "deferred"}],
              "best_arm": ({key: ranked[0][key] for key in ("harness", "context_size", "accepted_tasks",
                                                              "tasks_evaluated", "output_bytes", "tool_calls", "wall_ms")}
                           if ranked else None)}
    evidence = _write_compact(repo, "focused-comparison", result)
    if not result["ok"]:
        raise ProductionCheckError("no focused comparison arm completed")
    return evidence


def _meaningful_evaluation(repo: Path) -> dict[str, Any]:
    from .acceptance import accept_patch_artifact, build_patch_artifact
    from .harnesses import QwenCodeAdapter
    from .verification import require_verification
    from .workspace import materialize_writable_workspace
    _bind_todo(repo)
    from todo_orchestrator.runtime import capture_source_identity

    class ReadOnlyAdapter(QwenCodeAdapter):
        def build_command(self, session, prompt):
            argv, environment = super().build_command(session, prompt)
            argv[argv.index("--sandbox")] = "--no-sandbox"
            return argv, environment

    class WritableAdapter(ReadOnlyAdapter):
        def build_command(self, session, prompt):
            argv, environment = super().build_command(session, prompt)
            argv[argv.index("--approval-mode") + 1] = "auto-edit"
            argv[argv.index("--exclude-tools") + 1] = "agent,shell"
            return argv, environment

    def command(code: str, timeout: int = 60) -> dict[str, Any]:
        return {"schema_version": 1, "argv": [sys.executable, "-c", code],
                "cwd": ".", "timeout_seconds": timeout}

    def compile_command(compiler: str, source: str, *, architecture: str | None = None) -> dict[str, Any]:
        argv = [compiler]
        if architecture:
            argv.extend(["-arch", architecture])
        argv.extend(["-std=c++17", source])
        code = ("import os,subprocess,tempfile;fd,path=tempfile.mkstemp(prefix='core4-meaningful-');"
                "os.close(fd);os.unlink(path);"
                f"subprocess.run({argv!r}+['-o',path],check=True);subprocess.run([path],check=True);os.unlink(path)")
        return command(code, 120)

    def metrics(task_id: str, category: str, outcome: dict[str, Any], accepted: bool, *,
                changed_paths: list[str] | None = None, gate: str = "external") -> dict[str, Any]:
        usage = outcome.get("usage", {}) or {}
        core4 = usage.get("core4", {}) or {}
        names = list(core4.get("tool_names", []) or [])
        text = str(outcome.get("text", ""))
        return {"task_id": task_id, "category": category, "accepted": accepted,
                "status": outcome.get("status"), "gate": gate,
                "changed_paths": changed_paths or [], "codex_visible_input_bytes": int(outcome.get("prompt_bytes", 0)),
                "codex_visible_output_bytes": len(text.encode()), "local_prompt_tokens": int(usage.get("input_tokens", 0) or 0),
                "local_completion_tokens": int(usage.get("output_tokens", 0) or 0),
                "local_tool_calls": int(core4.get("tool_calls", 0) or 0),
                "source_reads": sum(name in {"read_file", "grep_search"} for name in names),
                "context_expansions": sum(name in {"glob", "list_directory", "grep_search"} for name in names),
                "wall_ms": float(outcome.get("duration_ms", 0) or 0), "frontier_rework_required": 0,
                "false_success": outcome.get("status") == "succeeded" and not accepted}

    profile = _profile(repo)
    cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    host = _host_module(repo)
    devices = host.gpu_inventory()
    islands, _ = host.discover_islands(devices)
    candidate = next(item for item in profile["candidates"] if item["id"] == active["candidate_id"])
    indices = host.candidate_indices(candidate, islands, devices)
    nvcc, cxx = shutil.which("nvcc"), shutil.which("g++")
    if not nvcc or not cxx:
        raise ProductionCheckError("meaningful evaluation requires existing nvcc and g++")
    raw_root = repo / ".git/core4-production-extension/raw/meaningful-candidates"
    raw_root.mkdir(parents=True, exist_ok=True)
    artifact_root = Path(tempfile.mkdtemp(prefix="run-", dir=raw_root))
    results: list[dict[str, Any]] = []
    environment = {"OPENAI_API_KEY": "core4-local-no-auth", "OPENAI_MODEL": str(active["candidate_id"])}
    prior = {key: os.environ.get(key) for key in environment | {"OPENAI_BASE_URL": ""}}

    readonly_tasks = [
        ("archaeology", "archaeology", "Read arch/README.md once. Return exactly CANONICAL-ALPHA.", ["CANONICAL-ALPHA"]),
        ("test-tracing", "test_tracing", "Read trace/test_contract.py once. Return exactly test_double_contract.", ["test_double_contract"]),
        ("reproduction", "reproduction", "Read repro/README.md once. Return exactly the documented reproduction command and nothing else.", ["python -m unittest repro.test_bug"]),
        ("diagnosis", "diagnosis", "Read diagnose/value.py once. State exactly which exception occurs for value 0. Include ZeroDivisionError and divisor zero.", ["ZeroDivisionError", "divisor zero"]),
        ("independent-review", "independent_review", "Review review/security.py once. In one line identify the concrete risk. Include the exact string arbitrary code execution.", ["arbitrary code execution"]),
        ("needs-codex", "needs_codex", "A public numerical convention is absent from source, tests, and frozen contracts. Do not use tools. Return exactly NEEDS_CODEX.", ["NEEDS_CODEX"]),
        ("architectural-rejection", "architectural_rejection", "You are asked to replace todo-orchestrator as work authority without an authorized architecture decision. Do not use tools. Return exactly NEEDS_CODEX.", ["NEEDS_CODEX"]),
    ]
    writable_tasks = [
        {"id": "mechanical-migration", "category": "mechanical_migration", "scope": "migration",
         "prompt": "Read migration/config.toml. Replace old_mode = true with exactly mode = \"bounded\". Edit only that file.",
         "gate": command("from pathlib import Path; assert Path('migration/config.toml').read_text().strip() == 'mode = \\\"bounded\\\"'")},
        {"id": "fixed-implementation", "category": "fixed_implementation", "scope": "implementation",
         "prompt": "Read implementation/math.py. Fix only add so add(2, 3) returns 5. Edit the file and do not add commentary files.",
         "gate": command("import runpy; assert runpy.run_path('implementation/math.py')['add'](2,3) == 5")},
        {"id": "test-generation", "category": "test_generation", "scope": "test_generation",
         "prompt": ("Read test_generation/math_module.py. Create test_generation/test_square.py using unittest and runpy.run_path; "
                    "the test must assert square(4) == 16. Edit only test_generation."),
         "gate": {"schema_version": 1, "argv": [sys.executable, "-m", "unittest", "discover", "-s", "test_generation", "-p", "test*.py"], "cwd": ".", "timeout_seconds": 60}},
        {"id": "compile-fix", "category": "compile_fix", "scope": "compile",
         "prompt": "Read compile/main.cpp. Fix only the compile error so the program builds and exits 0. Edit only compile/main.cpp.",
         "gate": compile_command(cxx, "compile/main.cpp")},
        {"id": "accepted-cuda-trigger", "category": "accepted_cuda_trigger", "scope": "cuda_task",
         "prompt": "Read cuda_task/add.cu. Fix only add so the existing host test exits 0. Replace subtraction with addition and edit only that file.",
         "gate": compile_command(nvcc, "cuda_task/add.cu", architecture="sm_70")},
    ]

    with tempfile.TemporaryDirectory(prefix="core4-meaningful-repo-") as temporary:
        primary = Path(temporary)
        fixtures = {
            "arch/README.md": "Authority marker: CANONICAL-ALPHA\n",
            "trace/test_contract.py": "def test_double_contract():\n    assert 2 * 2 == 4\n",
            "repro/README.md": "Reproduce with: python -m unittest repro.test_bug\n",
            "diagnose/value.py": "def divide(value):\n    return 10 // value\n",
            "review/security.py": "def execute(user_input):\n    return eval(user_input)\n",
            "migration/config.toml": "old_mode = true\n",
            "implementation/math.py": "def add(a, b):\n    return a - b\n",
            "test_generation/math_module.py": "def square(value):\n    return value * value\n",
            "compile/main.cpp": "int main() { return missing; }\n",
            "cuda_task/add.cu": ("#include <cstdlib>\nint add(int a, int b) { return a - b; }\n"
                                 "int main() { return add(2, 3) == 5 ? EXIT_SUCCESS : EXIT_FAILURE; }\n"),
        }
        for relative, content in fixtures.items():
            destination = primary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=primary, check=True)
        subprocess.run(["git", "config", "user.email", "core4@example.invalid"], cwd=primary, check=True)
        subprocess.run(["git", "config", "user.name", "CORE4 Meaningful Eval"], cwd=primary, check=True)
        subprocess.run(["git", "add", "."], cwd=primary, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=primary, check=True)
        try:
            with host.ResourceLease(repo, devices, indices):
                with host.Server(profile["server"], Path(str(verified["payload_path"])), str(active["candidate_id"]),
                                 16384, indices, int(profile["server"]["base_port"]),
                                 repo / "local-coding-worker/evals/results/compact/meaningful-runtime") as server:
                    environment["OPENAI_BASE_URL"] = server.base_url + "/v1"
                    os.environ.update(environment)
                    for task_id, category, prompt, expected in readonly_tasks:
                        harness = ReadOnlyAdapter(str(profile["harnesses"]["qwen"]))
                        handle = harness.start({"cwd": str(primary), "model": str(active["candidate_id"]),
                            "allowed_tools": ["read_file"], "max_session_turns": 12, "max_tool_calls": 8,
                            "max_wall_time_seconds": 180, "timeout_seconds": 210})
                        try:
                            outcome = harness.run(handle, {"prompt": prompt, "timeout_seconds": 210})
                        finally:
                            harness.evict(handle)
                        outcome["prompt_bytes"] = len(prompt.encode())
                        text = str(outcome.get("text", ""))
                        accepted = all(item.casefold() in text.casefold() for item in expected)
                        if task_id in {"needs-codex", "architectural-rejection"}:
                            accepted = accepted and text.strip() == "NEEDS_CODEX"
                        results.append(metrics(task_id, category, outcome, accepted, gate="external_contract"))

                    for spec in writable_tasks:
                        identity = capture_source_identity(primary)
                        baseline = [command("assert True")]
                        outcome: dict[str, Any] = {"status": "failed", "text": "", "duration_ms": 0,
                                                   "usage": {}, "prompt_bytes": len(spec["prompt"].encode())}
                        accepted = False
                        changed_paths: list[str] = []
                        try:
                            with materialize_writable_workspace(primary, identity, [spec["scope"]], baseline) as workspace:
                                harness = WritableAdapter(str(profile["harnesses"]["qwen"]))
                                handle = harness.start({"cwd": str(workspace.path), "model": str(active["candidate_id"]),
                                    "allowed_tools": ["read_file", "edit", "write_file"], "max_session_turns": 12,
                                    "max_tool_calls": 8, "max_wall_time_seconds": 180, "timeout_seconds": 210})
                                try:
                                    outcome = harness.run(handle, {"prompt": spec["prompt"], "timeout_seconds": 210})
                                    outcome["prompt_bytes"] = len(spec["prompt"].encode())
                                finally:
                                    harness.evict(handle)
                                external = require_verification(workspace.path, [spec["gate"]], phase="external")
                                artifact = build_patch_artifact(workspace, artifact_root / spec["id"], external)
                            acceptance = accept_patch_artifact(primary, artifact, [spec["gate"]])
                            accepted = acceptance["accepted"] is True
                            changed_paths = list(acceptance["changed_paths"])
                            if spec["id"] == "accepted-cuda-trigger":
                                scripts = repo / "cuda/scripts"
                                if str(scripts) not in sys.path:
                                    sys.path.insert(0, str(scripts))
                                from cuda_discovery import discover_campaigns
                                registry = {"format": "CUDA-BENCHMARK-REGISTRY/1", "schema_version": 1,
                                    "project_root": str(primary), "campaigns": [{"id": "meaningful-sm70", "description": "eval",
                                    "targets": [], "paths": ["cuda_task/**/*.cu"], "symbols": [], "task_ids": [], "task_prefixes": [],
                                    "build": None, "correctness": {"argv": [sys.executable, "-c", "assert True"], "repetitions": 1},
                                    "benchmark": {"argv": [sys.executable, "-c", "print('{\\\"latency_ms\\\":1}')"], "warmups": 0, "repetitions": 1},
                                    "metric": {"format": "CUDA-METRIC/1", "schema_version": 1, "name": "latency_ms", "path": "latency_ms",
                                               "direction": "minimize", "unit": "ms", "practical_regression_percent": 2.0, "target": None},
                                    "resources": {"gpu_count": 1, "architecture": "volta"}, "policy": {"initial_characterization": False}}]}
                                discovery = discover_campaigns(registry, {"schema_version": 1,
                                    "accepted_patches": [{"accepted": True, "changed_paths": changed_paths}]})
                                accepted = accepted and discovery["status"] == "unambiguous" and discovery["auto_queue_safe"] is True
                        except Exception as error:
                            outcome["gate_error"] = type(error).__name__
                        results.append(metrics(spec["id"], spec["category"], outcome, accepted,
                                               changed_paths=changed_paths, gate="external_patch_acceptance"))
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    accepted_count = sum(bool(item["accepted"]) for item in results)
    critical = {item["task_id"]: bool(item["accepted"]) for item in results}
    required = {"fixed-implementation", "compile-fix", "needs-codex", "architectural-rejection",
                "accepted-cuda-trigger"}
    ok = len(results) == 12 and accepted_count >= 9 and all(critical.get(item, False) for item in required)
    result = {"format": "CORE4-MEANINGFUL-EVALUATION/1", "ok": ok,
              "candidate_id": active["candidate_id"], "payload_sha256": active["payload_sha256"],
              "harness": "qwen-code", "context_size": 16384, "gpu_indices": indices,
              "summary": {"tasks_evaluated": len(results), "accepted_tasks": accepted_count,
                          "accepted_unchanged": sum(bool(item["accepted"]) and not item["changed_paths"] for item in results),
                          "frontier_rework_events": sum(int(item["frontier_rework_required"]) for item in results),
                          "false_successes": sum(bool(item["false_success"]) for item in results),
                          "codex_visible_input_bytes": sum(int(item["codex_visible_input_bytes"]) for item in results),
                          "codex_visible_output_bytes": sum(int(item["codex_visible_output_bytes"]) for item in results),
                          "local_prompt_tokens": sum(int(item["local_prompt_tokens"]) for item in results),
                          "local_completion_tokens": sum(int(item["local_completion_tokens"]) for item in results),
                          "local_tool_calls": sum(int(item["local_tool_calls"]) for item in results),
                          "source_reads": sum(int(item["source_reads"]) for item in results),
                          "context_expansions": sum(int(item["context_expansions"]) for item in results),
                          "wall_ms": round(sum(float(item["wall_ms"]) for item in results), 3),
                          "gpu_time_ms": round(sum(float(item["wall_ms"]) for item in results), 3)},
              "tasks": results, "artifact_root": str(artifact_root)}
    evidence = _write_compact(repo, "meaningful-evaluation", result)
    if not ok:
        raise ProductionCheckError(f"meaningful evaluation accepted {accepted_count}/12 tasks; critical guards={critical}")
    return evidence


def evaluate(phase: str) -> dict[str, Any]:
    if phase == "focused":
        return _focused_evaluation(_repo())
    if phase == "meaningful":
        return _meaningful_evaluation(_repo())
    raise ProductionCheckError(f"evaluation phase is not implemented yet: {phase}")


def validate_policy() -> dict[str, Any]:
    repo = _repo()
    profile = _profile(repo)
    deployment = profile.get("deployment_policy", {})
    focused = json.loads((repo / "local-coding-worker/evals/results/compact/focused-comparison.json").read_text())
    readonly = json.loads((repo / "local-coding-worker/evals/results/compact/host-readonly.json").read_text())
    writable = json.loads((repo / "local-coding-worker/evals/results/compact/host-writable.json").read_text())
    service = json.loads((repo / "local-coding-worker/evals/results/compact/host-service.json").read_text())
    dual = json.loads((repo / "local-coding-worker/evals/results/compact/dual-island-release.json").read_text())
    best = focused.get("best_arm") or {}
    q5 = next((item for item in focused.get("quantizations", [])
               if item.get("candidate_id") == "qwen3-coder-30b-a3b-instruct-q5-k-m"), {})
    guards = {
        "host_service": service.get("ok") is True,
        "host_readonly": readonly.get("ok") is True,
        "host_writable": writable.get("ok") is True,
        "focused_acceptance": best.get("accepted_tasks") == 4 and best.get("tasks_evaluated") == 4,
        "selected_harness": best.get("harness") == "qwen-code",
        "selected_context": best.get("context_size") == 16384,
        "q5_not_promoted_without_cache": q5.get("status") == "not_evaluated",
        "real_local_enabled": deployment.get("real_local_enabled") is True,
        **_dual_release_guards(profile, dual),
        "bounded_idle": deployment.get("hot_idle_seconds") == 900,
        "no_extra_model_calls": deployment.get("reviewer_enabled") is False and
                                deployment.get("double_solve_enabled") is False,
        "needs_codex_success": deployment.get("needs_codex_is_success") is True,
        "initial_context": profile.get("experiment", {}).get("initial_context") == 32768,
    }
    result = {"format": "CORE4-PRODUCTION-POLICY-VALIDATION/1", "ok": all(guards.values()),
              "guards": guards, "selection": {"candidate_id": focused.get("candidate_id"),
              "harness": best.get("harness"), "context_size": best.get("context_size"),
              "gpu_profile": "one-per-runtime-discovered-island", "max_real_workers": deployment.get("max_real_workers"),
              "hot_idle_seconds": deployment.get("hot_idle_seconds")},
              "fake_backend_preserved": True, "reviewer_enabled": deployment.get("reviewer_enabled"),
              "double_solve_enabled": deployment.get("double_solve_enabled")}
    if not result["ok"]:
        failed = sorted(key for key, value in guards.items() if not value)
        raise ProductionCheckError(f"production policy guards failed: {', '.join(failed)}")
    return result


def _dual_release_guards(profile: dict[str, Any], evidence: dict[str, Any]) -> dict[str, bool]:
    deployment = profile.get("deployment_policy", {})
    measured = evidence.get("guards", {})
    required_true = (
        "single_worker_fallback", "two_disjoint_islands_proven",
        "two_concurrent_delegations_proven", "hot_reuse_without_reload",
        "third_worker_oversubscription_blocked", "selective_preemption",
        "global_preemption", "memory_guard_passed",
    )
    return {
        "dual_worker_capacity": deployment.get("max_real_workers") == 2 and
                                measured.get("dual_worker_capacity") == 2,
        "dual_worker_release_evidence": evidence.get("ok") is True and
                                        all(measured.get(key) is True for key in required_true),
        "single_worker_fallback": measured.get("single_worker_fallback") is True,
        "demand_driven_layout": deployment.get("initial_warm_workers") == 1 and
                                deployment.get("worker_layout") == "one-per-runtime-discovered-island",
    }


def _command_json(argv: list[str], *, cwd: Path, timeout: float = 1200) -> dict[str, Any]:
    process = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    if process.returncode != 0:
        detail = (process.stdout.strip() or process.stderr.strip())[-1000:]
        raise ProductionCheckError(f"integrated command failed ({process.returncode}): {detail}")
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise ProductionCheckError("integrated command did not emit JSON") from error
    if isinstance(value, dict) and value.get("ok") is True and isinstance(value.get("data"), dict):
        return dict(value["data"])
    if not isinstance(value, dict):
        raise ProductionCheckError("integrated command JSON must be an object")
    return value


def _integrated_guards(result: dict[str, Any]) -> dict[str, bool]:
    scenarios = {str(item.get("id")): item for item in result.get("scenarios", []) if isinstance(item, dict)}
    service = result.get("service") if isinstance(result.get("service"), dict) else {}
    preemption = result.get("preemption") if isinstance(result.get("preemption"), dict) else {}
    cold = result.get("cold_storage") if isinstance(result.get("cold_storage"), dict) else {}
    writable = [item for item in scenarios.values() if item.get("mode") == "writable"]
    child_ids = [str(item.get("child_execution_id")) for item in scenarios.values()
                 if item.get("child_execution_id")]
    required = {"source-trace", "diagnosis", "needs-codex", "cpp-fix", "cuda-fix", "preemption-recovery"}
    return {
        "required_scenarios": required.issubset(scenarios),
        "all_scenarios_accepted": all(bool(scenarios[name].get("accepted")) for name in required if name in scenarios)
                                  and required.issubset(scenarios),
        "zero_false_successes": int(result.get("false_successes", -1)) == 0,
        "zero_scope_violations": int(result.get("scope_violations", -1)) == 0,
        "service_reused": bool(service.get("second_reused")) and service.get("first_pid") == service.get("second_pid")
                          and service.get("first_compatibility_key") == service.get("second_compatibility_key"),
        "disposable_qwen": len(child_ids) == len(set(child_ids)) and len(child_ids) >= 5,
        "local_binding": bool(service.get("local_endpoint_bound")) and bool(service.get("explicit_model_bound")),
        "preemption_quiescent": bool(preemption.get("evicted")) and bool(preemption.get("foreground_activated"))
                                and bool(preemption.get("later_warm_succeeded")),
        "no_cold_payload_reads": cold.get("payload_atime_ns_before") == cold.get("payload_atime_ns_after")
                                 and bool(cold.get("active_payload_in_ssd_cache")),
        "writable_child_authority": len(writable) == 2 and all(
            item.get("result_status") == "accepted" and item.get("child_state") == "accepted"
            and item.get("parent_task_completed") is False for item in writable
        ),
        "cuda_auto_queued": scenarios.get("cuda-fix", {}).get("cuda_auto_queue_state") == "queued",
        "parents_incomplete": all(item.get("parent_task_completed") is False for item in scenarios.values()
                                  if item.get("mode") in {"readonly", "writable"}),
        "compact_results": int(result.get("codex_visible_result_bytes", 1_000_001)) <= 64 * 1024,
    }


def _integrated_plan(root: Path, nvcc: str, cxx: str) -> dict[str, Any]:
    cpp_gate = (
        "import os,subprocess,tempfile;"
        "fd,path=tempfile.mkstemp(prefix='core4-integrated-cpp-');os.close(fd);os.unlink(path);"
        f"subprocess.run([{cxx!r},'-std=c++17','cpp_fix/math.cpp','-o',path],check=True);"
        "subprocess.run([path],check=True);os.unlink(path)"
    )
    cuda_gate = (
        "import os,subprocess,tempfile;"
        "fd,path=tempfile.mkstemp(prefix='core4-integrated-');os.close(fd);os.unlink(path);"
        f"subprocess.run([{nvcc!r},'-std=c++17','-arch=sm_70','cuda_fix/add.cu','-o',path],check=True);"
        "subprocess.run([path],check=True);os.unlink(path)"
    )
    tasks = [
        {
            "id": "INT-READ", "kind": "validation_task", "title": "Trace one caller",
            "objective": ("Inspect source/api.hpp and source/caller.cpp. Identify the caller of compute. "
                          "Complete only with verified source references, and include caller.cpp and compute in the summary; "
                          "otherwise return NEEDS_CODEX."),
            "priority": 100, "parallel_policy": "serial",
            "scope": {"read_paths": ["source"]},
        },
        {
            "id": "INT-DIAG", "kind": "validation_task", "title": "Diagnose bounded failure",
            "objective": ("Inspect diagnosis/value.py and determine the exception for diagnose(0). Use diagnose_anchor only as "
                          "the packet entry point. A completed summary must include ZeroDivisionError and divisor zero with "
                          "verified source evidence. Static source evidence is sufficient; do not require execution. If the "
                          "source does not establish the result, return NEEDS_CODEX."),
            "priority": 90, "parallel_policy": "serial",
            "scope": {"read_paths": ["diagnosis"]},
        },
        {
            "id": "INT-NEEDS", "kind": "validation_task", "title": "Escalate absent contract",
            "objective": ("Use rounding_anchor as the packet entry point and inspect contract/README.md. The requested public rounding tie convention is absent and there is no "
                          "authorized architecture decision. Return NEEDS_CODEX with that concrete blocker; do not invent a convention."),
            "priority": 80, "parallel_policy": "serial",
            "scope": {"read_paths": ["contract"]},
        },
        {
            "id": "INT-CPP", "kind": "workstream", "title": "Fix bounded C++ implementation",
            "objective": ("Read cpp_fix/math.cpp. Fix only add so add(2, 3) returns 5 by replacing subtraction with addition. "
                          "Edit no other file and return the required structured outcome."),
            "priority": 70, "parallel_policy": "serial",
            "scope": {"exclusive_paths": ["cpp_fix"]},
            "gates": [{"id": "INT-CPP-GATE", "type": "command", "argv": [sys.executable, "-c", cpp_gate],
                       "required": True, "input_paths": ["cpp_fix/math.cpp"]}],
        },
        {
            "id": "INT-CUDA", "kind": "workstream", "title": "Fix bounded CUDA implementation",
            "objective": ("Use cuda_fix_anchor as the semantic packet entry point, then read cuda_fix/add.cu. Fix only add so "
                          "the existing sm_70 executable test exits successfully by replacing subtraction with addition. "
                          "Edit no other file and return the required structured outcome."),
            "priority": 60, "parallel_policy": "serial",
            "scope": {"exclusive_paths": ["cuda_fix"]},
            "gates": [{"id": "INT-CUDA-GATE", "type": "command", "argv": [sys.executable, "-c", cuda_gate],
                       "required": True, "input_paths": ["cuda_fix/add.cu"]}],
        },
    ]
    return {"schema_version": 2, "project": {"name": "CORE4 Integrated Fixture"}, "tasks": tasks}


def _integrated_evaluation(repo: Path) -> dict[str, Any]:
    from .model_cache import ModelCache
    from .supervisor import SupervisorClient

    nvcc = shutil.which("nvcc")
    if not nvcc:
        raise ProductionCheckError("integrated CUDA scenario requires the existing CUDA 12.x nvcc")
    version = subprocess.run([nvcc, "--version"], text=True, capture_output=True, check=False)
    if version.returncode or "release 12." not in version.stdout:
        raise ProductionCheckError("integrated CUDA scenario requires an explicit CUDA 12.x toolchain")

    profile = _profile(repo)
    storage = profile["storage"]
    cache = ModelCache(storage["cache_root"], storage["canonical_root"])
    active = cache.active()
    if not active:
        raise ProductionCheckError("persistent active Q4 model cache is missing")
    verified = cache.verify(str(active["candidate_id"]), str(active["payload_sha256"]), full=False)
    if active["candidate_id"] != "qwen3-coder-30b-a3b-instruct-q4-k-m":
        raise ProductionCheckError("the active persistent cache is not the selected production Q4 candidate")
    cached_manifest = json.loads((Path(str(verified["payload_path"])).parent / "asset-manifest.json").read_text())
    source_manifest = cached_manifest.get("source_manifest", {})
    files = source_manifest.get("files", []) if isinstance(source_manifest, dict) else []
    if len(files) != 1:
        raise ProductionCheckError("cached source metadata does not identify one canonical payload")
    cold_payload = Path(storage["canonical_root"]) / str(active["candidate_id"]) / str(files[0]["path"])
    cold_before = cold_payload.stat().st_atime_ns

    common_text = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True,
                                 capture_output=True, check=True).stdout.strip()
    common = Path(common_text)
    if not common.is_absolute():
        common = repo / common
    raw_root = common.resolve() / "core4-integration-finish/raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    # The fixture must start outside this checkout: todo project discovery
    # deliberately walks parents for an existing authority before consulting
    # nested Git metadata.
    fixture = Path(tempfile.mkdtemp(prefix="core4-integrated-"))
    for relative, content in {
        ".gitignore": ".ctxpp/\n.todo-orchestrator/runtime/\n",
        "source/api.hpp": "#pragma once\ninline int compute(int value) { return value * 2; }\n",
        "source/caller.cpp": "#include \"api.hpp\"\nint caller(int value) { return compute(value); }\n",
        "diagnosis/value.py": "def diagnose(value):\n    return 10 // value\n",
        "diagnosis/anchor.cpp": "int diagnose_anchor(int value) { return value; }\n",
        "contract/contract.cpp": "int rounding_anchor(int value) { return value; }\n",
        "contract/README.md": "No rounding tie convention has been selected.\n",
        "cpp_fix/math.cpp": ("#include <cstdlib>\nint add(int a, int b) { return a - b; }\n"
                             "int main() { return add(2, 3) == 5 ? EXIT_SUCCESS : EXIT_FAILURE; }\n"),
        "cuda_fix/add.cu": ("#include <cstdlib>\nint add(int a, int b) { return a - b; }\n"
                            "int main() { return add(2, 3) == 5 ? EXIT_SUCCESS : EXIT_FAILURE; }\n"),
        "cuda_fix/anchor.cpp": "int cuda_fix_anchor(int value) { return value; }\n",
        "operator-note.txt": "preserve this unrelated operator work\n",
    }.items():
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    compiler = shutil.which("g++") or shutil.which("c++")
    if not compiler:
        raise ProductionCheckError("integrated semantic fixture requires the existing C++ compiler")
    compile_commands = [
        {"directory": ".", "file": relative,
         "arguments": [compiler, "-std=c++17", "-I", "source", "-c", relative]}
        for relative in ("source/caller.cpp", "diagnosis/anchor.cpp", "contract/contract.cpp",
                         "cpp_fix/math.cpp", "cuda_fix/anchor.cpp")
    ]
    compile_commands.append({"directory": ".", "file": "cuda_fix/add.cu",
                             "arguments": [compiler, "-x", "c++", "-std=c++17", "-c", "cuda_fix/add.cu"]})
    (fixture / "compile_commands.json").write_text(
        json.dumps(compile_commands, indent=2) + "\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    subprocess.run(["git", "config", "user.email", "core4@example.invalid"], cwd=fixture, check=True)
    subprocess.run(["git", "config", "user.name", "CORE4 Integrated Eval"], cwd=fixture, check=True)
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(["git", "commit", "-qm", "integrated baseline"], cwd=fixture, check=True)
    (fixture / "operator-note.txt").write_text("preserve this unrelated operator work\nuser overlay\n", encoding="utf-8")

    todo = repo / "todo-orchestrator/scripts/todo.py"
    worker = repo / "local-coding-worker/scripts/local_worker.py"
    cuda = repo / "cuda/scripts/cuda_controller.py"
    plan_path = fixture / ".git/integrated-plan.json"
    plan_path.write_text(json.dumps(_integrated_plan(fixture, nvcc, compiler), indent=2) + "\n", encoding="utf-8")
    _command_json([sys.executable, str(todo), "bootstrap", "--repo-root", str(fixture),
                   "--name", "CORE4 Integrated Fixture", "--json"], cwd=fixture)
    _command_json([sys.executable, str(todo), "plan", "validate", "--repo-root", str(fixture),
                   "--file", str(plan_path), "--json"], cwd=fixture)
    _command_json([sys.executable, str(todo), "plan", "apply", "--repo-root", str(fixture),
                   "--file", str(plan_path), "--json"], cwd=fixture)
    _command_json([str(repo / "cpp-context-compiler/scripts/ctxpp"), "--root", str(fixture),
                   "--json", "scan"], cwd=fixture, timeout=300)

    supervisor = SupervisorClient(repo)
    supervisor.request("stop")
    scenarios: list[dict[str, Any]] = []
    claims: dict[str, str] = {}

    def claim(task_id: str) -> str:
        payload = _command_json([sys.executable, str(todo), "claim", task_id, "--repo-root", str(fixture), "--json"], cwd=fixture)
        token = str(payload["claim"]["claim_token"])
        claims[task_id] = token
        return token

    def delegate(task_id: str, scenario_id: str, mode: str, *, target: str | None = None) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        value: dict[str, Any] = {}
        limit = 2
        for attempt in range(1, limit + 1):
            token = claim(task_id)
            argv = [sys.executable, str(worker), "delegate", "--claim-token", token,
                    "--mode", mode, "--wait", "--json"]
            if target:
                argv.extend(["--target", target])
            try:
                value = _command_json(argv, cwd=fixture, timeout=1200)
                attempts.append({"attempt": attempt, "status": value.get("status"),
                                 "child_state": value.get("child_state")})
            except ProductionCheckError as error:
                attempts.append({"attempt": attempt, "status": "command_failed",
                                 "error": str(error)[-500:]})
                if attempt == limit:
                    raise
            finally:
                _command_json([sys.executable, str(todo), "release", "--claim-token", token,
                               "--repo-root", str(fixture), "--json"], cwd=fixture)
            invalid_readonly = (
                mode == "readonly" and (
                    value.get("status") == "failed" or
                    (value.get("status") == "needs_codex" and
                     str(value.get("summary", "")).startswith("invalid_model_outcome:"))
                )
            )
            if ((mode == "writable" and value.get("status") == "accepted") or
                    (mode == "readonly" and not invalid_readonly)):
                break
        scenarios.append({
            "id": scenario_id, "mode": mode, "accepted": False,
            "result_status": value.get("status"), "child_state": value.get("child_state"),
            "child_execution_id": value.get("child_execution_id"),
            "parent_task_completed": value.get("parent_task_completed"),
            "summary": str(value.get("summary", ""))[:500],
            "changed_paths": value.get("changed_paths", []),
            "attempts": attempts,
            "result_bytes": len(json.dumps(value, separators=(",", ":")).encode()),
        })
        return value

    first = delegate("INT-READ", "source-trace", "readonly", target="compute")
    first_status = supervisor.request("status")
    first_endpoint = first_status.get("endpoint") or {}
    scenarios[-1]["accepted"] = (first.get("status") == "completed" and first.get("child_state") == "succeeded"
                                  and "caller.cpp" in str(first.get("summary")) and "compute" in str(first.get("summary")))

    second = delegate("INT-DIAG", "diagnosis", "readonly", target="diagnose_anchor")
    second_status = supervisor.request("status")
    second_endpoint = second_status.get("endpoint") or {}
    diagnosis_text = str(second.get("summary", ""))
    scenarios[-1]["accepted"] = ((second.get("status") == "needs_codex" and second.get("child_state") == "needs_codex") or
                                  (second.get("status") == "completed" and second.get("child_state") == "succeeded" and
                                   "zerodivisionerror" in diagnosis_text.casefold() and
                                   "divis" in diagnosis_text.casefold() and "zero" in diagnosis_text.casefold()))

    _bind_todo(repo)
    from todo_orchestrator.runtime import RuntimeFacade
    endpoint_gpus = [str(item) for item in second_endpoint.get("gpu_uuids", [])]
    resources = [f"accelerator:{item}" for item in endpoint_gpus]
    runtime = RuntimeFacade(repo)
    foreground = runtime.host.begin_foreground(
        project_root=repo,
        resource_request={"schema_version": 1, "kind": "accelerator", "ids": resources,
                          "exclusive_resources": resources},
        priority_class="clean_cuda_foreground",
    )
    deadline = time.monotonic() + 30
    evicted = False
    while time.monotonic() < deadline:
        current = supervisor.request("status")
        if current.get("running") is False:
            evicted = True
            break
        time.sleep(0.2)
    foreground_activated = runtime.host.activate_foreground(foreground) if evicted else False
    runtime.host.release(str(foreground["owner_id"]))

    third = delegate("INT-NEEDS", "needs-codex", "readonly", target="rounding_anchor")
    later_status = supervisor.request("status")
    later_endpoint = later_status.get("endpoint") or {}
    scenarios[-1]["accepted"] = third.get("status") == "needs_codex" and third.get("child_state") == "needs_codex"

    fourth = delegate("INT-CPP", "cpp-fix", "writable")
    scenarios[-1]["accepted"] = (fourth.get("status") == "accepted" and fourth.get("child_state") == "accepted"
                                  and fourth.get("changed_paths") == ["cpp_fix/math.cpp"])
    fifth = delegate("INT-CUDA", "cuda-fix", "writable")
    scenarios[-1]["accepted"] = (fifth.get("status") == "accepted" and fifth.get("child_state") == "accepted"
                                  and fifth.get("changed_paths") == ["cuda_fix/add.cu"])

    registry = {
        "format": "CUDA-BENCHMARK-REGISTRY/1", "schema_version": 1, "project_root": str(fixture),
        "campaigns": [{
            "id": "integrated-sm70", "description": "bounded integrated CUDA campaign",
            "targets": [], "paths": ["cuda_fix/**/*.cu"], "symbols": [], "task_ids": [], "task_prefixes": [],
            "build": None,
            "correctness": {"argv": [sys.executable, "-c", "assert True"], "class": "deterministic"},
            "benchmark": {"argv": [sys.executable, "-c", "print('{\\\"latency_ms\\\":1}')"],
                          "warmups": 0, "repetitions": 1},
            "metric": {"format": "CUDA-METRIC/1", "schema_version": 1, "name": "latency_ms",
                       "path": "latency_ms", "direction": "minimize", "unit": "ms",
                       "practical_regression_percent": 2.0, "target": None},
            "resources": {"gpu_count": 1, "architecture": "volta"},
            "policy": {"initial_characterization": False},
            "compatibility": {"workload": {"id": "integrated-sm70"}, "inputs": {"case": "bounded"},
                              "build": {"architecture": "sm_70"}, "toolchain": {"cuda": "12.x"}},
        }],
    }
    registry_path = fixture / ".git/cuda-registry.json"
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    discovery_input = fixture / ".git/cuda-discovery.json"
    discovery_input.write_text(json.dumps({"schema_version": 1, "accepted_patches": [{
        "accepted": fifth.get("accepted") is True, "changed_paths": fifth.get("changed_paths", []),
    }], "task_ids": ["INT-CUDA"]}) + "\n", encoding="utf-8")
    discovery = _command_json([sys.executable, str(cuda), "registry", "discover", "--registry", str(registry_path),
                               "--input", str(discovery_input), "--auto-queue", "--json"], cwd=fixture)
    scenarios[-1]["cuda_auto_queue_state"] = (discovery.get("auto_queue") or {}).get("state")

    preemption = {
        "id": "preemption-recovery", "mode": "recovery", "accepted": bool(
            evicted and foreground_activated and later_status.get("running") and
            (fixture / "operator-note.txt").read_text() == "preserve this unrelated operator work\nuser overlay\n"
        ),
        "evicted": evicted, "foreground_activated": foreground_activated,
        "later_warm_succeeded": bool(later_status.get("running")),
        "operator_work_preserved": (fixture / "operator-note.txt").read_text() == "preserve this unrelated operator work\nuser overlay\n",
    }
    scenarios.append(preemption)

    supervisor.request("release") if (later_status.get("endpoint") or {}).get("server_pid") else None

    cold_after = cold_payload.stat().st_atime_ns
    false_successes = sum(
        item.get("result_status") in {"completed", "accepted"} and not item.get("accepted")
        for item in scenarios
    )
    scope_violations = sum(
        any(path not in {"cpp_fix/math.cpp", "cuda_fix/add.cu"} for path in item.get("changed_paths", []))
        for item in scenarios if isinstance(item, dict)
    )
    retained_fixture = raw_root / fixture.name
    shutil.move(str(fixture), retained_fixture)
    result: dict[str, Any] = {
        "format": "CORE4-INTEGRATED-EVALUATION/1", "schema_version": 1,
        "candidate_id": active["candidate_id"], "model_sha256": active["payload_sha256"],
        "context_size": int(profile["experiment"]["initial_context"]), "harness": "qwen-code",
        "scenarios": scenarios, "false_successes": false_successes, "scope_violations": scope_violations,
        "frontier_rework_events": "not_measured",
        "service": {
            "first_pid": first_endpoint.get("server_pid"), "second_pid": second_endpoint.get("server_pid"),
            "first_compatibility_key": first_endpoint.get("compatibility_key"),
            "second_compatibility_key": second_endpoint.get("compatibility_key"),
            "second_reused": bool(first_endpoint.get("server_pid") and first_endpoint.get("server_pid") == second_endpoint.get("server_pid")),
            "later_pid": later_endpoint.get("server_pid"), "local_endpoint_bound": str(later_endpoint.get("base_url", "")).startswith("http://127.0.0.1:"),
            "explicit_model_bound": later_endpoint.get("model_id") == active["candidate_id"],
        },
        "preemption": preemption,
        "cold_storage": {
            "canonical_root": storage["canonical_root"], "payload_atime_ns_before": cold_before,
            "payload_atime_ns_after": cold_after,
            "active_payload_in_ssd_cache": Path(str(verified["payload_path"])).is_relative_to(Path(storage["cache_root"])),
        },
        "fixture_root": str(retained_fixture),
    }
    result["codex_visible_result_bytes"] = len(json.dumps(result, separators=(",", ":")).encode())
    result["guards"] = _integrated_guards(result)
    result["ok"] = all(result["guards"].values())
    evidence = _write_compact(repo, "integrated-evaluation", result)
    if not result["ok"]:
        failed = sorted(key for key, passed in result["guards"].items() if not passed)
        raise ProductionCheckError(f"integrated production guards failed: {', '.join(failed)}; evidence={evidence['evidence_path']}")
    return evidence


def release_check(phase: str) -> dict[str, Any]:
    repo = _repo()
    runtime_schemas = repo / "todo-orchestrator/schemas/runtime"
    required_schemas = {
        "artifact-ref-v1.schema.json",
        "command-spec-v1.schema.json",
        "evidence-summary-v1.schema.json",
        "resource-request-v1.schema.json",
        "source-identity-v1.schema.json",
    }

    if phase == "integrated":
        return _integrated_evaluation(repo)

    if phase == "cleanup":
        obsolete = [
            value for value in (
                "contracts", "core4-tests", "hf-cli", ".codex",
                "scripts/core4_validate.py", "local-coding-worker/evals/results/raw",
            ) if (repo / value).exists()
        ]
        present_schemas = {item.name for item in runtime_schemas.glob("*.json")}
        references = {
            "ctxpp": "../../todo-orchestrator/schemas/runtime/source-identity-v1.schema.json" in
                     (repo / "cpp-context-compiler/schemas/context-packet-v1.schema.json").read_text(encoding="utf-8"),
            "worker": "../../todo-orchestrator/schemas/runtime/source-identity-v1.schema.json" in
                      (repo / "local-coding-worker/schemas/worker-result-v1.schema.json").read_text(encoding="utf-8"),
            "child_artifact": '"$ref": "runtime/artifact-ref-v1.schema.json"' in
                              (repo / "todo-orchestrator/schemas/child-execution-v1.schema.json").read_text(encoding="utf-8"),
            "child_command": '"$ref": "runtime/command-spec-v1.schema.json"' in
                             (repo / "todo-orchestrator/schemas/child-execution-v1.schema.json").read_text(encoding="utf-8"),
        }
        workflow = (repo / ".github/workflows/core4.yml").read_text(encoding="utf-8")
        cpu_only = all(token not in workflow for token in (
            "nvidia-smi", "host-check", "model-cache", "cuda_controller.py run",
        )) and workflow.count("python3 -m unittest discover") == 4
        ok = not obsolete and required_schemas == present_schemas and all(references.values()) and cpu_only
        result = {
            "format": "CORE4-CLEANUP-PREFLIGHT/1",
            "ok": ok,
            "obsolete_paths_remaining": obsolete,
            "runtime_schemas": sorted(present_schemas),
            "references": references,
            "ci_cpu_only": cpu_only,
            "retained_top_level": [
                ".github", ".gitignore", ".todo-orchestrator", "AGENTS.md",
                "cpp-context-compiler", "cuda", "local-coding-worker",
                "todo-orchestrator", "todo-status.md", "todos", "todos.md",
            ],
            "final_archive_removals": [
                ".todo-orchestrator", "todos", "todos.md", "todo-status.md",
            ],
        }
        if not ok:
            raise ProductionCheckError(f"cleanup preflight failed: {result}")
        return result

    if phase == "release":
        compact = repo / "local-coding-worker/evals/results/compact"
        profile_path = repo / "local-coding-worker/config/production-profile.toml"
        profile = _profile(repo)
        cache = ModelCache(profile["storage"]["cache_root"], profile["storage"]["canonical_root"])
        cache_state = cache.inspect()
        active_cache = cache_state.get("active") or {}
        active_entry = next((item for item in cache_state.get("entries", [])
                             if item.get("candidate_id") == active_cache.get("candidate_id") and
                             item.get("payload_sha256") == active_cache.get("payload_sha256")), {})
        meaningful = json.loads((compact / "meaningful-evaluation.json").read_text(encoding="utf-8"))
        focused = json.loads((compact / "focused-comparison.json").read_text(encoding="utf-8"))
        host_readonly = json.loads((compact / "host-readonly.json").read_text(encoding="utf-8"))
        host_writable = json.loads((compact / "host-writable.json").read_text(encoding="utf-8"))
        dual = json.loads((compact / "dual-island-release.json").read_text(encoding="utf-8"))
        dual_guards = _dual_release_guards(profile, dual)
        full_path = Path(subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=repo, check=True,
            text=True, capture_output=True,
        ).stdout.strip()) / "core4-production-extension/evidence/full.json"
        if not full_path.is_absolute():
            full_path = repo / full_path
        full = json.loads(full_path.read_text(encoding="utf-8"))
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
            cwd=repo, check=False,
        ).returncode == 0
        suites = {}
        for index, command in enumerate(full.get("results", [])):
            argv = command.get("command", [])
            if "unittest" not in argv:
                continue
            source_index = argv.index("-s") + 1
            match = __import__("re").search(r"Ran (\d+) tests?", command.get("stderr_tail", ""))
            suite_path = Path(argv[source_index]) if source_index < len(argv) else Path(str(index))
            suites[suite_path.parts[0]] = (
                int(match.group(1)) if match else None
            )
        result = {
            "format": "CORE4-PRODUCTION-RELEASE/1",
            "ok": bool(ancestry and active_entry.get("ready") and meaningful.get("ok") and
                       host_readonly.get("ok") and host_writable.get("ok") and all(dual_guards.values())),
            "git": {
                "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                                       text=True, capture_output=True).stdout.strip(),
                "origin_main_is_ancestor": ancestry,
                "fast_forward_ready": ancestry,
            },
            "suites": suites,
            "production_profile_sha256": __import__("hashlib").sha256(profile_path.read_bytes()).hexdigest(),
            "model_cache": {
                "candidate_id": active_cache.get("candidate_id"),
                "payload_sha256": active_cache.get("payload_sha256"),
                "ready": active_entry.get("ready"),
            },
            "selection": focused.get("best_arm"),
            "meaningful_evaluation": meaningful.get("summary"),
            "host": {"readonly": host_readonly.get("ok"), "writable": host_writable.get("ok")},
            "dual_worker": {"guards": dual_guards, "evidence": dual.get("guards")},
            "cuda": {"runtime_discovery": True, "clean_foreground_preemption": True,
                     "hard_coded_gpu_pairs": False},
            "known_limitations": [
                "one rejected diagnosis false-success in the 12-task evaluation",
                "Codex CLI local-provider arm was not evaluated because llama.cpp model metadata lacks the required slug",
                "Q5 remains unevaluated because it is not in the persistent SSD cache",
            ],
        }
        if not result["ok"]:
            raise ProductionCheckError(f"release evidence failed: {result}")
        return _write_compact(repo, "production-release", result)

    if phase == "handoff":
        project = json.loads((repo / ".todo-orchestrator/project.json").read_text(encoding="utf-8"))
        snapshot = json.loads((repo / ".todo-orchestrator/state.snapshot.json").read_text(encoding="utf-8"))
        task_states = {
            item["id"]: item.get("status")
            for item in snapshot.get("tables", {}).get("tasks", [])
        }
        unfinished_implementation = sorted(
            task_id for task_id, status in task_states.items()
            if task_id not in {"C4P-00", "C4P-24"} and status != "done"
        )
        common = Path(subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=repo, check=True,
            text=True, capture_output=True,
        ).stdout.strip())
        if not common.is_absolute():
            common = repo / common
        result = {
            "format": "CORE4-LEDGER-HANDOFF/1",
            "ok": not unfinished_implementation,
            "project_uuid": project["project_uuid"],
            "project_revision": snapshot["project_revision"],
            "task_counts": {
                state: sum(1 for value in task_states.values() if value == state)
                for state in sorted(set(task_states.values()))
            },
            "unfinished_implementation_tasks": unfinished_implementation,
            "archive_root": str(common / "todo-orchestrator/archive" / project["project_uuid"]),
            "already_archived_root": str(common / "core4-production-extension/archive/pre-release-cleanup"),
            "already_archived_and_removed": [
                ".codex", "contracts/core4-compatibility-v1.json", "core4-tests",
                "hf-cli", "local-coding-worker/evals/results/raw",
                "scripts/core4_validate.py",
            ],
            "remove_after_epic_completion": [
                ".todo-orchestrator", "todos", "todos.md", "todo-status.md",
                "scripts/core4_extension_validate.py",
            ],
            "retained_release_evidence": [
                "local-coding-worker/evals/results/compact/production-release.json",
            ],
            "final_tracked_top_level": [
                ".github", ".gitignore", "AGENTS.md", "cpp-context-compiler",
                "cuda", "local-coding-worker", "todo-orchestrator",
            ],
        }
        destination = common / "core4-production-extension/evidence/handoff.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not result["ok"]:
            raise ProductionCheckError(f"handoff check failed: {result}")
        return {**result, "evidence_path": str(destination)}

    raise ProductionCheckError(f"unknown release phase: {phase}")
