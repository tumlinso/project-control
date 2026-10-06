#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = Path(__file__).resolve().parent


class WorkerError(RuntimeError):
    """Fallback CLI error type until the verified worker core is loaded."""


def _load_receiver_runtime(*, worker_core: bool = False) -> None:
    """Bind the receiver before importing its modules or compiling worker_core."""
    from project_control.runtime_binding import bind_local_runtime

    identity = bind_local_runtime()
    expected_script = identity.root / "scripts/local_worker.py"
    if Path(__file__).resolve() != expected_script.resolve():
        raise RuntimeError("receiver_cli_source_mismatch")

    # local_worker imports are intercepted by the binder's manifest-backed
    # finder; don't add receiver directories to sys.path or accept ambient
    # modules from the caller's import state.
    module_names = (
        "local_worker.acceptance",
        "local_worker.controller",
        "local_worker.model_cache",
        "local_worker.supervisor",
        "local_worker.canonical_runtime",
        "local_worker.production_checks",
        "local_worker.service",
        "local_worker.verification",
        "local_worker.workspace",
    )
    modules = {name: importlib.import_module(name) for name in module_names}
    globals().update({
        "AcceptanceError": modules["local_worker.acceptance"].AcceptanceError,
        "DelegationNotEligible": modules["local_worker.controller"].DelegationNotEligible,
        "IntegrationController": modules["local_worker.controller"].IntegrationController,
        "IntegrationError": modules["local_worker.controller"].IntegrationError,
        "ModelCache": modules["local_worker.model_cache"].ModelCache,
        "ModelCacheError": modules["local_worker.model_cache"].ModelCacheError,
        "SupervisorClient": modules["local_worker.supervisor"].SupervisorClient,
        "SupervisorError": modules["local_worker.supervisor"].SupervisorError,
        "runtime_root": modules["local_worker.supervisor"].runtime_root,
        "CanonicalRuntimeError": modules["local_worker.canonical_runtime"].CanonicalRuntimeError,
        "bind_canonical_runtime": modules["local_worker.canonical_runtime"].bind,
        "subprocess_environment": modules["local_worker.canonical_runtime"].subprocess_environment,
        "ProductionCheckError": modules["local_worker.production_checks"].ProductionCheckError,
        "evaluate": modules["local_worker.production_checks"].evaluate,
        "host_check": modules["local_worker.production_checks"].host_check,
        "release_check": modules["local_worker.production_checks"].release_check,
        "validate_policy": modules["local_worker.production_checks"].validate_policy,
        "AdapterError": modules["local_worker.service"].AdapterError,
        "VerificationError": modules["local_worker.verification"].VerificationError,
        "WorkspaceError": modules["local_worker.workspace"].WorkspaceError,
    })
    if worker_core:
        relative = "scripts/worker_core.py"
        manifest = json.loads((identity.root / "receiver-manifest.json").read_text(encoding="utf-8"))
        expected_sha256 = manifest.get("files", {}).get(relative)
        source_path = identity.root / relative
        source = source_path.read_bytes()
        if not isinstance(expected_sha256, str) or hashlib.sha256(source).hexdigest() != expected_sha256:
            raise RuntimeError("receiver_worker_core_hash_mismatch")
        namespace = {
            "__name__": f"_project_control_verified_worker_core_{identity.fingerprint}",
            "__file__": str(source_path),
            "__package__": None,
            "__builtins__": __builtins__,
        }
        exec(compile(source, str(source_path), "exec", dont_inherit=True), namespace)
        globals().update({name: namespace[name] for name in ("WorkerError", "eligibility", "run_controller")})


INACTIVE_COMMAND_ERROR = (
    "local coding, delegation, model-execution, and project-mutation commands "
    "are disabled at this receiver"
)
INACTIVE_COMMANDS = frozenset({
    "run", "integrate", "delegate", "_delegate-worker", "self-test",
    "host-check", "evaluate", "release-check",
})


def _request(path: str) -> object:
    if path == "-":
        return json.load(sys.stdin)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _model_cache() -> ModelCache:
    profile = tomllib.loads((SKILL_ROOT / "config/production-profile.toml").read_text(encoding="utf-8"))
    storage = profile["storage"]
    return ModelCache(storage["cache_root"], storage["canonical_root"])


