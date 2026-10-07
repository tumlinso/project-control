from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types

import pytest

from project_control.assistance import lab_gpu


GPU0 = "GPU-11111111-1111-1111-1111-111111111111"
GPU1 = "GPU-22222222-2222-2222-2222-222222222222"


class Scope:
    project = "fixture"
    gpu_uuids = (GPU0,)
    tools = ("cuda",)
    toolchain_root = "/tmp/toolkit"


class Artifact:
    def __init__(self, path: str, content: str):
        self.path = path
        self.content = content


class Citation:
    def __init__(self, path: str, digest: str):
        self.path = path
        self.sha256 = digest


class Proposal:
    hypothesis = "the fixed tail is handled"
    measurements = ("observed_count",)
    stop_rule = "stop on a mismatch"

    def __init__(self, artifacts=(), citations=(), argv=("cuda", "--case", "tail")):
        self.artifacts = artifacts
        self.source_citations = citations
        self.argv = argv


def test_gpu_limits_are_hard_capped():
    with pytest.raises(lab_gpu.GpuLabError, match="2 cores"):
        lab_gpu.GpuLimits(cpu_cores=2.1)
    with pytest.raises(lab_gpu.GpuLabError, match="60 seconds"):
        lab_gpu.GpuLimits(run_seconds=61)
    with pytest.raises(lab_gpu.GpuLabError, match="1 GiB"):
        lab_gpu.GpuLimits(memory_bytes=1024 * 1024 * 1024 + 1)


def test_gpu_scope_requires_explicit_unique_uuid_and_cuda_tool():
    assert lab_gpu._approved_uuids(Scope) == (GPU0,)

    class Duplicate(Scope):
        gpu_uuids = (GPU0, GPU0)

    class NoCuda(Scope):
        tools = ("python3",)

    class Malformed(Scope):
        gpu_uuids = ("0",)

    for invalid in (Duplicate, NoCuda, Malformed):
        with pytest.raises(lab_gpu.GpuLabError):
            lab_gpu._approved_uuids(invalid)


def test_gpu_argv_must_name_cuda_tool_and_contains_only_program_arguments():
    assert lab_gpu._runtime_arguments(Proposal()) == ("--case", "tail")
    with pytest.raises(lab_gpu.GpuLabError, match="cuda tool"):
        lab_gpu._runtime_arguments(Proposal(argv=("python3", "x.py")))


def test_proposal_artifacts_and_source_citations_are_hash_bound(tmp_path: Path):
    source = tmp_path / "snapshot"
    proposal_root = tmp_path / "proposal"
    source.mkdir()
    proposal_root.mkdir()
    cited = source / "kernel.cuh"
    cited.write_text("inline int helper() { return 1; }\n")
    generated = proposal_root / "case.cu"
    generated.write_text("#include \"kernel.cuh\"\nint main() { return helper()-1; }\n")
    digest = lab_gpu._sha256(cited.read_bytes())
    proposal = Proposal((Artifact("case.cu", generated.read_text()),), (Citation("kernel.cuh", digest),))

    records, paths = lab_gpu._artifact_and_citations(proposal, proposal_root, source)
    assert records == [{"path": "case.cu", "sha256": lab_gpu._sha256(generated.read_bytes()),
                        "bytes": generated.stat().st_size}]
    assert paths == [generated.resolve()]

    cited.write_text("changed")
    with pytest.raises(lab_gpu.GpuLabError, match="differs"):
        lab_gpu._artifact_and_citations(proposal, proposal_root, source)


