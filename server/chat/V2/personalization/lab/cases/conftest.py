"""Every case directory holds a `test_mock.py`, and the directories are named for the cases
(`cost-recount`, not importable packages), so pytest's default import mode would see two modules
called `test_mock` and refuse the second. Test modules under `cases/` are imported by path instead
(pytest's `importlib` mode), for this directory only."""

import pytest
from _pytest.python import Module


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    if not isinstance(collector, Module):
        return (yield)
    config = collector.config
    saved = config.option.importmode
    config.option.importmode = "importlib"
    try:
        return (yield)
    finally:
        config.option.importmode = saved
