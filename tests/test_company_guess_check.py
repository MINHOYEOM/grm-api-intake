# -*- coding: utf-8 -*-
"""가림·깨진 값 채움 검사(`company/editorial/guess_check.py`)와 사전 점검 배선 — 리허설 1121b 반려 꼴."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load_module("company_guess_check", "company/editorial/guess_check.py")
precheck = _load_module("company_precheck_guess", "company/editorial/precheck.py")

BODY = ("OBSERVATION 4 Written procedures are not followed. Specifically, the operator aborted [redacted] tests "
        "on Line [redacted] Tester. OBSERVATION 5 Data generated from your [redacted] meter (Equipment #A33770A) "
        "is transcribed. Environmental monitoring samples, water samples are incubated on ofmedia plates.")


def _row(cid="c", body=BODY):
    return {"web_card_id": cid, "kind": "fda-483", "body": "FDA 483",
            "deep_analysis_input": {"body_full": body}}


def _scaffold_card(cid="c"):
    return {"id": cid, "facts": [], "quotes": [], "deterministic_detail": {"type": "fda_483_observations", "observations": [
        {"number": "4", "deficiency": "Written procedures are not followed.",
         "detail": "the operator aborted [redacted] tests on Line [redacted] Tester."},
        {"number": "5", "deficiency": "Data generated from your [redacted] meter (Equipment #A33770A) is transcribed.",
         "detail": "Environmental monitoring samples, water samples are incubated."}]}}


def _card(**kw):
    base = {"title_issue": "절차 미준수", "summary": "FDA가 관찰사항을 적었다.", "key_facts": ["관찰 4: 시험 중단"],
            "en": {"title_issue": "Procedures not followed", "summary": "FDA issued a 483.", "key_facts": ["Obs 4: tests aborted"]}}
    base.update(kw)
    return base


class LexiconTest(unittest.TestCase):
    def _block(self, text, src=BODY, fact=True):
        return gc.check_slots([("s", text, fact, None)], src)

    def test_redacted_meter_filled_as_ph_blocks(self):
        b, w = self._block("산도계(설비 번호 A33770A) 데이터를 옮겨 적었다")
        self.assertEqual(len(b), 1)
        self.assertIn("'산도'", b[0])
        b, _ = self._block("산도계 데이터", src=BODY.replace("[redacted] meter", "pH meter"))
        self.assertEqual(b, [])

    def test_redacted_tester_filled_as_leak_blocks(self):
        b, _ = self._block("누설시험 장비 운전 절차서를 따르지 않았다")
        self.assertTrue(b and "누설" in b[0])

    def test_vague_count_blocks_but_not_su_man(self):
        self.assertTrue(self._block("분당 수십 개씩 검사")[0])
        self.assertEqual(self._block("세균과 진균 집락의 누적 수만 한계와 비교한다")[0], [])

    def test_water_words(self):
        src = BODY.replace("water samples", "samples")
        self.assertTrue(self._block("정제수 시스템이 적격성평가를 받지 않았다", src)[0])
        self.assertTrue(self._block("이 물은 모노그래프를 충족한다", src)[0])
        self.assertEqual(self._block("실사관이 물은 2026-05-14에야 일탈을 열었다", src)[0], [])   # '묻다'
        self.assertEqual(self._block("생물막과 물질 이동", src)[0], [])
        self.assertEqual(self._block("정제수 시스템", BODY)[0], [])                              # 원문 'water'

    def test_ocr_merged_source_word_counts(self):
        self.assertEqual(self._block("배지 플레이트에서 배양한다")[0], [])                      # 'ofmedia'

    def test_english_output_checked_too(self):
        b, _ = self._block("Unlocked Excel worksheets")
        self.assertTrue(b and "Excel" in b[0])

    def test_advice_slot_only_warns(self):
        b, w = self._block("엑셀 시트 잠금 여부를 확인하는가?", fact=False)
        self.assertEqual(b, [])
        self.assertEqual(len(w), 1)

    def test_soft_word_warns_only(self):
        b, w = self._block("원료 시험을 하지 않았다", src="tests were not done")
        self.assertEqual(b, [])
        self.assertTrue(w and "원료" in w[0])


class DeltaTest(unittest.TestCase):
    def test_delta_blocks_with_handoff_row(self):
        delta = {"cards": {"c": _card(title_issue="누설시험 중단")}}
        b, w = gc.delta_guess_lines(delta, {"c": _row()}, {"c": _scaffold_card()})
        self.assertEqual(len(b), 1)
        self.assertTrue(b[0].startswith("가림 값 채움 c.title_issue:"))

    def test_delta_without_handoff_row_only_warns(self):
        delta = {"cards": {"c": _card(title_issue="누설시험 중단")}}
        b, w = gc.delta_guess_lines(delta, {}, {"c": _scaffold_card()})
        self.assertEqual(b, [])
        self.assertTrue(w and w[0].startswith("가림 값 채움 의심 c.title_issue"))

    def test_delta_without_any_source_skips(self):
        self.assertEqual(gc.delta_guess_lines({"cards": {"c": _card(title_issue="누설시험")}}, {}, {}), ([], []))

    def test_implication_and_checks_warn(self):
        delta = {"cards": {"c": _card(implication="누설시험 장비 관리가 약하다.", checks=["누설시험 기록을 보는가?"])}}
        b, w = gc.delta_guess_lines(delta, {"c": _row()}, {})
        self.assertEqual(b, [])
        self.assertEqual(len(w), 2)


class DeepTest(unittest.TestCase):
    def test_observation_translation_blocks(self):
        shard = {"items": {"c": {"observations_ko": [
            {"number": "5", "deficiency_ko": "산도계(설비 번호 A33770A) 데이터를 옮겨 적는다.", "detail_ko": "환경 시료를 배양한다."}],
            "deep_analysis": {"key_violations": [{"observation": "누설시험을 중단했다(관찰 4).", "citation": "21 CFR 211.100",
                                                  "risk": "누설 결함을 놓칠 수 있다."}]}}}}
        b, w = gc.deep_guess_lines(shard, {"c": _row()}, {"c": _scaffold_card()})
        text = "\n".join(b)
        self.assertIn("가림 값 채움 c.deep.observations_ko[0].deficiency_ko", text)
        self.assertIn("가림 값 채움 c.deep.key_violations[0].observation", text)
        self.assertNotIn("risk", text)                                   # 권고 칸은 경고
        self.assertTrue(any("key_violations[0].risk" in x for x in w))

    def test_original_and_citation_not_scanned(self):
        shard = {"c": {"violations_ko": [{"original": "leak test 엑셀", "statement_ko": "절차를 따르지 않았다."}],
                       "deep_analysis": {"key_violations": [{"citation": "엑셀 조항", "observation": "절차 미준수"}]}}}
        self.assertEqual(gc.deep_guess_lines(shard, {"c": _row()}), ([], []))

    def test_word_from_another_observation_warns(self):
        # 'water' 는 관찰 5 에만 있다 — 가림 표지가 있는 관찰 4 번역에 '용수' 를 쓰면 경고
        shard = {"c": {"observations_ko": [{"number": "4", "deficiency_ko": "절차 미준수", "detail_ko": "용수 시험을 중단했다."}]}}
        b, w = gc.deep_guess_lines(shard, {"c": _row()}, {"c": _scaffold_card()})
        self.assertEqual(b, [])
        self.assertTrue(w and "관찰 4 원문에는 없고" in w[0])

    def test_no_handoff_row_skips(self):
        shard = {"c": {"observations_ko": [{"number": "4", "deficiency_ko": "누설시험", "detail_ko": "x"}]}}
        self.assertEqual(gc.deep_guess_lines(shard, {}), ([], []))


class CliTest(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = precheck.main(argv)
        return code, out.getvalue()

    def test_terms_blocks_and_deep_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            ho = os.path.join(tmp, "handoff.json")
            with open(ho, "w", encoding="utf-8") as f:
                json.dump({"rows": [_row()]}, f)
            sc = os.path.join(tmp, "sc.json")
            with open(sc, "w", encoding="utf-8") as f:
                json.dump({"cards": [_scaffold_card()]}, f)
            delta = os.path.join(tmp, "delta.A.json")
            with open(delta, "w", encoding="utf-8") as f:
                json.dump({"cards": {"c": _card(title_issue="누설시험 중단")}}, f, ensure_ascii=False)
            code, text = self._run(["terms", delta, "--handoff", ho, "--scaffold", sc])
            self.assertEqual(code, 1)
            self.assertIn("가림 값 채움 c.title_issue:", text)
            shard = os.path.join(tmp, "deep.A.json")
            with open(shard, "w", encoding="utf-8") as f:
                json.dump({"items": {"c": {"deep_analysis": {"fda_evaluation": "엑셀 시트가 잠기지 않았다."}}}}, f,
                          ensure_ascii=False)
            code, text = self._run(["deep", ho, shard, "--scaffold", sc])
            self.assertEqual(code, 1)
            self.assertIn("가림 값 채움 c.deep.fda_evaluation:", text)


class InstructionsTest(unittest.TestCase):
    def test_rule_in_reporter_and_deep_docs(self):
        for rel in ("company/editorial/reporter.md", "company/editorial/next/reporter.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("## ★ 최우선 규칙 — 가려진·깨진 값은 채우지 않는다", text, rel)
            self.assertLess(text.index("최우선 규칙"), text.index("## 맡는 v16 절"), rel)
            self.assertIn("가림 값 채움 <id>.<슬롯>", text, rel)
        for name in ("DeepFda483", "DeepWL", "DeepAdmin"):
            text = (ROOT / "docs" / "prompts" / f"GRM_Prompt_{name}_v1.md").read_text(encoding="utf-8")
            self.assertIn("가려진·깨진 값은 채우지 않는다", text, name)


if __name__ == "__main__":
    unittest.main()
