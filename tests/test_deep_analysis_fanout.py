"""deep_analysis_fanout 테스트 — [WL 심층분석 fan-out] 오케스트레이션(순수·결정론).

build_jobs(handoff → 서브에이전트 작업목록) + assemble_deltas(응답 → 게이트 → inject 델타)
+ end-to-end(build_jobs → assemble → inject_slots.inject_deep_analysis 실병합).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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
        # Codex P3 / 실검증 관측(Intas): 서브에이전트가 'FD&C Act'를 'FD&amp;C Act'로 이스케이프해
        # 산출 → 그대로 두면 Jinja 자동이스케이프와 겹쳐 렌더가 이중 이스케이프('FD&amp;amp;C').
        # assemble 이 병합 전 원문자로 되돌려야 한다(실관측 값 그대로 회귀).
        da = dict(_GOOD_DA)
        da["administrative_risks"] = "미이행 시 수입금지 등 FD&amp;C Act 상의 규제 조치가 가능하다."
        r = fo.assemble_deltas(self.jobs, {"WL-1": da})
        stored = r.deltas["WL-1"]["deep_analysis"]["administrative_risks"]
        self.assertIn("FD&C Act", stored)
        self.assertNotIn("FD&amp;C", stored)


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


# ── [FDA 483 분석층 2026-07-02] card_type 이 Job → 게이트로 흘러 유형별 게이트를 고른다 ──────
_FDA483_BODY = (
    "During an inspection of your firm we observed OBSERVATION 1 that OOS results were "
    "invalidated without scientific justification and discrepancies were not investigated. "
    "OBSERVATION 2 aseptic processing areas were not adequately monitored and records were incomplete."
)
_FDA483_DA = {
    "key_violations": [
        {"citation": "21 CFR 211.192",   # 원문(관찰사항)엔 CFR 명시 없음 → 483 은 D2 WARN(비차단)
         "description": "OOS 결과를 과학적 근거 없이 무효화하고 불일치 조사를 문서화하지 않음",
         "risk": "불량 배치가 시장에 유통될 위험"},
    ],
    "inspectional_significance": "데이터 무결성·무균 관리의 systemic 결함으로 Warning Letter 승격 가능성이 있다.",
    "required_remediation": {"deadline": "483 수령 후 15영업일 이내 서면 회신",
                             "items": ["OOS 조사 절차를 재수립하고 소급 검토를 수행한다"]},
    "administrative_risks": "미시정 시 Warning Letter·Import Alert 로 이어질 수 있다.",
}


class CardTypeFlowTest(unittest.TestCase):
    def test_build_jobs_carries_kind_as_card_type(self):
        handoff = [{"card_id": "fda_483::D-1", "kind": "fda-483", "deep_analysis_ready": True,
                    "deep_analysis_input": {"body_full": _FDA483_BODY}}]
        jobs = fo.build_jobs(handoff)
        self.assertEqual(jobs[0].card_type, "fda-483")
        self.assertEqual(jobs[0].to_dict()["card_type"], "fda-483")

    def test_build_jobs_missing_kind_omits_card_type(self):
        jobs = fo.build_jobs([{"card_id": "x::y", "deep_analysis_ready": True,
                               "deep_analysis_input": {"body_full": _FDA483_BODY}}])
        self.assertEqual(jobs[0].card_type, "")
        self.assertNotIn("card_type", jobs[0].to_dict())   # 빈값은 직렬화 생략(후방호환)

    def test_dict_job_roundtrips_card_type(self):
        r = fo.assemble_deltas(
            [{"document_id": "D-1", "body_full": _FDA483_BODY, "card_type": "fda-483"}],
            {"D-1": _FDA483_DA})
        self.assertIn("D-1", r.deltas)     # 483 스키마·D2 WARN 로 병합

    def test_483_ungrounded_cfr_merges_via_card_type(self):
        # card_type 이 게이트에 흘러 483 스키마(inspectional_significance)·D2 WARN 적용 →
        # CFR 미근거여도 병합(WL 이면 하드 FAIL·보류 — gate 테스트가 대조군을 잠근다).
        jobs = [fo.Job("D-1", _FDA483_BODY, card_type="fda-483")]
        r = fo.assemble_deltas(jobs, {"D-1": _FDA483_DA})
        self.assertIn("D-1", r.deltas)
        self.assertEqual(r.merged, 1)
        self.assertEqual(r.deltas["D-1"], {"deep_analysis": _FDA483_DA, "source_text": _FDA483_BODY})


# ── [번역층 독립 2026-09-28] 게이트 FAIL 이어도 483 관찰 국문 번역은 델타에 남는다 ───────────
# 종전 assemble_deltas 는 게이트 FAIL 카드를 통째로 버려 `observations_ko` 까지 잃었고, 483 은
# 번역 1건만 빠져도 fail-closed 인 `render.validate_483_observations` 가 그 주 브리프 전체를
# 막는다(2026-07-20·08-10 발행 차단과 같은 층). 클라우드 쌍둥이 `delta_bridge._gate_deep_analysis`
# 는 2026-08-24 에 고쳐졌다. FAIL 표본은 실제 발행을 막아 온 모양 그대로 — 4섹션은 멀쩡한데
# original 이 "Specifically…" 상세 앞에서 잘려 D5b(483 전용 하드 FAIL)에 걸린다.
_FDA483_TRUNC_BODY = (
    "OBSERVATION 1 There is a failure to thoroughly review any unexplained discrepancy. "
    "Specifically, the investigation did not extend to other potentially affected batches. "
    "OBSERVATION 2 Established sampling plans are not documented at the time of performance. "
    "Specifically, sampling records were completed after the operation."
)
_FDA483_TRUNC_DA = {
    "key_violations": [
        {"citation": "21 CFR 211.192",
         "original": "There is a failure to thoroughly review any unexplained discrepancy.",
         "observation": "설명되지 않은 불일치를 철저히 검토하지 않았다.",
         "risk": "영향 배치를 놓친 채 출하할 위험"},
    ],
    "inspectional_significance": "조사 범위 결함이 반복되면 Warning Letter 로 승격될 수 있다.",
    "required_remediation": {"deadline": "483 수령 후 15영업일 이내 서면 회신",
                             "items": ["불일치 조사 범위를 영향 배치 전체로 확장한다"]},
    "administrative_risks": "미시정 시 Warning Letter·Import Alert 로 이어질 수 있다.",
}
# 번호는 픽스처 `fda_483_observations` 의 결정론 관찰(1·2)과 1:1.
_OBS_KO = [
    {"number": "1", "deficiency_ko": "설명되지 않은 불일치를 철저히 검토하지 않았다.",
     "detail_ko": "조사가 영향을 받았을 수 있는 다른 배치로 확장되지 않았다."},
    {"number": "2", "deficiency_ko": "확립된 검체 채취 계획이 수행 시점에 문서화되지 않는다.",
     "detail_ko": "검체 채취 기록이 작업이 끝난 뒤에 작성되었다."},
]


def _render_mod():
    web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
    if web_dir not in sys.path:
        sys.path.insert(0, web_dir)
    import render  # noqa: E402
    return render


class GateFailKeepsTranslationTest(unittest.TestCase):
    """(a) FAIL 이어도 번역층 보존 · (b) 번역층 없으면 종전대로 항목 없음 · (c) PASS 경로 불변."""

    _JOB = fo.Job("fda483-1", _FDA483_TRUNC_BODY, card_type="fda-483")

    def test_fail_sample_is_really_a_gate_fail(self):
        # 전제 고정 — 표본이 게이트를 통과해 버리면 아래 테스트들이 FAIL 분기를 안 밟는다.
        import verify_deep_analysis as vda
        gate = vda.run_deep_analysis_gate(dict(_FDA483_TRUNC_DA), _FDA483_TRUNC_BODY,
                                          card_type="fda-483")
        self.assertFalse(gate.ok)
        self.assertIn("D5-483-ORIGINAL-TRUNCATED", gate.report)

    # (a)
    def test_483_gate_fail_keeps_observations_ko_as_translation_only_entry(self):
        r = fo.assemble_deltas([self._JOB],
                               {"fda483-1": dict(_FDA483_TRUNC_DA, observations_ko=_OBS_KO)})
        self.assertEqual(r.deltas, {"fda483-1": {"observations_ko": _OBS_KO,
                                                 "source_text": _FDA483_TRUNC_BODY}})
        self.assertEqual(list(r.deltas["fda483-1"]), ["observations_ko", "source_text"])
        o = r.outcomes[0]
        self.assertEqual(o.status, fo.GATE_FAILED)              # outcome 은 FAIL 그대로
        self.assertIn("D5-483-ORIGINAL-TRUNCATED", o.detail)
        self.assertEqual(o.kept_layers, ("observations_ko", "source_text"))
        self.assertEqual((r.merged, r.held, r.translation_kept), (0, 1, 1))
        report = r.report()
        self.assertIn("병합 0 · 보류 1 (총 1) · 보류 중 번역 층 보존 1", report)
        self.assertIn("✖ 게이트 FAIL — fda483-1 · 분석 drop·번역 층 보존("
                      "observations_ko, source_text)", report)

    def test_gate_exception_keeps_translation_and_other_cards_continue(self):
        # 종전엔 게이트 예외가 assemble 전체를 죽였다(출력 파일조차 없음 → 전 카드 번역 소실).
        jobs = [self._JOB, fo.Job("WL-1", _BODY)]
        responses = {"fda483-1": dict(_FDA483_TRUNC_DA, observations_ko=_OBS_KO),
                     "WL-1": _GOOD_DA}
        with mock.patch.object(fo.vda, "run_deep_analysis_gate",
                               side_effect=RuntimeError("boom")):
            r = fo.assemble_deltas(jobs, responses)
        self.assertEqual(r.deltas, {"fda483-1": {"observations_ko": _OBS_KO,
                                                 "source_text": _FDA483_TRUNC_BODY}})
        self.assertEqual([o.status for o in r.outcomes], [fo.GATE_FAILED, fo.GATE_FAILED])
        self.assertIn("RuntimeError: boom", r.outcomes[0].detail)
        self.assertEqual(r.outcomes[1].kept_layers, ())         # 번역 없는 WL 은 통째 보류

    # (b) 대조군 — 번역층이 없으면 종전과 똑같이 항목이 남지 않는다.
    def test_non483_gate_fail_without_translation_leaves_no_entry(self):
        bad = {**_GOOD_DA, "key_violations": _GOOD_DA["key_violations"] + [
            {"citation": "21 CFR 999.99", "description": "지어낸 조항", "risk": "-"}]}
        r = fo.assemble_deltas([fo.Job("WL-1", _BODY, card_type="warning-letter")],
                               {"WL-1": bad})
        self.assertEqual(r.deltas, {})
        self.assertEqual(r.outcomes[0].status, fo.GATE_FAILED)
        self.assertNotIn("번역 층 보존", r.report())

    def test_483_gate_fail_with_empty_observations_ko_leaves_no_entry(self):
        # source_text 만 남은 빈 껍데기 항목은 어느 계층도 소비하지 않는다.
        r = fo.assemble_deltas([self._JOB],
                               {"fda483-1": dict(_FDA483_TRUNC_DA, observations_ko=[])})
        self.assertEqual(r.deltas, {})
        self.assertNotIn("번역 층 보존", r.report())

    # (c) PASS 경로 불변 — 4섹션 + source_text + 최상위 observations_ko, 키 순서까지 그대로.
    def test_483_gate_pass_delta_unchanged(self):
        r = fo.assemble_deltas([fo.Job("D-1", _FDA483_BODY, card_type="fda-483")],
                               {"D-1": dict(_FDA483_DA, observations_ko=_OBS_KO)})
        self.assertEqual(r.outcomes[0].status, fo.MERGED)
        self.assertEqual(r.deltas["D-1"], {"deep_analysis": _FDA483_DA,
                                           "source_text": _FDA483_BODY,
                                           "observations_ko": _OBS_KO})
        self.assertEqual(list(r.deltas["D-1"]), ["deep_analysis", "source_text", "observations_ko"])
        self.assertNotIn("observations_ko", r.deltas["D-1"]["deep_analysis"])
        self.assertNotIn("번역 층 보존", r.report())

    def test_residue_matches_cloud_bridge_for_same_input(self):
        # 두 생산 경로(로컬 fanout · 클라우드 브릿지)가 같은 FAIL 입력에서 같은 결과를 내야 한다 —
        # 번역층이 있으면 같은 번역 전용 항목, 없으면 둘 다 항목 없음(브릿지는 None 을 돌려준다).
        import delta_bridge as db
        for with_ko in (True, False):
            with self.subTest(observations_ko=with_ko):
                response = dict(_FDA483_TRUNC_DA)
                entry = {"deep_analysis": dict(_FDA483_TRUNC_DA),
                         "source_text": _FDA483_TRUNC_BODY}
                if with_ko:
                    response["observations_ko"] = entry["observations_ko"] = _OBS_KO
                fan = fo.assemble_deltas([self._JOB], {"fda483-1": response})
                bridge = db._gate_deep_analysis({"fda483-1": entry})
                self.assertEqual(fan.deltas or None, bridge)


class GateFailPublishGateEndToEndTest(unittest.TestCase):
    """분석 FAIL 이 브리프 발행을 막지 않는다 — build_jobs → assemble → inject → 발행 게이트."""

    def test_483_fail_card_still_passes_render_publish_gate(self):
        fx = _load_input("fda_483_observations")
        raw = dict(fx["raw"]); raw["fda483_body_full"] = _FDA483_TRUNC_BODY
        card = cs.build_card_scaffold(fx["row"], raw)
        brief = cs.assemble_web_brief([card], {
            "run_date_kst": "2026-09-28", "window": "2026-09-21 ~ 2026-09-28",
            "publish_date": "2026-09-28", "intake_total": 1})

        jobs = fo.build_jobs([card.to_dict()])
        self.assertEqual([(j.document_id, j.card_type) for j in jobs],
                         [(brief["cards"][0]["id"], "fda-483")])
        doc = jobs[0].document_id
        result = fo.assemble_deltas(jobs, {doc: dict(_FDA483_TRUNC_DA, observations_ko=_OBS_KO)})
        self.assertEqual(result.outcomes[0].status, fo.GATE_FAILED)   # 분석은 여전히 보류

        inj.inject_deep_analysis(brief, result.deltas)
        out = brief["cards"][0]
        self.assertIsNone(out.get("deep_analysis"))                   # 4섹션은 안 실린다
        obs = out["deterministic_detail"]["observations"]
        self.assertEqual([(o.get("deficiency_ko"), o.get("detail_ko")) for o in obs],
                         [(t["deficiency_ko"], t["detail_ko"]) for t in _OBS_KO])
        self.assertEqual(_render_mod().validate_483_observations([brief]), [])


class GateFailCliTest(unittest.TestCase):
    def test_cli_writes_translation_only_entry_and_says_so(self):
        # 월요일 백필은 이 CLI 로 돈다 — 출력 파일과 리포트가 같은 사실을 말해야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            jp = os.path.join(tmp, "j.json"); rp = os.path.join(tmp, "r.json")
            dp = os.path.join(tmp, "d.json")
            with open(jp, "w", encoding="utf-8") as f:
                json.dump([{"document_id": "fda483-1", "body_full": _FDA483_TRUNC_BODY,
                            "card_type": "fda-483"}], f, ensure_ascii=False)
            with open(rp, "w", encoding="utf-8") as f:
                json.dump({"fda483-1": dict(_FDA483_TRUNC_DA, observations_ko=_OBS_KO)}, f,
                          ensure_ascii=False)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = fo.main(["assemble", "--jobs", jp, "--responses", rp, "--out", dp])
            self.assertEqual(rc, 0)
            with open(dp, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"fda483-1": {
                    "observations_ko": _OBS_KO, "source_text": _FDA483_TRUNC_BODY}})
        self.assertIn("분석 drop·번역 층 보존", err.getvalue())
        self.assertIn("[assemble] 델타 1건 (번역 전용 1건 포함) →", err.getvalue())


if __name__ == "__main__":
    unittest.main()
