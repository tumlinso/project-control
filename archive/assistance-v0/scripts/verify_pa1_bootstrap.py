#!/usr/bin/env python3
"""Bounded, model-free acceptance probe for the public PC MCP surfaces.

The probe performs read-only information calls over the configured source HTTP
server and a new source ``codex`` stdio process. It writes a private JSON
receipt and never calls workflow mutation tools. Assistance calls are opt-in.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


REPO_ROOT = Path(__file__).resolve().parents[1]
READ_TOOLS = ("overview", "search", "frontier", "evidence", "history", "impact", "machine")
HTTP_DEFAULT = "http://127.0.0.1:8768/mcp"
PARTIAL_STATUSES = {"partial", "unavailable", "refresh_required", "busy", "pending", "degraded"}
ERROR_STATUSES = {"error", "failed", "refused", "denied", "invalid", "blocked"}


def _decode_result(result: Any) -> dict[str, Any]:
    """Keep the server's returned payload intact and classify it honestly."""
    payload: Any = getattr(result, "structuredContent", None)
    if payload is None:
        payload = getattr(result, "structured_content", None)
    if payload is None:
        content = []
        for item in getattr(result, "content", ()) or ():
            value = getattr(item, "text", None)
            if value is not None:
                try:
                    content.append(json.loads(value))
                except (TypeError, ValueError):
                    content.append(value)
        payload = content[0] if len(content) == 1 else content
    if not isinstance(payload, (dict, list, str, int, float, bool, type(None))):
        payload = repr(payload)
    status = "error" if bool(getattr(result, "isError", False)) else "ok"
    server_status = _top_level_status(payload)
    if status == "ok" and server_status in ERROR_STATUSES:
        status = "error"
    elif status == "ok" and server_status in PARTIAL_STATUSES:
        status = "partial"
    return {"status": status, "server_status": server_status, "payload": payload,
            "is_error": bool(getattr(result, "isError", False))}


def _top_level_status(value: Any) -> str | None:
    """Read status only from known response envelopes, never nested warnings."""
    if not isinstance(value, dict):
        return None
    for key in ("status", "result_status"):
        status = value.get(key)
        if isinstance(status, str):
            return status.lower()
    for key in ("data", "result", "packet"):
        nested = value.get(key)
        if isinstance(nested, dict):
            status = nested.get("status")
            if isinstance(status, str):
                return status.lower()
            payload = nested.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("status"), str):
                return payload["status"].lower()
    return None


def _input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    return schema if isinstance(schema, dict) else {}


