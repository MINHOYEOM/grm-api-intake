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
    # 기존 5종
    "admin_action_chemical",
    "recall_quality_chemical",
    "gmp_inspection_biologic",
    "warning_letter_chemical",
    "guidance_fr",
    # K2.5 — 활성 소스 전 유형 확장(openfda·HC·WHO 3종·ICH·RSS·MFDS RSS 4종)
    "openfda_recall_chemical",
    "hc_recall_chemical",
    "who_noc",
    "who_inspection",
    "who_news",
    "ich_guideline",
    "rss_news_mhra",
    "safety_letter",
    "legislative_notice",
    "regulation_final",
    "mfds_notice",
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

    def test_evidence_quote_consistency(self) -> None:
        # P1-1 불변식: Evidence A ⟺ W3 원문 인용(`> `) 존재. "A 인데 quote 없음" 0건.
        for name in FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                has_quote = "\n> " in card.markdown
                self.assertEqual(card.evidence == "A", has_quote,
                                 f"{name}: evidence={card.evidence}, has_quote={has_quote}")

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


def _build_cards_from_rows(rows: list[dict]) -> list:
    return [cs.build_card_scaffold(item["row"], item["raw"]) for item in rows]


class MergeRecallCardsTest(unittest.TestCase):
    """card_spec §14 — recall 다품목 1카드 병합 렌더 (K3 G1)."""

    def _merged_fixture(self) -> dict:
        return json.loads(_read(os.path.join(GOLDEN, "recall_merged.input.json")))

    def _merged_cards(self) -> list:
        return cs.merge_recall_cards(_build_cards_from_rows(self._merged_fixture()["rows"]))

    def test_representative_byte_identical(self) -> None:
        # 대표(card_id 오름차순 첫) = 3품목 1카드 병합 렌더, golden 과 바이트 동일.
        merged = self._merged_cards()
        rep = merged[0]
        self.assertEqual(rep.card_id, "MFDS::recall-2026003474")
        self.assertFalse(rep.merged_into)
        got_json_str = json.dumps(rep.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        if _UPDATE:
            _write(os.path.join(GOLDEN, "recall_merged.expected.md"), rep.markdown)
            _write(os.path.join(GOLDEN, "recall_merged.expected.json"), got_json_str)
            return
        expected_md = _read(os.path.join(GOLDEN, "recall_merged.expected.md"))
        self.assertEqual(rep.markdown, expected_md)
        expected_json = json.loads(_read(os.path.join(GOLDEN, "recall_merged.expected.json")))
        self.assertEqual(json.loads(got_json_str), expected_json)

    def test_members_marked_merged_into(self) -> None:
        # §14(F): 멤버(나머지 2건)는 merged_into=대표 card_id 마킹.
        merged = self._merged_cards()
        self.assertEqual([c.merged_into for c in merged],
                         ["", "MFDS::recall-2026003474", "MFDS::recall-2026003474"])

    def test_merge_preserves_order_and_length(self) -> None:
        cards = _build_cards_from_rows(self._merged_fixture()["rows"])
        merged = cs.merge_recall_cards(cards)
        self.assertEqual(len(merged), len(cards))
        self.assertEqual([c.card_id for c in merged], [c.card_id for c in cards])

    def test_merged_prose_input_enumerates_items(self) -> None:
        # §14(E): 대표 prose_input.product = 품목 전체 나열 · merged_count = N+1.
        rep = self._merged_cards()[0]
        self.assertEqual(rep.prose_input["product"],
                         "아세트아미노펜정 500mg, 아세트아미노펜정 325mg, 이부프로펜정 200mg")
        self.assertEqual(rep.prose_input["merged_count"], 3)

    def test_merged_render_has_toggle_and_count(self) -> None:
        rep = self._merged_cards()[0]
        self.assertIn("<details>", rep.markdown)
        self.assertIn("<summary>전체 품목 (3)</summary>", rep.markdown)
        self.assertIn("외 2품목", rep.markdown)
        # W3/W5/W6/W7/W8 슬롯·인용은 대표 그대로 보존
        self.assertIn("{{W5}}", rep.markdown)
        self.assertIn("\n> 함량부적합", rep.markdown)
        self.assertEqual(cs.assert_no_forbidden_markdown(rep.markdown), [])

    def test_empty_key_not_merged(self) -> None:
        # 빈 recall_group_key(ENTRPS/사유/발행일 결측)는 병합 금지.
        rows = self._merged_fixture()["rows"]
        rows2 = [dict(item, raw=dict(item["raw"])) for item in rows]
        for item in rows2:
            item["raw"].pop("RTRVL_RESN")  # 사유 결측(폴백 없음) → recall_group_key=""
        cards = _build_cards_from_rows(rows2)
        merged = cs.merge_recall_cards(cards)
        self.assertTrue(all(not c.merged_into for c in merged))
        self.assertEqual([c.markdown for c in merged], [c.markdown for c in cards])

    def test_single_member_group_unchanged(self) -> None:
        # 단독 멤버(그룹 1건)는 무변화 — 기존 build 출력과 바이트 동일.
        fx = _load_input("recall_quality_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        merged = cs.merge_recall_cards([card])
        self.assertEqual(merged[0].markdown, card.markdown)
        self.assertFalse(merged[0].merged_into)

    def test_distinct_reasons_form_separate_groups(self) -> None:
        # 이종 사유(다른 RTRVL_RESN) = 다른 키 → 병합 금지.
        rows = self._merged_fixture()["rows"]
        rows2 = [dict(item, raw=dict(item["raw"])) for item in rows]
        rows2[1]["raw"]["RTRVL_RESN"] = "성상 변화(변색) 확인에 따른 회수"
        cards = _build_cards_from_rows(rows2)
        merged = cs.merge_recall_cards(cards)
        # 동일 사유 2건(0·2)만 병합, 이종 사유(1)는 단독 → 무병합
        self.assertEqual(merged[0].card_id, "MFDS::recall-2026003474")
        self.assertFalse(merged[0].merged_into)
        self.assertFalse(merged[1].merged_into)            # 다른 사유 → 단독
        self.assertEqual(merged[2].merged_into, "MFDS::recall-2026003474")

    def test_non_recall_cards_untouched(self) -> None:
        cards = [cs.build_card_scaffold(_load_input(n)["row"], _load_input(n)["raw"])
                 for n in ("guidance_fr", "warning_letter_chemical", "who_noc")]
        merged = cs.merge_recall_cards(cards)
        self.assertEqual([c.markdown for c in merged], [c.markdown for c in cards])
        self.assertTrue(all(not c.merged_into for c in merged))

    def test_assemble_skeleton_excludes_merged_members(self) -> None:
        # §14(F): 페이지 렌더는 대표 1카드만(멤버 markdown 미포함).
        merged = self._merged_cards()
        page = cs.assemble_brief_skeleton(merged)
        self.assertIn("외 2품목", page)
        self.assertNotIn("아세트아미노펜정 325mg</td>", page)  # 멤버 W2 미렌더
        self.assertEqual(page.count("<details>"), 1)             # 병합 toggle 1회


def _callout_colors(md: str) -> list[str]:
    import re
    return re.findall(r'color="([a-z_]+)"', md)


if __name__ == "__main__":
    unittest.main()
