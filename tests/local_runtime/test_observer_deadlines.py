"""CPU deadlines: owned startup and active inference, with no GPU/model download."""
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_runtime import assert_receiver_module, receiver_runtime_path
import local_runtime.test_supervisor as _supervisor_fixtures
from local_runtime.test_supervisor import (
    _Adapter, _Cache, _PoolBackend, _Runtime, _Service, _profile,
)

RECEIVER = receiver_runtime_path()
from local_worker import observer_runtime
from local_worker.observer_runtime import (JOB_INPUT_MAX_BYTES, MODEL_TURN_MAX_BYTES,
    ObserverWorkerPort, ReadOnlyCommandRunner, _reasoning_mode)
from local_worker.servers.llama_cpp import LlamaCppServerAdapter
from local_worker.service import AdapterError

assert_receiver_module(observer_runtime)
assert_receiver_module(__import__("local_worker.servers.llama_cpp", fromlist=["LlamaCppServerAdapter"]))


class Process:
    pid = 424242
    def __init__(self):
        self.returncode = None
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        assert self.returncode is not None
        return self.returncode


def adapter_fixture(tmp_path, transport):
    binary = tmp_path / "server"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    model = tmp_path / "fixture.gguf"
    model.write_bytes(b"GGUFfixture")
    processes, killed = [], []
    def spawn(*args, **kwargs):
        process = Process()
        processes.append(process)
        return process
    def stop(identity):
        killed.append(identity["pid"])
        processes[0].returncode = -9
    adapter = LlamaCppServerAdapter(str(binary), process_factory=spawn,
        transport=transport, help_runner=lambda *a, **kw: SimpleNamespace(stdout="", stderr=""),
        identity_reader=lambda pid: {"pid": pid}, owned_terminator=stop)
    return adapter, model, processes, killed


def test_startup_lifetime_stops_only_owned_process(tmp_path):
    adapter, model, processes, killed = adapter_fixture(tmp_path, lambda *a: (503, {}))
    started = time.monotonic()
    with pytest.raises(AdapterError, match="startup"):
        adapter.start({"model_path": str(model), "startup_timeout_seconds": 600,
                       "deadline_epoch": time.time() + .06})
    assert time.monotonic() - started < .5
    assert killed == [424242]
    assert processes[0].poll() is not None
    assert all(server["evicted"] for server in adapter._servers.values())


def test_inference_timeout_waits_for_transport_and_stops_owned_operation(tmp_path):
    stopped = threading.Event()
    transport_finished = threading.Event()
    calls = []
    def transport(method, url, body, timeout):
        if method == "GET":
            return 200, {}
        calls.append(timeout)
        assert stopped.wait(1), "hard bound did not stop owned operation"
        time.sleep(.02)  # Dispatch must retain its slot until this finishes.
        transport_finished.set()
        return 200, {"choices": [{"message": {"content": "{}"}}]}
    adapter, model, processes, killed = adapter_fixture(tmp_path, transport)
    original_stop = adapter.owned_terminator
    def stop(identity):
        original_stop(identity)
        stopped.set()
    adapter.owned_terminator = stop
    handle = adapter.start({"model_path": str(model)})
    # A distinct idle server remains untouched.
    adapter._servers["other"] = {"evicted": False, "accepting": True}
    with pytest.raises(AdapterError, match="timed_out"):
        adapter.run(handle, {"messages": [{"role": "user", "content": "question"}],
                    "timeout_seconds": .04, "deadline_epoch": time.time() + 1})
    assert transport_finished.is_set()
    assert 0 < calls[0] <= .04
    assert killed == [424242]
    assert not adapter._servers["other"]["evicted"]


def test_expired_deadline_does_not_dispatch_model_or_command(tmp_path):
    class Backend:
        def run_observer_turn(self, request):
            pytest.fail("expired inquiry dispatched a model")
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet")
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: None, fence=lambda *a: True)
    with pytest.raises(TimeoutError):
        worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
                    "deadline_epoch": time.time() - 1})
    with pytest.raises(TimeoutError):
        runner.run(["cat", "x"], str(tmp_path), deadline_epoch=time.time() - 1)


def test_refresh_context_and_remaining_model_budget_are_forwarded(tmp_path):
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {"status": "available", "text": json.dumps({"answer": "prior answer", "findings": []})}
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet")
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: None, fence=lambda *a: True)
    refresh = {"prior_answer": "prior answer", "findings": [], "changed_sources": ["file.py"]}
    deadline = time.time() + 5
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
                        "deadline_epoch": deadline, "refresh_context": refresh})
    assert result["status"] == "completed"
    assert turns[0]["deadline_epoch"] == deadline
    assert 0 < turns[0]["timeout_seconds"] <= 5
    assert turns[0]["response_format"] == {"type": "json_object", "schema": {"type": "object"}}
    assert json.loads(turns[0]["messages"][1]["content"])["refresh_context"] == refresh
    assert "Reuse valid prior work" in turns[0]["messages"][0]["content"]


