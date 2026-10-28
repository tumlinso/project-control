import os
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from project_control.as1_jobs import JobService, _reconcile_file_lock


def _service(directory):
    service = object.__new__(JobService)
    service.directory = directory
    return service


def test_reconcile_can_nest_on_same_thread(tmp_path):
    service = _service(tmp_path)
    calls = 0

    def reconcile_body():
        nonlocal calls
        calls += 1
        if calls == 1:
            service.reconcile()

    service._reconcile = reconcile_body
    thread = threading.Thread(target=service.reconcile, daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive(), "nested reconcile deadlocked on its own flock"
    assert calls == 2


def test_reconcile_remains_exclusive_between_threads(tmp_path):
    first = _service(tmp_path)
    second = _service(tmp_path)
    first_entered = threading.Event()
    second_attempting = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def hold_first():
        first_entered.set()
        assert release_first.wait(timeout=2)

    first._reconcile = hold_first
    second._reconcile = second_entered.set
    first_thread = threading.Thread(target=first.reconcile)
    def enter_second():
        second_attempting.set()
        second.reconcile()

    second_thread = threading.Thread(target=enter_second)
    first_thread.start()
    assert first_entered.wait(timeout=2)
    second_thread.start()
    assert second_attempting.wait(timeout=2)

    try:
        assert not second_entered.wait(timeout=0.05), "second thread bypassed reconcile lock"
    finally:
        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()


def test_reconcile_lock_excludes_another_process(tmp_path):
    probe = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(0)
    raise SystemExit(9)
finally:
    os.close(fd)
"""
    with _reconcile_file_lock(tmp_path):
        result = subprocess.run([sys.executable, "-c", probe,
                                 str(tmp_path / "reconcile.lock")], check=False)
    assert result.returncode == 0


def test_reconcile_lock_closes_fd_if_unlock_raises(tmp_path):
    import fcntl

    opened = []

    def fail_unlock(fd, operation):
        if operation == fcntl.LOCK_EX:
            opened.append(fd)
            return None
        raise OSError("unlock failed")

    with patch("project_control.as1_jobs.fcntl.flock", side_effect=fail_unlock):
        with pytest.raises(OSError, match="unlock failed"):
            with _reconcile_file_lock(tmp_path):
                pass

    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
