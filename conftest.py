"""Use the CI network guard when running tests with pytest."""

import sys
from contextlib import ExitStack

import pytest

from ci_network_guard import NetworkGuard


_guard = pytest.StashKey[NetworkGuard]()


def pytest_sessionstart(session):
    guard = session.config.stash[_guard] = NetworkGuard()
    patches = ExitStack()
    session.config.add_cleanup(patches.close)
    patches.enter_context(guard.installed())


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item):
    guard = item.config.stash[_guard]
    guard.test_id = item.nodeid
    try:
        yield
    finally:
        guard.test_id = None


def pytest_sessionfinish(session):
    guard = session.config.stash[_guard]
    if guard.violations:
        for violation in guard.violations:
            print(violation, file=sys.stderr)
        if session.exitstatus == pytest.ExitCode.OK:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