@pytest.mark.parametrize("bad_arguments", [
    {"argv": ["pwd"]},
    {"argv": ["pwd"], "cwd": ".", "typo": True},
])
def test_invalid_command_arguments_are_denied_and_repaired_without_dispatch(tmp_path, bad_arguments):
    turns, executions = [], []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) == 1:
                value = {"tool": "command", "arguments": bad_arguments}
            elif len(turns) == 2:
                value = {"tool": "command", "arguments": {"argv": ["pwd"], "cwd": str(tmp_path),
                    "timeout_seconds": 60}}
            else:
                value = {"answer": "The command completed.", "findings": [], "unresolved_questions": []}
            return {"status": "available", "text": json.dumps(value)}
    class Command(ReadOnlyCommandRunner):
        def run(self, argv, cwd, **kwargs):
            executions.append((argv, cwd, kwargs.get("timeout_seconds")))
            return self._packet({"status": "completed", "exit_code": 0, "stdout": str(tmp_path),
                "stderr": "", "truncated": False, "timed_out": False}, kwargs.get("guard"))
    worker = ObserverWorkerPort(Backend(), command=Command([tmp_path], packetize=lambda _: f"p{len(executions)}"),
        tools=lambda *a: pytest.fail("not a general tool"), fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "Inspect source",
        "max_steps": 3, "deadline_epoch": time.time() + 5})
    assert result["status"] == "completed"
    assert executions == [(["pwd"], str(tmp_path), 60)]
    assert turns[1]["reasoning_mode"] == "off"
    first_tool_feedback = next(json.loads(message["content"]) for message in turns[1]["messages"]
        if message["role"] == "user" and message["content"].startswith('{"status": "denied"'))
    assert first_tool_feedback["status"] == "denied"
    assert first_tool_feedback["reason"] == "invalid_command_arguments"
    assert first_tool_feedback["dispatched"] is False
    assert turns[-1]["response_format"]["schema"]["properties"]["answer"]["maxLength"] == 1200


def test_sed_window_emits_full_source_dependency_and_exact_range(tmp_path):
    import hashlib
    from local_worker.observer_runtime import _sed_source_reads
    path = tmp_path / "source.py"
    content = "alpha\r\nbeta\r\ngamma\r\nlast"
    path.write_bytes(content.encode("utf-8"))
    proof = _sed_source_reads(["sed", "-n", "2,3p", str(path)], str(tmp_path),
        lambda candidate: candidate == path, "beta\r\ngamma\r\n", status="completed",
        truncated=False, encoding_loss=False)
    assert proof == [{"path": str(path), "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "line_count": 4, "method": "direct_sed_lines", "line_ranges": [{"start": 2, "end": 3}]}]


def test_sed_window_proof_rejects_tampering_unsupported_syntax_and_untrusted_files(tmp_path):
    from local_worker.observer_runtime import _sed_source_reads
    path = tmp_path / "source.py"
    path.write_text("one\ntwo\n", encoding="utf-8")
    allows = lambda candidate: candidate == path
    common = {"status": "completed", "truncated": False, "encoding_loss": False}
    assert _sed_source_reads(["sed", "-n", "1,1p", str(path)], str(tmp_path), allows,
        "tampered\n", **common) is None
    assert _sed_source_reads(["sed", "-n", "1,1p;d", str(path)], str(tmp_path), allows,
        "one\n", **common) is None
    assert _sed_source_reads(["sed", "-n", "1,1p", str(path)], str(tmp_path), lambda _: False,
        "one\n", **common) is None


def test_sed_window_proof_rejects_non_utf8_source_and_truncated_output(tmp_path):
    from local_worker.observer_runtime import _sed_source_reads
    path = tmp_path / "source.py"
    path.write_bytes(b"one\n\xff\n")
    read = lambda candidate: candidate == path
    assert _sed_source_reads(["sed", "-n", "1,1p", str(path)], str(tmp_path), read,
        "one\n", status="completed", truncated=False, encoding_loss=False) is None
    path.write_text("one\n", encoding="utf-8")
    assert _sed_source_reads(["sed", "-n", "1,1p", str(path)], str(tmp_path), read,
        "one\n", status="completed", truncated=True, encoding_loss=False) is None


@pytest.mark.parametrize(("question", "mode"), [
    ("What version does the file report?", "off"),
    ("Why do the sources disagree, and how do they compare?", "auto"),
    ("Which GPU UUID is configured?", "off"),
])
def test_reasoning_policy_distinguishes_direct_reads_and_synthesis(question, mode):
    assert _reasoning_mode(question, mode="investigate", first_step=True,
        has_protocol_feedback=False, remaining=60) == mode


def test_reasoning_policy_disables_thinking_for_skill_entry_repair_and_short_turn():
    assert _reasoning_mode("Read the skill entry", mode="skill", first_step=True,
        has_protocol_feedback=False, remaining=60) == "off"
    assert _reasoning_mode("Explain how these skill instructions compare", mode="skill", first_step=True,
        has_protocol_feedback=False, remaining=60) == "auto"
    assert _reasoning_mode("Explain the difference", mode="investigate", first_step=False,
        has_protocol_feedback=True, remaining=60) == "off"
    assert _reasoning_mode("Explain the difference", mode="investigate", first_step=False,
        has_protocol_feedback=False, remaining=14.9) == "off"


def test_internal_turn_envelope_grows_without_raising_job_input_or_visible_limits(tmp_path):
    assert MODEL_TURN_MAX_BYTES == 1024 * 1024
    assert JOB_INPUT_MAX_BYTES == 256 * 1024
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {"status": "available", "text": json.dumps({"answer": "ok", "findings": []})}
    worker = ObserverWorkerPort(Backend(), command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet"),
        tools=lambda *a: None, fence=lambda *a: True)
    worker.run({"job_id": "job", "attempt": 1, "mode": "investigate",
        "question": "What version does the file report?"})
    assert turns[0]["reasoning_mode"] == "off"
    assert turns[0]["max_tokens"] == 2048
    # The model answered before the reserved final round, so tool-capable turns
    # retain the generic object grammar; the closed answer schema is final-only.
    assert turns[0]["response_format"] == {"type": "json_object", "schema": {"type": "object"}}
    assert len(turns[0]["messages"][-1]["content"].encode()) < 16384

    never_called = type("NeverCalled", (), {"run_observer_turn": lambda *_: pytest.fail("oversized trusted input dispatched")})()
    bounded = ObserverWorkerPort(never_called, command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet"),
        tools=lambda *a: None, fence=lambda *a: True)
    with pytest.raises(ValueError, match="job inputs exceed bounded evidence context"):
        bounded.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
            "hints": "x" * JOB_INPUT_MAX_BYTES})


