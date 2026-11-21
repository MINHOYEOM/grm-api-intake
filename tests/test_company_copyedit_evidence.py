# -*- coding: utf-8 -*-
"""교열 전수 대조(company/editorial/copyedit_evidence.py · copyedit.md 1-1).

리허설 1121b: 교열이 153장 중 17장만 원문과 맞춰 보고 `reject 0` 을 냈는데 `copyedit.json` 으로는 알 수 없었다.
카드마다 `checked`(카드 문장 ↔ 원문 조각)를 적게 하고, 조각이 그 카드 원문에 정말 있는지 코드가 본다.
"""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "company_copyedit_evidence", os.path.join(ROOT, "company", "editorial", "copyedit_evidence.py"))
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)

SRC_A = "OBSERVATION 1 There is no written procedure for OOS investigations. 8 observations were issued."


def _handoff():
    return {"run_date_kst": "2026-11-23", "rows": [
        {"web_card_id": "a", "firm": "Alpha Labs", "body": "FDA 483 issued to Alpha Labs (FEI 300123)",
         "deep_analysis_input": {"body_full": SRC_A}},
        {"web_card_id": "b", "firm": "Beta", "body": "Recall: Beta tablets 10 mg, lot X12, failed dissolution specification"},
        {"web_card_id": "n", "title": "제목만 있는 행"}]}


def _delta():
    return {"cards": {
        "a": {"title_issue": "기준일탈 조사 절차 없음", "summary": "Alpha Labs 에 관찰 8건", "hook_line": "절차서가 없었다",
              "key_facts": ["관찰 1: 기준일탈 조사 절차서 없음"], "en": {"summary": "8 observations to Alpha Labs"}},
        "b": {"title_issue": "용출 부적합 회수", "summary": "Beta tablets 10 mg 회수", "key_facts": []},
        "n": {"title_issue": "소식", "summary": "제목뿐"}}}


def _deep():
    return {"a": {"deep_analysis": {"key_violations": [{"observation": "기준일탈 조사 절차서가 없다(관찰 1)"}]}}}


def _full():
    return {"status": "완료", "cards": [
        {"web_card_id": "a", "verdict": "pass", "fails": [], "checked": [
            {"slot": "summary", "claim": "관찰 8건", "source": "8 observations were issued"},
            {"slot": "hook_line", "claim": "절차서가 없었다", "source": "no written procedure for OOS investigations"},
            {"slot": "deep.key_violations[0].observation", "claim": "기준일탈 조사 절차서가 없다",
             "source": "OBSERVATION 1 There is no written procedure"}]},
        {"web_card_id": "b", "verdict": "pass", "fails": [], "checked": [
            {"slot": "summary", "claim": "Beta tablets 10 mg", "source": "Beta tablets 10 mg, lot X12"},
            {"slot": "en.title_issue", "claim": "용출 부적합", "source": "failed dissolution specification"}]},
        {"web_card_id": "n", "verdict": "pass", "fails": [], "source_absent": "본문 없는 행 — 제목만"}]}


def _bad(copyedit):
    lines, stats = ce.check(_delta(), copyedit, _deep(), _handoff(), None)
    return lines


