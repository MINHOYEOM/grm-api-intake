# -*- coding: utf-8 -*-
"""교열 '칸 삭제'(company/editorial/blank_slots.py · copyedit.md 2-3) — 선택 칸만 국·영 짝으로 비운다."""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "company_blank_slots", os.path.join(ROOT, "company", "editorial", "blank_slots.py"))
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


def _delta():
    return {"cards": {"a": {
        "title_issue": "제목", "summary": "요약", "hook_line": "핵심 한 줄", "implication": "시사점",
        "key_facts": ["사실 0", "사실 1", "사실 2"], "checks": ["점검 0", "점검 1", "점검 2"],
        "en": {"title_issue": "T", "summary": "S", "hook_line": "H", "implication": "I",
               "key_facts": ["F0", "F1", "F2"], "checks": ["C0", "C1", "C2"]}}}}


def _deep():
    return {"a": {"deep_analysis": {
        "key_violations": [{"observation": "지적 0"}, {"observation": "지적 1"}],
        "inspectional_significance": "의미", "required_remediation": {"deadline": "", "items": []},
        "administrative_risks": ""}},
        "w": {"deep_analysis": {"key_violations": [{"description": "x"}, {"description": "y"}],
                                "fda_evaluation": "평가"}}}


def _ce(*slots, cid="a"):
    return {"cards": [{"web_card_id": cid, "verdict": "pass", "fails": [],
                       "notes": [{"item": "칸 삭제", "slot": s, "evidence": "사유"} for s in slots]
                       + [{"item": "같은 이름", "slot": "summary", "evidence": "문체 — 비우지 않는다"}]}]}


class BlankSlotsTest(unittest.TestCase):
    def _apply(self, *slots, cid="a"):
        delta, deep = _delta(), _deep()
        problems, done = bs.apply_blanks(delta, deep, bs.blank_requests(_ce(*slots, cid=cid)))
        return delta, deep, problems, done

    def test_optional_slots_blanked_as_ko_en_pairs(self):
        delta, _, problems, done = self._apply("hook_line", "implication", "key_facts[1]", "checks[0]")
        self.assertEqual(problems, [])
        c = delta["cards"]["a"]
        self.assertNotIn("hook_line", c)
        self.assertNotIn("hook_line", c["en"])
        self.assertEqual((c["implication"], c["en"]["implication"]), ("", ""))
        self.assertEqual((c["key_facts"], c["en"]["key_facts"]), (["사실 0", "사실 2"], ["F0", "F2"]))
        self.assertEqual((c["checks"], c["en"]["checks"]), (["점검 1", "점검 2"], ["C1", "C2"]))
        self.assertEqual(c["summary"], "요약")          # 문체 notes 는 건드리지 않는다
        self.assertEqual(len(done), 4)

    def test_several_items_of_one_list_removed_from_the_back(self):
        delta, _, problems, _ = self._apply("key_facts[0]", "key_facts[2]")
        self.assertEqual(problems, [])
        self.assertEqual(delta["cards"]["a"]["key_facts"], ["사실 1"])
        self.assertEqual(delta["cards"]["a"]["en"]["key_facts"], ["F1"])

    def test_required_slots_and_unsafe_removals_are_refused(self):
        for slot in ("summary", "title_issue", "quotes"):
            self.assertTrue(self._apply(slot)[2], slot)
        self.assertTrue(self._apply("key_facts[0]", "key_facts[1]", "key_facts[2]")[2])   # 빈 key_facts
        self.assertTrue(self._apply("checks[0]", "checks[1]")[2])                         # 점검 1개
        self.assertTrue(self._apply("key_facts[9]")[2])

    def test_ko_en_length_mismatch_is_refused(self):
        delta, deep = _delta(), _deep()
        delta["cards"]["a"]["en"]["key_facts"].pop()
        problems, _ = bs.apply_blanks(delta, deep, bs.blank_requests(_ce("key_facts[0]")))
        self.assertTrue(problems)

    def test_deep_only_for_483_optional_sections(self):
        _, deep, problems, _ = self._apply("deep.key_violations[0]", "deep.inspectional_significance")
        self.assertEqual(problems, [])
        da = deep["a"]["deep_analysis"]
        self.assertEqual(da["key_violations"], [{"observation": "지적 1"}])
        self.assertEqual(da["inspectional_significance"], "")
        self.assertTrue(self._apply("deep.required_remediation")[2])
        self.assertTrue(self._apply("deep.key_violations[0]", "deep.key_violations[1]")[2])
        self.assertTrue(self._apply("deep.fda_evaluation", cid="w")[2])        # 경고서한 = 필수 절

    def test_verify_catches_lost_deep_and_left_over_slots(self):
        reqs = bs.blank_requests(_ce("deep.key_violations[0]", "hook_line"))
        ok = {"cards": [{"id": "a", "deep_analysis": {"key_violations": []}, "en": {}}]}
        self.assertEqual(bs.verify_blanks(ok, reqs), [])
        lost = {"cards": [{"id": "a", "deep_analysis": None, "hook_line": "남음", "en": {}}]}
        self.assertEqual(len(bs.verify_blanks(lost, reqs)), 2)

    def test_cli_apply_writes_nothing_on_problem_and_is_repeatable_from_the_same_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = lambda n: os.path.join(tmp, n)
            for name, obj in (("d.json", _delta()), ("dp.json", _deep()),
                              ("ok.json", _ce("key_facts[0]")), ("bad.json", _ce("summary"))):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            self.assertEqual(bs.main(["apply", p("bad.json"), p("d.json"), p("out.json")]), 1)
            self.assertFalse(os.path.exists(p("out.json")))
            for _ in range(2):                          # 같은 입력으로 두 번 → 같은 결과
                self.assertEqual(bs.main(["apply", p("ok.json"), p("d.json"), p("out.json"),
                                          "--deep-in", p("dp.json"), "--deep-out", p("dpo.json")]), 0)
                with open(p("out.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["cards"]["a"]["key_facts"], ["사실 1", "사실 2"])

    def test_copyedit_md_two_editions_point_to_the_script(self):
        for folder in ((), ("next",)):
            with open(os.path.join(ROOT, "company", "editorial", *folder, "copyedit.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("2-3. **칸 삭제", text, folder)
            self.assertIn("python company/editorial/blank_slots.py apply company/run/{run_date_kst}/copyedit.json", text, folder)
            self.assertIn("blank_slots.py verify", text, folder)
            self.assertIn("**재작업(3-1)으로 되돌릴 때는 반영하지 않는다**", text, folder)


if __name__ == "__main__":
    unittest.main()
