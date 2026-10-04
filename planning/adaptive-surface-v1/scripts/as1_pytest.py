"""Pytest reporting plugin used by the bootstrap's product acceptance gates."""
from __future__ import annotations
import json
from pathlib import Path

_CASES: dict[str, list[dict[str,str]]] = {}


def pytest_addoption(parser):
    parser.addoption('--as1-report',action='store',default=None)


def pytest_configure(config):
    config.addinivalue_line('markers','as1_case(*ids): AS1 acceptance case IDs exercised by this behavioral test')
    _CASES.clear()


def pytest_runtest_makereport(item, call):
    # Called before reporting; only a normally executed call is passing evidence.
    if call.when!='call': return
    outcome='passed' if call.excinfo is None else 'failed'
    for mark in item.iter_markers('as1_case'):
        for case in mark.args:
            if isinstance(case,str):
                _CASES.setdefault(case,[]).append({'test':item.nodeid,'outcome':outcome})


def pytest_sessionfinish(session, exitstatus):
    target=session.config.getoption('--as1-report')
    if target:
        Path(target).write_text(json.dumps({'pytest_exitstatus':int(exitstatus),'cases':_CASES},sort_keys=True),encoding='utf-8')
