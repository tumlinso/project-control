from __future__ import annotations

import ipaddress
import hashlib
import os
import stat
import tomllib
import warnings
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_DENY_PATTERNS = (
    ".git/**",
    ".env",
    ".env.*",
    "**/*token*",
    "**/*secret*",
    "**/*credential*",
    "**/*.pem",
    "**/*.key",
    "**/*.gguf",
    "**/*.bin",
    "**/node_modules/**",
    "**/__pycache__/**",
)

CANONICAL_SKILLS_ROOT_ENV = "PROJECT_CONTROL_SKILLS_ROOT"
LEGACY_SKILLS_ROOT_ENV = "CODING_WORKFLOW_SKILLS_ROOT"


class RepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: Path

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("repository root must be absolute")
        return expanded.resolve()


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    authority_repository: str | None = None
    skills_root: Path | None = None
    deny_patterns: list[str] = Field(default_factory=list)
    repositories: dict[str, RepositoryConfig]

    @model_validator(mode="after")
    def authority_is_registered(self) -> "WorkspaceConfig":
        if self.authority_repository and self.authority_repository not in self.repositories:
            raise ValueError("authority_repository must name a registered repository")
        if not self.repositories:
            raise ValueError("workspace must register at least one repository")
        return self


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=8767, ge=1, le=65535)
    transport: str = "streamable-http"

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        try:
            if not ipaddress.ip_address(value).is_loopback:
                raise ValueError("server host must be a loopback IP address")
        except ValueError as exc:
            if "loopback" in str(exc):
                raise
            raise ValueError("server host must be a literal loopback IP address") from exc
        return value

    @field_validator("transport")
    @classmethod
    def streamable_http_only(cls, value: str) -> str:
        if value != "streamable-http":
            raise ValueError("transport must be streamable-http")
        return value


