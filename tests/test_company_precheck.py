# -*- coding: utf-8 -*-
"""편집팀 사전 점검 도구(`company/editorial/precheck.py`) — 조립 검사·발행 브릿지와 같은 규칙인지."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import delta_bridge
import inject_slots

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("company_precheck", ROOT / "company" / "editorial" / "precheck.py")
precheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precheck)


def _card(**kw):
    base = {"title_issue": "무균공정 관리 미흡", "summary": "FDA가 관찰사항 3건을 적었다.",
            "key_facts": ["관찰사항: 3건"], "implication": "", "checks": [],
            "en": {"title_issue": "Aseptic control gaps", "summary": "FDA listed three observations.",
                   "key_facts": ["Observations: 3"], "implication": "", "checks": []}}
    base.update(kw)
    return base


class TermsTest(unittest.TestCase):
    def test_clean_delta_passes(self):
        self.assertEqual(precheck.term_lines({"cards": {"a": _card()}}), [])

    def test_bare_abbreviation_and_left_column_term_are_caught(self):
        # 리허설 1113c 의 반려: 'CAPA' 가 풀이 없이 처음 나옴.
        delta = {"cards": {"a": _card(implication="CAPA 절차가 수립되지 않았다."),
                           "b": _card(summary="규격 이탈 결과를 조사하지 않았다."),
                           "c": _card(implication="시정 및 예방조치(CAPA) 절차가 없었다. CAPA 기록도 없었다.")}}
        lines = [x for x in precheck.term_lines(delta) if x.startswith("용어 표기")]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("'CAPA' 단독" in x and "a" in x for x in lines))
        self.assertTrue(any("기준일탈(OOS)" in x and "b" in x for x in lines))

    def test_same_rules_as_the_assembly_lint(self):
        delta = {"cards": {"a": _card(summary="데이터 무결성 문제가 있었다.")}}
        self.assertEqual(precheck.term_lines(delta), inject_slots.lint_prose_style(delta).lines())

    def test_gate_catches_the_1118_key_facts_block(self):
        # 리허설 1118: key_facts 5개 카드 6장이 사전 점검을 통과하고 조립(exit 2)에서 호 전체를 막았다.
        n, (lo, hi) = inject_slots._MAX_KEY_FACTS, inject_slots._CHECKS_RANGE
        ok = _card(key_facts=["사실"] * n, checks=["점검"] * hi,
                   en=dict(_card()["en"], key_facts=["fact"] * n, checks=["check"] * hi))
        self.assertEqual(precheck.gate_lines({"cards": {"a": ok}}), [])
        over = _card(key_facts=["사실"] * (n + 1), checks=["점검"] * (hi + 1),
                     en=dict(_card()["en"], key_facts=["fact"] * (n + 1), checks=["check"] * (hi + 1)))
        lines = precheck.gate_lines({"cards": {"a": over}})
        self.assertTrue(any(f"a.key_facts: {n + 1}개" in x for x in lines))
        self.assertTrue(any(f"a.en.key_facts: {n + 1}개" in x for x in lines))
        self.assertTrue(any("a.checks" in x for x in lines))

    def test_gate_is_the_assembly_validator_itself(self):
        # 규칙을 베끼지 않는다 — 조립 검증 함수가 내는 오류 = 사전 점검 오류(접두만 다름). 조립에 가드가 늘면 저절로 따라온다.
        bad = {"cards": {
            "a": _card(key_facts=["x"] * 9),
            "b": _card(summary="**굵게** 요약", title_issue="가" * 40),
            "c": _card(en={"title_issue": "only title"}),
            "d": _card(checks=["하나"]),
        }}
        brief = {"cards": [{"id": cid} for cid in sorted(bad["cards"])]}
        expected = inject_slots.validate_injection(brief, bad).errors
        self.assertTrue(expected)
        self.assertEqual(precheck.gate_lines(bad), [f"조립 검사 {e}" for e in expected])

    def test_assembly_gates_after_injection_are_caught(self):
        # 리허설 1120: 주입 검증은 통과했는데 발행본 관문(원문 확보 카드의 부재 단정)이 호 전체를 막았다.
        import assemble_publish_brief as apb
        body_type = sorted(apb._BODY_DETAIL_TYPES)[0]
        claim = "관찰은 기록이 없어 실제로 했는지 확인되지 않았다."
        self.assertTrue(apb._FALSE_ABSENCE_RE.search(claim))      # 전제: 조립 정규식이 이 문장을 잡는다
        scaffold = {"brief": {"run_date_kst": "2026-11-16"},
                    "cards": [{"id": "a", "card_type": "FDA 483 실사 관찰",
                               "deterministic_detail": {"type": body_type}}]}
        bad = {"cards": {"a": _card(implication=claim,
                                    en=dict(_card()["en"], implication="Not verified."))}}
        lines = precheck.assembly_lines(bad, scaffold)
        self.assertTrue(any("'a'" in x and "부재를 주장" in x for x in lines), lines)
        ok = {"cards": {"a": _card(implication="수행 기록이 남아 있지 않았다.",
                                   en=dict(_card()["en"], implication="No record was kept."))}}
        self.assertFalse([x for x in precheck.assembly_lines(ok, scaffold) if "부재를 주장" in x])
        self.assertEqual(precheck.assembly_lines(bad, None), [])     # 스캐폴드 없으면 이 검사는 건너뛴다

    def test_gate_without_scaffold_skips_only_quote_alignment(self):
        card = _card(quotes_translation=["번역"])
        lines = precheck.gate_lines({"cards": {"a": card}})
        self.assertFalse([x for x in lines if "quotes_translation" in x])
        scaffold = {"cards": [{"id": "a", "quotes": [{"text": "q1"}, {"text": "q2"}]}]}
        with_sc = precheck.gate_lines({"cards": {"a": card}}, scaffold=scaffold)
        self.assertEqual(with_sc, [f"조립 검사 {e}" for e in
                                   inject_slots.validate_injection(scaffold, {"cards": {"a": card}}).errors])

    def test_length_limits_are_the_code_limits(self):
        ko, en = inject_slots._HOOK_LINE_MAX["ko"], inject_slots._HOOK_LINE_MAX["en"]
        t = inject_slots._MAX_TITLE_ISSUE
        ok = _card(title_issue="가" * t, hook_line="가" * ko,
                   en=dict(_card()["en"], title_issue="a" * (t * 2), hook_line="a" * en))
        self.assertEqual(precheck.term_lines({"cards": {"a": ok}}), [])
        over = _card(title_issue="가" * (t + 1), hook_line="가" * (ko + 1),
                     en=dict(_card()["en"], title_issue="a" * (t * 2 + 1), hook_line="a" * (en + 1)))
        lines = precheck.term_lines({"cards": {"a": over}})
        self.assertEqual(len(lines), 4)
        self.assertTrue(any(f"a.en.title_issue: {t * 2 + 1}자" in x for x in lines))

    def test_empty_required_slot_and_missing_english_pair_are_caught(self):
        # 리허설 1114: 소식 카드 4장의 key_facts 가 한국어·영문 모두 [] → merge_parts 가 호 전체를 거부했다.
        thin = _card(key_facts=[])
        thin["en"] = dict(thin["en"], key_facts=[])
        self.assertEqual(precheck.term_lines({"cards": {"n1": thin}}),
                         ["빈 필수 칸 n1: key_facts", "영문 슬롯 미완결 n1: en.key_facts"])
        # 한국어가 채운 시사점은 영문도 채워야 한다. 한국어가 비운 칸은 영문도 비어 있는 것이 맞다.
        half = _card(implication="원문은 재발 방지 계획을 요구했다.")
        self.assertEqual(precheck.term_lines({"cards": {"h": half}}), ["영문 슬롯 미완결 h: en.implication"])
        no_en = _card()
        del no_en["en"]
        self.assertEqual(precheck.term_lines({"cards": {"x": no_en}}),
                         ["영문 슬롯 미완결 x: en.title_issue, en.summary, en.key_facts"])

    def test_pairing_rule_is_the_merge_rule(self):
        import importlib.util as _u
        spec = _u.spec_from_file_location("company_merge_parts_x", ROOT / "company" / "editorial" / "merge_parts.py")
        mp = _u.module_from_spec(spec)
        spec.loader.exec_module(mp)
        for card in (_card(), _card(key_facts=[]), _card(implication="문장."), {"title_issue": "가"}):
            got = [x for x in precheck.term_lines({"cards": {"c": card}}) if x.startswith("영문 슬롯 미완결")]
            self.assertEqual(bool(got), bool(mp._en_gaps(card)), card)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, bad = os.path.join(tmp, "g.json"), os.path.join(tmp, "b.json")
            Path(good).write_text(json.dumps({"cards": {"a": _card()}}, ensure_ascii=False), encoding="utf-8")
            Path(bad).write_text(json.dumps({"cards": {"a": _card(implication="CAPA 가 없었다.")}},
                                            ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(precheck.main(["terms", good]), 0)
                self.assertEqual(precheck.main(["terms", bad]), 1)
                self.assertEqual(precheck.main(["terms", os.path.join(tmp, "none.json")]), 2)


class DeepSourceTest(unittest.TestCase):
    HANDOFF = {"rows": [
        {"web_card_id": "wl-1", "deep_analysis_input": {"body_full": "원문 하나"}},
        {"web_card_id": "wl-1", "deep_analysis_input": {"body_full": "다른 원문"}},      # 먼저 만난 것이 정본
        {"web_card_id": "fda483-2", "deep_analysis_input": {"body_full": "  "}},
        {"web_card_id": "hc-3"},
        "쓰레기",
    ]}

    def test_bodies_match_the_bridge_extractor(self):
        with mock.patch.object(delta_bridge, "log"):
            want = delta_bridge.extract_handoff_body_full(self.HANDOFF)
        self.assertEqual(precheck.handoff_bodies(self.HANDOFF), want)
        self.assertEqual(want, {"wl-1": "원문 하나"})

    def test_copy_gets_the_same_source_text_the_bridge_applies(self):
        deep = {"wl-1": {"deep_analysis": {"x": 1}}, "fda483-2": {"deep_analysis": {}}, "odd": "문자열"}
        before = json.dumps(deep, ensure_ascii=False, sort_keys=True)
        out, filled = precheck.with_source_text(deep, precheck.handoff_bodies(self.HANDOFF))
        self.assertEqual(json.dumps(deep, ensure_ascii=False, sort_keys=True), before)   # 원본은 그대로
        bridged = json.loads(before)
        with mock.patch.object(delta_bridge, "log"):
            delta_bridge.apply_handoff_source_text(bridged, {"wl-1": "원문 하나"})
        self.assertEqual(out, bridged)
        self.assertEqual(filled, 1)
        self.assertNotIn("source_text", out["fda483-2"])

    def test_cli_writes_a_separate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            h, d, o = (os.path.join(tmp, n) for n in ("h.json", "d.json", "o.json"))
            Path(h).write_text(json.dumps(self.HANDOFF, ensure_ascii=False), encoding="utf-8")
            Path(d).write_text(json.dumps({"wl-1": {"deep_analysis": {}}}, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["deep-source", h, d, "--out", o]), 0)
            self.assertEqual(json.loads(Path(o).read_text(encoding="utf-8"))["wl-1"]["source_text"], "원문 하나")
            self.assertNotIn("source_text", json.loads(Path(d).read_text(encoding="utf-8"))["wl-1"])


class NestedTranslationTest(unittest.TestCase):
    """리허설 1114b: 심층 기자가 observations_ko 를 deep_analysis 안에 넣어, 교열의 조립 검사가 483 관찰 국문 없음으로 거부했다."""

    OBS = [{"number": "1", "deficiency_ko": "교육을 받지 않았다.", "detail_ko": "구체적으로 …"}]

    def _nested(self):
        return {"deep_analysis": {"key_violations": [], "observations_ko": list(self.OBS),
                                  "violations_ko": [{"number": "1"}]}}

    def test_lift_matches_the_bridge_gate(self):
        entry = self._nested()
        before = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        lifted = precheck.lift_nested(entry)
        self.assertEqual(json.dumps(entry, ensure_ascii=False, sort_keys=True), before)      # 원본은 그대로
        self.assertEqual(lifted["observations_ko"], self.OBS)
        self.assertNotIn("observations_ko", lifted["deep_analysis"])
        self.assertNotIn("violations_ko", lifted["deep_analysis"])
        # 브릿지 게이트가 같은 입력을 어떻게 바꾸는지와 비교한다(게이트 판정은 통과로 고정).
        bridged = {"x": json.loads(before)}
        gate = mock.MagicMock()
        gate.run_deep_analysis_gate.return_value = mock.MagicMock(ok=True)
        with mock.patch.object(delta_bridge, "_vda", gate), mock.patch.object(delta_bridge, "log"):
            kept = delta_bridge._gate_deep_analysis(bridged)
        self.assertEqual(kept["x"], lifted)
        self.assertEqual(set(precheck.NESTED_TRANSLATION_KEYS), {"observations_ko", "violations_ko"})

    def test_card_level_translation_wins_over_nested(self):
        entry = dict(self._nested(), observations_ko=[{"number": "9", "deficiency_ko": "카드 층"}])
        self.assertEqual(precheck.lift_nested(entry)["observations_ko"][0]["number"], "9")

    def test_untouched_when_nothing_is_nested(self):
        entry = {"deep_analysis": {"key_violations": []}, "observations_ko": self.OBS}
        self.assertIs(precheck.lift_nested(entry), entry)

    def test_deep_source_copy_is_lifted_and_assemblable(self):
        out, filled = precheck.with_source_text({"fda483-1": self._nested()}, {"fda483-1": "원문"})
        self.assertEqual(filled, 1)
        self.assertEqual(out["fda483-1"]["observations_ko"], self.OBS)
        self.assertEqual(out["fda483-1"]["source_text"], "원문")

    def test_shard_check_flags_nested_missing_and_incomplete(self):
        handoff = {"rows": [{"web_card_id": "fda483-1", "kind": "fda-483"}, {"web_card_id": "fda483-2", "kind": "fda-483"},
                            {"web_card_id": "fda483-3", "kind": "fda-483"}, {"web_card_id": "wl-1", "kind": "warning-letter"}]}
        ids = precheck.fda483_ids(handoff)
        self.assertEqual(ids, {"fda483-1", "fda483-2", "fda483-3"})
        shard = {"items": {"fda483-1": {"deep_analysis": {}, "observations_ko": self.OBS},
                           "fda483-2": self._nested(),
                           "fda483-3": {"deep_analysis": {}, "observations_ko": [{"number": "1"}]},
                           "wl-1": {"deep_analysis": {}}}}
        lines = precheck.deep_lines(shard, ids)
        self.assertEqual(len(lines), 3)
        self.assertTrue(any(x.startswith("자리 틀림 fda483-2: observations_ko") for x in lines))
        self.assertTrue(any(x.startswith("자리 틀림 fda483-2: violations_ko") for x in lines))
        self.assertTrue(any(x.startswith("483 관찰 국문 불완전 fda483-3") for x in lines))
        gone = {"items": {"fda483-1": {"deep_analysis": {}}}}
        self.assertEqual(precheck.deep_lines(gone, ids), ["483 관찰 국문 없음 fda483-1: observations_ko(★필수)"])

    def test_cli_and_instructions(self):
        # 심층 근거 게이트(1120d)는 DeepGate1120dTest 가 본다 — 원문 없는 최소 픽스처라 여기서는 통과로 고정.
        import verify_deep_analysis as vda
        patcher = mock.patch.object(vda, "run_deep_analysis_gate",
                                    return_value=vda.GateResult(ok=True, findings=[], report=""))
        patcher.start()
        self.addCleanup(patcher.stop)
        with tempfile.TemporaryDirectory() as tmp:
            h, good, bad = (os.path.join(tmp, n) for n in ("h.json", "g.json", "b.json"))
            Path(h).write_text(json.dumps({"rows": [{"web_card_id": "fda483-1", "kind": "fda-483"}]}), encoding="utf-8")
            Path(good).write_text(json.dumps({"items": {"fda483-1": {"deep_analysis": {}, "observations_ko": self.OBS}}},
                                             ensure_ascii=False), encoding="utf-8")
            Path(bad).write_text(json.dumps({"items": {"fda483-1": self._nested()}}, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["deep", h, good]), 0)
                self.assertEqual(precheck.main(["deep", h, bad]), 1)
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            text = ROOT.joinpath(*folder, "reporter.md").read_text(encoding="utf-8")
            self.assertIn("`items.<web_card_id>.observations_ko` 이고 `deep_analysis` 안이 아니다", text, folder)
        self.assertIn("python company/editorial/precheck.py deep _handoff/handoff_{run_date_kst}.json",
                      ROOT.joinpath("company", "editorial", "next", "reporter.md").read_text(encoding="utf-8"))


class ReworkDiffTest(unittest.TestCase):
    OLD = {"cards": {"a": _card(), "b": _card(summary="용출 규격 이탈로 회수했다."), "c": _card()},
           "tldr": ["하나", "둘", "셋"], "handoff_ref": {"date": "2026-11-13"}}
    R1 = {"status": "재작업", "round": 1, "rework": {"cards": ["b"]},
          "cards": [{"web_card_id": "a", "verdict": "pass"}, {"web_card_id": "b", "verdict": "reject"},
                    {"web_card_id": "c", "verdict": "pass"}]}

    def _new(self, **cards):
        new = json.loads(json.dumps(self.OLD, ensure_ascii=False))
        new["cards"].update(cards)
        return new

    def test_only_rejected_card_changed_passes_and_names_the_slots(self):
        new = self._new(b=_card(summary="용출 기준일탈(OOS)로 회수했다."))
        problems, notes = precheck.rework_diff(self.OLD, new, precheck.rejected_ids(self.R1))
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["고침 b: summary"])

    def test_touching_a_passed_card_is_out_of_scope(self):
        new = self._new(a=_card(summary="다른 문장."), b=_card(summary="고친 문장."))
        problems, _ = precheck.rework_diff(self.OLD, new, {"b"})
        self.assertEqual(problems, ["반려되지 않은 카드가 바뀌었다: a (summary)"])

    def test_added_removed_cards_and_tldr_change_are_out_of_scope(self):
        new = self._new(d=_card())
        del new["cards"]["c"]
        new["tldr"] = ["바뀜", "둘", "셋"]
        problems, _ = precheck.rework_diff(self.OLD, new, {"b"})
        self.assertEqual(problems, ["카드가 사라졌다: c", "카드가 새로 생겼다: d", "카드 밖의 값이 바뀌었다: tldr"])

    def test_untouched_rejected_card_is_reported_not_blocked(self):
        problems, notes = precheck.rework_diff(self.OLD, self._new(), {"b"})
        self.assertEqual((problems, notes), ([], ["안 바뀜(반려 카드) b"]))

    def test_no_rejects_recorded_is_a_problem(self):
        problems, _ = precheck.rework_diff(self.OLD, self._new(), precheck.rejected_ids({"cards": []}))
        self.assertEqual(problems, ["1차 교열 기록에 반려 카드가 없다"])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {n: os.path.join(tmp, n + ".json") for n in ("old", "good", "bad", "r1")}
            for name, obj in (("old", self.OLD), ("good", self._new(b=_card(summary="고친 문장."))),
                              ("bad", self._new(a=_card(summary="다른 문장."))), ("r1", self.R1)):
                Path(paths[name]).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["rework-diff", paths["old"], paths["good"], paths["r1"]]), 0)
                self.assertEqual(precheck.main(["rework-diff", paths["old"], paths["bad"], paths["r1"]]), 1)


class ReworkInstructionsTest(unittest.TestCase):
    """재작업 1회(2026-11-14 대표 결정) — 지시문·브릿지·점검 도구가 같은 이름을 쓰는지."""

    def _read(self, *parts):
        return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_both_instruction_sets_describe_the_same_round_trip(self):
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            copyedit = self._read(*folder, "copyedit.md")
            self.assertIn('"status": "재작업"', copyedit, folder)
            self.assertIn("python company/editorial/precheck.py rework-diff _scaffold/delta.r1.json", copyedit, folder)
            self.assertIn("copyedit.r1.json", copyedit, folder)
            self.assertIn("4. **마감 — 코드 검사 exit 0 이고 reject 0 일 때만.**", copyedit, folder)   # 마감 조건은 그대로
            reporter = self._read(*folder, "reporter.md")
            self.assertIn("## 재작업 — 교열이 반려한 카드만 한 번 고친다", reporter, folder)
            self.assertIn('{"status":"완료","round":1', reporter, folder)
            run = self._read(*folder, "RUN.md")
            self.assertIn("### 5-1. 재작업 1회", run, folder)
            self.assertIn("**09:25 시한은 그대로다**", run, folder)

    def test_bridge_never_takes_a_rework_round(self):
        # 브릿지는 완료만 가져간다 — '재작업' 상태의 반납(1차 delta.json)이 발행되면 안 된다.
        self.assertIn('if copyedit.get("status") != "완료":', self._read("delta_bridge.py"))

    def test_recall_reason_exception_is_in_the_term_table_and_reporter(self):
        self.assertIn("회수 사유의 `○○ 규격 부적합`(원문 `Failed … Specification(s)`)은 그대로 쓴다",
                      self._read("docs", "prompts", "GRM_Prompt_v16.md"))
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            self.assertIn("회수 사유의 `○○ 규격 부적합`(원문 `Failed … Specification(s)`)은 그대로 쓴다",
                          self._read(*folder, "reporter.md"), folder)

    def test_rehearsal_check_accepts_round_two_only_with_records(self):
        spec = importlib.util.spec_from_file_location(
            "company_rehearsal_check", ROOT / "company" / "editorial" / "rehearsal_check.py")
        rc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rc)
        done = {"status": "완료", "round": 2, "code_gate": {"ran": True, "exit": 0},
                "cards": [], "counts": {"reject": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            def four():
                return [r for r in rc.check(tmp, os.path.join(tmp, "none.json")) if r[0] == 4][0][2]
            Path(tmp, "copyedit.json").write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(four())                                   # 재작업 기록이 없다
            Path(tmp, "rework.json").write_text('{"status": "완료", "round": 1}', encoding="utf-8")
            Path(tmp, "copyedit.r1.json").write_text('{"status": "재작업", "round": 1}', encoding="utf-8")
            self.assertTrue(four())
            Path(tmp, "copyedit.json").write_text(
                json.dumps(dict(done, counts={"reject": 1}), ensure_ascii=False), encoding="utf-8")
            self.assertFalse(four())                                   # 2차에도 반려가 남았다
            Path(tmp, "copyedit.json").write_text(
                json.dumps(dict(done, round=1), ensure_ascii=False), encoding="utf-8")
            self.assertTrue(four())                                    # 1차에 바로 통과(종전)


class InstructionsPointAtTheToolTest(unittest.TestCase):
    def _read(self, *parts):
        return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")

    def test_reporters_run_terms_and_next_copyedit_runs_deep_source(self):
        for folder in (("company", "editorial"), ("company", "editorial", "next")):
            self.assertIn("python company/editorial/precheck.py terms company/run/{run_date_kst}/delta.{S}.json",
                          self._read(*folder, "reporter.md"), folder)
        copyedit = self._read("company", "editorial", "next", "copyedit.md")
        self.assertIn("python company/editorial/precheck.py deep-source _handoff/handoff_$D.json", copyedit)
        self.assertIn("--deep _scaffold/deep_check.json", copyedit)

    def test_rehearsal_success_still_requires_zero_rejects(self):
        self.assertIn("4. 교열 reject 가 0 이다", self._read("company", "editorial", "REHEARSAL.md"))
        self.assertIn("n_reject == 0", self._read("company", "editorial", "rehearsal_check.py"))


class DeepTermsBlockTest(unittest.TestCase):
    """리허설 1120b: 심층 산문 [용어·표기]가 경고만이라 교열이 19장을 반려했다 → 델타와 같은 함수로 막는다."""

    def _shard(self, **da):
        return {"status": "완료", "items": {"fda483-1": {"deep_analysis": da}}}

    def test_same_function_as_delta(self):
        # 같은 문장이 델타 카드에서 걸리는 라벨과 심층 칸에서 걸리는 라벨이 같다(규칙 한 벌).
        text = "배치 기록에 QC 검토가 없고 OOS 조사를 하지 않았다."
        delta_labels = set(inject_slots.lint_prose_style({"cards": {"a": {"summary": text}}}).terms)
        deep_labels = {lab for _, _, lab in inject_slots.deep_term_hits(
            self._shard(key_violations=[{"observation": text}]))}
        self.assertEqual(delta_labels, deep_labels)
        self.assertEqual(deep_labels, set(inject_slots.term_hits(text)))

    def test_1120b_rejections_are_caught(self):
        for bad, lab in (("이미 출하 승인된 배치 기록의 설비 모델번호", "제조기록서"),
                         ("배지충전 배치기록의 인쇄본", "제조기록서"),
                         ("QC 검토 보고서의 부서 회신", "'QC'"),
                         ("HPLC 컬럼 공급업체 4건", "'HPLC'"),
                         ("QA 관리자는 이 시료를 입고할", "'QA'"),
                         ("환경 모니터링(EM) 능동 공기 시료", "'EM'"),
                         ("AQL 표본 검사를 하지 않으며", "'AQL'"),
                         ("경향이탈(OOT) 내용을 로그북에", "'OOT'"),
                         ("회사의 OOS 절차 FDASOP002는", "OOS"),
                         ("기준일탈(OOS) 조사 4건 뒤에 회사의 모든 OOS 조사가", "이후 기준일탈")):
            lines = precheck.deep_term_lines(self._shard(key_violations=[{"observation": bad}]))
            self.assertTrue(any(lab in x for x in lines), (bad, lines))
            self.assertTrue(all(x.startswith("심층 용어 fda483-1.key_violations[0].observation: ") for x in lines))

    def test_clean_prose_and_identifiers_pass(self):
        for ok in ("기준일탈(OOS) 조사 OOS-24006 과 OOS/22/002 를 종결하지 않았다.",
                   "제조기록서와 '제조지시 및 기록서'를 대조하지 않았다.",
                   "SOP QU-SOP-0056623 은 경향이탈 결과를 로그북에 적으라고 한다.",
                   "품질관리 시험실의 고성능 액체크로마토그래피 원시 데이터를 검토하지 않았다.",
                   "FDA가 GMP·21 CFR 211.192 위반을 적었다. 표준작업절차서(SOP)·원료의약품(API)."):
            self.assertEqual(precheck.deep_term_lines(self._shard(key_violations=[{"observation": ok}])), [], ok)

    def test_first_mention_is_card_level_for_capa(self):
        # v16 '시정 및 예방조치(CAPA) — 처음 한 번, 이후 CAPA': 다른 칸에 한글이 있으면 통과.
        shard = self._shard(key_violations=[{"observation": "시정 및 예방조치(CAPA)를 세우지 않았다."},
                                            {"observation": "CAPA 효과성을 평가하지 않았다."}])
        self.assertEqual(precheck.deep_term_lines(shard), [])
        lone = self._shard(key_violations=[{"observation": "CAPA 효과성을 평가하지 않았다."}])
        self.assertTrue(precheck.deep_term_lines(lone))

    def test_original_quote_not_checked(self):
        shard = self._shard(key_violations=[{"observation": "품질관리 부서가 검토하지 않았다.",
                                             "original": "The QC unit did not review the batch record."}])
        self.assertEqual(precheck.deep_term_lines(shard), [])

    def test_cli_deep_exits_1_on_terms(self):
        # 심층 근거 게이트(1120d)는 DeepGate1120dTest 가 본다 — 원문 없는 최소 픽스처라 여기서는 통과로 고정.
        import verify_deep_analysis as vda
        patcher = mock.patch.object(vda, "run_deep_analysis_gate",
                                    return_value=vda.GateResult(ok=True, findings=[], report=""))
        patcher.start()
        self.addCleanup(patcher.stop)
        with tempfile.TemporaryDirectory() as tmp:
            ho, sh = Path(tmp, "h.json"), Path(tmp, "deep.A.json")
            ho.write_text(json.dumps({"rows": []}), encoding="utf-8")
            sh.write_text(json.dumps(self._shard(fda_evaluation="QA 승인 없이 출하했다."),
                                     ensure_ascii=False), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = precheck.main(["deep", str(ho), str(sh)])
            self.assertEqual(code, 1, out.getvalue())
            self.assertIn("심층 용어 fda483-1.fda_evaluation", out.getvalue())
            sh.write_text(json.dumps(self._shard(fda_evaluation="품질보증 승인 없이 출하했다."),
                                     ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["deep", str(ho), str(sh)]), 0)

    def test_reporter_instructions_require_deep_precheck_zero(self):
        text = (ROOT / "company" / "editorial" / "next" / "reporter.md").read_text(encoding="utf-8")
        self.assertIn("precheck.py deep", text)
        self.assertIn("심층 용어", text)


class Rehearsal1120cTest(unittest.TestCase):
    """리허설 1120c: 코드 검사 통과·심층 용어 0 — 교열 반려 13장은 부재 단정 10·hook 금칙어 2·관찰 번호 1."""

    def _admin(self, **da):
        return {"items": {"admin-1": {"deep_analysis": da}}}

    def test_source_doc_absence_blocks(self):
        for bad in ("처분 근거는 약사법 제38조다. 처분문에 가중·감경 사유는 없다.",
                    "처분문에 가중처분이나 형사고발에 관한 서술은 없다.",
                    "처분문에 차수와 가중·감경 사유는 적혀 있지 않다."):
            lines = precheck.deep_absence_lines(self._admin(administrative_risks=bad))
            self.assertTrue(lines and lines[0].startswith("심층 부재 admin-1.administrative_risks"), (bad, lines))

    def test_inspector_findings_are_not_absence(self):
        # 483 관찰의 "SOP 에 규정이 없다"·"정보가 없다"는 실사관이 찾은 사실이다 — 원문 부재가 아니다.
        for ok in ("양손 장갑을 한쪽 면만 모니터링했고 SOP에 양면 모니터링 규정이 없다(관찰 1).",
                   "USP <467> 시험법과 동등한지 판단할 정보가 없다(관찰 5).",
                   "안정성 기준일탈 조사를 정당한 사유 없이 제때 끝내지 않았다(관찰 1).",
                   "근거법령은 위반 시 업무정지 3개월을 정한다."):
            shard = {"items": {"fda483-1": {"deep_analysis": {"inspectional_significance": "",
                                                               "key_violations": [{"observation": ok}]}}}}
            self.assertEqual(precheck.deep_absence_lines(shard), [], ok)

    def test_same_function_as_publish_gate(self):
        import assemble_publish_brief as apb
        text = "처분문에 가중·감경 사유는 없다."
        card = {"id": "admin-1", "summary": text, "deep_analysis": {"x": 1}}
        self.assertTrue(apb.lint_false_absence_claims([card]))
        self.assertTrue(apb.false_absence_match(text))

    def test_483_basis_number_at_end(self):
        def shard(obs):
            return {"items": {"fda483-1": {"deep_analysis": {"inspectional_significance": "",
                                                              "key_violations": [{"observation": obs}]}}}}
        self.assertEqual(precheck.deep_basis_lines(shard("시험하지 않았다(관찰 2).")), [])
        self.assertEqual(precheck.deep_basis_lines(shard("시험하지 않았다(관찰 1 A)")), [])
        self.assertTrue(precheck.deep_basis_lines(shard("완제 API를 안정성시험에 넣는 단계를 포함하지 않았다.")))
        admin = {"items": {"admin-1": {"deep_analysis": {"key_violations": [{"observation": "번호 없음."}]}}}}
        self.assertEqual(precheck.deep_basis_lines(admin), [])        # 483 만 본다

    def test_hook_banned_uses_render_function(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "web"))
        import render
        self.assertEqual(render.hook_line_banned("이물 혼입 우려로 영업자 회수", "ko"), ["우려"])
        delta = {"cards": {"recall-1": {"hook_line": "파마레바미피드정, 이물 혼입 우려로 영업자 회수",
                                        "en": {"hook_line": "Recalled over concern of foreign matter"}},
                           "recall-2": {"hook_line": "이물 혼입으로 영업자 회수"}}}
        block, _warn = precheck.hook_lines(delta)
        self.assertEqual(len(block), 2, block)
        self.assertTrue(all(x.startswith("hook_line 금칙어 recall-1.") for x in block))
        # 화면도 같은 함수로 숨긴다.
        self.assertEqual(render._valid_hook_line({"hook_line": "이물 혼입 우려로 회수"}, "ko"), "")


class DeepGate1120dTest(unittest.TestCase):
    """리허설 1120d: 심층 근거 게이트 FAIL(조항 인용이 원문 글자와 다름) — 조립은 그 카드 심층을 빼고 교열은 반려했다."""

    def test_uses_bridge_gate_with_handoff_body(self):
        import verify_deep_analysis as vda
        seen = {}

        def fake(da, source, card_type=None):
            seen["source"] = source
            return vda.GateResult(ok=False, findings=[vda.Finding(vda.SEV_FAIL, "D2-CITATION-UNGROUNDED", "조항 없음"),
                                                      vda.Finding(vda.SEV_WARN, "D3-X", "경고")], report="")
        shard = {"items": {"admin-1": {"deep_analysis": {"key_violations": []}}, "admin-2": {"observations_ko": []}}}
        handoff = {"rows": [{"web_card_id": "admin-1", "deep_analysis_input": {"body_full": "처분문 원문"}}]}
        with mock.patch.object(vda, "run_deep_analysis_gate", side_effect=fake):
            lines = precheck.deep_gate_lines(shard, handoff)
        self.assertEqual(lines, ["심층 게이트 admin-1: [FAIL] D2-CITATION-UNGROUNDED — 조항 없음"])   # WARN 은 막지 않는다
        self.assertEqual(seen["source"], precheck.handoff_bodies(handoff).get("admin-1", ""))

    def test_pass_gives_no_lines(self):
        import verify_deep_analysis as vda
        with mock.patch.object(vda, "run_deep_analysis_gate",
                               return_value=vda.GateResult(ok=True, findings=[], report="")):
            self.assertEqual(precheck.deep_gate_lines({"items": {"a": {"deep_analysis": {}}}}, {"rows": []}), [])

    def test_bridge_calls_the_same_gate(self):
        import inspect
        self.assertIn("_vda.run_deep_analysis_gate(", inspect.getsource(delta_bridge._gate_deep_analysis))
        self.assertIn("deep_gate_lines(shard, handoff)", inspect.getsource(precheck.main))


class ReworkDeep1120dTest(unittest.TestCase):
    """리허설 1120d: 반려 1장이 심층 칸 — 재작업이 심층도 고치고, 대조는 델타와 같은 함수로 심층을 본다."""

    OLD = {"admin-1": {"deep_analysis": {"key_violations": [{"citation": "제30조제1항제9호"}]}},
           "admin-2": {"deep_analysis": {"key_violations": [{"citation": "제5조"}]}}}

    def _new(self, **change):
        new = json.loads(json.dumps(self.OLD))
        for cid, cit in change.items():
            new[cid.replace("_", "-")]["deep_analysis"]["key_violations"][0]["citation"] = cit
        return new

    def test_rejected_deep_card_may_change(self):
        problems, notes = precheck.rework_diff_deep(self.OLD, self._new(admin_1="제24조제5항제1호 및 제2호, 제30조제1항제9호"), {"admin-1"})
        self.assertEqual(problems, [])
        self.assertEqual(notes, ["심층: 고침 admin-1: deep_analysis"])

    def test_other_deep_card_must_not_change(self):
        problems, _ = precheck.rework_diff_deep(self.OLD, self._new(admin_2="제6조"), {"admin-1"})
        self.assertEqual(problems, ["심층: 반려되지 않은 카드가 바뀌었다: admin-2 (deep_analysis)"])

    def test_delta_only_rejection_is_fine(self):
        # 반려 카드가 심층에 없어도(델타 칸 반려) 심층 대조는 문제 삼지 않는다.
        self.assertEqual(precheck.rework_diff_deep(self.OLD, self._new(), {"fda483-9"}), ([], []))

    def test_cli_with_deep(self):
        with tempfile.TemporaryDirectory() as tmp:
            def w(name, obj):
                p = Path(tmp, name); p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8"); return str(p)
            delta = {"cards": {"admin-1": _card(), "admin-2": _card()}}
            ce = {"rework": {"cards": ["admin-1"]}, "cards": [{"web_card_id": "admin-1", "verdict": "reject"}]}
            args = ["rework-diff", w("d1.json", delta), w("d2.json", delta), w("ce.json", ce),
                    "--deep-old", w("x1.json", self.OLD)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(args + ["--deep-new", w("x2.json", self._new(admin_1="새 인용"))]), 0)
                self.assertEqual(precheck.main(args + ["--deep-new", w("x3.json", self._new(admin_2="바뀜"))]), 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(precheck.main(args), 2)       # 짝 없이 하나만 주면 못 한다

    def test_instructions_cover_deep_rework(self):
        rep = (ROOT / "company" / "editorial" / "next" / "reporter.md").read_text(encoding="utf-8")
        ce = (ROOT / "company" / "editorial" / "next" / "copyedit.md").read_text(encoding="utf-8")
        self.assertIn("`slot` 이 `deep.` 으로 시작하면", rep)
        self.assertIn("--deep-old _scaffold/deep_delta.r1.json", ce)


class Round2CloseTest(unittest.TestCase):
    """리허설 1120d: 2차 판정이 reject 0 인데 마감 1번(scaffold.json 반납)을 하지 않았다."""

    def test_round2_pass_goes_to_close(self):
        text = (ROOT / "company" / "editorial" / "next" / "copyedit.md").read_text(encoding="utf-8")
        i = text.index("3-2.")
        j = text.index("4. **마감")
        self.assertIn("곧바로 아래 4번 마감을 1번(`scaffold.json` 복사", text[i:j])


class WlViolationKo1120hTest(unittest.TestCase):
    """리허설 1120h: 경고서한 카드가 `violations_ko` 없이 `precheck deep` 0건 통과 → 교열 조립 exit 2
    ("위반 #1~#3 statement_ko 없음"). 심층 사전 점검이 조립 게이트 6(과 4·7)을 **같은 함수**로 돌린다."""

    VIOL = [{"number": str(n), "statement": f"Failure {n} [21 CFR 211.{n}0].", "citation": f"21 CFR 211.{n}0"}
            for n in (1, 2, 3)]
    KO = [{"number": str(n), "statement_ko": f"위반 {n} 국문"} for n in (1, 2, 3)]

    def _scaffold(self):
        return {"brief": {"run_date_kst": "2026-11-16"},
                "cards": [{"id": "wl-1", "card_type": "Warning Letter",
                           "deterministic_detail": {"type": "wl_violations", "count": 3,
                                                    "violations": json.loads(json.dumps(self.VIOL))}},
                          {"id": "other", "card_type": "Warning Letter",
                           "deterministic_detail": {"type": "wl_violations", "count": 1,
                                                    "violations": json.loads(json.dumps(self.VIOL[:1]))}}]}

    def _shard(self, **entry):
        return {"items": {"wl-1": dict({"deep_analysis": {"key_violations": "x"}}, **entry)}}

    def test_missing_violations_ko_blocks_like_assembly(self):
        import assemble_publish_brief as apb
        lines = precheck.deep_assembly_lines(self._shard(), {"rows": []}, self._scaffold())
        expected = apb._lint_wl_violation_ko([self._scaffold()["cards"][0]])   # 조립 게이트 6 그 함수
        self.assertEqual(len(expected), 3)
        self.assertEqual(lines, [f"심층 조립 검사 {e}" for e in expected])
        # 샤드 밖 카드(other)는 이 샤드 몫이 아니다 — 검사하지 않는다.
        self.assertFalse([x for x in lines if "'other'" in x])

    def test_filled_violations_ko_passes_and_no_scaffold_skips(self):
        self.assertEqual(precheck.deep_assembly_lines(self._shard(violations_ko=self.KO), {"rows": []},
                                                      self._scaffold()), [])
        self.assertEqual(precheck.deep_assembly_lines(self._shard(), {"rows": []}, None), [])

    def test_cli_deep_blocks_then_passes(self):
        import verify_deep_analysis as vda
        patcher = mock.patch.object(vda, "run_deep_analysis_gate",
                                    return_value=vda.GateResult(ok=True, findings=[], report=""))
        patcher.start()
        self.addCleanup(patcher.stop)
        with tempfile.TemporaryDirectory() as tmp:
            ho, sh, sc = Path(tmp, "h.json"), Path(tmp, "deep.F.json"), Path(tmp, "sc.json")
            ho.write_text(json.dumps({"rows": []}), encoding="utf-8")
            sc.write_text(json.dumps(self._scaffold(), ensure_ascii=False), encoding="utf-8")
            sh.write_text(json.dumps(self._shard(), ensure_ascii=False), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = precheck.main(["deep", str(ho), str(sh), "--scaffold", str(sc)])
            self.assertEqual(code, 1, out.getvalue())
            self.assertIn("심층 조립 검사 카드 'wl-1' 위반 #1: statement_ko 없음", out.getvalue())
            sh.write_text(json.dumps(self._shard(violations_ko=self.KO), ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(precheck.main(["deep", str(ho), str(sh), "--scaffold", str(sc)]), 0)


class AssembledEnGate1120jTest(unittest.TestCase):
    """리허설 1120j: 샤드 카드는 시사점·점검을 한·영 모두 비워 짝이 맞았는데, 조립(483 디제스트 대표)이 한국어만
    갈아 써서 교열 영문 게이트(`check_en_slots`)가 exit 1. 사전 점검은 조립 **뒤** 카드에 그 판정을 그대로 돌린다."""

    @staticmethod
    def _scaffold(*ids):
        return {"brief": {"run_date_kst": "2026-11-20"},
                "cards": [{"id": cid, "card_type": "FDA 483 실사 관찰", "type_tag": "483",
                           "facts": [{"label": "제조소/업체", "value": f"Firm {cid}"},
                                     {"label": "실사일", "value": "03/20/2026"}]} for cid in ids]}

    def test_lines_use_check_en_slots_verdict_on_assembled_cards(self):
        import check_en_slots
        cards = {"fda483-1": _card()}
        out = {"cards": [dict(_card(implication="조립이 채운 시사점", checks=["점검"]), id="fda483-1",
                              card_type="FDA 483 실사 관찰")]}
        prob = check_en_slots.card_problem(out["cards"][0])
        self.assertEqual(prob, ("partial", "fda483-1(빠짐: implication,checks)"))
        self.assertEqual(precheck.assembled_en_lines(cards, out),
                         [f"조립 검사 영문 게이트 {prob[1]} — 조립 뒤 영문 미완결(조립 전엔 완결)"])
        # 조립 전부터 빠진 카드는 `영문 슬롯 미완결` 줄로 이미 나간다 — 두 번 내지 않는다.
        before_bad = {"fda483-1": _card(implication="한국어만 있음")}
        self.assertEqual(precheck.assembled_en_lines(before_bad, out), [])
        # 이 샤드 카드가 아니면 보지 않는다.
        self.assertEqual(precheck.assembled_en_lines({}, out), [])

    def test_digest_representative_passes_after_assembly(self):
        # 관찰 원문 없는 483 두 장이 한 샤드에 오면 조립이 디제스트로 접는다 — 대표의 영문도 디제스트로 써야 통과.
        delta = {"cards": {"fda483-1": _card(), "fda483-2": _card()}}
        lines = precheck.assembly_lines(delta, self._scaffold("fda483-1", "fda483-2"))
        self.assertFalse([x for x in lines if "영문 게이트" in x], lines)

    def test_digest_without_english_rewrite_is_caught(self):
        # 수리 전 조립(대표가 기자의 낱장 en 을 그대로 듦)을 재현하면 사전 점검이 잡는다 — 이 검사가 살아 있다는 증거.
        import assemble_publish_brief as apb
        real = apb.merge_fda483_disclosures

        def stale(cards):
            before = {c.get("id"): c.get("en") for c in cards}
            return [dict(c, en=before[c["id"]]) if (c.get("merged_count") or 1) > 1 else c for c in real(cards)]

        delta = {"cards": {"fda483-1": _card(), "fda483-2": _card()}}
        with mock.patch.object(apb, "merge_fda483_disclosures", stale):
            lines = precheck.assembly_lines(delta, self._scaffold("fda483-1", "fda483-2"))
        self.assertIn("조립 검사 영문 게이트 fda483-1(빠짐: implication,checks) — 조립 뒤 영문 미완결(조립 전엔 완결)",
                      lines)


class CopyeditRules1121aTest(unittest.TestCase):
    """리허설 1121a: 교열 1차 59장 반려 중 기계로 셀 수 있는 규칙(용어표·군말·hook 약어)을 사전 점검이 놓쳤다.
    규칙은 v16 [용어·표기] 절을 문서에서 읽는다(`inject_slots.v16_terms`) — 표에 줄을 더하면 사전 점검도 는다."""

    def _terms(self, **kw):
        return [x for x in precheck.term_lines({"cards": {"a": _card(**kw)}}) if x.startswith("용어 표기")]

    def test_rules_are_read_from_v16_document(self):
        rules = inject_slots.v16_terms()
        self.assertTrue(rules["table"] and rules["filler"])
        self.assertTrue({"OOS", "CAPA", "API", "HVAC"} <= rules["listed"])
        self.assertTrue({"OOT", "CMO"} <= rules["off_list"])
        self.assertIn("FDA", rules["institutions"])
        self.assertIn("CFU", rules["units"])
        # 문서가 없으면 빈 규칙(손 베낌 검사는 그대로)
        self.assertEqual(inject_slots.v16_terms(str(ROOT / "없는_문서.md"))["table"], [])

    def test_table_terms_and_spacing_variant_block(self):
        self.assertTrue(any("외관 검사" in x for x in self._terms(key_facts=["관찰 2: 최종 제품 외관 검사 절차가 없음"])))
        self.assertTrue(any("경고 기준" in x for x in self._terms(implication="경고 기준 미만이라 일탈을 열지 않았다.")))
        deep = {"items": {"d": {"deep_analysis": {"key_violations": [{"observation": "시각검사 중 불합격 한도를 넘었다(관찰 5)."}]}}}}
        self.assertTrue(any("시각검사" in x for x in precheck.deep_term_lines(deep)))

    def test_table_terms_do_not_hit_right_hand_or_compound_words(self):
        self.assertEqual(self._terms(summary="육안검사와 시험성적서(COA)를 확인하지 않았다."), [])
        self.assertEqual(self._terms(summary="의약품공급자는 완제의약품을 공급하는 경우 보고한다."), [])
        self.assertEqual(self._terms(summary="환경 모니터링 결과를 검토하지 않았다."), [])

    def test_filler_words_block_with_word_boundary(self):
        self.assertTrue(any("다수" in x for x in self._terms(hook_line="결함 바이알 다수 발견")))
        self.assertTrue(any("장기간" in x for x in self._terms(implication="최대 보관 기한 없이 장기간 보관됐다.")))
        self.assertEqual(self._terms(summary="대다수 로트를 다수결로 정했다."), [])

    def test_hook_abbreviation_ko_blocks_en_warns(self):
        block, warn = precheck.hook_lines({"cards": {"a": _card(hook_line="OOS 결과 14건 반복")}})
        self.assertTrue(any("hook_line 약어" in x and "OOS" in x for x in block))
        en = dict(_card()["en"], hook_line="Fourteen recurring endotoxin OOS and OOT results")
        block, warn = precheck.hook_lines({"cards": {"a": _card(en=en)}})
        self.assertEqual(block, [])
        self.assertTrue(any("OOS, OOT" in x for x in warn))
        # 기관·단위·로마 숫자·숫자 든 등급은 약어가 아니다
        en = dict(_card()["en"], hook_line="FDA found 12 CFU in Class II KF94 masks under CGMP")
        self.assertEqual(precheck.hook_lines({"cards": {"a": _card(en=en, hook_line="CGMP 위반 KF94 마스크 Class II 회수")}}), ([], []))

    def test_hook_statement_number_warns(self):
        sc = {"cards": [{"id": "a", "deterministic_detail": {"observations": [{"number": "8", "deficiency": "No procedure.",
              "detail": "Liquid was observed in April 2026. According to the Associate VP, the firm has not cleaned it since March 2011."}]}}]}
        _, warn = precheck.hook_lines({"cards": {"a": _card(hook_line="2011년 설치 이후 세척한 적 없는 설비")}}, sc)
        self.assertTrue(any("진술 표기" in x and "2011" in x for x in warn))
        _, warn = precheck.hook_lines({"cards": {"a": _card(hook_line="2011년 설치 이후 세척하지 않았다고 진술")}}, sc)
        self.assertFalse(any("진술 표기" in x for x in warn))

    def test_accuracy_warnings(self):
        sc = {"cards": [
            {"id": "r", "facts": [{"label": "제품", "value": "허브팜절패모"}], "quotes": [{"original": "중금속(카드뮴)"}],
             "deterministic_detail": {"type": "mfds_recall_detail", "enforcement": "회수명령 (강제)"}},
            {"id": "d", "facts": [], "quotes": [{"original": "complaints related to potential leaks from small perforations"}],
             "deterministic_detail": {"type": "openfda_recall_detail"}},
            {"id": "o", "facts": [{"label": "관찰사항", "value": "5"}], "deterministic_detail": {"type": "fda483", "count": 5, "observations": [
                {"number": "4", "deficiency": "x", "detail": "The system operated with an expired [redacted] for 49 days. Three batches failed."}]}}]}
        delta = {"cards": {
            "r": _card(title_issue="카드뮴 부적합 회수", hook_line="한약재 절패모, 중금속(카드뮴) 부적합으로 회수명령(강제)"),
            "d": _card(title_issue="백 인쇄부 작은 구멍 누출 회수", hook_line="복막투석액 백 누출 의심 불만 접수"),
            "o": _card(summary="FDA가 관찰사항 5건을 적었다.", hook_line="유효기간 지난 부품으로 49일 운전",
                       implication="3배치가 불합격이었다. 8건을 조사하지 않았다.")}}
        lines = precheck.accuracy_warn_lines(delta, sc)
        text = "\n".join(lines)
        self.assertIn("회수 사유 덧붙임 r.title_issue: 부적합", text)
        self.assertIn("한약재", text)
        self.assertIn("가능성 표현 누락 d.title_issue", text)
        self.assertNotIn("가능성 표현 누락 d.hook_line", text)          # '의심' 이 있다
        self.assertIn("가림 값 채움 의심 o.hook_line: 숫자 49", text)
        self.assertIn("원문에 없는 수 o.implication: '8건'", text)
        self.assertNotIn("'5건'", text)                                 # 관찰사항 수는 코드 facts 에 있다
        self.assertNotIn("'3배치'", text)                               # 원문 'Three'

    def test_cli_prints_accuracy_as_warning_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "delta.A.json")
            sc = os.path.join(tmp, "sc.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cards": {"d": _card()}}, f, ensure_ascii=False)
            with open(sc, "w", encoding="utf-8") as f:
                json.dump({"cards": [{"id": "d", "quotes": [{"original": "potential glass particles"}],
                                      "deterministic_detail": {"type": "openfda_recall_detail"}}]}, f)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch.object(precheck, "assembly_lines", return_value=[]), \
                    mock.patch.object(precheck, "gate_lines", return_value=[]):
                code = precheck.main(["terms", path, "--scaffold", sc])
            self.assertIn("경고: 가능성 표현 누락 d.title_issue", out.getvalue())
            self.assertEqual(code, 0)

    def test_assembly_style_lint_keeps_old_rules(self):
        # 백업 Routine 은 옛 v16 으로 쓴다 — 발행 조립의 표현 검사(기본값)는 새 규칙을 세지 않는다(2026-11-21 대표 결정).
        delta = {"cards": {"a": _card(hook_line="결함 바이알 다수 발견", key_facts=["관찰 2: 외관 검사 절차 없음"])}}
        self.assertEqual(inject_slots.lint_prose_style(delta).terms, {})
        self.assertTrue(inject_slots.lint_prose_style(delta, v16_rules=True).terms)
        deep = {"d": {"deep_analysis": {"key_violations": [{"observation": "시각검사 중 불합격(관찰 5)."}]}}}
        self.assertEqual(inject_slots.deep_term_hits(deep), [])
        self.assertEqual(inject_slots.lint_deep_prose_style(deep).terms, {})

    def test_reporter_instructions_name_new_lines(self):
        for rel in ("reporter.md", "next/reporter.md"):
            rep = (ROOT / "company" / "editorial" / rel).read_text(encoding="utf-8")
            self.assertIn("v16_terms", rep)


if __name__ == "__main__":
    unittest.main()