def test_visible_answer_byte_limit_remains_sixteen_kib(tmp_path):
    class Backend:
        def run_observer_turn(self, request):
            return {"status": "available", "text": "x" * 16385}
    worker = ObserverWorkerPort(Backend(), command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet"),
        tools=lambda *a: None, fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q"})
    assert result["status"] == "partial"
    assert result["reason"] == "model_output_exceeded_budget"
    assert "answer" not in result


def test_context_preflight_prunes_oldest_observation_inside_same_semantic_round(tmp_path):
    turns = []
    observations = [
        {"packet_id": "packet-old", "status": "completed", "stdout": "old evidence"},
        {"packet_id": "packet-new", "status": "completed", "stdout": "current evidence"},
    ]
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) == 1:
                return {"status": "unavailable", "reason": "context_budget"}
            progress = json.loads(request["messages"][1]["content"])
            assert progress["progress"]["omitted_observation_packet_ids"] == ["packet-old"]
            return {"status": "available", "text": json.dumps({"answer": "Current evidence is retained.",
                "findings": [{"text": "Current evidence", "evidence_packets": ["packet-new"]}]})}
    worker = ObserverWorkerPort(Backend(), command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet"),
        tools=lambda *a: pytest.fail("context preflight must not dispatch a tool"), fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "Explain the observed evidence.",
        "observations": observations, "max_steps": 1})
    assert result["status"] == "completed"
    assert result["findings"][0]["evidence_packets"] == ["packet-new"]
    assert len(turns) == 2
    assert turns[0]["reasoning_mode"] == turns[1]["reasoning_mode"] == "auto"


def test_context_preflight_with_no_removable_observations_returns_partial(tmp_path):
    class Backend:
        def run_observer_turn(self, request):
            return {"status": "unavailable", "reason": "context_budget"}
    worker = ObserverWorkerPort(Backend(), command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet"),
        tools=lambda *a: pytest.fail("context preflight must not dispatch a tool"), fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "Explain the evidence."})
    assert result["status"] == "partial" and result["reason"] == "context_budget"


def test_command_deadline_kills_cpu_process_group(tmp_path):
    import shutil
    if not shutil.which("bwrap"):
        pytest.skip("Bubblewrap unavailable")
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet")
    started = time.monotonic()
    result = runner.run(["sleep", "2"], str(tmp_path), timeout_seconds=60,
                        deadline_epoch=time.time() + .06)
    if not result["timed_out"] and result.get("exit_code") != 0:
        pytest.skip("Host does not permit Bubblewrap namespace")
    assert result["timed_out"]
    assert time.monotonic() - started < .5


