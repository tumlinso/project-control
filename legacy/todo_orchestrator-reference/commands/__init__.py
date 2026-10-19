"""Automatic command-module registration contract."""

from __future__ import annotations

import importlib
import pkgutil


def register_all(subparsers, helpers) -> None:
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        register = getattr(module, "register", None)
        if register:
            register(subparsers, helpers)
