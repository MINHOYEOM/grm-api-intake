# -*- coding: utf-8 -*-
"""교열 판정이 회차마다 갈린 4가지를 코드로 고정한다(2026-11-23) — 개인 실명·가림 값 추측·직원 진술 단정·날짜 불일치.

재료는 실제 산출물에서 그대로 뗐다:
  - 리허설 1122b 2차 반려(dfd5fdee): fda483-196329 'Kenneth F Tesch'·196343 'Matthew A. Leo'(개인 실명),
    196444 'filling [redacted]' → 'filling parts/equipment'(가림 값 추측), 196410 'acknowledged … backdated' → 'records backdated'(진술 단정).
  - 리허설 1121d~1122b WHO 서류 평가 카드 who-whopir-98cfb8b39789: '실사 기간: … 미국 FDA(2026-03-02~07)' vs 코드 실사일 2026-09-22.
  - 11-23 실발행: 교열이 196329·196343 을 '개인 사업체 업체명' 이라며 통과 → 발행 PR 에서 손으로 '개인 사업자'.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import card_scaffold as cs  # noqa: E402
import grm_findings  # noqa: E402


def _load_module(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load_module("company_guess_check_src", "company/editorial/guess_check.py")
precheck = _load_module("company_precheck_src", "company/editorial/precheck.py")

# ── 실제 산출물 조각 ─────────────────────────────────────────────────────────
TESCH_BODY = ("Specifically, you offered an animal for sale on [redacted] and was found to have an antibiotic residue of "
              "Ampicillin in the kidney at 0.0799 ppm with a tolerance of 0.01 ppm. This cow was treated with ampicillin "
              "on you farm on 8/16/25 and 8/19/25.")
GENZYME_BODY = ("Specifically, A) On January 12, 2026, filling (b) (4) used for (b) (4) contained unknown brownish white residue "
                "around the (b) (4) Internal inspection revealed brownish red discoloration consistent with rust. "
                "Filling Line accounts for approximately 34% of these work orders. ordering replacement parts in October 2025.")
ILC_BODY = ("Specifically, We observed discrepancies on the OOS investigation logs and corresponding investigation reports. "
            "The microbiology manager acknowledged that they fabricated these documents with falsified information and "
            "backdated signatures. On further enquiry, the microbiology manager confirmed that they were created only on "
            "12SEP2024, four days before the start of the inspection. The firm did not provide release test data.")
WHO_BODY = ('{"reliance": [{"authority": "USA FDA", "dates": "2nd to 7th March 2026"}, '
            '{"authority": "TGA Australia", "dates": "03rd & 04th October 2024"}, '
            '{"authority": "USA FDA", "dates": "16 to 19 August 2022"}]}')


def _row(cid, firm, body, kind="fda-483"):
    return {"web_card_id": cid, "kind": kind, "firm": firm, "body": body,
            "deep_analysis_input": {"body_full": body}}


def _sc(cid, firm, est="Grower · 483", date="12/22/2025"):
    return {"id": cid, "headline_target": firm, "quotes": [],
            "facts": [{"label": "발행일", "value": "2026-11-17"}, {"label": "문서번호", "value": cid},
                      {"label": "제조소/업체", "value": f"{firm} · FEI 3041017158"},
                      {"label": "시설 · 유형", "value": est}, {"label": "실사일", "value": date}]}


def _card(**kw):
    base = {"title_issue": "제목", "summary": "요약.", "key_facts": ["사실"],
            "en": {"title_issue": "Title", "summary": "Summary.", "key_facts": ["Fact"]}}
    for k, v in kw.items():
        if k.startswith("en_"):
            base["en"][k[3:]] = v
        else:
            base[k] = v
    return base


def _run(cid, card, row, sc):
    return gc.delta_source_lines({"cards": {cid: card}}, {cid: row} if row else {}, {cid: sc} if sc else {})


# ── ① 개인 실명 ──────────────────────────────────────────────────────────────
class PersonFirmTest(unittest.TestCase):
    def test_person_name_forms(self):
        for name in ("Kenneth F Tesch", "Matthew A. Leo", "Frederick J. Raal", "Mary Ann K. Smith", "John B. Doe Jr."):
            self.assertTrue(grm_findings.person_firm_name(name), name)
        # 브리프 483 업체 449곳 실측: 두 낱말·법인 낱말은 사람으로 보지 않는다
        for name in ("Novo Nordisk", "Sanofi Pasteur", "Excel Vision", "Genzyme Ireland Limited", "Kenneth F Tesch Farms",
                     "John A. Smith Dairy", "B. Braun Medical Inc", "International Laboratories Corp", ""):
            self.assertFalse(grm_findings.person_firm_name(name), name)

    def test_suspect_only_with_individual_establishment(self):
        self.assertTrue(grm_findings.person_firm_name_suspect("John Smith", "Beef Ranch · 483"))
        self.assertFalse(grm_findings.person_firm_name_suspect("Novo Nordisk", "Drug Manufacturer · 483"))

    def test_redaction_word_is_shared_with_render(self):
        """표기는 한 곳 — web/render.py 가 같은 표를 쓴다(#1876)."""
        src = (ROOT / "web" / "render.py").read_text(encoding="utf-8")
        self.assertIn("REDACTED_PERSON_BY_LANG = _GRM_REDACTED_PERSON_BY_LANG", src)
        self.assertNotIn('REDACTED_PERSON_BY_LANG = {"ko"', src)
        self.assertEqual(grm_findings.redact_person_firm("Kenneth F Tesch"), "개인 사업자")
        self.assertEqual(grm_findings.redact_person_firm("Kenneth F Tesch", "en"), "Individual business")
        self.assertEqual(grm_findings.redact_person_firm("Genzyme Ireland Limited"), "Genzyme Ireland Limited")

    def test_1122b_and_1123_prose_blocks(self):
        """1122b 2차 반려 = 11-23 교열 통과분. 같은 재료면 매번 막힌다(국·영 둘 다)."""
        for cid, firm, ko, en in (
                ("fda483-196329", "Kenneth F Tesch",
                 "FDA가 미국의 사육 농가 Kenneth F Tesch(업체)에 Form 483(관찰사항 1건)을 발행했다.",
                 "FDA issued a Form 483 with 1 observation to Kenneth F Tesch, a grower in the United States."),
                ("fda483-196343", "Matthew A. Leo",
                 "FDA가 미국의 육우 목장 Matthew A. Leo(업체)에 Form 483(관찰사항 2건)을 발행했다.",
                 "FDA issued a Form 483 with 2 observations to Matthew A. Leo, a beef ranch in the United States.")):
            b, _ = _run(cid, _card(summary=ko, en_summary=en), _row(cid, firm, TESCH_BODY), _sc(cid, firm))
            self.assertEqual([x.split(":")[0] for x in b], [f"개인 실명 {cid}.summary", f"개인 실명 {cid}.en.summary"])
            self.assertIn("개인 사업자", b[0])

    def test_fixed_prose_passes_and_surname_alone_blocks(self):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        ok = _card(summary="FDA가 축산 농가(개인 사업자)에 Form 483(관찰사항 1건)을 발행했다.",
                   en_summary="FDA issued a Form 483 with 1 observation to a grower (an individual business).")
        b, w = _run(cid, ok, _row(cid, firm, TESCH_BODY), _sc(cid, firm))
        self.assertEqual([x for x in b if x.startswith("개인 실명")], [])
        b, _ = _run(cid, _card(summary="Tesch 농장에 Form 483 을 발행했다."), _row(cid, firm, TESCH_BODY), _sc(cid, firm))
        self.assertTrue(any("'Tesch'" in x for x in b))

    def test_scaffold_code_slots_redacted(self):
        """코드 칸(headline_target·업체 사실값·제목)은 스캐폴드가 같은 함수로 가린다 — 11-23 발행 PR 의 손 수정이 다음부터 자동."""
        row = {"source": "FDA_483", "kind": "fda-483", "firm": "Kenneth F Tesch", "headline": "Kenneth F Tesch"}
        self.assertEqual(cs._headline_target(row), "개인 사업자")
        rows = cs._w2_extra_fda_483(row, {"firm": "Kenneth F Tesch", "fei_number": "3041017158", "record_date": "12/22/2025"})
        self.assertEqual(rows[0], ("제조소/업체", "개인 사업자 · FEI 3041017158"))
        row2 = {"source": "FDA_483", "firm": "Genzyme Ireland Limited", "headline": "x"}
        self.assertEqual(cs._headline_target(row2), "Genzyme Ireland Limited")
        wl = cs._w2_extra_wl({"firm": "Matthew A. Leo"}, {})
        self.assertEqual(wl[0], ("업체/제조소", "개인 사업자"))

    def test_deep_names_block(self):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        deep = {"items": {cid: {"observations_ko": [{"number": "1", "deficiency_ko": "Kenneth F Tesch 가 판매했다"}]}}}
        b, _ = gc.deep_source_lines(deep, {cid: _row(cid, firm, TESCH_BODY)}, {cid: _sc(cid, firm)})
        self.assertTrue(b and b[0].startswith(f"개인 실명 {cid}.deep.observations_ko[0]"))


# ── ② 가림 값 추측 ───────────────────────────────────────────────────────────
class RedactionFillTest(unittest.TestCase):
    CID = "fda483-196444"

    def _b(self, **kw):
        return _run(self.CID, _card(**kw), _row(self.CID, "Genzyme Ireland Limited", GENZYME_BODY),
                    _sc(self.CID, "Genzyme Ireland Limited", "Drug Product Manufacturer · 483", "01/20/2026"))

    def test_1122b_filling_parts_blocks(self):
        b, _ = self._b(title_issue="충전 부품 부식·누수 일탈 미처리", en_title_issue="Corroded filling parts, leaks not deviations",
                       en_key_facts=["Observation 4: on 2026-01-12, filling equipment used for U.S. products showed rust"])
        heads = [x.split(" — ")[0] for x in b if x.startswith("가림 값 추측")]
        self.assertEqual(heads, [f"가림 값 추측 {self.CID}.en.title_issue: 'filling parts'",
                                 f"가림 값 추측 {self.CID}.en.key_facts[0]: 'filling equipment'"])
        self.assertIn("국문 짝 title_issue", b[0])

    def test_source_phrase_and_non_filler_pass(self):
        b, w = self._b(en_title_issue="Filling line work orders not deviations",       # 원문에 'Filling Line' 이 있다
                       en_key_facts=["Observation 4: filling residue was not investigated"])  # 일반 채움 명사가 아니다
        self.assertEqual([x for x in b + w if x.startswith("가림 값 추측")], [])

    def test_advice_slot_warns(self):
        b, w = self._b(en_implication="Check filling equipment routinely.")
        self.assertEqual([x for x in b if x.startswith("가림")], [])
        self.assertTrue(any(x.startswith(f"가림 값 추측 의심 {self.CID}.en.implication") for x in w))


# ── ③ 직원 진술 단정 ─────────────────────────────────────────────────────────
class StatementTest(unittest.TestCase):
    CID = "fda483-196410"

    def _w(self, **kw):
        b, w = _run(self.CID, _card(**kw), _row(self.CID, "International Laboratories Corp", ILC_BODY),
                    _sc(self.CID, "International Laboratories Corp", "OTC Drug Manufacturer · 483", "09/20/2024"))
        return [x for x in w if x.startswith("진술 단정 의심")]

    def test_1122b_backdated_title_warns_with_source_sentence(self):
        w = self._w(title_issue="기준일탈 조사 문서 소급 작성", en_title_issue="OOS investigation records backdated")
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].startswith(f"진술 단정 의심 {self.CID}.en.title_issue"))
        self.assertIn("acknowledged", w[0])
        self.assertIn("국문 짝 title_issue 도 진술 표기 없음", w[0])

    def test_marked_statement_passes(self):
        self.assertEqual(self._w(hook_line="조사 보고서를 소급했다고 진술",
                                 en_hook_line="Staff stated investigation reports were backdated"), [])
        self.assertEqual(self._w(en_key_facts=["the manager acknowledged fabricating OOS documents"]), [])

    def test_non_statement_fact_passes(self):
        self.assertEqual(self._w(en_key_facts=["release test data could not be provided"]), [])


