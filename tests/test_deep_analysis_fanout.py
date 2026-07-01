"""deep_analysis_fanout 테스트 — [WL 심층분석 fan-out] 오케스트레이션(순수·결정론).

build_jobs(handoff → 서브에이전트 작업목록) + assemble_deltas(응답 → 게이트 → inject 델타)
+ end-to-end(build_jobs → assemble → inject_slots.inject_deep_analysis 실병합).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import deep_analysis_fanout as fo
import card_scaffold as cs
import inject_slots as inj

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def _load_input(name):
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


_BODY = (
    "During our inspection we observed violations including 21 CFR 211.192 (failure to "
    "investigate discrepancies). Within 15 working days respond in writing. Failure may "
    "result in seizure or injunction."
)

_GOOD_DA = {
    "key_violations": [
        {"citation": "21 CFR 211.192", "description": "불일치 조사 미흡", "risk": "품질 위험"},
    ],
    "fda_evaluation": "FDA는 이전 대응이 불충분했다고 평가했다.",
    "required_remediation": {"deadline": "15영업일 이내 서면 회신",
                             "items": ["불일치 조사 절차를 재수립하고 결과를 문서화한다"]},
    "administrative_risks": "미이행 시 압류·금지명령 등 법적 조치가 가능하다.",
}


class BuildJobsTest(unittest.TestCase):
    def _ready(self, doc="WL-1", body=_BODY):
        return {"card_id": f"fda_warning_letter::{doc}", "deep_analysis_ready": True,
                "deep_analysis_input": {"body_full": body}}

    def test_ready_card_becomes_job_with_derived_document_id(self):
        jobs = fo.build_jobs([self._ready("WL-9")])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].document_id, "WL-9")   # 'source::document_id' 에서 도출
        self.assertEqual(jobs[0].body_full, _BODY)

    def test_not_ready_card_skipped(self):
        self.assertEqual(fo.build_jobs([{"card_id": "x::y", "kind": "recall-quality"}]), [])

    def test_ready_but_empty_body_skipped(self):
        self.assertEqual(fo.build_jobs([self._ready(body="   ")]), [])

    def test_cards_wrapper_and_explicit_document_id(self):
        handoff = {"cards": [{"deep_analysis_ready": True, "document_id": "DOC-7",
                              "deep_analysis_input": {"body_full": _BODY}}]}
        self.assertEqual(fo.build_jobs(handoff)[0].document_id, "DOC-7")

    def test_document_id_dedup_first_wins(self):
        c = self._ready("D")
        self.assertEqual(len(fo.build_jobs([c, dict(c)])), 1)

    def test_order_preserved(self):
        jobs = fo.build_jobs([self._ready("A"), self._ready("B"), self._ready("C")])
        self.assertEqual([j.document_id for j in jobs], ["A", "B", "C"])


class AssembleTest(unittest.TestCase):
    def setUp(self):
        self.jobs = [fo.Job("WL-1", _BODY)]

    def test_passing_response_makes_inject_delta(self):
        r = fo.assemble_deltas(self.jobs, {"WL-1": _GOOD_DA})
        self.assertEqual(r.deltas["WL-1"], {"deep_analysis": _GOOD_DA, "source_text": _BODY})
        self.assertEqual(r.merged, 1)
        self.assertEqual(r.held, 0)

    def test_fabricated_citation_excluded_with_reason(self):
        bad = {**_GOOD_DA, "key_violations": _GOOD_DA["key_violations"] + [
            {"citation": "21 CFR 999.99", "description": "지어낸 조항", "risk": "-"}]}
        r = fo.assemble_deltas(self.jobs, {"WL-1": bad})
        self.assertNotIn("WL-1", r.deltas)               # 병합 보류
        self.assertEqual(r.outcomes[0].status, fo.GATE_FAILED)
        self.assertIn("999.99", r.outcomes[0].detail)    # 사유(게이트 report) 로그
        self.assertIn("병합 0 · 보류 1", r.report())

    def test_missing_response_is_held_not_error(self):
        r = fo.assemble_deltas(self.jobs, {})
        self.assertEqual(r.outcomes[0].status, fo.MISSING_RESPONSE)
        self.assertEqual(r.deltas, {})

    def test_invalid_response_is_held(self):
        r = fo.assemble_deltas(self.jobs, {"WL-1": "not-a-dict"})
        self.assertEqual(r.outcomes[0].status, fo.INVALID_RESPONSE)
        self.assertEqual(r.deltas, {})

    def test_jobs_from_dicts_roundtrip(self):
        # jobs.json 에서 읽은 dict 목록도 그대로 처리(Job 객체와 동치).
        r = fo.assemble_deltas([{"document_id": "WL-1", "body_full": _BODY}], {"WL-1": _GOOD_DA})
        self.assertIn("WL-1", r.deltas)

    def test_html_entities_normalized_before_merge(self):
        # 실검증 발견(Intas 'FD&amp;C'): 서브에이전트가 & 를 이스케이프하면 Jinja 자동이스케이프와
        # 겹쳐 렌더가 이중 이스케이프된다. assemble 이 병합 전 원문자로 되돌려야 한다.
        da = dict(_GOOD_DA)
        da["administrative_risks"] = "미이행 시 수입경보(Import Alert) 및 R&amp;D 중단 위험이 있다."
        r = fo.assemble_deltas(self.jobs, {"WL-1": da})
        stored = r.deltas["WL-1"]["deep_analysis"]["administrative_risks"]
        self.assertIn("R&D", stored)
        self.assertNotIn("&amp;", stored)


class EndToEndTest(unittest.TestCase):
    """build_jobs → assemble → inject_slots.inject_deep_analysis 실병합(순수 계층 전체).

    ★ handoff `card_id`('source::document_id')에서 도출한 document_id 가 web-card `id`
    (=row.document_id)와 정확히 일치해야 inject 델타 키가 맞는다 — 이 테스트가 그 계약을 잠근다.
    """

    def test_fanout_delta_merges_into_brief(self):
        fx = _load_input("warning_letter_excerpt")
        raw = dict(fx["raw"]); raw["wl_body_full"] = _BODY
        card = cs.build_card_scaffold(fx["row"], raw)
        brief = cs.assemble_web_brief([card], {
            "run_date_kst": "2026-07-01", "window": "2026-06-24 ~ 2026-07-01",
            "publish_date": "2026-07-01", "intake_total": 1})

        jobs = fo.build_jobs([card.to_dict()])
        self.assertEqual(len(jobs), 1)
        doc = jobs[0].document_id
        self.assertEqual(doc, brief["cards"][0]["id"])   # 계약: 델타 키 == web-card id

        result = fo.assemble_deltas(jobs, {doc: _GOOD_DA})
        report = inj.inject_deep_analysis(brief, result.deltas)
        self.assertEqual(report.errors, [])
        self.assertEqual(brief["cards"][0]["deep_analysis"], _GOOD_DA)


class CliTest(unittest.TestCase):
    def test_cli_build_jobs_then_assemble(self):
        with tempfile.TemporaryDirectory() as tmp:
            hp = os.path.join(tmp, "h.json"); jp = os.path.join(tmp, "j.json")
            rp = os.path.join(tmp, "r.json"); dp = os.path.join(tmp, "d.json")
            with open(hp, "w", encoding="utf-8") as f:
                json.dump([{"card_id": "fda::WL-1", "deep_analysis_ready": True,
                            "deep_analysis_input": {"body_full": _BODY}}], f, ensure_ascii=False)
            self.assertEqual(fo.main(["build-jobs", "--handoff", hp, "--out", jp]), 0)
            with open(jp, encoding="utf-8") as f:
                self.assertEqual(json.load(f)[0]["document_id"], "WL-1")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump({"WL-1": _GOOD_DA}, f, ensure_ascii=False)
            self.assertEqual(fo.main(["assemble", "--jobs", jp, "--responses", rp, "--out", dp]), 0)
            with open(dp, encoding="utf-8") as f:
                deltas = json.load(f)
            self.assertEqual(deltas["WL-1"]["source_text"], _BODY)


if __name__ == "__main__":
    unittest.main()
