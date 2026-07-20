#!/usr/bin/env python3
"""서비스 업데이트 안내(announce) 테스트 — 빌더(결정론·provenance)·게이트·주간 삽입(네트워크 0).

CI(`unittest discover -s tests`)는 `tests/test_web_announce.py` shim 으로 순회(TestCase 전수
자동 재-export). 직접:
  python web/tests/test_announce.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent          # …/web
sys.path.insert(0, str(WEB_DIR))
import announce     # noqa: E402  (web/announce.py)
import linkcheck    # noqa: E402  (상태 상수)
import newsletter   # noqa: E402

BASE = "https://grm.example"
REAL_DIR = WEB_DIR / "data" / "announcements"
BRIEF_FIXTURE = WEB_DIR / "data" / "briefs" / "brief_web_2026_06_26.json"


def _ann(**over) -> dict:
    """최소 유효 공지. 개별 필드만 갈아끼워 실패 케이스를 만든다."""
    base = {
        "schema_version": "grm-announce/v1",
        "id": "2026-07-sample",
        "date": "2026-07-20",
        "title": "새 기능이 생겼습니다",
        "lede": "도입 문장.",
        "weekly_publish_date": None,
        "items": [{"label": "자료실", "text": "설명 한 줄.", "path": "/library/"}],
        "cta": {"text": "둘러보기", "path": "/guide/"},
    }
    base.update(over)
    return base


def _all_ok(url: str) -> str:
    return linkcheck.OK


# ── 스키마 게이트 ─────────────────────────────────────────────────────────────
class AnnounceSchemaGateTest(unittest.TestCase):
    def test_valid_passes(self):
        self.assertEqual(announce.gate_schema(_ann()), [])

    def test_cta_optional(self):
        self.assertEqual(announce.gate_schema(_ann(cta=None)), [])

    def test_rejects_wrong_schema_version(self):
        fails = announce.gate_schema(_ann(schema_version="grm-announce/v0"))
        self.assertTrue(any("schema_version" in f for f in fails))

    def test_rejects_bad_id(self):
        for bad in ("Uppercase", "no", "under_score", "", "x" * 80):
            with self.subTest(bad=bad):
                self.assertTrue(any("id 형식" in f for f in announce.gate_schema(_ann(id=bad))))

    def test_rejects_bad_dates(self):
        self.assertTrue(any("date 형식" in f for f in announce.gate_schema(_ann(date="2026/07/20"))))
        self.assertTrue(any("weekly_publish_date" in f
                            for f in announce.gate_schema(_ann(weekly_publish_date="next week"))))

    def test_weekly_publish_date_null_is_allowed(self):
        self.assertEqual(announce.gate_schema(_ann(weekly_publish_date=None)), [])

    def test_rejects_empty_items(self):
        self.assertTrue(any("items 0건" in f for f in announce.gate_schema(_ann(items=[]))))

    def test_rejects_too_many_items(self):
        many = [{"label": f"l{i}", "text": "t", "path": "/library/"}
                for i in range(announce.MAX_ITEMS + 1)]
        self.assertTrue(any("상한" in f for f in announce.gate_schema(_ann(items=many))))

    def test_rejects_absolute_or_dirty_paths(self):
        """외부 호스트·추적 파라미터·앵커를 경로 단계에서 이미 표현 불가로 막는다."""
        for bad in ("https://evil.example/x", "library/", "/library/?utm_source=mail",
                    "/library/#x", "//evil.example", ""):
            with self.subTest(bad=bad):
                fails = announce.gate_schema(_ann(items=[{"label": "l", "text": "t", "path": bad}]))
                self.assertTrue(any("path 형식" in f for f in fails), bad)

    def test_rejects_blank_label_or_text(self):
        fails = announce.gate_schema(_ann(items=[{"label": "  ", "text": "", "path": "/library/"}]))
        self.assertTrue(any("label" in f for f in fails))
        self.assertTrue(any("text" in f for f in fails))


# ── 공지 메일 빌더 ────────────────────────────────────────────────────────────
class AnnounceMailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ann = _ann(items=[{"label": "자료실", "text": "설명 A", "path": "/library/"},
                              {"label": "퀴즈", "text": "설명 B", "path": "/quiz/"}])
        cls.mail = announce.build_announcement(cls.ann, site_base_url=BASE)
        cls.html = cls.mail["html"]

    def test_subject_prefix_distinguishes_from_weekly(self):
        self.assertEqual(self.mail["subject"], "[GRM 안내] 새 기능이 생겼습니다")
        self.assertNotIn("[GRM 규제뉴스]", self.mail["subject"])

    def test_items_and_cta_rendered_as_absolute_urls(self):
        for path in ("/library/", "/quiz/", "/guide/"):
            self.assertIn(f'href="{BASE}{path}"', self.html)
        self.assertIn("설명 A", self.html)
        self.assertIn("둘러보기", self.html)

    def test_lede_rendered_and_optional(self):
        self.assertIn("도입 문장.", self.html)
        self.assertNotIn("도입 문장.", announce.build_announcement(
            _ann(lede=""), site_base_url=BASE)["html"])

    def test_no_ai_disclosure_on_human_written_notice(self):
        """AI 생성 면책은 규제 다이제스트 내용에 대한 고지다. 사람이 쓴 서비스 안내에
        붙이면 거짓 고지가 된다 — 대신 발신 근거를 밝힌다."""
        self.assertNotIn(newsletter.DISCLOSURE_KO, self.html)
        self.assertNotIn(newsletter.DISCLOSURE_EN, self.html)
        self.assertNotIn("AI 자동 생성", self.html)
        self.assertIn(announce.FOOTER_NOTICE_KO, self.html)

    def test_weekly_teaser_still_carries_ai_disclosure(self):
        """반대로 주간호에서는 면책이 그대로 살아 있어야 한다(회귀 가드)."""
        brief = json.loads(BRIEF_FIXTURE.read_text(encoding="utf-8"))
        html = newsletter.build_teaser(brief, site_base_url=BASE, issue_no=2)["html"]
        self.assertIn(newsletter.DISCLOSURE_KO, html)

    def test_deterministic(self):
        again = announce.build_announcement(self.ann, site_base_url=BASE)
        self.assertEqual(again["html"], self.html)
        self.assertEqual(again["subject"], self.mail["subject"])

    def test_html_escaped(self):
        mail = announce.build_announcement(
            _ann(title="a<b>&", items=[{"label": "<x>", "text": "t&t", "path": "/library/"}]),
            site_base_url=BASE)
        self.assertIn("a&lt;b&gt;&amp;", mail["html"])
        self.assertIn("&lt;x&gt;", mail["html"])
        self.assertNotIn("<b>", mail["html"])

    def test_unsubscribe_injected_only_when_passed(self):
        self.assertNotIn("수신거부", self.html)
        with_unsub = announce.build_announcement(
            self.ann, site_base_url=BASE,
            unsubscribe_html=newsletter.BREVO_UNSUBSCRIBE_HTML)["html"]
        self.assertIn("수신거부", with_unsub)

    def test_passes_newsletter_provenance_gate(self):
        """뉴스레터 provenance 게이트(우리 호스트 외 링크·쿼리 0)를 그대로 통과해야 한다."""
        self.assertEqual(newsletter.gate_provenance(self.mail, BASE), [])

    def test_no_tracking_query_on_any_link(self):
        for href in re.findall(r'href="([^"]*)"', self.html):
            self.assertNotIn("?", href)


# ── 링크 실존 게이트 ──────────────────────────────────────────────────────────
class AnnounceLinkGateTest(unittest.TestCase):
    def test_all_paths_dedup_in_order(self):
        ann = _ann(items=[{"label": "a", "text": "t", "path": "/library/"},
                          {"label": "b", "text": "t", "path": "/quiz/"},
                          {"label": "c", "text": "t", "path": "/library/"}],
                   cta={"text": "go", "path": "/quiz/"})
        self.assertEqual(announce.all_paths(ann), ["/library/", "/quiz/"])

    def test_broken_holds_send(self):
        fails, statuses = announce.gate_links(
            _ann(), site_base_url=BASE, checker=lambda u: linkcheck.BROKEN)
        self.assertTrue(fails)
        self.assertTrue(all(v == linkcheck.BROKEN for v in statuses.values()))

    def test_ok_passes(self):
        fails, statuses = announce.gate_links(_ann(), site_base_url=BASE, checker=_all_ok)
        self.assertEqual(fails, [])
        self.assertEqual(set(statuses), {f"{BASE}/library/", f"{BASE}/guide/"})

    def test_degraded_is_not_blocking(self):
        fails, _ = announce.gate_links(_ann(), site_base_url=BASE,
                                       checker=lambda u: linkcheck.DEGRADED)
        self.assertEqual(fails, [])

    def test_run_gates_integration(self):
        report, mail = announce.run_gates(_ann(), site_base_url=BASE, checker=_all_ok)
        self.assertTrue(report.ok, report.text())
        self.assertIn("[PASS]", report.text())
        self.assertIn("새 기능이 생겼습니다", mail["subject"])

    def test_report_is_labelled_and_reports_paths(self):
        """리포트가 '뉴스레터'로 보이면 안 되고, 링크 상태는 경로로 읽혀야 한다."""
        ann = _ann(items=[{"label": "홈", "text": "t", "path": "/"}], cta=None)
        report, _ = announce.run_gates(ann, site_base_url=BASE, checker=_all_ok)
        text = report.text()
        self.assertIn("공지 발송 게이트", text)
        self.assertNotIn("뉴스레터 발송 게이트", text)
        self.assertIn("/=ok", text)
        self.assertNotIn("grm.example=", text)

    def test_newsletter_report_keeps_its_default_label(self):
        self.assertIn("뉴스레터 발송 게이트", newsletter.GateReport(ok=True).text())

    def test_run_gates_skips_network_when_schema_broken(self):
        """스키마가 깨졌으면 링크 조회를 시도조차 하지 않는다(checker 호출 0)."""
        calls: list[str] = []

        def spy(url: str) -> str:
            calls.append(url)
            return linkcheck.OK

        report, _ = announce.run_gates(_ann(id="BAD"), site_base_url=BASE, checker=spy)
        self.assertFalse(report.ok)
        self.assertEqual(calls, [])

    def test_run_gates_no_linkcheck_flag(self):
        report, _ = announce.run_gates(_ann(), site_base_url=BASE, run_linkcheck=False)
        self.assertTrue(report.ok)
        self.assertIn("건너뜀", report.text())


# ── 로드·주간 선택 ────────────────────────────────────────────────────────────
class AnnounceLoadTest(unittest.TestCase):
    def _dir(self, *anns) -> pathlib.Path:
        import tempfile
        d = pathlib.Path(tempfile.mkdtemp())
        for a in anns:
            (d / f"{a['id']}.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
        return d

    def test_load_all_empty_when_dir_missing(self):
        self.assertEqual(announce.load_all(pathlib.Path("/nope/never/here")), [])

    def test_load_announcement_roundtrip(self):
        d = self._dir(_ann())
        self.assertEqual(announce.load_announcement(d, "2026-07-sample")["title"],
                         "새 기능이 생겼습니다")

    def test_load_rejects_filename_id_mismatch(self):
        d = self._dir(_ann())
        (d / "other.json").write_text(json.dumps(_ann()), encoding="utf-8")
        with self.assertRaises(SystemExit):
            announce.load_announcement(d, "other")

    def test_load_missing_raises(self):
        with self.assertRaises(SystemExit):
            announce.load_announcement(self._dir(), "nope")

    def test_find_for_weekly_match_and_miss(self):
        d = self._dir(_ann(id="a-one", weekly_publish_date="2026-07-27"),
                      _ann(id="b-two", weekly_publish_date=None))
        self.assertEqual(announce.find_for_weekly(d, "2026-07-27")["id"], "a-one")
        self.assertIsNone(announce.find_for_weekly(d, "2026-08-03"))

    def test_find_for_weekly_rejects_ambiguity(self):
        """한 호에 2건 이상이면 조용히 하나를 고르지 않고 실패한다."""
        d = self._dir(_ann(id="a-one", weekly_publish_date="2026-07-27"),
                      _ann(id="b-two", weekly_publish_date="2026-07-27"))
        with self.assertRaises(SystemExit):
            announce.find_for_weekly(d, "2026-07-27")

    def test_idempotency_name_namespaced_and_deterministic(self):
        name = announce.idempotency_campaign_name("2026-07-new-features")
        self.assertEqual(name, "GRM Update — 2026-07-new-features")
        self.assertEqual(name, announce.idempotency_campaign_name("2026-07-new-features"))
        self.assertNotIn("GRM Weekly Brief", name)


# ── 실제 저장된 공지 데이터(라이브 정본) ──────────────────────────────────────
class AnnounceRealDataTest(unittest.TestCase):
    """`web/data/announcements/*.json` 전건이 게이트를 통과해야 한다 — 사람이 손으로 쓰는
    파일이므로 오탈자·형식 이탈을 커밋 시점에 잡는다(발송 시점이 아니라)."""

    def test_every_stored_announcement_is_valid(self):
        anns = announce.load_all(REAL_DIR)
        self.assertTrue(anns, "저장된 공지 0건 — 픽스처 경로 확인")
        for a in anns:
            with self.subTest(id=a.get("id")):
                self.assertEqual(announce.gate_schema(a), [])

    def test_filenames_match_ids(self):
        for p in sorted(REAL_DIR.glob("*.json")):
            with self.subTest(file=p.name):
                self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["id"], p.stem)

    def test_stored_announcements_pass_provenance(self):
        for a in announce.load_all(REAL_DIR):
            with self.subTest(id=a.get("id")):
                mail = announce.build_announcement(a, site_base_url=BASE)
                self.assertEqual(newsletter.gate_provenance(mail, BASE), [])

    def test_weekly_slots_are_unique(self):
        """한 발행일에 얹을 공지가 둘 이상 커밋되는 일을 미리 막는다."""
        slots = [a.get("weekly_publish_date") for a in announce.load_all(REAL_DIR)
                 if a.get("weekly_publish_date")]
        self.assertEqual(len(slots), len(set(slots)), f"발행일 중복: {slots}")


# ── 주간 티저 삽입(2단 구성의 기본선) ─────────────────────────────────────────
class WeeklyUpdatesBlockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief = json.loads(BRIEF_FIXTURE.read_text(encoding="utf-8"))
        cls.ann = _ann(items=[{"label": "자료실", "text": "설명 A", "path": "/library/"},
                              {"label": "퀴즈", "text": "설명 B", "path": "/quiz/"}])

    def _teaser(self, **kw) -> str:
        return newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2, **kw)["html"]

    def test_absent_block_leaves_teaser_byte_identical(self):
        """공지 없는 주는 기존 메일과 **바이트 동일**해야 한다(회귀 0)."""
        self.assertEqual(self._teaser(), self._teaser(updates_html=""))

    def test_block_rendered_when_present(self):
        block = announce.render_weekly_block(self.ann, site_base_url=BASE)
        html = self._teaser(updates_html=block)
        self.assertIn("서비스 소식", html)
        self.assertIn(f'href="{BASE}/library/"', html)
        self.assertIn("설명 B", html)

    def test_block_sits_before_disclaimer(self):
        """규제 소식이 주인공 — 공지 블록은 본문 뒤·면책 앞."""
        block = announce.render_weekly_block(self.ann, site_base_url=BASE)
        html = self._teaser(updates_html=block)
        self.assertLess(html.index("서비스 소식"), html.index(newsletter.DISCLOSURE_KO))
        self.assertLess(html.index("이번 주 소식 전체 보기"), html.index("서비스 소식"))

    def test_block_is_deterministic_and_escaped(self):
        a = announce.render_weekly_block(self.ann, site_base_url=BASE)
        self.assertEqual(a, announce.render_weekly_block(self.ann, site_base_url=BASE))
        risky = announce.render_weekly_block(
            _ann(title="<b>x</b>", items=[{"label": "&", "text": "<i>", "path": "/library/"}]),
            site_base_url=BASE)
        self.assertNotIn("<b>x</b>", risky)
        self.assertIn("&amp;", risky)

    def test_teaser_with_block_still_passes_provenance(self):
        """공지 블록이 외부 호스트·추적 파라미터를 들여오면 주간 발송이 보류돼야 한다."""
        block = announce.render_weekly_block(self.ann, site_base_url=BASE)
        teaser = newsletter.build_teaser(self.brief, site_base_url=BASE, issue_no=2,
                                         updates_html=block)
        self.assertEqual(newsletter.gate_provenance(teaser, BASE), [])
        dirty = newsletter.build_teaser(
            self.brief, site_base_url=BASE, issue_no=2,
            updates_html='<a href="https://evil.example/x?utm=1">x</a>')
        self.assertTrue(newsletter.gate_provenance(dirty, BASE))

    def test_run_gates_threads_updates_into_teaser(self):
        block = announce.render_weekly_block(self.ann, site_base_url=BASE)
        pub = self.brief["brief"]["publish_date"]
        report, teaser = newsletter.run_gates(
            self.brief, expected_date=pub, site_base_url=BASE, issue_no=2,
            run_linkcheck=False, updates_html=block)
        self.assertTrue(report.ok, report.text())
        self.assertIn("서비스 소식", teaser["html"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