def test_supervisor_deadline_reaches_cold_start_and_transport(tmp_path):
    service = _Service()
    backend = _PoolBackend(tmp_path, runtime=_Runtime(), service=service, adapter=_Adapter(),
                           cache=_Cache(), profile=_profile(),
                           topology_classifier=lambda: SimpleNamespace(mode="x_mode", status="available"))
    backend._version = lambda binary, deadline_epoch=None: "fixture"
    deadline = time.time() + 5
    opened = backend.open_observer_sessions(1, compute_profile="narrow", parallelism="default",
                                             deadline_epoch=deadline)
    assert service.contexts[0]["deadline_epoch"] == deadline
    session = opened["session_ids"][0]
    result = backend.run_observer_turn({"format": "PC-LOCAL-INVESTIGATOR-TURN/2",
        "messages": [{"role": "user", "content": "q"}], "max_tokens": 20,
        "timeout_seconds": 90, "compute_profile": "narrow", "parallelism": "default",
        "session_id": session, "deadline_epoch": deadline})
    assert result["status"] == "available"
    assert service.requests[0][2]["deadline_epoch"] == deadline
    assert service.requests[0][2]["timeout_seconds"] <= 5
    backend.close_observer_session(session)
    backend.close()


def test_version_probe_timeout_releases_consumed_admission(tmp_path):
    runtime, service = _Runtime(), _Service()
    backend = _PoolBackend(tmp_path, runtime=runtime, service=service, adapter=_Adapter(),
        cache=_Cache(), profile=_profile(),
        topology_classifier=lambda: SimpleNamespace(mode="x_mode", status="available"))
    def expired_version(*args):
        raise TimeoutError("version_probe_deadline")
    backend._version = expired_version
    with pytest.raises(TimeoutError):
        backend.open_observer_sessions(1, compute_profile="narrow", parallelism="default",
                                        deadline_epoch=time.time() + 5)
    assert not runtime.host.owners
    assert not backend._admissions
    assert not backend._leases
    assert not service.starts


def test_failed_owned_cleanup_keeps_session_resolvable_until_retry(tmp_path):
    from local_worker.supervisor import SupervisorError
    runtime, service = _Runtime(), _Service()
    backend = _PoolBackend(tmp_path, runtime=runtime, service=service, adapter=_Adapter(),
        cache=_Cache(), profile=_profile(),
        topology_classifier=lambda: SimpleNamespace(mode="x_mode", status="available"))
    lease = backend.warm()
    session, slot_id = lease["service_lease_id"], lease["slot_id"]
    slot = backend._slots[slot_id]
    slot.state = "draining"
    original_evict = service.evict
    def failed_evict(*args):
        raise AdapterError("model_process_not_quiescent")
    service.evict = failed_evict
    with pytest.raises(SupervisorError, match="cleanup_pending"):
        backend.close_observer_session(session)
    assert backend._leases[session] == slot_id
    assert slot.service_lease_id == session
    assert slot.owner_id in runtime.host.owners
    service.evict = original_evict
    assert backend.close_observer_session(session)["released"]
    assert session not in backend._leases and slot_id not in backend._slots
    assert slot.owner_id not in runtime.host.owners


def test_active_turn_cannot_release_dispatch_session(tmp_path):
    from local_worker.supervisor import SupervisorError
    backend, runtime, service = _supervisor_fixtures.ServicePoolTests().backend()
    lease = backend.warm()
    slot = backend._slots[lease["slot_id"]]
    slot.active_turns = 1
    with pytest.raises(SupervisorError, match="still active"):
        backend.close_observer_session(lease["service_lease_id"])
    assert slot.service_lease_id == lease["service_lease_id"]
    slot.active_turns = 0
    backend.close_observer_session(lease["service_lease_id"])
    backend.close()


def test_project_and_installed_skill_readmes_are_labeled_separately(tmp_path):
    project_root, skills_root = tmp_path / 'project', tmp_path / 'skills'
    project_root.mkdir()
    skills_root.mkdir()
    (project_root / 'README.md').write_text('Project interval: 17 seconds.')
    (skills_root / 'README.md').write_text('Installed skill integration guide.')
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {'status': 'available', 'text': json.dumps({'answer': 'context inspected', 'findings': []})}
    # Reverse root order proves project targeting does not rely on mount order.
    runner = ReadOnlyCommandRunner([skills_root, project_root], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: None, fence=lambda *a: True)
    repositories = [{'project': 'p', 'repository': 'main', 'root': str(project_root)}]
    worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate',
        'question': 'What interval does the project README document?',
        'inquiry_repositories': repositories, 'installed_skill_roots': [str(skills_root)]})
    context = json.loads(turns[0]['messages'][1]['content'])
    instruction = turns[0]['messages'][0]['content']
    assert context['inquiry_repositories'] == repositories
    assert context['installed_skill_roots'] == [str(skills_root)]
    assert 'broker-selected authoritative question targets' in instruction
    assert 'a similarly named file under an installed skill root is not project evidence' in instruction
    command_grammar = json.dumps({'tool': 'command', 'arguments': {'argv': ['pwd'], 'cwd': str(project_root)}})
    assert command_grammar in instruction
    assert '17 seconds' not in instruction  # Context labels do not manufacture source evidence.