def test_proposal_artifacts_reject_traversal_symlinks_and_non_cuda_sources(tmp_path: Path):
    snapshot = tmp_path / "snapshot"
    proposal_root = tmp_path / "proposal"
    snapshot.mkdir()
    proposal_root.mkdir()
    outside = tmp_path / "outside.cu"
    outside.write_text("int main(){}")
    (proposal_root / "escape.cu").symlink_to(outside)
    with pytest.raises(lab_gpu.GpuLabError):
        lab_gpu._artifact_and_citations(Proposal((Artifact("../outside.cu", ""),)), proposal_root, snapshot)
    with pytest.raises(lab_gpu.GpuLabError, match="symlink"):
        lab_gpu._artifact_and_citations(Proposal((Artifact("escape.cu", "int main(){}"),)), proposal_root, snapshot)
    (proposal_root / "test.py").write_text("print('bad')")
    with pytest.raises(lab_gpu.GpuLabError, match=".cu"):
        lab_gpu._artifact_and_citations(Proposal((Artifact("test.py", "print('bad')"),)), proposal_root, snapshot)


def test_sandbox_binds_only_approved_device_nodes_and_never_host_dev_or_sys(tmp_path: Path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    proposal = tmp_path / "proposal"
    toolkit = tmp_path / "toolkit"
    snapshot.mkdir()
    proposal.mkdir()
    toolkit.mkdir()
    binary = tmp_path / "program"
    result = tmp_path / "result"
    binary.touch()
    result.touch()
    real_stat = Path.stat

    def stat_device(path, *args, **kwargs):
        if str(path) == "/dev/nvidia2":
            return types.SimpleNamespace(st_mode=stat.S_IFCHR)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_device)
    command = lab_gpu._sandbox_command(
        "/usr/bin/bwrap", snapshot=snapshot, proposal_root=proposal, toolchain=toolkit,
        binary=binary, result=result, source_names=("/proposal/a.cu",), phase="run",
        argv=("/artifacts/program",), device_minors=(2,), authorized_uuids=(GPU0,),
        artifact_bytes=1024, project_root=snapshot,
    )
    rendered = " ".join(command)
    assert "--unshare-net" in command
    assert "--dev" in command
    assert "--dev-bind /dev/nvidia2 /dev/nvidia2" in rendered
    assert "/dev/nvidia1" not in rendered
    assert "--ro-bind /dev" not in rendered
    assert "--ro-bind /sys" not in rendered
    assert "--setenv CUDA_VISIBLE_DEVICES GPU-11111111-1111-1111-1111-111111111111" in rendered


def test_gpu_map_keeps_cuda_index_separate_from_linux_minor(monkeypatch):
    class Completed:
        returncode = 0
        stdout = f"{GPU0}, 0, 7\n{GPU1}, 1, 2\n".encode()
        stderr = b""

    monkeypatch.setattr(lab_gpu.subprocess, "run", lambda *args, **kwargs: Completed())
    mapping = lab_gpu._gpu_map()
    assert mapping == {GPU0: (0, 7), GPU1: (1, 2)}
    selected, ungranted = lab_gpu._resolve_device_minors(mapping, (GPU0,), (0,))
    assert selected == (7,)
    assert ungranted == (2,)
    with pytest.raises(lab_gpu.GpuLabUnavailable, match="CUDA index mapping"):
        lab_gpu._resolve_device_minors(mapping, (GPU0,), (7,))


def test_controller_wrapper_preserves_structured_error_from_nonzero_exit(monkeypatch, tmp_path: Path):
    class Completed:
        returncode = 2
        stdout = b'{"ok":false,"code":"foreground_resource_contention"}'
        stderr = b"expected contention\n"

    monkeypatch.setattr(lab_gpu.subprocess, "run", lambda *args, **kwargs: Completed())
    result = lab_gpu.GpuLabExecutor._run_controller(tmp_path / "spec.json", Path("/bin/true"), 10)
    assert result["code"] == "foreground_resource_contention"
    assert result["_controller_returncode"] == 2
    assert result["_controller_stderr_sha256"] == lab_gpu._sha256(Completed.stderr)


def test_scope_deadline_clamps_each_phase_and_rejects_expiry(monkeypatch):
    monkeypatch.setattr(lab_gpu.time, "time", lambda: 100.0)
    assert lab_gpu._phase_timeout(60, 110) == 10
    assert lab_gpu._phase_timeout(10, 120) == 10
    with pytest.raises(lab_gpu.GpuLabUnavailable, match="deadline expired"):
        lab_gpu._phase_timeout(10, 100)


