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
import re
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
    "hc_recall_biologic",
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

# P1 web-card golden 대상 = 기존 카드 fixture 전체 + excerpt 2 + fda-483(병합은 별도 클래스).
# watch 섹션(legislative_notice)은 v1 카드 아님(§3.3) → per-card web 골든 미동결(to_web_card
# 는 watch 로 호출되지 않음; brief 제외는 WebBriefGoldenTest 가 검증).
WEBCARD_FIXTURES = [f for f in FIXTURES if f != "legislative_notice"] + [
    "who_inspection_excerpt", "warning_letter_excerpt", "fda_483",
    # [상세보기 결정론 승격 2026-07-02 · spec §16] gmp-inspection deterministic_detail —
    # periodic(지적 표 有 → deterministic_detail) + pre_market(표 無 → 필드 부재). web-card 전용
    # (FIXTURES 미포함 → brief golden·intake_total 불변).
    "gmp_inspection_periodic", "gmp_inspection_pre_market",
]

# assemble_web_brief golden 의 결정론 brief 메타(코드 소유 — LLM tldr 은 placeholder []).
WEB_BRIEF_META = {
    "run_date_kst": "2026-06-22",
    "window": "2026-06-15 ~ 2026-06-22",
    "publish_date": "2026-06-22",
    "intake_total": len(FIXTURES),
}


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


class NegDateSortKeyTest(unittest.TestCase):
    """C1 — _neg_date 정수 튜플 키: 비ASCII date 무크래시 + ASCII 순서 동치.

    종전 chr(255-ord) 문자열 키는 ord>255(한글 등) date 에서 chr(음수) ValueError
    로 _sort_key→assemble_brief_skeleton 전체를 중단시켰다. date 는 row.get("date")
    무검증 유입이라 입력 기인 크래시였다.
    """

    def test_ascii_order_identical_to_legacy_chr_key(self) -> None:
        def legacy(d: str) -> str:  # 종전 키(레퍼런스, ASCII 전용)
            return "".join(chr(255 - ord(ch)) for ch in d) if d else "\xff"
        dates = ["2026-06-08", "2026-06-01", "2025-12-31", "", "2026-06-08",
                 "2026-1-2", "2026-06"]   # 중복·빈 값·prefix 변형 포함
        self.assertEqual(sorted(dates, key=legacy),
                         sorted(dates, key=cs._neg_date))
        # 의미 자체도 고정: 큰 날짜 먼저(desc), 빈 date 최후순.
        self.assertEqual(
            sorted(["2026-06-01", "", "2026-06-08", "2025-12-31"],
                   key=cs._neg_date),
            ["2026-06-08", "2026-06-01", "2025-12-31", ""])

    def test_non_ascii_date_does_not_crash_skeleton(self) -> None:
        fx = _load_input("guidance_fr")
        bad_row = dict(fx["row"])
        bad_row["date"] = "이천이십육년 유월"          # 종전: ValueError 크래시
        bad_row["document_id"] = "fr-nonascii-date"
        cards = [cs.build_card_scaffold(bad_row, fx["raw"]),
                 cs.build_card_scaffold(dict(fx["row"]), fx["raw"])]
        page = cs.assemble_brief_skeleton(cards)
        self.assertIsInstance(page, str)
        self.assertIn("<table_of_contents/>", page)   # 정상 조립까지 완주


def _build_cards_from_rows(rows: list[dict]) -> list:
    return [cs.build_card_scaffold(item["row"], item["raw"]) for item in rows]


def _all_fixture_cards() -> list:
    return [cs.build_card_scaffold(_load_input(n)["row"], _load_input(n)["raw"])
            for n in FIXTURES]


def _recall_rows(entrps: str, reason: str, pub: str,
                 products: list[str]) -> list[dict]:
    """동일 키(병합 대상) recall 3요소 + 품목별 row 생성 — card_id 오름차순 = 입력 순서."""
    out = []
    for i, prd in enumerate(products):
        out.append({
            "row": {"date": pub, "document_id": f"recall-{i:04d}", "firm": entrps,
                    "headline": "회수", "language": "KO", "modality": "Chemical",
                    "raw_fetch_ok": True, "signal_tier": "Tier 2", "source": "MFDS",
                    "type_or_class": "recall-quality"},
            "raw": {"ENTRPS": entrps, "PRDUCT": prd, "RTRVL_RESN": reason},
        })
    return out


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

    def test_merged_product_guard_caps_300_chars(self) -> None:
        # R1-b: 대표 품목명 자체가 길어 `외 N품목` fallback 도 300자 초과 → 최종 ≤300자.
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["가" * 300, "나정"])
        rep = cs.merge_recall_cards(_build_cards_from_rows(rows))[0]
        product = rep.prose_input["product"]
        self.assertLessEqual(len(product), 300)
        self.assertTrue(product.endswith("…"))

    def test_merged_product_joined_when_under_limit(self) -> None:
        # R1-b 경계: 나열이 300자 이하면 전체 나열 그대로(축약 안 함).
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["가정", "나정", "다정"])
        rep = cs.merge_recall_cards(_build_cards_from_rows(rows))[0]
        self.assertEqual(rep.prose_input["product"], "가정, 나정, 다정")

    def test_c2_blank_member_counts_unified_to_named(self) -> None:
        # C2: 멤버 1건 빈 PRDUCT → 제목/W2/toggle summary/merged_count 전부
        # 비공란 수(2)로 일치 — 종전 "전체 품목 (3)" vs 불릿 2개 불일치 차단.
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["가정", "", "다정"])
        rep = cs.merge_recall_cards(_build_cards_from_rows(rows))[0]
        md = rep.markdown
        self.assertIn("<summary>전체 품목 (2)</summary>", md)
        self.assertNotIn("전체 품목 (3)", md)
        bullets = [ln for ln in md.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(bullets), 2)                       # summary 수 == 불릿 수
        self.assertIn("가정 외 1품목", md)                       # 제목·W2 의 '외 N'
        self.assertNotIn("외 2품목", md)
        self.assertEqual(rep.prose_input["merged_count"], 2)
        self.assertEqual(rep.prose_input["product"], "가정, 다정")

    def test_c2_blank_representative_counts_named_only(self) -> None:
        # C2 경계: 대표(card_id 첫) 자신이 빈 PRDUCT — '외 N' = 비공란 전체 수.
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["", "나정", "다정"])
        rep = cs.merge_recall_cards(_build_cards_from_rows(rows))[0]
        md = rep.markdown
        self.assertIn("<summary>전체 품목 (2)</summary>", md)
        bullets = [ln for ln in md.splitlines() if ln.startswith("- ")]
        self.assertEqual(len(bullets), 2)
        self.assertIn("한국제약(주) 외 2품목", md)               # 대표 품목명 없이
        self.assertEqual(rep.prose_input["merged_count"], 2)

    def test_merged_title_truncates_at_60(self) -> None:
        # R1-c: 구두점 없는 60자 초과 핵심대상 — _truncate_at_sentence 경계 동작 스냅샷.
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["아세트아미노펜정" * 10, "나정"])
        rep = cs.merge_recall_cards(_build_cards_from_rows(rows))[0]
        title = rep.markdown.splitlines()[0]
        target = title.partition("] ")[2].partition(" — ")[0]
        self.assertTrue(target.endswith("…"))      # 절단 마커
        self.assertLessEqual(len(target), 61)       # 60자 + '…'

    def test_assemble_skeleton_excludes_merged_members(self) -> None:
        # §14(F): 페이지 렌더는 대표 1카드만(멤버 markdown 미포함).
        merged = self._merged_cards()
        page = cs.assemble_brief_skeleton(merged)
        self.assertIn("외 2품목", page)
        self.assertNotIn("아세트아미노펜정 325mg</td>", page)  # 멤버 W2 미렌더
        self.assertEqual(page.count("<details>"), 1)             # 병합 toggle 1회