def test_repository_labels_do_not_grant_unmounted_source_access(tmp_path):
    mounted, outside = tmp_path / 'mounted', tmp_path / 'outside'
    mounted.mkdir()
    outside.mkdir()
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {'status': 'available', 'text': json.dumps({'answer': 'context inspected', 'findings': []})}
    runner = ReadOnlyCommandRunner([mounted], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: None, fence=lambda *a: True)
    worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'Inspect source',
        'inquiry_repositories': [{'project': 'p', 'repository': 'main', 'root': str(outside)}],
        'installed_skill_roots': [str(outside)]})
    assert not runner.allows(outside)
    assert json.dumps({'tool': 'command', 'arguments': {'argv': ['pwd'], 'cwd': str(mounted)}}) in turns[0]['messages'][0]['content']


@pytest.mark.parametrize('last_is_tool', [False, True])
def test_final_model_round_synthesizes_or_refuses_more_tools(tmp_path, last_is_tool):
    turns, calls = [], []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) <= 5 or last_is_tool:
                value = {'tool': 'search', 'arguments': {'query': 'missing evidence'}}
            else:
                value = {'answer': 'Observed sources support a partial answer.',
                    'findings': [{'text': 'Observed source fact', 'evidence_packets': ['packet-1']}],
                    'unresolved_questions': ['One source remains unverified.']}
            return {'status': 'available', 'text': json.dumps(value)}
    def tools(name, arguments):
        calls.append((name, arguments))
        return {'packet_id': f'packet-{len(calls)}', 'source': 'observed source fact'}
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=tools, fence=lambda *a: True)
    deadline = time.time() + 5
    result = worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate',
        'question': 'What do the observed sources establish?', 'max_steps': 6, 'deadline_epoch': deadline})
    assert len(turns) == 6
    assert len(calls) == 5
    assert result['status'] == 'partial'
    last_context = json.loads(turns[-1]['messages'][-1]['content'])
    assert last_context['final_round'] is True
    assert 'Return final JSON only' in last_context['instruction']
    assert '2,048-token completion budget' in last_context['instruction']
    assert '1,200 characters' in last_context['instruction']
    assert 'at most three concise items' in last_context['instruction']
    assert all(turn['response_format'] == {'type': 'json_object', 'schema': {'type': 'object'}}
        for turn in turns[:-1])
    final_schema = turns[-1]['response_format']['schema']
    assert len(json.dumps(final_schema).encode()) < 16384
    assert final_schema['additionalProperties'] is False
    assert final_schema['required'] == ['answer', 'findings', 'unresolved_questions']
    assert 'tool' not in final_schema['properties']
    assert final_schema['properties']['answer']['maxLength'] == 1200
    assert final_schema['properties']['findings']['maxItems'] == 3
    assert final_schema['properties']['findings']['items']['properties']['text']['maxLength'] == 350
    assert final_schema['properties']['findings']['items']['properties']['evidence_packets']['items']['enum'] == [
        'packet-1', 'packet-2', 'packet-3', 'packet-4', 'packet-5']
    assert all(turn['deadline_epoch'] == deadline and 0 < turn['timeout_seconds'] <= 5 for turn in turns)
    if last_is_tool:
        assert result['reason'] == 'final_round_requires_answer'
        assert 'answer' not in result
    else:
        assert result['answer'] == 'Observed sources support a partial answer.'
        assert result['findings'][0]['evidence_packets'] == ['packet-1']
        assert result['unresolved_questions'] == ['One source remains unverified.']


def test_invalid_json_is_repaired_within_same_bounded_session(tmp_path):
    turns = []
    invalid = [
        '{"tool":"search","arguments":{"query":"unused"}}\n{"answer":"second object"}' + "x" * 6000,
        '```json\n{"answer":"fenced","findings":[]}\n```' + "y" * 6000,
    ]
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            index = len(turns)
            if index <= len(invalid):
                return {"status": "available", "text": invalid[index - 1]}
            return {"status": "available", "text": json.dumps({"answer": "Recovered.", "findings": []})}

    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet")
    worker = ObserverWorkerPort(Backend(), command=runner,
        tools=lambda *a: pytest.fail("invalid model output must not dispatch a tool"), fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
        "max_steps": 4, "session_id": "borrowed-session"})

    assert result["status"] == "completed" and result["answer"] == "Recovered."
    assert len(turns) == 3
    assert all(turn["session_id"] == "borrowed-session" for turn in turns)
    first_repair = turns[1]["messages"]
    assert not any(m["role"] == "assistant" for m in first_repair)
    assert any("invalid_json_object" in m["content"] and "additional object" in m["content"]
               for m in first_repair if m["role"] == "user")
    second_repair = turns[2]["messages"]
    assert any("invalid_json_object" in m["content"] for m in second_repair if m["role"] == "user")
    assert all(not any(raw[:128] in m["content"] for m in turn["messages"])
               for raw, turn in zip(invalid, turns[1:]))