class CopyeditEvidenceTest(unittest.TestCase):
    def test_evidence_on_removed_deep_slot_is_not_a_failure_after_deep_removal(self):
        # 심층 삭제(blank_slots.py)로 심층분석이 통째로 빠진 뒤: 그 칸을 가리키던 근거는 카드에 없지만 실패가 아니다.
        ce_ = _full()
        ce_["cards"][0]["notes"] = [{"item": "심층 삭제", "slot": "deep.key_violations[0].observation", "evidence": "추정"}]
        lines, _ = ce.check(_delta(), ce_, {"a": {"observations_ko": []}}, _handoff(), None)
        self.assertEqual(lines, [])
        # 주의 없이 빠졌으면 종전대로 걸린다.
        lines, _ = ce.check(_delta(), _full(), {"a": {"observations_ko": []}}, _handoff(), None)
        self.assertTrue(any("claim 이 카드 문장에 없다" in l for l in lines), lines)

    def test_full_evidence_passes(self):
        lines, stats = ce.check(_delta(), _full(), _deep(), _handoff(), None)
        self.assertEqual(lines, [])
        self.assertEqual((stats["ok"], stats["bad"]), (3, 0))

    def test_1121b_shape_without_evidence_fails_every_card(self):
        # 1121b 의 카드 줄 꼴 그대로: verdict·fails 뿐.
        bare = {"cards": [{"web_card_id": c, "verdict": "pass", "fails": []} for c in ("a", "b")]
                + [_full()["cards"][2]]}
        lines = _bad(bare)
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(all("checked 없음" in l for l in lines), lines)

    def test_card_set_must_match_delta(self):
        cut = _full()
        cut["cards"] = cut["cards"][:2] + [dict(cut["cards"][0], web_card_id="zz")]
        lines = _bad(cut)
        self.assertTrue(any(l.startswith("판정 줄이 없는 카드 1: n") for l in lines), lines)
        self.assertTrue(any(l.startswith("델타에 없는 카드 줄 1: zz") for l in lines), lines)
        dup = _full()
        dup["cards"].append(dup["cards"][0])
        self.assertTrue(any(l.startswith("카드 줄 중복") for l in _bad(dup)))

    def test_fabricated_source_or_claim_fails(self):
        fake = _full()
        fake["cards"][1]["checked"][0]["source"] = "Beta tablets 20 mg, lot X12"     # 원문에 없는 조각
        self.assertTrue(any("source 가 이 카드 원문에 없다" in l for l in _bad(fake)))
        other = _full()
        other["cards"][1]["checked"][0]["source"] = "8 observations were issued"     # 다른 카드의 원문
        self.assertTrue(any(l.startswith("b:") and "원문에 없다" in l for l in _bad(other)))
        claim = _full()
        claim["cards"][1]["checked"][0]["claim"] = "Beta tablets 20 mg"
        self.assertTrue(any("claim 이 카드 문장에 없다" in l for l in _bad(claim)))

    def test_whitespace_case_and_quote_forms_are_ignored(self):
        ok = _full()
        ok["cards"][1]["checked"][0]["source"] = "beta  TABLETS\n10 mg,  lot x12"
        self.assertEqual(_bad(ok), [])

    def test_coverage_rules(self):
        two_same = _full()
        two_same["cards"][1]["checked"][1]["source"] = "Beta tablets 10 mg, lot X12"   # 같은 조각 두 번
        self.assertTrue(any("근거 1개" in l for l in _bad(two_same)))
        no_hook = _full()
        del no_hook["cards"][0]["checked"][1]
        no_hook["cards"][0]["checked"].append(
            {"slot": "key_facts[0]", "claim": "기준일탈 조사 절차서 없음", "source": "FDA 483 issued to Alpha Labs"})
        self.assertTrue(any("hook_line 근거 없음" in l for l in _bad(no_hook)))
        no_deep = _full()
        no_deep["cards"][0]["checked"][2] = {"slot": "title_issue", "claim": "기준일탈 조사 절차 없음",
                                             "source": "FDA 483 issued to Alpha Labs"}
        self.assertTrue(any("deep.<키> 근거 없음" in l for l in _bad(no_deep)))
        short = _full()
        short["cards"][1]["checked"][1]["source"] = "failed"
        self.assertTrue(any("source 길이" in l for l in _bad(short)))

    def test_source_absent_only_when_code_finds_no_body(self):
        lie = _full()
        lie["cards"][1] = {"web_card_id": "b", "verdict": "pass", "fails": [], "source_absent": "없음"}
        self.assertTrue(any("source_absent 인데 원문이 있다" in l for l in _bad(lie)))
        nobody = _full()
        nobody["cards"][2] = {"web_card_id": "n", "verdict": "pass", "fails": []}
        self.assertTrue(any(l.startswith("n:") and "source_absent" in l for l in _bad(nobody)))

    def test_absent_fact_on_pass_needs_blank_note_and_reject_needs_fails(self):
        ab = _full()
        ab["cards"][1]["checked"].append({"slot": "key_facts[0]", "claim": "용출 부적합", "absent": True})
        self.assertTrue(any("원문에 없는 사실인데 pass" in l for l in _bad(ab)))
        ab["cards"][1]["notes"] = [{"item": "칸 삭제", "slot": "key_facts[0]", "evidence": "원문에 없음"}]
        self.assertEqual(_bad(ab), [])
        rej = _full()
        rej["cards"][1]["verdict"] = "reject"
        self.assertTrue(any("reject 인데 fails 가 비었다" in l for l in _bad(rej)))

    def test_refused_blank_on_pass_card_fails_until_applied(self):
        # 리허설 1121c: 핵심 사실 하나뿐인 카드의 key_facts[0] 칸 삭제 — 1차 판정 때 잡아 재작업으로 보내야 한다.
        ce_ = _full()
        ce_["cards"][0]["notes"] = [{"item": "칸 삭제", "slot": "key_facts[0]", "evidence": "원문보다 강한 단정"}]
        self.assertEqual(_bad(ce_), [])                                   # 기본(blank_check 끔)은 종전 그대로
        delta = _delta()
        delta["cards"]["a"]["en"]["key_facts"] = ["Observation 1: no OOS procedure"]
        lines, stats = ce.check(delta, ce_, _deep(), _handoff(), None, blank_check=True)
        self.assertTrue(any(l.startswith("a:") and "칸 삭제를 반영할 수 없다" in l and "핵심 사실이 하나뿐" in l
                            for l in lines), lines)
        self.assertEqual(stats["bad"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            for name, obj in (("delta.json", _delta()), ("deep_delta.json", _deep()), ("handoff.json", _handoff()),
                              ("copyedit.json", ce_)):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            hp = os.path.join(tmp, "handoff.json")
            self.assertEqual(ce.main(["check", tmp, hp]), 1)              # 반영 전 — CLI 는 켜고 돈다
            with open(os.path.join(tmp, "slot_removals.json"), "w", encoding="utf-8") as f:
                json.dump({"removed": []}, f)
            self.assertEqual(ce.main(["check", tmp, hp]), 0)              # 반영 기록이 있으면 이미 비운 델타 — 보지 않는다

    def test_cli_check_and_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            def put(name, obj):
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            put("delta.json", _delta())
            put("deep_delta.json", _deep())
            put("handoff.json", _handoff())
            cards = _full()["cards"]
            put("copyedit.json", {"status": "완료", "code_gate": {"ran": True, "exit": 0}, "cards": cards[:1]})
            hp = os.path.join(tmp, "handoff.json")
            self.assertEqual(ce.main(["check", tmp, hp]), 1)                 # 1장만 읽었다
            put("p1.json", {"cards": [cards[2], cards[0]]})
            put("p2.json", {"cards": [cards[1]]})
            code, _msg = ce.merge(os.path.join(tmp, "copyedit.json"),
                                  [os.path.join(tmp, "p1.json"), os.path.join(tmp, "p2.json")])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "copyedit.json"), encoding="utf-8") as f:
                merged = json.load(f)
            self.assertEqual([c["web_card_id"] for c in merged["cards"]], ["a", "b", "n"])   # 델타 순서
            self.assertEqual(merged["code_gate"], {"ran": True, "exit": 0})                   # 다른 키는 그대로
            self.assertEqual((merged["counts"]["cards"], merged["counts"]["reject"]), (3, 0))
            self.assertEqual(ce.main(["check", tmp, hp]), 0)
            code, msg = ce.merge(os.path.join(tmp, "copyedit.json"),
                                 [os.path.join(tmp, "p1.json"), os.path.join(tmp, "p1.json")])
            self.assertEqual(code, 1)
            out = ce.show(tmp, hp, size=2, batch=1)
            self.assertIn("[원문]", out)
            self.assertIn("deep.key_violations[0].observation", out)
            self.assertIn(f"({len(SRC_A)}자 — --full 로 보기)", out)


class InstructionsRequireTheCheckTest(unittest.TestCase):
    def _read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_copyedit_runs_the_check_before_done(self):
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            text = self._read(*folder, "copyedit.md")
            self.assertIn("python company/editorial/copyedit_evidence.py check company/run/{run_date_kst} "
                          "_handoff/handoff_$D.json", text, folder)
            self.assertIn("전수 대조(1-1 `copyedit_evidence.py check`) exit 0 도 조건이다", text, folder)
        self.assertIn("copyedit_evidence.check(", self._read("company", "editorial", "rehearsal_check.py"))
        self.assertIn("4-1. 교열이 카드를 전부 원문과 맞춰 봤다", self._read("company", "editorial", "REHEARSAL.md"))


if __name__ == "__main__":
    unittest.main()
