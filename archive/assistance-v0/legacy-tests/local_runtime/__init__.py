"""Test entry points bound to Project Control's source-owned local runtime."""

from __future__ import annotations

import os
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from project_control.runtime_binding import bind_local_runtime


SOURCE_RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "src/project_control/local_runtime"

# These suites test the checked-out receiver tree, not a possibly configured
# installed release. Keep the user's process environment intact outside this
# binding operation.
with patch.dict(os.environ) as _environment:
    _environment.pop("PROJECT_CONTROL_RELEASE_MANIFEST", None)
    _environment.pop("PROJECT_CONTROL_RELEASE_DIGEST", None)
    RUNTIME_IDENTITY = bind_local_runtime()

if RUNTIME_IDENTITY.root != SOURCE_RUNTIME_ROOT.resolve():
    raise RuntimeError("local_runtime_tests_not_using_project_control_source")


def receiver_runtime_path() -> Path:
    """Return the exact receiver directory admitted by the source binder."""
    return RUNTIME_IDENTITY.root


def receiver_runtime_identity():
    """Return the verified receiver identity for isolated child-process tests."""
    return RUNTIME_IDENTITY


def assert_receiver_module(module: ModuleType) -> Path:
    """Assert an imported runtime module belongs to the verified receiver."""
    source = Path(str(getattr(module, "__file__", ""))).resolve()
    if RUNTIME_IDENTITY.package_root != source.parent and RUNTIME_IDENTITY.package_root not in source.parents:
        raise AssertionError("runtime_test_imported_module_outside_receiver")
    return source