class RenderPlanTest(unittest.TestCase):
    """R1-d(fork A안): compute_render_plan == assemble_brief_skeleton 순서 공유."""

    def test_render_order_matches_page_card_order(self) -> None:
        cards = cs.merge_recall_cards(_all_fixture_cards())
        plan = cs.compute_render_plan(cards)
        page = cs.assemble_brief_skeleton(cards)
        visible = [c for c in cards if not c.merged_into]
        # render_order 는 0..N-1 연속, 가시 카드 전원에 부여(멤버 제외).
        self.assertEqual(sorted(p["render_order"] for p in plan.values()),
                         list(range(len(visible))))
        # render_order 오름차순 == 페이지 등장 순서(동일 정렬 helper 공유 검증).
        by_order = sorted(visible, key=lambda c: plan[c.card_id]["render_order"])
        positions = [page.index(c.markdown) for c in by_order]
        self.assertEqual(positions, sorted(positions))

    def test_members_get_no_render_order(self) -> None:
        rows = _recall_rows("한국제약(주)", "함량부적합 회수", "2026-06-02",
                            ["가정", "나정", "다정"])
        cards = cs.merge_recall_cards(_build_cards_from_rows(rows))
        plan = cs.compute_render_plan(cards)
        member_ids = [c.card_id for c in cards if c.merged_into]
        self.assertEqual(len(member_ids), 2)
        for mid in member_ids:
            self.assertNotIn(mid, plan)

    def test_group_label_threshold_on_off(self) -> None:
        # 글로벌 ≥4 → 제품군 group_label 부여, ≤3 → 전부 빈 라벨(평면).
        glob = [n for n in FIXTURES
                if cs.build_card_scaffold(_load_input(n)["row"],
                                          _load_input(n)["raw"]).section == "global"]
        self.assertGreaterEqual(len(glob), 4)
        many = [cs.build_card_scaffold(_load_input(n)["row"], _load_input(n)["raw"])
                for n in glob]
        plan_many = cs.compute_render_plan(many)
        self.assertTrue(any(p["group_label"] for p in plan_many.values()))
        few = many[:3]
        plan_few = cs.compute_render_plan(few)
        self.assertTrue(all(not p["group_label"] for p in plan_few.values()))


