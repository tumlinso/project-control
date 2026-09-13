from __future__ import annotations

import unittest
from unittest.mock import patch

from project_control import app
from project_control.profiles import MCPProfile


class _Registry:
    def __init__(self): self.closed = 0
    def close(self): self.closed += 1


class _Mcp:
    def __init__(self, registry): self._project_control_observer_analysis_registry = registry
    def run(self, **kwargs): raise RuntimeError("stdio closed")


class StdioObserverCleanupTests(unittest.TestCase):
    def test_stdio_exit_closes_cached_observer_provider(self):
        registry = _Registry()
        with patch.object(app, "create_mcp", return_value=_Mcp(registry)):
            with self.assertRaisesRegex(RuntimeError, "stdio closed"):
                app._serve_stdio(MCPProfile.CODEX)
        self.assertEqual(registry.closed, 1)

