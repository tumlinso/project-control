from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import time
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from project_control.assistance.policies import generation_settings, policy_for

from ..service import AdapterError
from ..observer_runtime import remaining_seconds
from ..residency import process_identity, terminate_owned


def _bounded_shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Describe non-content response fields without retaining an unbounded body."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, str):
        return {"type": "string", "characters": len(value), "bytes": len(value.encode("utf-8")),
                "prefix": value[:256], "suffix": value[-256:] if len(value) > 256 else ""}
    if isinstance(value, (bool, int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, list):
        return {"type": "array", "length": len(value),
                "items": [] if depth >= 2 else [_bounded_shape(item, depth=depth + 1) for item in value[:2]]}
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value)[:16]
        return {"type": "object", "keys": keys,
                "fields": {} if depth >= 2 else {
                    key: _bounded_shape(value[key], depth=depth + 1) for key in keys[:8]}}
    return {"type": type(value).__name__}


def _http_json(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return int(response.status), body
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {"error": str(error)}
        return int(error.code), body


class LlamaCppServerAdapter:
    adapter_name = "llama.cpp-server"

    def __init__(self, binary: str = "llama-server", *, process_factory=subprocess.Popen,
                 transport: Callable[[str, str, dict[str, Any] | None, float], tuple[int, dict[str, Any]]] = _http_json,
                 help_runner: Callable[..., Any] = subprocess.run,
                 sleeper: Callable[[float], None] = time.sleep,
                 identity_reader: Callable[[int], dict] = process_identity,
                 owned_terminator: Callable[[dict], None] = terminate_owned) -> None:
        self.binary = binary
        self.process_factory = process_factory
        self.transport = transport
        self.help_runner = help_runner
        self.sleeper = sleeper
        self.identity_reader = identity_reader
        self.owned_terminator = owned_terminator
        self._servers: dict[str, dict[str, Any]] = {}
        self._supported_flags: set[str] | None = None

    def _flags(self, binary: str, deadline_epoch=None) -> set[str]:
        if self._supported_flags is None:
            result = self.help_runner([binary, "--help"], capture_output=True, text=True, timeout=min(10, remaining_seconds(deadline_epoch)) if deadline_epoch is not None else 10, check=False)
            text = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
            self._supported_flags = {word.rstrip(",=") for word in text.split() if word.startswith("--")}
        return self._supported_flags

    def _resolved_binary(self) -> str | None:
        if os.path.sep in self.binary:
            path = Path(self.binary)
            return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
        return shutil.which(self.binary)

    def inspect(self) -> dict[str, Any]:
        resolved = self._resolved_binary()
        return {
            "adapter": self.adapter_name, "available": resolved is not None, "binary": resolved,
            "protocol": "llama.cpp-openai-http", "health_path": "/health",
            "run_path": "/v1/chat/completions",
            "capabilities": ["inspect", "start", "health", "run", "cancel", "drain", "evict", "usage"],
        }

    def start(self, context: dict[str, Any]) -> str:
        deadline_epoch = context.get("deadline_epoch")
        if deadline_epoch is not None:
            remaining_seconds(deadline_epoch)
        binary = self._resolved_binary()
        if binary is None:
            raise AdapterError("llama-server binary is unavailable")
        model = Path(str(context.get("model_path", ""))).resolve()
        if not model.is_file():
            raise AdapterError("llama.cpp start requires an existing local model_path")
        repo_root = context.get("repo_root")
        if repo_root:
            repo = Path(str(repo_root)).resolve()
            if model == repo or repo in model.parents:
                raise AdapterError("model weights must live outside the repository")
        host = str(context.get("host", "127.0.0.1"))
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise AdapterError("llama.cpp adapter binds loopback only")
        port = int(context.get("port", 8080))
        if not 1 <= port <= 65535:
            raise AdapterError("llama.cpp port is invalid")
        profile = dict(context.get("service_profile") or {})
        if profile and profile.get("format") != "CORE4-MODEL-SERVICE/2":
            raise AdapterError("unsupported model-service profile format")
        if profile.get("observer_generation"):
            profile["observer_generation"] = self._generation_policy(profile["observer_generation"])
        flags = self._flags(binary, deadline_epoch)
        argv = [binary, "--model", str(model), "--host", host, "--port", str(port)]
        options = {
            "--ctx-size": profile.get("context_size", context.get("ctx_size")),
            "--n-gpu-layers": profile.get("gpu_layers", context.get("gpu_layers")),
            "--split-mode": profile.get("split_mode"),
            "--tensor-split": profile.get("tensor_split"),
            "--main-gpu": profile.get("main_gpu"),
            "--cache-type-k": profile.get("kv_cache_type_k"),
            "--cache-type-v": profile.get("kv_cache_type_v"),
            "--numa": profile.get("numa_policy"),
            "--threads": profile.get("cpu_threads"),
            "--parallel": profile.get("parallel_slots"),
            "--batch-size": profile.get("batch_size"),
            "--ubatch-size": profile.get("ubatch_size"),
            "--flash-attn": profile.get("flash_attention"),
        }
        for flag, value in options.items():
            if value is not None and flag in flags:
                if isinstance(value, list):
                    value = ",".join(str(item) for item in value)
                argv.extend([flag, str(value)])
        if profile.get("fit") and "--fit" in flags:
            argv.append("--fit")
        log_path = Path(str(profile.get("log_path") or context.get("log_path") or os.devnull)).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_stream = open(log_path, "a", encoding="utf-8")
        environment = os.environ.copy()
        environment["GGML_CUDA_P2P"] = "1"
        gpu_uuids = profile.get("allocated_gpu_uuids") or context.get("allocated_gpu_uuids")
        if gpu_uuids:
            environment["CUDA_VISIBLE_DEVICES"] = ",".join(str(item) for item in gpu_uuids)
        if deadline_epoch is not None:
            try:
                remaining_seconds(deadline_epoch)
            except TimeoutError:
                log_stream.close()
                raise
        process = self.process_factory(argv, stdout=log_stream, stderr=subprocess.STDOUT, text=True,
                                       env=environment, start_new_session=True)
        handle = str(uuid.uuid4())
        self._servers[handle] = {
            "process": process, "base_url": f"http://{host}:{port}", "accepting": True,
            "evicted": False, "canceled": set(), "active_requests": set(), "log_stream": log_stream, "log_path": str(log_path),
            "profile": profile,
            "reasoning_state": {},
            "last_completion_tokens": None,
            "observer_generation": copy.deepcopy(profile.get("observer_generation") or {}),
            "conservative_tokens_per_second": (profile.get("observer_generation") or {}).get(
                "conservative_tokens_per_second"),
            "conservative_prompt_tokens_per_second": (profile.get("observer_generation") or {}).get(
                "conservative_prompt_tokens_per_second"),
            "usage": {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0, "duration_ms": 0.0},
        }
        try:
            self._servers[handle]["termination_identity"] = self.identity_reader(process.pid)
        except Exception as error:
            failure = AdapterError(f"model_spawn_identity_incomplete: {error}")
            failure.owned_handle = handle
            raise failure from error
        on_spawn = context.get("on_spawn")
        if on_spawn is not None:
            try:
                on_spawn(handle, self.owned_process_descriptor(handle))
            except Exception as error:
                failure = AdapterError(f"model_spawn_ownership_incomplete: {error}")
                failure.owned_handle = handle
                raise failure from error
        timeout = float(profile.get("startup_timeout_seconds", context.get("startup_timeout_seconds", 0)))
        if deadline_epoch is not None:
            try:
                remaining = remaining_seconds(deadline_epoch)
            except TimeoutError:
                self._startup_evict(handle)
                raise
            timeout = min(timeout, remaining) if timeout > 0 else remaining
        if timeout > 0:
            deadline = time.monotonic() + timeout
            timer = threading.Timer(timeout, self._startup_evict, args=(handle,)) if deadline_epoch is not None else None
            if timer is not None:
                timer.daemon = True
                timer.start()
            try:
                while True:
                    if process.poll() is not None:
                        self._startup_evict(handle)
                        raise AdapterError(f"llama-server exited during startup; log: {log_path}")
                    if time.monotonic() >= deadline:
                        self._startup_evict(handle)
                        raise AdapterError("llama-server startup timed out")
                    try:
                        status, _ = self.transport("GET", f"http://{host}:{port}/health", None, min(2.0, deadline - time.monotonic()))
                    except (OSError, urllib.error.URLError):
                        status = 0
                    if status == 200 and time.monotonic() < deadline:
                        break
                    if time.monotonic() >= deadline:
                        self._startup_evict(handle)
                        raise AdapterError(f"llama-server startup timed out; log: {log_path}")
                    self.sleeper(min(0.1, max(0, deadline - time.monotonic())))
            finally:
                if timer is not None:
                    timer.cancel()
                    timer.join()

        if profile.get("observer_generation"):
            try:
                status, props = self.transport("GET", f"http://{host}:{port}/props", None,
                                               min(2.0, remaining_seconds(deadline_epoch)) if deadline_epoch is not None else 2.0)
                context_size = props.get("default_generation_settings", {}).get("n_ctx") if status == 200 else None
                if isinstance(context_size, bool) or not isinstance(context_size, int) or context_size < 1:
                    raise AdapterError("observer_actual_context_unavailable")
                self._servers[handle]["effective_context_size"] = context_size
            except Exception as error:
                try:
                    self._startup_evict(handle)
                except Exception:
                    pass
                if isinstance(error, AdapterError):
                    raise
                raise AdapterError("observer_actual_context_unavailable") from error

        return handle

    def _startup_evict(self, handle: str) -> None:
        try:
            self.evict(handle)
        except Exception as error:
            failure = AdapterError(f"model_startup_cleanup_incomplete: {error}")
            failure.owned_handle = handle
            raise failure from error

    def _server(self, handle: str) -> dict[str, Any]:
        try:
            return self._servers[handle]
        except KeyError as error:
            raise AdapterError("unknown llama.cpp server handle") from error

    def owned_process_descriptor(self, handle: str) -> dict[str, Any]:
        """Local process identity independent of HTTP endpoint readiness."""
        server = self._server(handle)
        return {"pid": server["process"].pid, "base_url": server["base_url"]}

    def health(self, handle: str) -> dict[str, Any]:
        server = self._server(handle)
        process = server["process"]
        if server["evicted"] or process.poll() is not None:
            return {"healthy": False, "state": "evicted" if server["evicted"] else "stopped"}
        status, body = self.transport("GET", server["base_url"] + "/health", None, 2.0)
        state = "draining" if not server["accepting"] else ("ready" if status == 200 else "loading")
        return {"healthy": status == 200, "state": state, "status_code": status, "details": body}

    def describe(self, handle: str) -> dict[str, Any]:
        """Return owned process identity without exposing the mutable server record."""
        server = self._server(handle)
        return {"base_url": server["base_url"], "pid": int(server["process"].pid),
                "log_path": server["log_path"], "profile": dict(server["profile"])}

    def run(self, handle: str, request: dict[str, Any]) -> dict[str, Any]:
        server = self._server(handle)
        if server["evicted"] or not server["accepting"]:
            raise AdapterError("llama.cpp server is not accepting work")
        request_id = str(request.get("request_id") or uuid.uuid4())
        if request_id in server["canceled"]:
            return {"status": "canceled", "request_id": request_id}
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise AdapterError("llama.cpp request requires messages")
        policy = request.get("observer_generation")
        if policy is None:
            policy = server.get("observer_generation") or {}
        if policy:
            if len(messages) > 24 or len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
                raise AdapterError("observer_input_budget_exceeded")
            return self._run_observer_generation(handle, request, policy)
        if len(json.dumps(messages, ensure_ascii=False)) > 200_000:
            raise AdapterError("llama.cpp messages exceed the adapter payload bound")
        payload = {"messages": messages, "stream": False}
        if request.get("model"):
            payload["model"] = request["model"]
        if request.get("max_tokens") is not None:
            payload["max_tokens"] = int(request["max_tokens"])
        if "response_format" in request:
            response_format = request["response_format"]
            if (not isinstance(response_format, dict) or set(response_format) != {"type", "schema"}
                    or response_format.get("type") != "json_object"
                    or not isinstance(response_format.get("schema"), dict)
                    or response_format["schema"].get("type") != "object"):
                raise AdapterError("llama.cpp response_format requires a JSON object schema")
            try:
                encoded_schema = json.dumps(response_format["schema"], ensure_ascii=False, allow_nan=False)
                if len(encoded_schema.encode("utf-8")) > 16 * 1024:
                    raise ValueError("schema too large")
                decoded_schema = json.loads(encoded_schema)
                pending = [decoded_schema]
                while pending:
                    node = pending.pop()
                    if isinstance(node, dict):
                        if "$ref" in node and (not isinstance(node["$ref"], str) or not node["$ref"].startswith("#/")):
                            raise ValueError("external schema reference")
                        pending.extend(node.values())
                    elif isinstance(node, list):
                        pending.extend(node)
            except (TypeError, ValueError, RecursionError):
                raise AdapterError("llama.cpp response_format schema invalid or exceeds bound") from None
            payload["response_format"] = {"type": "json_object", "schema": decoded_schema}
        if "temperature" in request:
            temperature = request["temperature"]
            if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                    or not math.isfinite(temperature) or not 0 <= temperature <= 2):
                raise AdapterError("llama.cpp temperature must be finite and between 0 and 2")
            payload["temperature"] = temperature
        started = time.perf_counter()
        timeout = request.get("timeout_seconds", 600)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
            raise AdapterError("llama.cpp timeout_seconds must be between 0 and 3600")
        deadline_epoch = request.get("deadline_epoch")
        timer = None
        if deadline_epoch is not None:
            timeout = min(float(timeout), 60.0, remaining_seconds(deadline_epoch))
        server.setdefault("active_requests", set()).add(request_id)
        if deadline_epoch is not None:
            # This handle belongs to the exclusive inquiry session. Stop its
            # model operation at the hard bound, then wait for transport to end.
            timer = threading.Timer(timeout, self.cancel, args=(handle, request_id))
            timer.daemon = True
            timer.start()
        try:
            status, body = self.transport("POST", server["base_url"] + "/v1/chat/completions", payload, float(timeout))
        except (OSError, urllib.error.URLError) as error:
            if deadline_epoch is not None:
                self.cancel(handle, request_id)
                raise AdapterError("observer_model_turn_timed_out") from error
            raise
        finally:
            if timer is not None:
                timer.cancel()
                timer.join()
            server["active_requests"].discard(request_id)
        if deadline_epoch is not None and (request_id in server["canceled"] or time.time() >= deadline_epoch):
            self.evict(handle)
            raise AdapterError("observer_model_turn_timed_out")
        duration = (time.perf_counter() - started) * 1000
        if status != 200:
            raise AdapterError(f"llama.cpp completion failed with HTTP {status}")
        if request_id in server["canceled"]:
            return {"status": "canceled", "request_id": request_id}
        usage = dict(body.get("usage") or {})
        server["usage"]["runs"] += 1
        server["usage"]["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        server["usage"]["completion_tokens"] += int(usage.get("completion_tokens", 0))
        server["usage"]["duration_ms"] += duration
        choices = body.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if "<think>" in text:
            close = text.rfind("</think>")
            text = text[close + len("</think>"):] if close >= 0 else ""
        other_keys = sorted(key for key in message if key not in {"content", "tool_calls", "reasoning_content"})[:8]
        other_fields = {key: _bounded_shape(message[key]) for key in other_keys}
        response_metadata = {
            "finish_reason": choice.get("finish_reason"),
            "message": {
                "content_present": "content" in message,
                "content_type": type(content).__name__,
                "content_characters": len(text),
                "content_bytes": len(text.encode("utf-8")),
                "field_names": sorted(str(key) for key in message)[:24],
                "tool_calls": _bounded_shape(message.get("tool_calls")) if "tool_calls" in message else {"present": False},
                # Never retain thought text in response metadata.
                "reasoning_content": ({"present": True, "characters": len(message["reasoning_content"]),
                    "bytes": len(message["reasoning_content"].encode("utf-8"))}
                    if isinstance(message.get("reasoning_content"), str) else {"present": False}),
                "other_fields": other_fields,
            },
            "choice_fields": sorted(str(key) for key in choice)[:16],
        }
        return {"status": "succeeded", "request_id": request_id, "text": text[:20_000], "usage": usage,
                "duration_ms": round(duration, 3), "raw_output_omitted_chars": max(len(text) - 20_000, 0),
                "response_metadata": response_metadata}

    _GENERATION_DEFAULTS = {
        "reasoning_tokens": 4096, "preserve_reasoning": True,
        "thinking_temperature": 0.6, "direct_temperature": 0.7,
        "top_k": 20, "thinking_top_p": 0.95, "direct_top_p": 0.8,
        "min_p": 0.0, "presence_penalty": 0.0, "min_answer_seconds": 15,
    }
    _GENERATION_FIELDS = frozenset((*_GENERATION_DEFAULTS, "conservative_tokens_per_second",
                                   "conservative_prompt_tokens_per_second"))

    def _generation_policy(self, policy: Any) -> dict[str, Any]:
        if not isinstance(policy, dict) or set(policy) - self._GENERATION_FIELDS:
            raise AdapterError("observer_generation_policy_invalid")
        result = dict(self._GENERATION_DEFAULTS)
        result.update(policy)
        cap = result["reasoning_tokens"]
        if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= 16384:
            raise AdapterError("observer_generation_policy_invalid")
        if not isinstance(result["preserve_reasoning"], bool):
            raise AdapterError("observer_generation_policy_invalid")
        for key in ("thinking_temperature", "direct_temperature", "thinking_top_p", "direct_top_p", "min_p", "presence_penalty"):
            value = result[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise AdapterError("observer_generation_policy_invalid")
        for key in ("thinking_temperature", "direct_temperature"):
            if not 0 <= result[key] <= 2:
                raise AdapterError("observer_generation_policy_invalid")
        for key in ("thinking_top_p", "direct_top_p", "min_p"):
            if not 0 <= result[key] <= 1:
                raise AdapterError("observer_generation_policy_invalid")
        if not 0 <= result["presence_penalty"] <= 2:
            raise AdapterError("observer_generation_policy_invalid")
        for key in ("top_k", "min_answer_seconds"):
            value = result[key]
            if (isinstance(value, bool) or not isinstance(value, int) or value < 0
                    or (key == "min_answer_seconds" and value > 59)):
                raise AdapterError("observer_generation_policy_invalid")
        seed = result.get("conservative_tokens_per_second")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, (int, float))
                                 or not math.isfinite(seed) or seed <= 0):
            raise AdapterError("observer_generation_policy_invalid")
        prompt_seed = result.get("conservative_prompt_tokens_per_second")
        if prompt_seed is not None and (isinstance(prompt_seed, bool) or not isinstance(prompt_seed, (int, float))
                                        or not math.isfinite(prompt_seed) or prompt_seed <= 0):
            raise AdapterError("observer_generation_policy_invalid")
        return result

    def set_observer_generation(self, handle: str, policy: dict[str, Any]) -> None:
        """Set bounded private generation tuning on an owned server for qualification."""
        server = self._server(handle)
        normalized = self._generation_policy(policy)
        server["observer_generation"] = normalized
        seed = normalized.get("conservative_tokens_per_second")
        if seed is not None:
            server["conservative_tokens_per_second"] = float(seed)
        prompt_seed = normalized.get("conservative_prompt_tokens_per_second")
        if prompt_seed is not None:
            server["conservative_prompt_tokens_per_second"] = float(prompt_seed)

    def clear_reasoning(self, handle: str, key: str | None = None) -> None:
        server = self._server(handle)
        if key is None:
            server["reasoning_state"].clear()
        else:
            server["reasoning_state"].pop(str(key), None)
        server["last_completion_tokens"] = None

    @staticmethod
    def _prompt_cache_estimate(server: dict[str, Any], prompt_tokens: list[int], policy: dict[str, Any]) -> tuple[int, float | None, float | None]:
        previous = server.get("last_completion_tokens") or []
        prefix = 0
        for old, new in zip(previous, prompt_tokens):
            if old != new:
                break
            prefix += 1
        uncached = len(prompt_tokens) - prefix
        rates = [value for value in (policy.get("conservative_prompt_tokens_per_second"),
            server.get("conservative_prompt_tokens_per_second"))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0]
        rate = min(rates) if rates else None
        return uncached, (uncached / rate if rate else None), rate

    @staticmethod
    def _learn_prompt_rate(server: dict[str, Any], timings: dict[str, Any]) -> None:
        evaluated = timings.get("prompt_n")
        elapsed_ms = timings.get("prompt_ms")
        if (isinstance(evaluated, bool) or not isinstance(evaluated, int) or evaluated < 512
                or isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float))
                or not math.isfinite(elapsed_ms) or elapsed_ms <= 0):
            return
        measured = 0.7 * evaluated * 1000 / elapsed_ms
        previous = server.get("conservative_prompt_tokens_per_second")
        server["conservative_prompt_tokens_per_second"] = min(previous, measured) if previous else measured

    def _remaining_turn(self, deadline: float) -> float:
        left = deadline - time.time()
        if left <= 0:
            raise AdapterError("observer_model_turn_timed_out")
        return left

    def _llama_call(self, server: dict[str, Any], path: str, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        remaining = self._remaining_turn(deadline)
        status, body = self.transport("POST", server["base_url"] + path, payload, remaining)
        if status != 200:
            raise AdapterError(f"llama.cpp {path} failed with HTTP {status}")
        return body

    def _template_prompt(self, server: dict[str, Any], messages: list[dict[str, Any]], *, thinking: bool,
                         deadline: float, preserve_thinking: bool = False) -> str:
        body = self._llama_call(server, "/apply-template", {
            "messages": messages, "chat_template_kwargs": {
                "enable_thinking": thinking, "preserve_thinking": preserve_thinking}}, deadline)
        prompt = body.get("prompt")
        if not isinstance(prompt, str):
            raise AdapterError("llama.cpp_template_invalid")
        return prompt

    def _tokenize(self, server: dict[str, Any], prompt: str, deadline: float) -> list[int]:
        body = self._llama_call(server, "/tokenize", {"content": prompt, "parse_special": True}, deadline)
        tokens = body.get("tokens")
        if not isinstance(tokens, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in tokens):
            raise AdapterError("llama.cpp_tokenize_invalid")
        return tokens

    @staticmethod
    def _assistant_text(message: dict[str, Any]) -> str | None:
        content = message.get("content")
        return content if isinstance(content, str) else None

    def _messages_with_reasoning(self, messages: list[dict[str, Any]], history: list[dict[str, str]]) -> list[dict[str, Any]]:
        augmented = copy.deepcopy(messages)
        pending = list(reversed(history))
        for message in reversed(augmented):
            if not pending or not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = self._assistant_text(message)
            match = next((index for index, item in enumerate(pending) if item["answer"] == content), None)
            if match is not None:
                item = pending.pop(match)
                message["content"] = "<think>" + item["reasoning"] + "</think>" + content
        return augmented

    def _completion(self, server: dict[str, Any], *, prompt: list[int], max_tokens: int, deadline: float,
                    temperature: float, top_p: float, top_k: int, min_p: float, presence_penalty: float,
                    stop: list[str] | None = None, response_format: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": prompt, "n_predict": max_tokens, "temperature": temperature,
            "top_p": top_p, "top_k": top_k, "min_p": min_p,
            "presence_penalty": presence_penalty, "cache_prompt": True,
            "id_slot": 0, "return_tokens": True,
        }
        if stop:
            payload["stop"] = stop
        if response_format:
            payload["json_schema"] = response_format["schema"]
        return self._llama_call(server, "/completion", payload, deadline)

    def _run_observer_generation(self, handle: str, request: dict[str, Any], raw_policy: Any) -> dict[str, Any]:
        server = self._server(handle)
        policy = self._generation_policy(raw_policy)
        turn_policy_id = request.get("turn_policy_id")
        selected_turn_policy = None
        logical_context_tokens = request.get("logical_context_tokens")
        messages = request.get("messages")
        if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
            raise AdapterError("observer_messages_invalid")
        if turn_policy_id is not None:
            try:
                selected_turn_policy = policy_for(turn_policy_id)
            except ValueError as error:
                raise AdapterError(str(error)) from None
            if (policy != self._generation_policy(generation_settings(selected_turn_policy.policy_id)) or
                    request.get("max_tokens") != selected_turn_policy.visible_tokens or
                    request.get("reasoning_mode") != selected_turn_policy.reasoning_mode or
                    logical_context_tokens != selected_turn_policy.logical_context_tokens or
                    request.get("turn_policy_instruction") != selected_turn_policy.instruction):
                raise AdapterError("observer_turn_policy_mismatch")
            if selected_turn_policy.instruction:
                # The registry owns behavioral guidance as well as budgets.
                # Copy the messages and place the trusted instruction after
                # existing leading system messages, before any user content.
                messages = copy.deepcopy(messages)
                insert_at = 0
                while insert_at < len(messages) and messages[insert_at].get("role") == "system":
                    insert_at += 1
                messages.insert(insert_at, {"role": "system", "content": selected_turn_policy.instruction})
        elif logical_context_tokens is not None or "turn_policy_instruction" in request:
            raise AdapterError("observer_turn_policy_mismatch")
        request_id = str(request.get("request_id") or uuid.uuid4())
        if request_id in server["canceled"]:
            return {"status": "canceled", "request_id": request_id}
        key = request.get("reasoning_state_key")
        if policy["preserve_reasoning"] and (not isinstance(key, str) or not key or len(key) > 256):
            raise AdapterError("reasoning_state_key_required")
        mode = request.get("reasoning_mode", "auto")
        if mode not in {"auto", "off"}:
            raise AdapterError("reasoning_mode_invalid")
        max_tokens = request.get("max_tokens", 2048)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 2048:
            raise AdapterError("observer_visible_token_cap_invalid")
        timeout = request.get("timeout_seconds", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise AdapterError("observer_model_turn_timeout_invalid")
        deadline_epoch = request.get("deadline_epoch")
        deadline = min(time.time() + float(timeout), float(deadline_epoch)) if deadline_epoch is not None else time.time() + float(timeout)
        response_format = request.get("response_format")
        if response_format is not None and (not isinstance(response_format, dict) or set(response_format) != {"type", "schema"}
                or response_format.get("type") != "json_object" or not isinstance(response_format.get("schema"), dict)
                or response_format["schema"].get("type") != "object"):
            raise AdapterError("llama.cpp response_format requires a JSON object schema")
        if response_format is not None:
            try:
                schema_json = json.dumps(response_format["schema"], ensure_ascii=False, allow_nan=False)
                if len(schema_json.encode("utf-8")) > 16 * 1024:
                    raise ValueError("schema too large")
                schema = json.loads(schema_json)
                pending = [schema]
                while pending:
                    node = pending.pop()
                    if isinstance(node, dict):
                        if "$ref" in node and (not isinstance(node["$ref"], str) or not node["$ref"].startswith("#/")):
                            raise ValueError("external schema reference")
                        pending.extend(node.values())
                    elif isinstance(node, list):
                        pending.extend(node)
                response_format = {"type": "json_object", "schema": schema}
            except (TypeError, ValueError, RecursionError):
                raise AdapterError("llama.cpp response_format schema invalid or exceeds bound") from None
        history = server["reasoning_state"].setdefault(key, []) if policy["preserve_reasoning"] else []
        history_length_before = len(history)
        started = time.perf_counter()
        server.setdefault("active_requests", set()).add(request_id)
        reasoning_count = visible_count = prompt_count = 0
        reasoning_token_budget = 0
        reasoning_ms = visible_ms = 0.0
        reasoning_prompt_count = answer_prompt_count = 0
        reasoning_prompt_evaluated = answer_prompt_evaluated = 0
        reasoning_prompt_cached = answer_prompt_cached = 0
        reasoning_prompt_ms = answer_prompt_ms = 0.0
        reasoning_prompt_tps = answer_prompt_tps = 0.0
        reasoning_tps = visible_tps = 0.0
        uncached_prompt_tokens = 0
        estimated_prefill_seconds: float | None = None
        effective_context_size = server.get("effective_context_size", server["profile"].get("context_size"))
        effective_turn_context = effective_context_size
        if logical_context_tokens is not None:
            if (isinstance(logical_context_tokens, bool) or not isinstance(logical_context_tokens, int)
                    or logical_context_tokens <= 0):
                raise AdapterError("observer_turn_policy_mismatch")
            effective_turn_context = (min(effective_context_size, logical_context_tokens)
                if isinstance(effective_context_size, int) and effective_context_size > 0
                else logical_context_tokens)
        reasoning = ""
        text = ""
        answer_stage = False
        try:
            try:
                rates = [value for value in (policy.get("conservative_tokens_per_second"),
                    server.get("conservative_tokens_per_second"))
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0]
                rate = min(rates) if rates else None
                if mode == "auto" and rate and self._remaining_turn(deadline) > policy["min_answer_seconds"] + 1:
                    turn_reason_cap = min(policy["reasoning_tokens"],
                        int(max(0, self._remaining_turn(deadline) - policy["min_answer_seconds"] - 1) * rate))
                else:
                    turn_reason_cap = 0
                use_thinking = turn_reason_cap > 0
                augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                if not use_thinking:
                    augmented = copy.deepcopy(messages) if not policy["preserve_reasoning"] else augmented
                prompt = self._template_prompt(server, augmented, thinking=use_thinking, deadline=deadline,
                    preserve_thinking=policy["preserve_reasoning"])
                if use_thinking and not prompt.rstrip().endswith("<think>"):
                    use_thinking = False
                    turn_reason_cap = 0
                    augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                    prompt = self._template_prompt(server, augmented, thinking=False, deadline=deadline,
                        preserve_thinking=policy["preserve_reasoning"])
                prompt_ids = self._tokenize(server, prompt, deadline)
                prompt_count = len(prompt_ids)
                # Template rendering and tokenization consume the same turn budget as inference.
                remaining_for_thinking = self._remaining_turn(deadline) - policy["min_answer_seconds"] - 1
                turn_reason_cap = min(turn_reason_cap, int(max(0, remaining_for_thinking * rate))) if use_thinking and rate else 0
                if turn_reason_cap <= 0 and use_thinking:
                    use_thinking = False
                    augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                    prompt = self._template_prompt(server, augmented, thinking=False, deadline=deadline,
                        preserve_thinking=policy["preserve_reasoning"])
                    prompt_ids = self._tokenize(server, prompt, deadline)
                    prompt_count = len(prompt_ids)
                ctx_size = effective_turn_context
                if isinstance(ctx_size, int) and ctx_size > 0:
                    close_ids = self._tokenize(server, "</think>", deadline)
                    base_required = prompt_count + (len(close_ids) if use_thinking else 0) + max_tokens
                    while base_required > ctx_size and history:
                        history.pop(0)
                        augmented = self._messages_with_reasoning(messages, history)
                        prompt = self._template_prompt(server, augmented, thinking=use_thinking, deadline=deadline,
                            preserve_thinking=policy["preserve_reasoning"])
                        prompt_ids = self._tokenize(server, prompt, deadline)
                        prompt_count = len(prompt_ids)
                        base_required = prompt_count + (len(close_ids) if use_thinking else 0) + max_tokens
                    if base_required > ctx_size:
                        raise AdapterError("observer_context_budget")
                    if use_thinking:
                        turn_reason_cap = min(turn_reason_cap, max(0, ctx_size - base_required))
                        if turn_reason_cap <= 0:
                            use_thinking = False
                            turn_reason_cap = 0
                            augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                            prompt = self._template_prompt(server, augmented, thinking=False, deadline=deadline,
                                preserve_thinking=policy["preserve_reasoning"])
                            prompt_ids = self._tokenize(server, prompt, deadline)
                            prompt_count = len(prompt_ids)
                            if prompt_count + max_tokens > ctx_size:
                                raise AdapterError("observer_context_budget")
                if use_thinking and rate:
                    remaining_for_thinking = self._remaining_turn(deadline) - policy["min_answer_seconds"] - 1
                    turn_reason_cap = min(turn_reason_cap, int(max(0, remaining_for_thinking * rate)))
                    if turn_reason_cap <= 0:
                        use_thinking = False
                        turn_reason_cap = 0
                        augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                        prompt = self._template_prompt(server, augmented, thinking=False, deadline=deadline,
                            preserve_thinking=policy["preserve_reasoning"])
                        prompt_ids = self._tokenize(server, prompt, deadline)
                        prompt_count = len(prompt_ids)
                        if isinstance(ctx_size, int) and ctx_size > 0 and prompt_count + max_tokens > ctx_size:
                            raise AdapterError("observer_context_budget")
                uncached_prompt_tokens, estimated_prefill_seconds, prompt_rate = self._prompt_cache_estimate(
                    server, prompt_ids, policy)
                if use_thinking:
                    remaining_for_thinking = self._remaining_turn(deadline) - policy["min_answer_seconds"] - 1
                    if prompt_rate is None or estimated_prefill_seconds is None:
                        turn_reason_cap = 0
                    else:
                        turn_reason_cap = min(turn_reason_cap, int(max(0,
                            remaining_for_thinking - estimated_prefill_seconds) * rate))
                    if turn_reason_cap <= 0:
                        use_thinking = False
                        turn_reason_cap = 0
                        augmented = self._messages_with_reasoning(messages, history) if policy["preserve_reasoning"] else copy.deepcopy(messages)
                        prompt = self._template_prompt(server, augmented, thinking=False, deadline=deadline,
                            preserve_thinking=policy["preserve_reasoning"])
                        prompt_ids = self._tokenize(server, prompt, deadline)
                        prompt_count = len(prompt_ids)
                        uncached_prompt_tokens, estimated_prefill_seconds, _ = self._prompt_cache_estimate(
                            server, prompt_ids, policy)
                reasoning_token_budget = turn_reason_cap if use_thinking else 0
                if use_thinking:
                    phase_deadline = deadline - policy["min_answer_seconds"]
                    thought = self._completion(server, prompt=prompt_ids, max_tokens=turn_reason_cap,
                        deadline=phase_deadline, temperature=policy["thinking_temperature"], top_p=policy["thinking_top_p"],
                        top_k=policy["top_k"], min_p=policy["min_p"], presence_penalty=policy["presence_penalty"],
                        stop=["</think>"])
                    reasoning = thought.get("content") if isinstance(thought.get("content"), str) else ""
                    thought_tokens = thought.get("tokens") if isinstance(thought.get("tokens"), list) else []
                    timings = thought.get("timings") if isinstance(thought.get("timings"), dict) else {}
                    predicted = timings.get("predicted_n", len(thought_tokens))
                    reasoning_count = predicted if isinstance(predicted, int) and predicted >= 0 else len(thought_tokens)
                    reasoning_ms = float(timings.get("predicted_ms", 0) or 0)
                    reasoning_prompt_count = len(prompt_ids)
                    reasoning_prompt_evaluated = int(timings.get("prompt_n", 0) or 0)
                    reasoning_prompt_cached = int(timings.get("cache_n", 0) or 0)
                    reasoning_prompt_ms = float(timings.get("prompt_ms", 0) or 0)
                    reasoning_prompt_tps = float(timings.get("prompt_per_second", 0) or 0)
                    reasoning_tps = float(timings.get("predicted_per_second", 0) or 0)
                    self._learn_prompt_rate(server, timings)
                    rate = timings.get("predicted_per_second")
                    if isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) and rate > 0:
                        old_rate = server.get("conservative_tokens_per_second")
                        measured = float(rate) * 0.7
                        server["conservative_tokens_per_second"] = min(old_rate, measured) if old_rate else measured
                    closure_ids = self._tokenize(server, "</think>", deadline)
                    if thought_tokens and closure_ids and thought_tokens[-len(closure_ids):] == closure_ids:
                        reasoning_count = max(0, reasoning_count - len(closure_ids))
                        answer_prompt = prompt_ids + thought_tokens
                    else:
                        answer_prompt = prompt_ids + thought_tokens + closure_ids
                    if not thought_tokens:
                        answer_prompt = self._tokenize(server, prompt + reasoning + "</think>", deadline)
                    answer_stage = True
                else:
                    answer_prompt = self._tokenize(server, prompt, deadline)
                if isinstance(ctx_size, int) and ctx_size > 0 and len(answer_prompt) + max_tokens > ctx_size:
                    raise AdapterError("observer_context_budget")
                answer = self._completion(server, prompt=answer_prompt, max_tokens=max_tokens,
                    deadline=deadline, temperature=policy["direct_temperature"], top_p=policy["direct_top_p"],
                    top_k=policy["top_k"], min_p=policy["min_p"], presence_penalty=policy["presence_penalty"],
                    response_format=response_format)
                text = answer.get("content") if isinstance(answer.get("content"), str) else ""
                if "<think>" in text or "</think>" in text:
                    raise AdapterError("observer_visible_reasoning_output_rejected")
                if len(text.encode("utf-8")) > 16 * 1024:
                    raise AdapterError("observer_visible_output_budget_exceeded")
                answer_tokens = answer.get("tokens") if isinstance(answer.get("tokens"), list) else []
                timings = answer.get("timings") if isinstance(answer.get("timings"), dict) else {}
                visible_ms = float(timings.get("predicted_ms", 0) or 0)
                answer_prompt_count = len(answer_prompt)
                answer_prompt_evaluated = int(timings.get("prompt_n", 0) or 0)
                answer_prompt_cached = int(timings.get("cache_n", 0) or 0)
                answer_prompt_ms = float(timings.get("prompt_ms", 0) or 0)
                answer_prompt_tps = float(timings.get("prompt_per_second", 0) or 0)
                visible_tps = float(timings.get("predicted_per_second", 0) or 0)
                self._learn_prompt_rate(server, timings)
                visible_count = timings.get("predicted_n", len(answer_tokens))
                if not isinstance(visible_count, int) or visible_count < 0:
                    visible_count = len(answer_tokens)
                rate = timings.get("predicted_per_second")
                if visible_count >= 16 and isinstance(rate, (int, float)) and not isinstance(rate, bool) and math.isfinite(rate) and rate > 0:
                    old_rate = server.get("conservative_tokens_per_second")
                    measured = float(rate) * 0.7
                    server["conservative_tokens_per_second"] = min(old_rate, measured) if old_rate else measured
                if policy["preserve_reasoning"] and reasoning:
                    history.append({"answer": text, "reasoning": reasoning})
                    if len(history) > 32:
                        del history[:-32]
                server["last_completion_tokens"] = answer_prompt + [
                    token for token in answer_tokens if isinstance(token, int) and not isinstance(token, bool)]
            except AdapterError:
                raise
            except (OSError, urllib.error.URLError, TimeoutError) as error:
                timed_out = time.time() >= deadline or self._remaining_before_error(deadline)
                try:
                    self.cancel(handle, request_id)
                except Exception:
                    pass
                raise AdapterError("observer_model_turn_timed_out" if timed_out else "observer_model_transport_failed") from error
        except AdapterError as error:
            server["last_completion_tokens"] = None
            if len(history) > history_length_before:
                del history[history_length_before:]
            if str(error) == "observer_model_turn_timed_out" and request_id in server.get("active_requests", ()):
                self.cancel(handle, request_id)
            raise
        except Exception:
            server["last_completion_tokens"] = None
            if len(history) > history_length_before:
                del history[history_length_before:]
            raise
        finally:
            server["active_requests"].discard(request_id)
        duration = (time.perf_counter() - started) * 1000
        total = reasoning_count + visible_count
        usage = {"prompt_tokens": prompt_count, "completion_tokens": total,
                 "reasoning_tokens": reasoning_count, "visible_tokens": visible_count,
                 "answer_tokens": visible_count, "total_tokens": prompt_count + total,
                 "reasoning_token_budget": reasoning_token_budget,
                 "reasoning_ms": round(reasoning_ms, 3), "visible_ms": round(visible_ms, 3),
                 "reasoning_prompt_tokens": reasoning_prompt_count,
                 "answer_prompt_tokens": answer_prompt_count,
                 "reasoning_prompt_evaluated_tokens": reasoning_prompt_evaluated,
                 "reasoning_prompt_cached_tokens": reasoning_prompt_cached,
                 "answer_prompt_evaluated_tokens": answer_prompt_evaluated,
                 "answer_prompt_cached_tokens": answer_prompt_cached,
                 "reasoning_prompt_ms": round(reasoning_prompt_ms, 3),
                 "answer_prompt_ms": round(answer_prompt_ms, 3),
                 "reasoning_prompt_tokens_per_second": reasoning_prompt_tps,
                 "answer_prompt_tokens_per_second": answer_prompt_tps,
                 "reasoning_tokens_per_second": reasoning_tps,
                 "visible_tokens_per_second": visible_tps,
                 "uncached_prompt_tokens": uncached_prompt_tokens,
                 "estimated_prefill_ms": (round(estimated_prefill_seconds * 1000, 3)
                    if estimated_prefill_seconds is not None else None),
                 "context_tokens": len(answer_prompt) + visible_count,
                 "effective_context_size": effective_context_size,
                 "turn_policy_id": selected_turn_policy.policy_id if selected_turn_policy else None,
                 "budget": {
                     "requested": {
                         "logical_context_tokens": logical_context_tokens,
                         "reasoning_tokens": (selected_turn_policy.reasoning_tokens
                             if selected_turn_policy else policy["reasoning_tokens"]),
                         "visible_tokens": max_tokens,
                     },
                     "effective": {
                         "logical_context_tokens": effective_turn_context,
                         "reasoning_tokens": reasoning_token_budget,
                         "visible_tokens": max_tokens,
                     },
                     "used": {
                         "context_tokens": len(answer_prompt) + visible_count,
                         "reasoning_tokens": reasoning_count,
                         "visible_tokens": visible_count,
                     },
                 }}
        server["usage"]["runs"] += 1
        server["usage"]["prompt_tokens"] += prompt_count
        server["usage"]["completion_tokens"] += total
        server["usage"]["duration_ms"] += duration
        return {"status": "succeeded", "request_id": request_id, "text": text, "usage": usage,
                "duration_ms": round(duration, 3), "response_metadata": {
                    "finish_reason": answer.get("stop_type") or "unknown",
                    "reasoning_preserved": bool(policy["preserve_reasoning"] and reasoning),
                    "answer_stage": answer_stage, "content_bytes": len(text.encode("utf-8"))}}

    @staticmethod
    def _remaining_before_error(deadline: float) -> bool:
        return time.time() >= deadline

    def cancel(self, handle: str, request_id: str | None = None) -> dict[str, Any]:
        server = self._server(handle)
        if request_id:
            server["canceled"].add(request_id)
            if request_id in server.get("active_requests", ()):
                self.evict(handle)
            return {"canceled": True, "request_id": request_id}
        return {"canceled": False, "reason": "request_id_required"}

    def drain(self, handle: str) -> dict[str, Any]:
        self._server(handle)["accepting"] = False
        return {"draining": True}

    def evict(self, handle: str) -> dict[str, Any]:
        server = self._server(handle)
        with server.setdefault("eviction_lock", threading.RLock()):
            process = server["process"]
            if process.poll() is None:
                identity = server.get("termination_identity")
                if identity is None:
                    raise AdapterError("owned_process_identity_unavailable")
                self.owned_terminator(identity)
                process.wait(timeout=10)
            if process.poll() is None:
                raise AdapterError("model_process_not_quiescent")
            server["log_stream"].close()
            server["accepting"] = False
            server["evicted"] = True
            server.get("reasoning_state", {}).clear()
            server["last_completion_tokens"] = None
            return {"evicted": True}

    def quiescent(self, handle: str) -> bool:
        server = self._server(handle)
        return server["process"].poll() is not None

    def usage(self, handle: str) -> dict[str, Any]:
        usage = dict(self._server(handle)["usage"])
        usage["duration_ms"] = round(float(usage["duration_ms"]), 3)
        return usage
