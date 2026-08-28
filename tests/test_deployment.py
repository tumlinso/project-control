from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def test_project_service_is_loopback_application_and_hardened(self) -> None:
        service = (ROOT / "deployment" / "project-control.service").read_text(encoding="utf-8")
        self.assertIn("project-control serve", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertNotIn("0.0.0.0", service)

    def test_tunnel_templates_forward_only_to_loopback_without_credentials(self) -> None:
        yaml = (ROOT / "deployment" / "tunnel-client.yaml.example").read_text(encoding="utf-8")
        service = (ROOT / "deployment" / "tunnel-client.service.example").read_text(encoding="utf-8")
        combined = yaml + service
        self.assertIn("http://127.0.0.1:8767/mcp", combined)
        self.assertIn("EnvironmentFile=", combined)
        self.assertNotIn("api_key:", combined.lower())
        self.assertNotIn("token:", combined.lower())

    def test_setup_has_exact_account_gate_and_reconnect_warning(self) -> None:
        setup = (ROOT / "docs" / "CHATGPT_SETUP.md").read_text(encoding="utf-8")
        for phrase in ("Developer Mode", "Secure MCP Tunnel", "official tunnel client", "custom ChatGPT app", "exactly these fifteen tools", "fresh ChatGPT conversation", "disposable workspace"):
            self.assertIn(phrase, setup)
        self.assertIn("reconnect or recreate", setup)
        self.assertIn("Never paste credentials", setup)

    def test_future_write_is_documentation_only(self) -> None:
        future = (ROOT / "docs" / "FUTURE_WRITE_PROFILE.md").read_text(encoding="utf-8")
        self.assertIn("separate, explicit", future)
        self.assertIn("No dormant write tools", future)


if __name__ == "__main__":
    unittest.main()