# ── ④ 날짜 ───────────────────────────────────────────────────────────────────
class DateTest(unittest.TestCase):
    def test_source_forms(self):
        src = "treated on 8/16/25 and created 12SEP2024; reliance 2nd to 7th March 2026; closed May !3, 2026; 20261117"
        norm = gc._ocr_dates(src)
        for y, m, d in ((2025, 8, 16), (2024, 9, 12), (2026, 3, 2), (2026, 11, 17)):
            self.assertTrue(gc.date_in_source(y, m, d, src), (y, m, d))
        self.assertTrue(gc.date_in_source(2026, 5, 13, norm))
        self.assertFalse(gc.date_in_source(2025, 1, 9, src))

    def test_unknown_date_blocks_front(self):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        b, _ = _run(cid, _card(key_facts=["관찰 1: 이 소는 2025-08-16과 2025-08-21에 투여받음"]),
                    _row(cid, firm, TESCH_BODY), _sc(cid, firm))
        self.assertEqual([x.split(" — ")[0] for x in b if x.startswith("원문에 없는 날짜")],
                         [f"원문에 없는 날짜 {cid}.key_facts[0]: '2025-08-21'"])

    def test_who_label_mismatch_blocks(self):
        """리허설 1121d~1122b — 다른 기관 실사 날짜를 이 보고서의 '실사 기간' 으로 적었는데 교열이 통과시켰다."""
        cid = "who-whopir-98cfb8b39789"
        sc = {"id": cid, "headline_target": "Lupin (Ankleshwar)", "quotes": [],
              "facts": [{"label": "실사일", "value": "2026-09-22"}, {"label": "주제", "value": "Lupin (Ankleshwar)"}]}
        row = {"web_card_id": cid, "kind": "whopir", "body": WHO_BODY}
        card = _card(key_facts=["결론: 수용 가능", "실사 기간: 현장 실사 대신 서류 평가, 미국 FDA(2026-03-02~07)·TGA(2024-10-03~04) 실사 결과 활용"],
                     en_key_facts=["Conclusion: acceptable", "Inspection period: desk assessment, relying on USA FDA (2026-03-02 to 07)"])
        b, _ = _run(cid, card, row, sc)
        heads = [x.split(":")[0] for x in b if x.startswith("날짜 불일치")]
        self.assertEqual(heads, [f"날짜 불일치 {cid}.key_facts[1]", f"날짜 불일치 {cid}.en.key_facts[1]"])
        ok = _card(key_facts=["결론: 수용 가능", "근거로 쓴 실사: 미국 FDA 2026-03-02~07, TGA 2024-10-03~04"])
        b, _ = _run(cid, ok, row, sc)
        self.assertEqual([x for x in b if "날짜" in x], [])

    def test_deep_dates_warn_only(self):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        deep = {cid: {"observations_ko": [{"number": "1", "detail_ko": "2025-08-21 에 투여했다"}]}}
        b, w = gc.deep_source_lines(deep, {cid: _row(cid, firm, TESCH_BODY)}, {cid: _sc(cid, firm)})
        self.assertEqual(b, [])
        self.assertTrue(any(x.startswith(f"원문에 없는 날짜 의심 {cid}.deep") for x in w))

    def test_no_handoff_row_downgrades_to_warning(self):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        b, w = _run(cid, _card(key_facts=["2025-08-21 투여"]), None, _sc(cid, firm))
        self.assertEqual([x for x in b if "날짜" in x], [])
        self.assertTrue(any(x.startswith("원문에 없는 날짜 의심") for x in w))


