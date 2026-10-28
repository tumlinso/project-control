"""Security and resource-boundary tests for the scratch laboratory runner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from project_control.assistance import lab_runner
from project_control.assistance.lab_runner import (
    ContainmentUnavailable,
    EffectCleanupUnverified,
    ExecutionGrant,
    InvalidExecutionRequest,
    _make_bwrap_command,
    _prepare_cgroup,
    _sandbox_probe,
    run_cpu,
)


class LabRunnerContractTests(unittest.TestCase):
    def test_default_grant_is_bounded_and_cannot_be_amplified(self) -> None:
        grant = ExecutionGrant()
        self.assertEqual(grant.cpu_cores, 1)
        self.assertEqual(grant.memory_bytes, 1024**3)
        self.assertEqual(grant.pids, 32)
        self.assertEqual(grant.timeout_seconds, 30)
        self.assertEqual(grant.output_bytes, 64 * 1024)
        self.assertEqual(grant.artifact_bytes, 64 * 1024**2)
        with self.assertRaises(InvalidExecutionRequest):
            ExecutionGrant(memory_bytes=1024**3 + 1)
        with self.assertRaises(InvalidExecutionRequest):
            ExecutionGrant(cpu_cores=2)

    def test_no_delegated_cgroup_fails_closed_before_artifact_or_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            attempt = root / "attempt"
            with self.assertRaises(ContainmentUnavailable):
                run_cpu(snapshot, ("/usr/bin/true",), attempt_root=attempt, cgroup_parent=root)
            self.assertFalse((attempt / lab_runner.ARTIFACT_NAME).exists())

    def test_full_runner_executes_only_under_current_delegated_cgroup(self) -> None:
        try:
            parent = lab_runner._current_cgroup_parent()
            controllers = set((parent / "cgroup.controllers").read_text().split())
            members = (parent / "cgroup.procs").read_text().split()
            if not {"cpu", "memory", "pids"} <= controllers or members or not os.access(parent, os.W_OK | os.X_OK):
                self.skipTest("current process has no delegated cpu/memory/pids cgroup")
        except (OSError, ContainmentUnavailable):
            self.skipTest("current process has no delegated cgroup v2")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            (snapshot / "input.txt").write_text("captured")
            attempt = root / "attempt"
            observed: list[lab_runner.OwnedProcessIdentity] = []
            limits_seen: list[dict[str, str]] = []

            def persist_identity(identity: lab_runner.OwnedProcessIdentity) -> None:
                observed.append(identity)
                group = Path(identity.cgroup_path)
                limits_seen.append({
                    name: (group / name).read_text().strip()
                    for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
                })

            result = run_cpu(
                snapshot,
                ("/usr/bin/python3", "-c", "from pathlib import Path; Path('/artifacts/result.bin').write_bytes(Path('input.txt').read_bytes().upper())"),
                attempt_root=attempt,
                effect_id="lab-runner-positive-test",
                on_started=persist_identity,
            )
            self.assertEqual(result.status, "ok", result.stderr.decode("utf-8", "replace"))
            self.assertEqual(result.artifact_path, attempt / lab_runner.ARTIFACT_NAME)
            self.assertEqual(result.artifact_path.read_bytes(), b"CAPTURED")
            self.assertEqual(result.artifact_bytes, 8)
            self.assertEqual(len(observed), 1)
            self.assertEqual(result.process_identity, observed[0])
            self.assertEqual(limits_seen, [{
                "memory.max": str(1024**3),
                "memory.swap.max": "0",
                "pids.max": "32",
                "cpu.max": "100000 100000",
            }])
            self.assertEqual(lab_runner.reconcile_process(observed[0]), "exited")

    def test_start_callback_failure_closes_gate_before_user_code(self) -> None:
        if not lab_runner.current_cgroup_ready():
            self.skipTest("current process has no delegated cgroup parent")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            attempt = root / "attempt"

            def reject_start(_identity: lab_runner.OwnedProcessIdentity) -> None:
                raise RuntimeError("receipt store unavailable")

            with self.assertRaisesRegex(RuntimeError, "receipt store unavailable"):
                run_cpu(
                    snapshot,
                    ("/usr/bin/python3", "-c", "from pathlib import Path; Path('/artifacts/result.bin').write_text('ran')"),
                    attempt_root=attempt,
                    effect_id="lab-runner-start-gate-test",
                    on_started=reject_start,
                )
            self.assertEqual((attempt / lab_runner.ARTIFACT_NAME).stat().st_size, 0)

    def test_unverified_cleanup_keeps_exact_identity_and_never_runs_user_code(self) -> None:
        if not lab_runner.current_cgroup_ready():
            self.skipTest("current process has no delegated cgroup parent")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            attempt = root / "attempt"

            def reject_start(_identity: lab_runner.OwnedProcessIdentity) -> None:
                raise RuntimeError("receipt store unavailable")

            identity = None
            with patch.object(lab_runner, "_kill_cgroup", return_value=False), \
                    patch.object(lab_runner, "_cgroup_populated", return_value=True):
                with self.assertRaises(EffectCleanupUnverified) as caught:
                    run_cpu(
                        snapshot,
                        ("/usr/bin/python3", "-c", "from pathlib import Path; Path('/artifacts/result.bin').write_text('ran')"),
                        attempt_root=attempt,
                        effect_id="lab-runner-unverified-cleanup-test",
                        on_started=reject_start,
                    )
                identity = caught.exception.identity
            self.assertEqual((attempt / lab_runner.ARTIFACT_NAME).stat().st_size, 0)
            self.assertEqual(identity.effect_id, "lab-runner-unverified-cleanup-test")
            cgroup = lab_runner._Cgroup(Path(identity.cgroup_path), identity.cgroup_inode)
            self.assertTrue(lab_runner._remove_cgroup(cgroup), "the gated launcher exited and the test removes its empty cgroup")

    def test_timeout_kills_and_reconciles_the_exact_owned_cgroup(self) -> None:
        if not lab_runner.current_cgroup_ready():
            self.skipTest("current process has no delegated cgroup parent")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            result = run_cpu(
                snapshot,
                ("/usr/bin/sleep", "5"),
                ExecutionGrant(timeout_seconds=0.2),
                attempt_root=root / "attempt",
                effect_id="lab-runner-timeout-test",
            )
            self.assertEqual(result.status, "timeout")
            self.assertTrue(result.cleanup_verified)
            self.assertIsNotNone(result.process_identity)
            self.assertEqual(lab_runner.reconcile_process(result.process_identity), "exited")

    def test_detached_descendant_is_killed_when_leader_exits(self) -> None:
        if not lab_runner.current_cgroup_ready():
            self.skipTest("current process has no delegated cgroup parent")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            child = "\n".join((
                "from pathlib import Path",
                "import time",
                "status=Path('/proc/self/status').read_text().splitlines()",
                "host_pid=int(next(line for line in status if line.startswith('NSpid:')).split()[1])",
                "stat=Path('/proc/self/stat').read_text()",
                "start=int(stat[stat.rfind(')')+2:].split()[19])",
                "Path('/artifacts/result.bin').write_text(f'{host_pid}:{start}')",
                "time.sleep(5)",
            ))
            leader = "\n".join((
                "import os,subprocess,sys,time",
                f"child=subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)",
                "for _ in range(200):",
                " try:",
                "  if open('/artifacts/result.bin').read(): break",
                " except OSError: pass",
                " time.sleep(.005)",
                "else: raise RuntimeError('detached child did not publish its identity')",
                "os.kill(child.pid,0)",
                "# The PID namespace init exits immediately after this script.",
            ))
            result = run_cpu(
                snapshot,
                ("/usr/bin/python3", "-c", leader),
                attempt_root=root / "attempt",
                effect_id="lab-runner-descendant-test",
            )
            self.assertIn(result.status, {"ok", "descendants_remaining"})
            self.assertTrue(result.cleanup_verified)
            self.assertIsNotNone(result.process_identity)
            self.assertIsNotNone(result.artifact_path)
            child_pid, child_start = map(int, result.artifact_path.read_text().split(":"))
            self.assertNotEqual(lab_runner._process_start_time(child_pid), child_start)
            self.assertEqual(lab_runner.reconcile_process(result.process_identity), "exited")

    def test_cgroup_configuration_applies_all_host_resource_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "delegated"
            parent.mkdir()
            (parent / "cgroup.controllers").write_text("cpu memory pids")
            (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")
            (parent / "cgroup.procs").write_text("")
            (parent / "cgroup.kill").write_text("")
            old_root = lab_runner.CGROUP_ROOT
            lab_runner.CGROUP_ROOT = root
            try:
                cgroup = _prepare_cgroup(parent, ExecutionGrant(), "test-effect")
            finally:
                lab_runner.CGROUP_ROOT = old_root
            self.assertEqual((cgroup.path / "memory.max").read_text(), str(1024**3))
            self.assertEqual((cgroup.path / "memory.swap.max").read_text(), "0")
            self.assertEqual((cgroup.path / "pids.max").read_text(), "32")
            self.assertEqual((cgroup.path / "cpu.max").read_text(), "100000 100000")

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is unavailable")
    def test_real_bubblewrap_denies_host_paths_network_and_bounds_artifact_file(self) -> None:
        bwrap = shutil.which("bwrap")
        self.assertIsNotNone(bwrap)
        assert bwrap is not None
        self.assertTrue(_sandbox_probe(bwrap), "host namespace/mount probe must pass")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            source_sentinel = snapshot / "canonical-sentinel.txt"
            source_sentinel.write_text("preserve")
            artifact = root / "artifact.bin"
            artifact.touch(mode=0o600)
            proposal = root / "proposal"
            proposal.mkdir(mode=0o700)
            proposal_file = proposal / "generated-test.py"
            proposal_file.write_text("print('proposal')\n")
            proposal_file.chmod(0o400)
            proposal.chmod(0o500)
            host_secret = root / "host-secret.txt"
            host_secret.write_text("host-only")
            python = "\n".join((
                "import json,pathlib,socket",
                "result={}",
                "def try_write(p): pathlib.Path(p).write_text('altered')",
                "try:",
                " try_write('/workspace/canonical-sentinel.txt'); result['source_write']='allowed'",
                "except OSError as exc: result['source_write']=exc.__class__.__name__",
                "result['proposal_visible']=pathlib.Path('/proposal/generated-test.py').read_text().strip()",
                "try:",
                " try_write('/proposal/generated-test.py'); result['proposal_write']='allowed'",
                "except OSError as exc: result['proposal_write']=exc.__class__.__name__",
                "try:",
                f" try_write({str(host_secret)!r}); result['host_write']='allowed'",
                "except OSError as exc: result['host_write']=exc.__class__.__name__",
                "result['host_secret_visible']=pathlib.Path('/etc/passwd').exists()",
                "result['host_credential_visible']=pathlib.Path('/root/.ssh').exists()",
                "result['host_device_visible']=pathlib.Path('/dev/nvidia0').exists()",
                "try:",
                " socket.create_connection(('127.0.0.1',8767),timeout=.2); result['host_loopback']='connected'",
                "except OSError as exc: result['host_loopback']=exc.__class__.__name__",
                "print(json.dumps(result),flush=True)",
                "pathlib.Path('/artifacts/result.bin').write_bytes(b'x'*2048)",
            ))
            command = _make_bwrap_command(bwrap, snapshot.resolve(), artifact,
                                          ("/usr/bin/python3", "-c", python), 1024, proposal.resolve())
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin"}, timeout=5, check=False)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout, result.stderr.decode("utf-8", "replace"))
            observations = json.loads(result.stdout.splitlines()[-1])
            self.assertNotEqual(observations["source_write"], "allowed")
            self.assertEqual(observations["proposal_visible"], "print('proposal')")
            self.assertNotEqual(observations["proposal_write"], "allowed")
            self.assertNotEqual(observations["host_write"], "allowed")
            self.assertFalse(observations["host_secret_visible"])
            self.assertFalse(observations["host_credential_visible"])
            self.assertFalse(observations["host_device_visible"])
            self.assertNotEqual(observations["host_loopback"], "connected")
            self.assertEqual(source_sentinel.read_text(), "preserve")
            self.assertLessEqual(artifact.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