def _delegation_root() -> Path:
    root = runtime_root() / "delegations"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _execution_path(execution_id: str, suffix: str) -> Path:
    try:
        normalized = str(uuid.UUID(execution_id))
    except ValueError as error:
        raise IntegrationError("invalid delegation execution id") from error
    return _delegation_root() / f"{normalized}.{suffix}"


def _cancel_admission(supervisor: object, admission_id: str) -> None:
    try:
        supervisor.request("cancel-admission", admission_id=admission_id)
    except Exception:
        pass


def _launch_delegate(
    repo: Path,
    claim_token: str,
    mode: str,
    target: str | None,
    objective: str | None = None,
    *,
    controller: IntegrationController | None = None,
    supervisor: SupervisorClient | None = None,
) -> dict[str, object]:
    controller = controller or IntegrationController()
    try:
        request = controller.request_from_claim(
            repo, claim_token, mode=mode, target=target, objective=objective,
        )
    except IntegrationError as error:
        if "no usable" not in str(error):
            raise
        return {"status": "not_eligible", "reason": str(error)[:500], "fallback": "continue_frontier"}
    if not request.get("target"):
        return {
            "status": "not_eligible", "reason": "no_proven_ctxpp_source_target",
            "fallback": "continue_frontier",
        }
    try:
        request = controller.prepare_delegation(request)
    except DelegationNotEligible as error:
        return {
            "status": "not_eligible", "reason": str(error)[:500],
            "fallback": "continue_frontier", "child_created": False,
            "scope_locked": False, "admission_created": False,
            "model_started": False,
        }
    resolved_mode = str(request["mode"])
    supervisor = supervisor or SupervisorClient(repo)
    try:
        admission = supervisor.request("admit")
    except SupervisorError as error:
        message = str(error)
        if "HOST_TOPOLOGY_UNAVAILABLE" in message:
            reason = "host_topology_unavailable"
        elif "HOST_TOPOLOGY_UNSUPPORTED" in message:
            reason = "host_topology_unsupported"
        elif "resource_unavailable" in message:
            reason = "all_local_worker_slots_busy"
        else:
            raise
        return {
            "status": "local_unavailable",
            "reason": reason,
            "fallback": "continue_frontier",
            "retry_recommended": False,
            "child_created": False,
            "scope_locked": False,
        }
    admission_id = str(admission.get("admission_id", ""))
    if not admission_id:
        raise IntegrationError("supervisor admission did not return an admission id")
    execution = dict(request.get("execution") or {})
    execution["admission_id"] = admission_id
    request["execution"] = execution
    execution_id = str(uuid.uuid4())
    request_path = _execution_path(execution_id, "request.json")
    try:
        _atomic_json(request_path, {"execution_id": execution_id, "request": request})
        log_path = _execution_path(execution_id, "log")
        with log_path.open("ab") as log:
            runtime_identity, _runtime_context = bind_canonical_runtime(repo)
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "_delegate-worker", "--request", str(request_path)],
                cwd=repo, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                env=subprocess_environment(runtime_identity),
            )
        _atomic_json(_execution_path(execution_id, "launch.json"), {"pid": process.pid})
    except Exception:
        _cancel_admission(supervisor, admission_id)
        request_path.unlink(missing_ok=True)
        raise
    return {"format": "CORE4-DELEGATE-LAUNCH/1", "status": "delegated",
            "execution_id": execution_id, "mode": resolved_mode,
            "pid": process.pid, "state": "running"}


