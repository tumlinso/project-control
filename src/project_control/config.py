from __future__ import annotations

import ipaddress
import os
import stat
import tomllib
from pathlib import Path
from typing import Any

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


class ProjectControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    skills_root: Path | None = None
    server: ServerConfig = Field(default_factory=ServerConfig)
    workspaces: dict[str, WorkspaceConfig] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def version_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported config schema_version")
        return value


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "project-control" / "config.toml"


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


def config_summary(config: ProjectControlConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "server": config.server.model_dump(mode="json"),
        "workspaces": sorted(config.workspaces),
    }
