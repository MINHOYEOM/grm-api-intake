#!/usr/bin/env python3
"""영문 주간 메일(`web/newsletter_en.py`) 테스트 — 수집 창·집계·빌더·게이트(네트워크 0).

CI(`unittest discover -s tests`)는 `tests/test_web_newsletter_en.py` shim 으로 순회. 직접:
  python web/tests/test_newsletter_en.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import newsletter     # noqa: E402
import newsletter_en as ne  # noqa: E402

BASE = "https://grm.example"
PUB = "2026-10-12"
IN_WINDOW = "2026-10-06T20:21:42Z"      # 창 [10-04 15:00Z, 10-11 15:00Z) 안


def _row(doc, *, source="FDA 483", dtype="483", firm="Acme Pharma LLC", country="United States",
         pub="2026-10-06", ingested=IN_WINDOW, cat="설비/시설", i=0, scope="ok"):
    return {"document_id": doc, "finding_id": f"{doc}-{i}", "source": source, "document_type": dtype,
            "agency": "", "firm_name": firm, "site_country": country, "published_date": pub,
            "category_label_ko": cat, "ingested_at": ingested, "scope_status": scope}


def _snap(rows, pub=PUB):
    return {"schema": ne.SNAPSHOT_SCHEMA, "publish_date": pub, "window": ne.window_for(pub), "rows": rows}


def _english_brief(pub=PUB):
    """브리프 한 호를 실제 데이터에서 빌리되, 영문 판정이 갈리는 경우는 테스트가 직접 고친다."""
    p = WEB_DIR / "data" / "briefs" / f"brief_web_{pub.replace('-', '_')}.json"
    return json.loads(p.read_text(encoding="utf-8"))


class WindowTest(unittest.TestCase):
    def test_window_is_derived_from_publish_date_in_kst(self):
        w = ne.window_for(PUB)
        self.assertEqual(w["ingested_from"], "2026-10-04T15:00:00Z")   # 10-05 00:00 KST
        self.assertEqual(w["ingested_to"], "2026-10-11T15:00:00Z")     # 10-12 00:00 KST
        self.assertEqual(w["published_from"], "2026-09-14")

    def test_consecutive_issues_tile_without_gap_or_overlap(self):
        a, b = ne.window_for("2026-10-05"), ne.window_for("2026-10-12")
        self.assertEqual(a["ingested_to"], b["ingested_from"])

    def test_filter_drops_out_of_window_backfill_and_out_of_scope(self):
        rows = [
            _row("ok"),
            _row("early", ingested="2026-10-04T14:59:59Z"),
            _row("edge-end", ingested="2026-10-11T15:00:00Z"),          # 끝은 열린 구간
            _row("old-doc", pub="2025-01-01"),                            # 백필 — 옛 문서
            _row("scope", scope="out_of_scope"),
        ]
        kept = [r["document_id"] for r in ne.rows_in_window(rows, ne.window_for(PUB))]
        self.assertEqual(kept, ["ok"])


class AggregateTest(unittest.TestCase):
    def test_korean_firm_names_are_never_romanized(self):
        docs = ne.aggregate_documents([_row("m1", source="MFDS", dtype="recall-quality",
                                            firm="(주)한풍제약", country="")])
        self.assertEqual(docs[0].firm, "")
        sec = ne.build_sections(docs)
        self.assertEqual(sec[0].label, "MFDS (Korea)")
        self.assertEqual(sec[0].rows, [])
        self.assertEqual(sec[0].counts, [("quality recall", 1)])
        mail = ne.build_email(sec, publish_date=PUB, site_base_url=BASE)
        self.assertNotIn("한풍", mail["html"])
        self.assertIsNone(ne._CJK.search(mail["html"]), "영문 메일에 한글이 샜다")

    def test_korean_country_name_is_dropped(self):
        docs = ne.aggregate_documents([_row("x", country="프랑스")])
        self.assertEqual(docs[0].country, "")

    def test_unknown_category_label_is_omitted_not_shown_in_korean(self):
        docs = ne.aggregate_documents([_row("x", cat="사전에 없는 분류")])
        self.assertEqual(docs[0].categories, [])

    def test_same_firm_documents_collapse_to_one_line(self):
        rows = [_row(f"e{i}", source="EU GMP NCR (EudraGMDP)", dtype="gmp-non-compliance",
                     firm="Same Hospital", country="Netherlands", cat="데이터 완전성") for i in range(4)]
        sec = ne.build_sections(ne.aggregate_documents(rows))
        self.assertEqual(len(sec[0].rows), 1)
        line = sec[0].rows[0]
        self.assertEqual((line.docs, line.n_findings), (4, 4))
        self.assertEqual(sec[0].hidden, 0)
        html = ne.build_email(sec, publish_date=PUB, site_base_url=BASE)["html"]
        self.assertEqual(html.count("Same Hospital"), 1)
        self.assertIn("4 records", html)

    def test_section_cap_reports_the_rest(self):
        rows = [_row(f"w{i}", source="FDA Warning Letter", dtype="CDER", firm=f"Firm {i:02d}")
                for i in range(8)]
        sec = ne.build_sections(ne.aggregate_documents(rows), per_section=5)
        self.assertEqual((sec[0].total, len(sec[0].rows), sec[0].hidden), (8, 5, 3))
        self.assertIn("and 3 more", ne.build_email(sec, publish_date=PUB, site_base_url=BASE)["html"])

    def test_unknown_source_is_kept_not_dropped(self):
        sec = ne.build_sections(ne.aggregate_documents([_row("z", source="New Agency Feed")]))
        self.assertEqual([s.label for s in sec], ["New Agency Feed"])

    def test_heaviest_record_comes_first(self):
        rows = [_row("light", firm="Light Co")] + [_row("heavy", firm="Heavy Co", i=i) for i in range(3)]
        sec = ne.build_sections(ne.aggregate_documents(rows))
        self.assertEqual([r.firm for r in sec[0].rows], ["Heavy Co", "Light Co"])


class EmailAndGateTest(unittest.TestCase):
    def test_deterministic(self):
        snap = _snap([_row("a"), _row("b", firm="Beta Inc")])
        r1, m1 = ne.run_gates(snap, publish_date=PUB, site_base_url=BASE)
        r2, m2 = ne.run_gates(snap, publish_date=PUB, site_base_url=BASE)
        self.assertTrue(r1.ok, r1.text())
        self.assertEqual((m1["subject"], m1["html"]), (m2["subject"], m2["html"]))
        self.assertEqual(m1["subject"], "[GRM Weekly] 2 new inspection records · Oct 12, 2026")

    def test_links_only_our_english_pages(self):
        _r, mail = ne.run_gates(_snap([_row("a")]), publish_date=PUB, site_base_url=BASE,
                                brief_obj=_english_brief())
        self.assertEqual(newsletter.gate_provenance(mail, BASE), [])
        import re
        hrefs = re.findall(r'href="([^"]*)"', mail["html"])
        self.assertTrue(hrefs)
        self.assertTrue(all(h.startswith(f"{BASE}/en/") for h in hrefs), hrefs)
        self.assertIn('<html lang="en">', mail["html"])

    def test_no_source_text_or_source_url_in_mail(self):
        row = _row("a")
        row["finding_text"] = "SECRET ORIGINAL QUOTE"
        row["evidence_url"] = "https://www.fda.gov/x"
        _r, mail = ne.run_gates(_snap([row]), publish_date=PUB, site_base_url=BASE)
        self.assertNotIn("SECRET ORIGINAL QUOTE", mail["html"])
        self.assertNotIn("fda.gov", mail["html"])

    def test_empty_week_blocks_send(self):
        report, _m = ne.run_gates(_snap([]), publish_date=PUB, site_base_url=BASE)
        self.assertFalse(report.ok)
        self.assertTrue(any("0건" in r for r in report.reasons))

    def test_stale_or_mismatched_snapshot_blocks_send(self):
        snap = _snap([_row("a")])
        snap["window"] = ne.window_for("2026-10-05")
        self.assertFalse(ne.run_gates(snap, publish_date=PUB, site_base_url=BASE)[0].ok)
        self.assertFalse(ne.run_gates(_snap([_row("a")], pub="2026-10-05"),
                                      publish_date=PUB, site_base_url=BASE)[0].ok)

    def test_brief_summary_only_when_issue_is_fully_english(self):
        brief = _english_brief()
        _r, with_brief = ne.run_gates(_snap([_row("a")]), publish_date=PUB, site_base_url=BASE,
                                      brief_obj=brief)
        if ne.render.brief_has_english(brief):
            self.assertIn(f"{BASE}/en/briefs/{PUB}/", with_brief["html"])
            self.assertLessEqual(with_brief["html"].count("margin:4px 0 0\">· "), ne.BRIEF_TLDR_MAX)
        brief["brief"]["en"] = {}
        _r, without = ne.run_gates(_snap([_row("a")]), publish_date=PUB, site_base_url=BASE,
                                   brief_obj=brief)
        self.assertNotIn("/en/briefs/", without["html"])
        self.assertTrue(_r.ok)        # 요약이 없어도 지적 기록만으로 나간다

    def test_campaign_name_never_collides_with_korean(self):
        self.assertNotEqual(ne.campaign_name(PUB, 20), newsletter.idempotency_campaign_name(PUB, 20))

    def test_unsubscribe_snippet_is_english(self):
        _r, mail = ne.run_gates(_snap([_row("a")]), publish_date=PUB, site_base_url=BASE,
                                unsubscribe_html=ne.BREVO_UNSUBSCRIBE_HTML_EN)
        self.assertIn("Unsubscribe", mail["html"])
        self.assertNotIn("수신거부", mail["html"])


class EmptyListTest(unittest.TestCase):
    """Brevo 는 연락처 0명 리스트로 캠페인을 못 만든다(400 invalid_parameter, 2026-10-19 실측).
    영문 리스트가 비어 있는 동안 영문 잡은 실패하지 않고 건너뛴다."""

    class _Sender:
        def __init__(self, counts): self.counts, self.created = counts, []
        def list_contact_count(self, i): return self.counts.get(i, 0)
        def find_campaign(self, name): return None
        def create_campaign(self, **kw): self.created.append(kw); return "1"
        def send_campaign(self, cid): pass
        def send_test(self, cid, emails): pass

    def test_lists_have_contacts(self):
        self.assertFalse(ne.lists_have_contacts(self._Sender({4: 0}), [4]))
        self.assertTrue(ne.lists_have_contacts(self._Sender({4: 1}), [4]))

    def _run(self, mode, counts, tmp):
        import os
        from unittest import mock
        snap = tmp / "snap.json"
        snap.write_text(json.dumps(_snap([_row("a")])), encoding="utf-8")
        sender = self._Sender(counts)
        env = {"NEWSLETTER_API_KEY": "k", "GRM_NEWSLETTER_SENDER_EMAIL": "s@example.com",
               "GRM_NEWSLETTER_LIST_ID_EN": "4", "GRM_NEWSLETTER_TEST_EMAILS": "t@example.com"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(ne.newsletter, "BrevoSender", lambda key: sender):
            rc = ne.main(["--publish-date", PUB, "--mode", mode, "--snapshot", str(snap)])
        return rc, sender

    def test_empty_list_skips_send_and_test_without_failing(self):
        import tempfile
        tmp = pathlib.Path(tempfile.mkdtemp())
        for mode in ("send", "test", "precheck"):
            rc, sender = self._run(mode, {4: 0}, tmp)
            self.assertEqual(rc, 0, mode)
            self.assertEqual(sender.created, [], f"{mode}: 빈 리스트로 캠페인을 만들면 안 된다")
        rc, sender = self._run("send", {4: 2}, tmp)
        self.assertEqual((rc, len(sender.created)), (0, 1))


class FetchTest(unittest.TestCase):
    def test_fetch_is_read_only_and_bounded_by_window(self):
        calls = []

        class _Resp:
            def __init__(self, data): self._d = data
            def raise_for_status(self): pass
            def json(self): return self._d

        class _Sess:
            def get(self, url, headers=None, params=None, timeout=None):
                calls.append((url, params))
                return _Resp([_row("b", i=1), _row("a")])

        snap = ne.fetch_snapshot(PUB, supabase_url="https://db.example", service_key="k",
                                 session=_Sess(), page_size=1000)
        self.assertEqual(len(calls), 1)
        url, params = calls[0]
        self.assertTrue(url.endswith("/rest/v1/findings"))
        p = dict()
        for k, v in params:
            p.setdefault(k, []).append(v)
        w = ne.window_for(PUB)
        self.assertEqual(sorted(p["ingested_at"]), sorted([f"gte.{w['ingested_from']}", f"lt.{w['ingested_to']}"]))
        self.assertEqual(p["published_date"], [f"gte.{w['published_from']}"])
        self.assertEqual(p["scope_status"], ["eq.ok"])
        self.assertEqual([r["finding_id"] for r in snap["rows"]], ["a-0", "b-1"])   # 결정론 정렬
        self.assertEqual(snap["window"], w)


if __name__ == "__main__":
    unittest.main()
