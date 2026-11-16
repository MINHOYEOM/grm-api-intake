"""브리프 글 품질 개선 코드 — 편집 지시문 수정안(2026-11-15) 2절 ①~⑤.

① FDA 483 심층에서 `inspectional_significance`·`administrative_risks`·`required_remediation.items` 를
  비워도 게이트(D1)를 통과하고, 화면은 빈 칸을 숨기며 483 고정 안내를 한 줄 붙인다.
② 경고서한 심층 소제목이 한국어 페이지에서 한국어다.
③ 소식·지침·고시 카드는 `key_facts` 가 비어도 합치기·사전 점검·주입·조립·영문 판정을 통과한다
  (다른 유형은 종전처럼 막힌다 — 유형은 handoff 의 `card_scaffold` 첫 줄에서 읽는다).
④ 심층 칸 산문 표현 검사 — 경고만.
⑤ 행정처분 요약 "식약처가 제약사·업체·의료기관에" — 경고만.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "company" / "editorial"))
sys.path.insert(0, str(ROOT / "tests"))

import assemble_publish_brief as apb  # noqa: E402
import inject_slots as inj  # noqa: E402
import merge_parts as mp  # noqa: E402
import precheck  # noqa: E402
import render  # noqa: E402
import verify_deep_analysis as vda  # noqa: E402
from test_assemble_publish_brief import DELTA_PATH, TRUTH_PATH, _blank_scaffold_from  # noqa: E402

FIXED_DEADLINE = "회신은 의무가 아니다. 15영업일 안에 받은 회신은 FDA가 후속 조치를 정할 때 검토한다."

DA_483_EMPTY = {
    "key_violations": [{"observation": "품질보증 책임자는 원료를 입고 때 시험하지 않는다고 진술했다(관찰 1)."}],
    "inspectional_significance": "",
    "required_remediation": {"deadline": FIXED_DEADLINE, "items": []},
    "administrative_risks": "",
}


class Fda483OptionalSectionsGateTest(unittest.TestCase):
    """① D1 — 483 만 ②·④·items 를 비울 수 있다."""

    def test_483_empty_sections_pass(self):
        sections = vda.resolve_required_sections(DA_483_EMPTY)
        self.assertIs(sections, vda.REQUIRED_SECTIONS_FDA483)
        self.assertEqual(vda.check_structure(DA_483_EMPTY, sections), [])
        gate = vda.run_deep_analysis_gate(DA_483_EMPTY, "Observation 1 ...")
        self.assertTrue(gate.ok, gate.report)

    def test_483_short_nonempty_still_fails(self):
        da = dict(DA_483_EMPTY, inspectional_significance="짧다")
        self.assertTrue(vda.has_failures(vda.check_structure(da, vda.REQUIRED_SECTIONS_FDA483)))

    def test_483_key_violations_and_deadline_still_required(self):
        for da in (dict(DA_483_EMPTY, key_violations=[]),
                   dict(DA_483_EMPTY, required_remediation={"deadline": "", "items": []}),
                   dict(DA_483_EMPTY, required_remediation={"deadline": FIXED_DEADLINE})):
            self.assertTrue(vda.has_failures(vda.check_structure(da, vda.REQUIRED_SECTIONS_FDA483)), da)

    def test_wl_and_admin_unchanged(self):
        wl = {"key_violations": [{"description": "x" * 30}], "fda_evaluation": "",
              "required_remediation": {"deadline": "15영업일 이내 서면 회신", "items": []},
              "administrative_risks": ""}
        codes = vda.check_structure(wl, vda.REQUIRED_SECTIONS)
        self.assertEqual(len(codes), 3, codes)          # fda_evaluation · items · administrative_risks
        admin = dict(wl, disposition_basis="")
        del admin["fda_evaluation"]
        self.assertEqual(len(vda.check_structure(admin, vda.REQUIRED_SECTIONS_ADMIN)), 3)

    def test_card_type_hint_when_keys_missing(self):
        da = {"key_violations": DA_483_EMPTY["key_violations"],
              "required_remediation": {"deadline": FIXED_DEADLINE, "items": []}}
        self.assertFalse(vda.run_deep_analysis_gate(da, "x").ok)               # 키가 없으면 WL 로 판별
        hinted = vda.run_deep_analysis_gate(da, "x", card_type="fda-483")
        self.assertFalse(hinted.ok)                                            # 키는 빼면 안 된다("")
        self.assertTrue(any("inspectional_significance" in f.detail for f in hinted.findings))


class DeepRenderTest(unittest.TestCase):
    """①② 화면 — 빈 483 칸은 숨기고 안내 한 줄, 경고서한은 한국어 소제목."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_quality1116_"))
        data = cls._tmp / "data"
        data.mkdir(parents=True)
        base = json.loads((ROOT / "web" / "tests" / "fixtures" / "multi" / "brief_web_2026_06_08.json")
                          .read_text(encoding="utf-8"))
        tmpl = dict(base["cards"][0])
        common = {"group": "글로벌", "group_label": None, "agency": "FDA", "category": "Other",
                  "evidence_level": "B", "signal_tier": 3, "signal_label": "High",
                  "title_issue": "", "summary": "", "quotes": [], "key_facts": [],
                  "implication": "", "checks": [], "merged_count": 1, "merged_items": [],
                  "sources": {"info_url": "", "official_url": "https://www.fda.gov/media/1/download",
                              "official_is_pdf": True,
                              "link_check": {"info": "pending", "official": "pending"}}}
        c483 = dict(tmpl, **common, id="fda483-quality", render_order=998,
                    card_type="FDA 483 실사 관찰", type_tag="483", headline_target="Acme Labs",
                    facts=[{"label": "문서번호", "value": "fda483-quality"}],
                    deep_analysis=copy.deepcopy(DA_483_EMPTY))
        cwl = dict(tmpl, **common, id="wl-quality", render_order=999,
                   card_type="Warning Letter", type_tag="Warning Letter", headline_target="Beta Pharma",
                   facts=[{"label": "문서번호", "value": "wl-quality"}],
                   deep_analysis={
                       "key_violations": [{"citation": "21 CFR 211.192",
                                           "description": "회사는 기준일탈(OOS) 결과를 조사하지 않았다."}],
                       "fda_evaluation": "FDA는 회신이 근본원인을 다루지 않아 부적절하다고 적었다.",
                       "required_remediation": {"deadline": "15영업일 이내 서면 회신",
                                                "items": ["OOS 조사 절차의 재검토 결과"]},
                       "administrative_risks": "FDA는 승인 보류를 할 수 있다고 적었다."})
        base["cards"] = base["cards"] + [c483, cwl]
        (data / "brief_web_2026_06_08.json").write_text(json.dumps(base, ensure_ascii=False),
                                                        encoding="utf-8")
        render.render_site(data, cls._tmp / "out", render_doc_pages=False)
        cls.html = (cls._tmp / "out" / "briefs/2026-06-08/index.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _block(self, body_text):
        """본문 문구가 든 심층 블록(목차 등 다른 곳의 업체명에 끌려가지 않게 블록 안에서 찾는다)."""
        pos = 0
        while True:
            start = self.html.index('<details class="block deep">', pos)
            block = self.html[start:self.html.index("</details>", start)]
            if body_text in block:
                return block
            pos = start + 1

    def test_483_empty_sections_hidden_with_notice(self):
        b = self._block("품질보증 책임자는")
        self.assertIn("위반 항목 및 리스크", b)
        self.assertNotIn("실사 지적의 의미", b)     # 빈 ② 숨김
        self.assertNotIn("행정 리스크", b)          # 빈 ④ 숨김
        self.assertIn(FIXED_DEADLINE, b)
        self.assertNotIn('class="rem-list"', b)     # 빈 items 숨김
        self.assertIn("FDA의 최종 판단이 아니다", b)
        self.assertNotIn("Key Violations", b)       # 빈 483 이 WL 로 그려지지 않는다
        self.assertIn('<span class="deep-n">2</span><span class="deep-t">요구 시정 조치', b)

    def test_483_preview_hint_omits_empty_sections(self):
        hint = render._deep_preview(DA_483_EMPTY)
        self.assertEqual(hint, "위반 1건")

    def test_wl_korean_headings(self):
        b = self._block("회사는 기준일탈(OOS)")
        for h in ("위반 항목 및 리스크", "회사 회신과 FDA 판단", "요구 시정 조치", "행정 리스크"):
            self.assertIn(h, b)
        for h in ("Key Violations", "FDA's Evaluation of Response", "Required Remediation",
                  "Administrative Risks"):
            self.assertNotIn(h, b)
        self.assertNotIn("FDA의 최종 판단이 아니다", b)

    def test_en_catalog_has_new_keys(self):
        cat = json.loads((ROOT / "web" / "data" / "i18n" / "en.json").read_text(encoding="utf-8"))
        self.assertIn("회사 회신과 FDA 판단", cat)
        self.assertIn("483은 실사가 끝날 때 실사관이 남긴 관찰 목록이다. FDA의 최종 판단이 아니다.", cat)


EN_NEWS = {"title_issue": "EMA updates", "summary": "EMA published a notice.",
           "key_facts": [], "implication": "", "checks": []}


def _news_card(**over):
    c = {"title_issue": "EMA 공지", "summary": "EMA가 공지를 냈다.", "key_facts": [],
         "implication": "", "checks": [], "en": dict(EN_NEWS)}
    c.update(over)
    return c


class NewsKeyFactsOptionalTest(unittest.TestCase):
    """③ 소식·지침·고시만 빈 key_facts 허용 — 네 모듈 같은 판정."""

    def test_type_lists_match(self):
        self.assertEqual(tuple(mp.KEY_FACTS_OPTIONAL_CARD_TYPES), tuple(inj.KEY_FACTS_OPTIONAL_CARD_TYPES))
        self.assertEqual(tuple(mp.KEY_FACTS_OPTIONAL_CARD_TYPES), tuple(render.KEY_FACTS_OPTIONAL_CARD_TYPES))

    def test_type_names_exist_in_scaffold_registry(self):
        import card_scaffold as cs
        labels = {spec.label for spec in cs._REGISTRY.values()}
        for t in mp.KEY_FACTS_OPTIONAL_CARD_TYPES:
            self.assertIn(t, labels)

    def test_en_gaps_by_type(self):
        card = _news_card()
        for t in mp.KEY_FACTS_OPTIONAL_CARD_TYPES:
            self.assertEqual(mp._en_gaps(card, t), [])
            self.assertEqual(render.card_english_gaps(dict(card, card_type=t)), [])
        self.assertEqual(mp._en_gaps(card), ["key_facts"])                       # 유형 모름 = 종전
        self.assertEqual(mp._en_gaps(card, "FDA 483 실사 관찰"), ["key_facts"])
        self.assertEqual(render.card_english_gaps(dict(card, card_type="행정처분")), ["key_facts"])
        # 한국어가 채웠는데 영문만 비면 여전히 반쪽 영어
        self.assertEqual(mp._en_gaps(_news_card(key_facts=["적용 대상: x"]), "규제 소식"), ["key_facts"])

    def test_handoff_card_types(self):
        payload = {"rows": [
            {"web_card_id": "n1", "card_scaffold": "### [규제 소식 · EMA] EMA — **{{TITLE_ISSUE}}**"},
            {"web_card_id": "g1", "card_scaffold": "### [지침·안내서 · FDA] Guidance — **x**"},
            {"web_card_id": "f1", "card_scaffold": "### [FDA 483 실사 관찰 · FDA] Acme — **x**"},
            {"web_card_id": "m1", "merged_into": "n1", "card_scaffold": "### [회수 · MFDS] x"},
            {"web_card_id": "z1"}]}
        self.assertEqual(mp.handoff_card_types(payload),
                         {"n1": "규제 소식", "g1": "지침·안내서", "f1": "FDA 483 실사 관찰"})

    def test_precheck_terms_by_type(self):
        delta = {"cards": {"n1": _news_card(), "f1": _news_card()}}
        lines = precheck.term_lines(delta, {"n1": "규제 소식", "f1": "FDA 483 실사 관찰"})
        self.assertFalse([x for x in lines if "n1" in x], lines)
        self.assertTrue([x for x in lines if "빈 필수 칸 f1: key_facts" in x], lines)
        self.assertTrue([x for x in precheck.term_lines(delta) if "n1" in x])      # 유형 모름 = 종전

    def test_merge_reads_handoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp) / "company" / "run" / "2026-11-23"
            run.mkdir(parents=True)
            (pathlib.Path(tmp) / "_handoff").mkdir()
            (pathlib.Path(tmp) / "_handoff" / "handoff_2026-11-23.json").write_text(json.dumps(
                {"rows": [{"web_card_id": "n1", "card_scaffold": "### [규제 소식 · EMA] x"}]},
                ensure_ascii=False), encoding="utf-8")
            (run / "desk.json").write_text(json.dumps(
                {"status": "완료", "run_date_kst": "2026-11-23",
                 "shards": {"delta": {"A": ["n1"]}, "deep": {}}}), encoding="utf-8")
            (run / "delta.A.json").write_text(json.dumps(
                {"status": "완료", "cards": {"n1": _news_card()}}, ensure_ascii=False), encoding="utf-8")
            (run / "delta.T.json").write_text(json.dumps(
                {"status": "완료", "tldr": ["a", "b", "c"], "tldr_en": ["a", "b", "c"]}), encoding="utf-8")
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                _delta, _deep, problems = mp.merge(str(run))
                self.assertEqual(problems, [])
                os.remove(pathlib.Path(tmp) / "_handoff" / "handoff_2026-11-23.json")
                _delta, _deep, problems = mp.merge(str(run))
                self.assertTrue(any("영문 슬롯 미완결" in p for p in problems), problems)
            finally:
                os.chdir(cwd)


