"""구독 권유 정리 가드 (2026-11-26).

처음 온 사람이 한 화면에서 구독 권유를 다섯 번 만났다(헤더·첫 화면 띠·중간 칸·하단 띠·팝업).
기관 목록도 곳마다 달랐고("FDA·EU·영국·WHO·캐나다·식약처"·"FDA·EMA·PIC/S·식약처"·
"FDA·EU·식약처"), 한국어 화면에 영문 제목("Weekly Newsletter")이 섞였다.

지키는 것
1. 한 화면에 구독 칸은 하나(하단 밴드 또는 그 화면 첫 화면 칸) + 하단 팝업 하나.
   켜진 빌드(env-on)를 실제로 지어 페이지마다 센다.
2. 구독 문구의 기관 목록은 render._make_env 한 곳에서만 온다 — 템플릿에 손으로 적으면 실패.
3. 한국어 화면에 "Weekly Newsletter" 가 없다.
4. 폼이 있는 칸은 모두 동의 범위 문장(공지 메일의 근거)을 싣는다.
5. 팝업은 검은 띠가 아니다.
"""
from __future__ import annotations

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
sys.path.insert(0, str(REPO))
import render  # noqa: E402

TEMPLATES = WEB / "templates"
PARTIAL = WEB / "partials" / "sub_inline.html"
CONSENT = "사이트에 새로운 기능이 생기면 함께 안내드립니다"
CONSENT_EN = "let you know when the site gains new features"
AGENCY_LIST_RE = re.compile(r"(FDA|EMA|EU|식약처|PIC/S)·(FDA|EMA|EU|식약처|PIC/S|영국|WHO)")


def _subscribe_regions(base: str) -> list[str]:
    band = base[base.index('<section class="subscribe"'):base.index("</div></section>")]
    cta = base[base.index('<aside class="grm-cta"'):]
    cta = cta[:cta.index("</aside>")]
    return [band, cta]


class SubscribeTemplateRulesTest(unittest.TestCase):
    def test_agency_list_comes_from_one_place(self):
        self.assertEqual("·".join(render.SUBSCRIBE_AGENCIES), "FDA·EU·영국·WHO·캐나다·식약처")
        ko = render._make_env("ko")
        self.assertEqual(str(ko.globals["sub_agencies"]), "·".join(render.SUBSCRIBE_AGENCIES))
        self.assertEqual(ko.globals["sub_agency_n"], len(render.SUBSCRIBE_AGENCIES))
        en = render._make_env("en")
        self.assertIsNone(re.search(r"[가-힣]", str(en.globals["sub_agencies"])))
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        for region in _subscribe_regions(base) + [PARTIAL.read_text(encoding="utf-8")]:
            self.assertIsNone(AGENCY_LIST_RE.search(region),
                              "구독 문구에 기관 목록을 손으로 적었다 — {{ sub_agencies }} 를 쓴다")

    def test_one_block_per_page_in_templates(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        self.assertIn("and not sub_inline_on %}\n{# 뉴스레터 구독 밴드(T1)", base)
        for path in sorted(TEMPLATES.glob("*.html")):
            src = path.read_text(encoding="utf-8")
            if path.name == "base.html" or "sub_inline.html" not in src:
                continue
            self.assertEqual(src.count("sub_inline.html"), 1, f"{path.name}: 구독 칸이 둘 이상")
            self.assertRegex(src, r"\{% set sub_inline_on = [^%]+%\}", f"{path.name}: 하단 밴드를 끄지 않았다")
            self.assertIn("sub_slot='top'", src, f"{path.name}: 첫 화면 칸만 둔다")
            self.assertTrue("sub_anchor=True" in src or "sub_band=True" in src,
                            f"{path.name}: #subscribe 도착점이 없다")

    def test_no_english_kicker_on_korean_pages(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        for m in re.finditer(r"Weekly Newsletter", base):
            line = base[base.rfind("\n", 0, m.start()) + 1:base.find("\n", m.end())]
            if "{#" in line or "<script" in line:
                continue
            self.assertIn("{% if lang == 'en' %}", line, "한국어 화면에 영문 제목이 나간다")

    def test_every_form_block_carries_consent(self):
        src = PARTIAL.read_text(encoding="utf-8")
        self.assertEqual(src.count(CONSENT), 2, "홈 밴드·줄형 칸 둘 다 동의 범위 문장을 싣는다")
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        band = _subscribe_regions(base)[0]
        self.assertGreaterEqual(band.count(CONSENT), 3, "하단 밴드 세 갈래 모두")

    def test_popup_is_not_a_dark_band(self):
        base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
        rule = re.search(r"\.grm-cta\{([^}]*)\}", base).group(1)
        self.assertNotIn("background:var(--ink", rule)
        self.assertNotIn("color:#fff", rule)


class SubscribeBuiltSiteTest(unittest.TestCase):
    """켜진 빌드를 실제로 지어 화면마다 구독 칸 수를 센다(env 게이트 뒤라 골든이 못 보는 자리)."""

    @classmethod
    def setUpClass(cls):
        from test_render import _minimal_brief, _DOC_PAGES_IN_TESTS
        cls.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_subp_"))
        data, out = cls.tmp / "data", cls.tmp / "out"
        data.mkdir(parents=True)
        import json
        br = _minimal_brief("2026-06-15")
        (data / "brief_web_2026-06-15.json").write_text(json.dumps(br, ensure_ascii=False), encoding="utf-8")
        saved = (render.NEWSLETTER_FORM_ACTION, render.NEWSLETTER_FORM_ACTION_EN)
        try:
            render.NEWSLETTER_FORM_ACTION = "https://ko.example.com/subscribe"
            render.NEWSLETTER_FORM_ACTION_EN = "https://en.example.com/subscribe"
            render.render_site(data, out, render_doc_pages=_DOC_PAGES_IN_TESTS)
        finally:
            render.NEWSLETTER_FORM_ACTION, render.NEWSLETTER_FORM_ACTION_EN = saved
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pages(self):
        for p in sorted(self.out.rglob("index.html")):
            html = p.read_text(encoding="utf-8")
            if 'id="grm-cta"' in html:
                yield p.relative_to(self.out).as_posix(), html

    def test_at_most_one_block_and_one_popup_per_page(self):
        seen = 0
        for rel, html in self._pages():
            seen += 1
            blocks = html.count('<section class="subscribe"') + html.count('<aside class="grm-inl')
            self.assertLessEqual(blocks, 1, f"{rel}: 구독 칸 {blocks}개")
            self.assertEqual(html.count('<aside class="grm-cta"'), 1, rel)
            if blocks:
                self.assertEqual(html.count('id="subscribe"'), 1, f"{rel}: #subscribe 도착점")
                self.assertIn(CONSENT_EN if rel.startswith("en/") else CONSENT, html,
                              f"{rel}: 동의 범위 문장이 없다")
        self.assertGreater(seen, 10)

    def test_home_keeps_its_band_only(self):
        for rel in ("index.html", "en/index.html"):
            html = (self.out / rel).read_text(encoding="utf-8")
            self.assertEqual(html.count('data-sub-zone="home-band"'), 1, rel)
            self.assertNotIn('<section class="subscribe"', html, rel)

    def test_korean_pages_have_no_english_kicker_or_other_agency_lists(self):
        for rel, html in self._pages():
            if rel.startswith("en/"):
                continue
            self.assertNotIn("Weekly Newsletter", html, rel)
            self.assertNotIn("PIC/S·식약처", html, rel)


if __name__ == "__main__":
    unittest.main()