class ForbiddenMarkdownGuardTest(unittest.TestCase):
    """A2 — 금지 마크다운 가드: raw 입력에 금지 토큰이 있어도 최종 markdown 에 0."""

    def _admin_row_with_forbidden(self, expose_cont: str) -> tuple[dict, dict]:
        """admin-action 기반, EXPOSE_CONT 에 금지 토큰을 심은 (row, raw)."""
        return (
            {"date": "2026-06-01", "document_id": "adm-forbidden-01",
             "firm": "테스트제약", "headline": "행정처분",
             "language": "KO", "modality": "Chemical",
             "raw_fetch_ok": True, "signal_tier": "Tier 2",
             "source": "MFDS", "type_or_class": "admin-action",
             "evidence_candidate": "A"},
            {"EXPOSE_CONT": expose_cont,
             "ADM_DISPS_SEQ": "99999",
             "ADM_DISPS_NAME": "업무정지"},
        )

    def _recall_row_with_forbidden(self, rtrvl_resn: str) -> tuple[dict, dict]:
        """recall-quality 기반, RTRVL_RESN 에 금지 토큰을 심은 (row, raw)."""
        return (
            {"date": "2026-06-01", "document_id": "recall-forbidden-01",
             "firm": "테스트제약", "headline": "회수",
             "language": "KO", "modality": "Chemical",
             "raw_fetch_ok": True, "signal_tier": "Tier 2",
             "source": "MFDS", "type_or_class": "recall-quality"},
            {"ENTRPS": "테스트제약", "PRDUCT": "위반약정",
             "RTRVL_RESN": rtrvl_resn},
        )

    def test_admin_expose_cont_with_all_forbidden_tokens(self) -> None:
        """W2 표·W3 인용 경로: EXPOSE_CONT 에 금지 토큰 전종 → 최종 markdown 0."""
        poison = "위반 [!WARNING] 내용 [!NOTE] 사항 +++ <toggle> 중간 </toggle> 끝 [TOC] [!IMPORTANT] [!TIP] [!CAUTION] <toggle x>"
        row, raw = self._admin_row_with_forbidden(poison)
        card = cs.build_card_scaffold(row, raw)
        found = cs.assert_no_forbidden_markdown(card.markdown)
        self.assertEqual(found, [], f"금지 토큰 잔존: {found}")
        for tok in cs.FORBIDDEN_MARKDOWN:
            self.assertNotIn(tok, card.markdown)

    def test_recall_rtrvl_resn_with_forbidden_tokens(self) -> None:
        """W3 인용 경로: RTRVL_RESN 에 금지 토큰 → 최종 markdown 0."""
        poison = "함량부적합 [!WARNING] 회수 +++ <toggle>위험</toggle>"
        row, raw = self._recall_row_with_forbidden(poison)
        card = cs.build_card_scaffold(row, raw)
        found = cs.assert_no_forbidden_markdown(card.markdown)
        self.assertEqual(found, [], f"금지 토큰 잔존: {found}")

    def test_w2_table_value_with_forbidden_tokens(self) -> None:
        """W2 표 경로: firm 에 금지 토큰이 있어도 최종 markdown 0."""
        row, raw = self._admin_row_with_forbidden("정상 내용")
        raw["firm"] = "제약 [!WARNING] 회사"
        row["firm"] = "제약 [!WARNING] 회사"
        card = cs.build_card_scaffold(row, raw)
        found = cs.assert_no_forbidden_markdown(card.markdown)
        self.assertEqual(found, [], f"금지 토큰 잔존: {found}")

    def test_neutralize_is_noop_on_clean_input(self) -> None:
        """clean 입력 → _neutralize_forbidden 은 no-op (golden 안정성 전제)."""
        clean = "정상 텍스트 <callout> > 인용 <details> <table> ### H3 ---"
        self.assertEqual(cs._neutralize_forbidden(clean), clean)

    def test_neutralize_preserves_allowed_syntax(self) -> None:
        """허용 문법(<callout>·>·<details>·<table>·<table_of_contents/>·### H3·---)은
        금지 토큰 정화 후에도 그대로."""
        allowed = [
            "<callout icon=\"📌\" color=\"blue_bg\">", "</callout>",
            "> 원문 인용", "<details>", "</details>",
            "<table>", "</table>", "<table_of_contents/>",
            "### 제목", "---",
            "{{W1}}", "{{W5}}", "{{TITLE_ISSUE}}",
        ]
        for token in allowed:
            mixed = f"before {token} after [!WARNING] end"
            result = cs._neutralize_forbidden(mixed)
            self.assertIn(token, result, f"허용 토큰 손상: {token}")
            self.assertNotIn("[!WARNING]", result)

    def test_merged_recall_with_forbidden_tokens(self) -> None:
        """병합 경로: 품목명에 금지 토큰 → 병합 후 markdown 0."""
        rows = _recall_rows("테스트제약", "함량부적합 [!WARNING] 회수", "2026-06-02",
                            ["가정 +++ 이상", "나정 <toggle>위험</toggle>", "다정"])
        cards = _build_cards_from_rows(rows)
        merged = cs.merge_recall_cards(cards)
        rep = merged[0]
        found = cs.assert_no_forbidden_markdown(rep.markdown)
        self.assertEqual(found, [], f"병합 대표 금지 토큰 잔존: {found}")

    def test_recall_rtrvl_resn_overlong_quote_is_truncated(self) -> None:
        """A3: 2000자+ 무종결 RTRVL_RESN 의 '>' 인용 라인이 형제 분기처럼 250자
        절단돼 Notion rich-text 한도(2000자)를 넘지 않는다. 옛 무절단 분기는 전체
        회수사유를 그대로 '>' 한 줄로 내보내 한도 초과 가능했다."""
        # 종결부호 없는 한국어 장문(>2000자) — _split_sentences 가 통째 반환하던 경로.
        long_resn = "회수사유 " + "가나다라마바사아자차카타파하" * 200
        self.assertGreater(len(long_resn), 2000)
        row, raw = self._recall_row_with_forbidden(long_resn)
        card = cs.build_card_scaffold(row, raw)
        quote_lines = [ln for ln in card.markdown.splitlines() if ln.startswith("> ")]
        self.assertTrue(quote_lines, "W3 인용 라인이 생성돼야 함(recall-quality=Evidence A)")
        for ln in quote_lines:
            # '> ' 접두(2) + 250자 + '…'(1) = 최대 253. 2000 한도 대비 충분히 짧다.
            self.assertLessEqual(len(ln), 253, f"인용 라인 과길이: {len(ln)}")
        # A2 불변식: 입력 기인 금지 토큰 부재(가드가 이 경로에서도 [] 를 반환).
        self.assertEqual(cs.assert_no_forbidden_markdown(card.markdown), [])


class ExcerptProseInputGoldenTest(unittest.TestCase):
    """WHY-1 #1/#2 — WHOPIR/WL excerpt 가 prose_input 에 반영되되 Evidence 는 불변(B).

    신규 golden 2종(기존 16종과 분리 — page.expected.md·16-count 불변 유지). 두 키는
    _prose_input 의 issue_or_reason·body_excerpt _first 폴백에만 추가됐으므로:
      - issue_or_reason 이 링크텍스트(anchor_text)/subject 대신 excerpt 를 가리킨다.
      - markdown(슬롯 {{W5}})·Evidence 판정은 불변(인용 승격 아님 → 여전히 B·무 '>').
    """

    _EXCERPT_FIXTURES = ("who_inspection_excerpt", "warning_letter_excerpt")

    def test_excerpt_fixtures_byte_identical(self) -> None:
        for name in self._EXCERPT_FIXTURES:
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
                self.assertEqual(card.markdown, expected_md)
                expected_json = json.loads(_read(os.path.join(GOLDEN, f"{name}.expected.json")))
                self.assertEqual(json.loads(got_json_str), expected_json)

    def test_whopir_excerpt_feeds_issue_or_reason_and_body(self) -> None:
        fx = _load_input("who_inspection_excerpt")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        excerpt = fx["raw"]["whopir_excerpt"]
        # excerpt 가 링크텍스트(anchor_text "WHOPIR: Site Z…") 대신 issue_or_reason 선두.
        self.assertTrue(excerpt.startswith(card.prose_input["issue_or_reason"][:30]))
        self.assertNotEqual(card.prose_input["issue_or_reason"], fx["raw"]["anchor_text"])
        self.assertTrue(card.prose_input["body_excerpt"])

    def test_wl_body_excerpt_feeds_issue_or_reason_over_subject(self) -> None:
        fx = _load_input("warning_letter_excerpt")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        excerpt = fx["raw"]["wl_body_excerpt"]
        # excerpt 가 subject("CGMP/Finished…") 대신 issue_or_reason 선두.
        self.assertTrue(excerpt.startswith(card.prose_input["issue_or_reason"][:30]))
        self.assertNotEqual(card.prose_input["issue_or_reason"], fx["raw"]["subject"])

    def test_excerpt_does_not_promote_to_evidence_a(self) -> None:
        # §6: prose_input(W5/W6/W7)만 보강 — W3 인용(Evidence A) 승격 아님.
        for name in self._EXCERPT_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                self.assertEqual(card.evidence, "B")
                self.assertNotIn("\n> ", card.markdown)

    def test_excerpt_key_does_not_change_rendered_markdown(self) -> None:
        # excerpt 는 prose_input(JSON 슬롯 입력)만 바꾸고 렌더 markdown 은 안 바꾼다.
        # 동일 fixture 에서 excerpt 키만 제거 → markdown 바이트 동일(슬롯 {{W5}} 렌더).
        keys = {"who_inspection_excerpt": "whopir_excerpt",
                "warning_letter_excerpt": "wl_body_excerpt"}
        for name, key in keys.items():
            with self.subTest(fixture=name):
                fx = _load_input(name)
                with_excerpt = cs.build_card_scaffold(fx["row"], fx["raw"])
                raw_without = {k: v for k, v in fx["raw"].items() if k != key}
                without_excerpt = cs.build_card_scaffold(fx["row"], raw_without)
                self.assertEqual(with_excerpt.markdown, without_excerpt.markdown)
                self.assertNotEqual(with_excerpt.prose_input["issue_or_reason"],
                                    without_excerpt.prose_input["issue_or_reason"])


