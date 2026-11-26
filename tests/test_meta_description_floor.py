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

    def test_tail_is_skipped_when_its_noun_is_already_there(self):
        """'…용어사전 — … 용어사전입니다.' 같은 겹침 금지(11-26). 명사가 있으면 사이트 문장으로 간다."""
        for lang, path in (("ko", "glossary/"), ("en", "en/glossary/")):
            tr = render.Translator(lang)
            d = tr(render.GLOSSARY_DESCRIPTION)
            out = render.extend_short_description(d, path, lang)
            noun = tr(render._DESC_TAIL_NOUNS["glossary/"]).casefold()
            self.assertEqual(out.casefold().count(noun), d.casefold().count(noun), out)
        out = render.extend_short_description("질소 퍼지: 공정 기체로 산소를 밀어내는 일.", "glossary/n2/", "ko")
        self.assertIn("용어사전입니다", out, "명사가 없으면 묶음 문장을 그대로 붙인다")

    def test_every_noun_ending_tail_declares_its_noun(self):
        """'…X입니다.' 로 끝나는 묶음 문장은 X 를 _DESC_TAIL_NOUNS 에 적는다(새 묶음 추가 시 빠뜨림 방지)."""
        for pre, t in render._DESC_TAILS[:-1]:
            m = re.search(r"(\S+)입니다\.$", t)
            if not m:
                continue
            with self.subTest(pre=pre):
                self.assertIn(pre, render._DESC_TAIL_NOUNS)
                self.assertTrue(m.group(1).endswith(render._DESC_TAIL_NOUNS[pre].split()[-1]), m.group(1))
                self.assertIn(render._DESC_TAIL_NOUNS[pre], t)


class BuiltSiteFloorTest(unittest.TestCase):
    def test_sitemap_pages_meet_the_floor(self):
        import test_render
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_desc_"))
        try:
            test_render._build_single(tmp)
            sm = (tmp / "sitemap-full.xml").read_text(encoding="utf-8")
            locs = re.findall(r"<loc>https://[^/]+/([^<]*)</loc>", sm)
            self.assertGreater(len(locs), 10)
            short, doubled = [], []
            trs = {"ko": render.Translator("ko"), "en": render.Translator("en")}
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
                tr = trs[lang]
                for pre, noun in render._DESC_TAIL_NOUNS.items():
                    tail = tr(dict(render._DESC_TAILS)[pre])
                    if tail in d and d.casefold().count(tr(noun).casefold()) > 1:
                        doubled.append((rel, d))
            self.assertEqual(short, [], "설명이 짧은 페이지")
            self.assertEqual(doubled, [], "덧붙인 문장의 명사가 원래 설명과 겹친다")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_firm_single_year_is_not_a_range(self):
        src = (WEB / "render.py").read_text(encoding="utf-8")
        self.assertIn("f\"({fp['first_seen'][:4]})\"", src)


if __name__ == "__main__":
    unittest.main()
