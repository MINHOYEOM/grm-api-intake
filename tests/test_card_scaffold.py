"""card_scaffold golden 테스트 — 같은 입력 → 바이트 동일 출력 회귀 동결 (card_spec §10).

각 유형 대표 fixture(tests/golden/*.input.json)를 build_card_scaffold() 에 넣어
기대 마크다운/JSON(*.expected.md·*.expected.json)과 바이트 비교한다. 양식 변경은
문서 + golden 갱신으로만(우연한 변형 차단). 사용자 제약:
  1) Notion 렌더 가능 문법만 — LV-15.7a 폴백 금지 문법 부재 assert.
  2) 카드 1장 / 페이지 조립 분리(build_card_scaffold vs assemble_brief_skeleton).
  3) W2 에 문서번호 행 포함.
"""
import io
import json
import os
import unittest

import card_scaffold as cs

# 양식 변경 시 golden 갱신(§10.5): GRM_GOLDEN_UPDATE=1 로 expected.* 재기록 후 커밋.
_UPDATE = bool(os.environ.get("GRM_GOLDEN_UPDATE"))
GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
FIXTURES = [
    "admin_action_chemical",
    "recall_quality_chemical",
    "gmp_inspection_biologic",
    "warning_letter_chemical",
    "guidance_fr",
]


def _read(path: str) -> str:
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def _load_input(name: str) -> dict:
    return json.loads(_read(os.path.join(GOLDEN, f"{name}.input.json")))


def _write(path: str, content: str) -> None:
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(content)


