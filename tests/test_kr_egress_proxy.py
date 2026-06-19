"""KR-egress proxy and MFDS residual-board selection tests."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mfds
import grm_common


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    content = b'{"ok": true}'
    text = '{"ok": true}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, bool]:
        return {"ok": True}


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


class KrEgressProxyTest(EnvMixin, unittest.TestCase):
    def test_proxy_disabled_by_default(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", None)
        self.assertIsNone(grm_common._proxies_for("https://www.mfds.go.kr/www/rss/brd.do"))

    def test_proxy_applies_only_to_mfds_nedrug_and_law_hosts(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://kr-proxy.local:3128")
        expected = {
            "http": "http://kr-proxy.local:3128",
            "https": "http://kr-proxy.local:3128",
        }
        self.assertEqual(grm_common._proxies_for("https://www.mfds.go.kr/www/rss/brd.do"), expected)
        self.assertEqual(grm_common._proxies_for("https://nedrug.mfds.go.kr/pbp/CCBBD03/getList"), expected)
        self.assertEqual(grm_common._proxies_for("https://www.law.go.kr/DRF/lawService.do"), expected)
        self.assertIsNone(grm_common._proxies_for("https://apis.data.go.kr/1170000/law/lawSearchList.do"))
        self.assertIsNone(grm_common._proxies_for("https://api.fda.gov/drug/enforcement.json"))

    def test_http_get_json_passes_proxy_to_requests(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://kr-proxy.local:3128")
        calls: list[dict] = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            return _Response()

        original = grm_common.requests.get
        grm_common.requests.get = fake_get
        try:
            self.assertEqual(grm_common.http_get_json("https://www.mfds.go.kr/test"), {"ok": True})
        finally:
            grm_common.requests.get = original

        self.assertEqual(
            calls[0]["proxies"],
            {"http": "http://kr-proxy.local:3128", "https": "http://kr-proxy.local:3128"},
        )


class MfdsRssBoardSelectionTest(EnvMixin, unittest.TestCase):
    def test_board_selection_defaults_to_all_boards(self) -> None:
        self.set_env("MFDS_RSS_BOARD_MODE", None)
        self.set_env("MFDS_RSS_BOARD_IDS", None)
        self.assertEqual(collect_mfds._configured_rss_boards(), collect_mfds.MFDS_RSS_BOARDS)

    def test_residual_mode_selects_guidance_boards_only(self) -> None:
        self.set_env("MFDS_RSS_BOARD_MODE", "residual")
        self.set_env("MFDS_RSS_BOARD_IDS", None)
        self.assertEqual(
            [brd_id for brd_id, _type in collect_mfds._configured_rss_boards()],
            ["data0013", "data0011", "data0010"],
        )

    def test_explicit_board_ids_override_mode(self) -> None:
        self.set_env("MFDS_RSS_BOARD_MODE", "residual")
        self.set_env("MFDS_RSS_BOARD_IDS", "data0011 seohan001")
        self.assertEqual(
            [brd_id for brd_id, _type in collect_mfds._configured_rss_boards()],
            ["data0011", "seohan001"],
        )


if __name__ == "__main__":
    unittest.main()