# ── 배선: 기자 사전 점검(terms·deep)과 교열 코드 검사(source)가 같은 함수 ─────────
class WiringTest(unittest.TestCase):
    def _folder(self, tmp):
        cid, firm = "fda483-196329", "Kenneth F Tesch"
        delta = {"cards": {cid: _card(summary="Kenneth F Tesch(업체)에 483을 발행했다.",
                                      en_summary="FDA issued a 483 to Kenneth F Tesch.")}}
        handoff = {"rows": [_row(cid, firm, TESCH_BODY)]}
        scaffold = {"cards": [_sc(cid, firm)]}
        paths = {}
        for name, obj in (("delta.json", delta), ("delta.A.json", delta), ("handoff.json", handoff), ("scaffold.json", scaffold)):
            paths[name] = os.path.join(tmp, name)
            with open(paths[name], "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
        return paths

    def _cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = precheck.main(argv)
        return code, out.getvalue()

    def test_terms_and_source_block_same_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._folder(tmp)
            code, out = self._cli(["terms", p["delta.A.json"], "--handoff", p["handoff.json"], "--scaffold", p["scaffold.json"]])
            self.assertEqual(code, 1)
            terms_lines = [x for x in out.splitlines() if x.startswith("개인 실명 ")]
            code, out = self._cli(["source", tmp, p["handoff.json"], "--scaffold", p["scaffold.json"]])
            self.assertEqual(code, 1)
            source_lines = [x for x in out.splitlines() if x.startswith("개인 실명 ")]
            self.assertEqual(len(terms_lines), 2)
            self.assertEqual(terms_lines, source_lines)

    def test_deep_cli_blocks_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._folder(tmp)
            shard = os.path.join(tmp, "deep.A.json")
            with open(shard, "w", encoding="utf-8") as f:
                json.dump({"items": {"fda483-196329": {"observations_ko": [
                    {"number": "1", "deficiency_ko": "Kenneth F Tesch 가 판매했다"}]}}}, f, ensure_ascii=False)
            code, out = self._cli(["deep", p["handoff.json"], shard, "--scaffold", p["scaffold.json"]])
            self.assertEqual(code, 1)
            self.assertIn("개인 실명 fda483-196329.deep.observations_ko[0]", out)


# ── 지시문이 코드와 같은 기준인지 ─────────────────────────────────────────────
class InstructionParityTest(unittest.TestCase):
    FILES = ("company/editorial/copyedit.md", "company/editorial/next/copyedit.md")

    def test_copyedit_runs_source_and_names_heads(self):
        for rel in self.FILES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("precheck.py source", text, rel)
            for head in ("개인 실명", "가림 값 추측", "원문에 없는 날짜", "날짜 불일치", "진술 단정 의심", "날짜 연도 확인"):
                self.assertIn(head, text, f"{rel}: {head}")

    def test_old_sole_proprietor_exception_removed(self):
        """'개인 사업체 업체명으로서의 표기는 제외' — 11-23 교열이 실명을 통과시킨 근거 문구."""
        for rel in self.FILES + ("company/editorial/reporter.md", "company/editorial/next/reporter.md"):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("개인 사업체 업체명으로서의 표기는 제외", text, rel)
            self.assertNotRegex(text, r"개인 사업체는 업체명.{0,20}싣는다", rel)


if __name__ == "__main__":
    unittest.main()