class GoldenScaffoldTest(unittest.TestCase):
    def test_each_fixture_byte_identical(self) -> None:
        for name in FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                got_json_str = json.dumps(card.to_dict(), ensure_ascii=False,
                                          indent=2, sort_keys=True)
                if _UPDATE:
                    _write(os.path.join(GOLDEN, f"{name}.expected.md"), card.markdown)
                    _write(os.path.join(GOLDEN, f"{name}.expected.json"), got_json_str)
                    continue
                expected_md = _read(os.path.join(GOLDEN, f"{name}.expected.md"))
                self.assertEqual(card.markdown, expected_md,
                                 f"{name}: scaffold 마크다운이 golden 과 다름")
                expected_json = json.loads(_read(os.path.join(GOLDEN, f"{name}.expected.json")))
                self.assertEqual(json.loads(got_json_str), expected_json,
                                 f"{name}: scaffold JSON 이 golden 과 다름")

    def test_determinism_byte_for_byte(self) -> None:
        # 같은 입력 두 번 → 바이트 동일 (순수 함수 §12G)
        for name in FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                a = cs.build_card_scaffold(fx["row"], fx["raw"]).markdown
                b = cs.build_card_scaffold(fx["row"], fx["raw"]).markdown
                self.assertEqual(a, b)

    def test_no_forbidden_markdown(self) -> None:
        # 제약 1: LV-15.7a 폴백 금지 문법([!WARNING]/[!NOTE]/[TOC]/+++/<toggle>) 부재
        for name in FIXTURES:
            with self.subTest(fixture=name):
                md = _read(os.path.join(GOLDEN, f"{name}.expected.md"))
                self.assertEqual(cs.assert_no_forbidden_markdown(md), [])

    def test_w2_has_document_number_row(self) -> None:
        # 제약 3: W2 에 문서번호 행 포함
        for name in FIXTURES:
            with self.subTest(fixture=name):
                md = _read(os.path.join(GOLDEN, f"{name}.expected.md"))
                self.assertIn("**문서번호**", md)

    def test_title_follows_frozen_index_form(self) -> None:
        # P1-1 / §13.1-1·8: 제목 = "### [유형 · 기관] 핵심대상 — **{{TITLE_ISSUE}}**".
        # 제목 라인에 prefix 색사각형 이모지·DocID·site_country 부재.
        prefix_emojis = ("🟧", "🟦", "🟫", "⬜")
        for name in FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                title = card.markdown.splitlines()[0]
                self.assertTrue(title.startswith("### ["), f"{name}: 제목 인덱스 형식")
                self.assertIn("**{{TITLE_ISSUE}}**", title)
                for e in prefix_emojis:
                    self.assertNotIn(e, title, f"{name}: 제목에 prefix 이모지 잔존")
                self.assertNotIn(fx["row"]["document_id"], title)  # DocID 부재
                sc = fx["row"].get("site_country")
                if sc:
                    self.assertNotIn(sc, title)  # site_country 부재

    def test_uses_only_notion_callout_and_quote_syntax(self) -> None:
        # 제약 1 보강: 색 callout 은 허용 색만, > 는 원문 인용에만
        allowed_colors = {"blue_bg", "gray_bg", "yellow_bg", "green_bg"}
        for name in FIXTURES:
            with self.subTest(fixture=name):
                md = _read(os.path.join(GOLDEN, f"{name}.expected.md"))
                for color in _callout_colors(md):
                    self.assertIn(color, allowed_colors, f"{name}: 비허용 callout 색 {color}")

    def test_output_matrix_evidence_b_has_no_quote(self) -> None:
        # §6: Evidence B 카드는 W3 원문 인용(>) 없음
        fx = _load_input("warning_letter_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertEqual(card.evidence, "B")
        self.assertNotIn("\n> ", card.markdown)
        self.assertNotIn("{{W4", card.markdown)

    def test_ko_card_has_no_translation_token(self) -> None:
        # §13.1-4: KO 항목은 번역 없이 한글 원문 quote
        fx = _load_input("admin_action_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertIn("\n> ", card.markdown)       # 원문 quote 있음
        self.assertNotIn("{{W4", card.markdown)    # 번역 토큰 없음

    def test_recall_group_key_present(self) -> None:
        # §12(E): recall 은 recall_group_key 산출(card_id 는 유지)
        fx = _load_input("recall_quality_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertTrue(card.recall_group_key)
        self.assertEqual(card.card_id, "MFDS::recall-2026003474")

    def test_normative_guidance_omits_modality_badge(self) -> None:
        # §4: 규범문서(guidance)는 제품군 배지 생략
        fx = _load_input("guidance_fr")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertNotIn("합성의약품", card.markdown)
        self.assertNotIn("바이오의약품", card.markdown)

    def test_graceful_degrade_forces_evidence_b(self) -> None:
        # 단계 B 연계: raw_fetch_ok=False → Evidence B 강등 + status_hint
        fx = _load_input("admin_action_chemical")
        row = dict(fx["row"])
        row["raw_fetch_ok"] = False
        row["status_hint"] = "Error"
        card = cs.build_card_scaffold(row, None)
        self.assertEqual(card.evidence, "B")
        self.assertEqual(card.status_hint, "Error")
        self.assertNotIn("\n> ", card.markdown)  # B → quote 없음


class BriefSkeletonTest(unittest.TestCase):
    def _all_cards(self) -> list:
        return [cs.build_card_scaffold(_load_input(n)["row"], _load_input(n)["raw"])
                for n in FIXTURES]

    def test_page_byte_identical(self) -> None:
        page = cs.assemble_brief_skeleton(self._all_cards())
        if _UPDATE:
            _write(os.path.join(GOLDEN, "page.expected.md"), page)
            return
        expected = _read(os.path.join(GOLDEN, "page.expected.md"))
        self.assertEqual(page, expected)

    def test_page_has_toc_and_disclaimer_and_no_forbidden(self) -> None:
        # 제약 2: 페이지 수준 요소(목차·면책)는 assemble_brief_skeleton 책임
        page = cs.assemble_brief_skeleton(self._all_cards())
        self.assertIn("<table_of_contents/>", page)
        self.assertIn("AI", page)  # 면책 푸터
        self.assertEqual(cs.assert_no_forbidden_markdown(page), [])

    def test_build_card_scaffold_is_single_card_only(self) -> None:
        # 제약 2: 카드 1장 함수는 목차/면책/섹션 H2 를 만들지 않는다
        fx = _load_input("recall_quality_chemical")
        md = cs.build_card_scaffold(fx["row"], fx["raw"]).markdown
        self.assertNotIn("<table_of_contents/>", md)
        # 섹션 H2 없음(카드 제목은 ### H3) — 줄 시작 기준 검사
        self.assertFalse(any(ln.startswith("## ") for ln in md.splitlines()))


def _callout_colors(md: str) -> list[str]:
    import re
    return re.findall(r'color="([a-z_]+)"', md)


if __name__ == "__main__":
    unittest.main()
