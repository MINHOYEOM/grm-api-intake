"""원문 무결성 트랙 회귀 — 2026-07-20 483 전수 점검(70건)에서 드러난 결함들.

점검이 찾아낸 것:
  · 483 8건이 **원문에 관찰이 있는데** "관찰 원문 없음"으로 발행돼 있었다(그중 2건은 당주분).
    수집 시점 추출 실패는 발행물에 흔적을 남기지 않아, 저장소만 봐서는 영영 알 수 없었다.
  · 그 2건을 되살리려 보니 파서에 결함이 둘 더 있었다 — PDF 서브셋 폰트 합자(`iniƟal`)와
    문장 중간에 낀 관찰 참조를 표제로 오인해 만든 가짜 관찰.
  · 되살린 카드의 번역이 조립에서 버려졌다(관찰 번역이 심층분석 게이트 뒤에 묶여 있었다).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assemble_publish_brief as apb  # noqa: E402
import card_scaffold as cs  # noqa: E402
import collect_fda_483 as f483  # noqa: E402
import inject_slots  # noqa: E402
import verify_published_sources as vps  # noqa: E402


class LigatureNormalizationTest(unittest.TestCase):
    """PDF 서브셋 폰트 합자 복원 — `_text_corruption_ratio` 가 못 잡는 침묵 결함."""

    def test_restores_observed_artifacts(self):
        raw = "iniƟal receipt of the informaƟon · wriƩen procedures · Speciﬁcally idenƟﬁed"
        self.assertEqual(
            f483.normalize_pdf_ligatures(raw),
            "initial receipt of the information · written procedures · Specifically identified")

    def test_standard_ligature_block(self):
        self.assertEqual(f483.normalize_pdf_ligatures("eﬀect ﬂow ﬁle"), "effect flow file")

    def test_bullet_artifact_dropped(self):
        self.assertEqual(f483.normalize_pdf_ligatures("aʖb"), "ab")

    def test_clean_text_untouched(self):
        clean = "There is a failure to thoroughly review any unexplained discrepancy."
        self.assertEqual(f483.normalize_pdf_ligatures(clean), clean)

    def test_empty_safe(self):
        self.assertEqual(f483.normalize_pdf_ligatures(""), "")

    def test_corruption_ratio_does_not_see_ligatures(self):
        # 이 테스트가 '왜 별도 복원이 필요한가'를 고정한다 — 깨짐률로는 판별 불가.
        self.assertEqual(f483._text_corruption_ratio("iniƟal informaƟon wriƩen"), 0.0)

    def test_parser_normalizes_committed_source_text(self):
        # 이미 커밋된 낡은 source_text 를 조립에서 재파싱할 때도 복원돼야 한다.
        text = ("WE OBSERVED: OBSERVATION 1 Not all adverse drug experiences that are both "
                "serious and unexpected have been reported to FDA within 15 calendar days of "
                "iniƟal receipt of the informaƟon. Speciﬁcally, the ﬁrm did not report.")
        obs = f483._extract_483_observations_from_text(text)
        self.assertEqual(len(obs), 1)
        self.assertIn("initial receipt of the information", obs[0]["deficiency"])
        self.assertNotIn("Ɵ", obs[0]["deficiency"] + obs[0]["detail"])


class MidSentenceObservationReferenceTest(unittest.TestCase):
    """표제 뒤 첫 문장이 소문자면 문장 중간에 낀 참조다(신호 ③)."""

    def test_lowercase_continuation_is_rejected(self):
        text = ("WE OBSERVED: OBSERVATION 1 An investigation was not conducted in accordance "
                "with the signed statement of investigator. Specifically, subjects were "
                "enrolled despite ineligibility. The findings listed on the Form FDA 483, "
                "OBSERVATION 1 and the Discussion Items, had already been discussed with the "
                "study team throughout the inspection and preceding the final meeting.")
        obs = f483._extract_483_observations_from_text(text)
        self.assertEqual([o["number"] for o in obs], ["1"])
        self.assertTrue(obs[0]["deficiency"].startswith("An investigation was not conducted"))

    def test_real_headings_with_capital_survive(self):
        text = ("WE OBSERVED: OBSERVATION 1 There is a failure to thoroughly review any "
                "unexplained discrepancy. Specifically, the batch was released. "
                "OBSERVATION 3 The quality control unit lacks authority to review production "
                "records. Specifically, no records were reviewed.")
        # 번호가 건너뛰어도(1→3) 정상 관찰은 살아남는다(순차성은 신호가 아니다).
        self.assertEqual([o["number"] for o in f483._extract_483_observations_from_text(text)],
                         ["1", "3"])


class Refresh483CreatesBlockTest(unittest.TestCase):
    """조립 시점 재추출이 **없던 관찰 블록도 만든다** — 디제스트 오접힘의 근원 차단."""

    SOURCE = ("WE OBSERVED: OBSERVATION 1 There is a failure to thoroughly review any "
              "unexplained discrepancy. Specifically, the batch was released without review.")

    def _card(self, **kw):
        base = {"id": "fda483-1", "card_type": "FDA 483 실사 관찰"}
        base.update(kw)
        return {"cards": [base]}

    def test_creates_when_absent(self):
        out = self._card()
        rep = apb.AssembleReport()
        apb._refresh_483_observations(out, {"fda483-1": {"source_text": self.SOURCE}}, rep)
        dd = out["cards"][0]["deterministic_detail"]
        self.assertEqual(dd["type"], "fda_483_observations")
        self.assertEqual(dd["count"], 1)
        self.assertTrue(any("신설" in w for w in rep.warnings))

    def test_ignores_non_483_card_without_block(self):
        out = self._card(id="wl-1", card_type="Warning Letter")
        rep = apb.AssembleReport()
        apb._refresh_483_observations(out, {"wl-1": {"source_text": self.SOURCE}}, rep)
        self.assertNotIn("deterministic_detail", out["cards"][0])

    def test_never_overwrites_other_detail_type(self):
        out = self._card(deterministic_detail={"type": "wl_violations", "count": 2})
        rep = apb.AssembleReport()
        apb._refresh_483_observations(out, {"fda483-1": {"source_text": self.SOURCE}}, rep)
        self.assertEqual(out["cards"][0]["deterministic_detail"]["type"], "wl_violations")

    def test_restored_card_leaves_the_digest_fold(self):
        # 블록이 생기면 `merge_fda483_disclosures` 가 그 카드를 접지 않는다(연쇄 확인).
        cards = [
            {"id": "fda483-1", "type_tag": "483", "facts": [], "headline_target": "A",
             "deterministic_detail": {"type": "fda_483_observations", "count": 1,
                                      "observations": [{"number": "1", "deficiency": "d",
                                                        "detail": ""}]}},
            {"id": "fda483-2", "type_tag": "483", "facts": [], "headline_target": "B"},
            {"id": "fda483-3", "type_tag": "483", "facts": [], "headline_target": "C"},
        ]
        merged = apb.merge_fda483_disclosures(cards)
        ids = [c["id"] for c in merged]
        self.assertIn("fda483-1", ids)                       # 관찰 보유 → 접힘 대상 아님
        self.assertEqual(len(merged), 2)                     # 나머지 2건이 목록 1장으로


class ObservationKoIndependentOfDeepTest(unittest.TestCase):
    """관찰 국문 번역은 심층분석과 독립이다 — deep-ready 아닌 카드도 병합돼야 한다."""

    def _brief(self):
        return {"cards": [{
            "id": "fda483-1",
            "deterministic_detail": {
                "type": "fda_483_observations", "count": 1,
                "observations": [{"number": "1", "deficiency": "d", "detail": "x"}]},
        }]}                                                   # deep_analysis 키 없음(=대상 아님)

    def test_ko_merged_without_deep_analysis_key(self):
        brief = self._brief()
        rep = inject_slots.inject_deep_analysis(brief, {"fda483-1": {
            "source_text": "…", "observations_ko": [
                {"number": "1", "deficiency_ko": "국문", "detail_ko": "국문 상세"}]}})
        obs = brief["cards"][0]["deterministic_detail"]["observations"][0]
        self.assertEqual(obs["deficiency_ko"], "국문")
        self.assertEqual(obs["detail_ko"], "국문 상세")
        self.assertEqual(rep.errors, [])                      # 심층분석 부재는 오류가 아니다

    def test_deep_payload_for_non_ready_card_still_warns(self):
        brief = self._brief()
        rep = inject_slots.inject_deep_analysis(
            brief, {"fda483-1": {"deep_analysis": {"key_violations": []}, "source_text": ""}})
        self.assertTrue(any("대상이 아님" in w for w in rep.warnings))


class Lint483ObservationKoTest(unittest.TestCase):
    """조립 단계 국문 병기 결손 게이트(배포 fail-closed 게이트의 선행 검출)."""

    def _card(self, obs):
        return {"id": "c", "deterministic_detail": {
            "type": "fda_483_observations", "count": len(obs), "observations": obs}}

    def test_missing_deficiency_ko_blocks(self):
        errs = apb._lint_483_observation_ko([self._card([{"number": "1", "deficiency": "d"}])])
        self.assertTrue(errs and "deficiency_ko" in errs[0])

    def test_only_ids_limits_scope(self):
        """소급 검사 금지 — 이번 조립에서 손댄 카드만 본다(병기 요구 이전 발행분 보호)."""
        card = self._card([{"number": "1", "deficiency": "d"}])
        self.assertTrue(apb._lint_483_observation_ko([card], ["c"]))
        self.assertEqual(apb._lint_483_observation_ko([card], []), [])
        self.assertEqual(apb._lint_483_observation_ko([card], ["other"]), [])

    def test_missing_detail_ko_blocks_only_when_detail_present(self):
        with_detail = self._card([{"number": "1", "deficiency": "d", "deficiency_ko": "국",
                                   "detail": "x"}])
        self.assertTrue(apb._lint_483_observation_ko([with_detail]))
        no_detail = self._card([{"number": "1", "deficiency": "d", "deficiency_ko": "국",
                                 "detail": ""}])
        self.assertEqual(apb._lint_483_observation_ko([no_detail]), [])

    def test_other_detail_types_ignored(self):
        card = {"id": "c", "deterministic_detail": {"type": "wl_violations", "count": 1,
                                                    "violations": [{"number": "1"}]}}
        self.assertEqual(apb._lint_483_observation_ko([card]), [])


class SourceBodyCapturedSignalTest(unittest.TestCase):
    """prose_input 의 정직성 신호 — LLM 이 원문 존재를 추측하지 않게 한다."""

    def test_true_when_any_body_key_present(self):
        for key in ("wl_body_full", "fda483_excerpt", "gmp_deficiencies", "article_excerpt"):
            with self.subTest(key=key):
                self.assertTrue(cs._has_source_body({key: "x"}))

    def test_false_for_metadata_only_raw(self):
        self.assertFalse(cs._has_source_body({"firm": "A", "subject": "CGMP", "url": "u"}))
        self.assertFalse(cs._has_source_body({}))

    def test_empty_value_does_not_count(self):
        self.assertFalse(cs._has_source_body({"wl_body_full": ""}))
        self.assertFalse(cs._has_source_body({"fda_483_observations": []}))


class VerifyPublishedSourcesTest(unittest.TestCase):
    """원문 대조 스크립트의 순수 부분(네트워크 없는 판정·보고)."""

    def test_card_kind(self):
        self.assertEqual(vps._card_kind({"id": "fda483-1"}), "483")
        self.assertEqual(vps._card_kind({"id": "x", "card_type": "Warning Letter"}), "wl")
        self.assertEqual(vps._card_kind({"id": "admin-1", "card_type": "행정처분"}), "")

    def test_report_empty(self):
        self.assertIn("불일치 0", vps.format_report([]))

    def test_report_lists_mismatches(self):
        out = vps.format_report([
            {"date": "2026_07_20", "id": "fda483-1", "shown": 0, "found": 2, "status": "pdf-ok"}])
        self.assertIn("**1건**", out)
        self.assertIn("fda483-1", out)
        self.assertIn("source_text", out)          # 조치 안내가 붙는다


if __name__ == "__main__":
    unittest.main()
