#!/usr/bin/env python3
"""개인정보처리방침·이용약관 공개 페이지 가드 (2026-11-03 시행).

CI 는 `tests/test_web_legal_pages.py` shim 으로 순회. 직접: python web/tests/test_legal_pages.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(WEB_DIR / "tests"))
import render        # noqa: E402
import test_render   # noqa: E402

STEMS = ("privacy_ko", "terms_ko")
EFFECTIVE = "시행일: 2026년 11월 3일"


class LegalPagesPublishedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = pathlib.Path(cls._tmp.name)
        test_render._build_single(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_pages_emitted_indexable_and_final(self):
        for sub in ("privacy", "terms"):
            html = (self.out / sub / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("noindex", html)
            self.assertNotIn("확인 필요", html)
            self.assertNotIn("초안", html)
            self.assertIn("2026년 11월 3일", html)
        self.assertFalse((self.out / "privacy-draft").exists())

    def test_sitemap_and_footer_link(self):
        sm = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("/privacy/</loc>", sm)
        self.assertIn("/terms/</loc>", sm)
        index = (self.out / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/privacy/"', index)
        self.assertIn('href="/terms/"', index)


class LegalContentTest(unittest.TestCase):
    def test_final_text_has_no_open_items(self):
        for stem in STEMS:
            md = (render.LEGAL_DIR / f"{stem}.md").read_text(encoding="utf-8")
            title, _lead, secs = render.render_about_html(md)
            self.assertNotIn("초안", title)
            self.assertNotIn("확인 필요", md)
            self.assertIn(EFFECTIVE, md)
            self.assertGreaterEqual(len(secs), 4)

    def test_under_14_sentence(self):
        md = (render.LEGAL_DIR / "privacy_ko.md").read_text(encoding="utf-8")
        self.assertIn("만 14세 미만은 회원으로 가입할 수 없습니다", md)

    def test_contact_is_the_real_address(self):
        real = json.loads((WEB_DIR / "data" / "contact.json").read_text(encoding="utf-8"))["email"]
        for stem in STEMS:
            md = (render.LEGAL_DIR / f"{stem}.md").read_text(encoding="utf-8")
            self.assertIn(real, md)

    def test_signup_links_resolve_to_emitted_paths(self):
        js = (WEB_DIR / "assets" / "reactions.js").read_text(encoding="utf-8")
        for _stem, path in render.LEGAL_PAGES:
            self.assertIn(f'legalHref("{path}")', js)


if __name__ == "__main__":
    unittest.main()
