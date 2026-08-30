"""One verified, in-process binding to Todo Orchestrator's workflow kernel."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Mapping

from .runtime_identity import RuntimeIdentity, bind_runtime, validate_runtime


@dataclass(frozen=True)
class WorkflowBinding:
    identity: RuntimeIdentity
    kernel: object
    protocol: object

    def validate(self) -> None:
        validate_runtime(self.identity)


_LOCK = RLock()
_BINDING: WorkflowBinding | None = None


def _runtime_guard(identity: RuntimeIdentity):
    def guard(repo_root: Path) -> None:
        try:
            validate_runtime(identity)
        except Exception as exc:
            if getattr(exc, "code", None) != "runtime_identity_mismatch":
                raise
            from todo_orchestrator.models import TodoError

            raise TodoError(
                "runtime_identity_mismatch",
                str(exc),
                details={
                    "expected": str(getattr(exc, "expected", identity.module_file)),
                    "observed": str(getattr(exc, "observed", "unknown")),
                    "canonical_skills_root": str(identity.skills_root),
                    "canonical_package_root": str(identity.package_root),
                    "runtime_fingerprint": identity.fingerprint,
                    "remediation": "restart Project Control using the validated candidate runtime",
                },
            ) from exc

    return guard


def initialize_workflow_binding(
    environment: Mapping[str, str] = os.environ,
) -> WorkflowBinding:
    """Initialize the sole process-wide workflow binding, or validate it.

    Repeated calls are safe only while the configured root and loaded package
    remain identical.  Runtime rebinding is intentionally unsupported.
    """

    global _BINDING
    with _LOCK:
        if _BINDING is not None:
            _BINDING.validate()
            requested = bind_runtime(environment)
            if requested != _BINDING.identity:
                from .runtime_identity import RuntimeIdentityError

                raise RuntimeIdentityError(
                    "Project Control cannot rebind Todo after initialization",
                    expected=str(_BINDING.identity.skills_root),
                    observed=str(requested.skills_root),
                )
            return _BINDING

        identity = bind_runtime(environment)
        # Imports happen only after identity verification.  No MCP client,
        # subprocess, shell, or private table access participates in binding.
        from todo_orchestrator.workflow import (
            WorkflowCapabilityLocator,
            WorkflowKernel,
            WorkflowProtocol,
        )

        locator = WorkflowCapabilityLocator()
        kernel = WorkflowKernel(locator=locator, runtime_guard=_runtime_guard(identity))
        protocol = WorkflowProtocol(kernel, locator)
        _BINDING = WorkflowBinding(identity=identity, kernel=kernel, protocol=protocol)
        return _BINDING


def runtime_identity(environment: Mapping[str, str] = os.environ) -> RuntimeIdentity:
    return initialize_workflow_binding(environment).identity


def workflow_kernel(environment: Mapping[str, str] = os.environ):
    return initialize_workflow_binding(environment).kernel


def workflow_protocol(environment: Mapping[str, str] = os.environ):
    return initialize_workflow_binding(environment).protocol


def reset_runtime_for_testing() -> None:
    """Clear process state for isolated unit tests; never used by server code."""

    global _BINDING
    with _LOCK:
        _BINDING = None
