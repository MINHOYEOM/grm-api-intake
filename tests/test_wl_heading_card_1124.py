"""WL 위반항목 `heading` 이 브리프 카드 화면까지 이어지는지 고정한다 (2026-11-24).

11-23 파서(F4)는 소제목(`heading` — 'Unapproved New Drug Violations' 등 닫힌 어휘)을 raw 에 싣는데,
카드 행 키 목록(`card_scaffold._WL_VIOLATION_ROW_KEYS`)에 없어 **카드 직전에서 떨어졌다**.
여기서 막는 것:
  ① raw 행 → 스캐폴드 상세 → 조립 시점 재추출 → 화면(국문·영문) 어느 층에서든 heading 이 빠지면 실패.
  ② 영문 화면에 한글이 나오지 않는다(국문 라벨 `heading_ko` 는 `*_ko` 접미라 영문 렌더가 걷어낸다).
  ③ 소제목 어휘(`collect_intake._WL_F4_HEADINGS`)가 늘면 국문 사전(`card_scaffold.WL_HEADING_KO`)도 늘어야 한다.
  ④ heading 이 없는 행은 키를 달지 않는다(기존 골든·카드 계약 불변).
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import assemble_publish_brief as apb  # noqa: E402
import card_scaffold as cs  # noqa: E402
import collect_intake as ci  # noqa: E402
import render  # noqa: E402

_HANGUL = re.compile(r"[가-힣ㄱ-ㅎ]")
_HEAD = "Unapproved New Drug Violations"
_STMT = ("Your product is an unapproved new drug under sections 505(a) and 301(d) of the FD&C Act.")


def _raw_row(heading: str = _HEAD) -> dict:
    row = {"number": "1", "statement": _STMT, "citation": "FD&C Act 505(a)"}
    if heading:
        row["heading"] = heading
    return row


def _web_card(rows: list[dict]) -> dict:
    fx = json.loads((ROOT / "tests" / "fixtures" / "brief_web_2026_07_06.json").read_text(encoding="utf-8"))
    card = dict((fx.get("cards") or fx[0]["cards"])[0])
    card["deterministic_detail"] = cs.card_detail_for_test(rows) if hasattr(cs, "card_detail_for_test") else {
        "type": "wl_violations", "count": len(rows), "violations": rows}
    return card


def _render(card: dict, lang: str) -> str:
    tr = render.Translator(lang) if lang != "ko" else render._KO
    view = render._card_view(card, tr, lang)
    return render._make_env(lang, tr).get_template("partials/card.html").render(card=view)


class HeadingReachesCardTest(unittest.TestCase):
    def test_scaffold_detail_keeps_heading_and_adds_korean_label(self):
        dd = cs._detail_wl_violations({}, {"wl_violations": [_raw_row()]})
        v = dd["violations"][0]
        self.assertEqual(v["heading"], _HEAD)
        self.assertEqual(v["heading_ko"], cs.WL_HEADING_KO[_HEAD])

    def test_row_without_heading_gets_no_keys(self):
        dd = cs._detail_wl_violations({}, {"wl_violations": [_raw_row(heading="")]})
        v = dd["violations"][0]
        self.assertNotIn("heading", v)
        self.assertNotIn("heading_ko", v)
        self.assertEqual(sorted(v), ["citation", "number", "statement"])   # 기존 행 모양 그대로

    def test_assemble_refresh_keeps_heading(self):
        """조립 시점 재추출(원문→행)이 스캐폴드와 같은 행 함수를 지난다 — heading 이 거기서 떨어지지 않는다."""
        text = ("Dear Sir: " + "Unapproved New Drug Violations Your product Foo is an unapproved new drug under "
                "sections 505(a) and 301(d) of the FD&C Act. " + "Conclusion You must respond.")
        out = {"cards": [{"id": "wl-1", "card_type": "Warning Letter"}]}
        report = apb.AssembleReport() if hasattr(apb, "AssembleReport") else None
        if report is None:
            self.skipTest("AssembleReport 없음")
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": text}}, report)
        dd = out["cards"][0].get("deterministic_detail")
        self.assertIsNotNone(dd, "원문에서 위반항목이 안 뽑힘 — 픽스처 점검")
        v = dd["violations"][0]
        self.assertEqual(v.get("heading"), _HEAD)
        self.assertEqual(v.get("heading_ko"), cs.WL_HEADING_KO[_HEAD])

    def test_korean_screen_shows_heading_korean_label(self):
        card = _web_card([cs.wl_violation_row(_raw_row())])
        html = _render(card, "ko")
        self.assertIn(cs.WL_HEADING_KO[_HEAD], html)

    def test_english_screen_shows_english_heading_and_no_hangul(self):
        row = cs.wl_violation_row(_raw_row())
        card = _web_card([row])
        html = _render(card, "en")
        self.assertIn(f'<span class="dt-chip">{_HEAD}</span>', html)
        # 상세 블록 안에 한글이 없어야 한다(국문 라벨·번역은 영문 렌더가 걷어낸다).
        block = html[html.index('class="dt-row obs-row"'):]
        block = block[:block.index("</details>")]
        self.assertIsNone(_HANGUL.search(block), block)

    def test_english_screen_drops_heading_when_not_in_data(self):
        """데이터에 heading 이 없으면 화면에도 칩이 없다(조용히 빈 칩을 그리지 않는다)."""
        card = _web_card([cs.wl_violation_row(_raw_row(heading=""))])
        html = _render(card, "en")
        block = html[html.index('class="dt-row obs-row"'):]
        self.assertNotIn("dt-chip", block[:block.index("</details>")])


class HeadingVocabularyTest(unittest.TestCase):
    def test_every_parser_heading_has_a_korean_label(self):
        missing = [h for h in ci._WL_F4_HEADINGS if h not in cs.WL_HEADING_KO]
        self.assertEqual(missing, [], "파서 소제목 어휘가 늘었는데 card_scaffold.WL_HEADING_KO 가 안 따라왔다")
        extra = [h for h in cs.WL_HEADING_KO if h not in ci._WL_F4_HEADINGS]
        self.assertEqual(extra, [], "사전에만 있는 소제목 — 파서 어휘와 갈라졌다")

    def test_publish_gate_flags_heading_without_korean_label(self):
        card = {"id": "c1", "deterministic_detail": {"type": "wl_violations", "violations": [
            {"number": "1", "statement": "x", "statement_ko": "엑스", "heading": _HEAD}]}}
        self.assertEqual(len(render.validate_wl_violations([card])), 1)
        card["deterministic_detail"]["violations"][0]["heading_ko"] = cs.WL_HEADING_KO[_HEAD]
        self.assertEqual(render.validate_wl_violations([card]), [])


if __name__ == "__main__":
    unittest.main()