class NewsKeyFactsAssembleTest(unittest.TestCase):
    """③ 주입·조립 — 소식 유형 카드만 빈 key_facts 를 경고로 통과."""

    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))

    def _case(self, card_type):
        s = _blank_scaffold_from(self.truth)
        d = copy.deepcopy(self.delta)
        cid = next(iter(d["cards"]))
        for c in s["cards"]:
            if c.get("id") == cid:
                c["card_type"] = card_type
        d["cards"][cid]["key_facts"] = []
        en = d["cards"][cid].get("en")
        if isinstance(en, dict):
            en["key_facts"] = []
        return s, d, cid

    def test_news_type_assembles_with_warning(self):
        s, d, cid = self._case("규제 소식")
        self.assertEqual([e for e in inj.validate_injection(s, d).errors if cid in e], [])
        out, report = apb.assemble_publish_brief(s, d, strict=True)
        card = next(c for c in out["cards"] if c["id"] == cid)
        self.assertEqual(card["key_facts"], [])
        self.assertTrue(any("key_facts 비움" in w for w in report.warnings), report.warnings)

    def test_other_type_still_blocks(self):
        s, d, _cid = self._case("행정처분")
        with self.assertRaises((apb.AssembleError, inj.SlotInjectionError)):
            apb.assemble_publish_brief(s, d, strict=True)