def test_no_payload_controller_codes_keep_admission_and_payload_separate():
    assert lab_gpu._controller_admission_started("foreground_build_failed") is False
    assert lab_gpu._controller_admission_started("requested_accelerator_unavailable") is False
    for code in ("foreground_resource_contention", "foreign_gpu_activity", "gpu_not_quiescent"):
        assert lab_gpu._controller_admission_started(code) is True


def test_actual_cuda_controller_rejects_build_before_admission_and_adapter_resumes(tmp_path: Path, monkeypatch):
    controller_path = Path("/home/tumlinson/.agents/skills/cuda/scripts/cuda_controller.py")
    if not controller_path.is_file():
        pytest.skip("installed CUDA controller is unavailable on this host")
    spec = importlib.util.spec_from_file_location("pc_test_cuda_controller", controller_path)
    assert spec and spec.loader
    monkeypatch.syspath_prepend(str(controller_path.parent))
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)

    project = tmp_path / "project"
    snapshot = tmp_path / "snapshot"
    proposal_root = tmp_path / "proposal"
    toolkit = tmp_path / "toolkit"
    (project / "src").mkdir(parents=True)
    snapshot.mkdir()
    proposal_root.mkdir()
    (toolkit / "bin").mkdir(parents=True)
    compiler = toolkit / "bin" / "nvcc"
    compiler.write_text("#!/bin/sh\nexit 0\n")
    compiler.chmod(0o700)
    cited = snapshot / "kernel.cuh"
    cited.write_text("// fixed source identity\n")
    source = proposal_root / "case.cu"
    source.write_text("int main(){return 0;}\n")
    proposal = Proposal((Artifact("case.cu", source.read_text()),),
                        (Citation("kernel.cuh", lab_gpu._sha256(cited.read_bytes())),))
    (project / "src" / "kernel.cuh").write_text(cited.read_text())
    scoped = type("ScopedFixture", (Scope,), {"toolchain_root": str(toolkit),
                                                "source_paths": ("src/kernel.cuh",)})

    monkeypatch.setattr(controller, "git_root", lambda _path: project)

    class BuildResult:
        returncode = 1
        stdout = ""
        stderr = "contained build reported failure"

    monkeypatch.setattr(controller.subprocess, "run", lambda *args, **kwargs: BuildResult())
    resumed = []

    def quiesce(*, request_id, resource_ids, deadline_epoch):
        return {"format": "PC-MODEL-FOREGROUND-HANDOFF/1", "status": "quiesced",
                "request_id": request_id, "continuation_id": "continue-1",
                "resource_ids": resource_ids, "slots": []}

    def resume(**kwargs):
        resumed.append(kwargs)
        return {"request_id": kwargs["request_id"], "continuation_id": kwargs["continuation_id"],
                "resource_ids": kwargs["resource_ids"], "status": "resumed"}

    def controller_runner(spec_path, _controller_path, _timeout):
        generated = json.loads(spec_path.read_text())
        assert generated["benchmark"]["build_argv"][:3] == [sys.executable, "-I", str(Path(lab_gpu.__file__).resolve())]
        assert "build_argv" not in generated
        assert generated["resources"]["gpu_uuids"] == [GPU0]
        request_path = Path(generated["benchmark"]["build_argv"][-1])
        phase_root = request_path.parent / "phases"
        phase_root.mkdir(exist_ok=True)
        (phase_root / "build-result.json").write_text(json.dumps({
            "format": "PC-GPU-LAB-BUILD/1", "status": "command_failed",
            "build": {"phase": "build", "status": "command_failed", "returncode": 1,
                      "cleanup_verified": True, "cgroup_removed": True},
            "cleanup_verified": True, "cgroup_removed": True,
        }))
        return controller.foreground_run(generated)

    executor = lab_gpu.GpuLabExecutor(
        project_roots={"fixture": project}, cuda_controller=controller_path,
        quiesce=quiesce, resume=resume, owner_reader=lambda _owner: pytest.fail("no GPU owner expected"),
        controller_runner=controller_runner,
    )
    result = executor.execute(scoped, proposal, snapshot, proposal_root, session_id="s1", effect_id="e1",
                              deadline=lab_gpu.time.time() + 500)
    assert result["status"] == "not_started"
    assert result["phase"] == "build"
    assert result["controller"] == {"code": "foreground_build_failed", "returned": True,
                                     "admission_started": False}
    assert result["foreground_terminal"] is None
    assert result["payload_started"] is False
    assert result["cleanup_verified"] is True
    assert result["proposal_artifacts"]
    assert len(resumed) == 1


