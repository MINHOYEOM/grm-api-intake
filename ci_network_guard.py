"""Block external Python network calls in the CI and pytest runners."""

import ipaddress
import socket
import sys
import unittest
import urllib.request
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import PurePath
from unittest.mock import patch
from urllib.parse import urlsplit

import requests


class ExternalNetworkBlocked(AssertionError):
    """A test attempted to use the network without a fake response."""


def _is_loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if isinstance(host, str) and host.lower() in ("localhost", "localhost."):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or (
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        and address.ipv4_mapped.is_loopback
    )


def _test_location():
    frame = sys._getframe(1)
    while frame is not None:
        filename = PurePath(frame.f_code.co_filename).name
        if filename.startswith(("test_", "conftest.")):
            return f"{filename}:{frame.f_lineno} ({frame.f_code.co_name})"
        frame = frame.f_back
    return "test discovery or fixture"


class NetworkGuard:
    def __init__(self):
        self.test_id = None
        self.violations = []

    def _check(self, host, operation):
        # The isolated PostgreSQL jobs use local Unix sockets. TCP loopback
        # is also safe for test services, including local PostgreSQL.
        if host is None or _is_loopback(host):
            return
        owner = self.test_id or _test_location()
        message = f"External network blocked: {owner} -> {host!r} ({operation})"
        self.violations.append(message)
        raise ExternalNetworkBlocked(message)

    @contextmanager
    def installed(self):
        with ExitStack() as patches:
            def intercept(target, name, check):
                original = getattr(target, name)

                @wraps(original)
                def guarded(*args, **kwargs):
                    check(name, *args, **kwargs)
                    return original(*args, **kwargs)

                patches.enter_context(patch.object(target, name, guarded))

            def check_address(name, address, *args, **kwargs):
                if isinstance(address, tuple) and address:
                    self._check(address[0], f"socket.{name}")

            def check_socket(name, sock, *args, **kwargs):
                if sock.family in (socket.AF_INET, socket.AF_INET6):
                    check_address(name, args[-1] if args else kwargs["address"])

            def check_sendmsg(name, sock, buffers, ancdata=(), flags=0, address=None):
                if sock.family in (socket.AF_INET, socket.AF_INET6):
                    check_address(name, address)

            def check_lookup(name, host, *args, **kwargs):
                if host != "" and host != b"":
                    self._check(host, f"socket.{name}")

            def check_requests(name, session, request, **kwargs):
                self._check(urlsplit(request.url).hostname, "requests.Session.send")

            def check_urllib(name, opener, fullurl, *args, **kwargs):
                url = getattr(fullurl, "full_url", fullurl)
                self._check(urlsplit(url).hostname, "urllib.request")

            intercept(socket, "create_connection", check_address)
            for name in ("connect", "connect_ex", "sendto"):
                intercept(socket.socket, name, check_socket)
            if hasattr(socket.socket, "sendmsg"):
                intercept(socket.socket, "sendmsg", check_sendmsg)
            for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
                intercept(socket, name, check_lookup)
            intercept(requests.Session, "send", check_requests)
            intercept(urllib.request.OpenerDirector, "open", check_urllib)
            yield self


def run_guarded(run):
    guard = NetworkGuard()
    with guard.installed():
        status = run(guard)
    for violation in guard.violations:
        print(violation, file=sys.stderr)
    return 1 if guard.violations else status


def main(argv=None):
    def run(_guard):
        program = unittest.main(module=None, argv=argv, exit=False)
        return 0 if program.result.wasSuccessful() else 1

    return run_guarded(run)


if __name__ == "__main__":
    sys.exit(main())
