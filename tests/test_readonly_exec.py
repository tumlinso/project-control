from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from project_control.services.readonly_exec import exec_readonly
from project_control.terminal import BubblewrapSandbox


@unittest.skipUnless(BubblewrapSandbox().probe(), "bubblewrap unavailable")
class ReadonlyExecTests(unittest.TestCase):
    def test_reads_host_and_repo_but_cannot_write_them(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home() / ".cache") as temporary:
            outside = Path(temporary) / "ordinary.txt"
            outside.write_text("ordinary-host-evidence\n", encoding="utf-8")
            read = exec_readonly(["cat", str(outside)], cwd=temporary, default_cwd=Path.cwd())
            self.assertEqual((read["returncode"], read["stdout"]), (0, "ordinary-host-evidence\n"))
            write = exec_readonly(["bash", "-lc", f"echo changed > {outside}"], cwd=temporary, default_cwd=Path.cwd())
            self.assertNotEqual(write["returncode"], 0)
            self.assertEqual(outside.read_text(encoding="utf-8"), "ordinary-host-evidence\n")

    def test_private_tmp_works_network_and_runtime_sockets_do_not(self) -> None:
        temporary = exec_readonly(["bash", "-lc", "echo ok >/tmp/x; cat /tmp/x"], cwd=str(Path.cwd()), default_cwd=Path.cwd())
        self.assertEqual(temporary["stdout"], "ok\n")
        network = exec_readonly(["python3", "-c", "import socket;s=socket.socket();s.connect(('1.1.1.1',80))"], cwd=str(Path.cwd()), default_cwd=Path.cwd(), timeout_seconds=2)
        self.assertNotEqual(network["returncode"], 0)
        runtime = exec_readonly(["bash", "-lc", "test -S /run/user/$(id -u)/bus"], cwd=str(Path.cwd()), default_cwd=Path.cwd())
        self.assertNotEqual(runtime["returncode"], 0)

    def test_credentials_are_masked_and_bad_processes_are_terminated(self) -> None:
        hidden = exec_readonly(["bash", "-lc", "test ! -e /home/tumlinson/.ssh/id_rsa"], cwd=str(Path.cwd()), default_cwd=Path.cwd())
        self.assertEqual(hidden["returncode"], 0)
        output = exec_readonly(["python3", "-c", "print('x'*100000)"], cwd=str(Path.cwd()), default_cwd=Path.cwd(), output_limit=2048)
        self.assertEqual(output["status"], "output_limit")
        self.assertLessEqual(len(output["stdout"].encode()) + len(output["stderr"].encode()), 2048)
        timed = exec_readonly(["python3", "-c", "import time; time.sleep(5)"], cwd=str(Path.cwd()), default_cwd=Path.cwd(), timeout_seconds=.1)
        self.assertEqual(timed["status"], "timeout")


if __name__ == "__main__":
    unittest.main()
