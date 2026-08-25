"""WHO 공개 실사보고서(WHOPIR) 구조화 상세 — 수집 → 카드 → 번역 → 병합 → 렌더 회귀.

배경(2026-07-27 사용자 지적): "WHO 공개실사보고서에 내용이 PDF로 상당히 많고 잘 정리된 것
같은데 지금 브리프에는 전혀 읽어내지 못하고 있음." 실제로 WHOPIR PDF 는
[Part 2 활동범위·항목별 요약(최대 22항목) → Part 3 결론] 으로 잘 정돈돼 있는데, 카드에는
링크와 1,500자 excerpt 만 실려 그 구조가 통째로 유실됐다.

체인: collect_who.extract_whopir_report → card_scaffold._detail_whopir_report →
      translation_fields() → build_translation_jobs → assemble_translation_deltas →
      inject_slots._merge_whopir_translations → card.html
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_scaffold as cs
import deep_analysis_fanout as fan
import inject_slots as inj

_REPORT = {
    "type": "whopir_report", "report_kind": "findings",
    "outcome": ("Based on the areas inspected, the manufacturer was considered to be "
                "operating at an acceptable level of compliance with WHO GMP."),
    "sections": [
        {"no": "1", "title": "Quality System",
         "text": "The quality manual was reviewed and found to be current."},
        {"no": "2", "title": "Production System",
         "text": "Line clearance was observed during the inspection."},
    ],
}
_RELIANCE = {
    "type": "whopir_report", "report_kind": "reliance",
    "outcome": "Reliance was placed on the inspections listed above.",
    "reliance": [{"authority": "EDQM", "dates": "12-15 March 2025"}],
}


def _detail(report=None):
    return cs._detail_whopir_report({}, {"whopir_report": report or _REPORT})


class DetailBuilderTest(unittest.TestCase):
    """`_detail_whopir_report` — verbatim 적재(생성 0). 못 읽은 건 안 싣는다."""

    def test_findings_detail_carries_outcome_and_sections(self):
        dd = _detail()
        self.assertEqual(dd["type"], "whopir_report")
        self.assertEqual(dd["report_kind"], "findings")
        self.assertEqual([s["no"] for s in dd["sections"]], [1, 2])
        self.assertEqual(dd["sections"][0]["title"], "Quality System")
        self.assertNotIn("reliance", dd)

    def test_reliance_detail_has_no_invented_sections(self):
        dd = _detail(_RELIANCE)
        self.assertEqual(dd["report_kind"], "reliance")
        self.assertEqual(dd["sections"], [])
        self.assertEqual(dd["reliance"][0]["authority"], "EDQM")

    def test_missing_or_empty_report_yields_no_detail(self):
        self.assertIsNone(cs._detail_whopir_report({}, {}))
        self.assertIsNone(cs._detail_whopir_report({}, {"whopir_report": "not a dict"}))
        self.assertIsNone(cs._detail_whopir_report(
            {}, {"whopir_report": {"type": "whopir_report", "outcome": "",
                                   "sections": []}}))

    def test_sections_without_text_are_dropped(self):
        dd = _detail({"type": "whopir_report", "report_kind": "findings",
                      "outcome": "ok",
                      "sections": [{"no": "1", "title": "T", "text": "  "},
                                   {"no": "2", "title": "U", "text": "body"}]})
        self.assertEqual([s["no"] for s in dd["sections"]], [2])

    def test_registered_on_who_inspection_kind(self):
        """`SourceSpec.detail` 배선 — 배선이 빠지면 상세가 영원히 안 뜬다."""
        self.assertIs(cs._spec("who-inspection").detail, cs._detail_whopir_report)


class TranslationInputTest(unittest.TestCase):
    """필드명 계약(`whopir_translation_input`) — 방출·병합이 같은 함수를 본다."""

    def test_keys_are_outcome_and_section_numbers(self):
        got = cs.whopir_translation_input(_detail())
        self.assertEqual(set(got), {"outcome", "s1_title", "s1", "s2_title", "s2"})
        self.assertEqual(got["s2"], _REPORT["sections"][1]["text"])

    def test_keys_follow_source_numbering_not_position(self):
        """항목 번호가 건너뛰어도(원문 6·7만 확보) 키는 원문 번호를 따른다.

        위치 인덱스였다면 국문이 다른 항목에 붙는다(2026-07-21 findings 교차연결 사고와 같은 종).
        """
        dd = _detail({"type": "whopir_report", "report_kind": "findings", "outcome": "",
                      "sections": [{"no": "6", "title": "A", "text": "x"},
                                   {"no": "7", "title": "B", "text": "y"}]})
        self.assertEqual(set(cs.whopir_translation_input(dd)),
                         {"s6_title", "s6", "s7_title", "s7"})

    def test_emitted_input_matches_published_detail_verbatim(self):
        """번역 입력 = 발행 카드에 실릴 원문과 글자 단위 동일(같은 producer)."""
        dd = _detail()
        got = cs.whopir_translation_input(dd)
        self.assertEqual(got["outcome"], dd["outcome"])
        for sec in dd["sections"]:
            self.assertEqual(got[f"s{sec['no']}"], sec["text"])


class ScaffoldEmissionTest(unittest.TestCase):
    """WHOPIR 카드도 NCR 과 **같은 번역 채널**을 탄다(와이어 키 `ncr_translation_*`)."""

    def _card(self):
        row = {"source": "WHO", "document_id": "who-whopir-abc",
               "type_or_class": "WHO Inspection", "headline": "[WHOPIR] Site Z",
               "official_url": "https://extranet.who.int/x", "date_iso": "2026-07-20",
               "firm": "Site Z", "signal_tier": "Tier 2", "evidence_level": "B"}
        return cs.build_card_scaffold(row, {"whopir_report": _REPORT,
                                            "pdf_url": "https://who.int/a.pdf"})

    def test_translation_fields_emitted(self):
        fields = self._card().translation_fields()
        self.assertTrue(fields.get("ncr_translation_ready"))
        self.assertEqual(fields.get("kind"), "who-inspection")
        self.assertIn("s1", fields["ncr_translation_input"])

    def test_not_a_deep_analysis_card(self):
        """번역만 필요하다 — 심층분석 4섹션을 억지로 생성하게 만들지 않는다."""
        self.assertEqual(self._card().deep_fields(), {})

    def test_card_without_report_emits_nothing(self):
        row = {"source": "WHO", "document_id": "who-whopir-x",
               "type_or_class": "WHO Inspection", "headline": "[WHOPIR] Y",
               "official_url": "https://extranet.who.int/y", "date_iso": "2026-07-20"}
        card = cs.build_card_scaffold(row, {"whopir_excerpt": "deficiencies …"})
        self.assertEqual(card.translation_fields(), {})


class FanoutTest(unittest.TestCase):
    HANDOFF = {"cards": [{
        "card_id": "WHO::who-whopir-abc", "kind": "who-inspection",
        "ncr_translation_ready": True,
        "ncr_translation_input": {"outcome": "Acceptable.", "s1_title": "Quality System",
                                  "s1": "The quality manual was reviewed."}}]}

    def test_job_built_for_whopir_card(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        self.assertEqual([j.document_id for j in jobs], ["who-whopir-abc"])
        self.assertEqual(set(jobs[0].fields), {"outcome", "s1_title", "s1"})

    def test_delta_assembled_on_shared_channel(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        out = fan.assemble_translation_deltas(
            jobs, {"who-whopir-abc": {"outcome_ko": "적합 수준.",
                                      "s1_title_ko": "품질 시스템",
                                      "s1_ko": "품질 매뉴얼을 검토했다."}})
        self.assertEqual(out["who-whopir-abc"]["ncr_ko"]["s1_ko"], "품질 매뉴얼을 검토했다.")


class MergeTest(unittest.TestCase):
    def _card(self):
        return {"id": "who-whopir-abc", "deterministic_detail": _detail()}

    def test_merges_outcome_and_sections(self):
        card = self._card()
        inj._merge_ncr_translations(
            card, {"outcome_ko": "적합 수준.", "s1_ko": "품질 매뉴얼을 검토했다.",
                   "s1_title_ko": "품질 시스템"}, inj.InjectionReport(), "x")
        dd = card["deterministic_detail"]
        self.assertEqual(dd["outcome_ko"], "적합 수준.")
        self.assertEqual(dd["sections"][0]["text_ko"], "품질 매뉴얼을 검토했다.")
        self.assertEqual(dd["sections"][0]["title_ko"], "품질 시스템")
        self.assertNotIn("text_ko", dd["sections"][1], "짝 없는 항목에 국문이 붙었다")
        self.assertEqual(dd["sections"][0]["text"],
                         _REPORT["sections"][0]["text"], "원문이 훼손됐다")

    def test_section_numbers_match_not_positions(self):
        dd = _detail({"type": "whopir_report", "report_kind": "findings", "outcome": "",
                      "sections": [{"no": "6", "title": "A", "text": "x"},
                                   {"no": "7", "title": "B", "text": "y"}]})
        card = {"id": "z", "deterministic_detail": dd}
        inj._merge_ncr_translations(card, {"s7_ko": "국문 7"}, inj.InjectionReport(), "z")
        self.assertNotIn("text_ko", dd["sections"][0])
        self.assertEqual(dd["sections"][1]["text_ko"], "국문 7")

    def test_pairless_translation_skipped(self):
        card = self._card()
        inj._merge_ncr_translations(card, {"s9_ko": "없는 항목의 국문"},
                                    inj.InjectionReport(), "x")
        for sec in card["deterministic_detail"]["sections"]:
            self.assertNotIn("text_ko", sec)

    def test_ncr_cards_unaffected(self):
        card = {"id": "186339", "deterministic_detail": {
            "type": "eu_gmp_ncr_statement", "nature": "Critical deficiencies."}}
        inj._merge_ncr_translations(card, {"nature_ko": "중대결함."},
                                    inj.InjectionReport(), "x")
        self.assertEqual(card["deterministic_detail"]["nature_ko"], "중대결함.")

    def test_routed_through_inject_deep_analysis(self):
        brief = {"cards": [self._card()]}
        rep = inj.inject_deep_analysis(
            brief, {"who-whopir-abc": {"ncr_ko": {"outcome_ko": "적합 수준."}}})
        self.assertEqual(
            brief["cards"][0]["deterministic_detail"]["outcome_ko"], "적합 수준.")
        self.assertFalse(rep.errors)


class WhopirKoGateTest(unittest.TestCase):
    """[게이트 7 + 배포 fail-closed, 2026-08-25] 상세를 확보하고도 국문 병기가 빠진 채
    영문 단독으로 조용히 degrade 하던 마지막 구멍 — 483·WL 과 같은 2겹으로 소리 나게 막는다."""

    def _incomplete_card(self):
        return {"id": "who-whopir-abc", "deterministic_detail": _detail()}

    def _complete_card(self):
        dd = _detail()
        dd["outcome_ko"] = "적합 수준으로 판정됐다."
        for s in dd["sections"]:
            s["text_ko"] = "국문 요약."
            s["title_ko"] = "국문 표제"
        return {"id": "who-whopir-abc", "deterministic_detail": dd}

    def test_assemble_gate_flags_missing_ko(self):
        import assemble_publish_brief as apb
        errs = apb._lint_whopir_ko([self._incomplete_card()])
        # outcome_ko 1 + 섹션 2개 × (text_ko·title_ko) = 5건
        self.assertEqual(len(errs), 5)
        self.assertTrue(all("국문 병기 결손" in e for e in errs))

    def test_assemble_gate_passes_when_complete(self):
        import assemble_publish_brief as apb
        self.assertEqual(apb._lint_whopir_ko([self._complete_card()]), [])

    def test_assemble_gate_ignores_link_cards_and_reliance_rows(self):
        import assemble_publish_brief as apb
        rel = cs._detail_whopir_report({}, {"whopir_report": _RELIANCE})
        rel["outcome_ko"] = "인용 실사에 근거한 판정."
        cards = [
            {"id": "a", "card_type": "WHO"},                       # 상세 없는 링크 카드
            {"id": "b", "deterministic_detail": rel},              # reliance 행은 국문 요구 없음
        ]
        self.assertEqual(apb._lint_whopir_ko(cards), [])

    def _render_mod(self):
        import pathlib
        web_dir = pathlib.Path(__file__).resolve().parent.parent / "web"
        sys.path.insert(0, str(web_dir))
        import render  # noqa: E402
        return render

    def test_render_validate_flags_missing_ko(self):
        render = self._render_mod()
        brief = {"brief": {"publish_date": "2026-08-24"},
                 "cards": [self._incomplete_card()]}
        out = render.validate_whopir_ko([brief])
        self.assertEqual(len(out), 5)
        self.assertTrue(any("MISSING_OUTCOME_KO" in v for v in out))
        self.assertTrue(any("MISSING_SECTION_TEXT_KO" in v for v in out))
        self.assertTrue(any("MISSING_SECTION_TITLE_KO" in v for v in out))
        self.assertIn("2026-08-24", out[0])

    def test_render_validate_passes_when_complete(self):
        render = self._render_mod()
        self.assertEqual(render.validate_whopir_ko([self._complete_card()]), [])


if __name__ == "__main__":
    unittest.main()
