"""KR-egress proxy and MFDS residual-board selection tests."""

from __future__ import annotations

import ast
import glob
import io
import os
import sys
import unittest
from urllib.parse import urlparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

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


class KrGovEndpointSweepTest(EnvMixin, unittest.TestCase):
    """수집기 `*_ENDPOINT` 상수 **전수** — KR 정부 호스트면 반드시 KR egress 를 탄다.

    ★호스트 집합(`MFDS_EGRESS_HOSTS`)은 손목록이다. 손목록만 고치면 새 수집기가 새 KR 정부
    호스트를 들고 들어와도 아무도 안 잡고, 그 소스는 해외 IP 차단이 시작되는 날 조용히 0건이
    된다(apis.data.go.kr 이 정확히 그렇게 2026-08-02·08-24 두 번 무너졌다). 그래서 검사는
    **저장소 파생**이다 — `collect_*.py` 를 AST 로 훑어 모든 모듈 수준 `*_ENDPOINT` 문자열
    상수를 모으고, `.go.kr` 호스트면 프록시가 붙는지 본다. import 하지 않으므로 부작용이 없다.
    """

    def _endpoints(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for path in sorted(glob.glob(os.path.join(REPO, "collect_*.py"))):
            with io.open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in tree.body:  # 모듈 수준만 — 함수 안 지역 상수는 대상이 아니다
                targets = node.targets if isinstance(node, ast.Assign) else (
                    [node.target] if isinstance(node, ast.AnnAssign) else [])
                for tgt in targets:
                    if not isinstance(tgt, ast.Name) or not tgt.id.endswith("_ENDPOINT"):
                        continue
                    val = node.value
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        found.append((os.path.basename(path) + ":" + tgt.id, val.value))
        return found

    def test_every_collector_endpoint_on_go_kr_is_proxied(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://kr-proxy.local:3128")
        endpoints = self._endpoints()
        # 0건 가드 — 글롭이나 명명 관례가 바뀌어 아무것도 못 찾으면 이 검사는 침묵한다.
        self.assertGreaterEqual(len(endpoints), 5, f"수집기 엔드포인트 상수를 못 찾았다: {endpoints}")
        kr = [(name, url) for name, url in endpoints
              if (urlparse(url).hostname or "").lower().endswith(".go.kr")]
        self.assertGreaterEqual(len(kr), 4, f"KR 정부 엔드포인트를 못 찾았다: {endpoints}")
        unproxied = [name for name, url in kr if grm_common.proxies_for(url) is None]
        self.assertEqual(
            unproxied, [],
            "KR 정부 API 인데 KR egress 를 안 탄다 — MFDS_EGRESS_HOSTS 에 호스트를 추가하라: "
            f"{unproxied}")


class KrEgressProxyTest(EnvMixin, unittest.TestCase):
    def test_proxy_disabled_by_default(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", None)
        self.assertIsNone(grm_common._proxies_for("https://www.mfds.go.kr/www/rss/brd.do"))

    def test_proxy_applies_only_to_kr_gov_api_hosts(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", "http://kr-proxy.local:3128")
        expected = {
            "http": "http://kr-proxy.local:3128",
            "https": "http://kr-proxy.local:3128",
        }
        self.assertEqual(grm_common._proxies_for("https://www.mfds.go.kr/www/rss/brd.do"), expected)
        self.assertEqual(grm_common._proxies_for("https://nedrug.mfds.go.kr/pbp/CCBBD03/getList"), expected)
        self.assertEqual(grm_common._proxies_for("https://www.law.go.kr/DRF/lawService.do"), expected)
        # ★2026-08-26 추가 — 공공데이터포털도 KR egress 로 보낸다. 러너에서 직접 열던 이
        #   호스트가 08-24 부터 연결 자체를 timeout 시켰고(회수·행정처분 3일 0건), 같은 시각
        #   한국에서는 70ms 에 붙었다. 08-02~08-05 4연속 0건도 같은 원인이었다.
        self.assertEqual(grm_common._proxies_for("https://apis.data.go.kr/1170000/law/lawSearchList.do"), expected)
        # 경계는 음성 검사로 고정한다 — KR 정부 호스트가 아니면 프록시를 타지 않는다.
        self.assertIsNone(grm_common._proxies_for("https://api.fda.gov/drug/enforcement.json"))
        self.assertIsNone(grm_common._proxies_for("https://api.notion.com/v1/pages"))
        self.assertIsNone(grm_common._proxies_for("https://data.go.kr.evil.example.com/x"))

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


class KrEgressProxyFailoverTest(EnvMixin, unittest.TestCase):
    """프록시 홉이 죽으면 직결로 1회 폴백한다(2026-09-14 사고 회귀 방지).

    프록시 1대가 timeout 나자 MFDS_EGRESS_HOSTS 4종이 동시에 0건이 됐다. 직결이 열려
    있는 날에도 시도조차 안 했기 때문이다. 폴백 경계(프록시 홉 실패만)도 함께 못박는다.
    """

    PROXY = "http://kr-proxy.local:3128"
    URL = "https://www.mfds.go.kr/www/rss/brd.do"

    def _patch_get(self, fake):
        original = grm_common.requests.get
        grm_common.requests.get = fake
        self.addCleanup(lambda: setattr(grm_common.requests, "get", original))

    def test_direct_retry_when_proxy_hop_fails(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", self.PROXY)
        calls: list[dict] = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            if kwargs.get("proxies"):
                raise grm_common.requests.exceptions.ProxyError(
                    "Cannot connect to proxy kr-proxy.local:3128")
            return _Response()

        self._patch_get(fake_get)
        self.assertEqual(grm_common.http_get_json(self.URL), {"ok": True})
        self.assertEqual(len(calls), 2, "프록시 1회 + 직결 1회여야 한다")
        self.assertEqual(calls[0]["proxies"], {"http": self.PROXY, "https": self.PROXY})
        self.assertIsNone(calls[1]["proxies"], "폴백은 프록시를 떼고 나가야 한다")

    def test_connect_timeout_naming_the_proxy_is_a_hop_failure(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", self.PROXY)
        err = grm_common.requests.exceptions.ConnectTimeout(
            "HTTPSConnectionPool(host='kr-proxy.local', port=3128): Read timed out")
        self.assertTrue(grm_common._is_proxy_hop_failure(err, self.PROXY))

    def test_origin_connect_timeout_is_not_a_hop_failure(self) -> None:
        """원 서버가 늦은 것까지 직결로 다시 쏘면 차단 IP 로 두 배 두드릴 뿐이다."""
        err = grm_common.requests.exceptions.ConnectTimeout(
            "HTTPSConnectionPool(host='www.mfds.go.kr', port=443): Read timed out")
        self.assertFalse(grm_common._is_proxy_hop_failure(err, self.PROXY))

    def test_origin_5xx_is_not_retried_direct(self) -> None:
        """5xx 는 프록시가 정상 동작했다는 뜻 — 폴백 대상이 아니다."""
        self.set_env("MFDS_HTTP_PROXY", self.PROXY)
        calls: list[dict] = []

        class _Err500(_Response):
            status_code = 500

            def raise_for_status(self):
                raise grm_common.requests.exceptions.HTTPError("500")

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            return _Err500()

        self._patch_get(fake_get)
        with self.assertRaises(RuntimeError):
            grm_common.http_get_json(self.URL, retries=0)
        self.assertEqual([c["proxies"] for c in calls],
                         [{"http": self.PROXY, "https": self.PROXY}])

    def test_no_proxy_configured_is_unchanged(self) -> None:
        self.set_env("MFDS_HTTP_PROXY", None)
        calls: list[dict] = []

        def fake_get(url, **kwargs):
            calls.append(kwargs)
            return _Response()

        self._patch_get(fake_get)
        self.assertEqual(grm_common.http_get_json(self.URL), {"ok": True})
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["proxies"])


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
