from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Sequence

from .config import apply_config_migration, config_path, config_summary, init_config, load_config, migrate_config_dry_run, save_config
from .migration import MigrationError
from .mutation import MutationRejected
from .preledger import PreledgerError
from .registry import RegistryError, WorkspaceRegistry
from .snapshot import SnapshotBuilder, resolve_skills_root, resolve_todo_provider
from .terminal import BubblewrapSandbox
from .runtime_identity import runtime_diagnostics


def _lab_delegated_properties(runtime_limit: int) -> tuple[str, ...]:
    """Request CPU, memory, and pid delegation before creating the controller subgroup."""
    return (
        "--property=Delegate=cpu memory pids",
        "--property=CPUAccounting=yes",
        "--property=MemoryAccounting=yes",
        "--property=TasksAccounting=yes",
        "--property=CPUWeight=100",
        "--property=DelegateSubgroup=controller",
        f"--property=RuntimeMaxSec={runtime_limit}s",
    )


def _live_link(value: str) -> tuple[str, Path]:
    relative, separator, target = value.partition("=")
    if not separator or not relative or not target:
        raise argparse.ArgumentTypeError("live link must be PATH=ABSOLUTE_TARGET")
    return relative, Path(target)


def _terminal_service_constraints() -> dict[str, object]:
    """Inspect only bounded systemd policy fields relevant to bubblewrap."""

    try:
        completed = subprocess.run(
            [
                "systemctl", "--user", "show", "project-control.service",
                "--property=LoadState", "--property=ActiveState",
                "--property=RestrictAddressFamilies", "--property=RestrictNamespaces",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable", "compatible": None,
            "error_code": "project_control_service_policy_unavailable",
        }
    fields = dict(
        line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line
    )
    if completed.returncode != 0 or fields.get("LoadState") != "loaded":
        return {"status": "not_installed", "compatible": None, "error_code": None}
    address_families = fields.get("RestrictAddressFamilies", "").split()
    if address_families and "AF_NETLINK" not in address_families:
        return {
            "status": "incompatible", "compatible": False,
            "error_code": "bwrap_service_address_family_restricted",
            "required_address_family": "AF_NETLINK",
            "active": fields.get("ActiveState") == "active",
        }
    if fields.get("RestrictNamespaces") in {"yes", "true"}:
        return {
            "status": "incompatible", "compatible": False,
            "error_code": "bwrap_service_namespaces_restricted",
            "active": fields.get("ActiveState") == "active",
        }
    return {
        "status": "compatible", "compatible": True, "error_code": None,
        "active": fields.get("ActiveState") == "active",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="project-control")
    commands = parser.add_subparsers(dest="command", required=True)

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("init")
    migrate = config_commands.add_parser("migrate")
    migration_mode = migrate.add_mutually_exclusive_group(required=True)
    migration_mode.add_argument("--dry-run", action="store_true")
    migration_mode.add_argument("--apply", action="store_true")

    workspace = commands.add_parser("workspace")
    workspace_commands = workspace.add_subparsers(dest="workspace_command", required=True)
    add = workspace_commands.add_parser("add")
    add.add_argument("workspace")
    add.add_argument("repository")
    add.add_argument("root", type=Path)
    add.add_argument("--authority", action="store_true")
    add.add_argument("--display-name")
    add.add_argument(
        "--live-link", action="append", default=[], type=_live_link, metavar="PATH=ABSOLUTE_TARGET",
        help="allow one exact repository symlink for read-only live source inspection",
    )
    remove = workspace_commands.add_parser("remove")
    remove.add_argument("workspace")
    workspace_commands.add_parser("list")

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--json", action="store_true", dest="as_json")
    doctor.add_argument("--tunnel", action="store_true")

    serve = commands.add_parser("serve")
    serve.add_argument("profile", nargs="?", choices=("observer", "coder", "codex", "mutator", "investigator", "skill_assembler"), default="observer")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    commands.add_parser("codex")
    commands.add_parser("mutator")

    assistance = commands.add_parser("assistance", help="use demand-only local assistance controls")
    assistance_commands = assistance.add_subparsers(dest="assistance_command", required=True)
    assistance_commands.add_parser("status")
    assistance_commands.add_parser("start", help="start and verify the selected inference runtime")
    assistance_commands.add_parser("stop", help="release owned assistance work and stop inference")
    goal = assistance_commands.add_parser("goal")
    goal.add_argument("project")
    goal.add_argument("text")
    focus = assistance_commands.add_parser("focus")
    focus.add_argument("project")
    focus.add_argument("text")
    focus.add_argument("--repository")
    focus.add_argument("--path", action="append", default=[])
    focus.add_argument("--automatic", action="store_true",
                       help="explicitly permit bounded automatic preparation")
    focus.add_argument("--for", dest="duration")
    quiet = assistance_commands.add_parser("quiet")
    quiet_window = quiet.add_mutually_exclusive_group()
    quiet_window.add_argument("--for", dest="duration")
    quiet_window.add_argument("--until")
    quiet_window.required = True
    release = assistance_commands.add_parser("release")
    release.add_argument("--for", dest="duration")
    release.add_argument("--reason", default="operator-requested")
    resume = assistance_commands.add_parser("resume")
    resume_group = resume.add_mutually_exclusive_group(required=True)
    resume_group.add_argument("--quiet", action="store_true")
    resume_group.add_argument("--release", action="store_true")
    resume_group.add_argument("--all", action="store_true")
    dismiss = assistance_commands.add_parser("dismiss")
    dismiss.add_argument("project")
    dismiss.add_argument("--repository")
    dismiss.add_argument("focus_id")
    dismiss.add_argument("fingerprint")
    dismiss.add_argument("--reason", default="operator-dismissed")
    accept = assistance_commands.add_parser("accept")
    accept.add_argument("project")
    accept.add_argument("note_id")
    handoff = assistance_commands.add_parser("handoff")
    handoff.add_argument("project")
    handoff.add_argument("--focus-id")
    handoff.add_argument("--repository")
    ask = assistance_commands.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--project", required=True)
    run = assistance_commands.add_parser("run", help="explicitly run one local question")
    run.add_argument("question")
    run.add_argument("--project", required=True)
    lab = assistance_commands.add_parser("lab", help="run isolated, explicitly granted lab experiments")
    lab_commands = lab.add_subparsers(dest="lab_command", required=True)
    lab_run = lab_commands.add_parser("run", help="select and run one bounded lab experiment")
    lab_run.add_argument("--project", required=True)
    lab_run.add_argument("--source", action="append", required=True, metavar="PATH")
    lab_run.add_argument("--hypothesis", required=True)
    lab_run.add_argument("--reference", required=True)
    lab_run.add_argument("--measure", action="append", required=True, metavar="MEASURE")
    lab_run.add_argument("--stop-rule", required=True)
    lab_run.add_argument("argv", nargs=argparse.REMAINDER, metavar="COMMAND")
    lab_status = lab_commands.add_parser("status", help="read cold lab state")
    lab_status.add_argument("--experiment")
    lab_candidate = lab_commands.add_parser("candidate", help="create a reviewable candidate patch")
    lab_candidate.add_argument("--experiment", required=True)
    lab_candidate.add_argument("--patch-file", type=Path, required=True)
    lab_verify = lab_commands.add_parser("verify", help="verify one candidate against its experiment")
    lab_verify.add_argument("--candidate", required=True)
    from .assistance.lab_cli import add_scoped_lab_parsers, configure_scoped_run_parser
    add_scoped_lab_parsers(lab_commands)
    configure_scoped_run_parser(lab_run)
    chat = assistance_commands.add_parser("chat")
    chat.add_argument("--project")

    plan = commands.add_parser("plan")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    compile_plan = plan_commands.add_parser("compile")
    compile_plan.add_argument("--package", type=Path, required=True)
    compile_plan.add_argument("--repository-label", required=True)
    compile_plan.add_argument("--output", type=Path, required=True)
    validate_plan = plan_commands.add_parser("validate")
    validate_plan.add_argument("--project", required=True)
    validate_plan.add_argument("--file", type=Path, required=True)
    apply_plan = plan_commands.add_parser("apply")
    apply_plan.add_argument("--project", required=True)
    apply_plan.add_argument("--file", type=Path, required=True)

    migrate_repo = commands.add_parser("migrate-repository")
    migrate_repo.add_argument("--repo", type=Path, required=True)
    mode = migrate_repo.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--remove", action="store_true")

    admin = commands.add_parser("admin")
    admin_commands = admin.add_subparsers(dest="admin_command", required=True)
    ingest_skill = admin_commands.add_parser("ingest-skill-archive")
    ingest_skill.add_argument("--skill", required=True)
    ingest_skill.add_argument("--resource", required=True)
    ingest_skill.add_argument("--destination", required=True)
    ingest_skill.add_argument("--apply", action="store_true")
    recover = admin_commands.add_parser("recover")
    recover.add_argument("--repo", required=True)
    recover.add_argument("--task")
    recover.add_argument("--reason", required=True)
    recover.add_argument("--inspect-only", action="store_true")
    delegated_recovery = admin_commands.add_parser("recover-authorized")
    delegated_recovery.add_argument("--repo", required=True)
    delegated_recovery.add_argument("--authorization", required=True)
    delegated_recovery.add_argument("--reason", required=True)
    prepare_maintenance = admin_commands.add_parser(
        "prepare-maintenance", help="issue one bounded maintenance assignment and operator launch packet"
    )
    prepare_maintenance.add_argument("--repo", required=True)
    prepare_maintenance.add_argument("--task", required=True)
    prepare_maintenance.add_argument("--run")
    prepare_maintenance.add_argument("--recipient", required=True)
    prepare_maintenance.add_argument("--expires", type=int, default=300)
    prepare_supersession = admin_commands.add_parser("prepare-supersession")
    prepare_supersession.add_argument("--repo", required=True)
    prepare_supersession.add_argument("--intent", required=True)
    prepare_supersession.add_argument("--recipient", required=True)
    prepare_supersession.add_argument("--expires", type=int, default=1800)
    retire_batch = admin_commands.add_parser("retire-run-batch")
    retire_batch.add_argument("--repo", required=True)
    retire_batch.add_argument("--request", required=True)
    retire_batch.add_argument("--apply", action="store_true")
    retire_batch.add_argument("--confirm")
    prepare_retire = admin_commands.add_parser("prepare-retire-run-batch")
    prepare_retire.add_argument("--repo", required=True)
    prepare_retire.add_argument("--intent", required=True)
    prepare_retire.add_argument("--output")
    prepare = admin_commands.add_parser("prepare-run-workspaces")
    prepare.add_argument("--repo", required=True)
    prepare.add_argument("--plan", required=True)
    prepare.add_argument("--run", required=True)
    prepare.add_argument("--lane")
    prepare.add_argument("--apply", action="store_true")
    prepare.add_argument("--confirm")
    reconcile = admin_commands.add_parser("reconcile-workspace-base")
    reconcile.add_argument("--repo", required=True)
    reconcile.add_argument("--run", required=True)
    reconcile.add_argument("--lane", required=True)
    reconcile.add_argument("--base", required=True)
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--confirm")
    cleanup = admin_commands.add_parser("mark-run-workspaces-cleanup-eligible")
    cleanup.add_argument("--repo", required=True)
    cleanup.add_argument("--run", required=True)
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--confirm")
    advance = admin_commands.add_parser("advance-producer-wave")
    advance.add_argument("--repo", required=True)
    advance.add_argument("--plan", required=True)
    advance.add_argument("--run", required=True)
    advance.add_argument("--lane", required=True)
    advance.add_argument("--base", required=True)
    advance.add_argument("--integration-task", required=True)
    advance.add_argument("--reason", required=True)
    advance.add_argument("--apply", action="store_true")
    advance.add_argument("--confirm")
    publish = admin_commands.add_parser("publish-producer-wave")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--plan", required=True)
    publish.add_argument("--run", required=True)
    publish.add_argument("--lane", required=True)
    publish.add_argument("--apply", action="store_true")
    publish.add_argument("--confirm")
    binding = admin_commands.add_parser("bind-integration-gates")
    binding.add_argument("--repo", required=True)
    binding.add_argument("--plan", required=True)
    binding.add_argument("--run", required=True)
    binding.add_argument("--integration-task", required=True)
    binding.add_argument("--gates", required=True)
    binding.add_argument("--apply", action="store_true")
    binding.add_argument("--confirm")
    integration_wave = admin_commands.add_parser("integration-wave")
    integration_wave.add_argument("--repo", required=True)
    integration_wave.add_argument("--plan", required=True)
    integration_wave.add_argument("--run", required=True)
    integration_wave.add_argument("--integration-task", required=True)
    integration_wave.add_argument("--action", required=True, choices=("declare", "apply", "gate-finalize", "recover-finalization"))
    integration_wave.add_argument("--wave")
    integration_wave.add_argument("--adopt-gate-failed", action="store_true")
    integration_wave.add_argument("--legacy-provenance-reason")
    integration_wave.add_argument("--reason")
    integration_wave.add_argument("--apply", action="store_true")
    integration_wave.add_argument("--confirm")
    publish_interface = admin_commands.add_parser("publish-completed-interface")
    publish_interface.add_argument("--repo", required=True)
    publish_interface.add_argument("--plan", required=True)
    publish_interface.add_argument("--interface", required=True)
    publish_interface.add_argument("--source-worktree", required=True)
    publish_interface.add_argument("--apply", action="store_true")
    publish_interface.add_argument("--confirm")
    selective_replan = admin_commands.add_parser("selective-replan")
    selective_replan.add_argument("--project", required=True)
    selective_replan.add_argument("--file", type=Path, required=True)
    return parser


def _serve_profile(profile: str, *, host: str | None, port: int | None) -> int:
    """Start only a profile chosen by trusted process startup arguments."""

    if profile == "observer":
        from .app import serve

        return serve(host=host, port=port)
    if host is not None or port is not None:
        raise ValueError(f"{profile.capitalize()} stdio profile does not accept --host or --port")
    if profile in {"coder", "codex"}:
        from .app import serve_codex

        return serve_codex()
    if profile == "mutator":
        from .app import serve_mutator

        return serve_mutator()
    if profile in {"investigator", "skill_assembler"}:
        from .app import _serve_stdio
        from .profiles import MCPProfile
        return _serve_stdio(MCPProfile(profile))
    raise ValueError(f"unsupported MCP profile: {profile!r}")


def _load_native_plan(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid native Todo plan: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("native Todo plan root must be an object")
    return value


def _plan_command(args: argparse.Namespace) -> int:
    if args.plan_command == "compile":
        from .preledger import compile_preledger

        compiled = compile_preledger(args.package, target_repository=args.repository_label)
        args.output.write_text(compiled.canonical_plan_json(), encoding="utf-8")
        print(json.dumps({
            "status": "compiled",
            "output": str(args.output),
            "package_digest": compiled.package_digest,
            "plan_digest": compiled.plan_digest,
            "selected_task_count": compiled.selected_task_count,
            "excluded_task_count": compiled.excluded_task_count,
            "internal_dependency_count": compiled.internal_dependency_count,
            "external_dependencies": compiled.external_dependencies,
            "interfaces_imported": compiled.interfaces_imported,
            "warnings": compiled.warnings,
            "source_files": compiled.source_files,
        }, sort_keys=True, separators=(",", ":")))
        return 0

    from .mutation import apply_proposal, build_mutation_snapshot, validate_native_plan

    config = load_config()
    native_plan = _load_native_plan(args.file)
    if args.plan_command == "validate":
        result = validate_native_plan(config, args.project, native_plan)
    else:
        from .models import ProposalEnvelope
        from .proposals import observation_preconditions

        snapshot = build_mutation_snapshot(config, args.project)
        proposal = ProposalEnvelope.create(
            intent=f"Apply native Todo plan to {args.project}",
            proposed_change=native_plan,
            observation_preconditions=observation_preconditions(snapshot),
            created_at=snapshot.observed_at,
        )
        result = apply_proposal(config, args.project, proposal)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _assistance_composition(project: str):
    """Build the existing observer composition; callers start it only to ask."""
    from .app import Runtime
    from .as1_surface import compose_surface
    from .profiles import MCPProfile

    config = load_config()
    if project not in config.workspaces:
        raise PermissionError("project_not_registered")
    composition = compose_surface(Runtime(config), MCPProfile.OBSERVER)
    return config, composition


def _assistance_demand_work_status() -> dict[str, object]:
    """Read content-free broker work state without starting its dispatcher."""
    config = load_config()
    if not config.workspaces:
        return {"status": "unavailable", "reason": "owner_workspace_unavailable"}
    try:
        _config, composition = _assistance_composition(sorted(config.workspaces)[0])
    except (OSError, RuntimeError, ValueError, PermissionError) as error:
        return {"status": "unavailable", "reason": f"owner_work_status_{type(error).__name__.lower()}"}
    try:
        reader = getattr(composition.jobs, "demand_work_status", None)
        if not callable(reader):
            return {"status": "unavailable", "reason": "owner_work_status_unavailable"}
        value = reader()
        return value if isinstance(value, dict) else {
            "status": "unavailable", "reason": "owner_work_status_invalid"}
    except Exception as error:
        return {"status": "unavailable", "reason": f"owner_work_status_{type(error).__name__.lower()}"}
    finally:
        composition.jobs.shutdown(timeout=1)


def _assistance_repository(config, project: str, repository: str | None = None) -> tuple[str, Path]:
    workspace = config.workspaces.get(project)
    if workspace is None:
        raise PermissionError("project_not_registered")
    alias = repository or workspace.authority_repository
    if alias is None:
        if len(workspace.repositories) != 1:
            raise ValueError("project_requires_authority_repository_or_explicit_repository")
        alias = next(iter(workspace.repositories))
    if alias not in workspace.repositories:
        raise ValueError("repository_not_registered_for_project")
    return alias, workspace.repositories[alias].root


def _assistance_ask(composition, question: str, project: str) -> dict[str, object]:
    """Submit a demand; the broker starts inference only after its cache gate."""
    from .as1_surface import public_inquiry

    composition.start()
    scope = composition.scope(project)
    value = composition.jobs.inquire(question=question, access_scope=scope)
    if value.get("status") not in {"completed", "partial"}:
        return public_inquiry(value)
    job = value.get("job")
    if not isinstance(job, dict):
        return {"status": "unavailable", "reason": "answer_unavailable"}
    composition.jobs.reconcile()
    reference = job.get("result_packet")
    result = composition.store.lookup(reference, access_scope=scope) if reference else None
    if not result or result.status != "ok":
        return {"status": "unavailable", "reason": "answer_unavailable"}
    return public_inquiry({**result.packet.payload, "status": value["status"],
        "packet_id": result.packet.packet_id, "alias": result.packet.alias,
        "evidence_packets": job.get("evidence_packets", []),
        "unresolved_questions": job.get("unresolved_questions", []),
        "sources": [source.model_dump(exclude_none=True) for source in result.packet.sources]})


def _lab_service(project: str | None = None):
    """Build a cold lab adapter from the configured registry and private state."""
    from .assistance.lab import LabProject, LabService

    projects = {}
    if project is not None:
        config = load_config()
        repository, trusted_root = _assistance_repository(config, project)
        projects[project] = LabProject(project=project, root=trusted_root, repository=repository)
    return LabService(projects=projects)


def _scoped_lab_service(project: str | None = None):
    """Build the trusted registry needed to resolve durable scoped LAB sessions."""
    from .assistance.lab import LabProject, LabService

    config = load_config()
    selected = [project] if project is not None else sorted(config.workspaces)
    projects = {}
    for project_id in selected:
        repository, trusted_root = _assistance_repository(config, project_id)
        projects[project_id] = LabProject(project=project_id, root=trusted_root, repository=repository)
    return LabService(projects=projects)


def _lab_transient_unit_active() -> bool:
    """Avoid recursively submitting a transient unit when its delegation failed."""
    try:
        entries = [line[3:].lstrip("/") for line in Path("/proc/self/cgroup").read_text().splitlines()
                   if line.startswith("0::")]
    except OSError:
        return False
    if len(entries) != 1:
        return False
    return any(re.fullmatch(r"project-control-lab-[0-9a-f]{32}\.service", part)
               for part in Path(entries[0]).parts)


def _lab_runtime_identity() -> tuple[Path, Path]:
    """Return the exact interpreter and source root already running this CLI."""
    cli_path = Path(__file__).resolve(strict=True)
    try:
        spec = importlib.util.find_spec("project_control.cli")
        spec_path = Path(spec.origin).resolve(strict=True) if spec is not None and spec.origin else None
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError("lab_cli_source_identity_unavailable") from exc
    if spec_path != cli_path:
        raise ValueError("lab_cli_source_identity_unavailable")
    source_root = cli_path.parent.parent
    package_init = source_root / "project_control" / "__init__.py"
    if not package_init.is_file():
        raise ValueError("lab_cli_source_identity_unavailable")
    interpreter = Path(sys.executable)
    if not interpreter.is_absolute() or not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError("lab_cli_interpreter_unavailable")
    # Keep the venv launcher path: resolving it to the base Python drops the
    # verified environment and its installed dependencies.
    try:
        interpreter.resolve(strict=True)
    except OSError as exc:
        raise ValueError("lab_cli_interpreter_unavailable") from exc
    return interpreter, source_root


def _lab_command_argv(args, command_argv: Sequence[str]) -> list[str]:
    result = ["assistance", "lab", "run", "--project", args.project]
    for source in args.source:
        result.extend(("--source", source))
    result.extend(("--hypothesis", args.hypothesis, "--reference", args.reference))
    for measurement in args.measure:
        result.extend(("--measure", measurement))
    result.extend(("--stop-rule", args.stop_rule, "--", *command_argv))
    return result


def _lab_run_in_transient_unit(args, command_argv: Sequence[str]) -> int:
    """Re-enter this exact CLI in a short-lived delegated systemd user unit."""
    if _lab_transient_unit_active():
        raise ValueError("lab_delegated_cgroup_unavailable")
    systemd_run = shutil.which("systemd-run", path="/usr/bin:/bin")
    if systemd_run is None:
        raise ValueError("lab_transient_runner_unavailable")
    interpreter, source_root = _lab_runtime_identity()
    unit = f"project-control-lab-{uuid.uuid4().hex}.service"
    argv = [
        systemd_run, "--user", "--wait", "--pipe", "--collect", "--quiet",
        f"--unit={unit}", *_lab_delegated_properties(45),
        f"--working-directory={source_root}",
    ]
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(Path.home()),
        "PYTHONPATH": str(source_root),
    }
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_BUS_ADDRESS"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    for name, value in env.items():
        argv.append(f"--setenv={name}={value}")
    argv.extend(("--", str(interpreter), "-m", "project_control.cli",
                 *_lab_command_argv(args, command_argv)))
    try:
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=50, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("lab_transient_runner_unavailable") from exc
    if completed.returncode != 0:
        raise ValueError("lab_transient_execution_failed")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("lab_transient_result_unavailable") from exc
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def _scoped_lab_in_transient_unit(command: str, scope_id: str) -> int:
    """Run one authorized scope in its delegated cgroup under the same release."""
    try:
        from .assistance.lab_runner import current_cgroup_ready
        if current_cgroup_ready():
            return -1
    except Exception as exc:
        raise ValueError("lab_delegated_cgroup_unavailable") from exc
    if _lab_transient_unit_active():
        raise ValueError("lab_delegated_cgroup_unavailable")
    systemd_run = shutil.which("systemd-run", path="/usr/bin:/bin")
    if systemd_run is None:
        raise ValueError("lab_transient_runner_unavailable")
    interpreter, source_root = _lab_runtime_identity()
    unit = f"project-control-lab-{uuid.uuid4().hex}.service"
    runtime_limit = 600
    timeout = runtime_limit + 30
    argv = [
        systemd_run, "--user", "--wait", "--pipe", "--collect", "--quiet",
        f"--unit={unit}", *_lab_delegated_properties(runtime_limit),
        f"--working-directory={source_root}",
    ]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
           "PYTHONPATH": str(source_root)}
    for name in (
        "XDG_CONFIG_HOME", "XDG_STATE_HOME", "PROJECT_CONTROL_OBSERVER_ANALYSIS_STATE_DIR",
        "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SYSTEMD_BUS_ADDRESS",
        "PROJECT_CONTROL_RELEASE_MANIFEST", "PROJECT_CONTROL_RELEASE_DIGEST",
        "PROJECT_CONTROL_SKILLS_ROOT", "PROJECT_CONTROL_OBSERVER_SUPERVISOR_SHA256",
        "PROJECT_CONTROL_OBSERVER_GPU_UUIDS", "TODO_ORCHESTRATOR_STATE_DIR",
    ):
        value = os.environ.get(name)
        if value:
            env[name] = value
    for name, value in env.items():
        argv.append(f"--setenv={name}={value}")
    argv.extend(("--", str(interpreter), "-m", "project_control.cli",
                 "assistance", "lab", command, "--scope-id", scope_id))
    try:
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("lab_transient_runner_unavailable") from exc
    if completed.returncode != 0:
        raise ValueError("lab_transient_execution_failed")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("lab_transient_result_unavailable") from exc
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def _assistance_scoped_lab_command(args) -> int:
    from .assistance.lab_cli import handle_scoped_lab, make_experiment_planner
    from .assistance.demand_runtime import capture_runtime_pin, ensure_demand_runtime_ready
    from .observer_analysis import SkillsObserverAnalysisProvider

    provider: SkillsObserverAnalysisProvider | None = None

    def get_provider():
        nonlocal provider
        if provider is None:
            provider = SkillsObserverAnalysisProvider()
        return provider

    def ensure_ready():
        return ensure_demand_runtime_ready(
            deadline_epoch=time.time() + 120.0, provider=get_provider())

    def gpu_executor(*effect_args, **effect_kwargs):
        # The LAB service calls this only for an already-authorized GPU scope.
        # Build no CUDA/supervisor adapter for previews, CPU runs, or controls.
        from .assistance.lab_gpu import GpuLabExecutor, native_owner_reader

        config = load_config()
        roots = {}
        for project_id in sorted(config.workspaces):
            _repository, trusted_root = _assistance_repository(config, project_id)
            roots[project_id] = trusted_root
        pin = capture_runtime_pin()
        skills_root = (pin.release_root / "runtime-skills").resolve(strict=True)
        controller = skills_root / "cuda" / "scripts" / "cuda_controller.py"
        if controller.is_symlink() or not controller.resolve(strict=True).is_relative_to(skills_root):
            raise ValueError("lab_cuda_controller_outside_selected_runtime_skills")
        active_provider = get_provider()
        client = active_provider._checked_client(deadline_epoch=min(
            float(effect_kwargs.get("deadline", time.time() + 5.0)), time.time() + 5.0))
        executor = GpuLabExecutor(
            project_roots=roots, cuda_controller=controller,
            quiesce=client.quiesce_for_foreground,
            resume=client.resume_after_foreground,
            owner_reader=native_owner_reader,
        )
        return executor(*effect_args, **effect_kwargs)

    if args.lab_command in {"run", "resume"}:
        scope_id = getattr(args, "scope_id", None)
        if not scope_id:
            raise ValueError("scoped_lab_scope_id_required")
        result = _scoped_lab_in_transient_unit(args.lab_command, scope_id)
        if result != -1:
            return result
    return handle_scoped_lab(
        args, lab_service_factory=lambda project: _scoped_lab_service(project),
        planner=make_experiment_planner(get_provider),
        demand_start=ensure_ready,
        lifecycle_hooks={"gpu_executor": gpu_executor},
    )


