"""EU/UK GMP 비준수(NCR) 상세 국문 병기 — 방출 → 작업 → 델타 → 병합 전 구간 회귀.

배경(2026-07-27 사용자 지적): 발행된 브리프의 "비준수 상세" 블록이 **전부 영문**이었다.
483·WL 에는 `deficiency_ko`/`statement_ko` 병기 층이 있는데 NCR 만 없었고, 원인은 문구가
아니라 **파이프라인 부재**였다 — NCR kind 는 `deep_body_key` 가 없어 fan-out 대상 자체가
아니었으므로 번역이 산출될 자리가 없었다. 이 파일은 그 자리(번역 전용 층)를 고정한다.

체인: CardScaffold.translation_fields() → handoff → build_translation_jobs →
      assemble_translation_deltas → inject_slots._merge_ncr_translations → card.html
"""
import json
import os
import sys
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_scaffold as cs
import deep_analysis_fanout as fan
import delta_bridge as db
import grm_handoff as gh
import inject_slots as inj

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def _load(name):
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as fh:
        return json.load(fh)


class TranslationFieldsEmissionTest(unittest.TestCase):
    """`translation_fields()` — NCR 카드에만, 결정론 상세와 **같은 원문**을 방출."""

    def _scaffold(self, name):
        data = _load(name)
        return cs.build_card_scaffold(data["row"], data["raw"])

    def test_eu_ncr_emits_translation_input(self):
        card = self._scaffold("eu_gmp_ncr")
        fields = card.translation_fields()
        self.assertTrue(fields.get("ncr_translation_ready"))
        self.assertEqual(fields.get("kind"), card.kind)
        payload = fields["ncr_translation_input"]
        self.assertIn("nature", payload)
        self.assertIn("action", payload)

    def test_mhra_ncr_emits_translation_input(self):
        card = self._scaffold("mhra_gmp_ncr")
        self.assertTrue(card.translation_fields().get("ncr_translation_ready"))

    def test_translation_input_matches_published_detail_verbatim(self):
        """번역 입력 = 발행 카드에 실릴 원문과 **글자 단위로 동일**.

        두 값이 갈리면 국문이 다른 원문에 붙는다(2026-07-21 findings 교차연결 사고와 같은 종).
        같은 producer(`_deterministic_detail`)를 쓰는지를 값으로 확인한다.
        """
        for name in ("eu_gmp_ncr", "mhra_gmp_ncr"):
            with self.subTest(name=name):
                card = self._scaffold(name)
                detail = card.to_web_card()["deterministic_detail"]
                payload = card.translation_fields()["ncr_translation_input"]
                for key, value in payload.items():
                    self.assertEqual(value, detail[key])

    def test_non_ncr_cards_emit_nothing(self):
        """다른 유형은 완전 무영향 — 빈 dict(기존 handoff 바이트 불변)."""
        for name in ("warning_letter_chemical", "fda_483", "gmp_inspection_periodic"):
            with self.subTest(name=name):
                self.assertEqual(self._scaffold(name).translation_fields(), {})

    def test_ncr_is_not_deep_analysis_target(self):
        """NCR 은 심층분석 대상이 아니다 — 번역만 요구한다(4섹션 게이트 강제 방지)."""
        card = self._scaffold("eu_gmp_ncr")
        self.assertFalse(card.deep_analysis_ready)
        self.assertEqual(card.deep_fields(), {})

    def test_both_serializers_carry_the_fields(self):
        """`to_dict()` 와 실제 Notion handoff v2 빌더가 **둘 다** 싣는다.

        2026-07-27 deep 입력 사고의 재발 방지 — 직렬화기가 둘로 갈라져 한쪽에만 실리면
        클라우드 Routine 은 대상 0건으로 보고 조용히 단계를 건너뛴다.
        """
        data = _load("eu_gmp_ncr")
        card = cs.build_card_scaffold(data["row"], data["raw"])
        self.assertTrue(card.to_dict().get("ncr_translation_ready"))
        row = dict(data["row"], raw=data["raw"])   # K2-prep 이 붙이는 형태 그대로
        payload = gh.build_routine_handoff_payload_v2(
            [row], date(2026, 7, 27), 7, datetime(2026, 7, 27, 7, 30))
        rows = payload.get("rows") or []
        self.assertTrue(rows, "handoff v2 행이 비었다")
        self.assertTrue(rows[0].get("ncr_translation_ready"),
                        "handoff v2 에 번역 입력이 실리지 않았다")
        self.assertIn("nature", rows[0]["ncr_translation_input"])