def test_slice_yield_preserves_json_feedback_and_retained_citation_repair(tmp_path):
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) == 1:
                return {"status": "available", "text": "not valid JSON"}
            if len(turns) == 2:
                return {"status": "available", "text": json.dumps({"answer": "Found it.",
                    "findings": [{"text": "A finding", "evidence_packets": ["unknown-packet"]}],
                    "unresolved_questions": []})}
            return {"status": "available", "text": json.dumps({"answer": "Found it.",
                "findings": [{"text": "A finding", "evidence_packets": ["source-packet"]}],
                "unresolved_questions": []})}

    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda _: "citation-feedback-packet")
    worker = ObserverWorkerPort(Backend(), command=runner,
        tools=lambda *a: pytest.fail("repair turns must not dispatch tools"), fence=lambda *a: True)
    request = {"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
        "max_steps": 4, "observations": [{"packet_id": "source-packet", "status": "completed"}]}

    first = worker.run_slice(request, max_model_turns=1)
    assert first["status"] == "yielding" and first["reason"] == "slice_complete"
    assert first["protocol_feedback"] == [{"role": "user", "content": json.dumps({
        "protocol_error": "invalid_json_object", "detail": "Expecting value at line 1, column 1",
        "instruction": "No tool call was dispatched. Return exactly one JSON object in the required protocol, with no Markdown fence, commentary, or additional object. Continue from the supplied question and visible observations."}, ensure_ascii=False)}]

    request.update(observations=first["observations"], model_turns_used=first["model_turns_used"],
        protocol_feedback=first["protocol_feedback"])
    second = worker.run_slice(request, max_model_turns=1)
    assert second["status"] == "yielding" and second["reason"] == "slice_complete"
    assert any(item["packet_id"] == "citation-feedback-packet" for item in second["observations"])
    assert second["protocol_feedback"] == first["protocol_feedback"]

    request.update(observations=second["observations"],
        model_turns_used=request["model_turns_used"] + second["model_turns_used"],
        protocol_feedback=second["protocol_feedback"])
    third = worker.run_slice(request, max_model_turns=1)

    assert third["status"] == "completed" and third["answer"] == "Found it."
    assert third["findings"][0]["evidence_packets"] == ["source-packet"]
    final_messages = turns[2]["messages"]
    assert any("invalid_json_object" in message["content"] for message in final_messages
        if message["role"] == "user")
    assert any("finding_requires_observed_packet" in message["content"] for message in final_messages
        if message["role"] == "user")
    assert len(turns) == 3


@pytest.mark.parametrize("feedback", [
    [{"role": "assistant", "content": "{}"}],
    [{"role": "user", "content": json.dumps({"protocol_error": "unknown", "instruction": "fix"})}],
    [{"role": "user", "content": json.dumps({"protocol_error": "invalid_json_object",
        "instruction": "x" * 1537})}],
])
def test_slice_rejects_untrusted_protocol_feedback(tmp_path, feedback):
    worker = ObserverWorkerPort(None, command=ReadOnlyCommandRunner([tmp_path], packetize=lambda _: "packet"),
        tools=lambda *a: None, fence=lambda *a: True)
    with pytest.raises(ValueError, match="invalid protocol feedback"):
        worker.run_slice({"job_id": "job", "attempt": 1, "mode": "investigate", "question": "q",
            "protocol_feedback": feedback}, max_model_turns=1)


def test_repeated_invalid_json_exhausts_turn_budget_without_dispatch(tmp_path):
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {"status": "available", "text": "not an object"}

    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: "packet")
    worker = ObserverWorkerPort(Backend(), command=runner,
        tools=lambda *a: pytest.fail("invalid model output must not dispatch a tool"), fence=lambda *a: True)
    result = worker.run({"job_id": "job", "attempt": 1, "mode": "skill", "question": "q",
        "skill": {"name": "fixture", "root": str(tmp_path)}, "max_steps": 3,
        "session_id": "borrowed-session"})

    assert len(turns) == 3
    assert all(turn["session_id"] == "borrowed-session" for turn in turns)
    assert result["status"] == "partial" and result["reason"] == "model_output_invalid_json"
    assert result["unresolved_questions"] == ["The final model round did not return exactly one valid JSON object."]
    assert not result.get("answer") and not result.get("skill_selection")