class _Fn:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class _CudaStub:
    def __init__(self, allow_ungranted_context: bool):
        self.allow_ungranted_context = allow_ungranted_context
        self.freed = False
        self.destroyed = False
        self.cuInit = _Fn(lambda flags: 0)
        self.cuDeviceGetCount = _Fn(self.device_count)
        self.cuDeviceGetUuid_v2 = _Fn(self.device_uuid)
        self.cuDeviceGet = _Fn(self.device_get)
        self.cuCtxCreate_v2 = _Fn(self.context_create)
        self.cuMemAlloc_v2 = _Fn(self.mem_alloc)
        self.cuMemFree_v2 = _Fn(self.mem_free)
        self.cuCtxDestroy_v2 = _Fn(self.context_destroy)

    @staticmethod
    def device_count(out):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_int))[0] = 2
        return 0

    @staticmethod
    def device_uuid(out, ordinal):
        raw = bytes.fromhex((GPU0 if ordinal == 0 else GPU1).removeprefix("GPU-").replace("-", ""))
        ctypes.memmove(out, raw, len(raw))
        return 0

    @staticmethod
    def device_get(out, ordinal):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_int))[0] = ordinal
        return 0

    def context_create(self, out, flags, device):
        if device == 1 and not self.allow_ungranted_context:
            return 999
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(1234)
        return 0

    def mem_alloc(self, out, size):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_ulonglong))[0] = 5678
        return 0

    def mem_free(self, address):
        self.freed = True
        return 0

    def context_destroy(self, context):
        self.destroyed = True
        return 0


def test_negative_probe_is_nonvacuous_and_accepts_only_blocked_ungranted_access(monkeypatch):
    cuda = _CudaStub(allow_ungranted_context=False)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: cuda)
    monkeypatch.setattr(sys, "argv", ["probe", GPU0])
    source = lab_gpu._negative_cuda_probe((19,))
    exec(compile(source, "<trusted-negative-cuda-probe>", "exec"), {})
    assert cuda.freed is False
    assert cuda.destroyed is False


def test_negative_probe_fails_if_ungranted_context_and_allocation_work(monkeypatch):
    cuda = _CudaStub(allow_ungranted_context=True)
    monkeypatch.setattr(ctypes, "CDLL", lambda name: cuda)
    monkeypatch.setattr(sys, "argv", ["probe", GPU0])
    source = lab_gpu._negative_cuda_probe((19,))
    with pytest.raises(SystemExit) as raised:
        exec(compile(source, "<trusted-negative-cuda-probe>", "exec"), {})
    assert raised.value.code == 42
    assert cuda.freed and cuda.destroyed


def test_foreground_owner_requires_exact_terminal_identity(monkeypatch):
    lease = lab_gpu.ForegroundLease(Path("/tmp/lease"), "foreground:owner", 12345, "8",
                                    Path("/project"), (f"accelerator:{GPU0}",), (0,))
    monkeypatch.setattr(lab_gpu, "_process_start", lambda pid: None)
    owner = {"id": "foreground:owner", "owner_kind": "foreground", "state": "released",
             "pid": 12345, "process_start": "8", "resources": []}
    assert lab_gpu._validate_owner_terminal(lease, owner, (GPU0,))["verified"] is True
    owner["resources"] = [f"accelerator:{GPU0}"]
    with pytest.raises(lab_gpu.GpuLabUnavailable, match="terminal"):
        lab_gpu._validate_owner_terminal(lease, owner, (GPU0,))
