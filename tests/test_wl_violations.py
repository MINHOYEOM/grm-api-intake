"""WL 위반항목 결정론 층 + 거짓 부재 서술 차단 게이트 회귀 — 2026-07-20 발행 사고 대응.

사고 요약(재발 방지 대상):
  수집기는 Warning Letter 원문 전문(2만자·조항별 위반 3~5건)을 정상 확보했는데, 발행된 카드는
  "세부 위반내용은 원문에 명시되지 않았다"고 말했다. 세 겹의 결함이 겹쳤다.
    ① WL 은 결정론 상세층이 없어 상세 경로가 deep_analysis fan-out(LLM) 하나뿐이었다.
    ② 그 fan-out 이 안 돈 주, 폴백인 6슬롯 LLM 에게 전달된 입력은 300자 문장경계 절단을 거쳐
       "…violations including, but not limited to, the following." 도입구 **118자뿐**이었다.
    ③ 그렇게 나온 거짓 서술을 아무 게이트도 막지 않았다.
  이 파일은 ①~③ 각각의 수리를 고정한다.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assemble_publish_brief as apb  # noqa: E402
import card_scaffold as cs  # noqa: E402
import collect_intake as ci  # noqa: E402

# 실제 FDA cGMP Warning Letter 본문 형태(2026-07-20 발행분 2건에서 확인된 구조).
_LEADIN = ("During our inspection, our investigators observed specific violations "
           "including, but not limited to, the following. ")
_V1 = ("1. Your firm failed to thoroughly investigate any unexplained discrepancy or "
       "failure of a batch to meet any of its specifications (21 CFR 211.192). "
       "For example, you inadequately investigated multiple TNTC microbial findings. ")
_V2 = ("2. Your firm failed to establish and follow appropriate written procedures "
       "designed to prevent microbiological contamination (21 CFR 211.42(c)(10) and "
       "21 CFR 211.113(b)). Your filling line lacks a physical barrier. ")
_V3 = ("3. Your firm failed to use equipment of appropriate design (21 CFR 211.63). "
       "Storage tank interior surfaces were covered with dark plaques. ")
_BODY = _LEADIN + _V1 + _V2 + _V3


class ExtractWlViolationsTest(unittest.TestCase):
    """`extract_wl_violations_from_text` — 표제 3신호(도입부·조항인용·종결) 판별."""

    def test_parses_numbered_violations_in_order(self):
        got = ci.extract_wl_violations_from_text(_BODY)
        self.assertEqual([v["number"] for v in got], ["1", "2", "3"])
        self.assertEqual(got[0]["citation"], "21 CFR 211.192")
        self.assertEqual(got[2]["citation"], "21 CFR 211.63")

    def test_multi_citation_heading_keeps_both_clauses(self):
        # 한 표제가 두 조항을 함께 인용하는 실제 형태 — 괄호 중첩에도 둘 다 살아야 한다.
        got = ci.extract_wl_violations_from_text(_BODY)
        self.assertEqual(got[1]["citation"],
                         "21 CFR 211.42(c)(10) · 21 CFR 211.113(b)")

    def test_statement_is_verbatim_and_ends_at_citation(self):
        # statement 는 원문 그대로(요약·의역 0)이며 인용 괄호 뒤 마침표에서 끊는다.
        got = ci.extract_wl_violations_from_text(_BODY)
        self.assertTrue(got[0]["statement"].startswith("Your firm failed to thoroughly"))
        self.assertTrue(got[0]["statement"].endswith("(21 CFR 211.192)."))
        self.assertNotIn("For example", got[0]["statement"])

    def test_footnote_number_is_not_a_violation(self):
        # "5. Your response is inadequate." 형태의 각주/응답 문단은 표제가 아니다
        # (도입부가 failed/did not 계열이 아니라서 걸러진다).
        text = _BODY + "5. Your response is inadequate. Your proposal does not address it. "
        got = ci.extract_wl_violations_from_text(text)
        self.assertEqual([v["number"] for v in got], ["1", "2", "3"])

    def test_heading_without_cfr_citation_is_skipped(self):
        text = _LEADIN + "1. Your firm failed to keep adequate records. Nothing further. "
        self.assertEqual(ci.extract_wl_violations_from_text(text), [])

    def test_out_of_order_number_is_dropped(self):
        # 번호 역행은 각주 오인식 신호 — 버린다(과소추출 우선).
        text = _LEADIN + _V2 + _V1
        got = ci.extract_wl_violations_from_text(text)
        self.assertEqual([v["number"] for v in got], ["2"])

    def test_empty_and_unnumbered_text_yield_empty(self):
        self.assertEqual(ci.extract_wl_violations_from_text(""), [])
        self.assertEqual(
            ci.extract_wl_violations_from_text(
                "This warning letter concerns unapproved new drugs. "), [])


class WlLeadinSkipTest(unittest.TestCase):
    """①→② 수리: 내용 없는 도입구를 건너뛰어 하류 300자 절단이 실제 위반을 담게 한다."""

    def _html(self, body: str) -> str:
        return f"<html><body><p>{body}</p></body></html>"

    def test_excerpt_starts_at_first_violation_not_leadin(self):
        ex = ci._extract_wl_body_excerpt(self._html(_BODY))
        self.assertTrue(ex.startswith("1. Your firm failed to thoroughly"))
        self.assertNotIn("but not limited to", ex)

    def test_prose_input_300_truncation_now_carries_a_real_violation(self):
        # 이 테스트가 사고의 핵심을 고정한다 — 종전에는 여기서 도입구 118자만 남았다.
        ex = ci._extract_wl_body_excerpt(self._html(_BODY))
        got = cs._truncate_at_sentence(ex, 300)
        self.assertIn("21 CFR 211.192", got)
        self.assertNotIn("but not limited to", got)

    def test_leadin_not_skipped_when_nothing_substantive_follows(self):
        # 도입구 뒤에 본문이 거의 없으면 이동하지 않는다(정보 손실 금지 게이트).
        text = "x" * 50 + _LEADIN + "short tail."
        start = text.index("During")
        self.assertEqual(ci._skip_wl_leadin(text, start), start)

    def test_no_leadin_leaves_start_unchanged(self):
        text = "During our inspection, we found that your firm failed to do X. " * 20
        self.assertEqual(ci._skip_wl_leadin(text, 0), 0)


class DetailWlViolationsTest(unittest.TestCase):
    """① 수리: WL 결정론 상세 슬롯(483 `fda_483_observations` 동형)."""

    def test_builds_block_from_raw(self):
        raw = {"wl_violations": ci.extract_wl_violations_from_text(_BODY)}
        dd = cs._detail_wl_violations({}, raw)
        self.assertEqual(dd["type"], "wl_violations")
        self.assertEqual(dd["count"], 3)
        self.assertEqual(sorted(dd["violations"][0]),
                         ["citation", "number", "statement"])

    def test_statement_ko_preserved_only_when_present(self):
        raw = {"wl_violations": [
            {"number": "1", "statement": "s", "citation": "c", "statement_ko": "국문"},
            {"number": "2", "statement": "s2", "citation": "c2"},
        ]}
        dd = cs._detail_wl_violations({}, raw)
        self.assertEqual(dd["violations"][0]["statement_ko"], "국문")
        self.assertNotIn("statement_ko", dd["violations"][1])

    def test_absent_or_empty_raw_yields_none(self):
        self.assertIsNone(cs._detail_wl_violations({}, {}))
        self.assertIsNone(cs._detail_wl_violations({}, {"wl_violations": []}))
        # statement 가 빈 행만 있으면 블록을 달지 않는다(요약카드 유지).
        self.assertIsNone(cs._detail_wl_violations(
            {}, {"wl_violations": [{"number": "1", "statement": ""}]}))

    def test_registry_wires_warning_letter_to_this_detail(self):
        raw = {"wl_violations": ci.extract_wl_violations_from_text(_BODY)}
        dd = cs._deterministic_detail("warning-letter", {}, raw)
        self.assertEqual(dd["type"], "wl_violations")


class RefreshWlViolationsTest(unittest.TestCase):
    """조립 시점 재추출 — 스캐폴드에 블록이 **없어도** 원문에서 만들어 넣는다.

    WL 결정론층은 2026-07-20 에 생겼으므로 그 이전 스캐폴드에는 키 자체가 없다. 입력
    (`source_text`)이 deep 델타로 커밋돼 있어 산출이 재현 가능하다는 점이 이 경로의 근거다.
    """

    def _out(self, **card):
        base = {"id": "wl-1", "card_type": "Warning Letter"}
        base.update(card)
        return {"cards": [base]}

    def test_creates_block_when_scaffold_has_none(self):
        out = self._out()
        rep = apb.AssembleReport()
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": _BODY}}, rep)
        dd = out["cards"][0]["deterministic_detail"]
        self.assertEqual(dd["count"], 3)
        self.assertTrue(any("블록 없음" in w for w in rep.warnings))

    def test_noop_without_source_text(self):
        out = self._out()
        rep = apb.AssembleReport()
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": "  "}}, rep)
        self.assertNotIn("deterministic_detail", out["cards"][0])
        self.assertEqual(rep.warnings, [])

    def test_noop_when_parse_yields_nothing(self):
        # 형식이 다른 편지(번호 목록 없음) — degrade 로 블록 없이 발행.
        out = self._out()
        rep = apb.AssembleReport()
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": "No numbered items here."}}, rep)
        self.assertNotIn("deterministic_detail", out["cards"][0])

    def test_never_overwrites_other_detail_type(self):
        out = self._out(deterministic_detail={"type": "gmp_deficiencies", "count": 2})
        rep = apb.AssembleReport()
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": _BODY}}, rep)
        self.assertEqual(out["cards"][0]["deterministic_detail"]["type"], "gmp_deficiencies")

    def test_ignores_non_warning_letter_cards(self):
        out = {"cards": [{"id": "wl-1", "card_type": "FDA 483"}]}
        rep = apb.AssembleReport()
        apb._refresh_wl_violations(out, {"wl-1": {"source_text": _BODY}}, rep)
        self.assertNotIn("deterministic_detail", out["cards"][0])


class FalseAbsenceGateTest(unittest.TestCase):
    """③ 수리: 원문을 확보한 카드가 "원문에 없다"고 주장하면 발행 차단."""

    _EVIDENCE = {"deterministic_detail": {"type": "wl_violations", "count": 3}}

    def test_flags_the_2026_07_20_wording(self):
        card = {"id": "wl-1", **self._EVIDENCE,
                "summary": "세부 위반내용은 원문에 명시되지 않았다."}
        errs = apb.lint_false_absence_claims([card])
        self.assertEqual(len(errs), 1)
        self.assertIn("wl-1", errs[0])

    def test_flags_key_facts_and_reports_index(self):
        card = {"id": "wl-1", **self._EVIDENCE,
                "key_facts": ["발행기관: CDER", "세부 위반사항: 원문 미기재"]}
        errs = apb.lint_false_absence_claims([card])
        self.assertEqual(len(errs), 1)
        self.assertIn("key_facts[1]", errs[0])

    def test_flags_483_and_gmp_wordings(self):
        for text in ("구체적 관찰사항 내용은 원문에 기재되어 있지 않다.",
                     "구체적 지적 내용은 원문에 명시되어 있지 않다."):
            with self.subTest(text=text):
                card = {"id": "c", "deep_analysis": {"key_violations": []},
                        "deterministic_detail": {"type": "fda_483_observations", "count": 1},
                        "summary": text}
                self.assertTrue(apb.lint_false_absence_claims([card]))

    def test_honest_absence_passes_when_no_source_body(self):
        # 원문이 실제로 없는 카드(스캔 483 등)의 정직한 서술은 걸리지 않는다.
        card = {"id": "c", "summary": "구체적 관찰사항 내용은 원문에 기재되어 있지 않다."}
        self.assertEqual(apb.lint_false_absence_claims([card]), [])

    def test_legitimate_prose_not_flagged(self):
        for text in ("FDA 경고서한 원문 전문 확인",
                     "구체적 지적 내용은 원문 확인이 필요하다.",
                     "위반유형: 완제의약품 cGMP 위반(Adulterated)",
                     "관찰사항 1: 공여자 기록 요약에 부적격 사유 미기재"):
            with self.subTest(text=text):
                card = {"id": "c", **self._EVIDENCE, "summary": text}
                self.assertEqual(apb.lint_false_absence_claims([card]), [])

    def test_facts_slot_is_not_inspected(self):
        # facts 는 코드 verbatim 칸 — 거기서의 "원문 미기재"는 그 칸 값이 실제로 없다는 정직한 표기.
        card = {"id": "c", **self._EVIDENCE,
                "facts": [{"label": "세부 위반사항", "value": "원문 미기재"}]}
        self.assertEqual(apb.lint_false_absence_claims([card]), [])

    def test_gate_blocks_assembly(self):
        # 게이트가 report.errors 로 올라가 strict 조립을 실제로 막는지(경고로 끝나지 않는지).
        scaffold = {"brief": {}, "cards": [{
            "id": "wl-1", "card_type": "Warning Letter", "render_order": 0,
            "title_issue": "", "summary": "", "implication": "",
            "key_facts": [], "checks": [],
            "deterministic_detail": {"type": "wl_violations", "count": 3},
        }]}
        delta = {"cards": {"wl-1": {
            "title_issue": "t", "implication": "i", "checks": ["c1", "c2"],
            "summary": "세부 위반내용은 원문에 명시되지 않았다.", "key_facts": ["k"],
        }}}
        with self.assertRaises(apb.AssembleError) as cm:
            apb.assemble_publish_brief(scaffold, delta, strict=True)
        self.assertIn("부재를 주장", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
