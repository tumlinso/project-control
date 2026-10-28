from __future__ import annotations

import unittest
import tempfile
import asyncio
from contextlib import asynccontextmanager
from argparse import Namespace
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from scripts import verify_pa1_bootstrap as probe


class BootstrapProbeTests(unittest.TestCase):
    def test_read_calls_are_bounded_to_live_schema_fields(self):
        schema = {"properties": {"project": {}, "subject": {}, "detail": {}},
                  "required": ["project", "subject"]}
        self.assertEqual(probe._arguments("evidence", "project-control", schema), {
            "project": "project-control", "subject": "project-control", "detail": "compact"})

    def test_required_unknown_schema_is_reported_instead_of_faking_a_call(self):
        with self.assertRaisesRegex(ValueError, "required schema fields"):
            probe._arguments("machine", "project-control", {
                "properties": {"diagnostic": {}}, "required": ["diagnostic"]})

    def test_partial_and_error_results_are_not_reported_as_success(self):
        partial = probe._decode_result(SimpleNamespace(
            structuredContent={"status": "partial", "answer": {"summary": "limited"}},
            content=(), isError=False))
        failed = probe._decode_result(SimpleNamespace(
            structuredContent={"status": "denied"}, content=(), isError=False))
        transport_error = probe._decode_result(SimpleNamespace(
            structuredContent={"status": "ok"}, content=(), isError=True))
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(transport_error["status"], "error")

    def test_nested_warning_does_not_turn_successful_response_into_partial(self):
        response = probe._decode_result(SimpleNamespace(
            structuredContent={"status": "ok", "warnings": [{"status": "partial"}],
                               "data": {"answer": "useful"}}, content=(), isError=False))
        self.assertEqual(response["status"], "ok")

    def test_task_subject_is_selected_from_returned_task_packet(self):
        value = {"packet": {"payload": {"tasks": [
            {"id": "PC-PA1-QUALIFY", "title": "Qualify assistance", "status": "planned"}]}}}
        self.assertEqual(probe._task_subject(value), "PC-PA1-QUALIFY")

    def test_impact_target_is_validated_against_live_schema(self):
        schema = {
            "type": "object", "required": ["project", "targets"],
            "properties": {
                "project": {"type": "string"},
                "targets": {"type": "array", "items": {"type": "object",
                    "required": ["project", "kind", "id"],
                    "properties": {"project": {"type": "string"},
                                   "kind": {"const": "task"}, "id": {"type": "string"}}}},
                "change": {"type": "string"}, "view": {"type": "string"},
                "detail": {"type": "string"},
            },
        }
        args = probe._arguments("impact", "project-control", schema, subject="PC-PA1-QUALIFY")
        self.assertEqual(args["targets"], [{"project": "project-control", "kind": "task",
                                             "id": "PC-PA1-QUALIFY"}])

    def test_failed_call_is_recorded_and_later_call_can_continue(self):
        class Session:
            async def call_tool(self, name, arguments):
                if name == "impact":
                    raise RuntimeError("fixture tool failure")
                return SimpleNamespace(structuredContent={"status": "ok"}, content=(), isError=False)

        async def run():
            calls = {}
            checkpoints = []
            await probe._call(Session(), transport="http", tool="impact", label="impact",
                              arguments={}, timeout=0.2, calls=calls,
                              checkpoint=lambda: checkpoints.append(dict(calls)))
            await probe._call(Session(), transport="http", tool="machine", label="machine",
                              arguments={}, timeout=0.2, calls=calls,
                              checkpoint=lambda: checkpoints.append(dict(calls)))
            return calls, checkpoints

        calls, checkpoints = asyncio.run(run())
        self.assertEqual(calls["impact"]["status"], "error")
        self.assertEqual(calls["machine"]["status"], "ok")
        self.assertEqual(len(checkpoints), 2)

    def test_http_transport_failure_does_not_skip_fresh_stdio_probe(self):
        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def initialize(self):
                return None

            async def list_tools(self):
                tool = SimpleNamespace(name="overview", inputSchema={
                    "type": "object", "properties": {
                        "project": {"type": "string"}, "detail": {"type": "string"}},
                    "required": ["project"]})
                return SimpleNamespace(tools=[tool])

            async def call_tool(self, _name, _arguments):
                return SimpleNamespace(structuredContent={"status": "ok", "projects": ["demo"]},
                                       content=(), isError=False)

        @asynccontextmanager
        async def failed_http(*_args, **_kwargs):
            raise ConnectionError("HTTP unavailable")
            yield

        @asynccontextmanager
        async def fake_stdio(*_args, **_kwargs):
            yield object(), object()

        args = Namespace(http_url="http://127.0.0.1:1/mcp", project="demo", timeout=0.2,
                         assistance=False)
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            with patch.object(probe, "streamable_http_client", failed_http), \
                    patch.object(probe, "stdio_client", fake_stdio), \
                    patch.object(probe, "ClientSession", side_effect=lambda *_streams: Session()):
                receipt = asyncio.run(probe.run_probe(args, receipt_path))
            self.assertIn("HTTP unavailable", receipt["transports"]["http"]["transport_error"])
            self.assertEqual(receipt["transports"]["stdio"]["initialize"]["status"], "ok")
            self.assertEqual(receipt["transports"]["stdio"]["calls"]["overview_project"]["status"], "ok")
            self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)

    def test_catalog_extracts_project_ids_from_public_packet_shapes(self):
        value = {"packet": {"data": {"projects": [
            {"id": "project-control"}, {"project": "baseplane"}, "cellerator"]}}}
        self.assertEqual(probe._catalog_projects(value),
                         {"project-control", "baseplane", "cellerator"})

    def test_custom_receipt_does_not_change_existing_directory_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            parent.chmod(0o750)
            mode_before = parent.stat().st_mode & 0o777
            probe._write_receipt(parent / "receipt.json", {"status": "ok"})
            self.assertEqual(parent.stat().st_mode & 0o777, mode_before)
            self.assertEqual((parent / "receipt.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