class TranslationJobsTest(unittest.TestCase):
    HANDOFF = {"cards": [
        {"card_id": "EudraGMDP::186339", "kind": "eu-gmp-ncr",
         "ncr_translation_ready": True,
         "ncr_translation_input": {"nature": "Critical deficiencies.",
                                   "action": "Prohibition of supply."}},
        {"card_id": "FDA::fda483-1", "kind": "fda-483", "deep_analysis_ready": True,
         "deep_analysis_input": {"body_full": "OBSERVATION 1 ..."}},
    ]}

    def test_build_translation_jobs_picks_only_ncr(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        self.assertEqual([j.document_id for j in jobs], ["186339"])
        self.assertEqual(jobs[0].card_type, "eu-gmp-ncr")
        self.assertEqual(set(jobs[0].fields), {"nature", "action"})

    def test_deep_jobs_unaffected(self):
        jobs = fan.build_jobs(self.HANDOFF)
        self.assertEqual([j.document_id for j in jobs], ["fda483-1"])

    def test_empty_input_skipped(self):
        jobs = fan.build_translation_jobs({"cards": [
            {"card_id": "x::1", "ncr_translation_ready": True,
             "ncr_translation_input": {"nature": "   "}}]})
        self.assertEqual(jobs, [])

    def test_assemble_translation_deltas(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        out = fan.assemble_translation_deltas(
            jobs, {"186339": {"nature_ko": "중대결함.", "action_ko": "공급 금지."}})
        self.assertEqual(out, {"186339": {"ncr_ko": {"nature_ko": "중대결함.",
                                                     "action_ko": "공급 금지."}}})

    def test_assemble_drops_fields_without_source(self):
        """원문에 없는 필드의 번역은 버린다 — 근거 없는 국문 문장 차단."""
        jobs = fan.build_translation_jobs(self.HANDOFF)
        out = fan.assemble_translation_deltas(
            jobs, {"186339": {"nature_ko": "중대결함.", "additional_ko": "지어낸 문장."}})
        self.assertEqual(out["186339"]["ncr_ko"], {"nature_ko": "중대결함."})

    def test_missing_response_is_not_an_error(self):
        jobs = fan.build_translation_jobs(self.HANDOFF)
        self.assertEqual(fan.assemble_translation_deltas(jobs, {}), {})

    def test_jobs_roundtrip_through_json(self):
        """jobs.json 경유(CLI 경로)에도 동형으로 동작."""
        jobs = fan.build_translation_jobs(self.HANDOFF)
        raw = json.loads(json.dumps([j.to_dict() for j in jobs]))
        out = fan.assemble_translation_deltas(raw, {"186339": {"nature_ko": "중대결함."}})
        self.assertIn("186339", out)


class MergeNcrTranslationsTest(unittest.TestCase):
    def _card(self, dtype="eu_gmp_ncr_statement"):
        return {"id": "186339", "deterministic_detail": {
            "type": dtype, "nature": "Critical deficiencies.",
            "action": "Prohibition of supply."}}

    def test_merges_into_detail(self):
        card = self._card()
        rep = inj.InjectionReport()
        inj._merge_ncr_translations(
            card, {"nature_ko": "중대결함.", "action_ko": "공급 금지."}, rep, "186339")
        dd = card["deterministic_detail"]
        self.assertEqual(dd["nature_ko"], "중대결함.")
        self.assertEqual(dd["action_ko"], "공급 금지.")
        self.assertEqual(dd["nature"], "Critical deficiencies.", "원문이 훼손됐다")

    def test_mhra_type_also_merges(self):
        card = self._card("mhra_gmp_ncr_statement")
        inj._merge_ncr_translations(card, {"nature_ko": "무균 실패."},
                                    inj.InjectionReport(), "x")
        self.assertEqual(card["deterministic_detail"]["nature_ko"], "무균 실패.")

    def test_pairless_translation_skipped(self):
        card = self._card()
        rep = inj.InjectionReport()
        inj._merge_ncr_translations(card, {"additional_ko": "짝 없는 국문."}, rep, "186339")
        self.assertNotIn("additional_ko", card["deterministic_detail"])
        self.assertTrue(any("원문" in w for w in rep.warnings))

    def test_other_detail_types_untouched(self):
        card = {"id": "x", "deterministic_detail": {
            "type": "fda_483_observations", "count": 1, "observations": []}}
        inj._merge_ncr_translations(card, {"nature_ko": "x"}, inj.InjectionReport(), "x")
        self.assertNotIn("nature_ko", card["deterministic_detail"])

    def test_inject_deep_analysis_routes_ncr_ko(self):
        """`inject_deep_analysis` 가 심층분석 없는 번역 전용 항목도 처리한다."""
        brief = {"cards": [self._card()]}
        rep = inj.inject_deep_analysis(
            brief, {"186339": {"ncr_ko": {"nature_ko": "중대결함."}}})
        self.assertEqual(
            brief["cards"][0]["deterministic_detail"]["nature_ko"], "중대결함.")
        self.assertFalse(rep.errors)


class Fda483OcrProvenanceTest(unittest.TestCase):
    """[OCR 출처 표기 2026-07-27] 우리가 OCR 로 판독한 영문을 "원문"이라고 부르지 않는다.

    스캔 483 OCR 폴백이 들어오면서 관찰 블록의 영문이 두 출처로 갈렸다 — 원문 텍스트층과
    우리 판독물. 렌더는 이 블록을 "원문 · FDA 483" 이라고 표시하는데, 판독물에 그 라벨을
    달면 거짓이다(OCR 오인식이 실측됐다). `text_source` 로 갈라 표기한다.
    """

    def test_scaffold_marks_ocr_derived_observations(self):
        raw = {"fda_483_observations": [
            {"number": "1", "deficiency": "Aseptic processing areas are deficient.",
             "detail": ""}],
               "fda483_text_status": "pdf-ok-ocr"}
        self.assertEqual(cs._detail_fda_483_observations({}, raw).get("text_source"), "ocr")

    def test_scaffold_leaves_native_text_unlabelled(self):
        """기존 카드(텍스트층 산출)는 키 미추가 — 골든 바이트 불변(additive)."""
        raw = {"fda_483_observations": [
            {"number": "1", "deficiency": "Aseptic processing areas are deficient.",
             "detail": ""}],
               "fda483_text_status": "pdf-ok"}
        self.assertNotIn("text_source", cs._detail_fda_483_observations({}, raw))

    def test_assembly_refresh_carries_provenance(self):
        import assemble_publish_brief as apb
        brief = {"cards": [{"id": "fda483-1"}]}
        report = apb.AssembleReport()
        apb._refresh_483_observations(
            brief,
            {"fda483-1": {"source_text": "OBSERVATION 1\nAseptic failure.\n"
                                         "Specifically, first air was blocked.",
                          "source_text_status": "pdf-ok-ocr"}},
            report)
        dd = brief["cards"][0]["deterministic_detail"]
        self.assertEqual(dd["type"], "fda_483_observations")
        self.assertEqual(dd["text_source"], "ocr")

    def test_assembly_refresh_without_status_stays_unlabelled(self):
        """출처 표기가 없는 옛 델타는 종전대로 — 원문 텍스트층 가정(무회귀)."""
        import assemble_publish_brief as apb
        brief = {"cards": [{"id": "fda483-1"}]}
        apb._refresh_483_observations(
            brief,
            {"fda483-1": {"source_text": "OBSERVATION 1\nAseptic failure.\n"
                                         "Specifically, first air was blocked."}},
            apb.AssembleReport())
        self.assertNotIn("text_source", brief["cards"][0]["deterministic_detail"])


class DeltaBridgeTranslationPassthroughTest(unittest.TestCase):
    """번역 전용 항목이 심층분석 근거 게이트에 걸려 조용히 사라지지 않는다.

    종전 `_gate_deep_analysis` 는 `deep_analysis` 키가 없는 entry 를 통째로 4섹션 산출물로
    간주해 게이트에 태웠고 — 검증할 분석이 없으므로 — 전건 FAIL·drop 시켰다. 관찰 국문
    번역(`observations_ko`)이 클라우드 경로에서 사라지던 구멍이고, NCR 번역도 같은 자리에서
    죽었을 것이다.
    """

    def test_translation_only_entry_survives(self):
        deep = {"186339": {"ncr_ko": {"nature_ko": "중대결함."}}}
        self.assertEqual(db._gate_deep_analysis(deep), deep)

    def test_observations_only_entry_survives(self):
        deep = {"fda483-1": {"source_text": "OBSERVATION 1 ...",
                             "observations_ko": [{"number": "1", "deficiency_ko": "무균."}]}}
        self.assertEqual(db._gate_deep_analysis(deep), deep)

    def test_empty_entry_still_dropped(self):
        self.assertIsNone(db._gate_deep_analysis({"x": {"nonsense": 1}}))


if __name__ == "__main__":
    unittest.main()
