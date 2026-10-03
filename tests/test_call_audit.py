from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.testclient import TestClient

from project_control import call_audit
from project_control.app import AuditCallerMiddleware, create_asgi_app, serve
from project_control.config import ServerConfig
from project_control.observer_analysis import SkillsObserverAnalysisProvider
from project_control.profiles import ProfiledFastMCP


def _write_audit_events(directory: str, worker: int) -> None:
    call_audit.AUDIT_DIR = Path(directory)
    call_audit.MAX_FILE_BYTES = 500
    for index in range(24):
        call_audit.write_event({"event": "tool_call", "call_id": f"{worker}-{index}",
                                "tool": "project_overview", "arguments": {"question_chars": 123}})


class CallAuditTests(unittest.TestCase):
    def test_redacted_bounded_summaries_and_private_file(self) -> None:
        secret = "sk_abcdefghijklmnopqrstuvwx"
        with tempfile.TemporaryDirectory() as temporary, patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"):
            args = call_audit.summarize_arguments({
                "project": "demo", "questions": [f"Inspect request using {secret}"],
                "api_key": secret, "extra": "full private content",
            })
            messages = call_audit.summarize_messages([
                {"role": "system", "content": "private system prompt"},
                {"role": "user", "content": f"What happened with {secret}?"},
            ])
            call_audit.write_event({"event": "tool_call", "arguments": args, "messages": messages})
            path = call_audit.AUDIT_DIR / "calls.jsonl"
            raw = path.read_bytes()
            self.assertLessEqual(len(raw), call_audit.MAX_EVENT_BYTES)
            self.assertNotIn(secret.encode(), raw)
            self.assertNotIn(b"private system prompt", raw)
            self.assertNotIn(b"full private content", raw)
            self.assertIn(b"Inspect request", raw)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(call_audit.AUDIT_DIR.stat().st_mode & 0o777, 0o700)

    def test_rotation_and_concurrent_writers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"), \
                patch.object(call_audit, "MAX_FILE_BYTES", 400):
            def write(index: int) -> None:
                call_audit.write_event({"event": "tool_call", "call_id": str(index),
                                        "arguments": {"question_excerpt": "x" * 110}})

            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(write, range(80)))
            files = sorted(call_audit.AUDIT_DIR.glob("calls.jsonl*"))
            self.assertLessEqual(len(files), 1 + call_audit.BACKUPS)
            self.assertTrue((call_audit.AUDIT_DIR / "calls.jsonl.1").exists())
            for path in files:
                if path.name == "calls.lock":
                    continue
                self.assertLessEqual(path.stat().st_size, 400)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                for line in path.read_text().splitlines():
                    json.loads(line)

    def test_oversized_event_keeps_caller_and_request_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"):
            arguments = {key: "x" * 96 for key in (
                "project", "repository", "kind", "target", "detail", "effort",
                "compute_profile", "parallelism", "mode", "action", "run_id",
                "task_id", "lane_id", "campaign", "subject",
            )}
            arguments["questions"] = ["What is " + "x" * 200] * 2
            call_audit.write_event({"event": "tool_call", "phase": "started", "call_id": "case-1",
                "tool": "local_investigate", "profile": "observer",
                "caller": {"transport": "http", "peer_ip": "127.0.0.1", "peer_port": 4444,
                           "upstream_identity": "unknown"},
                "arguments": call_audit.summarize_arguments(arguments)})
            raw = (call_audit.AUDIT_DIR / "calls.jsonl").read_bytes()
            self.assertLessEqual(len(raw), call_audit.MAX_EVENT_BYTES)
            event = json.loads(raw)
            self.assertEqual(event["caller"]["peer_ip"], "127.0.0.1")
            self.assertEqual(event["tool"], "local_investigate")
            self.assertTrue(event["arguments"]["keys"])

    def test_rotation_is_safe_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = str(Path(temporary) / "audit")
            context = multiprocessing.get_context("fork")
            workers = [context.Process(target=_write_audit_events, args=(directory, index)) for index in range(3)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
                self.assertEqual(worker.exitcode, 0)
            paths = list(Path(directory).glob("calls.jsonl*"))
            self.assertLessEqual(len(paths), 1 + call_audit.BACKUPS)
            for path in paths:
                self.assertLessEqual(path.stat().st_size, 500)
                for line in path.read_text().splitlines():
                    json.loads(line)

    def test_tool_call_correlation_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"):
            server = ProfiledFastMCP("audit-test", profile="observer")

            def project_overview(project: str) -> dict[str, str]:
                self.assertIsNotNone(call_audit.call_id_var.get())
                call_audit.write_event({"event": "model_request", "call_id": call_audit.call_id_var.get()})
                return {"project": project}

            server.add_tool(project_overview)
            asyncio.run(server.call_tool("project_overview", {"project": "demo"}))
            with self.assertRaises(Exception):
                asyncio.run(server.call_tool("unregistered_tool", {"project": "demo"}))
            with self.assertRaises(Exception):
                asyncio.run(server.call_tool("project_overview", None))
            events = [json.loads(line) for line in (call_audit.AUDIT_DIR / "calls.jsonl").read_text().splitlines()]
            model = next(item for item in events if item["event"] == "model_request")
            started = next(item for item in events if item["event"] == "tool_call" and item["phase"] == "started")
            self.assertEqual(model["call_id"], started["call_id"])
            self.assertEqual(started["caller"]["transport"], "stdio")
            self.assertEqual(started["caller"]["parent_pid"], os.getppid())
            self.assertTrue(any(item.get("outcome") == "raised" for item in events))
            self.assertTrue(any(item.get("arguments", {}).get("invalid_argument_type") == "NoneType"
                                for item in events if item.get("phase") == "started"))

    def test_http_caller_is_immediate_peer(self) -> None:
        seen = []

        async def inner(scope, receive, send):
            seen.append(call_audit.caller_var.get())

        scope = {"type": "http", "client": ("127.0.0.1", 4321),
                 "headers": [(b"x-forwarded-for", b"203.0.113.7")]}
        asyncio.run(AuditCallerMiddleware(inner)(scope, None, None))
        self.assertEqual(seen, [{"transport": "http", "upstream_identity": "unknown",
                                 "peer_ip": "127.0.0.1", "peer_port": 4321}])
        self.assertIsNone(call_audit.caller_var.get())

    def test_production_server_disables_forwarded_peer_rewrite(self) -> None:
        config = SimpleNamespace(server=ServerConfig())
        with patch("project_control.app.load_config", return_value=config), \
                patch("project_control.app.create_asgi_app", return_value=object()), \
                patch("uvicorn.run") as run:
            self.assertEqual(serve(), 0)
        self.assertFalse(run.call_args.kwargs["proxy_headers"])

    def test_http_tool_call_reaches_audit_with_peer_and_unknown_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"):
            server = ProfiledFastMCP("audit-test", profile="observer", host="127.0.0.1", port=8767,
                                      stateless_http=True, json_response=True)

            def project_overview(project: str) -> dict[str, str]:
                call_audit.write_event({"event": "model_request", "call_id": call_audit.call_id_var.get()})
                return {"project": project}

            server.add_tool(project_overview)
            server._project_control_runtime = SimpleNamespace(terminals=SimpleNamespace(shutdown=lambda: None))
            server._project_control_observer_analysis_registry = SimpleNamespace(close=lambda: None)
            with patch("project_control.app.create_mcp", return_value=server):
                with TestClient(create_asgi_app(), base_url="http://127.0.0.1:8767") as client:
                    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                        "method": "tools/call", "params": {"name": "project_overview",
                        "arguments": {"project": "demo"}}},
                        headers={"accept": "application/json, text/event-stream",
                                 "x-forwarded-for": "203.0.113.7"})
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line) for line in (call_audit.AUDIT_DIR / "calls.jsonl").read_text().splitlines()]
            started = next(item for item in events if item.get("phase") == "started")
            self.assertEqual(started["caller"]["transport"], "http")
            self.assertEqual(started["caller"]["peer_ip"], "testclient")
            self.assertEqual(started["caller"]["upstream_identity"], "unknown")
            model = next(item for item in events if item["event"] == "model_request")
            self.assertEqual(model["call_id"], started["call_id"])
            self.assertNotIn("203.0.113.7", str(events))

    def test_model_request_records_summary_and_shared_call_id(self) -> None:
        class Backend:
            def run_observer_turn(self, request):
                return {"status": "available", "text": "private response"}

            def analyze_observer_packet(self, packet):
                return {"status": "available"}

        with tempfile.TemporaryDirectory() as temporary, patch.object(call_audit, "AUDIT_DIR", Path(temporary) / "audit"):
            provider = SkillsObserverAnalysisProvider(temporary)
            token = call_audit.call_id_var.set("test-call-id")
            try:
                with patch.object(provider, "_get_backend", return_value=Backend()):
                    provider.investigate_turn({"messages": [
                        {"role": "user", "content": "Inspect sk_abcdefghijklmnopqrstuvwx"}]})
                    provider.analyze({"evidence": [{"private": "do not log"}]})
            finally:
                call_audit.call_id_var.reset(token)
            raw = (call_audit.AUDIT_DIR / "calls.jsonl").read_text()
            events = [json.loads(line) for line in raw.splitlines()]
            self.assertEqual(len(events), 4)
            self.assertTrue(all(item["call_id"] == "test-call-id" for item in events))
            self.assertEqual({item["operation"] for item in events}, {"investigate_turn", "observer_analyze"})
            self.assertNotIn("sk_abcdefghijklmnopqrstuvwx", raw)
            self.assertNotIn("do not log", raw)
            self.assertNotIn("private response", raw)


if __name__ == "__main__":
    unittest.main()
