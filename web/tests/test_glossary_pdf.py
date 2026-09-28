"""GMP 규제 용어집 PDF(`web/glossary_pdf.py`) — HTML 조립 단위 테스트(Chrome 불필요).

PDF 인쇄 자체는 배포 워크플로의 비차단 스텝이 하고, 인쇄 뒤 `verify_pdf` 가 용어 수를 다시
센다. 여기서는 그 앞단 — 정본 전량이 실리는지, QR·링크가 운영 주소와 UTM 을 가리키는지,
약속하지 않기로 한 문구가 없는지 — 를 고정한다. 용어 목록은 손으로 적지 않고 정본에서 읽는다.
"""
from __future__ import annotations

import io
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import glossary_pdf  # noqa: E402
import render  # noqa: E402


class GlossaryPdfHtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.view = glossary_pdf.load_view()
        cls.terms = [t for g in cls.view["groups"] for t in g["terms"]]
        cls.html = glossary_pdf.build_html(cls.view, "2026년 9월", font_links=False)

    def test_every_term_is_an_entry_and_in_the_index(self):
        for t in self.terms:
            self.assertIn(f'<article class="t" id="{t["id"]}">', self.html, t["id"])
            self.assertIn(f'<li><a href="#{t["id"]}">', self.html, t["id"])

    def test_one_online_link_per_term(self):
        self.assertEqual(self.html.count(glossary_pdf.ONLINE), len(self.terms))
        for t in self.terms:
            self.assertIn(f'href="https://grm-solutions.com/glossary/{t["id"]}/"', self.html, t["id"])

    def test_values_are_verbatim(self):
        # 풀이·출처를 고쳐 쓰지 않는다 — 이스케이프만.
        from markupsafe import escape
        for t in self.terms[:40]:
            self.assertIn(str(escape(t["easy"])), self.html, t["id"])
            self.assertIn(str(escape(t["definition_source"])), self.html, t["id"])

    def test_cover_counts_come_from_the_view(self):
        self.assertIn(f'<b>{self.view["total"]}</b><span>용어</span>', self.html)
        self.assertIn(f'전체 용어 {self.view["total"]}개', self.html)
        self.assertIn("2026년 9월 판", self.html)

    def test_qr_targets_are_production_with_utm(self):
        for url in (glossary_pdf.QR_GLOSSARY, glossary_pdf.QR_HOME):
            self.assertTrue(url.startswith("https://grm-solutions.com/"), url)
            self.assertIn("utm_source=glossary_pdf", url)
            self.assertIn("utm_medium=pdf", url)
        self.assertEqual(self.html.count('<div class="qr"><svg'), 2)

    def test_production_host_is_fixed_not_env(self):
        # 프리뷰 배포에서 만들어도 운영 주소 — render.SITE_BASE_URL(env) 을 따라가지 않는다.
        self.assertEqual(glossary_pdf.SITE, "https://grm-solutions.com")
        hosts = set(re.findall(r'href="https?://([^/"]+)', self.html))
        self.assertEqual(hosts, {"grm-solutions.com"}, hosts)

    def test_no_weekly_update_promise(self):
        # 2026-09-28 사용자 결정 — 추가가 없는 주도 있으니 '매주 새 용어'는 약속하지 않는다.
        for phrase in ("매주 새", "매주 추가", "매주 갱신", "매주 업데이트"):
            self.assertNotIn(phrase, self.html)
        self.assertIn("주기적으로 갱신됩니다", self.html)

    def test_four_promos_spread_between_groups(self):
        self.assertEqual(self.html.count('<a class="promo"'), min(4, len(self.view["groups"])))


class GlossaryPdfHelpersTest(unittest.TestCase):
    def test_promo_slots_spread_and_distinct(self):
        for n in (1, 2, 4, 5, 14, 26):
            slots = glossary_pdf.promo_slots(n)
            self.assertEqual(len(slots), min(4, n), n)
            self.assertTrue(all(0 <= i < n for i in slots), (n, slots))
            self.assertEqual(sorted(slots.values()), list(range(min(4, n))))
        self.assertEqual(glossary_pdf.promo_slots(0), {})

    def test_href_from_shown_address(self):
        self.assertEqual(glossary_pdf._href("grm-solutions.com"), "https://grm-solutions.com/")
        self.assertEqual(glossary_pdf._href("grm-solutions.com/findings/trends"),
                         "https://grm-solutions.com/findings/trends/")

    def test_qr_svg_is_inline_svg(self):
        svg = glossary_pdf.qr_svg("https://grm-solutions.com/")
        self.assertTrue(svg.startswith("<svg"), svg[:40])
        self.assertIn("<path", svg)

    def test_edition_label(self):
        self.assertEqual(glossary_pdf.edition_label(datetime(2026, 9, 28)), "2026년 9월")

    def test_pdf_path_is_outside_glossary_tree(self):
        # glossary/ 아래 디렉터리는 전부 용어 페이지여야 한다(유령 페이지 가드).
        self.assertFalse(render.GLOSSARY_PDF_PATH.startswith("glossary/"))
        self.assertTrue(render.GLOSSARY_PDF_PATH.endswith(".pdf"))


class GlossaryPdfCliTest(unittest.TestCase):
    def test_without_chrome_writes_html_at_the_shared_path_and_exits_2(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_gpdf_"))
        try:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(glossary_pdf.lc, "find_chrome", return_value=None), \
                    redirect_stdout(out), redirect_stderr(err):
                rc = glossary_pdf.main(["--out", str(tmp)])
            self.assertEqual(rc, 2)
            html_path = (tmp / render.GLOSSARY_PDF_PATH).with_suffix(".html")
            self.assertTrue(html_path.is_file(), html_path)
            self.assertIn("Chrome 없음", err.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