def test_skill_final_round_reuses_agentically_read_maps_and_validates_source(tmp_path):
    import hashlib
    files = ['SKILL.md', 'map.md', 'architecture.md', 'start.md', 'needs.md']
    for index, name in enumerate(files):
        next_file = files[index + 1] if index + 1 < len(files) else 'none'
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'Read {next_file}; observed guidance {index}.\n')
    turns, reads = [], []
    class Command(ReadOnlyCommandRunner):
        def run(self, argv, cwd, **kwargs):
            path = Path(argv[1]).resolve()
            assert self.allows(path)
            reads.append(path.name)
            content = path.read_text()
            return {'packet_id': f'packet-{len(reads)}', 'status': 'completed', 'exit_code': 0,
                'stdout': content, 'truncated': False,
                'source_reads': [{'path': str(path), 'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
                                  'line_count': len(content.splitlines())}]}
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) <= 5:
                value = {'tool': 'command', 'arguments': {'argv': ['cat', str(tmp_path / files[len(turns) - 1])],
                                                        'cwd': str(tmp_path)}}
            else:
                value = {'answer': 'The read maps provide partial guidance.',
                        'findings': [{'text': 'Observed guidance 4.', 'evidence_packets': ['packet-5']}],
                    'unresolved_questions': ['Further implementation details were not read.'],
                    'skill_selection': {'format': 'pc-skill-selection/1', 'selections': [{
                        'skill': 'fixture', 'resource': 'needs.md',
                        'content_sha256': hashlib.sha256((tmp_path / 'needs.md').read_bytes()).hexdigest(),
                        'line_start': 1, 'line_end': 1, 'reason': 'Observed task guidance'}],
                        'synthesis': 'Observed guidance 4.'}}
            return {'status': 'available', 'text': json.dumps(value)}
    command = Command([tmp_path], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=command, tools=lambda *a: pytest.fail('unexpected tool'), fence=lambda *a: True)
    result = worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'skill', 'question': 'Read skill maps for guidance',
        'skill': {'name': 'fixture', 'root': str(tmp_path)}, 'max_steps': 6, 'deadline_epoch': time.time() + 5})
    assert result['status'] == 'partial'
    assert len(turns) == 6
    final_context = json.loads(turns[-1]['messages'][-1]['content'])
    assert final_context['final_round'] is True
    assert 'minimum necessary valid resources, at most three' in final_context['instruction']
    assert all(turn['response_format'] == {'type': 'json_object', 'schema': {'type': 'object'}} for turn in turns[:-1])
    final_schema = turns[-1]['response_format']['schema']
    assert len(json.dumps(final_schema).encode()) < 16384
    assert final_schema['required'] == ['answer', 'findings', 'unresolved_questions', 'skill_selection']
    assert 'tool' not in final_schema['properties']
    assert final_schema['properties']['findings']['items']['properties']['evidence_packets']['items']['enum'] == [
        'packet-1', 'packet-2', 'packet-3', 'packet-4', 'packet-5']
    selection_schema = final_schema['properties']['skill_selection']
    assert selection_schema['required'] == ['format', 'selections', 'synthesis']
    assert 'unresolved' in selection_schema['properties']
    assert selection_schema['properties']['selections']['maxItems'] == 3
    item_schema = selection_schema['properties']['selections']['items']
    assert item_schema['additionalProperties'] is False
    assert item_schema['required'] == ['skill', 'resource', 'content_sha256', 'line_start', 'line_end', 'reason']
    assert item_schema['properties']['skill'] == {'type': 'string', 'minLength': 1, 'maxLength': 128}
    assert item_schema['properties']['resource']['type'] == 'string'
    assert 'const' not in item_schema['properties']['resource']
    assert item_schema['properties']['prerequisites']['maxItems'] == 6
    assert 'const' not in item_schema['properties']['skill']
    assert reads == [*files, 'needs.md']  # Last read is authoritative final validation, not a model-requested call.
    assert result['findings'][0]['evidence_packets'] == ['packet-5']
    assert result['skill_selection']['selections'][0]['resource'] == 'needs.md'
    assert 'unresolved' not in result['skill_selection']
    assert all('hidden' not in observation for observation in result['observations'])


@pytest.mark.parametrize('budget', [6, 2])
def test_initial_prompt_announces_actual_round_budget_and_reserved_final(tmp_path, budget):
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {'status': 'available', 'text': json.dumps({'answer': 'No further inquiry needed.', 'findings': []})}
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: pytest.fail('unexpected tool'), fence=lambda *a: True)
    result = worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'Explain available evidence',
                        'max_steps': budget})
    assert result['status'] == 'completed'
    assert len(turns) == 1
    opening = turns[0]['messages'][0]['content']
    assert f'You have {budget} model rounds remaining out of {budget} total for this attempt.' in opening
    assert 'Reserve the final round for final JSON synthesis of the evidence gathered so far' in opening
    assert 'no further command or tool calls are permitted on that final round' in opening


