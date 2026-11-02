#!/usr/bin/env python3
"""법무 초안(개인정보처리방침·이용약관) 가드 — 기본 빌드에 노출되지 않아야 한다.

CI 는 `tests/test_web_legal_draft.py` shim 으로 순회. 직접: python web/tests/test_legal_draft.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(WEB_DIR / "tests"))
import render        # noqa: E402
import test_render   # noqa: E402

DRAFTS = ("privacy_ko", "terms_ko")


class LegalDraftHiddenTest(unittest.TestCase):
    def test_default_build_emits_nothing(self):
        self.assertFalse(render.LEGAL_DRAFT_PAGES)
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d)
            test_render._build_single(out)
            self.assertFalse((out / "privacy-draft").exists())
            self.assertFalse((out / "terms-draft").exists())
            for f in ("sitemap.xml", "llms.txt"):
                p = out / f
                if p.exists():
                    t = p.read_text(encoding="utf-8")
                    self.assertNotIn("privacy-draft", t)
                    self.assertNotIn("terms-draft", t)

    def test_flag_emits_noindex_unlinked_pages(self):
        old = render.LEGAL_DRAFT_PAGES
        render.LEGAL_DRAFT_PAGES = True
        try:
            with tempfile.TemporaryDirectory() as d:
                out = pathlib.Path(d)
                test_render._build_single(out)
                for sub in ("privacy-draft", "terms-draft"):
                    html = (out / sub / "index.html").read_text(encoding="utf-8")
                    self.assertIn('content="noindex,nofollow"', html)
                    self.assertIn("확인 필요", html)
                index = (out / "index.html").read_text(encoding="utf-8")
                self.assertNotIn("privacy-draft", index)
                self.assertNotIn("terms-draft", index)
                self.assertNotIn("privacy-draft",
                                 (out / "sitemap.xml").read_text(encoding="utf-8"))
        finally:
            render.LEGAL_DRAFT_PAGES = old


class LegalDraftContentTest(unittest.TestCase):
    def test_drafts_parse_and_flag_open_items(self):
        for stem in DRAFTS:
            md = (render.LEGAL_DRAFT_DIR / f"{stem}.md").read_text(encoding="utf-8")
            title, _lead, secs = render.render_about_html(md)
            self.assertIn("초안", title)
            self.assertGreaterEqual(len(secs), 4)
            self.assertIn("[확인 필요]", md)

    def test_contact_is_the_real_address(self):
        import json
        real = json.loads((WEB_DIR / "data" / "contact.json").read_text(encoding="utf-8"))["email"]
        for stem in DRAFTS:
            md = (render.LEGAL_DRAFT_DIR / f"{stem}.md").read_text(encoding="utf-8")
            self.assertIn(real, md)


if __name__ == "__main__":
    unittest.main()