def _run_detached_delegate(request_path: Path) -> int:
    try:
        launch = json.loads(request_path.read_text(encoding="utf-8"))
        request_path.unlink(missing_ok=True)
        execution_id = str(launch["execution_id"])
        request = launch.get("request")
        if isinstance(request, dict):
            result = IntegrationController().run(request)
        else:  # Compatibility with launch records created before admission tickets.
            result = IntegrationController().delegate(
                Path(str(launch["repo_root"])), str(launch["claim_token"]),
                mode=str(launch["mode"]), target=launch.get("target"), objective=launch.get("objective"),
            )
        state = "completed"
        payload: dict[str, object] = {"result": result}
    except Exception as error:  # The detached boundary must always leave a collectable result.
        launch_value = locals().get("launch", {})
        request_value = launch_value.get("request", {}) if isinstance(launch_value, dict) else {}
        execution = request_value.get("execution", {}) if isinstance(request_value, dict) else {}
        admission_id = execution.get("admission_id") if isinstance(execution, dict) else None
        repo_root = request_value.get("repo_root") if isinstance(request_value, dict) else None
        if admission_id and repo_root:
            _cancel_admission(SupervisorClient(str(repo_root)), str(admission_id))
        execution_id = str(launch_value.get("execution_id", request_path.name.split(".", 1)[0]))
        state = "failed"
        payload = {"error": str(error)}
    _atomic_json(_execution_path(execution_id, "result.json"), {
        "format": "CORE4-DELEGATE-COLLECT/1", "execution_id": execution_id,
        "state": state, **payload,
    })
    return 0 if state == "completed" else 2


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _collect_delegate(execution_id: str, *, wait: bool, timeout: float) -> dict[str, object]:
    result_path = _execution_path(execution_id, "result.json")
    launch_path = _execution_path(execution_id, "launch.json")
    if not launch_path.is_file() and not result_path.is_file():
        raise IntegrationError("unknown delegation execution id")
    deadline = time.monotonic() + timeout
    while wait and not result_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.1)
    if result_path.is_file():
        value = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise IntegrationError("delegation result is invalid")
        return value
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    pid = int(launch["pid"])
    state = "running" if _pid_alive(pid) else "failed"
    result: dict[str, object] = {"format": "CORE4-DELEGATE-COLLECT/1",
        "execution_id": execution_id, "state": state, "pid": pid}
    if state == "failed":
        result["error"] = "delegate process exited without a result"
    elif wait:
        result["retryable"] = True
        result["reason"] = "collection timeout"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded CORE4 local coding worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("eligible", "run"):
        command = subparsers.add_parser(name)
        command.add_argument("--request", required=True, help="LCW-REQUEST/1 JSON path or - for stdin")
    integrate = subparsers.add_parser("integrate")
    integrate.add_argument("--request", required=True, help="CORE4-INTEGRATION-REQUEST/1 JSON path or -")
    delegate = subparsers.add_parser("delegate")
    delegate.add_argument("--claim-token")
    delegate.add_argument("--mode", choices=["auto", "readonly", "writable"])
    delegate.add_argument("--target")
    delegate.add_argument("--objective")
    delegate.add_argument("--collect", metavar="EXECUTION_ID")
    delegate.add_argument("--wait", action="store_true")
    delegate.add_argument("--timeout", type=float, default=900.0)
    delegate.add_argument("--json", action="store_true")
    detached = subparsers.add_parser("_delegate-worker", help=argparse.SUPPRESS)
    detached.add_argument("--request", required=True)
    self_test = subparsers.add_parser("self-test")
    self_test.add_argument("--repo", default=".")
    self_test.add_argument("--json", action="store_true")
    model_cache = subparsers.add_parser("model-cache")
    cache_commands = model_cache.add_subparsers(dest="cache_command", required=True)
    for name in ("inspect", "list", "install", "verify", "activate", "remove"):
        command = cache_commands.add_parser(name)
        command.add_argument("--json", action="store_true")
    cache_commands.choices["install"].add_argument("--candidate-id", required=True)
    cache_commands.choices["install"].add_argument("--activate", action="store_true")
    for name in ("verify", "activate", "remove"):
        cache_commands.choices[name].add_argument("--candidate-id")
        cache_commands.choices[name].add_argument("--payload-sha256")
    cache_commands.choices["verify"].add_argument("--quick", action="store_true")
    cache_commands.choices["verify"].add_argument("--full", action="store_true")
    host = subparsers.add_parser("host-check")
    host.add_argument("--scenario", required=True,
                      choices=["service", "structured-output", "readonly", "writable"])
    host.add_argument("--json", action="store_true")
    service = subparsers.add_parser("service")
    service.add_argument("--repo-root", default=".")
    service_commands = service.add_subparsers(dest="service_command", required=True)
    for name in ("status", "warm", "drain", "evict", "stop"):
        service_commands.add_parser(name).add_argument("--json", action="store_true")
    evaluation = subparsers.add_parser("evaluate")
    evaluation.add_argument("--phase", required=True, choices=["focused", "meaningful"])
    evaluation.add_argument("--json", action="store_true")
    policy = subparsers.add_parser("policy")
    policy_subcommands = policy.add_subparsers(dest="policy_command", required=True)
    policy_subcommands.add_parser("validate").add_argument("--json", action="store_true")
    release = subparsers.add_parser("release-check")
    release.add_argument("--phase", required=True, choices=["cleanup", "integrated", "release", "handoff"])
    release.add_argument("--json", action="store_true")
    args = parser.parse_args()
    # Retained CORE4 implementation is inaccessible through these commands.
    # Deny before receiver imports or request reads; there is no environment,
    # profile, or command-line override for this boundary.
    inactive = args.command in INACTIVE_COMMANDS
    inactive = inactive or (args.command == "service" and args.service_command != "status")
    inactive = inactive or (args.command == "model-cache" and
                            args.cache_command in {"install", "activate", "remove"})
    if inactive:
        print(json.dumps({"format": "LOCAL-CODING-WORKER-ERROR/1",
                          "error": INACTIVE_COMMAND_ERROR}, sort_keys=True, separators=(",", ":")))
        return 2

    try:
        _load_receiver_runtime(worker_core=args.command == "eligible")
    except Exception as error:
        print(json.dumps({"format": "LOCAL-CODING-WORKER-ERROR/1", "error": str(error)},
                         sort_keys=True, separators=(",", ":")))
        return 2

    try:
        if args.command == "_delegate-worker":
            return _run_detached_delegate(Path(args.request))
        if args.command == "service":
            result = SupervisorClient(args.repo_root).request(args.service_command)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "host-check":
            print(json.dumps(host_check(args.scenario), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "evaluate":
            print(json.dumps(evaluate(args.phase), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "policy":
            print(json.dumps(validate_policy(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "release-check":
            print(json.dumps(release_check(args.phase), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "model-cache":
            cache = _model_cache()
            if args.cache_command == "inspect":
                result = cache.inspect()
            elif args.cache_command == "list":
                result = {"format": "CORE4-MODEL-CACHE-LIST/1", "entries": cache.list()}
            elif args.cache_command == "install":
                result = cache.install(args.candidate_id, activate=args.activate)
            else:
                active = cache.active() or {}
                candidate_id = args.candidate_id or active.get("candidate_id")
                payload_sha256 = args.payload_sha256 or active.get("payload_sha256")
                if not candidate_id or not payload_sha256:
                    raise ModelCacheError("candidate and payload hash are required when no active profile exists")
                if args.cache_command == "verify":
                    if args.quick and args.full:
                        raise ModelCacheError("choose only one of --quick or --full")
                    result = cache.verify(candidate_id, payload_sha256, full=bool(args.full))
                elif args.cache_command == "activate":
                    result = cache.activate(candidate_id, payload_sha256)
                else:
                    result = cache.remove(candidate_id, payload_sha256)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "self-test":
            root = Path(args.repo).resolve()
            process = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "local-coding-worker/tests",
                 "-p", "test_core4_integration.py", "-v"],
                cwd=root, text=True, capture_output=True, check=False,
            )
            result = {
                "format": "CORE4-INTEGRATION-SELF-TEST/1",
                "schema_version": 1,
                "ok": process.returncode == 0,
                "scenarios": [
                    "readonly", "writable", "needs_codex", "stale_patch",
                    "preemption", "accepted_patch", "cuda_trigger",
                ],
                "tests_run": sum(1 for line in process.stderr.splitlines() if line.startswith("test_")),
            }
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0 if result["ok"] else 2
        if args.command == "delegate":
            if args.collect:
                if args.claim_token or args.mode or args.target or args.objective:
                    raise IntegrationError("--collect cannot be combined with launch arguments")
                result = _collect_delegate(args.collect, wait=args.wait, timeout=args.timeout)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                return 0 if result.get("state") != "failed" else 2
            if not args.claim_token or not args.mode:
                raise IntegrationError("delegate launch requires --claim-token and --mode")
            if args.wait:
                result = IntegrationController().delegate(
                    Path.cwd(), args.claim_token, mode=args.mode,
                    target=args.target, objective=args.objective,
                )
            else:
                result = _launch_delegate(
                    Path.cwd(), args.claim_token, args.mode, args.target, objective=args.objective,
                )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            return 0
        request = _request(args.request)
        if args.command == "eligible":
            result = eligibility(request)
        elif args.command == "run":
            result = run_controller(request)
        else:
            result = IntegrationController().run(request)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("eligible", True) else 2
    except (OSError, json.JSONDecodeError, WorkerError, IntegrationError, AdapterError,
            AcceptanceError, VerificationError, WorkspaceError, ModelCacheError,
            ProductionCheckError, SupervisorError, CanonicalRuntimeError) as error:
        print(json.dumps({"format": "LOCAL-CODING-WORKER-ERROR/1", "error": str(error)}, sort_keys=True,
                         separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
