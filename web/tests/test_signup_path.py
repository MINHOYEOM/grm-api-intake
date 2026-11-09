#!/usr/bin/env python3
"""구독 폼 가입 페이지 경로(SIGNUP_PATH·SIGNUP_LANG) 가드 — 결재 #2 B안 (2026-11-09).

CI 는 `tests/test_web_signup_path.py` shim 으로 순회. 직접: python web/tests/test_signup_path.py

고정하는 성질
  (1) env 게이트가 꺼져 있으면 폼·스크립트 모두 종전과 같다(속성 없는 Brevo 에서 가입이 안 깨진다).
  (2) 켜면 밴드·배너 두 폼이 hidden 두 칸을 갖고, 한국어·영문 페이지 모두 같다.
  (3) 값은 location.pathname 만에서 나온다 — 쿼리·해시·이메일 미참조, 80자·허용 문자 검증(node 로 실행).
  (4) 익명 카운터(funnel_path_bump)·기존 필드(EMAIL·허니팟·locale)는 불변.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(WEB_DIR / "tests"))
import render        # noqa: E402
import test_render   # noqa: E402

KO_ACTION = "https://sibforms.example.com/serve/ko"
EN_ACTION = "https://sibforms.example.com/serve/en"


def _build(out: pathlib.Path, *, fields: bool) -> None:
    saved = (render.NEWSLETTER_FORM_ACTION, render.NEWSLETTER_FORM_ACTION_EN,
             render.SIGNUP_PATH_FIELDS)
    try:
        render.NEWSLETTER_FORM_ACTION = KO_ACTION
        render.NEWSLETTER_FORM_ACTION_EN = EN_ACTION
        render.SIGNUP_PATH_FIELDS = fields
        test_render._build_single(out)
    finally:
        (render.NEWSLETTER_FORM_ACTION, render.NEWSLETTER_FORM_ACTION_EN,
         render.SIGNUP_PATH_FIELDS) = saved


def _signup_script(html: str) -> str:
    i = html.index("function signupPath(")
    s = html.rindex("<script>", 0, i)
    return html[s + len("<script>"):html.index("</script>", i)]


class SignupPathGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls._tmp.name)
        cls.on = root / "on"
        cls.off = root / "off"
        _build(cls.on, fields=True)
        _build(cls.off, fields=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _pages(self, root):
        pages = {"ko": (root / "index.html").read_text(encoding="utf-8")}
        en = root / "en" / "index.html"
        if en.exists():
            pages["en"] = en.read_text(encoding="utf-8")
        return pages

    def test_gate_off_leaves_forms_and_scripts_untouched(self):
        for lang, html in self._pages(self.off).items():
            self.assertIn('id="grm-sub-form"', html, lang)
            self.assertNotIn("SIGNUP_PATH", html, lang)
            self.assertNotIn("SIGNUP_LANG", html, lang)
            self.assertNotIn("function signupPath(", html, lang)

    def test_gate_on_adds_hidden_fields_to_both_forms_ko_and_en(self):
        pages = self._pages(self.on)
        self.assertIn("en", pages, "영문 페이지가 빌드에 없다 — 영문 동일 동작을 못 본다")
        for lang, html in pages.items():
            for fid in ("grm-sub-form", "grm-cta-form"):
                m = re.search(r'<form[^>]*id="%s".*?</form>' % fid, html, re.S)
                self.assertIsNotNone(m, f"{lang}:{fid} 폼 없음")
                form = m.group(0)
                self.assertIn('name="SIGNUP_PATH"', form, f"{lang}:{fid}")
                self.assertIn('name="SIGNUP_LANG"', form, f"{lang}:{fid}")
                self.assertEqual(form.count('type="hidden"'), 3, f"{lang}:{fid} hidden 은 locale+2")
                # 기존 필드 불변
                for keep in ('name="EMAIL"', 'name="email_address_check"', 'name="locale"'):
                    self.assertIn(keep, form, f"{lang}:{fid} 기존 필드 {keep} 사라짐")
            self.assertEqual(html.count("function signupPath("), 1, f"{lang}: 스크립트는 한 번만")

    def test_anonymous_counter_untouched(self):
        """084 익명 카운터 호출은 가산 전과 같다 — 새 스크립트는 funnel 을 참조하지 않는다."""
        for lang, html in self._pages(self.on).items():
            sc = _signup_script(html)
            self.assertNotIn("funnel_", sc, lang)
            self.assertNotIn("fetch(", sc, lang)          # 이 스크립트는 네트워크를 쓰지 않는다
            if "rpc/funnel_path_bump" in html:
                self.assertIn("JSON.stringify({p_key:key,p_path:subPath()})", html, lang)

    def test_script_reads_pathname_only(self):
        sc = _signup_script(self._pages(self.on)["ko"])
        self.assertIn("location.pathname", sc)
        for bad in ("location.search", "location.href", "location.hash", "EMAIL", "email", "referrer"):
            self.assertNotIn(bad, sc, f"{bad} 를 읽으면 PII·쿼리가 샌다")

    def test_capture_listener_runs_before_form_handlers(self):
        sc = _signup_script(self._pages(self.on)["ko"])
        self.assertIn("addEventListener('submit'", sc)
        self.assertTrue(sc.rstrip().rstrip(";").endswith("})()") or "},true);" in sc)
        self.assertIn("},true);", sc, "캡처 단계가 아니면 FormData 를 읽는 기존 핸들러보다 늦다")


@unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
class SignupPathBehaviourTest(unittest.TestCase):
    """실제 인라인 스크립트를 node 로 실행해 값 정제 성질을 묻는다."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        out = pathlib.Path(cls._tmp.name) / "on"
        _build(out, fields=True)
        cls.script = _signup_script((out / "index.html").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run(self, cases):
        """cases: {이름: [pathname, form_id]} -> {이름: [SIGNUP_PATH, SIGNUP_LANG]}"""
        driver = (
            "var handlers=[];\n"
            "var document={addEventListener:function(t,fn,cap){handlers.push({t:t,fn:fn,cap:cap});}};\n"
            "var location={pathname:'/'};\n"
            + self.script + "\n"
            "var cases=" + json.dumps(cases, ensure_ascii=False) + ";\n"
            "var out={};\n"
            "for(var k in cases){\n"
            "  location.pathname=cases[k][0];\n"
            "  var f={id:cases[k][1],elements:{SIGNUP_PATH:{value:'x'},SIGNUP_LANG:{value:'x'}}};\n"
            "  handlers.forEach(function(h){if(h.t==='submit')h.fn({target:f});});\n"
            "  out[k]=[f.elements.SIGNUP_PATH.value,f.elements.SIGNUP_LANG.value];\n"
            "}\n"
            "out.__cap=handlers.map(function(h){return h.cap;});\n"
            "console.log(JSON.stringify(out));\n"
        )
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_signup_"))
        try:
            drv = tmp / "driver.js"
            drv.write_text(driver, encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True,
                                  encoding="utf-8", timeout=30)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
        return json.loads(proc.stdout)

    def test_sanitisation_properties(self):
        long_path = "/" + "a" * 200
        cases = {
            "home": ["/", "grm-sub-form"],
            "glossary": ["/glossary/capa/", "grm-sub-form"],
            "index_html": ["/findings/index.html", "grm-cta-form"],
            "en": ["/en/findings/", "grm-sub-form"],
            "en_bare": ["/en", "grm-cta-form"],
            "english_lookalike": ["/enterprise/", "grm-sub-form"],
            "upper": ["/Glossary/CAPA/", "grm-sub-form"],
            "long": [long_path, "grm-sub-form"],
            "bad_chars": ["/glossary/<script>/", "grm-sub-form"],
            "korean": ["/용어/", "grm-sub-form"],
            "space": ["/a b/", "grm-sub-form"],
            "other_form": ["/glossary/", "grm-contact-form"],
        }
        out = self._run(cases)
        self.assertEqual(out["home"], ["/", "ko"])
        self.assertEqual(out["glossary"], ["/glossary/capa/", "ko"])
        self.assertEqual(out["index_html"], ["/findings/", "ko"])
        self.assertEqual(out["en"], ["/en/findings/", "en"])
        self.assertEqual(out["en_bare"][1], "en")
        self.assertEqual(out["english_lookalike"][1], "ko", "'/enterprise/' 는 영문판이 아니다")
        self.assertEqual(out["upper"], ["/glossary/capa/", "ko"])
        # 길이 상한 80자 — 넘치면 자르되 허용 문자면 유지한다.
        self.assertLessEqual(len(out["long"][0]), 80)
        self.assertTrue(out["long"][0].startswith("/aaaa"))
        # 위반 문자(태그·한글·공백)는 빈 값 — 제출은 그대로 간다.
        for k in ("bad_chars", "korean", "space"):
            self.assertEqual(out[k][0], "", f"{k}: 이상 문자가 통과했다")
        # 다른 폼의 제출에는 손대지 않는다.
        self.assertEqual(out["other_form"], ["x", "x"])
        # 캡처 단계 리스너 하나.
        self.assertEqual(out["__cap"], [True])

    def test_query_and_hash_never_reach_the_value(self):
        """pathname 에는 쿼리·해시가 없지만, 설령 들어와도 허용 문자 밖이라 빈 값이 된다."""
        out = self._run({
            "q": ["/findings/inspector/?key=Kim", "grm-sub-form"],
            "h": ["/findings/#email=a@b.com", "grm-sub-form"],
            "at": ["/findings/a@b.com", "grm-sub-form"],
        })
        for k in ("q", "h", "at"):
            self.assertEqual(out[k][0], "", f"{k}: 쿼리·해시·이메일 꼴이 값으로 나갔다")


if __name__ == "__main__":
    unittest.main()
