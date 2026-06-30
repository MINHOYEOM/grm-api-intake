#!/usr/bin/env python3
"""뉴스레터(T1.3) 테스트 — 티저 빌더(결정론·무변형·provenance)·게이트·Brevo 어댑터(네트워크 0).

CI(`unittest discover -s tests`)는 `tests/test_web_newsletter.py` shim 으로 순회. 직접:
  python web/tests/test_newsletter.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent          # …/web
sys.path.insert(0, str(WEB_DIR))
import newsletter  # noqa: E402  (web/newsletter.py)
import linkcheck   # noqa: E402  (상태 상수)

TESTS_DIR = pathlib.Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data" / "briefs"
REAL_FIXTURE = DATA_DIR / "brief_web_2026_06_26.json"
BRIEF_TEMPLATE = WEB_DIR / "templates" / "brief.html"
BASE = "https://grm.example"

__all__ = [
    "NewsletterTeaserTest",
    "NewsletterGateTest",
    "NewsletterDisclosureDriftTest",
    "BrevoSenderTest",
    "NewsletterLoadTest",
]


def _real() -> dict:
    return json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))


def _minimal(pub: str = "2026-06-01", *, tldr=None, cards=None, ai=True) -> dict:
    if cards is None:
        cards = [{
            "id": "x1", "render_order": 0, "group": "글로벌", "group_label": None,
            "agency": "FDA", "card_type": "Recall", "category": "Other", "modality": None,
            "evidence_level": "A", "signal_tier": 3, "signal_label": "High", "type_tag": "Recall",
            "headline_target": "ACME", "title_issue": "", "summary": "",
            "facts": [{"label": "발행일", "value": pub}], "quotes": [],
            "evidence_basis": "Intake raw", "key_facts": [], "implication": "", "checks": [],
            "merged_count": 1, "merged_items": [],
            "sources": {"info_url": "https://example.org/i", "official_url": "https://example.org/o",
                        "official_is_pdf": False, "link_check": {"info": "pending", "official": "pending"}},
        }]
    return {
        "schema_version": "grm-web-card/v1",
        "brief": {"run_date_kst": pub, "window": f"{pub} ~ {pub}", "publish_date": pub,
                  "agencies": ["FDA"], "categories": ["Other"], "tldr": tldr or [],
                  "coverage": {"intake_total": 1, "rendered": 1, "evidence": {"A": 1, "B": 0, "C": 0}},
                  "ai_disclosure": ai},
        "cards": cards,
    }


# ── 티저 빌더(순수·결정론·무변형·provenance) ──────────────────────────────────
class NewsletterTeaserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = _real()
        cls.t = newsletter.build_teaser(cls.brief, site_base_url=BASE, issue_no=2)
        cls.html = cls.t["html"]

    def test_subject_date_issue_no_weekday(self):
        # 발행일+호수 기반(요일 없음 — web JSON 에 weekday 없음·산술 금지 클래스 차단).
        self.assertEqual(self.t["subject"],
                         "[GRM 주간 브리프] 2026년 6월 4주차 · 제2호 (2026-06-26 발행)")
        self.assertNotRegex(self.t["subject"], r"(월|화|수|목|금|토|일)요일")

    def test_title_is_tldr0(self):
        self.assertIn(self.brief["brief"]["tldr"][0], self.html)            # 제목 = tldr[0]

    def test_tldr_verbatim(self):
        for t in self.brief["brief"]["tldr"]:
            self.assertIn(t, self.html)                                    # 요약 verbatim

    def test_brief_cta_and_section_anchors(self):
        self.assertIn(f'href="{BASE}/briefs/2026-06-26/"', self.html)      # 전체 보기
        # 섹션 앵커(관심 주제 클릭 신호) — 06-26 = 글로벌/국내/Recall, #sec-{그룹}(percent-encode).
        self.assertIn(f'{BASE}/briefs/2026-06-26/#sec-Recall', self.html)
        from urllib.parse import quote
        for g in ("글로벌", "국내"):
            self.assertIn(f'#sec-{quote(g, safe="")}', self.html)
        self.assertEqual(self.t["section_count"], 3)

    def test_no_card_source_urls_in_mail(self):
        # 무변형/provenance — 카드 출처 URL(보호 대상)은 메일에 들어가지 않는다(우리 페이지만).
        for c in self.brief["cards"]:
            s = c.get("sources") or {}
            for u in (s.get("info_url"), s.get("official_url")):
                if u:
                    self.assertNotIn(u, self.html, f"카드 출처 URL 누출: {u}")

    def test_no_tracking_query_on_our_links(self):
        for h in re.findall(r'href="([^"]*)"', self.html):
            self.assertNotIn("?", h, f"추적/쿼리 파라미터 부착: {h}")

    def test_disclaimer_present_ko_en(self):
        self.assertIn("AI 자동 생성 안내", self.html)
        self.assertIn(newsletter.DISCLOSURE_EN, self.html)
        self.assertIn("원문을 확인하십시오", self.html)

    def test_deterministic(self):
        a = newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2)
        b = newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2)
        self.assertEqual(a["html"], b["html"])
        self.assertEqual(a["subject"], b["subject"])

    def test_section_groups_distinct_render_order(self):
        self.assertEqual(newsletter.section_groups(self.brief), ["글로벌", "국내", "Recall"])

    def test_unsubscribe_injected_only_when_passed(self):
        no_unsub = newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2)
        with_unsub = newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2,
                                             unsubscribe_html=newsletter.BREVO_UNSUBSCRIBE_HTML)
        self.assertNotIn("{{ unsubscribe }}", no_unsub["html"])
        self.assertIn("{{ unsubscribe }}", with_unsub["html"])


# ── 발송 게이트 ────────────────────────────────────────────────────────────────
class NewsletterGateTest(unittest.TestCase):
    def test_publishable_ok(self):
        self.assertEqual(newsletter.gate_publishable(_minimal("2026-06-01"), "2026-06-01"), [])

    def test_publishable_rejects(self):
        self.assertTrue(newsletter.gate_publishable(_minimal("2026-06-01"), "2026-06-02"))  # 날짜 불일치
        bad_schema = _minimal("2026-06-01"); bad_schema["schema_version"] = "x"
        self.assertTrue(newsletter.gate_publishable(bad_schema, "2026-06-01"))
        no_disc = _minimal("2026-06-01", ai=False)
        self.assertTrue(newsletter.gate_publishable(no_disc, "2026-06-01"))                 # 면책 누락
        empty = _minimal("2026-06-01", cards=[])
        self.assertTrue(newsletter.gate_publishable(empty, "2026-06-01"))                   # 빈 호

    def test_provenance_clean_vs_dirty(self):
        t = newsletter.build_teaser(_minimal("2026-06-01", tldr=["요약"]), site_base_url=BASE, issue_no=1)
        self.assertEqual(newsletter.gate_provenance(t, BASE), [])
        dirty = {"html": f'<a href="{BASE}/briefs/x/?utm_source=a">x</a>'
                         '<a href="https://evil.example/track">y</a>'}
        fails = newsletter.gate_provenance(dirty, BASE)
        self.assertEqual(len(fails), 2)                                    # 쿼리 1 + 외부 호스트 1

    def test_linkcheck_gate_broken_holds(self):
        brief = _minimal("2026-06-01")
        fails_ok, tally_ok = newsletter.gate_linkcheck(brief, checker=lambda u: linkcheck.OK)
        self.assertEqual(fails_ok, [])
        fails_bad, tally_bad = newsletter.gate_linkcheck(brief, checker=lambda u: linkcheck.BROKEN)
        self.assertTrue(fails_bad)
        self.assertGreaterEqual(tally_bad.get(linkcheck.BROKEN, 0), 1)

    def test_linkcheck_gate_does_not_mutate_input(self):
        brief = _minimal("2026-06-01")
        before = json.dumps(brief, sort_keys=True)
        newsletter.gate_linkcheck(brief, checker=lambda u: linkcheck.BROKEN)
        self.assertEqual(json.dumps(brief, sort_keys=True), before)        # deepcopy 보장

    def test_run_gates_integration(self):
        brief = _minimal("2026-06-01", tldr=["요약 한 줄"])
        report, teaser = newsletter.run_gates(brief, expected_date="2026-06-01",
                                              site_base_url=BASE, issue_no=1,
                                              checker=lambda u: linkcheck.OK)
        self.assertTrue(report.ok)
        report2, _ = newsletter.run_gates(brief, expected_date="2026-06-09",  # 날짜 불일치 → FAIL
                                          site_base_url=BASE, issue_no=1,
                                          checker=lambda u: linkcheck.OK)
        self.assertFalse(report2.ok)

    def test_idempotency_name_deterministic(self):
        self.assertEqual(newsletter.idempotency_campaign_name("2026-06-26", 2),
                         "GRM Weekly Brief — 2026-06-26 (No.2)")


# ── 면책 캐논 drift 가드(메일 = brief.html 동일 문안) ──────────────────────────
class NewsletterDisclosureDriftTest(unittest.TestCase):
    def test_canon_matches_brief_template(self):
        tpl = BRIEF_TEMPLATE.read_text(encoding="utf-8")
        norm = re.sub(r"\s+", " ", tpl)
        self.assertIn(re.sub(r"\s+", " ", newsletter.DISCLOSURE_KO), norm,
                      "메일 면책 KO 문안이 brief.html 과 드리프트")
        self.assertIn(newsletter.DISCLOSURE_EN, norm,
                      "메일 면책 EN 문안이 brief.html 과 드리프트")


# ── Brevo 어댑터(FakeSession — 네트워크 0, 엔드포인트·페이로드 단언) ────────────
class _FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status = data, status

    def raise_for_status(self):
        if self.status >= 400:
            raise AssertionError(f"HTTP {self.status}")

    def json(self):
        return self._d


class _FakeSession:
    def __init__(self, campaigns=None, create_id="cid-99"):
        self.headers: dict = {}
        self.calls: list = []
        self._campaigns = campaigns or []
        self._create_id = create_id

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return _FakeResp({"campaigns": self._campaigns})

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        if url.endswith("/emailCampaigns"):
            return _FakeResp({"id": self._create_id})
        return _FakeResp({}, status=204)


class BrevoSenderTest(unittest.TestCase):
    def _sender(self, **kw):
        sess = _FakeSession(**kw)
        return newsletter.BrevoSender("key-123", session=sess), sess

    def test_requires_api_key(self):
        with self.assertRaises(ValueError):
            newsletter.BrevoSender("", session=_FakeSession())

    def test_auth_header_set(self):
        _, sess = self._sender()
        self.assertEqual(sess.headers.get("api-key"), "key-123")

    def test_find_campaign_match_and_miss(self):
        s, _ = self._sender(campaigns=[{"id": 7, "name": "GRM Weekly Brief — 2026-06-26 (No.2)"}])
        self.assertEqual(s.find_campaign("GRM Weekly Brief — 2026-06-26 (No.2)"), "7")
        s2, _ = self._sender(campaigns=[{"id": 7, "name": "other"}])
        self.assertIsNone(s2.find_campaign("GRM Weekly Brief — 2026-06-26 (No.2)"))

    def test_create_campaign_payload(self):
        s, sess = self._sender()
        cid = s.create_campaign(name="N", subject="S", html="<b>h</b>", list_ids=[3],
                                sender_name="GRM", sender_email="brief@grm.example")
        self.assertEqual(cid, "cid-99")
        post = [c for c in sess.calls if c[0] == "POST"][0]
        self.assertTrue(post[1].endswith("/emailCampaigns"))
        body = json.loads(post[2])
        self.assertEqual(body["name"], "N")
        self.assertEqual(body["subject"], "S")
        self.assertEqual(body["htmlContent"], "<b>h</b>")
        self.assertEqual(body["type"], "classic")
        self.assertEqual(body["recipients"], {"listIds": [3]})
        self.assertEqual(body["sender"], {"name": "GRM", "email": "brief@grm.example"})

    def test_send_now_and_test_endpoints(self):
        s, sess = self._sender()
        s.send_campaign("42")
        s.send_test("42", ["a@x.com", "b@x.com"])
        urls = [c[1] for c in sess.calls if c[0] == "POST"]
        self.assertTrue(any(u.endswith("/emailCampaigns/42/sendNow") for u in urls))
        test_call = [c for c in sess.calls if c[1].endswith("/emailCampaigns/42/sendTest")][0]
        self.assertEqual(json.loads(test_call[2]), {"emailTo": ["a@x.com", "b@x.com"]})


# ── 호 로딩 + issue 번호(render 파생 재사용) ──────────────────────────────────
class NewsletterLoadTest(unittest.TestCase):
    def test_load_issue_assigns_render_issue_no(self):
        brief, issue_no = newsletter.load_issue(DATA_DIR, "2026-06-26")
        self.assertEqual(brief["brief"]["publish_date"], "2026-06-26")
        self.assertEqual(issue_no, 2)                          # 06-22=1 · 06-26=2(date 오름차순)

    def test_load_issue_missing_raises(self):
        with self.assertRaises(SystemExit):
            newsletter.load_issue(DATA_DIR, "1999-01-01")


if __name__ == "__main__":
    unittest.main()