def test_initial_prompt_distinguishes_active_runtime_from_cli_harness_limits(tmp_path):
    turns = []
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {'status': 'available', 'text': json.dumps({'answer': 'The evidence is sufficient.', 'findings': []})}
    worker = ObserverWorkerPort(Backend(), command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet'),
        tools=lambda *a: pytest.fail('unexpected tool'), fence=lambda *a: True)
    worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'Explain this inquiry',
                'max_steps': 3, 'deadline_epoch': time.time() + 30})
    opening = turns[0]['messages'][0]['content']
    worker_source = Path(observer_runtime.__file__).resolve()
    production_profile = worker_source.parent.parent / 'config' / 'production-profile.toml'
    assert str(worker_source) in opening
    assert str(production_profile) in opening
    assert 'src/project_control/as1_jobs.py' in opening
    assert 'supplied to this attempt as max_steps and deadline_epoch' in opening
    assert '[harnesses].qwen_* are separate CLI harness limits, not the public observer contract' in opening
    assert 'refinement_contexts are calibration candidates, not proof of the active context' in opening
    assert 'Do not infer public observer limits from those harnesses' in opening
    assert 'first batch a narrow search for the relevant definitions and callers' in opening
    assert 'include the adjacent adapter/provider implementation' in opening
    assert 'src/project_control/as1_jobs.py from the permitted Project Control inquiry repository' in opening
    assert "sed -n 'START,ENDp' PATH" in opening
    assert 'grep/search only to locate lines, not as final source proof' in opening
    assert 'below the 32 KiB worker observation limit' in opening
    assert 'do not repeat a whole-file read after truncation' in opening
    assert 'trace their callers and distinguish their scope and enforcement point' in opening
    assert 'You have 3 model rounds remaining out of 3 total for this attempt.' in opening


def test_argument_schemas_are_available_on_first_model_turn(tmp_path):
    turns = []
    schemas = {'log': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'additionalProperties': False},
               'evidence': {'type': 'object', 'properties': {'subject': {'type': 'string'}}, 'required': ['subject']}}
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            return {'status': 'available', 'text': json.dumps({'answer': 'No additional tools needed.', 'findings': []})}
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=lambda *a: pytest.fail('unexpected tool'), fence=lambda *a: True)
    worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'q', 'tool_argument_schemas': schemas})
    opening = turns[0]['messages'][0]['content']
    assert json.dumps(schemas, separators=(',', ':')) in opening
    assert 'Answer promptly once gathered evidence suffices' in opening
    assert 'Log is optional' in opening
    assert len(json.dumps(turns[0]).encode()) < 60000


def test_invalid_tool_argument_feedback_consumes_one_round_and_is_not_evidence(tmp_path):
    turns, calls = [], []
    retained = {'packet_id': 'source-1', 'source': 'Observed source fact'}
    class Backend:
        def run_observer_turn(self, request):
            turns.append(request)
            if len(turns) == 1:
                value = {'tool': 'log', 'arguments': {'offset': 0}}
            else:
                assert any('invalid_tool_arguments' in m['content'] for m in request['messages'])
                value = {'answer': 'The retained source supports the answer.',
                    'findings': [{'text': 'Observed source fact', 'evidence_packets': ['source-1']}]}
            return {'status': 'available', 'text': json.dumps(value)}
    def tools(name, arguments):
        calls.append((name, arguments))
        return {'packet_id': 'validation-1', 'status': 'denied', 'reason': 'invalid_tool_arguments',
            'accepted': False, 'dispatched': False, 'tool': name, 'validation_error': 'offset is unsupported',
            'validation_data': {'is_source_evidence': False},
            'corrective_action': 'Use the advertised argument schema and retained evidence.'}
    runner = ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet')
    worker = ObserverWorkerPort(Backend(), command=runner, tools=tools, fence=lambda *a: True)
    result = worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'q', 'max_steps': 2,
        'observations': [retained], 'tool_argument_schemas': {'log': {'type': 'object', 'additionalProperties': False}}})
    assert result['status'] == 'completed'
    assert len(turns) == 2 and len(calls) == 1
    assert result['findings'][0]['evidence_packets'] == ['source-1']
    assert result['observations'][-1]['validation_data']['is_source_evidence'] is False


@pytest.mark.parametrize('schemas', [{'mutation': {}}, {'log': []}])
def test_schema_metadata_cannot_add_tools_or_invalid_contracts(tmp_path, schemas):
    worker = ObserverWorkerPort(None, command=ReadOnlyCommandRunner([tmp_path], packetize=lambda p: 'packet'),
                                tools=lambda *a: None, fence=lambda *a: True)
    with pytest.raises(ValueError, match='invalid tool argument schemas'):
        worker.run({'job_id': 'job', 'attempt': 1, 'mode': 'investigate', 'question': 'q', 'tool_argument_schemas': schemas})
