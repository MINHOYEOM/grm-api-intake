#!/usr/bin/env python3
"""링크체크(P3·C1) 단위테스트 — 전부 모킹(네트워크 0·결정론).

검증: 상태 산정표 · KR-egress 스킵 · HEAD→GET 폴백 · 타임아웃/연결실패 분기 ·
런 캐시 · enrich(빈 URL 보존) · 비파괴 run(원본 불변) · JSON 파싱실패 비차단 통과 ·
CLI 비차단(인자 오용에도 exit 0).

CI(`unittest discover -s tests`)는 `tests/test_web_linkcheck.py` shim 으로 순회한다.
직접 실행: python web/tests/test_linkcheck.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import requests

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent      # …/web
sys.path.insert(0, str(WEB_DIR))
import linkcheck  # noqa: E402  (web/linkcheck.py — 경로 삽입 후 import)

__all__ = [
    "ClassifyStatusTest",
    "KrSkipTest",
    "BotwallTest",
    "CheckUrlTest",
    "MakeCheckerTest",
    "EnrichBriefTest",
    "RunTest",
    "CliTest",
]


# ── 모킹 세션 ─────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, status_code: int, url: str = ""):
        self.status_code = status_code
        self.url = url                    # 리다이렉트 최종 URL(봇월/교차호스트 판정용)

    def close(self):
        pass


def _step(spec, url):
    """spec → (응답 or 예외 발생). int=상태코드, Exception(클래스/인스턴스)=raise."""
    if isinstance(spec, type) and issubclass(spec, BaseException):
        raise spec("mock")
    if isinstance(spec, BaseException):
        raise spec
    return _Resp(int(spec), url)


class FakeSession:
    """head/get 응답을 주입. 각 인자는 단일 spec 또는 spec 리스트(시도별 순차).

    final_url 지정 시 응답 .url 을 그 값으로(리다이렉트 시뮬레이션) — 미지정이면 요청 URL.
    """

    def __init__(self, head=None, get=None, final_url=None):
        self.headers = {}
        self._head = head
        self._get = get
        self._final = final_url
        self.head_calls = 0
        self.get_calls = 0

    @staticmethod
    def _pick(spec, n):
        if isinstance(spec, list):
            return spec[min(n, len(spec) - 1)]
        return spec

    def head(self, url, **kw):
        spec = self._pick(self._head, self.head_calls)
        self.head_calls += 1
        return _step(spec, self._final or url)

    def get(self, url, **kw):
        spec = self._pick(self._get, self.get_calls)
        self.get_calls += 1
        return _step(spec, self._final or url)

    def close(self):
        pass


# ── 상태 산정(순수) ──────────────────────────────────────────────────────────
class ClassifyStatusTest(unittest.TestCase):
    def test_2xx_3xx_ok(self):
        for code in (200, 204, 301, 302, 307, 308, 399):
            self.assertEqual(linkcheck.classify_status(code), linkcheck.OK, code)

    def test_401_403_inconclusive(self):
        # 봇방어 추정 → 단정 금지(상태 보존). false-broken 방지(§3.2).
        for code in (401, 403):
            self.assertEqual(linkcheck.classify_status(code), linkcheck.INCONCLUSIVE, code)

    def test_transient_and_5xx_degraded(self):
        for code in (408, 429, 500, 502, 503, 504):
            self.assertEqual(linkcheck.classify_status(code), linkcheck.DEGRADED, code)

    def test_gone_and_other_4xx_broken(self):
        for code in (404, 410, 400, 451):
            self.assertEqual(linkcheck.classify_status(code), linkcheck.BROKEN, code)


# ── KR-egress 스킵(순수) ─────────────────────────────────────────────────────
class KrSkipTest(unittest.TestCase):
    def test_kr_domains_skipped(self):
        for url in (
            "https://nedrug.mfds.go.kr/pbp/CCBBB01",
            "https://www.mfds.go.kr/brd/m_99",
            "https://apis.data.go.kr/1471000/x",
            "http://example.or.kr/path",
            "https://mfds.go.kr",
        ):
            self.assertTrue(linkcheck.is_kr_skip(url), url)

    def test_non_kr_not_skipped(self):
        for url in (
            "https://www.fda.gov/media/192438/download",
            "https://www.ema.europa.eu/en",
            "https://example.com/go.kr",          # 경로에 go.kr — 호스트 아님
            "https://notgo.kr.evil.com/x",        # 호스트는 evil.com
            "",
        ):
            self.assertFalse(linkcheck.is_kr_skip(url), url)


# ── 봇월 인터스티셜 감지(순수) ───────────────────────────────────────────────
class BotwallTest(unittest.TestCase):
    def test_interstitial_urls_detected(self):
        for u in (
            "https://www.fda.gov/apology_objects/abuse-detection?x=1",   # Akamai 실측 형태
            "https://x.test/cdn-cgi/challenge-platform/h/b",             # Cloudflare
            "https://x.test/_Incapsula_Resource?SWUDNSAI=1",            # Imperva
            "https://x.test/distil_r_captcha.html",                     # Distil
        ):
            self.assertTrue(linkcheck.looks_botwall(u), u)

    def test_normal_urls_not_flagged(self):
        for u in (
            "https://www.fda.gov/media/192438/download",
            "https://www.ema.europa.eu/en/documents/x.pdf",
            "",
        ):
            self.assertFalse(linkcheck.looks_botwall(u), u)


# ── check_url(네트워크 모킹) ─────────────────────────────────────────────────
class CheckUrlTest(unittest.TestCase):
    def _check(self, **kw):
        return linkcheck.check_url("https://x.test/a", FakeSession(**kw), retries=1)

    def test_head_200_ok(self):
        self.assertEqual(self._check(head=200), linkcheck.OK)

    def test_head_404_broken(self):
        self.assertEqual(self._check(head=404), linkcheck.BROKEN)

    def test_head_503_degraded(self):
        self.assertEqual(self._check(head=503), linkcheck.DEGRADED)

    def test_head_500_degraded(self):
        self.assertEqual(self._check(head=500), linkcheck.DEGRADED)

    def test_head_403_then_get_403_inconclusive(self):
        # 봇방어(WAF) 실측 형태: HEAD 403 → GET 폴백도 403 → inconclusive(보존).
        s = FakeSession(head=403, get=403)
        self.assertEqual(linkcheck.check_url("https://www.fda.gov/x", s), linkcheck.INCONCLUSIVE)
        self.assertEqual(s.head_calls, 1)
        self.assertEqual(s.get_calls, 1)

    def test_fda_apology_redirect_404_is_inconclusive(self):
        # 실측 형태: HEAD 가 404 를 주지만 최종 URL 이 Akamai apology 페이지 → 보존.
        s = FakeSession(head=404,
                        final_url="https://www.fda.gov/apology_objects/abuse-detection?x")
        self.assertEqual(
            linkcheck.check_url("https://www.fda.gov/media/192438/download", s),
            linkcheck.INCONCLUSIVE)

    def test_cross_host_error_redirect_is_inconclusive(self):
        # 다른 호스트로 리다이렉트된 에러 응답 → 봇월/차단 추정 → 보존.
        s = FakeSession(head=403, get=404, final_url="https://blockpage.cdn.example/denied")
        self.assertEqual(
            linkcheck.check_url("https://x.test/a", s), linkcheck.INCONCLUSIVE)

    def test_cross_host_ok_redirect_is_ok(self):
        # 정상 교차호스트 리다이렉트(2xx) → ok(끊지 않음).
        s = FakeSession(head=200, final_url="https://cdn.other.example/asset")
        self.assertEqual(linkcheck.check_url("https://x.test/a", s), linkcheck.OK)

    def test_head_405_falls_back_to_get(self):
        s = FakeSession(head=405, get=200)
        self.assertEqual(linkcheck.check_url("https://x.test/a", s), linkcheck.OK)
        self.assertEqual(s.head_calls, 1)
        self.assertEqual(s.get_calls, 1)

    def test_head_403_falls_back_to_get(self):
        s = FakeSession(head=403, get=200)
        self.assertEqual(linkcheck.check_url("https://x.test/a", s), linkcheck.OK)
        self.assertEqual(s.get_calls, 1)

    def test_timeout_degraded_and_retried(self):
        s = FakeSession(head=requests.exceptions.Timeout)
        self.assertEqual(
            linkcheck.check_url("https://x.test/a", s, retries=1), linkcheck.DEGRADED)
        self.assertEqual(s.head_calls, 2)        # 최초 + 재시도 1

    def test_connection_error_degraded_after_retries(self):
        # DNS·refused = 빌드 IP 의 일시 egress 차단 가능 → degraded 보존(false-broken 방지).
        s = FakeSession(head=requests.exceptions.ConnectionError)
        self.assertEqual(
            linkcheck.check_url("https://x.test/a", s, retries=2), linkcheck.DEGRADED)
        self.assertEqual(s.head_calls, 3)        # 최초 + 재시도 2

    def test_other_request_exception_broken_no_retry(self):
        # 비일시 요청예외(과다 리다이렉트 등) → broken, 재시도 안 함(break).
        s = FakeSession(head=requests.exceptions.TooManyRedirects)
        self.assertEqual(
            linkcheck.check_url("https://x.test/a", s, retries=2), linkcheck.BROKEN)
        self.assertEqual(s.head_calls, 1)

    def test_timeout_then_success(self):
        # 첫 시도 타임아웃 → 재시도 200 → ok.
        s = FakeSession(head=[requests.exceptions.Timeout, 200])
        self.assertEqual(
            linkcheck.check_url("https://x.test/a", s, retries=1), linkcheck.OK)
        self.assertEqual(s.head_calls, 2)


# ── make_checker(캐시·KR 스킵·백오프 주입) ───────────────────────────────────
class MakeCheckerTest(unittest.TestCase):
    def _no_sleep(self):
        self.slept = []
        return lambda s: self.slept.append(s)

    def test_kr_url_skips_network(self):
        s = FakeSession(head=500)                # 네트워크 타면 broken 나올 spec
        checker = linkcheck.make_checker(s, host_delay=0, sleeper=self._no_sleep())
        self.assertEqual(checker("https://nedrug.mfds.go.kr/x"), linkcheck.OK)
        self.assertEqual(s.head_calls, 0)        # 네트워크 미접촉

    def test_cache_one_check_per_url(self):
        s = FakeSession(head=200)
        checker = linkcheck.make_checker(s, host_delay=0, sleeper=self._no_sleep())
        self.assertEqual(checker("https://x.test/a"), linkcheck.OK)
        self.assertEqual(checker("https://x.test/a"), linkcheck.OK)
        self.assertEqual(s.head_calls, 1)        # 캐시 적중 → 1회만

    def test_non_kr_goes_to_network(self):
        s = FakeSession(head=404)
        checker = linkcheck.make_checker(s, host_delay=0, sleeper=self._no_sleep())
        self.assertEqual(checker("https://www.fda.gov/x"), linkcheck.BROKEN)
        self.assertEqual(s.head_calls, 1)

    def test_host_backoff_sleeps_between_same_host(self):
        s = FakeSession(head=200)
        sleeps: list[float] = []
        ticks = iter([0.0, 0.0, 0.05])           # 두 번째 요청 시각이 간격 미달
        checker = linkcheck.make_checker(
            s, host_delay=1.0, sleeper=sleeps.append, clock=lambda: next(ticks))
        checker("https://h.test/a")
        checker("https://h.test/b")              # 같은 호스트 → 백오프 sleep 발생
        self.assertTrue(sleeps and sleeps[0] > 0, "동일 호스트 백오프 미발생")


# ── enrich_brief(체커 주입·순수 로직) ────────────────────────────────────────
def _brief_with(sources: dict) -> dict:
    return {"cards": [{"id": "c0", "sources": sources}]}


class EnrichBriefTest(unittest.TestCase):
    def test_sets_states_from_checker(self):
        b = _brief_with({"info_url": "https://a.test/i", "official_url": "https://a.test/o",
                         "link_check": {"info": "pending", "official": "pending"}})
        mapping = {"https://a.test/i": linkcheck.OK, "https://a.test/o": linkcheck.BROKEN}
        tally = linkcheck.enrich_brief(b, lambda u: mapping[u])
        lc = b["cards"][0]["sources"]["link_check"]
        self.assertEqual(lc, {"info": "ok", "official": "broken"})
        self.assertEqual(tally["ok"], 1)
        self.assertEqual(tally["broken"], 1)

    def test_empty_url_preserved_not_checked(self):
        b = _brief_with({"info_url": "", "official_url": "https://a.test/o",
                         "link_check": {"info": "pending", "official": "pending"}})
        calls: list[str] = []

        def checker(u):
            calls.append(u)
            return linkcheck.OK

        linkcheck.enrich_brief(b, checker)
        lc = b["cards"][0]["sources"]["link_check"]
        self.assertEqual(lc["info"], "pending")      # 빈 URL → 상태 보존
        self.assertEqual(lc["official"], "ok")
        self.assertEqual(calls, ["https://a.test/o"])  # 빈 URL 은 체커 미호출

    def test_missing_link_check_created(self):
        b = _brief_with({"info_url": "https://a.test/i", "official_url": ""})
        linkcheck.enrich_brief(b, lambda u: linkcheck.DEGRADED)
        self.assertEqual(b["cards"][0]["sources"]["link_check"]["info"], "degraded")

    def test_inconclusive_preserves_prior_state(self):
        # 401/403(봇방어) → 기존 상태(pending) 보존, link_check 에 'inconclusive' 미기록.
        b = _brief_with({"info_url": "https://www.fda.gov/i", "official_url": "https://www.fda.gov/o",
                         "link_check": {"info": "pending", "official": "pending"}})
        tally = linkcheck.enrich_brief(b, lambda u: linkcheck.INCONCLUSIVE)
        lc = b["cards"][0]["sources"]["link_check"]
        self.assertEqual(lc, {"info": "pending", "official": "pending"})   # 보존
        self.assertNotIn("inconclusive", lc.values())
        self.assertEqual(tally[linkcheck.INCONCLUSIVE], 2)


# ── run(비파괴·원본 불변·비차단) ─────────────────────────────────────────────
class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_lc_"))
        self.data = self.tmp / "data"
        self.out = self.tmp / "out"
        self.data.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, obj):
        (self.data / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_non_destructive_enrich(self):
        original = _brief_with({"info_url": "https://a.test/i", "official_url": "https://a.test/o",
                                "link_check": {"info": "pending", "official": "pending"}})
        self._write("brief_web_2026_06_22.json", original)
        meta = linkcheck.run(self.data, self.out, checker=lambda u: linkcheck.OK)
        # 원본 불변(pending 유지).
        src_after = json.loads((self.data / "brief_web_2026_06_22.json").read_text("utf-8"))
        self.assertEqual(src_after["cards"][0]["sources"]["link_check"]["info"], "pending")
        # 사본은 enrich(ok).
        enriched = json.loads((self.out / "brief_web_2026_06_22.json").read_text("utf-8"))
        self.assertEqual(enriched["cards"][0]["sources"]["link_check"]["info"], "ok")
        self.assertEqual(meta["files"], 1)
        self.assertEqual(meta["totals"]["ok"], 2)

    def test_in_place_overwrites(self):
        self._write("b.json", _brief_with(
            {"info_url": "https://a.test/i", "official_url": "https://a.test/o",
             "link_check": {"info": "pending", "official": "pending"}}))
        linkcheck.run(self.data, None, in_place=True, checker=lambda u: linkcheck.BROKEN)
        src = json.loads((self.data / "b.json").read_text("utf-8"))
        self.assertEqual(src["cards"][0]["sources"]["link_check"]["official"], "broken")

    def test_bad_json_passed_through_non_blocking(self):
        (self.data / "good.json").write_text(
            json.dumps(_brief_with({"info_url": "https://a.test/i", "official_url": "",
                                    "link_check": {"info": "pending", "official": "pending"}}),
                       ensure_ascii=False), encoding="utf-8")
        (self.data / "bad.json").write_text("{ not json", encoding="utf-8")
        meta = linkcheck.run(self.data, self.out, checker=lambda u: linkcheck.OK)
        # 깨진 파일도 사본 생성(통과)·경고 기록 — 비차단.
        self.assertTrue((self.out / "bad.json").exists())
        self.assertEqual((self.out / "bad.json").read_text("utf-8"), "{ not json")
        self.assertTrue(any("bad.json" in w for w in meta["warnings"]))
        # 정상 파일은 enrich.
        good = json.loads((self.out / "good.json").read_text("utf-8"))
        self.assertEqual(good["cards"][0]["sources"]["link_check"]["info"], "ok")

    def test_out_dir_required_without_in_place(self):
        with self.assertRaises(ValueError):
            linkcheck.run(self.data, None, checker=lambda u: linkcheck.OK)


# ── CLI 비차단 ───────────────────────────────────────────────────────────────
class CliTest(unittest.TestCase):
    def test_missing_out_and_in_place_exit_zero(self):
        # 인자 오용(--out·--in-place 둘 다 없음)에도 비차단: exit 0.
        rc = linkcheck.main(["--data", str(WEB_DIR / "data" / "briefs")])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
