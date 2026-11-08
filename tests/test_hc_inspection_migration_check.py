# -*- coding: utf-8 -*-
"""hc_inspection_migration_check — 원천 이전 판정 규칙을 잠근다(네트워크 0).

2026-11-08 실측 상태(옛 서버 연결 끊김 · 새 도메인은 DSO 만 링크 · /gmp/ 404 · 중간 인증서
미전송)를 대역으로 재현하고, GMP 가 새 도메인에 생기는 두 신호(링크 · 200 경로)를 각각
켰을 때 판정이 바뀌는지 본다.
"""
import os
import sys
import unittest

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hc_inspection_migration_check as mod

NEW = mod.NEW_HOST
DHPID_PAGE = mod.LINK_PAGES[1]

# 새 앱 메인(→ /dso/en/search) — 실측: 자기 DB(dso) 링크만 있다.
DSO_HTML = (
    '<a href="https://drug-inspection-results.canada.ca/dso/en/result">x</a>'
    '<a href="https://drug-inspection-results.canada.ca/dso/fr/search">fr</a>'
)
# canada.ca 안내 페이지 — 실측: GMP 링크는 아직 옛 호스트, 다른 DB 는 새 도메인.
DHPID_HTML = (
    '<a href="https://www.drug-inspections.canada.ca/gmp/index-en.html">GMP</a>'
    '<a href="https://drug-inspection-results.canada.ca/dso/en/search">DSO</a>'
    '<a href="/gmp/whatever.html">canada.ca 자기 경로</a>'
)


class _Resp:
    def __init__(self, status, text="", content=b""):
        self.status_code = status
        self.text = text
        self.content = content


class _Fake:
    """url → _Resp 또는 예외. 기본: 현 실측 상태."""

    def __init__(self, **over):
        self.routes = {
            mod.OLD_HOST_PAGE: requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING"),
            f"{NEW}/robots.txt": _Resp(200, "User-agent: *"),
            mod.NEW_GMP_CANDIDATES[0]: _Resp(404),
            mod.NEW_GMP_CANDIDATES[1]: _Resp(404),
            f"{NEW}/": _Resp(200, DSO_HTML),
            DHPID_PAGE: _Resp(200, DHPID_HTML),
        }
        self.routes.update(over)
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


class TestVerdict(unittest.TestCase):
    def test_current_state_is_migrating(self):
        r = mod.check(http_get=_Fake())
        self.assertEqual(r["verdict"], mod.VERDICT_MIGRATING)
        self.assertFalse(r["old_host_reachable"])
        self.assertIn("SSLError", r["old_host_error"])
        self.assertEqual(r["gmp_evidence"], [])

    def test_gmp_link_on_new_app_page_is_detected(self):
        html = DSO_HTML + '<a href="/gmp/en/search">GMP</a>'
        r = mod.check(http_get=_Fake(**{f"{NEW}/": _Resp(200, html)}))
        self.assertEqual(r["verdict"], mod.VERDICT_GMP_MIGRATED)

    def test_dhpid_page_pointing_gmp_to_new_domain_is_detected(self):
        html = DHPID_HTML.replace(
            "https://www.drug-inspections.canada.ca/gmp/index-en.html",
            "https://drug-inspection-results.canada.ca/gmp/en/search")
        r = mod.check(http_get=_Fake(**{DHPID_PAGE: _Resp(200, html)}))
        self.assertEqual(r["verdict"], mod.VERDICT_GMP_MIGRATED)

    def test_gmp_path_200_is_detected(self):
        r = mod.check(http_get=_Fake(**{mod.NEW_GMP_CANDIDATES[0]: _Resp(200, "ok")}))
        self.assertEqual(r["verdict"], mod.VERDICT_GMP_MIGRATED)
        self.assertIn(f"200 {mod.NEW_GMP_CANDIDATES[0]}", r["gmp_evidence"])

    def test_canada_ca_relative_gmp_path_is_not_evidence(self):
        # 안내 페이지의 상대 `/gmp/` 는 canada.ca 자기 경로다 — 세면 오탐.
        self.assertEqual(mod.gmp_links_in(DHPID_HTML, DHPID_PAGE), [])

    def test_old_host_back_is_old_host_ok(self):
        r = mod.check(http_get=_Fake(**{mod.OLD_HOST_PAGE: _Resp(200, "<html>")}))
        self.assertEqual(r["verdict"], mod.VERDICT_OLD_HOST_OK)

    def test_gmp_evidence_wins_over_live_old_host(self):
        r = mod.check(http_get=_Fake(**{
            mod.OLD_HOST_PAGE: _Resp(200, "<html>"),
            mod.NEW_GMP_CANDIDATES[0]: _Resp(200, "ok"),
        }))
        self.assertEqual(r["verdict"], mod.VERDICT_GMP_MIGRATED)

    def test_everything_down_is_unknown(self):
        down = requests.exceptions.ConnectionError("down")
        r = mod.check(http_get=_Fake(**{
            f"{NEW}/robots.txt": down, mod.NEW_GMP_CANDIDATES[0]: down,
            mod.NEW_GMP_CANDIDATES[1]: down, f"{NEW}/": down,
            DHPID_PAGE: _Resp(200, DHPID_HTML),
        }))
        self.assertEqual(r["verdict"], mod.VERDICT_UNKNOWN)


class TestIncompleteChain(unittest.TestCase):
    def test_cert_failure_builds_bundle_and_keeps_verification_on(self):
        der = b"\x30\x82\x00\x01fake-der"
        fake = _Fake(**{
            f"{NEW}/robots.txt": requests.exceptions.SSLError(
                "SSLCertVerificationError CERTIFICATE_VERIFY_FAILED"),
            mod.NEW_HOST_INTERMEDIATE_URL: _Resp(200, content=der),
        })
        r = mod.check(http_get=fake)
        self.assertTrue(r["new_host_incomplete_chain"])
        self.assertEqual(r["verdict"], mod.VERDICT_MIGRATING)
        new_calls = [kw for url, kw in fake.calls
                     if url.startswith(NEW) and not url.endswith("robots.txt")]
        self.assertTrue(new_calls)
        for kw in new_calls:
            # ★verify=False 로 우회하지 않는다 — 묶음 경로(문자열)로 검증을 유지한다.
            self.assertIsInstance(kw["verify"], str)
            with open(kw["verify"], encoding="utf-8") as fh:
                self.assertIn("-----BEGIN CERTIFICATE-----", fh.read())

    def test_no_request_ever_disables_verification(self):
        fake = _Fake()
        mod.check(http_get=fake)
        self.assertTrue(all(kw.get("verify", True) is not False for _, kw in fake.calls))


if __name__ == "__main__":
    unittest.main()