def _required(schema: dict[str, Any]) -> set[str]:
    required = schema.get("required", [])
    return set(required) if isinstance(required, list) else set()


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_error_text(child) for child in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def _task_subject(value: Any) -> str | None:
    """Find a real task identity in overview/frontier packets for trace reads."""
    if isinstance(value, dict):
        candidate = value.get("task_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        candidate = value.get("id")
        if isinstance(candidate, str) and (
                "objective" in value or "title" in value or value.get("kind") == "task"):
            return candidate
        for nested in value.values():
            found = _task_subject(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _task_subject(nested)
            if found:
                return found
    return None


def _arguments(name: str, project: str, schema: dict[str, Any], *,
               subject: str | None = None) -> dict[str, Any]:
    """Build deliberately small read requests from the live tools/list schema."""
    if name == "impact" and not subject:
        raise ValueError("impact requires a task identity observed in overview or frontier")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        props = {}
    values: dict[str, Any] = {
        "overview": {"project": project, "detail": "compact"},
        "search": {"project": project, "query": subject or "project"},
        "frontier": {"project": project, "detail": "compact"},
        "evidence": {"project": project, "subject": subject or project, "detail": "compact"},
        "history": {"project": project, "subject": subject or project, "detail": "compact"},
        "impact": {"project": project, "targets": ([{"project": project, "kind": "task", "id": subject}]
                                                       if subject else []),
                   "change": "unknown", "view": "paths", "detail": "compact"},
        "machine": {"query_or_view": "host_memory", "detail": "compact"},
    }
    candidate = {key: value for key, value in values.get(name, {}).items() if key in props}
    missing = _required(schema) - candidate.keys()
    if missing:
        raise ValueError(f"no bounded read arguments for {name}; required schema fields: {sorted(missing)}")
    try:
        jsonschema.validate(candidate, schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"bounded arguments fail live {name} schema: {exc.message}") from exc
    return candidate


def _catalog_projects(value: Any) -> set[str]:
    """Extract configured project names from the returned overview packet."""
    found: set[str] = set()
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"projects", "catalog", "workspaces"} and isinstance(item, list):
                    for entry in item:
                        if isinstance(entry, str):
                            found.add(entry)
                        elif isinstance(entry, dict):
                            for name in ("project", "id", "name", "alias"):
                                if isinstance(entry.get(name), str):
                                    found.add(entry[name])
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(value)
    return found


async def _call(session: ClientSession, *, transport: str, tool: str, label: str,
                arguments: dict[str, Any], timeout: float, calls: dict[str, Any],
                checkpoint) -> dict[str, Any]:
    started = asyncio.get_running_loop().time()
    print(f"START {transport}.{label} ({tool})", flush=True)
    try:
        response = await asyncio.wait_for(session.call_tool(tool, arguments), timeout=timeout)
        record = _decode_result(response)
    except asyncio.TimeoutError:
        record = {"status": "timeout", "server_status": None,
                  "error": f"request exceeded {timeout:g}s deadline", "payload": None}
    except Exception as exc:
        record = {"status": "error", "server_status": None,
                  "error": _error_text(exc), "payload": None}
    record["elapsed_seconds"] = round(asyncio.get_running_loop().time() - started, 3)
    calls[label] = record
    checkpoint()
    print(f"DONE {transport}.{label} status={record['status']} "
          f"elapsed={record['elapsed_seconds']:.3f}s", flush=True)
    return record


async def _exercise(session: ClientSession, *, transport: str, project: str,
                    do_assistance: bool, timeout: float, result: dict[str, Any],
                    checkpoint) -> None:
    started = asyncio.get_running_loop().time()
    print(f"START {transport}.initialize", flush=True)
    try:
        await asyncio.wait_for(session.initialize(), timeout=timeout)
        result["initialize"] = {"status": "ok",
                                 "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 3)}
    except Exception as exc:
        result["initialize"] = {"status": "timeout" if isinstance(exc, asyncio.TimeoutError) else "error",
                                "error": _error_text(exc),
                                "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 3)}
        print(f"DONE {transport}.initialize status={result['initialize']['status']} "
              f"elapsed={result['initialize']['elapsed_seconds']:.3f}s", flush=True)
        checkpoint()
        return
    print(f"DONE {transport}.initialize status=ok "
          f"elapsed={result['initialize']['elapsed_seconds']:.3f}s", flush=True)
    checkpoint()
    started = asyncio.get_running_loop().time()
    print(f"START {transport}.tools_list", flush=True)
    try:
        listed = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        schemas = {tool.name: _input_schema(tool) for tool in listed.tools}
        result["tools_list"] = {"status": "ok", "count": len(schemas), "names": sorted(schemas),
                                 "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 3)}
    except Exception as exc:
        result["tools_list"] = {"status": "timeout" if isinstance(exc, asyncio.TimeoutError) else "error",
                                "error": _error_text(exc),
                                "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 3)}
        print(f"DONE {transport}.tools_list status={result['tools_list']['status']} "
              f"elapsed={result['tools_list']['elapsed_seconds']:.3f}s", flush=True)
        checkpoint()
        return
    print(f"DONE {transport}.tools_list status=ok "
          f"elapsed={result['tools_list']['elapsed_seconds']:.3f}s", flush=True)
    checkpoint()
    result["status"] = "connected"
    result["calls"] = {}
    calls = result["calls"]
    if "overview" not in schemas:
        calls["overview_catalog"] = {"status": "missing_tool", "payload": None}
        checkpoint()
        return

    # Catalog and configured-project overview are separate public reads.
    overview_schema = schemas["overview"]
    overview_args = {key: value for key, value in {"detail": "compact"}.items()
                     if key in overview_schema.get("properties", {})}
    catalog_record = await _call(session, transport=transport, tool="overview", label="overview_catalog",
                arguments=overview_args, timeout=timeout, calls=calls, checkpoint=checkpoint)
    available = _catalog_projects(catalog_record.get("payload"))
    result["catalog_projects"] = sorted(available)
    result["project_catalog_match"] = (
        "present" if project in available else "not_listed" if available else "not_exposed")
    checkpoint()
    try:
        project_overview_args = _arguments("overview", project, overview_schema)
    except Exception as exc:
        calls["overview_project"] = {"status": "invalid_probe_arguments", "error": str(exc), "payload": None}
        checkpoint()
    else:
        await _call(session, transport=transport, tool="overview", label="overview_project",
                    arguments=project_overview_args, timeout=timeout,
                    calls=calls, checkpoint=checkpoint)
    frontier_schema = schemas.get("frontier")
    if frontier_schema:
        try:
            frontier_args = _arguments("frontier", project, frontier_schema)
        except Exception as exc:
            calls["frontier"] = {"status": "invalid_probe_arguments", "error": str(exc), "payload": None}
            checkpoint()
        else:
            await _call(session, transport=transport, tool="frontier", label="frontier",
                        arguments=frontier_args, timeout=timeout,
                        calls=calls, checkpoint=checkpoint)
    task_id = (_task_subject(calls.get("frontier", {}).get("payload"))
               or _task_subject(calls.get("overview_project", {}).get("payload")))
    subject = task_id or project
    result["selected_subject"] = subject
    result["selected_task_id"] = task_id
    checkpoint()
    for name in ("search", "evidence", "history", "impact", "machine"):
        if name not in schemas:
            calls[name] = {"status": "missing_tool", "payload": None}
            checkpoint()
            continue
        try:
            arguments = _arguments(name, project, schemas[name],
                                   subject=task_id if name == "impact" else subject)
        except Exception as exc:
            calls[name] = {"status": "invalid_probe_arguments", "error": str(exc), "payload": None}
            checkpoint()
            print(f"DONE {transport}.{name} status=invalid_probe_arguments", flush=True)
            continue
        await _call(session, transport=transport, tool=name, label=name, arguments=arguments,
                    timeout=timeout, calls=calls, checkpoint=checkpoint)

    if do_assistance:
        for name, args in (("investigate", {"project": project,
                                           "question": f"Summarize the registered {project} project using verified evidence."}),
                           ("skill", {"query": "Project Control source development and safe workflow guidance"})):
            if name not in schemas:
                calls[name] = {"status": "missing_tool", "payload": None}
                checkpoint()
                continue
            args = {key: value for key, value in args.items()
                    if key in schemas[name].get("properties", {})}
            missing = _required(schemas[name]) - args.keys()
            if not missing:
                try:
                    jsonschema.validate(args, schemas[name])
                except jsonschema.ValidationError as exc:
                    missing = {f"schema validation: {exc.message}"}
            if missing:
                calls[name] = {"status": "unsupported_schema", "missing": sorted(missing)}
                checkpoint()
            else:
                await _call(session, transport=transport, tool=name, label=name, arguments=args,
                            timeout=timeout, calls=calls, checkpoint=checkpoint)


async def run_probe(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "format": "pc-pa1-public-surface-receipt/1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(REPO_ROOT),
        "http_url": args.http_url,
        "project": args.project,
        "scope": {"model_assistance": bool(args.assistance), "workflow_mutation": False},
        "transports": {},
    }
    _write_receipt(output, receipt)

    def checkpoint() -> None:
        _write_receipt(output, receipt)

    receipt["transports"]["http"] = {"status": "running", "calls": {}}
    checkpoint()
    try:
        async with httpx.AsyncClient(timeout=args.timeout) as http_client:
            async with streamable_http_client(args.http_url, http_client=http_client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await _exercise(session, transport="http", project=args.project,
                                    do_assistance=args.assistance, timeout=args.timeout,
                                    result=receipt["transports"]["http"], checkpoint=checkpoint)
    except Exception as exc:
        receipt["transports"]["http"].update({"status": "error",
                                                "transport_error": _error_text(exc)})
        checkpoint()
    parameters = StdioServerParameters(
        command=str(REPO_ROOT / "scripts" / "pc-dev"),
        args=["run", "codex"],
        cwd=str(REPO_ROOT),
        env={"PROJECT_CONTROL_BOOTSTRAP_PROJECT": args.project},
    )
    receipt["transports"]["stdio"] = {"status": "running", "calls": {}}
    checkpoint()
    try:
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await _exercise(session, transport="stdio", project=args.project,
                                do_assistance=args.assistance, timeout=args.timeout,
                                result=receipt["transports"]["stdio"], checkpoint=checkpoint)
    except Exception as exc:
        receipt["transports"]["stdio"].update({"status": "error",
                                                "transport_error": _error_text(exc)})
        checkpoint()
    failures = []
    for transport, record in receipt["transports"].items():
        if record.get("transport_error"):
            failures.append(f"{transport}.transport={record['transport_error']}")
        for call, value in record.get("calls", {}).items():
            if value.get("status") != "ok":
                failures.append(f"{transport}.{call}={value.get('status')}")
    all_statuses = [value.get("status") for record in receipt["transports"].values()
                    for value in record.get("calls", {}).values()]
    all_statuses.extend(record.get(key, {}).get("status") for record in receipt["transports"].values()
                         for key in ("initialize", "tools_list") if key in record)
    receipt["status"] = "ok" if not failures else "partial" if all(
        status in {"ok", "partial"} for status in all_statuses) and not any(
            record.get("transport_error") for record in receipt["transports"].values()) else "error"
    receipt["failures"] = failures
    checkpoint()
    return receipt


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-url", default=os.environ.get("PROJECT_CONTROL_MCP_URL", HTTP_DEFAULT))
    parser.add_argument("--project", default=os.environ.get("PROJECT_CONTROL_BOOTSTRAP_PROJECT", "project-control"))
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="per-transport request budget in seconds (default: 30)")
    parser.add_argument("--output", type=Path, help="receipt path; defaults to private XDG state")
    parser.add_argument("--assistance", action="store_true",
                        help="also send one bounded investigate and skill request; may load inference")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout <= 0 or args.timeout > 120:
        print("--timeout must be greater than zero and at most 120 seconds", file=sys.stderr)
        return 2
    output = args.output
    if output is None:
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = state / "project-control" / "qualification" / "pa1-bootstrap" / f"public-surface-{stamp}.json"
    try:
        receipt = asyncio.run(run_probe(args, output))
    except Exception as exc:
        try:
            receipt = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            receipt = {"format": "pc-pa1-public-surface-receipt/1",
                       "created_at": datetime.now(timezone.utc).isoformat(),
                       "http_url": args.http_url, "project": args.project,
                       "scope": {"model_assistance": bool(args.assistance),
                                 "workflow_mutation": False}, "transports": {}}
        receipt.update({"status": "error", "failure": _error_text(exc),
                        "failure_detail": "".join(traceback.format_exception(exc))})
    _write_receipt(output, receipt)
    print(json.dumps({"status": receipt.get("status"), "receipt": str(output),
                      "failures": receipt.get("failures", []),
                      "failure": receipt.get("failure")}, sort_keys=True))
    return 0 if receipt.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
