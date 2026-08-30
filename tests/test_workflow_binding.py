from __future__ import annotations

import os
import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from project_control.runtime_identity import RuntimeIdentity, RuntimeIdentityError
from project_control.workflow_binding import (
    initialize_workflow_binding,
    reset_runtime_for_testing,
    runtime_identity,
    workflow_kernel,
    workflow_protocol,
)


class WorkflowBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_runtime_for_testing()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        package = root / "todo-orchestrator" / "todo_orchestrator"
        package.mkdir(parents=True)
        module_file = package / "__init__.py"
        module_file.write_text("", encoding="utf-8")
        self.identity = RuntimeIdentity(root, package, package, module_file, "fingerprint")
        self.workflow_module = types.ModuleType("todo_orchestrator.workflow")

        class Locator:
            pass

        class Kernel:
            def __init__(inner, *, locator, runtime_guard):
                inner.locator = locator
                inner.runtime_guard = runtime_guard

        class Protocol:
            def __init__(inner, kernel, locator):
                inner.kernel = kernel
                inner.locator = locator

        self.workflow_module.WorkflowCapabilityLocator = Locator
        self.workflow_module.WorkflowKernel = Kernel
        self.workflow_module.WorkflowProtocol = Protocol

    def tearDown(self) -> None:
        reset_runtime_for_testing()
        self.temporary.cleanup()

    def patches(self, *, identities=None):
        values = identities or self.identity
        return (
            patch("project_control.workflow_binding.bind_runtime", side_effect=values if isinstance(values, list) else None, return_value=None if isinstance(values, list) else values),
            patch("project_control.workflow_binding.validate_runtime"),
            patch.dict(sys.modules, {"todo_orchestrator.workflow": self.workflow_module}),
        )

    def test_exports_one_kernel_and_protocol(self) -> None:
        bind, validate, modules = self.patches()
        with bind, validate, modules:
            binding = initialize_workflow_binding({})
            self.assertIs(workflow_kernel({}), binding.kernel)
            self.assertIs(workflow_protocol({}), binding.protocol)
            self.assertIs(runtime_identity({}), binding.identity)
        self.assertIs(binding.protocol.kernel, binding.kernel)
        self.assertIs(binding.protocol.locator, binding.kernel.locator)

    def test_repeated_initialization_is_stable(self) -> None:
        bind, validate, modules = self.patches()
        with bind, validate, modules:
            first = initialize_workflow_binding({})
            second = initialize_workflow_binding({})
        self.assertIs(first, second)

    def test_rebinding_is_rejected(self) -> None:
        other = RuntimeIdentity(
            self.identity.skills_root / "other",
            self.identity.source_package_root,
            self.identity.package_root,
            self.identity.module_file,
            self.identity.fingerprint,
        )
        bind, validate, modules = self.patches(identities=[self.identity, other])
        with bind, validate, modules:
            initialize_workflow_binding({})
            with self.assertRaises(RuntimeIdentityError):
                initialize_workflow_binding({})

    def test_binding_uses_no_mcp_or_subprocess_types(self) -> None:
        source = Path(__file__).resolve().parents[1] / "src" / "project_control" / "workflow_binding.py"
        text = source.read_text(encoding="utf-8")
        imported = {
            alias.name
            for node in ast.walk(ast.parse(text))
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(any(name.startswith(("mcp", "subprocess")) for name in imported))


if __name__ == "__main__":
    unittest.main()
