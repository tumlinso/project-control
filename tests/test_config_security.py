from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from project_control.config import (
    ProgramConfig,
    ProjectControlConfig,
    ServerConfig,
    init_config,
    load_config,
    migrate_config,
    render_config,
    save_config,
)
from project_control.registry import RegistryError, WorkspaceRegistry
from project_control.security import SecurityError, read_bounded_text, redact, redact_output, resolve_registered_path, stable_public_id


class ConfigSecurityTests(unittest.TestCase):
    def test_private_round_trip_and_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "config.toml"
            init_config(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            config = load_config(path)
            self.assertEqual(config.server.host, "127.0.0.1")
            with self.assertRaises(ValueError):
                ServerConfig(host="0.0.0.0")
            with self.assertRaises(ValueError):
                ServerConfig(host="localhost")

    def test_registry_accepts_ids_not_unregistered_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            config = ProjectControlConfig()
            registry = WorkspaceRegistry(config)
            registry.add_workspace("demo", "source", root, authority=True)
            self.assertEqual(registry.repository("demo").root, root.resolve())
            with self.assertRaises(RegistryError):
                registry.repository("missing")
            with self.assertRaises(RegistryError):
                registry.add_workspace("../bad", "source", root)

    def test_symlink_secret_binary_and_oversized_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "repo"
            root.mkdir()
            (root / "src").mkdir()
            (root / "src" / "key_value.cc").write_text("int key_value = 1;\n", encoding="utf-8")
            self.assertIn("key_value", read_bounded_text(root, "src/key_value.cc"))
            (root / ".env").write_text("SECRET=x", encoding="utf-8")
            with self.assertRaises(SecurityError):
                read_bounded_text(root, ".env")
            (root / "data.dat").write_bytes(b"a\x00b")
            with self.assertRaises(SecurityError):
                read_bounded_text(root, "data.dat")
            (root / "large.txt").write_text("12345", encoding="utf-8")
            with self.assertRaises(SecurityError):
                read_bounded_text(root, "large.txt", max_bytes=4)
            outside = base / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "escape.txt").symlink_to(outside)
            with self.assertRaises(SecurityError):
                resolve_registered_path(root, "escape.txt")

    def test_redaction_is_recursive(self) -> None:
        value = {"api_key": "visible?", "nested": ["Bearer abc.def", {"safe": "toc_abcdefghijklmnop"}]}
        result = redact(value)
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertNotIn("abc.def", str(result))
        self.assertNotIn("abcdefghijklmnop", str(result))

    def test_redaction_preserves_identifiers_and_redacts_standalone_tokens(self) -> None:
        self.assertEqual(redact("task_dependencies"), "task_dependencies")
        self.assertEqual(redact("value sk_abcdefghijklmnop value"), "value [REDACTED] value")

    def test_mcp_output_suppresses_private_paths_but_stable_ids_remain(self) -> None:
        private = "/home/example/private/worktree"
        internal = redact({"worktree_path": private, "safe": "value"})
        self.assertEqual(internal["worktree_path"], private)
        public = redact_output(internal)
        self.assertEqual(public["worktree_path"], "[REDACTED]")
        self.assertEqual(stable_public_id("wt", private), stable_public_id("wt", private))
        self.assertNotIn("home", stable_public_id("wt", private))

    def test_world_readable_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            save_config(ProjectControlConfig(), path)
            os.chmod(path, 0o644)
            with self.assertRaises(PermissionError):
                load_config(path)

    def test_schema_v1_is_read_without_implicit_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('schema_version = 1\n[server]\nhost = "127.0.0.1"\nport = 8767\ntransport = "streamable-http"\n', encoding="utf-8")
            os.chmod(path, 0o600)
            before = path.read_bytes()
            config = load_config(path)
            self.assertEqual(config.schema_version, 1)
            self.assertEqual(path.read_bytes(), before)
            preview = migrate_config(path, apply=False)
            self.assertTrue(preview["migration_required"])
            self.assertFalse(preview["applied"])
            self.assertEqual(path.read_bytes(), before)

    def test_explicit_schema_v2_migration_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            path = Path(temporary) / "config.toml"
            config = ProjectControlConfig(
                schema_version=1,
                workspaces={"demo": {"repositories": {"source": {"root": root}}}},
            )
            save_config(config, path)
            result = migrate_config(path, apply=True)
            self.assertTrue(result["applied"])
            migrated = load_config(path)
            self.assertEqual(migrated.schema_version, 2)
            self.assertIn("demo", migrated.workspaces)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_programs_render_only_under_schema_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = ProjectControlConfig(
                schema_version=2,
                workspaces={"demo": {"repositories": {"source": {"root": root}}}},
                programs={"stack": ProgramConfig(display_name="Stack", workspaces=["demo"])},
            )
            rendered = render_config(config)
            self.assertIn("[programs.stack]", rendered)
            self.assertIn('workspaces = ["demo"]', rendered)
            with self.assertRaisesRegex(ValueError, "programs require"):
                ProjectControlConfig(
                    schema_version=1,
                    workspaces=config.workspaces,
                    programs=config.programs,
                )


if __name__ == "__main__":
    unittest.main()