def _assistance_lab_command(args) -> int:
    if (args.lab_command in {"preview", "authorize", "resume", "cancel"}
            or getattr(args, "scope_id", None) is not None):
        return _assistance_scoped_lab_command(args)
    from .assistance.lab import LabSelection

    if args.lab_command == "run":
        missing = [name for name in ("project", "source", "hypothesis", "reference", "measure", "stop_rule")
                   if not getattr(args, name, None)]
        if missing:
            raise ValueError("lab_run_missing_required_arguments:" + ",".join(missing))
        argv = list(args.argv)
        if argv and argv[0] == "--":
            argv.pop(0)
        if not argv or not argv[0].strip():
            raise ValueError("lab_run_requires_command_after_--")
        from .assistance.lab_runner import current_cgroup_ready
        if not current_cgroup_ready():
            return _lab_run_in_transient_unit(args, argv)
    project = args.project if args.lab_command == "run" else None
    service = _lab_service(project)
    operator = service.operator()
    if args.lab_command == "run":
        selection = LabSelection(
            project=args.project,
            source_paths=tuple(args.source),
            hypothesis=args.hypothesis,
            argv=tuple(argv),
            reference=args.reference,
            expected_measurements="; ".join(args.measure),
            stop_rule=args.stop_rule,
        )
        grant = service.select(operator, selection)
        result = service.run(operator, grant)
    elif args.lab_command == "status":
        result = service.status(operator, experiment_id=args.experiment)
    elif args.lab_command == "candidate":
        try:
            patch_text = args.patch_file.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise ValueError("candidate_patch_must_be_utf8") from exc
        result = service.create_candidate(operator, args.experiment, patch_text)
    elif args.lab_command == "verify":
        result = service.verify_candidate(operator, args.candidate)
    else:
        raise ValueError("unsupported_assistance_lab_command")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _assistance_chat(args) -> int:
    """Line chat has no resident lease; ordinary lines are explicit questions."""
    from .assistance.operator import AssistanceOperator
    operator = AssistanceOperator()
    project = args.project
    composition = None
    print("Assistance chat. Use /status, /quiet DURATION, /release, /resume quiet|release|all, /accept NOTE_ID, /ask TEXT, /exit.")
    try:
        while True:
            try:
                line = input("you> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in {"/exit", "/quit"}:
                break
            if line == "/status":
                print(json.dumps(operator.status(), sort_keys=True))
                continue
            if line.startswith("/quiet"):
                fields = line.split(maxsplit=1)
                from .assistance.operator import parse_duration
                if len(fields) != 2:
                    print("usage: /quiet DURATION")
                    continue
                until = operator.clock() + parse_duration(fields[1])
                print(json.dumps(operator.quiet(until=until), sort_keys=True))
                continue
            if line == "/release":
                if composition is None:
                    config = load_config()
                    if config.workspaces:
                        _, composition = _assistance_composition(
                            project or sorted(config.workspaces)[0])
                print(json.dumps(operator.request_release(
                    job_service=composition.jobs if composition is not None else None), sort_keys=True))
                continue
            if line.startswith("/resume "):
                selection = line.split(maxsplit=1)[1]
                print(json.dumps(operator.resume(quiet=selection in {"quiet", "all"},
                    release=selection in {"release", "all"}), sort_keys=True))
                continue
            if line.startswith("/accept "):
                fields = line.split(maxsplit=1)
                if not project:
                    print("select a registered project with --project")
                    continue
                config = load_config()
                result = operator.accept_suggestion(project=project, note_id=fields[1],
                                                    trusted_projects=set(config.workspaces))
                print(json.dumps(result, sort_keys=True))
                continue
            if line == "/ask" or line.startswith("/ask "):
                line = line[4:].strip()
                if not line:
                    print("question required")
                    continue
            if line.startswith("/"):
                print("unknown command")
                continue
            if not project:
                print("select a registered project with --project")
                continue
            if composition is None:
                _, composition = _assistance_composition(project)
            result = _assistance_ask(composition, line, project)
            print(json.dumps(result, sort_keys=True))
    finally:
        # No release claim is made here; dispatcher shutdown is only process
        # cleanup, and any unresolved owner state remains durable/pending.
        if composition is not None:
            composition.jobs.shutdown(timeout=1)
    return 0


def _assistance_command(args) -> int:
    from .assistance.operator import AssistanceOperator, parse_duration, parse_utc_timestamp

    operator = AssistanceOperator()
    command = args.assistance_command
    if command == "status":
        from .assistance.demand_runtime import demand_runtime_status
        result = {"operator": operator.status(), "runtime": demand_runtime_status(),
                  "work": _assistance_demand_work_status()}
    elif command == "start":
        from .assistance.demand_runtime import start_demand_runtime
        result = start_demand_runtime(deadline_epoch=time.time() + 120.0)
    elif command == "stop":
        from .assistance.demand_runtime import stop_demand_runtime

        def coordinate_stop():
            config = load_config()
            if not config.workspaces:
                return {"active_work_cancelled": False, "owned_resources_released": False,
                        "reason": "owner_workspace_unavailable"}
            _project, composition = _assistance_composition(sorted(config.workspaces)[0])
            try:
                coordinator = getattr(composition.jobs, "coordinate_demand_stop", None)
                if not callable(coordinator):
                    return {"active_work_cancelled": False, "owned_resources_released": False,
                            "reason": "owner_stop_coordinator_unavailable"}
                try:
                    return coordinator(operator.control, timeout=95.0)
                except Exception as error:
                    return {"active_work_cancelled": False, "owned_resources_released": False,
                            "reason": f"owner_stop_coordination_{type(error).__name__.lower()}"}
            finally:
                # The coordinator owns cancellation and release proof. This
                # bounded shutdown only closes an unstarted/finished frontend.
                composition.jobs.shutdown(timeout=1)

        result = stop_demand_runtime(coordinate_stop=coordinate_stop)
    elif command == "chat":
        return _assistance_chat(args)
    elif command == "lab":
        return _assistance_lab_command(args)
    elif command in {"goal", "focus", "dismiss", "accept", "handoff"}:
        config = load_config()
        projects = set(config.workspaces)
        if command == "goal":
            result = operator.set_goal(project=args.project, text=args.text, trusted_projects=projects)
        elif command == "focus":
            automatic_seconds = None
            if args.automatic:
                if not args.duration or not args.path:
                    raise ValueError("automatic_focus_requires_paths_and_bounded_for_duration")
                automatic_seconds = parse_duration(args.duration)
                if automatic_seconds > 86400:
                    raise ValueError("automatic_focus_exceeds_24_hour_ceiling")
            elif args.duration:
                raise ValueError("--for_requires_explicit_--automatic")
            repository, trusted_root = _assistance_repository(config, args.project, args.repository)
            result = operator.set_focus(project=args.project, text=args.text,
                trusted_projects=projects, trusted_root=trusted_root, trusted_repository=repository,
                automatic_seconds=automatic_seconds, source_paths=args.path)
        elif command == "dismiss":
            repository, trusted_root = _assistance_repository(config, args.project, args.repository)
            result = operator.dismiss(project=args.project, focus_id=args.focus_id,
                fingerprint=args.fingerprint, trusted_projects=projects,
                trusted_root=trusted_root, trusted_repository=repository, reason=args.reason)
        elif command == "accept":
            result = operator.accept_suggestion(project=args.project, note_id=args.note_id,
                                                trusted_projects=projects)
        else:
            repository, trusted_root = _assistance_repository(config, args.project, args.repository)
            result = operator.handoff(project=args.project, focus_id=args.focus_id,
                trusted_projects=projects, trusted_root=trusted_root, trusted_repository=repository)
    elif command == "quiet":
        until = (operator.clock() + parse_duration(args.duration) if args.duration else
                 parse_utc_timestamp(args.until) if args.until else None)
        result = operator.quiet(until=until)
    elif command == "release":
        config = load_config()
        until = operator.clock() + parse_duration(args.duration) if args.duration else None
        composition = None
        try:
            if not config.workspaces:
                raise ValueError("no_registered_projects_for_observer_runtime")
            _, composition = _assistance_composition(sorted(config.workspaces)[0])
            result = operator.request_release(job_service=composition.jobs, until=until,
                                              reason=args.reason)
        except (OSError, RuntimeError, ValueError, PermissionError) as exc:
            result = operator.request_release(until=until, reason=args.reason)
            result["owner_setup"] = "unavailable"
            result["owner_setup_reason"] = type(exc).__name__
        finally:
            if composition is not None:
                composition.jobs.shutdown(timeout=1)
    elif command == "resume":
        result = operator.resume(quiet=args.quiet or args.all,
                                 release=args.release or args.all)
    elif command in {"ask", "run"}:
        _config, composition = _assistance_composition(args.project)
        try:
            result = _assistance_ask(composition, args.question, args.project)
        finally:
            composition.jobs.shutdown(timeout=1)
    else:
        raise ValueError("unsupported_assistance_command")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def _doctor(*, tunnel: bool) -> tuple[bool, dict[str, object]]:
    terminal_sandbox = BubblewrapSandbox()
    probe = terminal_sandbox.probe_diagnostics()
    service_constraints = _terminal_service_constraints()
    service_compatible = service_constraints.get("compatible")
    from .profiles import profile_policy, MCPProfile, TEMPORARILY_INACTIVE
    checks: dict[str, object] = {
        "surface": {"profiles": {p.value: list(profile_policy(p).tool_names) for p in MCPProfile},
                    "temporarily_inactive": TEMPORARILY_INACTIVE, "automatic_overview": False},
        "config_path": str(config_path()),
        "internal_command_sandbox": {
            "backend": "bubblewrap",
            "installed": probe["installed"],
            "ready": bool(probe["ready"] and service_compatible is not False),
            "probe": probe,
            "service_constraints": service_constraints,
        },
        # This validates one import/runtime identity without opening an
        # authority. It does not expose ambient environment values or launch a
        # process.
        "workflow_runtime": runtime_diagnostics(),
        # Project Control can observe the existing local-worker supervisor but
        # owns no tool-capable launcher. This says nothing about an executor
        # configured by Todo itself.
        "executor_capabilities": {
            "local_worker_observation_adapter": {
                "status": "observation_only",
                "reason": "observer_only_local_worker_adapter",
                "supported_action": "consult_todo_executor_capabilities",
            },
            "project_control_tool_capable_launcher": {
                "status": "unavailable",
                "reason": "no_project_control_owned_tool_capable_launcher",
                "supported_action": "use_configured_todo_executor",
            },
        },
    }
    try:
        config = load_config()
        registry = WorkspaceRegistry(config)
        checks["config"] = "ok"
        checks["workspaces"] = sorted(config.workspaces)
        checks["ready"] = bool(config.workspaces)
        providers: dict[str, object] = {}
        builder = SnapshotBuilder(config)
        for workspace_id in config.workspaces:
            registry.workspace(workspace_id)
            provider = resolve_todo_provider(config, workspace_id)
            snapshot = builder.build(workspace_id)
            todo_warnings = snapshot.warnings_for("todo")
            providers[workspace_id] = {
                "skills_root": "ok" if resolve_skills_root(config, workspace_id) else "unavailable",
                "todo_provider": provider.local_diagnostics(),
                "todo": {
                    "status": "ok" if snapshot.todo_revision is not None else "unavailable",
                    "revision": snapshot.todo_revision,
                    "cause": todo_warnings[0] if todo_warnings else None,
                    "components": snapshot.todo_status.get("component_authority", {}),
                    "consistency": snapshot.todo_status.get("observation_consistency"),
                },
                "cuda": {
                    "status": snapshot.cuda.get("status", "unavailable"),
                    "cause": (snapshot.warnings_for("cuda") or [None])[0],
                },
                "local_worker": {
                    "status": snapshot.local_worker.get("status", "unavailable"),
                    "cause": (snapshot.warnings_for("worker") or [None])[0],
                },
            }
        checks["providers"] = providers
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        checks.update(config="unavailable", ready=False, error=str(exc))
    if tunnel:
        checks["tunnel"] = "configuration template available; credentials intentionally not inspected"
    return bool(checks.get("config") == "ok"), checks


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "config":
            if args.config_command == "init":
                print(init_config())
            else:
                result = apply_config_migration() if args.apply else migrate_config_dry_run()
                print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "workspace":
            config = load_config()
            registry = WorkspaceRegistry(config)
            if args.workspace_command == "add":
                registry.add_workspace(
                    args.workspace,
                    args.repository,
                    args.root,
                    authority=args.authority,
                    display_name=args.display_name,
                    live_links=dict(args.live_link) if args.live_link else None,
                )
                save_config(config)
                print(args.workspace)
            elif args.workspace_command == "remove":
                registry.remove_workspace(args.workspace)
                save_config(config)
                print(args.workspace)
            else:
                print(json.dumps(config_summary(config), sort_keys=True))
            return 0
        if args.command == "doctor":
            ok, result = _doctor(tunnel=args.tunnel)
            print(json.dumps(result, sort_keys=True) if args.as_json else result)
            return 0 if ok else 1
        if args.command == "serve":
            return _serve_profile(args.profile, host=args.host, port=args.port)
        if args.command == "codex":
            return _serve_profile("codex", host=None, port=None)
        if args.command == "mutator":
            return _serve_profile("mutator", host=None, port=None)
        if args.command == "assistance":
            return _assistance_command(args)
        if args.command == "plan":
            return _plan_command(args)
        if args.command == "migrate-repository":
            from .migration import migrate

            result = migrate(args.repo, apply=args.apply or args.remove, remove=args.remove)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "admin":
            if args.admin_command == "ingest-skill-archive":
                from .config import configured_observer_skills_root
                from .skills import SkillRegistry
                from .skill_ingestion import ingest_skill_archive

                registry = SkillRegistry(configured_observer_skills_root(load_config()))
                result = ingest_skill_archive(
                    registry, args.skill, args.resource, args.destination, apply=args.apply,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
                return 0
            from .admin import (
                advance_producer_wave,
                inspect_recovery,
                mark_run_workspaces_cleanup_eligible,
                manage_integration_wave,
                bind_integration_gates,
                prepare_run_workspaces,
                publish_producer_wave,
                publish_completed_interface,
                reconcile_workspace_base,
                recover,
                recover_authorized,
                prepare_retire_run_batch,
                retire_run_batch,
                prepare_maintenance_assignment,
                prepare_supersession_assignment,
            )

            if args.admin_command == "selective-replan":
                from .mutation import apply_selective_replan

                request = _load_native_plan(args.file)
                result = apply_selective_replan(load_config(), args.project, request)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "publish-completed-interface":
                result = publish_completed_interface(
                    args.repo, args.plan, args.interface, args.source_worktree,
                    apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "prepare-run-workspaces":
                result = prepare_run_workspaces(
                    args.repo, args.plan, args.run, lane_id=args.lane,
                    apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "recover" and args.inspect_only:
                print(json.dumps(inspect_recovery(args.repo, args.task), sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "recover":
                recover(args.repo, reason=args.reason, task_id=args.task)
            elif args.admin_command == "recover-authorized":
                result = recover_authorized(args.repo, authorization_id=args.authorization, reason=args.reason)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "prepare-maintenance":
                from .maintenance_host import operator_launch_command

                assignment = prepare_maintenance_assignment(
                    args.repo, task_id=args.task, run_id=args.run, recipient_principal=args.recipient,
                    expires_seconds=args.expires,
                )
                print(json.dumps({
                    **assignment,
                    "operator_launch": operator_launch_command(args.recipient),
                }, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "prepare-supersession":
                from .maintenance_host import operator_launch_command
                assignment = prepare_supersession_assignment(args.repo, args.intent, recipient_principal=args.recipient, expires_seconds=args.expires)
                print(json.dumps({**assignment, "operator_launch": operator_launch_command(args.recipient)}, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "retire-run-batch":
                result = retire_run_batch(args.repo, args.request, apply=args.apply, confirmation=args.confirm)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "prepare-retire-run-batch":
                result = prepare_retire_run_batch(args.repo, args.intent, args.output)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "reconcile-workspace-base":
                result = reconcile_workspace_base(
                    args.repo, args.run, args.lane, args.base, reason=args.reason,
                    apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "mark-run-workspaces-cleanup-eligible":
                result = mark_run_workspaces_cleanup_eligible(
                    args.repo, args.run, apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "advance-producer-wave":
                result = advance_producer_wave(
                    args.repo, args.plan, args.run, args.lane, args.base, args.integration_task,
                    reason=args.reason, apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "bind-integration-gates":
                result = bind_integration_gates(args.repo, args.plan, args.run, args.integration_task, args.gates,
                                               apply=args.apply, confirmation=args.confirm)
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            elif args.admin_command == "integration-wave":
                result = manage_integration_wave(
                    args.repo, args.plan, args.run, args.integration_task,
                    action=args.action, wave_id=args.wave,
                    adopt_gate_failed=args.adopt_gate_failed, apply=args.apply,
                    legacy_provenance_reason=args.legacy_provenance_reason,
                    reason=args.reason,
                    confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            else:
                result = publish_producer_wave(
                    args.repo, args.plan, args.run, args.lane,
                    apply=args.apply, confirmation=args.confirm,
                )
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
    except (FileNotFoundError, PermissionError, RegistryError, MigrationError, MutationRejected,
            PreledgerError, ValueError, RuntimeError) as exc:
        print(f"project-control: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