class Fda483GoldenTest(unittest.TestCase):
    """WHY-1 #3 — FDA 483/EIR 신규 kind `fda-483` golden(기존 golden 과 분리, 바이트 불변 유지).

    483 = Tier 3 결함 원본. excerpt(fda483_excerpt)는 prose_input(W5/W6/W7)만 보강하고
    W3 인용(Evidence A) 승격은 아님(§5 — 여전히 Evidence B·무 '>'). W2 에 제조소·FEI·시설/
    유형·실사일이 들어가고, W8 공식원본은 건별 483 PDF(/media/<id>/download)를 가리킨다.
    """

    _NAME = "fda_483"

    def test_fda483_fixture_byte_identical(self) -> None:
        fx = _load_input(self._NAME)
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        got_json_str = json.dumps(card.to_dict(), ensure_ascii=False,
                                  indent=2, sort_keys=True)
        if _UPDATE:
            _write(os.path.join(GOLDEN, f"{self._NAME}.expected.md"), card.markdown)
            _write(os.path.join(GOLDEN, f"{self._NAME}.expected.json"), got_json_str)
            return
        expected_md = _read(os.path.join(GOLDEN, f"{self._NAME}.expected.md"))
        self.assertEqual(card.markdown, expected_md)
        expected_json = json.loads(_read(os.path.join(GOLDEN, f"{self._NAME}.expected.json")))
        self.assertEqual(json.loads(got_json_str), expected_json)

    def test_fda483_kind_section_and_evidence_b(self) -> None:
        fx = _load_input(self._NAME)
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertEqual(card.kind, "fda-483")
        self.assertEqual(card.section, "global")
        self.assertEqual(card.evidence, "B")            # §5 — Evidence B(인용 승격 아님)
        self.assertNotIn("\n> ", card.markdown)         # B → W3 원문 인용 없음
        self.assertNotIn("{{W4", card.markdown)         # 번역 토큰 없음

    def test_fda483_excerpt_feeds_prose_over_meta(self) -> None:
        fx = _load_input(self._NAME)
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        excerpt = fx["raw"]["fda483_excerpt"]
        # excerpt 가 headline/메타가 아니라 issue_or_reason·body_excerpt 선두를 채운다.
        self.assertTrue(excerpt.startswith(card.prose_input["issue_or_reason"][:30]))
        self.assertTrue(excerpt.startswith(card.prose_input["body_excerpt"][:30]))

    def test_fda483_excerpt_key_does_not_change_markdown(self) -> None:
        # excerpt 는 prose_input 만 바꾸고 렌더 markdown 은 안 바꾼다(슬롯 {{W5}} 렌더).
        fx = _load_input(self._NAME)
        with_excerpt = cs.build_card_scaffold(fx["row"], fx["raw"])
        raw_without = {k: v for k, v in fx["raw"].items() if k != "fda483_excerpt"}
        without_excerpt = cs.build_card_scaffold(fx["row"], raw_without)
        self.assertEqual(with_excerpt.markdown, without_excerpt.markdown)
        self.assertNotEqual(with_excerpt.prose_input["issue_or_reason"],
                            without_excerpt.prose_input["issue_or_reason"])

    def test_fda483_w2_and_official_pdf_link(self) -> None:
        fx = _load_input(self._NAME)
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        # W2 에 제조소·FEI·시설/유형·실사일
        self.assertIn("**제조소/업체**", card.markdown)
        self.assertIn("FEI 3015156709", card.markdown)
        self.assertIn("Outsourcing Facility", card.markdown)
        self.assertIn("**실사일**", card.markdown)
        # W8 공식원본 = 건별 483 PDF
        self.assertIn("/media/192439/download", card.markdown)
        # 금지 마크다운 부재
        self.assertEqual(cs.assert_no_forbidden_markdown(card.markdown), [])


def _callout_colors(md: str) -> list[str]:
    import re
    return re.findall(r'color="([a-z_]+)"', md)