class ProgramConfig(BaseModel):
    """A query-only grouping of registered workspaces.

    Membership never implies dependency, ownership, or architectural authority.
    """

    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, max_length=256)
    workspaces: list[str] = Field(min_length=1, max_length=16)

    @field_validator("workspaces")
    @classmethod
    def unique_workspaces(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("program workspaces must be unique")
        return value


class ProjectControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    skills_root: Path | None = None
    server: ServerConfig = Field(default_factory=ServerConfig)
    workspaces: dict[str, WorkspaceConfig] = Field(default_factory=dict)
    programs: dict[str, ProgramConfig] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def supported_version(cls, value: int) -> int:
        if value not in {1, 2}:
            raise ValueError("unsupported config schema_version")
        return value

    @model_validator(mode="after")
    def programs_are_registered(self) -> "ProjectControlConfig":
        if self.schema_version == 1 and self.programs:
            raise ValueError("programs require config schema_version 2")
        registered = set(self.workspaces)
        for program_id, program in self.programs.items():
            missing = sorted(set(program.workspaces) - registered)
            if missing:
                raise ValueError(f"program {program_id!r} names unknown workspaces: {', '.join(missing)}")
        return self


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "project-control" / "config.toml"


def configured_skills_root(
    config: ProjectControlConfig,
    environment: Mapping[str, str] = os.environ,
) -> Path | None:
    """Resolve the explicit Skills root with a bounded legacy alias.

    This is configuration parsing only; runtime identity verification is owned
    by ``workflow_binding``.  The canonical variable always wins and the
    compatibility alias is never copied into persistent configuration.
    """

    canonical = environment.get(CANONICAL_SKILLS_ROOT_ENV)
    legacy = environment.get(LEGACY_SKILLS_ROOT_ENV)
    if canonical:
        return Path(canonical).expanduser().resolve()
    if legacy:
        warnings.warn(
            f"{LEGACY_SKILLS_ROOT_ENV} is deprecated; use {CANONICAL_SKILLS_ROOT_ENV}",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy).expanduser().resolve()
    return config.skills_root


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _assert_private(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError(f"config must be owner-only (0600), found {mode:04o}")


def load_config(path: Path | None = None, *, require_private: bool = True) -> ProjectControlConfig:
    target = path or config_path()
    if not target.is_file():
        raise FileNotFoundError(f"project-control config not found: {target}")
    if require_private:
        _assert_private(target)
    with target.open("rb") as stream:
        return ProjectControlConfig.model_validate(tomllib.load(stream))


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_config(config: ProjectControlConfig) -> str:
    lines = [f"schema_version = {config.schema_version}"]
    if config.skills_root:
        lines.append(f"skills_root = {_quote(str(config.skills_root))}")
    lines.extend([
        "",
        "[server]",
        f"host = {_quote(config.server.host)}",
        f"port = {config.server.port}",
        f"transport = {_quote(config.server.transport)}",
    ])
    for workspace_id in sorted(config.workspaces):
        workspace = config.workspaces[workspace_id]
        section = f"workspaces.{workspace_id}"
        lines.extend(["", f"[{section}]"])
        if workspace.display_name:
            lines.append(f"display_name = {_quote(workspace.display_name)}")
        if workspace.authority_repository:
            lines.append(f"authority_repository = {_quote(workspace.authority_repository)}")
        if workspace.skills_root:
            lines.append(f"skills_root = {_quote(str(workspace.skills_root))}")
        if workspace.deny_patterns:
            values = ", ".join(_quote(item) for item in workspace.deny_patterns)
            lines.append(f"deny_patterns = [{values}]")
        for alias in sorted(workspace.repositories):
            repository = workspace.repositories[alias]
            lines.extend([
                "",
                f"[{section}.repositories.{alias}]",
                f"root = {_quote(str(repository.root))}",
            ])
    for program_id in sorted(config.programs):
        program = config.programs[program_id]
        lines.extend(["", f"[programs.{program_id}]"])
        if program.display_name:
            lines.append(f"display_name = {_quote(program.display_name)}")
        values = ", ".join(_quote(item) for item in program.workspaces)
        lines.append(f"workspaces = [{values}]")
    return "\n".join(lines) + "\n"


def save_config(config: ProjectControlConfig, path: Path | None = None) -> Path:
    target = path or config_path()
    ensure_private_directory(target.parent)
    temporary = target.with_name(f".{target.name}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(render_config(config))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def init_config(path: Path | None = None) -> Path:
    target = path or config_path()
    if target.exists():
        _assert_private(target)
        return target
    return save_config(ProjectControlConfig(), target)


def migration_preview(config: ProjectControlConfig) -> dict[str, Any]:
    """Describe the explicit v1-to-v2 migration without changing any file."""

    migrated = config.model_copy(deep=True, update={"schema_version": 2})
    rendered = render_config(migrated)
    return {
        "from_schema_version": config.schema_version,
        "to_schema_version": 2,
        "migration_required": config.schema_version != 2,
        "changes": ["schema_version:1->2"] if config.schema_version == 1 else [],
        "preserved_workspaces": sorted(config.workspaces),
        "preserved_programs": sorted(config.programs),
        "result_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }


def migrate_config(
    path: Path | None = None,
    *,
    apply: bool = False,
    require_private: bool = True,
) -> dict[str, Any]:
    """Preview or explicitly apply the lossless configuration-v2 migration.

    The default is dry-run.  Applying uses the same private, fsync-and-replace
    path as normal configuration writes; merely loading never migrates a file.
    """

    target = path or config_path()
    config = load_config(target, require_private=require_private)
    result = migration_preview(config)
    result["applied"] = False
    if apply and result["migration_required"]:
        migrated = config.model_copy(deep=True, update={"schema_version": 2})
        save_config(ProjectControlConfig.model_validate(migrated.model_dump()), target)
        result["applied"] = True
    return result


def migrate_config_dry_run(path: Path | None = None, *, require_private: bool = True) -> dict[str, Any]:
    """CLI-facing dry-run seam; guaranteed not to write."""

    return migrate_config(path, apply=False, require_private=require_private)


def apply_config_migration(path: Path | None = None, *, require_private: bool = True) -> dict[str, Any]:
    """CLI-facing explicit apply seam."""

    return migrate_config(path, apply=True, require_private=require_private)


def config_summary(config: ProjectControlConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "server": config.server.model_dump(mode="json"),
        "workspaces": sorted(config.workspaces),
        "programs": sorted(config.programs),
    }
