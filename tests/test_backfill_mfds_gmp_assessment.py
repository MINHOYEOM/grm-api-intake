#!/usr/bin/env python3
"""식약처 GMP 판정 소급 재계산의 순수 부분(네트워크 없음)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_mfds_gmp_assessment as b  # noqa: E402


def _row(text: str, assess: str = "unknown", excerpt: str = "",
         rid: str = "rawsig-1", as_str: bool = False):
    # 상태 칸은 중립값(skipped-type: 수집 시점 관측값이라 백필이 안 건드림)으로 고정 — 판정 시험만 본다.
    payload = {"attachment_text": text, "attachment_deficiency_assessment": assess,
               "gmp_deficiency_table_status": "skipped-type"}
    if excerpt:
        payload["attachment_deficiency_excerpt"] = excerpt
    raw = json.dumps(payload, ensure_ascii=False) if as_str else payload
    return {"raw_signal_id": rid, "raw_json": raw}


class PlanRowTest(unittest.TestCase):
    def test_unknown_to_none_is_upgraded(self):
        plan = b.plan_row(_row("2 실태조사 개요 평가 결과: 적합"))
        self.assertEqual(plan["kind"], "update")
        self.assertEqual((plan["old"], plan["new"]), ("unknown", "none"))
        self.assertTrue(plan["changed_assess"])
        self.assertEqual(
            plan["raw_json"]["attachment_deficiency_assessment"], "none")

    def test_unknown_to_present_is_upgraded(self):
        plan = b.plan_row(_row("평가결과 : 보완적합 - 지적사항 분류 : 기타 1건"))
        self.assertEqual(plan["new"], "present")

    def test_already_correct_row_is_untouched(self):
        self.assertIsNone(b.plan_row(
            _row("평가 결과: 적합", assess="none", excerpt="평가 결과: 적합")))

    def test_row_without_text_is_reported_not_updated(self):
        self.assertEqual(b.plan_row(_row(""))["kind"], "no_text")
        self.assertEqual(b.plan_row(_row("   "))["kind"], "no_text")

    def test_downgrade_is_blocked_not_applied(self):
        """★강등은 적용하지 않고 보고한다 — 판정 규칙이 바뀐 신호이지 소급 대상이 아니다."""
        # 판정도 excerpt 도 그대로면 변화 없음(정상)
        self.assertIsNone(b.plan_row(_row(
            "지적(보완)사항 3건 품질경영 미흡", assess="present",
            excerpt="지적(보완)사항 3건 품질경영 미흡")))
        # 인위적 강등 상황: 저장값이 present 인데 텍스트는 판정 불가
        plan2 = b.plan_row(_row("제조소 현황만 있는 표지", assess="present"))
        self.assertEqual(plan2["kind"], "downgrade_blocked")
        self.assertEqual((plan2["old"], plan2["new"]), ("present", "unknown"))

    def test_none_to_present_is_not_treated_as_upgrade(self):
        # none ↔ present 상호 변환은 사람이 봐야 한다(조용한 덮어쓰기 금지).
        self.assertFalse(b.is_upgrade("none", "present"))
        self.assertFalse(b.is_upgrade("present", "none"))
        self.assertTrue(b.is_upgrade("unknown", "none"))
        self.assertTrue(b.is_upgrade("unknown", "present"))

    def test_excerpt_is_filled_but_never_overwritten(self):
        """★기존 excerpt 는 바꾸지 않는다 — 발행된 카드의 인용문이 소급으로 흔들리면 안 된다."""
        keep = "기존에 뽑아 둔 인용문"
        plan = b.plan_row(_row("평가 결과: 적합", assess="none", excerpt=keep))
        self.assertIsNone(plan)          # 판정도 excerpt 도 바꿀 게 없다
        plan2 = b.plan_row(_row("평가 결과: 적합", assess="none"))
        self.assertTrue(plan2["fill_excerpt"])
        self.assertEqual(
            plan2["raw_json"]["attachment_deficiency_excerpt"], "평가 결과: 적합")

    def test_raw_json_accepts_text_column(self):
        # raw_json 은 text 컬럼이라 문자열로 올 수 있다.
        plan = b.plan_row(_row("평가 결과: 적합", as_str=True))
        self.assertEqual(plan["new"], "none")

    def test_malformed_raw_json_is_skipped_not_crashed(self):
        self.assertEqual(
            b.plan_row({"raw_signal_id": "x", "raw_json": "{not json"})["kind"], "no_text")
        self.assertEqual(
            b.plan_row({"raw_signal_id": "x", "raw_json": None})["kind"], "no_text")

    def test_other_payload_keys_are_preserved(self):
        row = {"raw_signal_id": "r", "raw_json": {
            "attachment_text": "평가 결과: 적합",
            "attachment_deficiency_assessment": "unknown",
            "manufacturer": "(주)보존해야함", "attachment_pages": 3}}
        plan = b.plan_row(row)
        self.assertEqual(plan["raw_json"]["manufacturer"], "(주)보존해야함")
        self.assertEqual(plan["raw_json"]["attachment_pages"], 3)

    def test_uses_the_real_collector_functions_not_a_copy(self):
        """★사본 금지 — 판정은 수집기 함수를 그대로 import 해서 쓴다."""
        import collect_mfds_gmp_inspection as g
        self.assertIs(b.assess_deficiency_with_rows, g.assess_deficiency_with_rows)
        self.assertIs(b._extract_deficiency_excerpt, g._extract_deficiency_excerpt)


# ── #9 판정 보강 2026-11-25: 표 행 승격 + 상태 칸 채우기 ─────────────────────
def _row2(payload: dict, rid: str = "rawsig-2"):
    return {"raw_signal_id": rid, "raw_json": payload}


class StatusAndRowsTest(unittest.TestCase):
    def test_extracted_rows_with_unknown_assessment_is_promoted(self):
        """status=extracted 인데 본문 판정이 unknown 인 113건형 → present."""
        plan = b.plan_row(_row2({
            "attachment_text": "본문 판정 불능", "attachment_deficiency_assessment": "unknown",
            "gmp_deficiency_table_status": "extracted",
            "gmp_deficiencies": [{"field": "품질"}]}))
        self.assertEqual((plan["old"], plan["new"]), ("unknown", "present"))
        self.assertFalse(plan["changed_status"])

    def test_rerun_after_row_promotion_is_not_a_downgrade(self):
        """승격된 행을 다시 돌려도 present→unknown 강등으로 오인하지 않는다(멱등)."""
        self.assertIsNone(b.plan_row(_row2({
            "attachment_text": "본문 판정 불능", "attachment_deficiency_assessment": "present",
            "gmp_deficiency_table_status": "extracted",
            "gmp_deficiencies": [{"field": "품질"}]})))

    def test_missing_status_is_filled_from_stored_text(self):
        """R1 행(상태 없음·판정 none) → empty. 판정은 그대로."""
        plan = b.plan_row(_row2({
            "attachment_text": "의약품 제조소 GMP 정기실태조사 결과 평가 결과 지적(보완)사항(Deficiencies) 없음",
            "attachment_deficiency_assessment": "none",
            "attachment_deficiency_excerpt": "x"}))
        self.assertEqual(plan["kind"], "update")
        self.assertFalse(plan["changed_assess"])
        self.assertEqual((plan["status_old"], plan["status_new"]), ("", "empty"))
        self.assertEqual(plan["raw_json"]["gmp_deficiency_table_status"], "empty")

    def test_footnoted_table_row_gets_present_and_gate_degraded(self):
        """한국코러스형: status=empty·unknown → present + gate-degraded(표 파서 0행)."""
        from tests.test_gmp_inspection import _FOOTNOTE_TABLE_TEXT
        plan = b.plan_row(_row2({
            "attachment_text": _FOOTNOTE_TABLE_TEXT,
            "attachment_deficiency_assessment": "unknown",
            "attachment_deficiency_excerpt": "e",
            "gmp_deficiency_table_status": "empty"}))
        self.assertEqual((plan["old"], plan["new"]), ("unknown", "present"))
        self.assertEqual(plan["status_new"], "gate-degraded")

    def test_empty_with_unknown_becomes_unreadable(self):
        plan = b.plan_row(_row2({
            "attachment_text": "판정 불능 본문", "attachment_deficiency_assessment": "unknown",
            "attachment_deficiency_excerpt": "e", "gmp_deficiency_table_status": "empty"}))
        self.assertEqual(plan["status_new"], "unreadable")

    def test_observed_statuses_are_never_overwritten(self):
        for status in ("extracted", "skipped-type", "gate-degraded", "parse-fail", "unreadable"):
            with self.subTest(status=status):
                self.assertIsNone(b.plan_row(_row2({
                    "attachment_text": "평가 결과: 적합",
                    "attachment_deficiency_assessment": "none",
                    "attachment_deficiency_excerpt": "평가 결과: 적합",
                    "gmp_deficiency_table_status": status})))

    def test_empty_none_row_is_unchanged(self):
        self.assertIsNone(b.plan_row(_row2({
            "attachment_text": "지적(보완)사항 없음", "attachment_deficiency_assessment": "none",
            "attachment_deficiency_excerpt": "지적(보완)사항 없음",
            "gmp_deficiency_table_status": "empty"})))

    def test_no_text_row_stays_no_text(self):
        self.assertEqual(b.plan_row(_row2({"gmp_deficiency_table_status": ""}))["kind"], "no_text")

    def test_uses_shared_functions_not_copies(self):
        import collect_mfds_gmp_inspection as g
        self.assertIs(b.assess_deficiency_with_rows, g.assess_deficiency_with_rows)
        self.assertIs(b.derive_table_status, g.derive_table_status)


if __name__ == "__main__":
    unittest.main()