class AdminL1VerifyTest(unittest.TestCase):
    """E2 — admin-action 듀얼링크가 raw.admin_l1_verify 를 존중.

    None(flag off=기본)=현행 seq→L1 단언(골든 불변) · "pass"=검증된 L1 · "fail"=L2 인덱스+⚠️.
    """
    _ROW = {"official_url": "https://www.data.go.kr/data/15058457/openapi.do"}

    def _official(self, raw):
        _info, official, fallback = cs._dual_links("admin-action", self._ROW, raw)
        return official, fallback

    def test_verify_none_is_current_behavior(self):
        official, fb = self._official({"ADM_DISPS_SEQ": "2026004188"})
        self.assertEqual(
            official,
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026004188")
        self.assertFalse(fb)

    def test_verify_pass_is_verified_l1(self):
        official, fb = self._official(
            {"ADM_DISPS_SEQ": "2026004188", "admin_l1_verify": "pass"})
        self.assertIn("getItem?dispsApplySeq=2026004188", official)
        self.assertFalse(fb)  # 검증됨 → ⚠️ 없음

    def test_verify_fail_demotes_to_l2_index(self):
        official, fb = self._official(
            {"ADM_DISPS_SEQ": "2026004188", "admin_l1_verify": "fail"})
        self.assertEqual(official, "https://nedrug.mfds.go.kr/pbp/CCBAO01")
        self.assertTrue(fb)  # 죽은 후보 → L2 인덱스 + ⚠️

    def test_no_seq_is_l2_index(self):
        official, fb = self._official({})
        self.assertEqual(official, "https://nedrug.mfds.go.kr/pbp/CCBAO01")
        self.assertTrue(fb)

    def test_footer_labels_l2_list_fallback(self):
        md = cs._footer_block("admin-action", self._ROW,
                              {"ADM_DISPS_SEQ": "2026004188", "admin_l1_verify": "fail"},
                              cs.DEFAULT_CONFIG)
        self.assertIn("📎 공식원본(목록)", md)

    def test_footer_labels_l2_dataset_fallback(self):
        self.assertEqual(
            cs._official_label("https://www.data.go.kr/data/15058457/openapi.do", True),
            "공식원본(데이터셋)")


class WebCardGoldenTest(unittest.TestCase):
    """P1 — `grm-web-card/v1` 카드 직렬화 골든 + 필드 소유권/verbatim/불변식.

    per-card 골든은 render_entry={} 로 직렬화(render_order/group_label=null) — 카드 고유
    필드만 동결한다. 브리프 단위 정렬(render_order/group_label)은 WebBriefGoldenTest 가 동결.
    """

    def test_each_webcard_byte_identical(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                wc = card.to_web_card({})
                got = json.dumps(wc, ensure_ascii=False, indent=2, sort_keys=True)
                path = os.path.join(GOLDEN, f"{name}.expected.webcard.json")
                if _UPDATE:
                    _write(path, got)
                    continue
                self.assertEqual(json.loads(got), json.loads(_read(path)),
                                 f"{name}: web-card JSON 이 golden 과 다름")

    def test_determinism_byte_for_byte(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                a = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({"render_order": 3})
                b = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({"render_order": 3})
                self.assertEqual(json.dumps(a, ensure_ascii=False, sort_keys=True),
                                 json.dumps(b, ensure_ascii=False, sort_keys=True))

    def test_llm_slots_empty_code_fields_set(self) -> None:
        # 필드 소유권: LLM 슬롯만 빈 placeholder, 코드 필드는 결정값.
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({})
                self.assertEqual(wc["title_issue"], "")
                self.assertEqual(wc["summary"], "")
                self.assertEqual(wc["key_facts"], [])
                self.assertEqual(wc["implication"], "")
                self.assertEqual(wc["checks"], [])
                # 코드 필드는 채워짐
                self.assertTrue(wc["agency"])
                self.assertTrue(wc["card_type"])
                self.assertIn(wc["category"],
                              {"Warning Letter", "Guidance", "Guideline", "Other"})
                self.assertIn(wc["evidence_level"], {"A", "B", "C"})
                self.assertIn(wc["signal_label"], {"High", "Med", "Low"})
                self.assertIsInstance(wc["signal_tier"], int)
                self.assertTrue(wc["facts"])

    def test_facts_verbatim_no_markup(self) -> None:
        # facts[].value = _w2_rows 값에서 백틱만 벗긴 verbatim. 마크업 부재(불변식 #6).
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                wc = card.to_web_card({})
                expected = [{"label": l, "value": cs._plain(v)}
                            for l, v in cs._w2_rows(card.kind, fx["row"], fx["raw"])]
                self.assertEqual(wc["facts"], expected)
                for f in wc["facts"]:
                    self.assertNotIn("`", f["value"])

    def test_headline_target_verbatim(self) -> None:
        # 비병합 headline_target = _headline_target(row) (제목과 동일 단일원천).
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({})
                self.assertEqual(wc["headline_target"], cs._headline_target(fx["row"]))

    def test_sources_match_dual_links(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                info, official, _fb = cs._dual_links(card.kind, fx["row"], fx["raw"])
                wc = card.to_web_card({})
                self.assertEqual(wc["sources"]["info_url"], info)
                self.assertEqual(wc["sources"]["official_url"], official)
                self.assertEqual(wc["sources"]["link_check"],
                                 {"info": "pending", "official": "pending"})

    def test_id_is_document_id_not_card_id(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({})
                self.assertEqual(wc["id"], fx["row"].get("document_id", ""))
                self.assertNotIn("::", wc["id"])

    def test_evidence_quote_invariant(self) -> None:
        # A ⟺ quotes 비지 않음(quote 소스 있을 때) · B/C → quotes==[].
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                wc = card.to_web_card({})
                if card.evidence == "A":
                    quote = cs._quote_source(card.kind, fx["raw"])
                    self.assertEqual(bool(wc["quotes"]), bool(quote))
                else:
                    self.assertEqual(wc["quotes"], [])

    def test_ko_translation_null_nonko_empty(self) -> None:
        # KO(MFDS) Evidence A → translation null. 비KO Evidence A → translation "".
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                if not (card.evidence == "A" and card.to_web_card({})["quotes"]):
                    continue
                lang = cs._language(fx["row"], card.kind)
                wc = card.to_web_card({})
                for q in wc["quotes"]:
                    if lang == "KO":
                        self.assertIsNone(q["translation"])
                    else:
                        self.assertEqual(q["translation"], "")

    def test_no_card_markup(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({})
                self.assertEqual(cs.assert_no_card_markup(wc), [])

    def test_modality_null_for_normative_kinds(self) -> None:
        for name in WEBCARD_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load_input(name)
                card = cs.build_card_scaffold(fx["row"], fx["raw"])
                wc = card.to_web_card({})
                if card.kind in cs._NORMATIVE_KINDS:
                    self.assertIsNone(wc["modality"])

    def test_render_entry_passthrough(self) -> None:
        fx = _load_input("guidance_fr")
        wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card(
            {"render_order": 7, "group_label": "💊 합성의약품"})
        self.assertEqual(wc["render_order"], 7)
        self.assertEqual(wc["group_label"], "💊 합성의약품")
        # 빈 group_label → null
        wc2 = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card(
            {"render_order": 7, "group_label": ""})
        self.assertIsNone(wc2["group_label"])


class WebMergedRecallTest(unittest.TestCase):
    """P1 §3.7 — 병합 대표 카드 web-card 골든 + merged_count/items/headline."""

    def _rep(self):
        rows = json.loads(_read(os.path.join(GOLDEN, "recall_merged.input.json")))["rows"]
        cards = cs.merge_recall_cards(_build_cards_from_rows(rows))
        return cards[0]

    def test_merged_webcard_byte_identical(self) -> None:
        wc = self._rep().to_web_card({})
        got = json.dumps(wc, ensure_ascii=False, indent=2, sort_keys=True)
        path = os.path.join(GOLDEN, "recall_merged.expected.webcard.json")
        if _UPDATE:
            _write(path, got)
            return
        self.assertEqual(json.loads(got), json.loads(_read(path)))

    def test_merged_fields(self) -> None:
        wc = self._rep().to_web_card({})
        self.assertEqual(wc["merged_count"], 3)
        self.assertEqual(wc["merged_items"],
                         ["아세트아미노펜정 500mg", "아세트아미노펜정 325mg",
                          "이부프로펜정 200mg"])
        self.assertIn("외 2품목", wc["headline_target"])
        prod = [f for f in wc["facts"] if f["label"] == "제품"]
        self.assertEqual(prod, [{"label": "제품", "value": "아세트아미노펜정 500mg 외 2품목"}])
        self.assertEqual(cs.assert_no_card_markup(wc), [])

    def test_non_merged_merged_count_one(self) -> None:
        fx = _load_input("recall_quality_chemical")
        wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card({})
        self.assertEqual(wc["merged_count"], 1)
        self.assertEqual(wc["merged_items"], [])


class WebBriefGoldenTest(unittest.TestCase):
    """P1 §3.2 — assemble_web_brief 골든 + 브리프 불변식(정렬·제외·집계)."""

    def _cards(self) -> list:
        return cs.merge_recall_cards(
            [cs.build_card_scaffold(_load_input(n)["row"], _load_input(n)["raw"])
             for n in FIXTURES])

    def _brief(self) -> dict:
        return cs.assemble_web_brief(self._cards(), WEB_BRIEF_META)

    def test_brief_web_byte_identical(self) -> None:
        brief = self._brief()
        got = json.dumps(brief, ensure_ascii=False, indent=2, sort_keys=True)
        path = os.path.join(GOLDEN, "brief_web.expected.json")
        if _UPDATE:
            _write(path, got)
            return
        self.assertEqual(json.loads(got), json.loads(_read(path)))

    def test_schema_and_brief_meta(self) -> None:
        brief = self._brief()
        self.assertEqual(brief["schema_version"], "grm-web-card/v1")
        b = brief["brief"]
        self.assertEqual(b["run_date_kst"], "2026-06-22")
        self.assertEqual(b["window"], "2026-06-15 ~ 2026-06-22")
        self.assertEqual(b["tldr"], [])           # LLM placeholder
        self.assertTrue(b["ai_disclosure"])
        self.assertEqual(b["coverage"]["intake_total"], len(FIXTURES))
        self.assertEqual(b["coverage"]["rendered"], len(brief["cards"]))
        # 면책 정식 문안은 JSON 에 미포함(렌더러 보유)
        self.assertNotIn("disclaimer", json.dumps(brief, ensure_ascii=False))

    def test_render_order_monotonic(self) -> None:
        ro = [c["render_order"] for c in self._brief()["cards"]]
        self.assertEqual(ro, sorted(ro))
        self.assertTrue(all(isinstance(x, int) for x in ro))

    def test_watch_and_merged_members_excluded(self) -> None:
        cards = self._cards()
        brief = cs.assemble_web_brief(cards, WEB_BRIEF_META)
        ids = {c["id"] for c in brief["cards"]}
        # watch(legislative) 제외
        self.assertNotIn(_load_input("legislative_notice")["row"]["document_id"], ids)
        # 병합 멤버 제외 — recall_merged 멤버 추가해 확인
        merged_rows = json.loads(
            _read(os.path.join(GOLDEN, "recall_merged.input.json")))["rows"]
        merged = cs.merge_recall_cards(_build_cards_from_rows(merged_rows))
        brief2 = cs.assemble_web_brief(merged, WEB_BRIEF_META)
        self.assertEqual(len(brief2["cards"]), 1)  # 대표 1장만

    def test_brief_order_matches_render_plan(self) -> None:
        # 단일원천: assemble_web_brief 카드의 render_order 가 compute_render_plan 의
        # (watch 제외) render_order 집합과 일치하고 엄격 증가(중복 없음).
        cards = self._cards()
        plan = cs.compute_render_plan(cards)
        non_watch_orders = sorted(
            plan[c.card_id]["render_order"] for c in cards
            if not c.merged_into and c.section != "watch")
        brief = cs.assemble_web_brief(cards, WEB_BRIEF_META)
        ro = [c["render_order"] for c in brief["cards"]]
        self.assertEqual(ro, non_watch_orders)        # plan 순서와 동일(단일원천)
        self.assertEqual(ro, sorted(set(ro)))         # 엄격 증가(중복 없음)

    def test_no_card_markup_in_brief(self) -> None:
        for c in self._brief()["cards"]:
            self.assertEqual(cs.assert_no_card_markup(c), [])


class Brief2026_06_22WebFixtureTest(unittest.TestCase):
    """P1 §4-1 — 실-6/22 web-card 픽스처(`brief_web_2026_06_22.json`) 회귀.

    빌더(`build_brief_web_2026_06_22.py`)가 36 동결 scaffold markdown 을 파싱한 결과를
    동결한다. 핵심 보증: 각 카드의 facts 가 scaffold W2 셀과 **글자 단위 동일**(PL18 의미 보존).
    """

    _FIX = os.path.join(os.path.dirname(__file__), "fixtures", "brief_web_2026_06_22.json")
    _HANDOFF = os.path.join(os.path.dirname(__file__), "fixtures",
                            "handoff_rows_2026_06_22.json")

    def _load(self) -> dict:
        return json.loads(_read(self._FIX))

    def _builder(self):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), "fixtures",
                            "build_brief_web_2026_06_22.py")
        spec = importlib.util.spec_from_file_location("build_brief_web", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_fixture_matches_builder_output(self) -> None:
        # 동결된 픽스처 == 빌더 재실행 결과(결정론 freeze).
        fixture = self._load()
        built = self._builder().build()
        self.assertEqual(built, fixture, "brief_web_2026_06_22.json 이 빌더 출력과 다름")

    def test_schema_and_card_count(self) -> None:
        d = self._load()
        self.assertEqual(d["schema_version"], "grm-web-card/v1")
        self.assertEqual(len(d["cards"]), 36)
        self.assertEqual(d["brief"]["coverage"]["intake_total"], 36)
        ro = [c["render_order"] for c in d["cards"]]
        self.assertEqual(ro, list(range(36)))   # 6/22 = watch/병합 없음 → 0..35 연속

    def test_no_card_markup(self) -> None:
        for c in self._load()["cards"]:
            self.assertEqual(cs.assert_no_card_markup(c), [],
                             f"{c['id']}: 표현 틀 마크업 잔존")

    def test_facts_verbatim_match_scaffold_cells(self) -> None:
        # PL18 보증: web facts[].value == scaffold W2 셀(백틱 제거) 글자 단위 동일.
        scaffolds = {r["document_id"]: r["card_scaffold"]
                     for r in json.loads(_read(self._HANDOFF))}
        for c in self._load()["cards"]:
            with self.subTest(doc=c["id"]):
                md = scaffolds[c["id"]]
                expected = []
                for ln in md.split("\n"):
                    m = re.match(r"<tr><td>\*\*(.+?)\*\*</td><td>(.*)</td></tr>$", ln)
                    if m:
                        expected.append({"label": m.group(1),
                                         "value": cs._plain(m.group(2))})
                self.assertEqual(c["facts"], expected)

    def test_evidence_matches_scaffold_badge(self) -> None:
        scaffolds = {r["document_id"]: r["card_scaffold"]
                     for r in json.loads(_read(self._HANDOFF))}
        for c in self._load()["cards"]:
            with self.subTest(doc=c["id"]):
                badge = re.search(r"`Evidence ([ABC])`", scaffolds[c["id"]]).group(1)
                self.assertEqual(c["evidence_level"], badge)

    def test_quotes_verbatim_present_in_scaffold(self) -> None:
        # 인용 원문(마커 제거)의 각 줄이 scaffold 에 그대로 존재(전사 무결성, 비순환 가드).
        scaffolds = {r["document_id"]: r["card_scaffold"]
                     for r in json.loads(_read(self._HANDOFF))}
        for c in self._load()["cards"]:
            md = scaffolds[c["id"]]
            for q in c["quotes"]:
                self.assertIsInstance(q["original"], str)
                for line in q["original"].split("\n"):
                    if line.strip():
                        self.assertIn(line, md,
                                      f"{c['id']}: 인용 줄이 scaffold 에 없음: {line!r}")

    def test_source_urls_verbatim_present_in_scaffold(self) -> None:
        # info/official URL 이 scaffold 링크에 글자 단위 존재 — URL 조기 절단 회귀 차단.
        scaffolds = {r["document_id"]: r["card_scaffold"]
                     for r in json.loads(_read(self._HANDOFF))}
        for c in self._load()["cards"]:
            md = scaffolds[c["id"]]
            for key in ("info_url", "official_url"):
                url = c["sources"][key]
                if url:
                    self.assertIn(f"]({url})", md,
                                  f"{c['id']}: {key} 가 scaffold 와 불일치(절단?): {url}")


class CategoryMappingTest(unittest.TestCase):
    """Codex 보정 #1 — _category 전 발현 kind 망라 + 휴면 gmp-guideline 가드(죽은 매핑 금지)."""

    # resolve_kind 가 실제 낼 수 있는 전 내부 kind → 기대 Notion 카테고리.
    _EXPECTED = {
        "warning-letter": "Warning Letter",
        "guidance": "Guidance", "mfds-notice": "Guidance",
        "regulation": "Guidance", "legislative": "Guidance",
        "ich": "Guideline",
        "openfda-recall": "Other", "hc-recall": "Other", "fda-483": "Other",
        "who-noc": "Other", "who-inspection": "Other", "who-news": "Other",
        "admin-action": "Other", "recall-quality": "Other",
        "gmp-inspection": "Other", "gmp-certificate": "Other",
        "safety-letter": "Other", "rss-news": "Other",
    }

    def test_all_emergeable_kinds_map_to_notion_set(self) -> None:
        for kind, expected in self._EXPECTED.items():
            with self.subTest(kind=kind):
                self.assertEqual(cs._category(kind), expected)
                self.assertIn(cs._category(kind),
                              {"Warning Letter", "Guidance", "Guideline", "Other"})

    def test_no_dead_gmp_guideline_key(self) -> None:
        # §3.4 표기와 달리 죽은 매핑을 넣지 않는다(휴면 Type + resolve_kind 분기 부재).
        self.assertNotIn("gmp-guideline", cs._CATEGORY_MAP)

    def test_dormant_gmp_guideline_type_absorbs_to_mfds_notice_guidance(self) -> None:
        # 휴면 Type 이 인입돼도 Other 로 새지 않고 mfds-notice→Guidance 로 흡수됨을 고정.
        row = {"source": cs.SOURCE_MFDS, "type_or_class": "gmp-guideline",
               "document_id": "mfds-gmpg-1", "date": "2026-06-01"}
        self.assertEqual(cs.resolve_kind(row), "mfds-notice")
        self.assertEqual(cs._category("mfds-notice"), "Guidance")

    def test_category_keys_subset_of_emergeable_kinds(self) -> None:
        # _CATEGORY_MAP 의 모든 키는 실제 발현 가능한 kind 여야 한다(죽은 키 0).
        for k in cs._CATEGORY_MAP:
            self.assertIn(k, self._EXPECTED, f"발현 불가 kind 가 _CATEGORY_MAP 에: {k}")


class OfficialIsPdfTest(unittest.TestCase):
    """Codex 보정 #2 — _official_is_pdf 쿼리/프래그먼트 경계 고정(코드 무변경, 테스트만)."""

    def test_true_cases(self) -> None:
        for u in (
            "https://www.fda.gov/media/192438/download",
            "https://x/doc.pdf",
            "https://x/doc.pdf?download=1",
            "https://x/doc.pdf#page=2",
            "https://x/file/download?x=1",
            "https://x/DOC.PDF",
        ):
            self.assertTrue(cs._official_is_pdf(u), u)

    def test_false_cases(self) -> None:
        for u in (
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026004434",
            "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-"
            "investigations/compliance-actions-and-activities/warning-letters",
            "https://x/doc.pdfx",      # 꼬리 오탐 방지
            "https://x/downloadx",
            "",
            None,
        ):
            self.assertFalse(cs._official_is_pdf(u), repr(u))


class SignalDerivationTest(unittest.TestCase):
    """_signal_level(단일원천 _signal_badge 파생)·_signal_tier_num 경계 고정."""

    def test_signal_level(self) -> None:
        self.assertEqual(cs._signal_level("Tier 3"), "High")
        self.assertEqual(cs._signal_level("Tier 2"), "Med")
        self.assertEqual(cs._signal_level("Tier 1"), "Low")
        self.assertEqual(cs._signal_level("bogus"), "Low")   # 폴백 = Signal Low (T1)
        self.assertEqual(cs._signal_level(""), "Low")

    def test_signal_tier_num(self) -> None:
        self.assertEqual(cs._signal_tier_num("Tier 3"), 3)
        self.assertEqual(cs._signal_tier_num("Tier 1"), 1)
        self.assertEqual(cs._signal_tier_num(""), 1)
        self.assertEqual(cs._signal_tier_num("bogus"), 1)


class DeepAnalysisReadyTest(unittest.TestCase):
    """[WL 심층분석 fan-out 2026-07-01] 7번째·선택 슬롯 additive 회귀.

    deep_analysis_ready/deep_analysis 는 warning-letter + raw.wl_body_full 확보 시만
    True/키존재이고, 그 외 모든 카드(전 유형·전문 미확보 WL 포함)는 기존 20+ golden
    fixture 와 완전히 동일한 출력을 낸다(이 클래스는 golden 비교를 건드리지 않는다 —
    신규 동작만 별도로 확인).
    """

    def test_warning_letter_without_body_full_is_not_ready(self) -> None:
        fx = _load_input("warning_letter_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])
        self.assertFalse(card.deep_analysis_ready)
        webcard = card.to_web_card()
        self.assertNotIn("deep_analysis", webcard)          # 대다수 카드 — 키 자체 부재
        self.assertNotIn("deep_analysis_ready", card.to_dict())
        self.assertNotIn("deep_analysis_input", card.to_dict())

    def test_warning_letter_with_body_full_is_ready(self) -> None:
        fx = _load_input("warning_letter_excerpt")
        raw = dict(fx["raw"])
        raw["wl_body_full"] = ("During our inspection, we observed violations of 21 CFR "
                               "211.192. Required remediation: respond within 15 days.")
        card = cs.build_card_scaffold(fx["row"], raw)
        self.assertTrue(card.deep_analysis_ready)

        webcard = card.to_web_card()
        self.assertIn("deep_analysis", webcard)
        self.assertIsNone(webcard["deep_analysis"])          # 병합 전 placeholder

        d = card.to_dict()
        self.assertTrue(d["deep_analysis_ready"])
        self.assertEqual(d["deep_analysis_input"]["body_full"], raw["wl_body_full"])
        # 6종 동결 슬롯 토큰 목록(needs_llm_slots)은 이 신규 필드로 오염되지 않는다.
        self.assertNotIn("deep_analysis", card.needs_llm_slots)

    def test_non_deep_kinds_never_ready(self) -> None:
        # recall/gmp 등 비대상 유형은 raw 에 wl_body_full 이 있어도(방어적 입력) False.
        fx = _load_input("recall_quality_chemical")
        raw = dict(fx["raw"])
        raw["wl_body_full"] = "irrelevant"
        card = cs.build_card_scaffold(fx["row"], raw)
        self.assertFalse(card.deep_analysis_ready)
        self.assertNotIn("deep_analysis", card.to_web_card())

    # ── [소스확장 2026-07-02] MFDS 행정처분(admin-action) — WL 과 동형(admin_body_full 게이트) ──
    def test_admin_action_without_body_full_is_not_ready(self) -> None:
        fx = _load_input("admin_action_chemical")
        card = cs.build_card_scaffold(fx["row"], fx["raw"])   # 픽스처엔 admin_body_full 없음
        self.assertFalse(card.deep_analysis_ready)
        self.assertNotIn("deep_analysis", card.to_web_card())  # golden 불변(키 부재)
        self.assertNotIn("deep_analysis_ready", card.to_dict())

    def test_admin_action_with_body_full_is_ready(self) -> None:
        fx = _load_input("admin_action_chemical")
        raw = dict(fx["raw"])
        raw["admin_body_full"] = ("제조기록서를 사실과 다르게 작성해 약사법 제38조제1항을 위반함. "
                                  "처분명: 제조업무정지 1개월. 적용법령: [별표8] 행정처분 기준.")
        card = cs.build_card_scaffold(fx["row"], raw)
        self.assertTrue(card.deep_analysis_ready)

        webcard = card.to_web_card()
        self.assertIn("deep_analysis", webcard)
        self.assertIsNone(webcard["deep_analysis"])            # 병합 전 placeholder

        d = card.to_dict()
        self.assertTrue(d["deep_analysis_ready"])
        self.assertEqual(d["deep_analysis_input"]["body_full"], raw["admin_body_full"])
        self.assertNotIn("deep_analysis", card.needs_llm_slots)

    def test_admin_action_with_wrong_body_key_not_ready(self) -> None:
        # admin 은 admin_body_full 이 있어야 함 — wl_body_full(오키) 만으론 False.
        fx = _load_input("admin_action_chemical")
        raw = dict(fx["raw"])
        raw["wl_body_full"] = "irrelevant"
        card = cs.build_card_scaffold(fx["row"], raw)
        self.assertFalse(card.deep_analysis_ready)


class DeterministicDetailTest(unittest.TestCase):
    """[상세보기 결정론 승격 2026-07-02 · spec §16] 결정론 상세 슬롯 additive 회귀.

    deterministic_detail 은 gmp-inspection + raw.gmp_deficiencies 확보 시만 키가 존재하고,
    그 외 모든 카드(전 유형·표 미확보 gmp 포함)는 키 자체가 없어 기존 20+ golden 바이트 불변.
    WL deep_analysis(LLM 분석층)와 완전 별개 필드 — 서로 오염시키지 않는다.
    """

    def test_periodic_emits_deterministic_detail(self):
        fx = _load_input("gmp_inspection_periodic")
        wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card()
        self.assertIn("deterministic_detail", wc)
        dd = wc["deterministic_detail"]
        self.assertEqual(dd["type"], "gmp_deficiencies")
        self.assertEqual(dd["count"], 3)
        self.assertEqual(dd["severity_summary"], {"기타": 2, "중요": 1})
        self.assertEqual([r["area"] for r in dd["rows"]], ["시설장비", "제조", "품질"])
        # 결정론 층은 deep_analysis(LLM 분석층)와 상호 오염 없음.
        self.assertNotIn("deep_analysis", wc)

    def test_pre_market_has_no_detail(self):
        fx = _load_input("gmp_inspection_pre_market")
        wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card()
        self.assertNotIn("deterministic_detail", wc)

    def test_gmp_without_deficiencies_has_no_detail(self):
        # 기존 gmp fixture(gmp_deficiencies 키 부재) → 필드 없음(golden 불변 근거).
        fx = _load_input("gmp_inspection_biologic")
        wc = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card()
        self.assertNotIn("deterministic_detail", wc)

    def test_empty_or_invalid_rows_yield_no_detail(self):
        fx = _load_input("gmp_inspection_periodic")
        # 빈 배열 → 필드 없음.
        raw = dict(fx["raw"]); raw["gmp_deficiencies"] = []
        self.assertNotIn("deterministic_detail",
                         cs.build_card_scaffold(fx["row"], raw).to_web_card())
        # 근거법령·지적내용 둘 다 빈 행만 → 방어 필터로 제거 → 필드 없음.
        raw2 = dict(fx["raw"])
        raw2["gmp_deficiencies"] = [{"area": "제조", "severity": "기타",
                                     "legal_basis": "", "summary": "", "followup": "x"}]
        self.assertNotIn("deterministic_detail",
                         cs.build_card_scaffold(fx["row"], raw2).to_web_card())

    def test_non_gmp_kind_never_emits_detail(self):
        # 방어적 입력(다른 유형 raw 에 gmp_deficiencies) — gmp-inspection 만 산출.
        fx = _load_input("recall_quality_chemical")
        raw = dict(fx["raw"])
        raw["gmp_deficiencies"] = [{"area": "제조", "severity": "중대",
                                    "legal_basis": "[별표1] 1호",
                                    "summary": "x", "followup": ""}]
        self.assertNotIn("deterministic_detail",
                         cs.build_card_scaffold(fx["row"], raw).to_web_card())

    def test_deterministic_detail_row_keys_exact(self):
        fx = _load_input("gmp_inspection_periodic")
        dd = cs.build_card_scaffold(fx["row"], fx["raw"]).to_web_card()["deterministic_detail"]
        for r in dd["rows"]:
            self.assertEqual(set(r), {"area", "severity", "legal_basis",
                                      "summary", "followup"})


if __name__ == "__main__":
    unittest.main()
