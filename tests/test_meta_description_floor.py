"""메타 설명 최소 길이 가드 (2026-11-26, 빙 '설명이 짧은 페이지가 많다' 권고).

짧은 설명(한국어 80자·영어 110자 미만)에는 render.extend_short_description 이 그 묶음 한 문장을 덧붙인다.
빌드한 사이트에서 sitemap 에 실린 페이지의 설명이 모두 최소 길이 이상인지 잰다.
"""
from __future__ import annotations

import html
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / "tests"))
import render  # noqa: E402

DESC_RE = re.compile(r'<meta name="description" content="([^"]*)"')


class ExtendRuleTest(unittest.TestCase):
    def test_short_gets_section_tail_long_is_untouched(self):
        short = "업체 A의 공개 실사 문서 2건에서 확인된 지적 15건을 우리말로 정리했습니다(2024)."
        out = render.extend_short_description(short, "findings/firm/a/", "ko")
        self.assertTrue(out.startswith(short))
        self.assertIn("공식 원문 링크", out)
        self.assertGreaterEqual(len(out), render.DESC_MIN_CHARS["ko"])
        long = "가" * 90
        self.assertEqual(render.extend_short_description(long, "glossary/x/", "ko"), long)
        self.assertEqual(render.extend_short_description("", "findings/", "ko"), "")
        en = render.extend_short_description("Firm A: 15 findings from 2 documents.", "en/findings/firm/a/", "en")
        self.assertIn("official source", en)
        self.assertIsNone(re.search(r"[가-힣]", en))

    def test_every_tail_reaches_the_floor_and_is_translated(self):
        en = render.Translator("en")
        for pre, t in render._DESC_TAILS:
            with self.subTest(pre=pre):
                self.assertIsNone(re.search(r"[가-힣]", en(t)), "영문 문구 누락")
                self.assertIsNone(re.search(r"\d", t.replace("483", "")), "낡는 숫자를 넣지 않는다")


class BuiltSiteFloorTest(unittest.TestCase):
    def test_sitemap_pages_meet_the_floor(self):
        import test_render
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_desc_"))
        try:
            test_render._build_single(tmp)
            sm = (tmp / "sitemap-full.xml").read_text(encoding="utf-8")
            locs = re.findall(r"<loc>https://[^/]+/([^<]*)</loc>", sm)
            self.assertGreater(len(locs), 10)
            short = []
            for rel in locs:
                f = tmp / rel / "index.html"
                if not f.exists():
                    continue
                m = DESC_RE.search(f.read_text(encoding="utf-8"))
                if not m:
                    continue
                d = html.unescape(m.group(1))
                lang = "en" if rel.startswith("en/") else "ko"
                if d and len(d) < render.DESC_MIN_CHARS[lang]:
                    short.append((rel, len(d), d))
            self.assertEqual(short, [], "설명이 짧은 페이지")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_firm_single_year_is_not_a_range(self):
        src = (WEB / "render.py").read_text(encoding="utf-8")
        self.assertIn("f\"({fp['first_seen'][:4]})\"", src)


if __name__ == "__main__":
    unittest.main()
