#!/usr/bin/env python3
"""영문 복수형 표지 `{s}` 가드 — "1 items"·"1 observations"·"1 reactions" 계열 재발 방지.

CI(`unittest discover -s tests`)는 `tests/test_web_i18n_plural.py` shim 으로 이 모듈을
순회한다. 직접 실행:
  python web/tests/test_i18n_plural.py

배경 — 영문은 수에 따라 명사가 변하는데 `web/data/i18n/en.json` 이 복수형 "s" 를 그대로
박아 둬서 1건일 때 "1 items" 가 나갔다. 프리 아트는 66b4573(`web/linkedin_cards.py`) 의
`nfmt`+`{s}`+`ONE_PLURAL` 가드 — 같은 설계를 문구 사전(`web/grm_i18n.py`)의 세 층
(파이썬 `tr()`/템플릿 `_()`·JS `_t()`)으로 옮겼다. `{s}` 는 en.json 값에서만 쓰고 한국어
키에는 아예 없으므로 한국어 빌드는 이 로직을 절대 타지 않는다(바이트 불변 유지).

판정은 **고친 키 목록이 아니라 en.json 전체의 성질**로 한다 — `{n}` 바로 뒤 낱말이 's'로
끝나는데 `{s}` 표지가 없으면 실패(뮤테이션: `"{n} items"`를 되돌리면 이 테스트가 잡는다).
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
import grm_i18n  # noqa: E402  (web/grm_i18n.py — 경로 삽입 후 import)

EN_CATALOG = WEB_DIR / "data" / "i18n" / "en.json"


class EnCatalogPluralGuardTest(unittest.TestCase):
    """en.json 에 `{n} <복수명사>` 가 `{s}` 표지 없이 하드코딩돼 있으면 실패.

    ★정규식 자신을 먼저 시험한다 — 66b4573 이 실제로 겪은 함정(아무것도 못 잡는 정규식이
    조용히 초록)을 반복하지 않기 위해서다."""

    # `{n}` 바로 다음 낱말(공백 하나 뒤, 소문자)이 's'로 끝나면 하드코딩 복수형이다.
    # 표지를 단 값은 명사가 `item{s}` 처럼 단수형 + `{s}` 이므로 이 패턴에 걸리지 않는다
    # ("item" 은 's'로 끝나지 않는다). 라벨류("Item {n}"·"Go to page {n}")는 낱말이
    # `{n}` **앞**에 오거나 낱말이 아예 없어 애초에 대상이 아니다.
    UNMARKED_PLURAL = re.compile(r"\{n\} [a-z]+s\b")

    def test_regex_catches_a_real_unmarked_plural_but_not_marked_ones(self):
        for bad in ("{n} items", "{n} observations", "· {n} findings",
                    "<b>{date}</b> · {n} items updated"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(self.UNMARKED_PLURAL.search(bad), bad)
        for ok in ("{n} item{s}", "{n} observation{s}", "{n}-week streak",
                   "Matches: {n}", "Go to page {n}", "Item {n}",
                   "<b>{date}</b> · {n} item{s} updated"):
            with self.subTest(ok=ok):
                self.assertIsNone(self.UNMARKED_PLURAL.search(ok), ok)

    def test_en_catalog_has_no_unmarked_plural(self):
        catalog = json.loads(EN_CATALOG.read_text(encoding="utf-8"))
        offenders = {k: v for k, v in catalog.items() if self.UNMARKED_PLURAL.search(v)}
        self.assertEqual(offenders, {},
                          f"복수형 표지({{s}}) 없이 하드코딩된 영문 값: {offenders}")

    def test_mutation_putting_back_a_bare_plural_is_caught(self):
        """뮤테이션 — 고친 값을 되돌리면(`{n} item{s}` → `{n} items`) 가드가 잡는다."""
        mutated = "{n} items"  # 09-23 이전 web/data/i18n/en.json 의 "{n}건" 값
        self.assertIsNotNone(self.UNMARKED_PLURAL.search(mutated),
                              "가드가 되돌린 복수형 버그를 못 잡는다")


class PluralMarkerTranslatorTest(unittest.TestCase):
    """파이썬 층(`tr()`/`_()`) — `{s}` 는 `n` 값에서 계산되고, 한국어는 절대 안 탄다."""

    def test_plural_suffix_helper(self):
        self.assertEqual(grm_i18n.plural_suffix(1), "")
        self.assertEqual(grm_i18n.plural_suffix("1"), "")
        self.assertEqual(grm_i18n.plural_suffix(2), "s")
        self.assertEqual(grm_i18n.plural_suffix(0), "s")
        self.assertEqual(grm_i18n.plural_suffix(None), "s")   # n 부재 → 복수가 기본값

    def test_tr_singular_and_plural(self):
        tr = grm_i18n.Translator("en", {"{n}건": "{n} item{s}"})
        self.assertEqual(tr("{n}건", n=1), "1 item")
        self.assertEqual(tr("{n}건", n=2), "2 items")
        self.assertEqual(tr("{n}건", n="1"), "1 item")     # 문자열 "1" 도 단수
        self.assertEqual(tr("{n}건", n="2"), "2 items")

    def test_tr_matches_the_live_catalog_entries(self):
        """실제 en.json 값으로도 같은 규칙이 성립한다(사전 값이 바뀌어도 규칙은 그대로)."""
        catalog = json.loads(EN_CATALOG.read_text(encoding="utf-8"))
        tr = grm_i18n.Translator("en", catalog)
        self.assertEqual(tr("{n}건", n=1), "1 item")
        self.assertEqual(tr("{n}건", n=2), "2 items")
        self.assertEqual(tr("Observation {n}건", n=1), "1 observation")
        self.assertEqual(tr("Observation {n}건", n=5), "5 observations")
        self.assertEqual(tr("{n}명 반응", n=1), "1 reaction")
        self.assertEqual(tr("{n}명 반응", n=3), "3 reactions")
        self.assertEqual(tr("규제 이력 {n}건", n=1), "Regulatory history: 1 item")
        self.assertEqual(
            tr("<b>{date}</b> · 자료 {n}건 반영", n=1, date="2026-09-23"),
            "<b>2026-09-23</b> · 1 item updated")

    def test_korean_translator_never_sees_s_marker(self):
        """한국어 키에는 `{s}` 가 없다 — 항등 번역기는 이 로직을 타지 않는다(바이트 불변)."""
        tr = grm_i18n.Translator("ko")
        self.assertEqual(tr("{n}건", n=1), "1건")
        self.assertEqual(tr("{n}건", n=2), "2건")

    def test_slot_missing_n_defaults_to_plural(self):
        self.assertEqual(grm_i18n.fill("count{s}", {}), "counts")
        self.assertEqual(grm_i18n.fill("count{s}", {"n": 1}), "count")
        self.assertEqual(grm_i18n.fill("count{s}", {"n": 2}), "counts")

    def test_explicit_s_slot_overrides_the_computed_one(self):
        """호출부가 `s=` 를 직접 주면(예외적 케이스) 그 값이 우선한다."""
        self.assertEqual(grm_i18n.fill("count{s}", {"s": "!!"}), "count!!")


class LintAllowsPluralSlotTest(unittest.TestCase):
    """`check_catalog` — `{s}` 는 키에 `{n}` 이 있을 때만 예외이고, 그 외 결손 슬롯은
    여전히 잡는다."""

    def test_s_allowed_only_when_key_has_n(self):
        problems = grm_i18n.check_catalog(
            {"{n}건": "{n} item{s}"}, {"{n}건": ["x.py:1"]}, lang="en")
        self.assertEqual(problems, [])

    def test_s_without_n_in_key_is_still_flagged(self):
        problems = grm_i18n.check_catalog(
            {"고정 문구": "fixed phrase{s}"}, {"고정 문구": ["x.py:1"]}, lang="en")
        self.assertTrue(problems, "키에 {n} 이 없는데 {s} 를 붙인 결손을 놓쳤다")
        self.assertIn("s", problems[0])

    def test_unrelated_extra_slot_is_still_flagged(self):
        problems = grm_i18n.check_catalog(
            {"{n}건": "{n} item{s} {oops}"}, {"{n}건": ["x.py:1"]}, lang="en")
        self.assertTrue(any("oops" in p for p in problems), problems)


class JsPluralShimTest(unittest.TestCase):
    """JS 층(`_t()`) — 정본 shim(`grm_i18n.JS_SHIM`)을 node 로 직접 실행해 같은 규칙을 본다."""

    def _driver(self) -> str:
        return (
            'var window = { GRM_I18N: { "{n}\\uAC74": "{n} item{s}" } };\n'
            + grm_i18n.JS_SHIM +
            'console.log(JSON.stringify([\n'
            '  _t("{n}\\uAC74", { n: 1 }),\n'
            '  _t("{n}\\uAC74", { n: 2 }),\n'
            '  _t("{n}\\uAC74", { n: "1" }),\n'
            '  _t("{n}\\uAC74", { n: "2" }),\n'
            '  _t("{n}\\uAC74", {}),\n'
            ']));\n'
        )

    @unittest.skipUnless(shutil.which("node"), "node 미설치 환경 — CI 에서 수행")
    def test_js_t_plural_marker_via_node(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_i18n_plural_"))
        try:
            drv = tmp / "drv.js"
            drv.write_text(self._driver(), encoding="utf-8")
            proc = subprocess.run(["node", str(drv)], capture_output=True,
                                  encoding="utf-8", timeout=30)
            self.assertEqual(proc.returncode, 0, f"node 실행 실패: {proc.stderr}")
            out = json.loads(proc.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(out, ["1 item", "2 items", "1 item", "2 items", "{n} items"])

    def test_js_shim_source_has_the_plural_branch(self):
        """node 미설치 환경 대비 — 소스 자체(정본 + 자산 사본 전부)에 분기가 있는지 정적 확인."""
        self.assertIn('k === "s"', grm_i18n.JS_SHIM)
        for p in grm_i18n.asset_files():
            self.assertIn('k === "s"', p.read_text(encoding="utf-8"),
                          f"{p.name}: 복수 표지 분기 없는 구형 shim 사본")


if __name__ == "__main__":
    unittest.main()
