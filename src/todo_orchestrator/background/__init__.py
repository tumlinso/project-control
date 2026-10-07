"""Private, sidecar-only background execution runtime.

This package is intentionally absent from the public todo CLI.  It consumes
committed todo events without changing the authoritative task database.
"""

from .store import BackgroundStore, runtime_paths

__all__ = ["BackgroundStore", "runtime_paths"]
