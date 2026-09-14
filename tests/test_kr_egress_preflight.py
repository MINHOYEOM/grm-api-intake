"""KR egress 프록시 **도달** preflight — 설정 여부가 아니라 도달 여부를 재는지 못박는다.

2026-09-08~14: `MFDS_HTTP_PROXY_CONFIGURED=true` 였지만 프록시 1대(52.79.207.141:8888)가
connection timeout 이었고, 이슈 #956 은 "MFDS 5종 실패" 로만 보였다. 이 preflight 가
있었다면 첫날에 "프록시 1대 사망" 한 줄이 떴다.
"""

from __future__ import annotations

import os
import socket
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import grm_common
import grm_health
from collect_intake import CollectionStats


class _Conn:
    closed = False

    def close(self) -> None:
        self.closed = True


class EnvMixin:
    def set_env(self, key: str, value: str | None) -> None:
        old = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

        def restore() -> None:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)


class ProxyEndpointParsingTest(unittest.TestCase):
    def test_bare_host_port(self) -> None:
        self.assertEqual(grm_common.kr_egress_proxy_endpoint("52.79.207.141:8888"),
                         ("52.79.207.141", 8888))

    def test_url_with_credentials_drops_userinfo(self) -> None:
        self.assertEqual(grm_common.kr_egress_proxy_endpoint("http://user:pw@kr-proxy.local:3128"),
                         ("kr-proxy.local", 3128))

    def test_scheme_default_ports(self) -> None:
        self.assertEqual(grm_common.kr_egress_proxy_endpoint("http://h"), ("h", 80))
        self.assertEqual(grm_common.kr_egress_proxy_endpoint("https://h"), ("h", 443))

    def test_empty_or_garbage_is_none(self) -> None:
        self.assertIsNone(grm_common.kr_egress_proxy_endpoint(""))
        self.assertIsNone(grm_common.kr_egress_proxy_endpoint("http://"))
        self.assertIsNone(grm_common.kr_egress_proxy_endpoint("http://h:notaport"))


class ProbeKrEgressProxyTest(EnvMixin, unittest.TestCase):
    def test_unconfigured(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", None)
        status, detail = grm_common.probe_kr_egress_proxy(connect=lambda *a, **k: _Conn())
        self.assertEqual(status, grm_common.KR_EGRESS_PROXY_UNCONFIGURED)
        self.assertIn("미설정", detail)

    def test_reachable_when_tcp_connect_succeeds(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://user:s3cret@kr-proxy.local:3128")
        conn = _Conn()
        seen: list[tuple] = []

        def fake_connect(address, timeout):
            seen.append((address, timeout))
            return conn

        status, detail = grm_common.probe_kr_egress_proxy(timeout=3, connect=fake_connect)
        self.assertEqual(status, grm_common.KR_EGRESS_PROXY_REACHABLE)
        self.assertEqual(seen, [(("kr-proxy.local", 3128), 3)])
        self.assertTrue(conn.closed, "판정용 연결은 곧바로 닫아야 한다")
        self.assertIn("kr-proxy.local:3128", detail)
        self.assertNotIn("s3cret", detail, "자격증명이 detail 로 새면 안 된다")

    def test_unreachable_when_tcp_connect_times_out(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://user:s3cret@52.79.207.141:8888")

        def fake_connect(address, timeout):
            raise socket.timeout("timed out")

        status, detail = grm_common.probe_kr_egress_proxy(connect=fake_connect)
        self.assertEqual(status, grm_common.KR_EGRESS_PROXY_UNREACHABLE)
        self.assertIn("52.79.207.141:8888", detail)
        self.assertIn("timed out", detail)
        self.assertNotIn("s3cret", detail)

    def test_unreachable_on_connection_refused(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "kr-proxy.local:3128")

        def fake_connect(address, timeout):
            raise ConnectionRefusedError(111, "Connection refused")

        status, _ = grm_common.probe_kr_egress_proxy(connect=fake_connect)
        self.assertEqual(status, grm_common.KR_EGRESS_PROXY_UNREACHABLE)


class HealthWiringTest(unittest.TestCase):
    """도달 불가는 **경고**로 health 에 실린다 — 실패면 그 주 발행이 막힌다."""

    def _health(self, **kw):
        base = dict(
            stats=CollectionStats(), active={"fr"}, enable_search=False,
            enable_mfds=False, enable_mfds_law=False, enable_mfds_recall=False,
            enable_mfds_admin=False, enable_mfds_gmp_cert=False,
            enable_mfds_safety_letter=False, enable_mfds_gmp_inspection=False,
            enable_ich=False, enable_who=False, enable_hc=False, enable_fda483=False,
            enable_moleg_api=False, enable_scrape=False, event_name="schedule",
            emit_routine_handoff=False, handoff_emitted=False, handoff_failed=False,
            handoff_error_msg="",
        )
        base.update(kw)
        return grm_health._evaluate_health(**base).finalize()

    def test_unreachable_proxy_is_a_warning_not_a_failure(self) -> None:
        health = self._health(kr_egress_proxy_status=grm_common.KR_EGRESS_PROXY_UNREACHABLE,
                              kr_egress_proxy_detail="52.79.207.141:8888 TCP 연결 실패")
        codes = [w.code for w in health.warnings]
        self.assertIn("kr-egress-proxy-unreachable", codes)
        self.assertEqual(health.exit_code, 0)
        self.assertEqual([f.code for f in health.failures], [])
        finding = next(w for w in health.warnings if w.code == "kr-egress-proxy-unreachable")
        # 사람이 해야 할 일이 detail 에 적혀 있어야 한다 — 코드로는 못 고치는 장애다.
        self.assertIn("사람", finding.detail)
        self.assertIn("MFDS_HTTP_PROXY", finding.detail)
        self.assertIn("52.79.207.141:8888", finding.detail)

    def test_reachable_or_unmeasured_emits_nothing(self) -> None:
        for status in (grm_common.KR_EGRESS_PROXY_REACHABLE,
                       grm_common.KR_EGRESS_PROXY_UNCONFIGURED, ""):
            health = self._health(kr_egress_proxy_status=status)
            self.assertNotIn("kr-egress-proxy-unreachable",
                             [w.code for w in health.warnings], status)


if __name__ == "__main__":
    unittest.main()
