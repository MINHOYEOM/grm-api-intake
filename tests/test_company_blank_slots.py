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

    # ── 심층 삭제(copyedit.md 2-4 · 리허설 1121b) ─────────────────────────────
    @staticmethod
    def _deep483():
        return {"p": {"deep_analysis": {
            "key_violations": [{"observation": "지적 0"}, {"observation": "지적 1"}, {"observation": "지적 2"}],
            "inspectional_significance": "의미", "required_remediation": {"deadline": "d", "items": []},
            "administrative_risks": ""},
            "observations_ko": [{"number": "1", "deficiency_ko": "결함", "detail_ko": "추정한 값"},
                                {"number": "2", "deficiency_ko": "결함 2", "detail_ko": "멀쩡"}],
            "source_text": "원문"},
            "w": {"deep_analysis": {"key_violations": [{"description": "x"}, {"description": "y"}],
                                    "fda_evaluation": "평가", "required_remediation": {"deadline": "d", "items": ["i0"]},
                                    "administrative_risks": "위험"},
                  "violations_ko": [{"number": "1", "statement_ko": "위반"}]},
            "h": {"ncr_ko": {"outcome_ko": "결과", "s9_ko": "추정", "s9_title_ko": "제목"}}}

    def _deep_apply(self, *slots):
        deep = self._deep483()
        reqs = [(s.split(":", 1)[0], s.split(":", 1)[1], "사유") for s in slots]
        log = []
        problems, done = bs.apply_blanks({"cards": {}}, deep, [], reqs, log)
        return deep, problems, done, log

    def test_deep_auto_turns_deep_only_rejects_into_pass_and_counts_the_rest(self):
        ce_ = {"cards": [
            {"web_card_id": "p", "verdict": "reject", "fails": [
                {"item": "자가점검 9", "slot": "deep.observations_ko[0].detail_ko", "evidence": "가린 값 추정"}]},
            {"web_card_id": "m", "verdict": "reject", "fails": [
                {"item": "자가점검 9", "slot": "deep.fda_evaluation", "evidence": "e"},
                {"item": "자가점검 2", "slot": "title_issue", "evidence": "e"}]},
            {"web_card_id": "q", "verdict": "pass", "fails": [], "checked": [
                {"slot": "deep.key_violations[1].observation", "claim": "x", "absent": True}]}],
            "rework": {"cards": ["p", "m"]}}
        converted, remaining, added = bs.deep_auto(ce_)
        self.assertEqual((converted, remaining), (["p"], ["m"]))
        self.assertEqual(added, ["q.deep.key_violations[1].observation"])
        p = ce_["cards"][0]
        self.assertEqual((p["verdict"], p["fails"]), ("pass", []))
        self.assertEqual(p["notes"][0]["item"], bs.DEEP_ITEM)
        self.assertEqual(ce_["rework"]["cards"], ["m"])
        self.assertEqual((ce_["counts"]["reject"], ce_["counts"]["pass"]), (1, 2))
        self.assertEqual(bs.deep_auto(ce_)[:2], ([], ["m"]))           # 다시 돌려도 같다
        self.assertEqual(len(bs.deep_requests(ce_)), 2)

    def test_deep_removal_removes_item_or_drops_whole_analysis(self):
        deep, problems, done, log = self._deep_apply("p:deep.key_violations[2].observation",
                                                    "p:deep.key_violations[0].observation")
        self.assertEqual(problems, [])
        self.assertEqual(deep["p"]["deep_analysis"]["key_violations"], [{"observation": "지적 1"}])
        self.assertEqual({e["item"] for e in log}, {bs.DEEP_ITEM})
        # 필수 절(경고서한 fda_evaluation) → 심층분석 통째. 번역(violations_ko)·원문은 남는다.
        deep, problems, _, _ = self._deep_apply("w:deep.fda_evaluation")
        self.assertEqual(problems, [])
        self.assertNotIn("deep_analysis", deep["w"])
        self.assertEqual(deep["w"]["violations_ko"][0]["statement_ko"], "위반")
        # 지적을 전부 빼면 통째 · 경고서한 시정 항목이 0 이 되면 통째 · 경고서한 administrative_risks 는 필수 절
        for slots in (("p:deep.key_violations[0]", "p:deep.key_violations[1]", "p:deep.key_violations[2]"),
                      ("w:deep.required_remediation.items[0]",), ("w:deep.administrative_risks",)):
            deep, problems, _, _ = self._deep_apply(*slots)
            self.assertEqual(problems, [], slots)
            self.assertNotIn("deep_analysis", deep[slots[0][0]], slots)
        # 483 선택 절은 비우기만
        deep, problems, _, _ = self._deep_apply("p:deep.inspectional_significance")
        self.assertEqual(deep["p"]["deep_analysis"]["inspectional_significance"], "")

    def test_deep_removal_replaces_translation_with_fixed_notice(self):
        deep, problems, _, _ = self._deep_apply("p:deep.observations_ko[0].detail_ko", "h:deep.ncr_ko.s9_ko",
                                               "h:deep.ncr_ko.s9_title_ko", "w:deep.violations_ko[0]")
        self.assertEqual(problems, [])
        self.assertEqual(deep["p"]["observations_ko"][0],
                         {"number": "1", "deficiency_ko": "결함", "detail_ko": bs.TRANSLATION_NOTICE})
        self.assertEqual(deep["p"]["observations_ko"][1]["detail_ko"], "멀쩡")
        self.assertIn("deep_analysis", deep["p"])                    # 번역만 바꾸고 심층분석은 그대로
        self.assertEqual(deep["h"]["ncr_ko"]["s9_ko"], bs.TRANSLATION_NOTICE)
        self.assertEqual(deep["h"]["ncr_ko"]["s9_title_ko"], bs.TRANSLATION_NOTICE_TITLE)
        self.assertEqual(deep["w"]["violations_ko"][0]["statement_ko"], bs.TRANSLATION_NOTICE)
        # 없는 칸·없는 카드는 문제(칸 이름을 잘못 적었다 — 반려로 되돌린다)
        self.assertTrue(self._deep_apply("p:deep.observations_ko[9].detail_ko")[1])
        self.assertTrue(self._deep_apply("zz:deep.key_violations[0]")[1])

    def test_verify_allows_lost_deep_only_for_deep_removal_cards(self):
        reqs = bs.blank_requests(_ce("deep.key_violations[0]"))
        lost = {"cards": [{"id": "a", "deep_analysis": None, "en": {}}]}
        self.assertEqual(len(bs.verify_blanks(lost, reqs)), 1)
        dreqs = [("a", "deep.fda_evaluation", "사유")]
        self.assertEqual(bs.verify_blanks(lost, reqs, dreqs), [])
        self.assertEqual(bs.deep_lost(lost, dreqs), ["a"])
        self.assertEqual(bs.deep_lost(lost, [("a", "deep.ncr_ko.s9_ko", "사유")]), [])

    def test_cli_deep_auto_then_apply_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = lambda n: os.path.join(tmp, n)
            ce_ = {"cards": [{"web_card_id": "p", "verdict": "reject", "fails": [
                {"item": "자가점검 9", "slot": "deep.key_violations[1].observation", "evidence": "추정"}]}]}
            for name, obj in (("ce.json", ce_), ("d.json", {"cards": {"p": {"title_issue": "t"}}}),
                              ("dp.json", self._deep483())):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            self.assertEqual(bs.main(["deep-auto", p("ce.json")]), 0)
            self.assertEqual(bs.main(["apply", p("ce.json"), p("d.json"), p("out.json"), "--deep-in", p("dp.json"),
                                      "--deep-out", p("dpo.json"), "--log", p("log.json")]), 0)
            with open(p("log.json"), encoding="utf-8") as f:
                log = json.load(f)["removed"]
            self.assertEqual([(e["web_card_id"], e["slot"]) for e in log], [("p", "deep.key_violations[1].observation")])
            with open(p("dpo.json"), encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)["p"]["deep_analysis"]["key_violations"]), 2)

    # ── 판정 전 사전 점검(copyedit.md 2-5 · 리허설 1121c) ──────────────────────
    @staticmethod
    def _1121c():
        """리허설 1121c fda483-196441 의 꼴: 핵심 사실 하나뿐인 카드에 `key_facts[0]` 칸 삭제 — 1차는 재작업이라 apply 를 안 돌렸다."""
        delta = _delta()
        delta["cards"]["k"] = {"title_issue": "제목", "summary": "요약", "hook_line": "한 줄", "key_facts": ["유일한 사실"],
                               "en": {"title_issue": "T", "summary": "S", "hook_line": "H", "key_facts": ["only fact"]}}
        ce = {"cards": [
            {"web_card_id": "a", "verdict": "pass", "fails": [],
             "notes": [{"item": "칸 삭제", "slot": "key_facts[1]", "evidence": "사유"}]},
            {"web_card_id": "k", "verdict": "pass", "fails": [],
             "notes": [{"item": "칸 삭제", "slot": "hook_line", "evidence": "사유"},
                       {"item": "칸 삭제", "slot": "key_facts[0]", "evidence": "원문보다 강한 단정"}]},
            {"web_card_id": "r", "verdict": "reject", "fails": [{"item": "사실 오류", "slot": "summary", "evidence": "x"}],
             "notes": [{"item": "칸 삭제", "slot": "summary", "evidence": "반려 카드 — 재작업으로 간다"}]}]}
        return delta, ce

    def test_check_names_only_pass_cards_whose_blank_would_be_refused_and_changes_nothing(self):
        delta, ce = self._1121c()
        deep = _deep()
        before = json.dumps([delta, deep, ce], ensure_ascii=False, sort_keys=True)
        refused = bs.refused_cards(ce, delta, deep)
        self.assertEqual(list(refused), ["k"])                       # a 는 비울 수 있고 r 은 이미 반려
        self.assertIn("핵심 사실이 하나뿐", refused["k"][0])
        self.assertEqual(json.dumps([delta, deep, ce], ensure_ascii=False, sort_keys=True), before)
        # apply 와 같은 판정 — 같은 입력으로 apply 를 돌리면 같은 카드 때문에 거부된다.
        problems, _ = bs.apply_blanks(json.loads(json.dumps(delta)), deep,
                                      [r for r in bs.blank_requests(ce) if r[0] != "r"])
        self.assertTrue(problems and all(p.startswith("k.") for p in problems), problems)

    def test_cli_check_exit_and_untouched_files(self):
        delta, ce = self._1121c()
        with tempfile.TemporaryDirectory() as tmp:
            p = lambda n: os.path.join(tmp, n)
            for name, obj in (("d.json", delta), ("dp.json", _deep()), ("ce.json", ce)):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            mtimes = {n: os.path.getmtime(p(n)) for n in ("d.json", "dp.json", "ce.json")}
            self.assertEqual(bs.main(["check", p("ce.json"), p("d.json"), p("dp.json")]), 1)
            self.assertEqual({n: os.path.getmtime(p(n)) for n in mtimes}, mtimes)
            ce["cards"][1]["notes"] = ce["cards"][1]["notes"][:1]       # key_facts[0] 를 빼면(교열이 reject 로 옮김) 통과
            with open(p("ce.json"), "w", encoding="utf-8") as f:
                json.dump(ce, f, ensure_ascii=False)
            self.assertEqual(bs.main(["check", p("ce.json"), p("d.json")]), 0)

    # ── 칸 삭제 자동 통과·1차 이음(11-23 실전 fda483-196441) ──────────────────────
    @staticmethod
    def _1123():
        """11-23 의 꼴: 1차가 `hook_line` 을 칸 삭제로 정한 재작업 카드 — 2차가 같은 칸을 `fails` 로 올려 `보류`."""
        delta = _delta()
        delta["cards"]["k"] = {"title_issue": "제목", "summary": "요약", "hook_line": "대기 시간을 정하지 않음",
                               "key_facts": ["고친 사실"],
                               "en": {"title_issue": "T", "summary": "S", "hook_line": "No wait time", "key_facts": ["fixed"]}}
        first = {"status": "재작업", "rework": {"cards": ["k"]}, "cards": [
            {"web_card_id": "a", "verdict": "pass", "fails": [],
             "notes": [{"item": "칸 삭제", "slot": "checks[0]", "evidence": "사유"}]},
            {"web_card_id": "k", "verdict": "reject",
             "fails": [{"item": "③ 원문보다 강한 단정", "slot": "key_facts[0]", "evidence": "x"}],
             "notes": [{"item": "칸 삭제", "slot": "hook_line", "evidence": "원문보다 강한 단정"}]}]}
        second = {"status": "재작업", "round": 2, "rework": {"cards": ["k"]}, "cards": [
            {"web_card_id": "a", "verdict": "pass", "fails": [], "notes": []},
            {"web_card_id": "k", "verdict": "reject",
             "fails": [{"item": "③ 원문보다 강한 단정", "slot": "hook_line", "evidence": "1차와 같은 과장"}],
             "notes": []}]}
        return delta, first, second

    def test_reject_only_on_blankable_slots_passes_with_blank_notes(self):
        delta, _first, second = self._1123()
        before = json.dumps(delta, ensure_ascii=False, sort_keys=True)
        report = {}
        converted, remaining, _ = bs.deep_auto(second, delta=delta, deep=_deep(), report=report)
        self.assertEqual((converted, remaining, report["blanked"]), (["k"], [], ["k"]))
        k = second["cards"][1]
        self.assertEqual((k["verdict"], k["fails"]), ("pass", []))
        self.assertEqual([(n["item"], n["slot"]) for n in k["notes"]], [(bs.ITEM, "hook_line")])
        self.assertEqual((second["counts"]["reject"], second["rework"]["cards"]), (0, []))
        self.assertEqual(json.dumps(delta, ensure_ascii=False, sort_keys=True), before)   # 델타는 건드리지 않는다
        self.assertEqual(bs.refused_cards(second, delta, _deep()), {})                       # 마감(apply) 가능
        self.assertEqual(bs.deep_auto(second, delta=delta, deep=_deep())[:2], ([], []))      # 다시 돌려도 같다
        self.assertEqual(len(second["cards"][1]["notes"]), 1)

    def test_reject_stays_when_check_would_refuse_or_slot_is_required(self):
        delta, _first, second = self._1123()
        cases = [
            [{"item": "③", "slot": "key_facts[0]", "evidence": "핵심 사실 하나뿐"}],           # apply 거부 → 반려
            [{"item": "③", "slot": "summary", "evidence": "필수 칸"}],
            [{"item": "③", "slot": "hook_line", "evidence": "e"}, {"item": "②", "slot": "title_issue", "evidence": "e"}],
            [{"item": "렌더 게이트", "slot": "hook_line", "evidence": "코드 게이트는 풀지 않는다"}],
        ]
        for fails in cases:
            ce = json.loads(json.dumps(second))
            ce["cards"][1]["fails"] = fails
            self.assertEqual(bs.deep_auto(ce, delta=delta, deep=_deep())[1], ["k"], fails)
            self.assertEqual(ce["cards"][1]["fails"], fails)
        # 델타를 주지 않으면 종전대로(심층만 반려만 통과)
        self.assertEqual(bs.deep_auto(json.loads(json.dumps(second)))[1], ["k"])
        # en. 접두는 국·영 짝으로 빼므로 같은 칸이다
        ce = json.loads(json.dumps(second))
        ce["cards"][1]["fails"][0]["slot"] = "en.hook_line"
        self.assertEqual(bs.deep_auto(ce, delta=delta, deep=_deep())[1], [])
        self.assertEqual(ce["cards"][1]["notes"][0]["slot"], "hook_line")

    def test_carry_first_keeps_first_round_blank_decisions(self):
        delta, first, second = self._1123()
        first_delta = json.loads(json.dumps(delta))
        # 반려 안 된 카드 a 의 1차 칸 삭제는 2차가 적지 않아도 잇는다. 재작업 카드 k 는 칸 글자가 1차와 같으면 잇는다.
        ce = json.loads(json.dumps(second))
        ce["cards"][1]["fails"] = [{"item": "③", "slot": "summary", "evidence": "다른 반려"}]
        carried, skipped = bs.carry_first(ce, first, delta, None, first_delta, None)
        self.assertEqual((carried, skipped), (["a.checks[0]", "k.hook_line"], []))
        self.assertEqual(bs.carry_first(ce, first, delta, None, first_delta, None), ([], []))   # 다시 돌려도 같다
        # 기자가 hook_line 을 고쳤으면(1차 델타와 다르면) · 1차 델타가 없으면 재작업 카드는 잇지 않는다(리허설 1122a).
        changed = json.loads(json.dumps(first_delta))
        changed["cards"]["k"]["hook_line"] = "옛 문장"
        for fd in (changed, None):
            ce = json.loads(json.dumps(second))
            ce["cards"][1]["fails"] = [{"item": "③", "slot": "summary", "evidence": "다른 반려"}]
            self.assertEqual(bs.carry_first(ce, first, delta, None, fd, None), (["a.checks[0]"], ["k.hook_line"]))
        # 2차가 같은 칸을 다른 이름으로 적었거나(checks) 반려로 옮겼으면 잇지 않는다(둘 다면 apply 가 거부 — 리허설 1121c).
        ce = json.loads(json.dumps(second))
        ce["cards"][0]["notes"] = [{"item": "칸 삭제", "slot": "checks", "evidence": "2차가 통째로"}]
        self.assertEqual(bs.carry_first(ce, first, delta, None, first_delta, None)[0], [])
        self.assertEqual(bs.refused_cards(ce, delta), {})

    def test_cli_deep_auto_reads_sibling_delta_and_r1(self):
        delta, first, second = self._1123()
        with tempfile.TemporaryDirectory() as tmp:
            p = lambda n: os.path.join(tmp, n)
            for name, obj in (("delta.json", delta), ("copyedit.r1.json", first), ("copyedit.json", second)):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            self.assertEqual(bs.main(["deep-auto", p("copyedit.json")]), 0)
            with open(p("copyedit.json"), encoding="utf-8") as f:
                got = json.load(f)
            self.assertEqual(got["counts"]["reject"], 0)
            self.assertEqual([(n["item"], n["slot"]) for n in got["cards"][0]["notes"]], [(bs.ITEM, "checks[0]")])
            # 다른 호의 1차 기록(카드가 다르다)은 잇지 않는다
            other = dict(first, cards=[dict(first["cards"][0], web_card_id="zz")])
            with open(p("copyedit.r1.json"), "w", encoding="utf-8") as f:
                json.dump(other, f, ensure_ascii=False)
            with open(p("copyedit.json"), "w", encoding="utf-8") as f:
                json.dump(second, f, ensure_ascii=False)
            self.assertEqual(bs.main(["deep-auto", p("copyedit.json")]), 0)
            with open(p("copyedit.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["cards"][0]["notes"], [])

    # ── 카드 단위 보류(defer · 2026-11-23 대표 방침) ──────────────────────
    def test_defer_moves_only_unfixable_cards_and_keeps_issue(self):
        delta, _first, second = self._1123()
        delta["tldr"] = ["Acme Labs 경고서한", "다른 소식"]
        delta["tldr_en"] = ["Acme Labs warned", "Other news"]
        delta["deferred"] = []
        deep = _deep()
        deep["k"] = {"deep_analysis": {"key_violations": [{"observation": "x"}]}}
        ce = json.loads(json.dumps(second))
        ce["cards"][1]["fails"] = [{"item": "③", "slot": "summary", "evidence": "필수 칸"}]
        scaffold = {"cards": [{"id": "k", "headline_target": "Acme Labs"}, {"id": "a", "headline_target": "Other Co"}]}
        targets, problems = bs.defer_cards(ce, delta, deep, {"a": []}, scaffold)
        self.assertEqual((targets, problems), (["k"], []))           # a 는 문제 목록이 비어 미루지 않는다
        self.assertEqual(list(delta["cards"]), ["a"])
        self.assertNotIn("k", deep)
        self.assertEqual(delta["deferred"], ["k"])
        self.assertEqual((delta["tldr"], delta["tldr_en"]), (["다른 소식"], ["Other news"]))
        self.assertEqual([c["web_card_id"] for c in ce["cards"]], ["a"])
        self.assertEqual(ce["deferred_cards"][0]["web_card_id"], "k")
        self.assertEqual((ce["counts"]["reject"], ce["counts"]["deferred"], ce["rework"]["cards"]), (0, 1, []))
        # 근거 미비 카드도 미룬다 · 상한을 넘으면 아무것도 바꾸지 않는다 · 요약이 전부 미룰 카드면 미루지 않는다.
        delta2, _f, ce2 = self._1123()
        self.assertEqual(bs.defer_cards(ce2, delta2, None, {"a": ["checked 없음"]}, cap=5)[0], ["a", "k"])
        delta3, _f, ce3 = self._1123()
        before = json.dumps([delta3, ce3], ensure_ascii=False, sort_keys=True)
        self.assertTrue(bs.defer_cards(ce3, delta3, None, {"a": ["x"]}, cap=1)[1])
        self.assertEqual(json.dumps([delta3, ce3], ensure_ascii=False, sort_keys=True), before)
        delta4, _f, ce4 = self._1123()
        delta4["tldr"], delta4["tldr_en"] = ["Acme Labs 경고서한"], ["Acme Labs warned"]
        self.assertTrue(bs.defer_cards(ce4, delta4, None, {}, {"cards": [{"id": "k", "headline_target": "Acme Labs"}]})[1])

    def test_cli_defer_passes_blankable_and_defers_the_rest(self):
        delta, first, second = self._1123()
        delta["cards"]["s"] = {"title_issue": "제목", "summary": "요약", "key_facts": ["사실"],
                               "en": {"title_issue": "T", "summary": "S", "key_facts": ["F"]}}
        for ce in (first, second):
            ce["cards"] = [dict(c, source_absent="본문 없는 행(테스트)") for c in ce["cards"]]
        first["cards"].append({"web_card_id": "s", "verdict": "reject", "source_absent": "테스트",
                               "fails": [{"item": "③", "slot": "summary", "evidence": "필수"}]})
        first["rework"]["cards"].append("s")
        second["cards"].append({"web_card_id": "s", "verdict": "reject", "source_absent": "테스트",
                                "fails": [{"item": "③", "slot": "summary", "evidence": "2차에도 남음"}]})
        second["rework"]["cards"].append("s")
        with tempfile.TemporaryDirectory() as tmp:
            p = lambda n: os.path.join(tmp, n)
            for name, obj in (("delta.json", delta), ("copyedit.r1.json", first), ("copyedit.json", second),
                              ("handoff.json", {"run_date_kst": "2099-01-01", "rows": []})):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            self.assertEqual(bs.main(["defer", tmp, p("handoff.json")]), 0)
            with open(p("copyedit.json"), encoding="utf-8") as f:
                got = json.load(f)
            with open(p("delta.json"), encoding="utf-8") as f:
                d = json.load(f)
            self.assertEqual(sorted(d["cards"]), ["a", "k"])           # k 는 칸 삭제로 통과, s 만 미룸
            self.assertEqual(d["deferred"], ["s"])
            self.assertEqual(got["counts"]["reject"], 0)
            self.assertEqual([c["web_card_id"] for c in got["deferred_cards"]], ["s"])
            # 1차 판정 그대로에는 defer 를 쓰지 않는다(1차 반려는 재작업으로) — --rework-unfinished 일 때만.
            for name, obj in (("copyedit.json", first), ("delta.json", delta)):
                with open(p(name), "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False)
            os.remove(p("copyedit.r1.json"))
            self.assertEqual(bs.main(["defer", tmp, p("handoff.json")]), 2)
            self.assertEqual(bs.main(["defer", tmp, p("handoff.json"), "--rework-unfinished"]), 0)
            with open(p("delta.json"), encoding="utf-8") as f:
                self.assertEqual(sorted(json.load(f)["cards"]), ["a"])

    def test_copyedit_md_two_editions_point_to_the_script(self):
        for folder in ((), ("next",)):
            with open(os.path.join(ROOT, "company", "editorial", *folder, "copyedit.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("2-3. **칸 삭제", text, folder)
            self.assertIn("python company/editorial/blank_slots.py apply company/run/{run_date_kst}/copyedit.json", text, folder)
            self.assertIn("blank_slots.py verify", text, folder)
            self.assertIn("**재작업(3-1)으로 되돌릴 때는 반영하지 않는다**", text, folder)
            self.assertIn("2-4. **심층 삭제", text, folder)
            self.assertIn("python company/editorial/blank_slots.py deep-auto company/run/{run_date_kst}/copyedit.json", text, folder)
            self.assertIn("--log company/run/{run_date_kst}/slot_removals.json", text, folder)
            self.assertIn("reject 1~25장", text, folder)
            self.assertNotIn("reject 1~10장", text, folder)
            self.assertIn(bs.TRANSLATION_NOTICE, text, folder)
            self.assertIn("2-5. **칸 삭제 사전 점검", text, folder)
            self.assertIn("python company/editorial/blank_slots.py check company/run/{run_date_kst}/copyedit.json", text, folder)
            self.assertLess(text.index("2-5. **칸 삭제 사전 점검"), text.index("3-1. **반려가 있으면"), folder)
            self.assertIn("**지울 수 있는 칸만 걸린 반려도 코드가 통과시킨다", text, folder)
            self.assertIn("1차 칸 삭제는 `deep-auto` 가 잇는다", text, folder)
            self.assertNotIn("반려가 하나라도 있으면 그대로 `reject` 다.", text, folder)
            self.assertIn("3-3. **카드 단위 보류", text, folder)
            self.assertIn("python company/editorial/blank_slots.py defer company/run/{run_date_kst} _handoff/handoff_$D.json", text, folder)
            self.assertIn("--rework-unfinished", text, folder)
            self.assertNotIn("**2차에도 reject 가 남으면 `보류`**", text, folder)
            self.assertIn("**2차 재작업 카드는 `checked` 를 새로 적는다**", text, folder)
        for folder in ((), ("next",)):
            with open(os.path.join(ROOT, "company", "editorial", *folder, "RUN.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("reject 가 1~25장", text, folder)
            self.assertIn("샤드 파일별로 서브 에이전트에 나눠", text, folder)
        self.assertEqual(bs.REWORK_CAP, 25)


if __name__ == "__main__":
    unittest.main()