class WarnOnlyLintTest(unittest.TestCase):
    """④⑤ 경고만 — `lines()`(막는 줄)에는 들어가지 않는다."""

    def test_deep_banned_and_terms(self):
        deep = {"c1": {"deep_analysis": {
            "key_violations": [{"description": "귀사는 조사하지 않았다.", "original": "Your firm failed",
                                "risk": "품질이 흔들릴 수 있다."}],
            "fda_evaluation": "Warning Letter 승격 가능성이 크다. 최종처분일자는 20261109이다.",
            "required_remediation": {"deadline": "x", "items": ["CAPA 를 수립한다."]},
            "administrative_risks": "체계적 문제로 해석될 소지가 있다."}}}
        rep = inj.lint_deep_prose_style(deep)
        self.assertEqual(rep.lines(), [])
        text = "\n".join(rep.warn_lines())
        for lab in ("귀사", "흔들릴 수 있다", "승격", "가능성이 크다", "8자리 날짜", "체계적", "해석될",
                    "소지가", "경고서한"):
            self.assertIn(lab, text)

    def test_original_not_checked(self):
        deep = {"c1": {"deep_analysis": {"key_violations": [
            {"description": "회사는 조사하지 않았다.", "original": "Warning Letter 귀사"}]}}}
        self.assertEqual(inj.lint_deep_prose_style(deep).warn_lines(), [])

    def test_repeat_sentence_three_cards(self):
        same = "483은 최종 판정이 아니며 회신 내용에 따라 후속 조치 수위가 달라진다."
        deep = {f"c{i}": {"deep_analysis": {"inspectional_significance": same}} for i in range(3)}
        self.assertTrue(any("같은 문장 반복" in x for x in inj.lint_deep_prose_style(deep).warn_lines()))
        deep.pop("c2")
        self.assertFalse(any("같은 문장 반복" in x for x in inj.lint_deep_prose_style(deep).warn_lines()))

    def test_shard_wrapper_accepted(self):
        shard = {"status": "완료", "items": {"c1": {"deep_analysis": {"fda_evaluation": "귀사는 회신했다."}}}}
        self.assertTrue(inj.lint_deep_prose_style(shard).warn_lines())

    def test_admin_generic_subject(self):
        for bad in ("식약처가 제약사에 업무정지 1개월을 처분했다.",
                    "식약처가 의약품 수입·판매 업체에 판매업무정지 10일을 처분했다.",
                    "식약처가 의료기관에 과징금을 부과했다."):
            rep = inj.lint_prose_style({"cards": {"a1": {"summary": bad}}})
            self.assertTrue(rep.warn_lines(), bad)
            self.assertEqual(rep.lines(), [])
        ok = inj.lint_prose_style({"cards": {"a1": {"summary": "식약처가 제이더블유중외제약(주)에 처분했다."}}})
        self.assertEqual(ok.warn_lines(), [])

    def test_precheck_warn_lines_do_not_block(self):
        delta = {"cards": {"a1": {"title_issue": "업무정지", "summary": "식약처가 제약사에 처분했다.",
                                  "key_facts": ["처분: 업무정지"], "en": {}}}}
        self.assertTrue(precheck.warn_lines(delta))
        self.assertFalse([x for x in precheck.term_lines(delta, {}) if "식약처가" in x])


if __name__ == "__main__":
    unittest.main()
