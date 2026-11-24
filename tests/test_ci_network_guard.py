import io
import socket
import sys
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import requests

import ci_shard
from ci_network_guard import ExternalNetworkBlocked, NetworkGuard


HOST = "network-guard.invalid"
URL = f"https://{HOST}/"


class NetworkGuardTest(unittest.TestCase):
    def test_socket_blocks_external_before_transport_and_allows_loopback(self):
        guard = NetworkGuard()
        guard.test_id = "example.Test.test_socket"
        with patch.object(socket, "create_connection") as connect, guard.installed():
            with self.assertRaisesRegex(
                ExternalNetworkBlocked, rf"example\.Test\.test_socket.*{HOST}"
            ):
                socket.create_connection((HOST, 443))
            connect.assert_not_called()
            for host in ("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"):
                socket.create_connection((host, 5432))
            self.assertEqual(connect.call_count, 4)

    def test_http_clients_block_target_before_transport(self):
        with (
            patch.object(requests.adapters.HTTPAdapter, "send") as requests_transport,
            patch.object(urllib.request.OpenerDirector, "_open") as urllib_transport,
            NetworkGuard().installed(),
        ):
            for get in (requests.get, urllib.request.urlopen):
                with self.assertRaisesRegex(ExternalNetworkBlocked, HOST):
                    get(URL)
            requests_transport.assert_not_called()
            urllib_transport.assert_not_called()

    def test_shard_fails_even_when_the_test_catches_the_block(self):
        class CatchesNetworkError(unittest.TestCase):
            def runTest(self):
                try:
                    socket.create_connection((HOST, 443))
                except Exception:
                    pass

        test = CatchesNetworkError()
        stderr = io.StringIO()
        # Stub the transport so the mutation check cannot open a real connection.
        with (
            patch.object(socket, "create_connection") as transport,
            patch.object(ci_shard, "discover", return_value=[test]),
            patch.object(ci_shard, "load_timings", return_value={}),
            patch.object(sys, "path", sys.path.copy()),
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            self.assertEqual(ci_shard.main(["--index", "0", "--total", "1"]), 1)
        output = stderr.getvalue()
        self.assertIn("FAILED (failures=1)", output)
        self.assertIn(test.id(), output)
        self.assertIn(HOST, output)
        transport.assert_not_called()
